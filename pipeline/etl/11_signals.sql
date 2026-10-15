-- SCRIPT: etl/11_signals.sql
-- PURPOSE: one row per account per reason they might buy.
-- DEPENDS ON: setup/03_signal_config.sql, etl/03_refresh_flags.sql, etl/09_domains.sql
-- RE-RUNNABLE: yes — full refresh (delete + insert) inside one transaction
-- WHY: account aggregates change whenever any scan arrives, and there are only thousands
--      of accounts. Incremental anti-joins would be wrong here. A full refresh is the update.
--
-- An account is one non-infrastructure registrable domain. A company with two domains
-- is two accounts until enrichment builds a real company record.
-- A scan that names two customer domains attaches the same evidence to both.
-- Honeypot-tagged scans are excluded before any signal is computed.
-- KEV is allowed to raise both Need and Timing: the account needs the product and has a reason to buy now.
-- CVE counts are never summed. scan_count is distinct scans. kev_total is distinct CVE ids.

begin;
set local statement_timeout = '15min';

create table if not exists public.signals (
  account_domain text not null,
  signal_type text not null references public.signal_catalog (signal_type),
  evidence jsonb not null default '{}',
  scan_count int not null default 0,
  computed_at timestamptz not null default now(),
  primary key (account_domain, signal_type)
);

create or replace view public.v_exposed_scans as
select distinct
  sd.domain as account_domain,
  sd.scan_id,
  sd.hostname
from public.scan_domains sd
join public.scan_records r on r.scan_id = sd.scan_id
join public.domains d on d.domain = sd.domain
where not sd.is_infra
  and not d.is_infra
  and coalesce(d.scan_count, 0) > 0
  and not ('honeypot' = any (coalesce(r.tags, '{}')));

create temp table exposed on commit drop as
select * from public.v_exposed_scans;
create index on exposed (scan_id);
create index on exposed (account_domain);

delete from public.signals;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  g.account_domain,
  'kev_exploited',
  jsonb_build_object(
    'kev_total', g.kev_total,
    'top_cves', top.top_cves,
    'scan_ids', g.scan_ids
  ),
  g.scan_count
from (
  select
    e.account_domain,
    count(distinct v.cve_id) as kev_total,
    count(distinct e.scan_id) as scan_count,
    coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb) as scan_ids
  from exposed e
  join public.scan_vulns v on v.scan_id = e.scan_id and v.kev
  group by e.account_domain
) g
cross join lateral (
  select coalesce(jsonb_agg(obj), '[]'::jsonb) as top_cves
  from (
    select jsonb_build_object(
      'cve_id', v.cve_id,
      'cvss', max(v.cvss),
      'epss', max(v.epss),
      'kev', true
    ) as obj
    from exposed e
    join public.scan_vulns v on v.scan_id = e.scan_id and v.kev
    where e.account_domain = g.account_domain
    group by v.cve_id
    order by max(v.cvss) desc nulls last, v.cve_id
    limit 3
  ) ranked
) top;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  g.account_domain,
  'critical_exposure',
  jsonb_build_object(
    'cve_total', g.cve_total,
    'top_cves', top.top_cves,
    'scan_ids', g.scan_ids
  ),
  g.scan_count
