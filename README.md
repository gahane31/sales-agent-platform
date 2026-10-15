# AI sales intelligence platform

A queue of accounts built from Shodan scans. The database holds the raw scans. The pipeline turns them into accounts, evidence, and a score. The app is the screen a rep uses.

## Set up

Python 3.14. From the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`. The app and the pipeline read that file from the repository root.

| Variable | Required | What it is |
|---|---|---|
| `host` | Yes | Database host, such as `db.<project-ref>.supabase.co` |
| `port` | Yes | `5432` |
| `database` | Yes | `postgres` |
| `user` | Yes | Database user, usually `postgres` |
| `password` | Yes | Database password |
| `APP_PASSWORD` | Yes | Password asked before the queue opens |
| `APP_DB_USER` | No | Restricted database role for the queue. If this and `APP_DB_PASSWORD` are empty, the queue uses `user` and `password`. |
| `APP_DB_PASSWORD` | No | Password for `APP_DB_USER` |
| `AI_ENABLED` | No | `true` lets the agents call the model. Anything else, including an empty value, leaves them off. |
| `OPENAI_API_KEY` | For the agents | Model key. The queue still opens without it. |
| `TAVILY_API_KEY` | For company overview | Search key used when writing a company overview. |
| `SUPABASE_URL` | For a new extract | Used only by `scripts/import_shodan_records.py` |
| `SUPABASE_SECRET_KEY` | For a new extract | Used only by that import script |
| `LOG_LEVEL` | No | Defaults to `INFO` |

On a new database:

```bash
.venv/bin/python run.py setup
.venv/bin/python run.py all
```

`setup` creates the tables and seed lists. `all` loads rows that are already in `shodan_records` into accounts, signals, and scores. To load a new JSONL file, run `scripts/import_shodan_records.py`, then `run.py all` again. The raw table is not edited.

## Run

Start the queue:

```bash
.venv/bin/python run.py dev
```

Open http://127.0.0.1:8501. The first screen asks for `APP_PASSWORD`.

Run one agent. With no account, it takes the top 20 and skips an account that already has a result. `--account` rewrites that one domain.

```bash
.venv/bin/python run.py company-overview
.venv/bin/python run.py signal-insights --account example.com
.venv/bin/python run.py outreach --account example.com
```

Score the signal-insights cases:

```bash
.venv/bin/python run.py eval
```

`run.py` with no command prints the full list.

The use cases are in [PLANNING.md](PLANNING.md). How the folders fit together is in [ARCHITECTURE.md](ARCHITECTURE.md). How the work was done is in [HOW_YOU_BUILD.md](HOW_YOU_BUILD.md). How to change a weight or a blocklist is in [pipeline/README.md](pipeline/README.md).
