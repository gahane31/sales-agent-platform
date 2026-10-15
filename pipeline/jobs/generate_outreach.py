"""Write an outreach brief for the top accounts.

The input is the saved company overview, the stored signals, and the signal
catalog. One model call returns the lead angle, suggested roles, and a first
email. The response shape is OUTPUT_SCHEMA, passed to the model call. A saved
brief is left alone. A bad answer is tried twice more. If it still fails, that
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
    save_outreach,
    select_accounts_for_outreach,
    select_active_prompt_version,
    select_llm_call_spend,
    select_signal_catalog,
    select_signals_for_insight,
)
from pipeline.jobs.generate_signal_insights import build_signal_input
from pipeline.logger import get_logger

logger = get_logger("generate_outreach")

TOP_ACCOUNTS = 20
ATTEMPTS = 3
SKILL = "outreach"
_ANGLE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "product_category": {"type": "string"},
        "reason": {"type": "string"},
        "supporting_signal_types": {"type": "array", "items": {"type": "string"}},
        "suggested_talking_point": {"type": "string"},
    },
    "required": ["product_category", "reason", "supporting_signal_types", "suggested_talking_point"],
}
_PERSONA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "job_title": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["job_title", "reason"],
}
_DRAFT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "channel": {"type": "string", "enum": ["email"]},
        "subject": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["channel", "subject", "body"],
}
_SECONDARY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "product_category": {"type": "string"},
        "talking_point": {"type": "string"},
        "supporting_signal_types": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["product_category", "talking_point", "supporting_signal_types"],
}
OUTPUT_SCHEMA = {
    "name": "outreach",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "company_name": {"type": "string"},
            "primary_outreach_angle": _ANGLE,
            "target_personas": {"type": "array", "items": _PERSONA, "maxItems": 3},
            "contact_status": {"type": "string", "enum": ["not_identified"]},
            "outreach_draft": _DRAFT,
            "secondary_angles": {"type": "array", "items": _SECONDARY, "maxItems": 3},
            "review_required": {"type": "boolean"},
        },
        "required": [
            "company_name",
            "primary_outreach_angle",
            "target_personas",
            "contact_status",
            "outreach_draft",
            "secondary_angles",
            "review_required",
        ],
    },
}


def _overview_for_model(raw) -> dict | str:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        return parsed if isinstance(parsed, dict) else raw
    return raw or {}


def _names(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item).strip()]


def _known(types: list[str], allowed: set[str]) -> bool:
    return bool(types) and set(types).issubset(allowed)


def read_outreach(parsed, signal_types: set[str], company_name: str) -> dict | None:
    if not isinstance(parsed, dict):
        return None
    angle = parsed.get("primary_outreach_angle")
    draft = parsed.get("outreach_draft")
    personas = parsed.get("target_personas")
    secondary = parsed.get("secondary_angles")
    if not isinstance(angle, dict) or not isinstance(draft, dict):
        return None
    if not isinstance(personas, list) or not personas or len(personas) > 3:
        return None
    if not isinstance(secondary, list) or len(secondary) > 3:
        return None
    primary_types = _names(angle.get("supporting_signal_types"))
    if not _known(primary_types, signal_types):
        return None
    if not str(angle.get("product_category") or "").strip() or not str(angle.get("reason") or "").strip():
        return None
    if not str(angle.get("suggested_talking_point") or "").strip():
        return None
    if not str(draft.get("subject") or "").strip() or not str(draft.get("body") or "").strip():
        return None
    clean_personas = []
    for persona in personas:
        if not isinstance(persona, dict):
            return None
        title = str(persona.get("job_title") or "").strip()
        reason = str(persona.get("reason") or "").strip()
        if not title or not reason:
            return None
        clean_personas.append({"job_title": title, "reason": reason})
    clean_secondary = []
    for item in secondary:
        if not isinstance(item, dict):
            return None
        types = _names(item.get("supporting_signal_types"))
        category = str(item.get("product_category") or "").strip()
        talking_point = str(item.get("talking_point") or "").strip()
        if not category or not talking_point or not _known(types, signal_types):
            return None
        clean_secondary.append(
            {
                "product_category": category,
                "talking_point": talking_point,
                "supporting_signal_types": types,
            }
        )
    return {
        "company_name": str(parsed.get("company_name") or "").strip() or company_name,
        "primary_outreach_angle": {
            "product_category": str(angle["product_category"]).strip(),
            "reason": str(angle["reason"]).strip(),
            "supporting_signal_types": primary_types,
            "suggested_talking_point": str(angle["suggested_talking_point"]).strip(),
        },
        "target_personas": clean_personas,
        "contact_status": "not_identified",
        "outreach_draft": {
            "channel": "email",
            "subject": str(draft["subject"]).strip(),
            "body": str(draft["body"]).strip(),
        },
        "secondary_angles": clean_secondary,
        "review_required": True,
    }


def write_outreach(conn, prompt: str, version: str, row: dict, catalog: list[dict], run_id: str) -> bool:
    domain = row["account_domain"]
    company_name = row.get("company_name") or domain
    signals = build_signal_input(select_signals_for_insight(conn, domain))
    if not signals:
        logger.error("outreach skipped", account_domain=domain, reason="no signals")
        return False
    present = {item["signal_type"] for item in signals}
    relevant_catalog = [item for item in catalog if item.get("signal_type") in present]
    user = {
        "company_overview": _overview_for_model(row.get("company_overview")),
        "security_signals": signals,
        "signal_catalog": relevant_catalog,
    }
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(user, default=str)},
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
            logger.error("outreach call failed", account_domain=domain, attempt=attempt, error=str(exc))
            continue
        written = read_outreach(result.get("parsed"), present, company_name)
        mark_validation(conn, result.get("call_id"), written is not None)
        if written is None:
            logger.error("outreach rejected", account_domain=domain, attempt=attempt, reason="response does not match the signals")
            continue
        save_outreach(conn, domain, json.dumps(written))
        return True
    logger.error("outreach skipped", account_domain=domain, reason="still failing after retries")
    return False


def main(conn: psycopg.Connection, *, limit: int = TOP_ACCOUNTS, account: str | None = None, run_id: str | None = None) -> None:
    if not ai_enabled():
        logger.error("outreach not started", reason="AI_ENABLED is off, so the model cannot be called")
        return
    prompt = load_prompt(conn, SKILL)
    version = (select_active_prompt_version(conn, SKILL) or ["v1"])[0]
    if run_id is None:
        run_id = f"outreach-{version}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
    catalog = select_signal_catalog(conn)
    created = []
    skipped = []
    failed = []
    try:
        for row in select_accounts_for_outreach(conn, limit, account):
            domain = row["account_domain"]
            if account is None and str(row.get("outreach") or "").strip():
                skipped.append(domain)
                continue
            if write_outreach(conn, prompt, version, row, catalog, run_id):
                created.append(domain)
            else:
                failed.append(domain)
    except (BudgetExceeded, AiDisabled) as exc:
        logger.error("outreach stopped", reason=type(exc).__name__, error=str(exc))
    spent = select_llm_call_spend(conn, run_id)
    logger.info(
        "outreach finished",
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
    parser = argparse.ArgumentParser(description="Write outreach briefs for the top accounts.")
    parser.add_argument("--limit", type=int, default=TOP_ACCOUNTS)
    parser.add_argument("--account", default=None, help="Rewrite this domain even if an outreach brief is already saved.")
    args = parser.parse_args()
    connection = open_database_connection()
    try:
        main(connection, limit=args.limit, account=args.account)
    finally:
        connection.close()
