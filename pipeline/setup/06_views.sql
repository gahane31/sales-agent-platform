-- SCRIPT: setup/06_views.sql
-- PURPOSE: queue order and the columns the rep sees.
-- DEPENDS ON: etl/13_account_scores.sql, setup/migrations/01_workflow_tables.sql
-- RE-RUNNABLE: yes — create or replace
-- WHY: disqualified and closed accounts stay in the tables and leave the daily queue.
--      Feedback is a training label, not a delete.

drop view if exists public.v_priority_queue;

create view public.v_priority_queue as
select
  s.account_domain,
  coalesce(d.company_name, s.account_domain) as display_name,
  (
    select coalesce(nullif(c.human_label, ''), sig.signal_type)
    from public.signals sig
    left join public.signal_catalog c using (signal_type)
    where sig.account_domain = s.account_domain
    order by c.need_weight desc nulls last, sig.signal_type
    limit 1
  ) as top_reason,
  s.composite as composite_score,
  s.tier,
  s.top_signals,
  s.product_angles,
  s.need_score,
  s.fit_score,
  s.timing_score,
  s.reach_score,
  d.scan_count,
  d.ip_count,
  d.country,
  d.company_name,
  d.industry,
  d.business_type,
  coalesce(w.status, 'new') as workflow_status,
  exists (
    select 1
    from public.signals k
    where k.account_domain = s.account_domain
      and k.signal_type = 'kev_exploited'
  ) as has_kev,
  d.first_seen,
  coalesce(nullif(w.owner, ''), '') as owner
from public.account_scores s
join public.domains d on d.domain = s.account_domain
left join public.account_workflow w on w.account_domain = s.account_domain
where s.tier in ('A', 'B', 'C')
  and not exists (
    select 1 from public.account_feedback f where f.account_domain = s.account_domain
  )
  and coalesce(w.status, 'new') not in ('disqualified', 'closed_won', 'closed_lost')
order by
  s.composite desc,
  exists (
    select 1
    from public.signals k
    where k.account_domain = s.account_domain
      and k.signal_type = 'kev_exploited'
  ) desc,
  s.need_score desc,
  d.ip_count desc nulls last;

create or replace view public.v_account_detail as
select
  s.account_domain,
  s.signal_type,
  s.evidence,
  s.scan_count,
  c.need_weight,
  c.timing_weight,
  c.confidence,
  c.product_angle,
  c.description,
  coalesce(nullif(c.human_label, ''), s.signal_type) as human_label,
  c.human_meaning
from public.signals s
join public.signal_catalog c using (signal_type);

-- 21 drops and recreates the queue view, which clears the grant from 20.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'app_role') then
    grant select on public.v_priority_queue, public.v_account_detail to app_role;
  end if;
end $$;
