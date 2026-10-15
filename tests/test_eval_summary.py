from pipeline.app.eval_summary import summarize


def test_current_and_previous_are_separate_pass_rates():
    latest = {
        "model": "gpt-6-luna",
        "metrics": {"n_cases": 17},
        "weaknesses": ["many_cves kev_exploited: missing 203.0.113.10"],
    }
    earlier = {
        "model": "gpt-6-luna",
        "metrics": {"n_cases": 17},
        "weaknesses": ["a: said compromised", "b: said compromised"],
    }
    summary = summarize(latest, earlier)
    assert summary["current"] == {"model": "gpt-6-luna", "percent": 94, "passed": 16, "total": 17}
    assert summary["previous"] == {"model": "gpt-6-luna", "percent": 88, "passed": 15, "total": 17}


def test_a_run_without_cases_is_skipped():
    assert summarize({"model": "gpt-6-luna", "metrics": {}, "weaknesses": []}, None) is None


def test_no_previous_run_leaves_that_side_empty():
    summary = summarize(
        {"model": "gpt-4.1", "metrics": {"n_cases": 4}, "weaknesses": ["one: said compromised"]},
        None,
    )
    assert summary["previous"] is None
    assert summary["current"]["percent"] == 75
