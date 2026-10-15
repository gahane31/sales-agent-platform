# Shodan normalization

Turns `public.shodan_records` into queryable tables. The raw table is never updated and never deleted. Every derived table can be rebuilt from it.

`shodan_records` is the source of truth (`id bigint`, `data jsonb`). This sample is 100,000 rows of a larger corpus, so each script is safe to run twice. The second run inserts nothing new.

## Run

From the repo root, with the database keys in `.env` (`host`, `port`, `database`, `user`, `password`):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py setup
.venv/bin/python run.py all
.venv/bin/python run.py dev
```

Put the database keys in `.env` in this folder before `setup`. `dev` starts the queue. `setup` creates tables, columns, and seed lists. `all` loads new Shodan records into accounts, signals, and scores.

One agent, or the signal-insights eval:

```bash
.venv/bin/python run.py signal-insights --account icealion.com
.venv/bin/python run.py company-overview
.venv/bin/python run.py outreach --account icealion.com
.venv/bin/python run.py eval
```

No account means the top 20, skipping accounts that already have a result. `--account` rewrites that one domain. `run.py` with no command prints the full list.

The direct host `db.<project>.supabase.co` publishes only an IPv6 address. When that connection is refused, the runner uses the session pooler `aws-0-ap-northeast-1.pooler.supabase.com` as `postgres.<project-ref>` with the same password. Session mode (port 5432) is required because each file is one transaction.

## DAG

```text
setup/00_helper_functions
setup/01_schema
setup/05_public_suffix_list_load
setup/migrations
setup/02_infra_seed
        |
        v
etl/01_scan_records
        |
        v
etl/02_scan_domains
        |
        v
etl/03_refresh_flags
        |
        +--------+--------+--------+--------+
        v        v        v        v        v
       04       05       06       07       08
     scan_ssl scan_http components cpes   vulns
        |        |        |        |        |
        +--------+--------+--------+--------+
                          |
                          v
                    09_domains
                          |
                          v
                   10_indexes
                          |
                          v
               setup/03_signal_config
                          |
                          v
                    11_signals
                          |
                          v
              12_enrich_account_domains
                          |
                          v
                 13_account_scores
                          |
                          v
              jobs/generate_company_overview.py
              jobs/generate_signal_insights.py
              jobs/generate_outreach.py
                          |
                          v
               setup/06_views.sql
                          |
                          v
                    app/queue.py
