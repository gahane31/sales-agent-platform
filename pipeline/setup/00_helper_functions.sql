-- SCRIPT: setup/00_helper_functions.sql
-- PURPOSE: coercion and domain extraction used by every ETL file.
-- DEPENDS ON: public.shodan_records
-- RE-RUNNABLE: yes — create or replace
-- WHY: IP parsing, timestamps, and registrable-domain rules change in one place.
--      ETL files call these functions and do not duplicate the rules.

drop materialized view if exists public.shodan_flat;

create or replace function public.safe_jsonb(t text) returns jsonb
language plpgsql immutable as $$
begin
  return t::jsonb;
exception when others then
  return null;
end $$;

create or replace function public.safe_inet(t text) returns inet
language plpgsql immutable as $$
begin
  if t is null or btrim(t) = '' then
    return null;
  end if;
  return btrim(t)::inet;
exception when others then
  return null;
end $$;

-- Scan timestamps are ISO-8601. Certificate dates are compact: 20260128000000Z.
-- A plain ::timestamp cast rejects the compact form, which would null out every cert date.
create or replace function public.safe_ts(t text) returns timestamptz
language plpgsql immutable as $$
declare
  s text;
begin
  s := nullif(btrim(t), '');
  if s is null then
    return null;
  end if;
  if s ~ '^\d{14}' then
    return to_timestamp(substr(s, 1, 14), 'YYYYMMDDHH24MISS');
  end if;
  return s::timestamp at time zone 'UTC';
exception when others then
  return null;
end $$;

create or replace function public.safe_bigint(t text) returns bigint
language plpgsql immutable as $$
begin
  if t is null or btrim(t) = '' or btrim(t) !~ '^-?\d+$' then
    return null;
  end if;
  return btrim(t)::bigint;
exception when others then
  return null;
end $$;

create or replace function public.safe_int(t text) returns integer
language plpgsql immutable as $$
begin
  if t is null or btrim(t) = '' or btrim(t) !~ '^-?\d+$' then
    return null;
  end if;
  return btrim(t)::integer;
exception when others then
  return null;
end $$;

create or replace function public.safe_float(t text) returns double precision
language plpgsql immutable as $$
begin
  if t is null or btrim(t) = '' then
    return null;
  end if;
  return btrim(t)::double precision;
exception when others then
  return null;
end $$;

create or replace function public.safe_numeric(t text) returns numeric
language plpgsql immutable as $$
begin
  if t is null or btrim(t) = '' then
    return null;
  end if;
  return btrim(t)::numeric;
exception when others then
  return null;
end $$;

create or replace function public.safe_bool(t text) returns boolean
language plpgsql immutable as $$
begin
  if t is null or btrim(t) = '' then
    return null;
  end if;
  return btrim(t)::boolean;
exception when others then
  return null;
end $$;

-- Registrable domain. Longest match against public.public_suffix_list.
-- ETL files call this function and do not repeat the rule.
-- A table read cannot be immutable. Drop first: create or replace cannot change volatility.
drop function if exists public.registrable_domain(text);

create function public.registrable_domain(host text)
returns text
language plpgsql
stable
as $$
declare
  cleaned text;
  labels text[];
  n int;
  i int;
  suffix text;
  rest text;
  exact_rules text[] := '{}';
  wildcard_rules text[] := '{}';
  exception_rules text[] := '{}';
  matched text[];
  best_len int := -1;
  best_rank int := -1;
  best_kind text;
  best_rule text;
  best_index int;
  label_count int;
  rank int;
  result text;
begin
  if host is null then
    return null;
  end if;
  cleaned := lower(btrim(host));
  cleaned := regexp_replace(cleaned, '^\*\.', '');
  cleaned := regexp_replace(cleaned, ':\d+$', '');
  if cleaned = '' or cleaned !~ '\.' or cleaned ~ ':' or cleaned ~ '^[0-9.]+$' then
    return null;
  end if;
  labels := string_to_array(cleaned, '.');
  n := array_length(labels, 1);
  if n is null or n < 2 then
    return null;
  end if;

  for i in 1..n loop
    suffix := array_to_string(labels[i:n], '.');
    exact_rules := exact_rules || suffix;
    exception_rules := exception_rules || ('!' || suffix);
    if i < n then
      rest := array_to_string(labels[i + 1:n], '.');
      wildcard_rules := wildcard_rules || ('*.' || rest);
    end if;
  end loop;

  select coalesce(array_agg(rule), '{}')
    into matched
  from public.public_suffix_list
  where rule = any(exact_rules)
     or rule = any(wildcard_rules)
     or rule = any(exception_rules);

  for i in 1..n loop
    suffix := array_to_string(labels[i:n], '.');
    label_count := n - i + 1;
    if suffix = any(matched) then
      rank := 2;
      if label_count > best_len or (label_count = best_len and rank > best_rank) then
        best_len := label_count;
        best_rank := rank;
        best_kind := 'exact';
        best_rule := suffix;
        best_index := i;
      end if;
    end if;
    if i < n then
      rest := array_to_string(labels[i + 1:n], '.');
      if ('*.' || rest) = any(matched) then
        rank := 1;
        if label_count > best_len or (label_count = best_len and rank > best_rank) then
          best_len := label_count;
          best_rank := rank;
          best_kind := 'wildcard';
          best_rule := '*.' || rest;
          best_index := i;
        end if;
      end if;
    end if;
    if ('!' || suffix) = any(matched) then
      rank := 3;
      if label_count > best_len or (label_count = best_len and rank > best_rank) then
        best_len := label_count;
        best_rank := rank;
        best_kind := 'exception';
        best_rule := '!' || suffix;
        best_index := i;
      end if;
    end if;
  end loop;

  if best_kind is null then
    result := labels[n - 1] || '.' || labels[n];
  elsif best_kind = 'exception' then
    result := array_to_string(labels[best_index:n], '.');
  elsif best_kind = 'exact' then
    if best_index = 1 then
      return null;
    end if;
    result := array_to_string(labels[best_index - 1:n], '.');
  elsif best_index = 1 then
    result := substring(best_rule from 3);
  else
    result := array_to_string(labels[best_index - 1:n], '.');
  end if;

  if result is null or position('.' in result) = 0 then
    return null;
  end if;
  if exists (select 1 from public.public_suffix_list where rule = result) then
    return null;
  end if;
  return result;
end $$;

-- Parse view: native-object rows (current sample) and string-encoded rows (possible later).
create or replace view public.shodan_parsed as
select id,
       case
         when jsonb_typeof(data) = 'object' then data
         when jsonb_typeof(data) = 'string' then public.safe_jsonb(data #>> '{}')
       end as j
from public.shodan_records;
