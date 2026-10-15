import pytest

from pipeline.ai.llm import AiDisabled, BudgetExceeded, complete
from pipeline.ai.openai_client import ChatResult


class FakeClient:
    def __init__(self, text: str):
        self.text = text

    def chat(self, model, messages, temperature=0, output_schema=None):
        self.output_schema = output_schema
        return ChatResult(
            text=self.text,
            model=model,
            input_tokens=1000,
            output_tokens=100,
            latency_ms=5,
        )


class FakeTracer:
    def __init__(self, *args, **kwargs):
        self.events = []

    def spent(self):
        return 0, 0.0

    def spent_since(self, start):
        return 0.0

    def accepted(self, **kwargs):
        self.events.append(("accepted", kwargs))
        return 7

    def error(self, **kwargs):
        self.events.append(("error", kwargs))

    def budget_exceeded(self, **kwargs):
        self.events.append(("budget_exceeded", kwargs))

    def ai_disabled(self, **kwargs):
        self.events.append(("ai_disabled", kwargs))


def _settings(conn, key, default):
    values = {
        "default_model": "gpt-4.1-mini",
        "max_calls_per_run": 2500,
        "max_cost_usd_per_run": 1.0,
        "max_cost_usd_per_day": 2.0,
        "max_cost_usd_per_month": 10.0,
    }
    return values.get(key, default)


def _patch(monkeypatch):
    monkeypatch.setattr("pipeline.ai.llm.get_setting", _settings)
    monkeypatch.setattr("pipeline.ai.llm.Tracer", FakeTracer)


def test_happy_path_parses_json_and_prices_tokens(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setenv("AI_ENABLED", "true")
    result = complete(
        None,
        skill="outreach",
        messages=[{"role": "user", "content": "evidence"}],
        run_id="test",
        client=FakeClient('{"brief": "mit.edu has a known-exploited vulnerability."}'),
    )
    assert result["parsed"] == {"brief": "mit.edu has a known-exploited vulnerability."}
    assert result["model"] == "gpt-6-luna"
    assert result["cost_usd"] == pytest.approx(0.00015)
    assert result["call_id"] == 7


def test_non_json_response_leaves_parsed_empty(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setenv("AI_ENABLED", "true")
    result = complete(
        None,
        skill="outreach",
        messages=[{"role": "user", "content": "evidence"}],
        run_id="test",
        client=FakeClient("not json"),
    )
    assert result["parsed"] is None
    assert result["text"] == "not json"


def test_output_schema_is_sent_with_the_call(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setenv("AI_ENABLED", "true")
    schema = {"name": "company_overview", "strict": True, "schema": {"type": "object"}}
    client = FakeClient('{"company_name": "Example", "overview": "A hospital."}')
    complete(
        None,
        skill="company-overview",
        messages=[{"role": "user", "content": "snippets"}],
        run_id="test",
        output_schema=schema,
        client=client,
    )
    assert client.output_schema == schema


def test_run_ceiling_refuses_before_the_client_is_used(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setenv("AI_ENABLED", "true")

    class OverRun(FakeTracer):
        def spent(self):
            return 1, 1.0

    class Boom:
        def chat(self, model, messages, temperature=0, output_schema=None):
            raise AssertionError("client should not be called")

    monkeypatch.setattr("pipeline.ai.llm.Tracer", OverRun)
    with pytest.raises(BudgetExceeded):
        complete(
            None,
            skill="signal-insights",
            messages=[{"role": "user", "content": "signals"}],
            run_id="test",
            client=Boom(),
        )


def test_day_ceiling_refuses_before_the_client_is_used(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setenv("AI_ENABLED", "true")

    class OverDay(FakeTracer):
        def spent_since(self, start):
            return 2.0

    class Boom:
        def chat(self, model, messages, temperature=0, output_schema=None):
            raise AssertionError("client should not be called")

    monkeypatch.setattr("pipeline.ai.llm.Tracer", OverDay)
    with pytest.raises(BudgetExceeded):
        complete(
            None,
            skill="outreach",
            messages=[{"role": "user", "content": "brief"}],
            run_id="test",
            client=Boom(),
        )


def test_ai_disabled_raises_before_the_client_is_used(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setenv("AI_ENABLED", "false")

    class Boom:
        def chat(self, model, messages, temperature=0, output_schema=None):
            raise AssertionError("client should not be called")

    with pytest.raises(AiDisabled):
        complete(
            None,
            skill="outreach",
            messages=[{"role": "user", "content": "evidence"}],
            run_id="test",
            client=Boom(),
        )