```

`04` through `08` do not depend on each other. The runner still executes them in numbered order. `09` needs `03`. `11` and `13` delete and insert the whole account set. An account's signals change when any of its scans change, and there are only thousands of accounts, so a full refresh is the update.

An account in v1 is one row in `domains` where `is_infra` is false and `scan_count > 0`. Two domains owned by one company are two accounts until enrichment adds a company id. A scan that names two customer domains contributes its evidence to both. Honeypot-tagged scans are dropped before any signal is calculated. Hosts with no domain are out of scope.

KEV raises both Need and Timing on purpose: the account needs the product and has a reason to call today. `cloud_workload` does not raise either axis. It only adds Fit and a cloud-security angle.

## Data-model families

Raw stays immutable. Everything else is derived, except the four workflow tables the app is allowed to write.

| Family | Tables | Who writes |
|---|---|---|
| Raw | `shodan_records` | The import. Never updated by the pipeline or the app. |
| Scan facts | `scan_records`, `scan_domains`, `scan_ssl`, `scan_http`, `scan_components`, `scan_cpes`, `scan_vulns` | ETL, rebuilt from raw. |
| Accounts | `domains`, `signals`, `account_scores` | ETL. Scores are arithmetic. |
| Config | `public_suffix_list`, `infrastructure_domains`, `signal_catalog`, `risky_ports`, `icp_industries`, `industry_keywords`, `generic_cert_orgs`, `llm_settings` | Setup seeds and, for ISPs, `03_refresh_flags.sql`. Judgment lives here. |
| Narrative | `prompt_registry`, `llm_calls`, `eval_runs` | Pipeline jobs. The app may insert `llm_calls` only. |
| Workflow | `account_workflow`, `account_notes`, `account_feedback` | The app. ETL does not touch them. |

Naming rule: a table or file name says what the thing is, in full words. `infrastructure_domains`, not a short form. `public_suffix_list`, not an abbreviation. `00_helper_functions.sql` holds functions. `12_enrich_account_domains.sql` fills account fields. The abbreviation for the public suffix list is banned in code, SQL, and this document.

## Tables

| Table | Grain | Why it exists | Signals it powers | Cost if merged or dropped |
|---|---|---|---|---|
| `scan_records` | 1 row per raw scan | Fact table everything joins to. Ports, geo, cloud, tags, org | Exposed services, cloud tag, eol, honeypot | Nothing else works |
| `scan_domains` | 1 row per scan, domain, and source | Attribution with provenance | Which company this IP belongs to; Reach | No accounts. An array on the scan loses indexes and source |
| `scan_ssl` | 1 row per scan with TLS | Richest signal block | Weak TLS, expired, self-signed, cert issued recently | `WHERE weak_tls` becomes a JSON scan |
| `scan_http` | 1 row per scan with HTTP | Page metadata and WAF | WAF present or absent, challenge page, titles | Same indexing problem, and HTML must stay out of the hot row |
| `scan_components` | 1 row per scan and component | Technographics | Old PHP, Apache, Jenkins | Arrays do not join to a software catalog |
| `scan_cpes` | 1 row per scan and CPE | Standardized software id | CVE published for software they expose | No CVE matching later |
| `scan_vulns` | 1 row per scan and CVE | Shodan already matched KEV, CVSS, EPSS | Call today on a known exploited vuln | Highest-intent table in the extract |
| `domains` | 1 row per registrable domain | Account anchor | Everything account-level. Later: name, industry, size | Enrichment has nothing to hang on |
| `infrastructure_domains` | config list | CDN, hosting, and auto-detected ISP suffixes, stored as data | Stops a CDN edge or a dynamic address pool from being called a customer | A code change for every new suffix |
| `etl_runs` | 1 row per script run | Audit trail | Operations | — |

## Changing the pipeline later

| You want to | Do this | Then re-run |
|---|---|---|
| Add or remove a column | New file `setup/migrations/NNN_description.sql` with `ALTER TABLE`. If the column comes from the raw JSON, update that table's ETL file. | That ETL file. `03_refresh_flags` if it is an evidence flag. |
| Add a CDN or hosting domain | `INSERT INTO infrastructure_domains` | `03_refresh_flags.sql`, then `09_domains.sql` |
| Change TLS or version logic | Edit `etl/04_scan_ssl.sql`. For a full correction, `TRUNCATE scan_ssl` once and re-run. It is rebuildable from raw. | `04`, then `10` if an index changed |
| Change domain extraction | Edit `registrable_domain()` in `setup/00_helper_functions.sql` only | `02`, `03`, `09`. Truncate `scan_domains` first if existing rows must be recomputed. Do not truncate on every `--all`. |
| New rows arrive in `shodan_records` | `python pipeline/runner.py --all` | Anti-joins insert only the new scans. Signals and scores are rebuilt in full. |
| New database, or a new table or column | `python pipeline/runner.py --setup`, then `--all` | Setup does not load records. |
| Tune a signal weight | `UPDATE signal_catalog` | `13_account_scores.sql` |
| Add a risky port | `INSERT INTO risky_ports` | `11_signals.sql`, then `13_account_scores.sql` |
| Add a signal type | Insert a `signal_catalog` row and a detection block in `etl/11_signals.sql` | `11`, then `13` |
| Add an ISP, CDN, or hosting suffix | `INSERT INTO infrastructure_domains`, or let the 80% PTR rule in `03_refresh_flags.sql` detect an ISP | `03_refresh_flags.sql`, then `09_domains.sql`, then `11` and `13` |

## Public suffix list

`registrable_domain()` reads `public.public_suffix_list`. The seed file is `pipeline/setup/seeds/public_suffix_list.dat`, loaded by `setup/05_public_suffix_list_load.sql`.

| | |
|---|---|
| URL | https://publicsuffix.org/list/public_suffix_list.dat |
| Snapshot | 2026-10-07_07-28-19_UTC |
| SHA-256 | `ba7d836c0ea57a8bcf9f1fcb868066a7554bed029b441ecc6204ad8494a32612` |

Refresh monthly. Download that URL, replace the seed file, regenerate the insert in `05_public_suffix_list_load.sql`, and re-run it. If the rules change an existing attribution, truncate `scan_domains` once, then re-run `02`, `03`, and `09`. Do not truncate on an ordinary `--all`.

An exact rule such as `nhs.uk` is the public suffix, so `lhch.nhs.uk` is the registrable domain. A wildcard that consumes the whole hostname, such as `*.compute-1.amazonaws.com`, returns the rule with the wildcard label removed (`compute-1.amazonaws.com`). An exception such as `!www.ck` wins over `*.ck`.

## ISP auto-detection

`03_refresh_flags.sql` inserts a registrable domain into `infrastructure_domains` with category `isp` when at least 80% of its distinct PTR hostnames match `(\d{1,3}[-.]){3}\d{1,3}` or contain `dynamic`, `dhcp`, `pool`, `pppoe`, `dsl`, or `broadband`. The script prints that list. A hand-listed row with the same pattern is left as it is.

## Lists still maintained by hand

| List | What retires it |
|---|---|
| Industry keywords | The model residual pass, next phase. Do not invent industries to raise coverage. |
| Challenge-page and junk-title detectors | One shared template rule, when the volume justifies it. |
| Score thresholds and signal weights | A documented review on a set cadence. Change `signal_catalog` or the score SQL, then re-run `14`. |

## Logger and trace

`pipeline/logger.py` writes operational JSON lines to stderr: a timestamp, a level, a component, and the fields for that step. `pipeline/ai/trace.py` is the only writer of `llm_calls`. That table is the contractual audit of each model call: the prompt version, the decision, the token cost, and the account. A log line can be dropped. A row in `llm_calls` is the record that the budget and the evals read.

Open the queue with `.venv/bin/python run.py dev`.

The app writes only `account_workflow`, `account_notes`, and `account_feedback`. Those tables are the future training labels: feedback type and workflow status say what a rep did, and no scoring script reads them. A disqualified, closed, or feedback-marked account stays in the database and drops out of `v_priority_queue`.

Do not put `http.html`, `ssl.chain`, `favicon.data`, or vuln `summary` / `references` into a derived table. The raw row already stores them.

## Identity and Fit

`etl/12_enrich_account_domains.sql` fills business type, company name, and industry only when the column is still null. A certificate organization replaces a name that came from a page title. Page titles drop a leading `Home ::`, `Home |`, `Home -`, or `::`. `etl/09_domains.sql` deletes a domain that no longer has a `scan_domains` row, and deletes the brief for that domain. It does not delete workflow, notes, or feedback. If that domain comes back in a later scan, the insert creates a blank row and this script fills it again. Country uses the most common country on scans that are not a CDN and not a cloud host. If every scan is one of those, the country is cleared rather than copied from the edge. A country this rule already wrote can be corrected. A country from any other source is left alone. Business type is education for `.edu` and `.ac.*`, government for `.gov` and `.mil`, `hoster_suspect` when the domain has 50 or more IPs or a hosting label, otherwise business. Hosters stay in the queue. They are operators who buy security products too.

Company name prefers the certificate subject organization, skipping generic issuers such as Amazon and Let's Encrypt stand-ins in `generic_cert_orgs`, then a short page title. Industry matches keywords in `industry_keywords` against domain labels and then against the page title. A label equals the keyword, or starts with it when the keyword is at least four characters. That matches "banking" to "bank" and also matches "career" to "care". A long junk title can also trip a short keyword. The certificate organization is not used for industry, so "University of X" in the cert does not classify the account unless the title or the domain says so.

Fit is now `40 + industry bonus + 10 if business_type is business + 15 if a cloud workload is present + 5 for a broad component list + 5 for a real web page + 5 for five or more services`, still capped at 100. Composite weights are unchanged: 0.40 need, 0.20 fit, 0.25 timing, 0.15 reach. MIT moves from 98 to 93 because it is education, not a generic business, and its industry bonus does not fire.

## Cost

A brief run is one model call for each of the top 20 accounts that does not already have a saved brief. That call returns the note and the email together. A research click is about $0.02 to $0.05 when web search is on, and $0 when it uses stored page titles. The ceilings stay $10 and 2,500 calls per run. gpt-4.1-mini is the default because the task is short grounded JSON, not open-ended writing.

## Two audiences

The queue is for a person who will say the words on a phone call. The default screen uses the phone-call wording: "Call today", "Work this week", "Nurture by email", "Hosting provider", "Unclassified", and the `human_label` on each signal. A checkmark means a brief that passed grounding.

The stored columns stay technical. `tier` is still A, B, C, or D. `signal_type` is still `kev_exploited`. The CSV download is that stored vocabulary, so an operator can filter it. The screen is the other vocabulary. Wording a rep sees lives in `signal_catalog.human_label` and `human_meaning`. Edit those columns. Do not edit the detector to change a label.

Country on a domain ignores scans tagged `cdn` and scans on a cloud provider. If every attributed scan is one of those, the country is left unknown instead of copying the edge location. `postnord.com` was US because all 24 of its scans are cloud CDN edges. It is now unknown.

The queue password is `APP_PASSWORD` in `.env` or Streamlit secrets. The queue connects as `APP_DB_USER` when that user and `APP_DB_PASSWORD` are set. That role can read, and can write only `account_workflow`, `account_notes`, and `account_feedback`. A write to `account_scores` with `app_role` returns `InsufficientPrivilege: permission denied for table account_scores`.

## Model calls

`pipeline/ai/llm.py` is the only entry point. It looks up settings, checks the budget, and calls `complete()`. `openai_client.py` is the only file that imports the OpenAI SDK. `trace.py` is the contractual `llm_calls` audit, not the stderr log. `cost.py` prices tokens and refuses an unknown model. `settings.py` reads `llm_settings`. `prompt_registry.py` loads the active prompt, ordering versions by number so v10 ranks above v2. Callers do not construct a client. A test may pass `complete(client=fake)`. Four rules: one SDK import, one table writer, one public function, and the same `complete()`, `BudgetExceeded`, `AiDisabled`, and `ai_enabled()` names as before.

Prompts live in `pipeline/ai/prompts/<skill>/vN.md`. On startup they are registered in `prompt_registry` with a checksum. A new version is a new file plus a registry row; the old row stays so an eval can be compared. Set `is_active` on the version that should run.

`AI_ENABLED` defaults to false. With it off, the three model jobs write nothing. A bad answer is tried twice more. If it still fails, that account is skipped and any saved result is left as it is.

The live prompts are `company-overview`, `signal-insights`, and `outreach`. Each one runs for the top 20 accounts.

Cost if the model is turned on: company overview uses `gpt-4.1`; signal insights and outreach do too. `llm_settings.max_cost_usd_per_run` defaults to 10 and `max_calls_per_run` to 2,500. Past either limit the next call is stored as `budget_exceeded` and that account is skipped.

## Order

After the extracts, the runner does enrichment, scores, the three model jobs, and the queue views. `11_signals` and `13_account_scores` are full refreshes. Workflow rows are not.
