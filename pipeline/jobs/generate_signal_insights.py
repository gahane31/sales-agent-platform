"""Write signal insights for the top accounts.

The input is the saved company overview plus the signal rows the Signals tab
shows. Priority is high, medium, low, or informational from the stored weights.
The model writes the section summary from the overview and rewrites each card.
The response shape is OUTPUT_SCHEMA, passed to the model call. A saved insight
is left alone. A bad answer is tried twice more. If it still fails, that
account is skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

project_directory = str(Path.cwd())
if project_directory not in sys.path:
    sys.path.insert(0, project_directory)

from pipeline.ai.llm import AiDisabled, BudgetExceeded, ai_enabled, complete, load_prompt, mark_validation
from pipeline.ai.models import model_name
from pipeline.db.postgres_client import open_database_connection
from pipeline.db.sql_statements import (
    save_signal_insights,
    select_accounts_for_signal_insights,
    select_active_prompt_version,
    select_llm_call_spend,
    select_signals_for_insight,
)
from pipeline.logger import get_logger

logger = get_logger("generate_signal_insights")

TOP_ACCOUNTS = 20
ATTEMPTS = 3
SKILL = "signal-insights"
_SIGNAL = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "signal_type": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "evidence_summary": {"type": "string"},
        "evidence_details": {"type": "array", "items": {"type": "string"}},
        "product_angle": {"type": "string"},
        "priority": {"type": "string", "enum": ["high", "medium", "low", "informational"]},
    },
    "required": [
        "signal_type",
        "title",
        "summary",
        "evidence_summary",
        "evidence_details",
        "product_angle",
        "priority",
    ],
}
_OVERVIEW_FIELDS = (
    "executive_summary",
    "industry",
    "headquarters",
    "company_type",
    "company_history",
    "operating_regions",
    "products_and_services",
    "business_focus",
    "mission_and_vision",
    "ownership_summary",
)


OUTPUT_SCHEMA = {
    "name": "signal_insights",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "company_name": {"type": "string"},
            "section_summary": {"type": "string"},
            "signals": {"type": "array", "items": _SIGNAL},
        },
        "required": ["company_name", "section_summary", "signals"],
    },
}


def priority_label(need_weight, timing_weight=None) -> str:
    need = int(need_weight or 0)
    if need >= 70:
        return "high"
    if need >= 40:
        return "medium"
    if need >= 1:
        return "low"
    return "informational"


def _evidence_for_model(evidence) -> dict:
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            return {}
    if not isinstance(evidence, dict):
        return {}
    kept = {}
    for key, value in evidence.items():
        if key == "scan_ids":
            continue
        if isinstance(value, list) and len(value) > 8:
            kept[key] = value[:8]
        else:
            kept[key] = value
    return kept


def _plain_meaning(row: dict, evidence: dict) -> str:
    text = str(row.get("human_meaning") or row.get("description") or "")
    if evidence.get("scanned_on"):
        text = text.replace("{date}", str(evidence["scanned_on"])[:10])
    if evidence.get("days_left") is not None:
        text = text.replace("{N}", str(evidence["days_left"]))
    return text


def overview_for_insights(raw) -> dict:
    """Keep the company description and drop source URLs the summary must not cite."""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return {"executive_summary": text}
    if not isinstance(raw, dict):
        return {}
    kept = {}
    for key in _OVERVIEW_FIELDS:
        value = raw.get(key)
        if value is None or value == "" or value == []:
            continue
        kept[key] = value
    metrics = []
    for item in raw.get("scale_metrics") or []:
        if not isinstance(item, dict):
            continue
        metric = str(item.get("metric") or "").strip()
        value = str(item.get("value") or "").strip()
        if not metric or not value:
            continue
        entry = {"metric": metric, "value": value}
        period = str(item.get("period") or "").strip()
        if period:
            entry["period"] = period
        metrics.append(entry)
    if metrics:
        kept["scale_metrics"] = metrics
    facts = []
    for item in raw.get("key_facts") or []:
        if isinstance(item, dict):
            fact = str(item.get("fact") or "").strip()
        else:
            fact = str(item or "").strip()
        if fact:
            facts.append(fact)
    if facts:
        kept["key_facts"] = facts
    return kept


def insight_request(company_name: str, overview, prepared: list[dict]) -> dict:
    return {
        "company_name": company_name,
        "company_overview": overview_for_insights(overview),
        "signals": prepared,
    }


def build_signal_input(rows: list[dict]) -> list[dict]:
    prepared = []
    for row in rows:
        evidence = _evidence_for_model(row.get("evidence"))
        prepared.append(
            {
                "signal_type": row.get("signal_type"),
                "human_label": row.get("human_label"),
                "human_meaning": _plain_meaning(row, evidence),
                "description": row.get("description"),
                "catalog_product_angle": row.get("product_angle"),
                "priority": priority_label(row.get("need_weight"), row.get("timing_weight")),
                "need_weight": int(row.get("need_weight") or 0),
                "scan_count": int(row.get("scan_count") or 0),
                "evidence": evidence,
            }
        )
    prepared.sort(key=lambda item: -item["need_weight"])
    return prepared


def read_signal_insights(parsed, prepared: list[dict]) -> dict | None:
    if not isinstance(parsed, dict) or not isinstance(parsed.get("signals"), list):
        return None
    by_type = {}
    for item in parsed["signals"]:
        if not isinstance(item, dict):
            return None
        signal_type = str(item.get("signal_type") or "").strip()
        if not signal_type or signal_type in by_type:
            return None
        if not str(item.get("title") or "").strip() or not str(item.get("summary") or "").strip():
            return None
        if not str(item.get("product_angle") or "").strip():
            return None
        details = item.get("evidence_details")
        if not isinstance(details, list) or not all(isinstance(detail, str) for detail in details):
            return None
        by_type[signal_type] = item
    if not str(parsed.get("section_summary") or "").strip():
        return None
    ordered = []
    for source in prepared:
        item = by_type.get(source["signal_type"])
        if item is None:
            return None
        ordered.append(
            {
                "signal_type": source["signal_type"],
                "title": str(item["title"]).strip(),
                "summary": str(item["summary"]).strip(),
                "evidence_summary": str(item.get("evidence_summary") or "").strip(),
                "evidence_details": [detail.strip() for detail in item["evidence_details"] if detail.strip()],
                "product_angle": str(item["product_angle"]).strip(),
                "priority": source["priority"],
            }
        )
    return {
        "company_name": str(parsed.get("company_name") or "").strip(),
        "section_summary": str(parsed.get("section_summary") or "").strip(),
        "signals": ordered,
    }


def write_insights(conn, prompt: str, version: str, domain: str, company_name: str, overview, run_id: str) -> bool:
    prepared = build_signal_input(select_signals_for_insight(conn, domain))
    if not prepared:
        logger.error("signal insights skipped", account_domain=domain, reason="no signals")
        return False
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(insight_request(company_name, overview, prepared), default=str)},
    ]
    for attempt in range(1, ATTEMPTS + 1):
        try:
            result = complete(
                conn,
                skill=SKILL,
                messages=messages,
                run_id=run_id,
                account_domain=domain,
                prompt_version=version,
                output_schema=OUTPUT_SCHEMA,
                model=model_name("agent"),
            )
        except (BudgetExceeded, AiDisabled):
            raise
        except Exception as exc:
            logger.error("signal insights call failed", account_domain=domain, attempt=attempt, error=str(exc))
            continue
        written = read_signal_insights(result.get("parsed"), prepared)
        mark_validation(conn, result.get("call_id"), written is not None)
        if written is None:
            logger.error("signal insights rejected", account_domain=domain, attempt=attempt, reason="signals do not match the input")
            continue
        if not written["company_name"]:
            written["company_name"] = company_name
        save_signal_insights(conn, domain, json.dumps(written))
        return True
    logger.error("signal insights skipped", account_domain=domain, reason="still failing after retries")
    return False


def main(conn: psycopg.Connection, *, limit: int = TOP_ACCOUNTS, account: str | None = None, run_id: str | None = None) -> None:
    if not ai_enabled():
        logger.error("signal insights not started", reason="AI_ENABLED is off, so the model cannot be called")
        return
    prompt = load_prompt(conn, SKILL)
    version = (select_active_prompt_version(conn, SKILL) or ["v1"])[0]
    if run_id is None:
        run_id = f"signal-insights-{version}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
    created = []
    skipped = []
    failed = []
    try:
        for row in select_accounts_for_signal_insights(conn, limit, account):
            domain = row["account_domain"]
            if account is None and str(row.get("signal_insights") or "").strip():
                skipped.append(domain)
                continue
            if write_insights(
                conn,
                prompt,
                version,
                domain,
                row.get("company_name") or domain,
                row.get("company_overview"),
                run_id,
            ):
                created.append(domain)
            else:
                failed.append(domain)
    except (BudgetExceeded, AiDisabled) as exc:
        logger.error("signal insights stopped", reason=type(exc).__name__, error=str(exc))
    spent = select_llm_call_spend(conn, run_id)
    logger.info(
        "signal insights finished",
        run_id=run_id,
        prompt_version=version,
        limit=limit,
        account=account,
        created=created,
        skipped=skipped,
        failed=failed,
        calls=int(spent[0]),
        cost_usd=float(spent[1]),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write signal insights for the top accounts.")
    parser.add_argument("--limit", type=int, default=TOP_ACCOUNTS)
    parser.add_argument("--account", default=None, help="Rewrite this domain even if insights are already saved.")
    args = parser.parse_args()
    connection = open_database_connection()
    try:
        main(connection, limit=args.limit, account=args.account)
    finally:
        connection.close()
