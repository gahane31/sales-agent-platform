-- SCRIPT: setup/03_signal_config.sql
-- PURPOSE: weights, confidence, and the risky-port list. Tuning data, not detection logic.
-- DEPENDS ON: setup/01_schema.sql
-- RE-RUNNABLE: yes — upsert weights, insert new ports
-- WHY: retuning a pitch is an UPDATE here plus a rescore, not an edit of the detector.

create table if not exists public.signal_catalog (
  signal_type text primary key,
  need_weight int not null,
  timing_weight int not null,
  confidence numeric not null,
  product_angle text not null,
  description text not null,
  human_label text,
  human_meaning text
);

alter table public.signal_catalog
  add column if not exists human_label text,
  add column if not exists human_meaning text;

create table if not exists public.risky_ports (
  port int primary key,
  category text not null
);

insert into public.risky_ports (port, category) values
  (3306, 'database'),
  (5432, 'database'),
  (1433, 'database'),
  (27017, 'database'),
  (6379, 'database'),
  (9200, 'database'),
  (9300, 'database'),
  (5984, 'database'),
  (3389, 'admin'),
  (5900, 'admin'),
  (5901, 'admin'),
  (5902, 'admin'),
  (5903, 'admin'),
  (5800, 'admin'),
  (23, 'admin'),
  (5985, 'admin'),
  (5986, 'admin'),
  (4899, 'admin'),
  (5601, 'admin'),
  (8500, 'admin'),
  (2375, 'devops'),
  (2376, 'devops'),
  (2377, 'devops'),
  (6443, 'devops'),
  (10250, 'devops'),
  (8080, 'devops'),
  (8443, 'devops'),
  (21, 'legacy'),
  (512, 'legacy'),
  (513, 'legacy'),
  (514, 'legacy'),
  (1099, 'legacy'),
  (161, 'legacy')
on conflict (port) do update set category = excluded.category;

insert into public.signal_catalog (
  signal_type, need_weight, timing_weight, confidence, product_angle, description
) values
  ('kev_exploited', 100, 100, 0.95, 'Vulnerability mgmt / EDR — lead with active exploitation',
   'A known-exploited vulnerability is on a host tied to this domain.'),
  ('critical_exposure', 85, 40, 0.80, 'Vulnerability mgmt',
   'A CVE scored 9 or higher is exposed, and it is not on the KEV list.'),
  ('public_database', 90, 45, 0.85, 'Attack-surface mgmt / DB security',
   'A database port or database tag is reachable on the internet.'),
  ('exposed_admin', 75, 40, 0.75, 'ASM / zero-trust / MFA',
   'An admin or devops port is reachable on the internet.'),
  ('ftp_anonymous', 85, 35, 0.90, 'ASM',
   'FTP allows anonymous login.'),
  ('telnet_exposed', 60, 25, 0.80, 'Network security',
   'Telnet is exposed, on port 23 or in the banner extras.'),
  ('eol_software', 70, 30, 0.80, 'VM / upgrade path',
   'The host is tagged end-of-life software or operating system.'),
  ('weak_tls', 55, 25, 0.85, 'Compliance / network sec',
   'TLS 1.0, 1.1, or SSL is still enabled.'),
  ('expired_cert', 55, 55, 0.90, 'Cert mgmt / ASM',
   'The certificate was already expired at scan time.'),
  ('self_signed', 40, 20, 0.60, 'ASM / PKI hygiene',
   'The certificate is self-signed.'),
  ('cert_expiring_30d', 20, 70, 0.85, 'Cert mgmt — timing hook',
   'The certificate expires within 30 days of the scan and was not already expired.'),
  ('open_resolver', 40, 15, 0.80, 'Network hygiene / DDoS-abuse risk',
   'DNS recursion is open.'),
  ('iot_exposed', 55, 20, 0.70, 'IoT/OT security',
   'An IoT tag or a known camera or NAS protocol is exposed.'),
  ('legacy_software', 35, 10, 0.60, 'VM / patch discipline',
   'Fifteen or more old CVEs, none of them KEV, median year before 2016.'),
  ('no_waf_site', 15, 5, 0.50, 'WAF (soft angle)',
   'A real HTTP 200 page has no detected WAF.'),
  ('cloud_workload', 0, 0, 0.90, 'Cloud security (fit + angle only)',
   'The host is in a cloud provider. This does not raise Need or Timing by itself.')
on conflict (signal_type) do update set
  need_weight = excluded.need_weight,
  timing_weight = excluded.timing_weight,
  confidence = excluded.confidence,
  product_angle = excluded.product_angle,
  description = excluded.description;

update public.signal_catalog as catalog
set
  human_label = wording.human_label,
  human_meaning = wording.human_meaning
from (
  values
    ('kev_exploited', 'Hackers are actively exploiting a flaw in their systems',
     'Attackers are already using a known flaw on a system tied to this company.'),
    ('critical_exposure', 'Severe known hole',
     'A severe known hole is exposed to the internet. It is not on the actively exploited list.'),
    ('public_database', 'Database open to the internet',
     'A database is reachable from the internet.'),
    ('exposed_admin', 'Admin login open to the internet',
     'A remote admin login is reachable from the internet.'),
    ('ftp_anonymous', 'File server allows anyone in',
     'Anyone can log in to the file server without a password.'),
    ('telnet_exposed', 'Old remote login is exposed',
     'An outdated remote-login service is open to the internet.'),
    ('eol_software', 'Software the vendor no longer supports',
     'The software or operating system is past the vendor''s support date.'),
    ('weak_tls', 'Outdated encryption',
     'The site still allows old, weak encryption.'),
    ('expired_cert', 'Expired certificate',
     'The site''s certificate had already expired when we looked.'),
    ('self_signed', 'Homemade certificate',
     'The certificate was signed by the company itself, not a public authority.'),
    ('cert_expiring_30d', 'Certificate expires soon',
     'When we scanned on {date}, this certificate had {N} days left.'),
    ('open_resolver', 'Open address lookup others can abuse',
     'The address-lookup server answers the whole internet and can be abused.'),
    ('iot_exposed', 'Camera or device open to the internet',
     'A camera, storage box, or similar device is reachable from the internet.'),
    ('legacy_software', 'Long-unpatched software',
     'Many old holes are still present. None of them are on the actively exploited list.'),
    ('no_waf_site', 'Website with no protective filter',
     'A normal website responds, and we did not see a filter in front of it.'),
    ('cloud_workload', 'Runs in a public cloud',
     'This system sits in a public cloud. That alone is not a reason to call.')
) as wording(signal_type, human_label, human_meaning)
where catalog.signal_type = wording.signal_type;
