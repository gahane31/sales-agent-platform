-- SCRIPT: setup/04_icp_config.sql
-- PURPOSE: industry keywords, fit bonuses, and generic certificate orgs.
-- DEPENDS ON: setup/01_schema.sql
-- RE-RUNNABLE: yes — upsert
-- WHY: changing who counts as a fit is data.

create table if not exists public.icp_industries (
  industry text primary key,
  fit_bonus int not null
);

create table if not exists public.industry_keywords (
  industry text not null references public.icp_industries (industry),
  keyword text not null,
  primary key (industry, keyword)
);

create table if not exists public.generic_cert_orgs (
  org text primary key
);

insert into public.icp_industries (industry, fit_bonus) values
  ('financial services', 20),
  ('healthcare', 20),
  ('insurance', 15),
  ('legal', 15),
  ('saas/tech', 15),
  ('energy/utilities', 15),
  ('manufacturing/ot', 10),
  ('ecommerce/retail', 5),
  ('education', 5),
  ('government', 5)
on conflict (industry) do update set fit_bonus = excluded.fit_bonus;

insert into public.industry_keywords (industry, keyword) values
  ('financial services', 'bank'),
  ('financial services', 'banc'),
  ('financial services', 'fin'),
  ('financial services', 'pay'),
  ('financial services', 'capital'),
  ('financial services', 'invest'),
  ('financial services', 'credit'),
  ('financial services', 'lending'),
  ('healthcare', 'health'),
  ('healthcare', 'med'),
  ('healthcare', 'clinic'),
  ('healthcare', 'hospital'),
  ('healthcare', 'pharma'),
  ('healthcare', 'dental'),
  ('healthcare', 'biotech'),
  ('healthcare', 'care'),
  ('insurance', 'insur'),
  ('insurance', 'assur'),
  ('insurance', 'mutual'),
  ('legal', 'law'),
  ('legal', 'legal'),
  ('legal', 'attorney'),
  ('legal', 'advocat'),
  ('saas/tech', 'software'),
  ('saas/tech', 'tech'),
  ('saas/tech', 'cloud'),
  ('saas/tech', 'systems'),
  ('saas/tech', 'solutions'),
  ('saas/tech', 'digital'),
  ('saas/tech', 'labs'),
  ('manufacturing/ot', 'industri'),
  ('manufacturing/ot', 'manufactur'),
  ('manufacturing/ot', 'automot'),
  ('manufacturing/ot', 'machin'),
  ('manufacturing/ot', 'steel'),
  ('energy/utilities', 'energy'),
  ('energy/utilities', 'power'),
  ('energy/utilities', 'utilit'),
  ('energy/utilities', 'electric'),
  ('energy/utilities', 'solar'),
  ('ecommerce/retail', 'shop'),
  ('ecommerce/retail', 'store'),
  ('ecommerce/retail', 'retail'),
  ('ecommerce/retail', 'commerce'),
  ('ecommerce/retail', 'market'),
  ('education', 'univ'),
  ('education', 'academ'),
  ('education', 'school'),
  ('education', 'college'),
  ('education', 'campus'),
  ('government', 'gov'),
  ('government', 'civic'),
  ('government', 'county'),
  ('government', 'cityof'),
  ('government', 'township')
on conflict (industry, keyword) do nothing;

insert into public.generic_cert_orgs (org) values
  ('Amazon'),
  ('Amazon.com Inc.'),
  ('Google LLC'),
  ('Google Trust Services'),
  ('Cloudflare Inc.'),
  ('Microsoft Corporation'),
  ('GoDaddy'),
  ('Sectigo Limited'),
  ('DigiCert Inc.')
on conflict (org) do nothing;
