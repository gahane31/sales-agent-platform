from eval.signal_insights.score import combine, score_case


def _case():
    return {
        "id": "sample",
        "coverage": "A sample company has one known flaw.",
        "input": {
            "company_name": "Example",
            "company_overview": {},
            "signals": [
                {
                    "signal_type": "kev_exploited",
                    "product_angle": "Vulnerability mgmt / EDR — lead with active exploitation",
                    "need_weight": 100,
                    "evidence": {"top_cves": [{"cve_id": "CVE-2023-44487"}]},
                }
            ],
        },
        "checks": {
            "labels": {
                "kev_exploited": {
                    "priority": "high",
                    "must_include": ["CVE-2023-44487"],
                    "must_not_say": ["breached"],
                }
            }
        },
    }


def test_recall_and_precision_count_only_supplied_facts():
    prepared = [
        {
            "signal_type": "kev_exploited",
            "catalog_product_angle": "Vulnerability mgmt / EDR — lead with active exploitation",
            "evidence": {"top_cves": [{"cve_id": "CVE-2023-44487"}]},
        }
    ]
    parsed = {
        "section_summary": "One known hole.",
        "signals": [
            {
                "signal_type": "kev_exploited",
                "title": "Known hole",
                "summary": "A known hole is on a system tied to the company.",
                "evidence_summary": "CVE-2023-44487 and CVE-2019-0001",
                "evidence_details": [],
                "product_angle": "Vulnerability management",
                "priority": "high",
            }
        ],
    }
    scored = score_case(_case(), prepared, parsed)
    totals = combine([scored])
    assert totals["evidence_recall"] == {"rate": 1.0, "included": 1, "n": 1}
    assert totals["evidence_precision"]["rate"] == 0.5
    assert totals["evidence_precision"]["n"] == 2
    assert totals["priority_accuracy"] == {"rate": 1.0, "correct": 1, "n": 1}
    assert totals["forbidden_claims"] == {"count": 0, "n": 1}
    assert totals["overview_used"] == {"rate": None, "correct": 0, "n": 0}
    assert totals["section_precision"] == {"rate": None, "correct": 0, "n": 0}
    assert totals["section_forbidden"] == {"count": 0, "n": 1}
    assert totals["n_signals"] == 1
    assert any("invented CVE-2019-0001" in line for line in scored["weaknesses"])


def test_section_summary_must_use_the_overview_and_not_invent_identifiers():
    case = _case()
    case["input"]["company_overview"] = {"executive_summary": "Example is a life insurance company."}
    case["checks"]["section_must_mention"] = ["life insurance"]
    prepared = [
        {
            "signal_type": "kev_exploited",
            "catalog_product_angle": "catalog",
            "evidence": {"top_cves": [{"cve_id": "CVE-2023-44487"}]},
        }
    ]
    parsed = {
        "section_summary": "A generic hosting firm has CVE-2019-0001.",
        "signals": [
            {
                "signal_type": "kev_exploited",
                "title": "Known hole",
                "summary": "A known hole is on a system tied to the company.",
                "evidence_summary": "CVE-2023-44487",
                "evidence_details": [],
                "product_angle": "Vulnerability management",
                "priority": "high",
            }
        ],
    }
    scored = score_case(case, prepared, parsed)
    totals = combine([scored])
    assert totals["overview_used"] == {"rate": 0.0, "correct": 0, "n": 1}
    assert totals["section_precision"] == {"rate": 0.0, "correct": 0, "n": 1}
    assert totals["section_forbidden"] == {"count": 0, "n": 1}
    assert totals["evidence_precision"] == {"rate": 1.0, "correct": 1, "n": 1}
    assert any("did not use the company overview" in line for line in scored["weaknesses"])
    assert any("section summary invented CVE-2019-0001" in line for line in scored["weaknesses"])


def test_a_forbidden_claim_in_the_section_is_not_charged_to_the_card():
    case = _case()
    case["input"]["company_overview"] = {"executive_summary": "Example is a life insurance company."}
    case["checks"]["section_must_mention"] = ["life insurance"]
    prepared = [{"signal_type": "kev_exploited", "catalog_product_angle": "catalog", "evidence": {"top_cves": [{"cve_id": "CVE-2023-44487"}]}}]
    parsed = {
        "section_summary": "Example is a life insurance company. The host lists CVE-2023-44487. You should call them, because they were breached.",
        "signals": [
            {
                "signal_type": "kev_exploited",
                "title": "Known hole",
                "summary": "A known hole is on a system tied to the company.",
                "evidence_summary": "CVE-2023-44487",
                "evidence_details": [],
                "product_angle": "Vulnerability management",
                "priority": "high",
            }
        ],
    }
    scored = score_case(case, prepared, parsed)
    totals = combine([scored])
    assert scored["forbidden"] == 0
    assert totals["overview_used"]["correct"] == 1
    assert totals["section_precision"] == {"rate": 1.0, "correct": 1, "n": 1}
    assert totals["section_forbidden"] == {"count": 1, "n": 1}


