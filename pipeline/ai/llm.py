"""The only permitted entry point for a model call.

Callers use complete(). They do not construct a client. Evals may pass client=
with a fake. Transport lives in openai_client, prices in cost, rows in trace.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import psycopg

from pipeline.ai.cost import estimate_cost
from pipeline.ai.models import model_name
from pipeline.ai.openai_client import OpenAIClient
from pipeline.ai.prompt_registry import load_prompt, register_prompts
from pipeline.ai.settings import get_setting
from pipeline.ai.trace import Tracer, mark_validation
from pipeline.logger import get_logger

logger = get_logger("llm")

__all__ = [
    "AiDisabled",
    "BudgetExceeded",
    "ai_enabled",
    "complete",
    "load_prompt",
    "mark_validation",
    "register_prompts",
]


class BudgetExceeded(Exception):
    pass


class AiDisabled(Exception):
    pass


def ai_enabled() -> bool:
    return os.environ.get("AI_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def check_budget(conn, tracer: Tracer, *, skill: str, run_id: str, account_domain: str | None, max_cost: float | None) -> None:
    """Refuse the call when this run, today, or this month is already at its ceiling."""
    calls, spent = tracer.spent()
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    day_spent = tracer.spent_since(day_start)
    month_spent = tracer.spent_since(month_start)
    max_calls = int(get_setting(conn, "max_calls_per_run", 2500))
    run_ceiling = float(max_cost if max_cost is not None else get_setting(conn, "max_cost_usd_per_run", 1.0))
    day_ceiling = float(get_setting(conn, "max_cost_usd_per_day", 2.0))
    month_ceiling = float(get_setting(conn, "max_cost_usd_per_month", 10.0))
    if calls >= max_calls or spent >= run_ceiling:
        reason = f"run spent {spent:.4f} across {calls} calls; run ceiling {run_ceiling}"
    elif day_spent >= day_ceiling:
        reason = f"day spent {day_spent:.4f}; day ceiling {day_ceiling}"
    elif month_spent >= month_ceiling:
        reason = f"month spent {month_spent:.4f}; month ceiling {month_ceiling}"
    else:
        return
    logger.warning(
        "budget exceeded",
        skill=skill,
        run_id=run_id,
        account_domain=account_domain,
        calls=calls,
        spent=round(spent, 4),
        day_spent=round(day_spent, 4),
        month_spent=round(month_spent, 4),
        reason=reason,
    )
    tracer.budget_exceeded(error=reason)
    raise BudgetExceeded(reason)


def complete(
    conn: psycopg.Connection,
    *,
    skill: str,
    messages: list[dict],
    run_id: str,
    account_domain: str | None = None,
    skill_version: str = "v1",
    prompt_version: str = "v1",
    max_cost: float | None = None,
    output_schema: dict | None = None,
    model: str | None = None,
    client=None,
) -> dict:
    model = model or model_name("agent")
    tracer = Tracer(conn, run_id, skill, skill_version=skill_version, prompt_version=prompt_version, model=model, account_domain=account_domain)
    check_budget(conn, tracer, skill=skill, run_id=run_id, account_domain=account_domain, max_cost=max_cost)
    if not ai_enabled():
        logger.info("model call skipped", reason="AI_ENABLED is false", skill=skill, run_id=run_id)
        tracer.ai_disabled(error="AI_ENABLED is false")
        raise AiDisabled("AI_ENABLED is false")
    if client is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            logger.error("model call refused", reason="OPENAI_API_KEY is not set", skill=skill, run_id=run_id)
            tracer.error(messages=messages, error="OPENAI_API_KEY is not set", latency_ms=0)
            raise AiDisabled("OPENAI_API_KEY is not set")
        client = OpenAIClient(api_key)
    started = time.monotonic()
    try:
        result = client.chat(model, messages, temperature=0, output_schema=output_schema)
    except Exception as exc:
        logger.error("model call failed", skill=skill, run_id=run_id, error=str(exc))
        tracer.error(
            messages=messages,
            error=exc,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise
    cost = estimate_cost(result.model, result.input_tokens, result.output_tokens)
    try:
        parsed = json.loads(result.text)
    except json.JSONDecodeError:
        parsed = None
    call_id = tracer.accepted(
        messages=messages,
        response_text=result.text,
        parsed_output=parsed,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd=cost,
        latency_ms=result.latency_ms,
    )
    return {
        "text": result.text,
        "parsed": parsed,
        "model": result.model,
        "cost_usd": cost,
        "call_id": call_id,
    }
