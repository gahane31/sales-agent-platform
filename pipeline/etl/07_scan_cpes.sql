-- SCRIPT: etl/07_scan_cpes.sql
-- PURPOSE: CPE 2.3 strings, split into part / vendor / product / version.
-- DEPENDS ON: etl/01_scan_records.sql
-- RE-RUNNABLE: yes — idempotent via anti-join on (scan_id, cpe23)
-- WHY: this is the join key for "a CVE was published this month for software they expose."

begin;
set local statement_timeout = '15min';

insert into public.scan_cpes (scan_id, cpe23, part, vendor, product, version)
select
  p.id,
  cpe,
  parts[3],
  parts[4],
  parts[5],
  parts[6]
from public.shodan_parsed p
cross join lateral jsonb_array_elements_text(p.j->'cpe23') cpe
cross join lateral (select string_to_array(cpe, ':') as parts) s
where jsonb_typeof(p.j->'cpe23') = 'array'
  and length(cpe) > 6
  and not exists (
    select 1
    from public.scan_cpes c
    where c.scan_id = p.id
      and c.cpe23 = cpe
  )
on conflict (scan_id, cpe23) do nothing;

commit;