def test_forbidden_wording_is_counted():
    prepared = [{"signal_type": "kev_exploited", "catalog_product_angle": "catalog", "evidence": {}}]
    parsed = {
        "section_summary": "Fine.",
        "signals": [
            {
                "signal_type": "kev_exploited",
                "title": "Known hole",
                "summary": "The company was breached.",
                "evidence_summary": "",
                "evidence_details": [],
                "product_angle": "Vulnerability management",
                "priority": "low",
            }
        ],
    }
    scored = score_case(_case(), prepared, parsed)
    assert scored["forbidden"] == 1
    assert scored["priority_correct"] == 0


def test_a_page_title_must_not_become_the_business_in_the_summary():
    case = _case()
    case["checks"]["section_must_not_say"] = ["insurance company"]
    prepared = [{"signal_type": "kev_exploited", "catalog_product_angle": "catalog", "evidence": {}}]
    parsed = {
        "section_summary": "Example is an insurance company.",
        "signals": [
            {
                "signal_type": "kev_exploited",
                "title": "Known hole",
                "summary": "The page title is Cancer Insurance.",
                "evidence_summary": "Cancer Insurance",
                "evidence_details": [],
                "product_angle": "Vulnerability management",
                "priority": "high",
            }
        ],
    }
    scored = score_case(case, prepared, parsed)
    totals = combine([scored])
    assert scored["forbidden"] == 0
    assert totals["section_forbidden"] == {"count": 1, "n": 1}


def _card(signal_type, **extra):
    item = {
        "signal_type": signal_type,
        "title": "Finding",
        "summary": "Observed.",
        "evidence_summary": "",
        "evidence_details": [],
        "product_angle": "Vulnerability management",
        "priority": "high",
    }
    item.update(extra)
    return item


def test_an_added_signal_is_counted():
    parsed = {
        "section_summary": "Fine.",
        "signals": [_card("kev_exploited"), _card("cloud_workload", priority="informational")],
    }
    scored = score_case(_case(), [{"signal_type": "kev_exploited", "evidence": {}}], parsed)
    assert scored["extra_signals"] == 1
    assert any("added cloud_workload" in line for line in scored["weaknesses"])


def test_signal_order_must_follow_need():
    case = _case()
    case["checks"]["labels"]["no_waf_site"] = {"priority": "low", "must_include": [], "must_not_say": []}
    case["checks"]["signal_order"] = ["kev_exploited", "no_waf_site"]
    parsed = {"section_summary": "Fine.", "signals": [_card("no_waf_site", priority="low"), _card("kev_exploited")]}
    scored = score_case(case, [], parsed)
    assert scored["order_ok"] == 0
    parsed["signals"] = [_card("kev_exploited"), _card("no_waf_site", priority="low")]
    assert score_case(case, [], parsed)["order_ok"] == 1


def test_a_long_host_list_names_at_most_two():
    case = _case()
    case["checks"]["labels"]["kev_exploited"]["max_hosts"] = 2
    hosts = ["alpha.fleet.example", "bravo.fleet.example", "charlie.fleet.example"]
    prepared = [{"signal_type": "kev_exploited", "evidence": {"hosts": hosts}, "catalog_product_angle": "catalog"}]
    parsed = {
        "section_summary": "Fine.",
        "signals": [_card("kev_exploited", evidence_summary=" ".join(hosts))],
    }
    scored = score_case(case, prepared, parsed)
    assert any("name at most 2" in line for line in scored["weaknesses"])


def test_cvss_wording_is_a_weakness():
    parsed = {
        "section_summary": "The CVSS is 9.8.",
        "signals": [_card("kev_exploited", evidence_summary="CVE-2023-44487")],
    }
    scored = score_case(_case(), [{"signal_type": "kev_exploited", "evidence": {}, "catalog_product_angle": "catalog"}], parsed)
    assert any("CVSS or EPSS" in line for line in scored["weaknesses"])
