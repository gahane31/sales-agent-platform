"""Which model each kind of call uses. Change the names here, not in the jobs."""

from __future__ import annotations

MODELS = {
    "agent": "gpt-6-luna",
    "eval": "gpt-6-luna",
}


def model_name(role: str) -> str:
    model = MODELS.get(role)
    if not isinstance(model, str) or not model.strip():
        raise KeyError(f"no model configured for {role}")
    return model.strip()
