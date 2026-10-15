"""One pass rate for an eval run, and the same rate for the run before it.

Any agent can use this. The page does not name the checks. It counts stored
cases that have no weakness line. A new eval shows up when its rows are saved.
"""

from __future__ import annotations

import json


def _load(value):
    if isinstance(value, str):
        return json.loads(value)
    return value or {}


def _percent(passed: int, total: int) -> int:
    return round(100 * passed / total)


def _failed_cases(weaknesses: list) -> set[str]:
    failed = set()
    for line in weaknesses:
        head = str(line).split(":", 1)[0].strip()
        case_id = head.split()[0] if head else ""
        if case_id:
            failed.add(case_id)
    return failed


def _case_counts(metrics: dict, weaknesses: list) -> tuple[int, int] | None:
    total = metrics.get("n_cases")
    if not isinstance(total, int) or total <= 0:
        return None
    failed = _failed_cases(weaknesses)
    return max(total - len(failed), 0), total


def _score(metrics: dict, weaknesses: list, model: str | None) -> dict | None:
    counts = _case_counts(metrics, weaknesses)
    if counts is None:
        return None
    passed, total = counts
    return {
        "model": model or "not recorded",
        "percent": _percent(passed, total),
        "passed": passed,
        "total": total,
    }


def summarize(latest: dict, earlier: dict | None) -> dict | None:
    metrics = _load(latest.get("metrics"))
    weaknesses = _load(latest.get("weaknesses"))
    if not isinstance(weaknesses, list):
        weaknesses = []
    current = _score(metrics, weaknesses, latest.get("model"))
    if current is None:
        return None
    previous = None
    if earlier:
        earlier_metrics = _load(earlier.get("metrics"))
        earlier_weaknesses = _load(earlier.get("weaknesses"))
        if not isinstance(earlier_weaknesses, list):
            earlier_weaknesses = []
        previous = _score(earlier_metrics, earlier_weaknesses, earlier.get("model"))
    return {"current": current, "previous": previous}