from (
  select
    e.account_domain,
    count(distinct v.cve_id) as cve_total,
    count(distinct e.scan_id) as scan_count,
    coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb) as scan_ids
  from exposed e
  join public.scan_vulns v on v.scan_id = e.scan_id
  where v.kev is not true
    and v.cvss >= 9
  group by e.account_domain
) g
cross join lateral (
  select coalesce(jsonb_agg(obj), '[]'::jsonb) as top_cves
  from (
    select jsonb_build_object(
      'cve_id', v.cve_id,
      'cvss', max(v.cvss),
      'epss', max(v.epss),
      'kev', false
    ) as obj
    from exposed e
    join public.scan_vulns v on v.scan_id = e.scan_id
    where e.account_domain = g.account_domain
      and v.kev is not true
      and v.cvss >= 9
    group by v.cve_id
    order by max(v.cvss) desc nulls last, v.cve_id
    limit 3
  ) ranked
) top;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'public_database',
  jsonb_build_object(
    'ports', coalesce(jsonb_agg(distinct r.port), '[]'::jsonb),
    'example_product', min(r.product) filter (where r.product is not null),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
left join public.risky_ports p on p.port = r.port and p.category = 'database'
where p.port is not null
   or 'database' = any (coalesce(r.tags, '{}'))
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'exposed_admin',
  jsonb_build_object(
    'ports', coalesce(jsonb_agg(distinct r.port), '[]'::jsonb),
    'products', coalesce(jsonb_agg(distinct r.product) filter (where r.product is not null), '[]'::jsonb),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
join public.risky_ports p on p.port = r.port and p.category in ('admin', 'devops')
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'ftp_anonymous',
  jsonb_build_object(
    'ports', coalesce(jsonb_agg(distinct r.port), '[]'::jsonb),
    'example_product', min(r.product) filter (where r.product is not null),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
where r.ftp_anonymous
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'telnet_exposed',
  jsonb_build_object(
    'ports', coalesce(jsonb_agg(distinct r.port), '[]'::jsonb),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
where r.port = 23
   or r.extras ? 'telnet'
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'eol_software',
  jsonb_build_object(
    'products', coalesce(
      jsonb_agg(distinct nullif(concat_ws(' ', r.product, r.product_version), ''))
        filter (where r.product is not null or r.product_version is not null),
      '[]'::jsonb
    ),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
where 'eol-product' = any (coalesce(r.tags, '{}'))
   or 'eol-os' = any (coalesce(r.tags, '{}'))
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'weak_tls',
  jsonb_build_object(
    'enabled_versions', coalesce((
      select jsonb_agg(distinct version)
      from exposed e2
      join public.scan_ssl s2 on s2.scan_id = e2.scan_id and s2.weak_tls
      cross join lateral unnest(coalesce(s2.enabled_versions, '{}')) as version
      where e2.account_domain = e.account_domain
    ), '[]'::jsonb),
    'endpoints', count(distinct e.scan_id),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_ssl s on s.scan_id = e.scan_id and s.weak_tls
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'expired_cert',
  jsonb_build_object(
    'subject_cns', coalesce((
      select jsonb_agg(cn)
      from (
        select distinct s2.subject_cn as cn
        from exposed e2
        join public.scan_ssl s2 on s2.scan_id = e2.scan_id and s2.expired
        where e2.account_domain = e.account_domain
          and s2.subject_cn is not null
        limit 5
      ) names
    ), '[]'::jsonb),
    'max_days_expired', max((r.scanned_at::date - s.expires_at::date)),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
join public.scan_ssl s on s.scan_id = e.scan_id and s.expired
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'self_signed',
  jsonb_build_object(
    'subject_cns', coalesce((
      select jsonb_agg(cn)
      from (
        select distinct s2.subject_cn as cn
        from exposed e2
        join public.scan_ssl s2 on s2.scan_id = e2.scan_id and s2.self_signed
        where e2.account_domain = e.account_domain
          and s2.subject_cn is not null
        limit 5
      ) names
    ), '[]'::jsonb),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_ssl s on s.scan_id = e.scan_id and s.self_signed
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'cert_expiring_30d',
  jsonb_build_object(
    'subject_cns', coalesce(jsonb_agg(distinct s.subject_cn) filter (where s.subject_cn is not null), '[]'::jsonb),
    'soonest_expiry', min(s.expires_at),
    'days_left', (array_agg((s.expires_at::date - r.scanned_at::date) order by s.expires_at))[1],
    'scanned_on', to_char((array_agg(r.scanned_at order by s.expires_at))[1] at time zone 'UTC', 'YYYY-MM-DD'),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
join public.scan_ssl s on s.scan_id = e.scan_id
where not s.expired
  and s.expires_at >= r.scanned_at
  and s.expires_at < r.scanned_at + interval '30 days'
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'open_resolver',
  jsonb_build_object(
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
where r.dns_recursive
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'iot_exposed',
  jsonb_build_object(
    'ports', coalesce(jsonb_agg(distinct r.port), '[]'::jsonb),
    'products', coalesce(jsonb_agg(distinct r.product) filter (where r.product is not null), '[]'::jsonb),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
where 'iot' = any (coalesce(r.tags, '{}'))
   or r.extras ?| array['hikvision', 'dahua', 'dahua_dvr_web', 'qnap', 'airplay']
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'legacy_software',
  jsonb_build_object(
    'oldest_cve_year', min(p.oldest_year),
    'newest_cve_year', max(p.newest_year),
    'scan_count', count(distinct p.scan_id),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct p.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct p.scan_id)
from (
  select
    v.scan_id,
    min(substring(v.cve_id from 'CVE-([0-9]{4})')::int) as oldest_year,
    max(substring(v.cve_id from 'CVE-([0-9]{4})')::int) as newest_year
  from public.scan_vulns v
  where substring(v.cve_id from 'CVE-([0-9]{4})') is not null
  group by v.scan_id
  having count(*) >= 15
     and count(*) filter (where v.kev) = 0
     and percentile_cont(0.5) within group (
       order by substring(v.cve_id from 'CVE-([0-9]{4})')::int
     ) < 2016
) p
join exposed e on e.scan_id = p.scan_id
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'no_waf_site',
  jsonb_build_object(
    'hosts', coalesce(jsonb_agg(distinct h.host) filter (where h.host is not null), '[]'::jsonb),
    'titles', coalesce(jsonb_agg(distinct h.title) filter (where h.title is not null), '[]'::jsonb),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_http h on h.scan_id = e.scan_id
where h.waf is null
  and not h.is_challenge_page
  and h.status = 200
group by e.account_domain;

insert into public.signals (account_domain, signal_type, evidence, scan_count)
select
  e.account_domain,
  'cloud_workload',
  jsonb_build_object(
    'providers', coalesce(jsonb_agg(distinct r.cloud_provider) filter (where r.cloud_provider is not null), '[]'::jsonb),
    'scan_ids', coalesce(to_jsonb((array_agg(distinct e.scan_id))[1:5]), '[]'::jsonb)
  ),
  count(distinct e.scan_id)
from exposed e
join public.scan_records r on r.scan_id = e.scan_id
where r.is_cloud
group by e.account_domain;

commit;
