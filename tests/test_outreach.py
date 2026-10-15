from pipeline.jobs.generate_outreach import read_outreach


def _parsed():
    return {
        "company_name": "ICEA LION Group",
        "primary_outreach_angle": {
            "product_category": "Vulnerability Management",
            "reason": "Two known-exploited CVEs are on systems tied to the company.",
            "supporting_signal_types": ["kev_exploited"],
            "suggested_talking_point": "How the team prioritizes those vulnerabilities.",
        },
        "target_personas": [
            {"job_title": "CISO", "reason": "Owns security priorities."},
        ],
        "contact_status": "not_identified",
        "outreach_draft": {
            "channel": "email",
            "subject": "Vulnerability prioritization",
            "body": "Hi there,\n\nWould you be open to a brief conversation?\n\nBest regards,\n[Your name]",
        },
        "secondary_angles": [
            {
                "product_category": "Patch Management",
                "talking_point": "Software upgrades",
                "supporting_signal_types": ["eol_software"],
            }
        ],
        "review_required": False,
    }


def test_outreach_keeps_only_supplied_signals():
    written = read_outreach(_parsed(), {"kev_exploited", "eol_software"}, "ICEA LION Group")
    assert written["contact_status"] == "not_identified"
    assert written["review_required"] is True
    assert written["primary_outreach_angle"]["supporting_signal_types"] == ["kev_exploited"]
    assert written["outreach_draft"]["channel"] == "email"


def test_outreach_rejects_an_unknown_signal():
    parsed = _parsed()
    parsed["primary_outreach_angle"]["supporting_signal_types"] = ["not_a_signal"]
    assert read_outreach(parsed, {"kev_exploited"}, "ICEA LION Group") is None
