-- SCRIPT: etl/04_scan_ssl.sql
-- PURPOSE: TLS and certificate hygiene.
-- DEPENDS ON: etl/01_scan_records.sql
-- RE-RUNNABLE: yes — idempotent via anti-join on scan_ssl
-- WHY: weak TLS, expired certs, self-signed certs, and recent issuance are the richest
--      signal block in this extract. A leading "-" on ssl.versions means that protocol is disabled.

begin;
set local statement_timeout = '15min';

-- One-time rebuild after subject_org was added. Later runs see real values and insert nothing.
do $$
begin
  if exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'scan_ssl'
      and column_name = 'subject_org'
  )
  and exists (select 1 from public.scan_ssl)
  and not exists (select 1 from public.scan_ssl where subject_org is not null) then
    truncate public.scan_ssl;
  end if;
end $$;

insert into public.scan_ssl (
  scan_id, sha256, sha1, subject_cn, subject_org, issuer_cn, issuer_org,
  issued_at, expires_at, expired, self_signed, sig_alg,
  pubkey_bits, pubkey_type, cipher_name, cipher_version, chain_len,
  jarm, ja3s, trust_revoked, alpn, tls_versions, enabled_versions, weak_tls
)
select
  p.id,
  s->'cert'->'fingerprint'->>'sha256',
  s->'cert'->'fingerprint'->>'sha1',
  s #>> '{cert,subject,CN}',
  nullif(btrim(s #>> '{cert,subject,O}'), ''),
  s #>> '{cert,issuer,CN}',
  s #>> '{cert,issuer,O}',
  public.safe_ts(left(s #>> '{cert,issued}', 14)),
  public.safe_ts(left(s #>> '{cert,expires}', 14)),
  coalesce(public.safe_bool(s->'cert'->>'expired'), false),
  coalesce('self-signed' = any(tg.tags), false)
    or coalesce(
      jsonb_typeof(s->'cert'->'issuer') = 'object'
      and jsonb_typeof(s->'cert'->'subject') = 'object'
      and s->'cert'->'issuer' = s->'cert'->'subject',
      false
    )
    or coalesce(
      nullif(s #>> '{cert,issuer,CN}', '') is not null
      and s #>> '{cert,issuer,CN}' = s #>> '{cert,subject,CN}',
      false
    ),
  s #>> '{cert,sig_alg}',
  public.safe_int(s #>> '{cert,pubkey,bits}'),
  s #>> '{cert,pubkey,type}',
  s #>> '{cipher,name}',
  s #>> '{cipher,version}',
  case
    when jsonb_typeof(s->'chain') = 'array' then jsonb_array_length(s->'chain')
  end,
  s->>'jarm',
  s->>'ja3s',
  public.safe_bool(s->'trust'->>'revoked'),
  case
    when jsonb_typeof(s->'alpn') = 'array'
      then array(select jsonb_array_elements_text(s->'alpn'))
  end,
  case
    when jsonb_typeof(s->'versions') = 'array'
      then array(select jsonb_array_elements_text(s->'versions'))
  end,
  case
    when jsonb_typeof(s->'versions') = 'array'
      then array(
        select v
        from jsonb_array_elements_text(s->'versions') v
        where v not like '-%'
      )
  end,
  exists (
    select 1
    from jsonb_array_elements_text(
      case
        when jsonb_typeof(s->'versions') = 'array' then s->'versions'
        else '[]'::jsonb
      end
    ) v
    where v not like '-%'
      and v in ('SSLv2', 'SSLv3', 'TLSv1', 'TLSv1.1')
  )
from public.shodan_parsed p
cross join lateral (select p.j->'ssl' as s) x
cross join lateral (
  select case
    when jsonb_typeof(p.j->'tags') = 'array'
      then array(select jsonb_array_elements_text(p.j->'tags'))
    else '{}'::text[]
  end as tags
) tg
where jsonb_typeof(p.j->'ssl') = 'object'
  and not exists (
    select 1 from public.scan_ssl t where t.scan_id = p.id
  )
on conflict (scan_id) do nothing;

commit;
