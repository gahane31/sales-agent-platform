"""Re-run signal insights on the labelled cases and compare with the previous prompt.

    .venv/bin/python -m eval.signal_insights.run
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

project_directory = str(Path.cwd())
if project_directory not in sys.path:
    sys.path.insert(0, project_directory)

from pipeline.ai.llm import complete, load_prompt
from pipeline.ai.models import model_name
from pipeline.db.postgres_client import open_database_connection
from pipeline.db.sql_statements import insert_eval_run, select_active_prompt_version, select_previous_eval_run
from pipeline.files import read_json_files
from pipeline.jobs.generate_signal_insights import OUTPUT_SCHEMA, SKILL, build_signal_input, insight_request
from eval.signal_insights.score import case_input, combine, score_case

METRICS = (
    "evidence_recall",
    "evidence_precision",
    "priority_accuracy",
    "forbidden_claims",
    "overview_used",
    "section_precision",
    "section_forbidden",
    "extra_signals",
    "signal_order",
)


def load_cases() -> list[dict]:
    return read_json_files(__file__, "cases")


def previous_run(conn, checksum: str, model: str) -> dict | None:
    row = select_previous_eval_run(conn, SKILL, checksum, model)
    if row is None:
        return None
    metrics = row.get("metrics") or {}
    if isinstance(metrics, str):
        metrics = json.loads(metrics)
    return {
        "prompt_version": row.get("prompt_version"),
        "prompt_checksum": row.get("prompt_checksum"),
        "model": row.get("model"),
        "metrics": metrics,
    }


def ask(conn, prompt: str, version: str, case: dict, run_id: str) -> dict | None:
    given = case_input(case)
    prepared = build_signal_input(given["signals"])
    result = complete(
        conn,
        skill=SKILL,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    insight_request(given["company_name"], given.get("company_overview"), prepared),
                    default=str,
                ),
            },
        ],
        run_id=run_id,
        account_domain=case["id"],
        prompt_version=version,
        output_schema=OUTPUT_SCHEMA,
        model=model_name("eval"),
    )
    parsed = result.get("parsed")
    return parsed if isinstance(parsed, dict) else None


def _rate_number(value):
    if isinstance(value, dict):
        if "rate" in value:
            return value.get("rate")
        return value.get("count")
    return value


def _shown(value) -> str:
    if not isinstance(value, dict):
        return "n/a" if value is None else str(value)
    if "count" in value:
        return f"{value['count']} of {value['n']}"
    rate = "n/a" if value.get("rate") is None else value["rate"]
    correct = value.get("correct", value.get("included"))
    return f"{rate} ({correct}/{value['n']})"


def report(metrics: dict, earlier: dict | None, model: str) -> None:
    print(f"model: {model}")
    print(f"cases: {metrics['n_cases']}")
    print(f"labelled signals: {metrics['n_signals']}")
    earlier_metrics = (earlier or {}).get("metrics") or {}
    for name in METRICS:
        current = metrics.get(name)
        line = f"{name}: {_shown(current)}"
        before = earlier_metrics.get(name)
        current_rate = _rate_number(current)
        before_rate = _rate_number(before)
        if before is not None and current_rate is not None and before_rate is not None:
            change = round(current_rate - before_rate, 3)
            sign = "+" if change > 0 else ""
            line += f"  previous {_shown(before)}  change {sign}{change}"
        print(line)
    if earlier:
        earlier_model = earlier.get("model") or "not recorded"
        print(
            f"compared with prompt {earlier.get('prompt_version')} {earlier.get('prompt_checksum', '')[:8]}"
            f" on {earlier_model}"
        )
    else:
        print("no previous run to compare")
    if metrics["weaknesses"]:
        print("weaknesses:")
        for line in metrics["weaknesses"]:
            print(f"  {line}")
    else:
        print("weaknesses: none on this labelled set")


def main() -> None:
    cases = load_cases()
    if not cases:
        raise SystemExit("no cases in eval/signal_insights/cases")
    conn = open_database_connection()
    try:
        prompt = load_prompt(conn, SKILL)
        version = (select_active_prompt_version(conn, SKILL) or ["v1"])[0]
        checksum = hashlib.sha256(prompt.encode()).hexdigest()
        model = model_name("eval")
        run_id = f"eval-signal-insights-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
        case_scores = []
        for case in cases:
            prepared = build_signal_input(case_input(case)["signals"])
            try:
                parsed = ask(conn, prompt, version, case, run_id)
            except Exception as exc:
                print(f"{case['id']}: call failed: {exc}")
                parsed = None
            case_scores.append(score_case(case, prepared, parsed))
        metrics = combine(case_scores)
        earlier = previous_run(conn, checksum, model)
        stored_metrics = {name: metrics[name] for name in ("n_cases", "n_signals", *METRICS)}
        insert_eval_run(conn, SKILL, version, checksum, model, stored_metrics, metrics["weaknesses"])
        report(metrics, earlier, model)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
