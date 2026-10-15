-- SCRIPT: setup/01_schema.sql
-- PURPOSE: every derived table. Change a column here, then update that table's ETL file.
-- DEPENDS ON: public.shodan_records
-- RE-RUNNABLE: yes — create table if not exists
-- WHY: one schema file so a column added for a later signal has one home.

-- Core fact table. Exposed-service signals join here. Nothing else works without it.
create table if not exists public.scan_records (
  scan_id bigint primary key references public.shodan_records (id) on delete cascade,
  shodan_uuid text,
  ip inet,
  port integer not null,
  transport text,
  module text,
  scanned_at timestamptz,
  banner text,
  banner_hash bigint,
  os text,
  product text,
  product_version text,
  asn text,
  isp text,
  org text,
  country_code text,
  city text,
  latitude double precision,
  longitude double precision,
  cloud_provider text,
  cloud_region text,
  cloud_service text,
  is_cloud boolean not null default false,
  tags text[] not null default '{}',
  heartbleed_vulnerable boolean,
  ftp_anonymous boolean,
  dns_recursive boolean,
  has_customer_evidence boolean not null default false,
  extras jsonb not null default '{}',
  etl_version integer not null default 1,
  etl_at timestamptz not null default now()
);

-- Attribution evidence with provenance. One row per scan, domain, and source.
-- Merging this into scan_records as an array would drop per-domain indexes.
create table if not exists public.scan_domains (
  scan_id bigint not null references public.shodan_records (id) on delete cascade,
  hostname text,
  domain text not null,
  source text not null,
  is_infra boolean not null default false,
  primary key (scan_id, domain, source)
);

-- TLS hygiene. Partial indexes on expired and weak_tls are the queries the queue will run.
create table if not exists public.scan_ssl (
  scan_id bigint primary key references public.shodan_records (id) on delete cascade,
  sha256 text,
  sha1 text,
  subject_cn text,
  subject_org text,
  issuer_cn text,
  issuer_org text,
  issued_at timestamptz,
  expires_at timestamptz,
  expired boolean not null default false,
  self_signed boolean not null default false,
  sig_alg text,
  pubkey_bits integer,
  pubkey_type text,
  cipher_name text,
  cipher_version text,
  chain_len integer,
  jarm text,
  ja3s text,
  trust_revoked boolean,
  alpn text[],
  tls_versions text[],
  enabled_versions text[],
  weak_tls boolean
);

alter table public.scan_ssl add column if not exists subject_org text;

-- Page metadata and WAF detection. HTML bodies stay in shodan_records.
create table if not exists public.scan_http (
  scan_id bigint primary key references public.shodan_records (id) on delete cascade,
  host text,
  path text,
  status integer,
  title text,
  server text,
  waf text,
  waf_product text,
  waf_vendor text,
  html_hash bigint,
  title_hash bigint,
  dom_hash bigint,
  headers_hash bigint,
  favicon_hash bigint,
  favicon_len integer,
  redirect_count integer,
  is_challenge_page boolean not null default false,
  has_securitytxt boolean
);

-- Technographics. One row per component so a later CVE feed can join on the name.
create table if not exists public.scan_components (
  scan_id bigint not null references public.shodan_records (id) on delete cascade,
  component text not null,
  categories text[] not null default '{}',
  primary key (scan_id, component)
);

-- CPE 2.3 identifiers. The join key for an NVD "published this month" pass.
create table if not exists public.scan_cpes (
  scan_id bigint not null references public.shodan_records (id) on delete cascade,
  cpe23 text not null,
  part text,
  vendor text,
  product text,
  version text,
  primary key (scan_id, cpe23)
);

-- Shodan-matched CVEs with KEV, CVSS, and EPSS. Summary and references stay in the raw row.
create table if not exists public.scan_vulns (
  scan_id bigint not null references public.shodan_records (id) on delete cascade,
  cve_id text not null,
  kev boolean,
  cvss numeric,
  cvss_v2 numeric,
  epss numeric,
  verified boolean,
  source text not null,
  primary key (scan_id, cve_id)
);

-- Account anchor. Enrichment columns survive every rebuild.
create table if not exists public.domains (
  domain text primary key,
  is_infra boolean not null default false,
  first_seen timestamptz,
  last_seen timestamptz,
  scan_count integer,
  ip_count integer,
  company_name text,
  industry text,
  country text,
  business_type text,
  enrich_source text,
  size_band text,
  enriched_at timestamptz
);

alter table public.domains add column if not exists business_type text;
alter table public.domains add column if not exists enrich_source text;

-- CDN, cloud, hosting, and detected ISP suffixes. Extend with INSERT, then re-run etl/03_refresh_flags.sql.
do $$
begin
  if to_regclass('public.infra_domains') is not null
     and to_regclass('public.infrastructure_domains') is null then
    alter table public.infra_domains rename to infrastructure_domains;
  end if;
end $$;

create table if not exists public.infrastructure_domains (
  pattern text primary key,
  category text not null
);

-- One row per script execution.
create table if not exists public.etl_runs (
  id bigint generated always as identity primary key,
  script text not null,
  mode text not null default 'incremental',
  rows_affected bigint,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  status text,
  error text
);
