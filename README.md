# Sales intelligence

A prospecting queue built from Shodan scans. The database holds the raw scans. The pipeline turns them into accounts, evidence, and a score. The Streamlit app is the screen a rep uses.

The old spreadsheet prototype has been removed. Everything that runs lives under `pipeline/`.

## Run the queue

```bash
.venv/bin/python -m streamlit run pipeline/app/queue.py
```

Open http://127.0.0.1:8501.

## Rebuild the tables

Database login is in `.env` (`host`, `port`, `database`, `user`, `password`).

```bash
.venv/bin/python pipeline/runner.py --setup
.venv/bin/python pipeline/runner.py --all
```

`--setup` creates the tables, columns, and seed lists. Use it on a new database, or after a definition change. `--all` loads records that are already in `shodan_records`. Load a new JSONL file with `scripts/import_shodan_records.py`, then run `--all` again. The raw table is not edited. Scores and signals are rebuilt from it.

How the folders are split, and why, is in [ARCHITECTURE.md](ARCHITECTURE.md). How to change a weight or a blocklist is in [pipeline/README.md](pipeline/README.md).
