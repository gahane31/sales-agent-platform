-- SCRIPT: etl/09_domains.sql
-- PURPOSE: one row per registrable domain. This is the account anchor.
-- DEPENDS ON: etl/02_scan_domains.sql, etl/03_refresh_flags.sql
-- RE-RUNNABLE: yes — upsert. Never truncates.
-- WHY: company_name, industry, country, and size_band are filled by a later enrichment
--      pass and must survive a rebuild of the scan counts.

begin;
set local statement_timeout = '15min';

insert into public.domains (domain, is_infra, first_seen, last_seen, scan_count, ip_count)
select
  d.domain,
  bool_or(d.is_infra),
  min(r.scanned_at),
  max(r.scanned_at),
  count(distinct d.scan_id),
  count(distinct r.ip)
from public.scan_domains d
join public.scan_records r on r.scan_id = d.scan_id
group by d.domain
on conflict (domain) do update
  set is_infra = excluded.is_infra,
      first_seen = excluded.first_seen,
      last_seen = excluded.last_seen,
      scan_count = excluded.scan_count,
      ip_count = excluded.ip_count
  where public.domains.is_infra is distinct from excluded.is_infra
     or public.domains.first_seen is distinct from excluded.first_seen
     or public.domains.last_seen is distinct from excluded.last_seen
     or public.domains.scan_count is distinct from excluded.scan_count
     or public.domains.ip_count is distinct from excluded.ip_count;

-- Workflow, notes, and feedback stay: a rep's notes survive a domain split.
-- A domain that is deleted here and later reappears is inserted blank, so
-- etl/12_enrich_account_domains.sql fills it again.
delete from public.domains d
where not exists (
  select 1 from public.scan_domains s where s.domain = d.domain
);

commit;
