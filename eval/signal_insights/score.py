"""Score a signal-insights reply against a labelled case. No model and no database."""

from __future__ import annotations

import json
import re

CVE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")
HOST = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
WEIGHT_WORD = re.compile(r"need_weight|timing_weight|confidence", re.IGNORECASE)
SCORE_WORD = re.compile(r"\bcvss\b|\bepss\b", re.IGNORECASE)


def _blob(*parts) -> str:
    return " ".join(str(part) for part in parts if part).lower()


def _evidence_text(item: dict) -> str:
    details = item.get("evidence_details") or []
    return _blob(item.get("evidence_summary"), " ".join(str(detail) for detail in details))


def _signal_text(item: dict) -> str:
    details = item.get("evidence_details") or []
    return _blob(
        item.get("title"),
        item.get("summary"),
        item.get("evidence_summary"),
        item.get("product_angle"),
        " ".join(str(detail) for detail in details),
    )


def claimed_identifiers(text: str) -> set[str]:
    found = {item.upper() for item in CVE.findall(text or "")}
    found.update(VERSION.findall(text or ""))
    found.update(item.lower() for item in HOST.findall(text or ""))
    found.update(IP.findall(text or ""))
    return found


def _phrases(values) -> list[str]:
    return [str(item).strip() for item in values or [] if str(item).strip()]


def case_input(case: dict) -> dict:
    return case.get("input") or {}


def case_checks(case: dict) -> dict:
    return case.get("checks") or {}


def score_section(case: dict, prepared: list[dict], parsed: dict | None) -> dict:
    """Score the paragraph only. Cards are scored separately."""
    checks = case_checks(case)
    section_raw = str(parsed.get("section_summary") or "") if isinstance(parsed, dict) else ""
    section = section_raw.lower()
    mentions = _phrases(checks.get("section_must_mention"))
    used = bool(mentions) and any(phrase.lower() in section for phrase in mentions)
    known = json.dumps(
        {"overview": case_input(case).get("company_overview") or {}, "signals": prepared},
        default=str,
    ).lower()
    claimed = 0
    correct = 0
    forbidden_phrases = []
    seen = set()
    for phrase in _phrases(checks.get("section_must_not_say")):
        seen.add(phrase.lower())
        forbidden_phrases.append(phrase)
    for label in (checks.get("labels") or {}).values():
        for phrase in _phrases(label.get("must_not_say")):
            if phrase.lower() not in seen:
                seen.add(phrase.lower())
                forbidden_phrases.append(phrase)
    said = next((phrase for phrase in forbidden_phrases if phrase.lower() in section), None)
    weaknesses = []
    if isinstance(parsed, dict) and mentions and not used:
        weaknesses.append(f"{case['id']}: section summary did not use the company overview")
    if isinstance(parsed, dict):
        for identifier in claimed_identifiers(section_raw):
            claimed += 1
            if identifier.lower() in known:
                correct += 1
            else:
                weaknesses.append(f"{case['id']}: section summary invented {identifier}")
        if said:
            weaknesses.append(f"{case['id']}: section summary said {said}")
        if SCORE_WORD.search(section_raw):
            weaknesses.append(f"{case['id']}: section summary included a CVSS or EPSS score")
    return {
        "overview_required": 1 if mentions else 0,
        "overview_hit": 1 if used else 0,
        "section_claimed": claimed,
        "section_correct": correct,
        "section_forbidden": 1 if said and isinstance(parsed, dict) else 0,
        "section_checked": 1 if isinstance(parsed, dict) else 0,
        "weaknesses": weaknesses,
    }


