-- SCRIPT: etl/13_account_scores.sql
-- PURPOSE: Need, Fit, Timing, Reach, tier, and the pitch order.
-- DEPENDS ON: etl/11_signals.sql
-- RE-RUNNABLE: yes — full refresh (delete + insert) inside one transaction
-- WHY: scores are a pure function of the signal rows and the catalog weights.
--      Changing a weight and rerunning this file is the whole retune.
--      KEV raises both Need and Timing on purpose: the account needs help and has a reason to call today.
--      Accounts with no signals stay in the table at Need 0 and tier D. The queue hides them.

begin;
set local statement_timeout = '15min';

create table if not exists public.account_scores (
  account_domain text primary key,
  need_score int,
  fit_score int,
  timing_score int,
  reach_score int,
  composite int,
  tier text,
  top_signals jsonb,
  product_angles text[],
  company_overview_name text,
  company_overview text,
  signal_insights text,
  outreach text,
  computed_at timestamptz not null default now()
);

alter table public.account_scores
  add column if not exists company_overview_name text,
  add column if not exists company_overview text,
  add column if not exists signal_insights text,
  add column if not exists outreach text;

create temporary table kept_company_overviews on commit drop as
select account_domain, company_overview_name, company_overview, signal_insights, outreach
from public.account_scores
where company_overview is not null
   or signal_insights is not null
   or outreach is not null;

delete from public.account_scores;

insert into public.account_scores (
  account_domain, need_score, fit_score, timing_score, reach_score,
  composite, tier, top_signals, product_angles
)
with accounts as (
  select domain as account_domain
  from public.domains
  where not is_infra
    and coalesce(scan_count, 0) > 0
),
sig as (
  select
    s.account_domain,
    s.signal_type,
    c.need_weight,
    c.timing_weight,
    c.confidence,
    c.product_angle,
    (c.need_weight * c.confidence) as need_rank
  from public.signals s
  join public.signal_catalog c using (signal_type)
),
agg as (
  select
    account_domain,
    count(*)::int as n,
    max(need_weight * confidence) as max_need,
    max(timing_weight * confidence) as max_timing
  from sig
  group by account_domain
),
ranked as (
  select
    sig.*,
    row_number() over (
      partition by account_domain
      order by need_rank desc, signal_type
    ) as rn
  from sig
),
tops as (
  select
    account_domain,
    jsonb_agg(
      jsonb_build_object('signal_type', signal_type, 'rank', need_rank)
      order by need_rank desc, signal_type
    ) filter (where rn <= 3) as top_signals,
    array_agg(distinct product_angle) filter (where rn <= 5) as product_angles
  from ranked
  group by account_domain
),
components as (
  select e.account_domain, count(distinct c.component)::int as n
  from public.v_exposed_scans e
  join public.scan_components c on c.scan_id = e.scan_id
  group by e.account_domain
),
services as (
  select e.account_domain, count(distinct (r.ip, r.port))::int as n
  from public.v_exposed_scans e
  join public.scan_records r on r.scan_id = e.scan_id
  where r.ip is not null
  group by e.account_domain
),
real_site as (
  select distinct e.account_domain
  from public.v_exposed_scans e
  join public.scan_http h on h.scan_id = e.scan_id
  where h.status = 200
    and not h.is_challenge_page
),
titled as (
  select distinct e.account_domain
  from public.v_exposed_scans e
  join public.scan_http h on h.scan_id = e.scan_id
  where nullif(btrim(h.title), '') is not null
),
hosts as (
  select
    account_domain,
    count(distinct hostname) filter (where nullif(hostname, '') is not null)::int as host_n,
    bool_or(hostname ilike 'mail.%' or hostname ilike 'www.%') as branded
  from public.v_exposed_scans
  group by account_domain
),
identity as (
  select
    d.domain as account_domain,
    d.business_type,
    i.fit_bonus
  from public.domains d
  left join public.icp_industries i on i.industry = d.industry
),
scored as (
  select
    a.account_domain,
    case
      when coalesce(g.n, 0) = 0 then 0
      else least(100, round(g.max_need + 5 * (g.n - 1)))::int
    end as need_score,
    least(
      100,
      40
        + coalesce(identity.fit_bonus, 0)
        + case when identity.business_type = 'business' then 10 else 0 end
        + case when exists (
            select 1 from sig s
            where s.account_domain = a.account_domain and s.signal_type = 'cloud_workload'
          ) then 15 else 0 end
        + case when coalesce(components.n, 0) >= 5 then 5 else 0 end
        + case when real_site.account_domain is not null then 5 else 0 end
        + case when coalesce(services.n, 0) >= 5 then 5 else 0 end
    )::int as fit_score,
    case
      when coalesce(g.n, 0) = 0 then 0
      else least(100, round(g.max_timing + 5 * (g.n - 1)))::int
    end as timing_score,
    least(
      100,
      40
        + case when titled.account_domain is not null then 20 else 0 end
        + case when coalesce(hosts.host_n, 0) >= 2 then 20 else 0 end
        + case when coalesce(hosts.branded, false) then 10 else 0 end
    )::int as reach_score,
    coalesce(tops.top_signals, '[]'::jsonb) as top_signals,
    coalesce(tops.product_angles, '{}'::text[]) as product_angles
  from accounts a
  left join agg g on g.account_domain = a.account_domain
  left join tops on tops.account_domain = a.account_domain
  left join components on components.account_domain = a.account_domain
  left join services on services.account_domain = a.account_domain
  left join real_site on real_site.account_domain = a.account_domain
  left join titled on titled.account_domain = a.account_domain
  left join hosts on hosts.account_domain = a.account_domain
  left join identity on identity.account_domain = a.account_domain
)
select
  account_domain,
  need_score,
  fit_score,
  timing_score,
  reach_score,
  round(0.40 * need_score + 0.20 * fit_score + 0.25 * timing_score + 0.15 * reach_score)::int,
  case
    when round(0.40 * need_score + 0.20 * fit_score + 0.25 * timing_score + 0.15 * reach_score)::int >= 75 then 'A'
    when round(0.40 * need_score + 0.20 * fit_score + 0.25 * timing_score + 0.15 * reach_score)::int >= 60 then 'B'
    when round(0.40 * need_score + 0.20 * fit_score + 0.25 * timing_score + 0.15 * reach_score)::int >= 45 then 'C'
    else 'D'
  end,
  top_signals,
  product_angles
from scored;

update public.account_scores as scores
set company_overview_name = kept.company_overview_name,
    company_overview = kept.company_overview,
    signal_insights = kept.signal_insights,
    outreach = kept.outreach
from kept_company_overviews as kept
where kept.account_domain = scores.account_domain;

commit;
