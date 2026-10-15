"""Write a company overview for the top accounts.

Search the web for the stored company name, or the domain when that name is
blank. The Tavily response is saved on tavily_responses and reused when the
query is unchanged. One model call turns the snippets into a name and a long overview.
The response shape is OUTPUT_SCHEMA, passed to the model call. A saved
overview is left alone. A failed search or a failed answer is tried twice
more. If it still fails, that account is skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
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
    save_company_overview,
    save_tavily_response,
    select_accounts_for_company_overview,
    select_active_prompt_version,
    select_llm_call_spend,
    select_tavily_response,
)
from pipeline.logger import get_logger

logger = get_logger("generate_company_overview")

TOP_ACCOUNTS = 20
ATTEMPTS = 3
SEARCH_RESULTS = 8
SKILL = "company-overview"
_STRING_OR_NULL = {"type": ["string", "null"]}
_METRIC = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "metric": {"type": "string"},
        "value": {"type": "string"},
        "period": _STRING_OR_NULL,
        "source_url": {"type": "string"},
    },
    "required": ["metric", "value", "period", "source_url"],
}
_FACT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fact": {"type": "string"},
        "source_url": {"type": "string"},
    },
    "required": ["fact", "source_url"],
}
_SOURCE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "url": {"type": "string"},
        "published_date": _STRING_OR_NULL,
    },
    "required": ["title", "url", "published_date"],
}
OUTPUT_SCHEMA = {
    "name": "company_overview",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "company_name": {"type": "string"},
            "website": _STRING_OR_NULL,
            "executive_summary": {"type": "string"},
            "industry": _STRING_OR_NULL,
            "headquarters": _STRING_OR_NULL,
            "company_type": _STRING_OR_NULL,
            "company_history": _STRING_OR_NULL,
            "operating_regions": {"type": "array", "items": {"type": "string"}},
            "products_and_services": {"type": "array", "items": {"type": "string"}},
            "business_focus": {"type": "array", "items": {"type": "string"}},
            "mission_and_vision": _STRING_OR_NULL,
            "ownership_summary": _STRING_OR_NULL,
            "scale_metrics": {"type": "array", "items": _METRIC},
            "key_facts": {"type": "array", "items": _FACT},
            "sources": {"type": "array", "items": _SOURCE},
        },
        "required": [
            "company_name",
            "website",
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
            "scale_metrics",
            "key_facts",
            "sources",
        ],
    },
}


def search_query(company_name: str | None, domain: str) -> str:
    subject = (company_name or "").strip() or domain
    return f"Give me information about {subject}"


def snippets_from_response(payload) -> list[dict]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []
    snippets = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content and not item.get("title"):
            continue
        snippets.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "content": content[:2000],
            }
        )
    return snippets


def fetch_tavily(query: str) -> dict | None:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        logger.error("company search skipped", reason="TAVILY_API_KEY is not set")
        return None
    body = json.dumps({"api_key": key, "query": query, "max_results": SEARCH_RESULTS}).encode()
    request = urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
    except Exception as exc:
        logger.error("company search failed", query=query, error=str(exc))
        return None
    return payload if isinstance(payload, dict) else None


def research_for_account(conn, domain: str, query: str) -> dict | None:
    """Return a saved Tavily response when the query matches. Otherwise search once and save it."""
    stored = select_tavily_response(conn, domain)
    if stored and stored.get("query") == query:
        payload = stored.get("response")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        if snippets_from_response(payload):
            return payload
    payload = fetch_tavily(query)
    if not snippets_from_response(payload):
        return None
    save_tavily_response(conn, domain, query, payload)
    return payload


def read_company_overview(parsed) -> tuple[str | None, str] | None:
    if not isinstance(parsed, dict):
        return None
    summary = str(parsed.get("executive_summary") or "").strip()
    name = str(parsed.get("company_name") or "").strip()
    if not summary and not name:
        return None
    return name or None, json.dumps(parsed)


def write_overview(conn, prompt: str, version: str, domain: str, company_name: str | None, run_id: str) -> bool:
    query = search_query(company_name, domain)
    for attempt in range(1, ATTEMPTS + 1):
        research = research_for_account(conn, domain, query)
        if not research:
            logger.error("company search empty", account_domain=domain, attempt=attempt)
            continue
        try:
            result = complete(
                conn,
                skill=SKILL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "RESEARCH RESULTS:\n" + json.dumps(research, default=str)},
                ],
                run_id=run_id,
                account_domain=domain,
                prompt_version=version,
                output_schema=OUTPUT_SCHEMA,
                model=model_name("agent"),
            )
        except (BudgetExceeded, AiDisabled):
            raise
        except Exception as exc:
            logger.error("company overview call failed", account_domain=domain, attempt=attempt, error=str(exc))
            continue
        written = read_company_overview(result.get("parsed"))
        mark_validation(conn, result.get("call_id"), written is not None)
        if written is None:
            logger.error("company overview rejected", account_domain=domain, attempt=attempt, reason="overview is empty")
            continue
        name, overview = written
        save_company_overview(conn, domain, name, overview)
        return True
    logger.error("company overview skipped", account_domain=domain, reason="still failing after retries")
    return False


def main(conn: psycopg.Connection, *, limit: int = TOP_ACCOUNTS, account: str | None = None, run_id: str | None = None) -> None:
    if not ai_enabled():
        logger.error("company overviews not started", reason="AI_ENABLED is off, so the model cannot be called")
        return
    prompt = load_prompt(conn, SKILL)
    version = (select_active_prompt_version(conn, SKILL) or ["v1"])[0]
    if run_id is None:
        run_id = f"company-overview-{version}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
    created = []
    skipped = []
    failed = []
    try:
        for row in select_accounts_for_company_overview(conn, limit, account):
            domain = row["account_domain"]
            if account is None and str(row.get("company_overview") or "").strip():
                skipped.append(domain)
                continue
            if write_overview(conn, prompt, version, domain, row.get("company_name"), run_id):
                created.append(domain)
            else:
                failed.append(domain)
    except (BudgetExceeded, AiDisabled) as exc:
        logger.error("company overviews stopped", reason=type(exc).__name__, error=str(exc))
    spent = select_llm_call_spend(conn, run_id)
    logger.info(
        "company overviews finished",
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
    parser = argparse.ArgumentParser(description="Write company overviews for the top accounts.")
    parser.add_argument("--limit", type=int, default=TOP_ACCOUNTS)
    parser.add_argument("--account", default=None, help="Rewrite this domain even if an overview is already saved.")
    args = parser.parse_args()
    connection = open_database_connection()
    try:
        main(connection, limit=args.limit, account=args.account)
    finally:
        connection.close()
