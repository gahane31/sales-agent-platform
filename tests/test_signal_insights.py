from pipeline.jobs.generate_signal_insights import (
    build_signal_input,
    insight_request,
    overview_for_insights,
    priority_label,
    read_signal_insights,
)


def test_priority_follows_the_stored_weights():
    assert priority_label(100, 100) == "high"
    assert priority_label(70, 30) == "high"
    assert priority_label(55, 25) == "medium"
    assert priority_label(15, 5) == "low"
    assert priority_label(0, 0) == "informational"


def test_signals_stay_in_priority_order():
    prepared = build_signal_input(
        [
            {"signal_type": "cloud_workload", "need_weight": 0, "timing_weight": 0, "evidence": {}},
            {"signal_type": "eol_software", "need_weight": 70, "timing_weight": 30, "evidence": {"products": ["nginx 1.24.0"]}},
            {"signal_type": "kev_exploited", "need_weight": 100, "timing_weight": 100, "evidence": {}},
        ]
    )
    assert [item["signal_type"] for item in prepared] == ["kev_exploited", "eol_software", "cloud_workload"]
    assert [item["priority"] for item in prepared] == ["high", "high", "informational"]


def test_output_keeps_our_priority_and_order():
    prepared = [
        {"signal_type": "kev_exploited", "priority": "high"},
        {"signal_type": "eol_software", "priority": "high"},
    ]
    written = read_signal_insights(
        {
            "company_name": "ICEA LION Group",
            "section_summary": "Two findings are worth a call.",
            "signals": [
                {
                    "signal_type": "eol_software",
                    "title": "End-of-life software",
                    "summary": "nginx 1.24.0 is past support.",
                    "evidence_summary": "nginx 1.24.0",
                    "evidence_details": [],
                    "product_angle": "An upgrade path",
                    "priority": "low",
                },
                {
                    "signal_type": "kev_exploited",
                    "title": "Known-exploited vulnerabilities",
                    "summary": "Two known holes are on their systems.",
                    "evidence_summary": "CVE-2023-44487 and CVE-2020-11023",
                    "evidence_details": ["CVE-2023-44487"],
                    "product_angle": "Vulnerability management",
                    "priority": "medium",
                },
            ],
        },
        prepared,
    )
    assert written["section_summary"] == "Two findings are worth a call."
    assert [item["signal_type"] for item in written["signals"]] == ["kev_exploited", "eol_software"]
    assert [item["priority"] for item in written["signals"]] == ["high", "high"]
    assert read_signal_insights({"company_name": "Example", "signals": []}, prepared) is None


def test_overview_sent_to_the_model_drops_source_urls():
    overview = overview_for_insights(
        {
            "executive_summary": "A life insurance company.",
            "industry": "Insurance",
            "sources": [{"title": "Site", "url": "https://example.com", "published_date": None}],
            "scale_metrics": [{"metric": "Customers", "value": "1 million", "period": "2024", "source_url": "https://example.com"}],
            "key_facts": [{"fact": "Sells pensions.", "source_url": "https://example.com"}],
        }
    )
    assert overview["executive_summary"] == "A life insurance company."
    assert "sources" not in overview
    assert overview["scale_metrics"] == [{"metric": "Customers", "value": "1 million", "period": "2024"}]
    assert overview["key_facts"] == ["Sells pensions."]
    request = insight_request("ICEA LION Group", overview, [])
    assert request["company_overview"]["industry"] == "Insurance"
    assert insight_request("Example", None, [])["company_overview"] == {}