def score_case(case: dict, prepared: list[dict], parsed: dict | None) -> dict:
    """Return counts for one company. Priority is read from the model reply, not overwritten."""
    labels = case_checks(case).get("labels") or {}
    by_type = {}
    if isinstance(parsed, dict):
        for item in parsed.get("signals") or []:
            if isinstance(item, dict) and item.get("signal_type"):
                by_type[item["signal_type"]] = item
    prepared_by_type = {item["signal_type"]: item for item in prepared}
    required = 0
    included = 0
    claimed = 0
    correct_claims = 0
    priority_correct = 0
    labelled = 0
    forbidden = 0
    weaknesses = []
    if parsed is None:
        weaknesses.append(f"{case['id']}: no usable reply")
    section = score_section(case, prepared, parsed)
    weaknesses.extend(section["weaknesses"])
    for signal_type, label in labels.items():
        labelled += 1
        item = by_type.get(signal_type)
        source = prepared_by_type.get(signal_type) or {}
        if item is None:
            weaknesses.append(f"{case['id']} {signal_type}: missing from the reply")
            required += len(label.get("must_include") or [])
            continue
        if str(item.get("priority") or "").strip().lower() == str(label.get("priority") or "").strip().lower():
            priority_correct += 1
        else:
            weaknesses.append(
                f"{case['id']} {signal_type}: priority {item.get('priority')} instead of {label.get('priority')}"
            )
        evidence_text = _evidence_text(item)
        for fact in label.get("must_include") or []:
            required += 1
            if str(fact).lower() in evidence_text:
                included += 1
            else:
                weaknesses.append(f"{case['id']} {signal_type}: missing {fact}")
        known = json.dumps(source.get("evidence") or {}, default=str).lower()
        for identifier in claimed_identifiers(evidence_text):
            claimed += 1
            if identifier.lower() in known:
                correct_claims += 1
            else:
                weaknesses.append(f"{case['id']} {signal_type}: invented {identifier}")
        for phrase in label.get("must_not_say") or []:
            if str(phrase).lower() in _signal_text(item):
                forbidden += 1
                weaknesses.append(f"{case['id']} {signal_type}: said {phrase}")
                break
        catalog = str(source.get("catalog_product_angle") or "").strip().lower()
        written = str(item.get("product_angle") or "").strip().lower()
        if catalog and written == catalog:
            weaknesses.append(f"{case['id']} {signal_type}: product angle copied the catalog phrase {catalog!r}")
        if WEIGHT_WORD.search(_signal_text(item)):
            weaknesses.append(f"{case['id']} {signal_type}: included a weight or confidence")
        if SCORE_WORD.search(_signal_text(item)):
            weaknesses.append(f"{case['id']} {signal_type}: included a CVSS or EPSS score")
        max_hosts = label.get("max_hosts")
        if isinstance(max_hosts, int):
            names = [str(host).lower() for host in (source.get("evidence") or {}).get("hosts") or []]
            listed = [name for name in names if name and name in evidence_text]
            if len(listed) > max_hosts:
                weaknesses.append(
                    f"{case['id']} {signal_type}: listed {len(listed)} hosts; name at most {max_hosts}"
                )
    extra = []
    if isinstance(parsed, dict):
        for item in parsed.get("signals") or []:
            if not isinstance(item, dict):
                continue
            signal_type = item.get("signal_type")
            if signal_type and signal_type not in labels:
                extra.append(str(signal_type))
    if extra:
        weaknesses.append(f"{case['id']}: added {', '.join(extra)}")
    expected_order = case_checks(case).get("signal_order") or []
    actual_order = []
    if isinstance(parsed, dict):
        for item in parsed.get("signals") or []:
            if isinstance(item, dict) and item.get("signal_type") in expected_order:
                actual_order.append(item.get("signal_type"))
    order_ok = bool(expected_order) and actual_order == list(expected_order)
    if expected_order and not order_ok:
        weaknesses.append(f"{case['id']}: signal order {actual_order} instead of {list(expected_order)}")
    return {
        "id": case["id"],
        "required": required,
        "included": included,
        "claimed": claimed,
        "correct_claims": correct_claims,
        "labelled": labelled,
        "priority_correct": priority_correct,
        "forbidden": forbidden,
        "overview_required": section["overview_required"],
        "overview_hit": section["overview_hit"],
        "section_claimed": section["section_claimed"],
        "section_correct": section["section_correct"],
        "section_forbidden": section["section_forbidden"],
        "section_checked": section["section_checked"],
        "extra_signals": len(extra),
        "order_required": 1 if expected_order else 0,
        "order_ok": 1 if order_ok else 0,
        "weaknesses": weaknesses,
    }


def _rate(correct: int, total: int) -> dict:
    return {
        "rate": round(correct / total, 3) if total else None,
        "correct": correct,
        "n": total,
    }


def combine(case_scores: list[dict]) -> dict:
    required = sum(item["required"] for item in case_scores)
    included = sum(item["included"] for item in case_scores)
    claimed = sum(item["claimed"] for item in case_scores)
    correct_claims = sum(item["correct_claims"] for item in case_scores)
    labelled = sum(item["labelled"] for item in case_scores)
    priority_correct = sum(item["priority_correct"] for item in case_scores)
    forbidden = sum(item["forbidden"] for item in case_scores)
    overview_required = sum(item["overview_required"] for item in case_scores)
    overview_hit = sum(item["overview_hit"] for item in case_scores)
    section_claimed = sum(item["section_claimed"] for item in case_scores)
    section_correct = sum(item["section_correct"] for item in case_scores)
    section_forbidden = sum(item["section_forbidden"] for item in case_scores)
    section_checked = sum(item["section_checked"] for item in case_scores)
    extra_signals = sum(item["extra_signals"] for item in case_scores)
    order_required = sum(item["order_required"] for item in case_scores)
    order_ok = sum(item["order_ok"] for item in case_scores)
    weaknesses = [line for item in case_scores for line in item["weaknesses"]]
    return {
        "n_cases": len(case_scores),
        "n_signals": labelled,
        "evidence_recall": {"rate": round(included / required, 3) if required else None, "included": included, "n": required},
        "evidence_precision": _rate(correct_claims, claimed),
        "priority_accuracy": _rate(priority_correct, labelled),
        "forbidden_claims": {"count": forbidden, "n": labelled},
        "overview_used": _rate(overview_hit, overview_required),
        "section_precision": _rate(section_correct, section_claimed),
        "section_forbidden": {"count": section_forbidden, "n": section_checked},
        "extra_signals": {"count": extra_signals, "n": len(case_scores)},
        "signal_order": _rate(order_ok, order_required),
        "weaknesses": weaknesses,
    }
