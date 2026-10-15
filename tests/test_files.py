from pipeline.ai.prompt_registry import PIPELINE, PROMPTS
from pipeline.files import read_json, read_sql
from eval.signal_insights.run import load_cases


def test_prompt_file_is_inside_the_pipeline_folder():
    path = next(PROMPTS.glob("*/*.md"))
    stored = path.relative_to(PIPELINE)
    assert stored.parts[:2] == ("ai", "prompts")
    assert (PIPELINE / stored).is_file()


def test_sql_is_read_from_beside_the_runner():
    text = read_sql("pipeline/runner.py", "etl/12_enrich_account_domains.sql")
    assert "country_code" in text


def test_eval_cases_load_as_json():
    cases = load_cases()
    assert len(cases) == 17
    assert {case["id"] for case in cases} == {
        "mixed_findings",
        "cloud_only",
        "no_evidence",
        "many_cves",
        "catalog_copy",
        "priority_bands",
        "no_overview",
        "wrong_title",
        "open_database",
        "admin_guest",
        "kev_no_cve",
        "low_only",
        "two_certs",
        "one_signal",
        "no_cvss",
        "host_cap",
        "signal_order",
    }
    assert all(case.get("coverage") and "signals" in case["input"] and "labels" in case["checks"] for case in cases)
    bank = read_json("eval/signal_insights/run.py", "cases/no_overview.json")
    assert bank["input"]["company_overview"] == {}
    assert "section_must_mention" not in bank["checks"]
