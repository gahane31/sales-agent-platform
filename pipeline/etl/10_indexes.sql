-- SCRIPT: etl/10_indexes.sql
-- PURPOSE: indexes after the first load, so the bulk insert does not maintain them row by row.
-- DEPENDS ON: etl/01 through etl/09
-- RE-RUNNABLE: yes — create index if not exists
-- WHY: the queue filters customer evidence, expired certs, weak TLS, KEV, and non-infra domains.

begin;
set local statement_timeout = '15min';

create index if not exists scan_records_ip_idx on public.scan_records (ip);
create index if not exists scan_records_port_idx on public.scan_records (port);
create index if not exists scan_records_scanned_at_idx on public.scan_records (scanned_at);
create index if not exists scan_records_customer_evidence_idx
  on public.scan_records (scan_id) where has_customer_evidence;
create index if not exists scan_records_tags_idx on public.scan_records using gin (tags);
create index if not exists scan_records_extras_idx
  on public.scan_records using gin (extras jsonb_path_ops);
create index if not exists scan_domains_domain_idx on public.scan_domains (domain);
create index if not exists scan_ssl_expired_idx on public.scan_ssl (expires_at) where expired;
create index if not exists scan_ssl_weak_tls_idx on public.scan_ssl (scan_id) where weak_tls;
create index if not exists scan_components_component_idx on public.scan_components (component);
create index if not exists scan_cpes_vendor_product_idx on public.scan_cpes (vendor, product);
create index if not exists scan_vulns_cve_idx on public.scan_vulns (cve_id);
create index if not exists scan_vulns_kev_idx on public.scan_vulns (scan_id) where kev;
create index if not exists scan_http_waf_product_idx on public.scan_http (waf_product);
create index if not exists domains_not_infra_idx on public.domains (is_infra) where not is_infra;

analyze public.scan_records;
analyze public.scan_domains;
analyze public.scan_ssl;
analyze public.scan_http;
analyze public.scan_components;
analyze public.scan_cpes;
analyze public.scan_vulns;
analyze public.domains;
analyze public.infrastructure_domains;

commit;
