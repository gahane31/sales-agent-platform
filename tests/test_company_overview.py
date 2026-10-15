from pipeline.jobs.generate_company_overview import read_company_overview, search_query, snippets_from_response


def test_search_uses_the_stored_name():
    assert search_query("Liverpool Heart and Chest Hospital", "lhch.nhs.uk") == (
        "Give me information about Liverpool Heart and Chest Hospital"
    )


def test_search_uses_the_domain_when_the_name_is_blank():
    assert search_query(None, "lhch.nhs.uk") == "Give me information about lhch.nhs.uk"
    assert search_query("  ", "lhch.nhs.uk") == "Give me information about lhch.nhs.uk"


def test_snippets_come_from_the_stored_search():
    snippets = snippets_from_response(
        {"results": [{"title": "About", "url": "https://example.com", "content": "They run hospitals."}]}
    )
    assert snippets == [{"title": "About", "url": "https://example.com", "content": "They run hospitals."}]
    assert snippets_from_response({"results": []}) == []
    assert snippets_from_response("not json") == []


def test_overview_requires_a_name_or_summary():
    import json

    profile = {
        "company_name": "Example",
        "executive_summary": "They run hospitals.",
        "industry": "Healthcare",
        "scale_metrics": [],
    }
    name, stored = read_company_overview(profile)
    assert name == "Example"
    assert json.loads(stored)["industry"] == "Healthcare"
    assert read_company_overview({"company_name": "", "executive_summary": "  "}) is None
    assert read_company_overview(None) is None
