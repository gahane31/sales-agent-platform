#!/usr/bin/env python3
"""Short commands for the queue, the pipeline, the agents, and the eval.

Run from any folder. The project root is the folder that contains this file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

AGENTS = {
    "company-overview": "pipeline/jobs/generate_company_overview.py",
    "signal-insights": "pipeline/jobs/generate_signal_insights.py",
    "outreach": "pipeline/jobs/generate_outreach.py",
}

EVALS = {
    "signal-insights": "eval/signal_insights/run.py",
}

HELP = """\
python run.py dev
    Start the queue.

python run.py setup
    Create tables and seed lists on a new database.

python run.py all
    Load records into accounts, signals, and scores.

python run.py company-overview [--account DOMAIN] [--limit N]
python run.py signal-insights [--account DOMAIN] [--limit N]
python run.py outreach [--account DOMAIN] [--limit N]
    Run one agent. No account means the top 20, skipping accounts that already have a result.
    --account rewrites that one domain.

python run.py eval [signal-insights]
    Score the signal-insights cases. That is the only eval.
"""


def execute(arguments: list[str]) -> int:
    completed = subprocess.run([sys.executable, *arguments], cwd=ROOT)
    return completed.returncode


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(HELP, end="")
        return 0
    command, rest = argv[0], argv[1:]
    if command == "dev":
        return execute(["-m", "streamlit", "run", "pipeline/app/queue.py", *rest])
    if command == "setup":
        return execute(["pipeline/runner.py", "--setup", *rest])
    if command == "all":
        return execute(["pipeline/runner.py", "--all", *rest])
    if command == "eval":
        skill = rest[0] if rest else "signal-insights"
        extra = rest[1:] if rest else []
        script = EVALS.get(skill)
        if script is None:
            known = ", ".join(EVALS)
            print(f"unknown eval: {skill}. Known evals: {known}", file=sys.stderr)
            return 1
        return execute([script, *extra])
    script = AGENTS.get(command)
    if script is None:
        print(f"unknown command: {command}\n", file=sys.stderr)
        print(HELP, end="")
        return 1
    return execute([script, *rest])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
