# How this repository is laid out

The product is a prospecting queue built from Shodan scans. Raw scans stay in Postgres. The pipeline turns them into accounts, reasons to call, and a score. The Streamlit app is the screen a rep uses.

The folders match three different jobs, plus the two edges of the system: the model, and the screen. A file lives in the folder for what it is. The runner decides when it runs.

## The project root

Commands are started in this folder. `.env` is here, and the database client reads it from the working directory. It does not search upward from the source file.

| Path | What it is |
|---|---|
| `sample.jsonl` | The large raw Shodan extract. It is not code. |
| `data/shodan_sample_100k.jsonl` | The 100,000-row sample that was loaded into `shodan_records`. |
| `scripts/import_shodan_records.py` | Loads a JSONL file into `shodan_records`. It stays outside `pipeline/` because it only fills the raw table. The pipeline never edits or deletes that table. |
| `skills/` | Notes for a person or another agent. They are not imported at runtime. |
| `tests/` | Unit tests for model cost and the model gateway. They do not need a database. |
| `.venv/` | The local Python environment. |

## `pipeline/`

This is the product. `runner.py` is the entry point. `logger.py` is the one structured logger. Every other file asks for a tag (`runner`, `postgres_client`, and so on) so a log line can be filtered by the file that wrote it.

`runner.py` keeps two lists. Each step is a number, a sentence about what it does, and the path of the file that does it. The SQL stays in `.sql` files. The runner reads the file and sends it to Postgres. A path like `etl/01_scan_records.sql` is the name of that job, not a guess at where the repository root is.

| Command | List | What it runs |
|---|---|---|
| `--setup` | Setup | Tables, columns, seed lists, and the phone-call wording for signals. Use this on a new database, or after a definition change. |
| `--all` | Data load | New Shodan records into accounts, signals, scores, briefs, and the screens. |

`--only` runs one file. `--from 13` starts at that ETL number and continues through the selected list.

## `pipeline/db/`

Everything Python does with Postgres lives here, so the SQL is not copied into each caller.

| File | What it does |
|---|---|
| `postgres_client.py` | Opens the connection. The runner and the import script use the admin login. The queue uses `app_role` when `APP_DB_USER` and `APP_DB_PASSWORD` are set. That role can read, and can write only the four workflow tables. |
| `sql_statements.py` | One function per statement Python runs: `insert_etl_run`, `select_priority_queue`, and the rest. Callers pass the connection and the values. |
| `workflow.py` | The queue's writes: status, notes, and feedback. No scoring script writes these. They are labels for later, not inputs to the score. |

## `pipeline/setup/`

The shape of the database, not a load of scan rows.

| File | What it does |
|---|---|
| `00_helper_functions.sql` | Functions that read an IP, a time, and a domain name out of a raw record. |
| `01_schema.sql` | The first tables: records, domains, certificates, web pages, vulnerabilities. |
| `02_infra_seed.sql` | Infrastructure domains that must not be treated as customers. |
| `03_signal_config.sql` | The buying-signal weights and the risky ports. Changing a weight is an update here, then a rescore. |
| `04_icp_config.sql` | Industry fit bonuses and the words used to detect an industry. |
| `06_views.sql` | The queue and account views. The file lives here because it is a definition. It runs at the end of `--all`, because the score and signal tables have to exist first. |
| `migrations/` | Workflow tables and the model tables. Phone-call wording for each signal lives in `03_signal_config.sql`. Edit that wording in the database. Do not edit a detector to change a label. |

## `pipeline/etl/`

SQL that turns raw scans into accounts. Each file is one transaction and is safe to run again.

`01` through `08` save the record, its domain names, certificates, web pages, software, product codes, and vulnerabilities. They insert a row only when that scan is not already there. `03` marks infrastructure domains and which records name a customer. `09` keeps one account per customer domain.

`11_signals.sql` and `13_account_scores.sql` delete and rewrite their tables. One new scan can change an account that already exists, so an incremental insert would be wrong. `12_enrich_account_domains.sql` fills a blank company name, industry, or business type. Country ignores CDN and cloud scans. If every scan for a domain is one of those, the country is left unknown. That is why `postnord.com` is no longer US: all of its scans in this sample are cloud CDN edges.

`10_indexes.sql` adds indexes that are not there yet and refreshes table statistics. A second run does not create the index again.

## `pipeline/jobs/`

Python steps in the data load. They are not the model itself. They read stored rows and write accounts.

| File | What it does |
|---|---|
| `generate_company_overview.py` | One model call for each of the top 20 accounts. Search snippets become a company profile. A saved profile is left alone. |
| `generate_signal_insights.py` | One model call for each of the top 20 accounts. The stored signals become the Signals tab. A saved result is left alone. |
| `generate_outreach.py` | One model call for each of the top 20 accounts. The overview and the signals become the outreach brief. A saved brief is left alone. |

`--all` calls each job with only the database connection, so a saved result is left alone and the limit is 20.

## `pipeline/ai/`

Only the model call. Briefs and proofs do not live here.

| File | What it does |
|---|---|
| `llm.py` | The only public entry, `complete()`. It checks the budget, then calls the client. |
| `openai_client.py` | The only file that imports the OpenAI SDK. |
| `cost.py` | Prices tokens. An unknown model is an error, not a silent fallback rate. |
| `trace.py` | The only writer of `llm_calls`. Every call is a row with a decision and a cost. |
| `prompt_registry.py` | Loads prompt files and marks the highest version number active. `v10` ranks above `v2`. |
| `prompts/` | The prompt text. The live skills are `company-overview`, `signal-insights`, and `outreach`. |
| `settings.py` | Reads `llm_settings`. |

The model never ranks accounts and never invents facts. Scores stay arithmetic.

## `pipeline/app/`

`queue.py` is the only screen. It reads the queue and account views. It writes only the four workflow tables.

The default view is for a phone call. Tier A, B, and C show as "Call today", "Work this week", and "Nurture by email". A signal shows its `human_label`. Certificate identifiers and raw evidence stay inside a collapsed Proof section.

The CSV download is the other vocabulary. It keeps the stored columns (`tier`, `signal_type`, and the rest) so an operator can filter them. The screen does not.

`AI operations` is a separate read-only page for calls, cost, latency, decisions, the active prompt versions, and the latest evals. It is evidence of what the model did. It does not rank accounts.

The page asks for `APP_PASSWORD` from `.env` or Streamlit secrets before it shows anything.
