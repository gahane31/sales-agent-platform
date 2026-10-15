"""Token prices. An unknown model is an error, not a silent fallback rate."""

from __future__ import annotations

# Dollars per million tokens: (input, output).
RATES = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate = RATES.get(model)
    if rate is None:
        for name in sorted(RATES, key=len, reverse=True):
            if model.startswith(name):
                rate = RATES[name]
                break
    if rate is None:
        raise ValueError(f"no price for model {model}")
    rate_in, rate_out = rate
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000
