-- SCRIPT: etl/05_scan_http.sql
-- PURPOSE: page metadata, WAF, and challenge-page detection.
-- DEPENDS ON: etl/01_scan_records.sql
-- RE-RUNNABLE: yes — idempotent via anti-join on scan_http
-- WHY: "no WAF" is only a pitch when the page is real. Cloudflare and Incapsula
--      challenge pages are bot walls, not the customer's site. HTML is read here
--      only to set that flag. It is not stored.

begin;
set local statement_timeout = '15min';

insert into public.scan_http (
  scan_id, host, path, status, title, server, waf, waf_product, waf_vendor,
  html_hash, title_hash, dom_hash, headers_hash, favicon_hash, favicon_len,
  redirect_count, is_challenge_page, has_securitytxt
)
select
  p.id,
  h->>'host',
  h->>'path',
  case when coalesce(h->>'status', '') ~ '^\d+$' then public.safe_int(h->>'status') end,
  h->>'title',
  h->>'server',
  h->>'waf',
  case when nullif(h->>'waf', '') is not null then split_part(h->>'waf', ' (', 1) end,
  substring(h->>'waf' from '\((.+)\)$'),
  public.safe_bigint(h->>'html_hash'),
  public.safe_bigint(h->>'title_hash'),
  public.safe_bigint(h->>'dom_hash'),
  public.safe_bigint(h->>'headers_hash'),
  public.safe_bigint(coalesce(h->>'favicon_hash', h #>> '{favicon,hash}')),
  length(h->'favicon'->>'data'),
  case
    when jsonb_typeof(h->'redirects') = 'array' then jsonb_array_length(h->'redirects')
  end,
  coalesce(h->>'title', '') ilike 'just a moment%'
    or coalesce(h->>'html', '') like '%_Incapsula_Resource%'
    or coalesce(h->>'title', '') ilike 'attention required%'
    or exists (
      select 1
      from jsonb_each_text(
        case
          when jsonb_typeof(h->'headers') = 'object' then h->'headers'
          else '{}'::jsonb
        end
      ) hdr
      where lower(hdr.key) = 'cf-mitigated'
        and lower(hdr.value) = 'challenge'
    ),
  (h->>'securitytxt') is not null
from public.shodan_parsed p
cross join lateral (select p.j->'http' as h) x
where jsonb_typeof(p.j->'http') = 'object'
  and not exists (
    select 1 from public.scan_http t where t.scan_id = p.id
  )
on conflict (scan_id) do nothing;

commit;
