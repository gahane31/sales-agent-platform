"""Talks to the OpenAI API. Knows nothing about the database."""

from __future__ import annotations

import time
from dataclasses import dataclass

from openai import OpenAI


@dataclass
class ChatResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class OpenAIClient:
    def __init__(self, api_key: str):
        self._client = OpenAI(api_key=api_key)

    def chat(self, model: str, messages: list[dict], temperature: int = 0, output_schema: dict | None = None) -> ChatResult:
        started = time.monotonic()
        request = {"model": model, "messages": messages, "temperature": temperature}
        if model.startswith("gpt-6"):
            request["reasoning_effort"] = "none"
        if output_schema is not None:
            request["response_format"] = {"type": "json_schema", "json_schema": output_schema}
        response = self._client.chat.completions.create(**request)
        text = response.choices[0].message.content or ""
        usage = response.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        return ChatResult(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
