"""Records each model decision. The insert statements live in db/sql_statements.py."""

from __future__ import annotations

from pipeline.db.sql_statements import (
    insert_llm_call_accepted,
    insert_llm_call_ai_disabled,
    insert_llm_call_budget_exceeded,
    insert_llm_call_error,
    select_llm_call_spend,
    select_llm_period_spend,
    update_llm_call_validation,
)


class Tracer:
    def __init__(
        self,
        conn,
        run_id,
        skill,
        skill_version="v1",
        prompt_version="v1",
        model=None,
        account_domain=None,
    ):
        self.conn = conn
        self.run_id = run_id
        self.skill = skill
        self.skill_version = skill_version
        self.prompt_version = prompt_version
        self.model = model
        self.account_domain = account_domain

    def spent(self) -> tuple[int, float]:
        row = select_llm_call_spend(self.conn, self.run_id)
        return int(row[0]), float(row[1])

    def spent_since(self, start) -> float:
        row = select_llm_period_spend(self.conn, start)
        return float(row[0])

    def accepted(
        self,
        *,
        messages,
        response_text,
        parsed_output,
        input_tokens,
        output_tokens,
        cost_usd,
        latency_ms,
    ) -> int:
        return insert_llm_call_accepted(
            self.conn,
            run_id=self.run_id,
            skill=self.skill,
            skill_version=self.skill_version,
            prompt_version=self.prompt_version,
            model=self.model,
            account_domain=self.account_domain,
            request_messages=messages,
            response_text=response_text,
            parsed_output=parsed_output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    def error(self, *, messages, error, latency_ms=0) -> None:
        insert_llm_call_error(
            self.conn,
            run_id=self.run_id,
            skill=self.skill,
            skill_version=self.skill_version,
            prompt_version=self.prompt_version,
            model=self.model,
            account_domain=self.account_domain,
            request_messages=messages,
            latency_ms=latency_ms,
            error=error,
        )

    def budget_exceeded(self, *, error) -> None:
        insert_llm_call_budget_exceeded(
            self.conn,
            run_id=self.run_id,
            skill=self.skill,
            model=self.model,
            account_domain=self.account_domain,
            error=error,
        )

    def ai_disabled(self, *, error) -> None:
        insert_llm_call_ai_disabled(
            self.conn,
            run_id=self.run_id,
            skill=self.skill,
            skill_version=self.skill_version,
            prompt_version=self.prompt_version,
            model=self.model,
            account_domain=self.account_domain,
            error=error,
        )


def mark_validation(conn, call_id, passed: bool) -> None:
    if call_id is None:
        return
    update_llm_call_validation(conn, call_id, passed)
