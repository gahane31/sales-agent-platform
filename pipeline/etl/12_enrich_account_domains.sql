-- SCRIPT: etl/12_enrich_account_domains.sql
-- PURPOSE: fill country, business type, company name, and industry when they are still null.
-- DEPENDS ON: setup/04_icp_config.sql, etl/04_scan_ssl.sql, etl/11_signals.sql
-- RE-RUNNABLE: yes — a page title or certificate name is kept only when that page or certificate belongs to the account.
-- WHY: Fit needs an industry and a business type. The model is only for the names this pass cannot see.
-- A shared address often lists many domains. The page title and the certificate belong to the host that was served, not to every domain on the reverse-DNS list.
--
-- Domain-label rule: a label matches a keyword when they are equal (case-insensitive), or when the
-- keyword is at least 4 characters and the label starts with it. False-positive risk: "care" matches
-- "career", "bank" matches "banking", and "shop" matches "shopping". Title matching uses a word start
-- (\m), so "Insurance" matches "insur" and "University" does not match "univ" unless the letters line up.
-- Certificate organization is not an industry source. A university named only in the certificate org
-- stays unclassified until a title or a domain label says so.

begin;
set local statement_timeout = '15min';

-- Country prefers the most common country on scans that are not a CDN and not a cloud host.
-- If every attributed scan is a CDN or a cloud host, use the most common country on those scans.
-- A value this rule already wrote can be replaced. A value from any other source is left alone.
update public.domains d
set
  country = src.country_code,
  enrich_source = case
    when d.enrich_source like '%country:scan%' then d.enrich_source
    else concat_ws(';', d.enrich_source, 'country:scan')
  end,
  enriched_at = now()
from (
  select
    e.account_domain,
    coalesce(
      mode() within group (order by r.country_code) filter (
        where not coalesce(r.is_cloud, false)
          and not ('cdn' = any (coalesce(r.tags, '{}')))
      ),
      mode() within group (order by r.country_code)
    ) as country_code
  from public.v_exposed_scans e
  join public.scan_records r on r.scan_id = e.scan_id
  where nullif(btrim(r.country_code), '') is not null
  group by e.account_domain
) src
where d.domain = src.account_domain
  and not d.is_infra
  and (
    d.country is null
    or d.enrich_source like '%country:scan%'
  )
  and d.country is distinct from src.country_code;

update public.domains d
set
  business_type = case
    when d.domain ~* '\.(edu|ac\.[a-z]{2})$' or d.domain ~* '\.edu\.[a-z]{2}$' then 'education'
    when d.domain ~* '\.(gov|mil)(\.[a-z]{2})?$' then 'government'
    when coalesce(d.ip_count, 0) >= 50
      or exists (
        select 1
        from unnest(string_to_array(d.domain, '.')) as label
        where label ~* '(hosting|datacenter)'
           or label in ('host', 'server', 'vps', 'cloud', 'datacenter')
      ) then 'hoster_suspect'
    else 'business'
  end,
  enrich_source = concat_ws(';', d.enrich_source, 'business_type:rule'),
  enriched_at = now()
where d.business_type is null
  and not d.is_infra
  and coalesce(d.scan_count, 0) > 0;

-- Drop names and title-based industries that came from another site's page, then fill them again below.
update public.domains
set
  company_name = null,
  enrich_source = nullif(trim(both ';' from regexp_replace(regexp_replace(coalesce(enrich_source, ''), 'company_name:(title|cert_org)', '', 'g'), ';{2,}', ';', 'g')), '')
where not is_infra
  and coalesce(enrich_source, '') not like '%company_name:llm%'
  and (enrich_source like '%company_name:title%' or enrich_source like '%company_name:cert_org%');

update public.domains
set
  industry = null,
  enrich_source = nullif(trim(both ';' from regexp_replace(regexp_replace(coalesce(enrich_source, ''), 'industry:title_keyword', '', 'g'), ';{2,}', ';', 'g')), '')
where enrich_source like '%industry:title_keyword%';

update public.domains d
set
  company_name = src.subject_org,
  enrich_source = case
    when coalesce(d.enrich_source, '') like '%company_name:title%'
      then replace(d.enrich_source, 'company_name:title', 'company_name:cert_org')
    else concat_ws(';', d.enrich_source, 'company_name:cert_org')
  end,
  enriched_at = now()
from (
  select distinct on (account_domain)
    account_domain,
    subject_org
  from (
    select
      e.account_domain,
      btrim(s.subject_org) as subject_org,
      count(*) as n
    from public.v_exposed_scans e
    join public.scan_ssl s on s.scan_id = e.scan_id
    where nullif(btrim(s.subject_org), '') is not null
      and public.registrable_domain(s.subject_cn) = e.account_domain
      and not exists (
        select 1
        from public.generic_cert_orgs g
        where lower(g.org) = lower(btrim(s.subject_org))
      )
    group by e.account_domain, btrim(s.subject_org)
  ) counted
  order by account_domain, n desc, subject_org
) src
where d.domain = src.account_domain
  and (
    d.company_name is null
    or (
      coalesce(d.enrich_source, '') like '%company_name:title%'
      and coalesce(d.enrich_source, '') not like '%company_name:llm%'
      and coalesce(d.enrich_source, '') not like '%company_name:cert_org%'
    )
  );

