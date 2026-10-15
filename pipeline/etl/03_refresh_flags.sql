-- SCRIPT: etl/03_refresh_flags.sql
-- PURPOSE: mark infrastructure domains and whether a scan has customer-owned evidence.
-- DEPENDS ON: etl/02_scan_domains.sql, setup/02_infra_seed.sql
-- RE-RUNNABLE: yes — full refresh. A second run updates a row only when the flag changed.
-- WHY: this is the script to re-run after INSERT into infrastructure_domains.
--      At 70GB this is a periodic job, not something to run per small batch.
--
-- ISP detection is a rule, not a carrier list. A registrable domain is an ISP when
-- at least 80% of its PTR hostnames look like a dynamic address pool.

begin;
set local statement_timeout = '15min';

insert into public.infrastructure_domains (pattern, category)
select counted.domain, 'isp'
from (
  select
    domain,
    count(distinct hostname) filter (where source = 'ptr_hostname') as ptr_hostnames,
    count(distinct hostname) filter (
      where source = 'ptr_hostname'
        and (
          hostname ~ '(\d{1,3}[-.]){3}\d{1,3}'
          or hostname ~* 'dynamic|dhcp|pool|pppoe|dsl|broadband'
        )
    ) as dynamic_hostnames
  from public.scan_domains
  where domain is not null
  group by domain
) counted
where counted.ptr_hostnames > 0
  and counted.dynamic_hostnames::numeric / counted.ptr_hostnames >= 0.80
on conflict (pattern) do nothing;

select counted.domain as detected_isp
from (
  select
    domain,
    count(distinct hostname) filter (where source = 'ptr_hostname') as ptr_hostnames,
    count(distinct hostname) filter (
      where source = 'ptr_hostname'
        and (
          hostname ~ '(\d{1,3}[-.]){3}\d{1,3}'
          or hostname ~* 'dynamic|dhcp|pool|pppoe|dsl|broadband'
        )
    ) as dynamic_hostnames
  from public.scan_domains
  where domain is not null
  group by domain
) counted
where counted.ptr_hostnames > 0
  and counted.dynamic_hostnames::numeric / counted.ptr_hostnames >= 0.80
order by 1;

update public.scan_domains d
set is_infra = v.flag
from (
  select
    d2.scan_id,
    d2.domain,
    d2.source,
    exists (
      select 1
      from public.infrastructure_domains i
      where d2.domain = i.pattern
         or d2.domain like '%.' || i.pattern
    ) as flag
  from public.scan_domains d2
) v
where d.scan_id = v.scan_id
  and d.domain = v.domain
  and d.source = v.source
  and d.is_infra is distinct from v.flag;

update public.scan_records r
set has_customer_evidence = v.flag
from (
  select
    r2.scan_id,
    exists (
      select 1
      from public.scan_domains d
      where d.scan_id = r2.scan_id
        and not d.is_infra
    ) as flag
  from public.scan_records r2
) v
where r.scan_id = v.scan_id
  and r.has_customer_evidence is distinct from v.flag;

commit;
