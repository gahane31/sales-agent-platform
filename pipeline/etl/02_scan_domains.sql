-- SCRIPT: etl/02_scan_domains.sql
-- PURPOSE: attribution evidence with provenance.
-- DEPENDS ON: etl/01_scan_records.sql
-- RE-RUNNABLE: yes — idempotent via anti-join (skip a scan that already has any domain row)
-- WHY: sales intelligence lives or dies on which company an IP belongs to.
--      Raw rows are immutable, so skipping a scan that already has evidence is safe.

begin;
set local statement_timeout = '15min';

insert into public.scan_domains (scan_id, hostname, domain, source)
select distinct on (scan_id, domain, source)
  scan_id,
  hostname,
  domain,
  source
from (
  select
    p.id as scan_id,
    null::text as hostname,
    public.registrable_domain(elem) as domain,
    'ptr_domain' as source
  from public.shodan_parsed p
  cross join lateral jsonb_array_elements_text(
    case when jsonb_typeof(p.j->'domains') = 'array' then p.j->'domains' else '[]'::jsonb end
  ) elem
  where p.j is not null

  union all

  select
    p.id,
    elem,
    public.registrable_domain(elem),
    'ptr_hostname'
  from public.shodan_parsed p
  cross join lateral jsonb_array_elements_text(
    case when jsonb_typeof(p.j->'hostnames') = 'array' then p.j->'hostnames' else '[]'::jsonb end
  ) elem
  where p.j is not null

  union all

  select
    p.id,
    p.j #>> '{_shodan,options,hostname}',
    public.registrable_domain(p.j #>> '{_shodan,options,hostname}'),
    'shodan_options'
  from public.shodan_parsed p
  where p.j is not null
    and nullif(p.j #>> '{_shodan,options,hostname}', '') is not null

  union all

  select
    p.id,
    lower(p.j #>> '{ssl,cert,subject,CN}'),
    public.registrable_domain(p.j #>> '{ssl,cert,subject,CN}'),
    'cert_cn'
  from public.shodan_parsed p
  where p.j is not null
    and lower(coalesce(p.j #>> '{ssl,cert,subject,CN}', '')) ~ '^[a-z0-9*.-]+\.[a-z]{2,}$'

  union all

  select
    p.id,
    p.j #>> '{http,host}',
    public.registrable_domain(p.j #>> '{http,host}'),
    'http_host'
  from public.shodan_parsed p
  where p.j is not null
    and nullif(p.j #>> '{http,host}', '') is not null
) extracted
where domain is not null
  and not exists (
    select 1 from public.scan_domains d where d.scan_id = extracted.scan_id
  )
order by scan_id, domain, source, hostname nulls last
on conflict (scan_id, domain, source) do nothing;

commit;