update public.domains d
set
  company_name = src.cleaned,
  enrich_source = case
    when coalesce(d.enrich_source, '') like '%company_name:title%' then d.enrich_source
    else concat_ws(';', d.enrich_source, 'company_name:title')
  end,
  enriched_at = now()
from (
  select distinct on (e.account_domain)
    e.account_domain,
    left(btrim(
      regexp_replace(
        regexp_replace(
          regexp_replace(
            regexp_replace(h.title, '^(\s*(home\s*(::|\||\-)|::)\s*)+', '', 'i'),
            '\s*(\||\-)\s*home\s*$', '', 'i'
          ),
          '(welcome to|official site)\s*', '', 'gi'
        ),
        '^(\s*(home\s*(::|\||\-)|::)\s*)+', '', 'i'
      )
    , E' \t\r\n'), 200) as cleaned
  from public.v_exposed_scans e
  join public.scan_http h on h.scan_id = e.scan_id
  where nullif(btrim(h.title, E' \t\r\n'), '') is not null
    and public.registrable_domain(h.host) = e.account_domain
    and h.title !~* 'just a moment|attention required|403 forbidden|index of|apache2|moved permanently|service unavailable|could not be satisfied'
    and btrim(h.title, E' \t\r\n') !~* '^(404|302 found|400 bad request|401 unauthorized|403 forbidden|503 |not found|one moment|just a moment|document moved|account suspended|cpanel|default index|nginx!|apache http server test|invalid url|invalid dynamic link|site under maintenance|page not found|error 404)'
    and btrim(h.title, E' \t\r\n') !~* '^error$'
    and length(btrim(h.title, E' \t\r\n')) between 4 and 80
  order by e.account_domain,
    case
      when length(btrim(h.title)) <= 8 then 1
      when btrim(h.title) ~ '^[[:alnum:]]+$'
       and btrim(h.title) ~ '[[:lower:]]'
       and btrim(h.title) ~ '[[:upper:]]' then 1
      else 0
    end,
    length(h.title) desc
) src
where d.domain = src.account_domain
  and (
    d.company_name is null
    or (
      coalesce(d.enrich_source, '') like '%company_name:title%'
      and coalesce(d.enrich_source, '') not like '%company_name:cert_org%'
      and (
        d.company_name ~* '(home\s*(::|\||\-)|::\s|\|\s*home|\-\s*home)'
        or length(d.company_name) <= 8
        or (
          d.company_name ~ '^[[:alnum:]]+$'
          and d.company_name ~ '[[:lower:]]'
          and d.company_name ~ '[[:upper:]]'
        )
      )
    )
  )
  and src.cleaned is not null
  and length(src.cleaned) >= 4
  and src.cleaned !~* '^(home|index of|apache2 default page|403 forbidden|just a moment|attention required|test page|nginx!|404 not found|302 found|400 bad request|401 unauthorized|not found|one moment, please...|document moved|account suspended|cpanel login|default index page|error)$'
  and src.cleaned !~* 'just a moment|attention required|403 forbidden|index of /|apache2 default|nginx';

update public.domains d
set
  industry = src.industry,
  enrich_source = concat_ws(';', d.enrich_source, src.layer),
  enriched_at = now()
from (
  select distinct on (domain)
    domain,
    industry,
    layer
  from (
    select
      labels.domain,
      k.industry,
      i.fit_bonus,
      'industry:domain_keyword' as layer
    from (
      select d2.domain, label
      from public.domains d2
      cross join lateral unnest(string_to_array(d2.domain, '.')) as label
      where d2.industry is null
        and not d2.is_infra
    ) labels
    join public.industry_keywords k
      on lower(labels.label) = lower(k.keyword)
      or (length(k.keyword) >= 4 and lower(labels.label) like lower(k.keyword) || '%')
    join public.icp_industries i on i.industry = k.industry

    union all

    select
      titles.domain,
      k.industry,
      i.fit_bonus,
      'industry:title_keyword' as layer
    from (
      select distinct on (e.account_domain)
        e.account_domain as domain,
        h.title
      from public.v_exposed_scans e
      join public.scan_http h on h.scan_id = e.scan_id
      join public.domains d3 on d3.domain = e.account_domain
      where d3.industry is null
        and nullif(btrim(h.title, E' \t\r\n'), '') is not null
        and public.registrable_domain(h.host) = e.account_domain
        and btrim(h.title, E' \t\r\n') !~* '^(404|302 found|400 bad request|401 unauthorized|403 forbidden|503 |not found|one moment|just a moment|document moved|account suspended|cpanel|default index|nginx!|apache http server test|invalid url|invalid dynamic link|site under maintenance|page not found|error 404)'
        and btrim(h.title, E' \t\r\n') !~* '^error$'
      order by e.account_domain, length(h.title) desc
    ) titles
    join public.industry_keywords k
      on titles.title ~* ('\m' || k.keyword)
    join public.icp_industries i on i.industry = k.industry
  ) hits
  order by domain, fit_bonus desc, layer
) src
where d.domain = src.domain
  and d.industry is null;

commit;
