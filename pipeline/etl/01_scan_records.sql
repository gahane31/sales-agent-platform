-- SCRIPT: etl/01_scan_records.sql
-- PURPOSE: core fact table; also the processed-row watermark for later scripts.
-- DEPENDS ON: setup/00_helper_functions.sql, setup/01_schema.sql
-- RE-RUNNABLE: yes — idempotent via anti-join on scan_records
-- WHY: ports, geo, cloud, tags, and org live here. Exposed-service signals read this table,
--      not the raw document.

begin;
set local statement_timeout = '15min';

insert into public.scan_records (
  scan_id, shodan_uuid, ip, port, transport, module, scanned_at,
  banner, banner_hash, os, product, product_version,
  asn, isp, org, country_code, city, latitude, longitude,
  cloud_provider, cloud_region, cloud_service, is_cloud, tags,
  heartbleed_vulnerable, ftp_anonymous, dns_recursive,
  has_customer_evidence, extras
)
select
  p.id,
  p.j #>> '{_shodan,id}',
  public.safe_inet(coalesce(nullif(p.j->>'ip_str', ''), p.j->>'ipv6')),
  public.safe_int(p.j->>'port'),
  p.j->>'transport',
  p.j #>> '{_shodan,module}',
  public.safe_ts(p.j->>'timestamp'),
  left(p.j->>'data', 32768),
  public.safe_bigint(p.j->>'hash'),
  p.j->>'os',
  p.j->>'product',
  p.j->>'version',
  p.j->>'asn',
  p.j->>'isp',
  p.j->>'org',
  p.j #>> '{location,country_code}',
  p.j #>> '{location,city}',
  public.safe_float(p.j #>> '{location,latitude}'),
  public.safe_float(p.j #>> '{location,longitude}'),
  p.j #>> '{cloud,provider}',
  p.j #>> '{cloud,region}',
  p.j #>> '{cloud,service}',
  (p.j ? 'cloud') or ('cloud' = any(t.tags)),
  t.tags,
  case
    when (p.j->'opts'->>'heartbleed') ilike '%NOT VULNERABLE%' then false
    when (p.j->'opts'->>'heartbleed') like '%VULNERABLE%' then true
    when p.j->'opts'->>'heartbleed' is null then null
    else false
  end,
  public.safe_bool(p.j->'ftp'->>'anonymous'),
  public.safe_bool(p.j->'dns'->>'recursive'),
  false,
  p.j - array[
    'ip','ipv6','ip_str','os','asn','isp','org','data','hash','opts','port','tags',
    'domains','location','hostnames','timestamp','transport','ssl','http','cloud',
    'cpe','cpe23','cpe23_hash','product','version','vulns','_shodan'
  ]
from public.shodan_parsed p
cross join lateral (
  select case
    when jsonb_typeof(p.j->'tags') = 'array'
      then array(select jsonb_array_elements_text(p.j->'tags'))
    else '{}'::text[]
  end as tags
) t
where p.j is not null
  and public.safe_int(p.j->>'port') is not null
  and not exists (
    select 1 from public.scan_records r where r.scan_id = p.id
  )
on conflict (scan_id) do nothing;

commit;
