-- SCRIPT: etl/06_scan_components.sql
-- PURPOSE: technographics, one row per detected component.
-- DEPENDS ON: etl/01_scan_records.sql
-- RE-RUNNABLE: yes — idempotent via anti-join on (scan_id, component)
-- WHY: "runs Jenkins / old PHP / Apache" is a vulnerability-management pitch.
--      An array on the scan cannot be joined to a software catalog.

begin;
set local statement_timeout = '15min';

insert into public.scan_components (scan_id, component, categories)
select
  p.id,
  e.key,
  case
    when jsonb_typeof(e.value->'categories') = 'array'
      then array(select jsonb_array_elements_text(e.value->'categories'))
    else '{}'::text[]
  end
from public.shodan_parsed p
cross join lateral jsonb_each(p.j->'http'->'components') e
where jsonb_typeof(p.j->'http'->'components') = 'object'
  and nullif(e.key, '') is not null
  and not exists (
    select 1
    from public.scan_components c
    where c.scan_id = p.id
      and c.component = e.key
  )
on conflict (scan_id, component) do nothing;

commit;
