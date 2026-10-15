-- SCRIPT: etl/08_scan_vulns.sql
-- PURPOSE: one row per scan per CVE, from the vulns object and from opts.vulns.
-- DEPENDS ON: etl/01_scan_records.sql
-- RE-RUNNABLE: yes — idempotent via anti-join on (scan_id, cve_id)
-- WHY: KEV, CVSS, and EPSS are already matched by Shodan. Summary and references
--      are huge and stay in the raw row. opts.vulns is a sparser second list.

begin;
set local statement_timeout = '15min';

insert into public.scan_vulns (
  scan_id, cve_id, kev, cvss, cvss_v2, epss, verified, source
)
select distinct on (scan_id, cve_id)
  scan_id,
  cve_id,
  kev,
  cvss,
  cvss_v2,
  epss,
  verified,
  source
from (
  select
    p.id as scan_id,
    e.key as cve_id,
    public.safe_bool(e.value->>'kev') as kev,
    public.safe_numeric(e.value->>'cvss') as cvss,
    public.safe_numeric(e.value->>'cvss_v2') as cvss_v2,
    public.safe_numeric(e.value->>'epss') as epss,
    public.safe_bool(e.value->>'verified') as verified,
    'vulns_object' as source
  from public.shodan_parsed p
  cross join lateral jsonb_each(p.j->'vulns') e
  where jsonb_typeof(p.j->'vulns') = 'object'

  union all

  select
    p.id,
    cve,
    null::boolean,
    null::numeric,
    null::numeric,
    null::numeric,
    null::boolean,
    'opts_vulns'
  from public.shodan_parsed p
  cross join lateral jsonb_array_elements_text(p.j->'opts'->'vulns') cve
  where jsonb_typeof(p.j->'opts'->'vulns') = 'array'
) src
where nullif(cve_id, '') is not null
  and not exists (
    select 1
    from public.scan_vulns existing
    where existing.scan_id = src.scan_id
      and existing.cve_id = src.cve_id
  )
order by scan_id, cve_id, case when source = 'vulns_object' then 0 else 1 end
on conflict (scan_id, cve_id) do nothing;

commit;
