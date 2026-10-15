"""Tabbed account page. Payload is the source of truth."""

from __future__ import annotations

import html
import json

import streamlit as st

from pipeline.app.state import PRIORITY_MARK, account_type, cached_read_only_connection
from pipeline.db.postgres_client import open_database_connection
from pipeline.db.sql_statements import (
    select_account_detail,
    select_account_evidence_context,
    select_account_page,
    select_account_technology,
    select_priority_queue,
)
from pipeline.jobs.generate_company_overview import main as generate_company_overview
from pipeline.jobs.generate_outreach import main as generate_outreach
from pipeline.jobs.generate_signal_insights import main as generate_signal_insights
from pipeline.db.workflow import WORKFLOW_STATUSES, add_feedback, add_note, set_owner, set_status

FEEDBACK = ("wrong_company", "not_our_icp", "already_customer", "bad_evidence")


def _text(value) -> str:
    return str(value or "").strip()


def _company_profile(raw: str) -> dict | None:
    text = _text(raw)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"_prose": text}
    if isinstance(parsed, dict) and ("executive_summary" in parsed or "products_and_services" in parsed):
        return parsed
    if isinstance(parsed, dict) and parsed.get("overview"):
        return {"_prose": parsed.get("overview"), "company_name": parsed.get("company_name")}
    return {"_prose": text}


def _filled(values) -> list[str]:
    return [_text(item) for item in values or [] if _text(item)]


def _chips(items: list[str]) -> None:
    pills = "".join(
        "<span style=\"display:inline-block;margin:0 0.45rem 0.45rem 0;padding:0.35rem 0.75rem;"
        "border:1px solid #2C313A;border-radius:999px;background:#12151A;color:#F3F4F6;"
        f"font-size:0.92rem;\">{html.escape(item)}</span>"
        for item in items
    )
    st.markdown(pills, unsafe_allow_html=True)


def _show_sources(sources: list[dict]) -> None:
    for source in sources:
        url = _text(source.get("url"))
        if not url:
            continue
        title = _text(source.get("title")) or url
        published = _text(source.get("published_date"))
        label = f"{title} ({published})" if published else title
        st.markdown(f"[{label}]({url})")


def _show_company_profile(profile: dict) -> None:
    if profile.get("_prose"):
        name = _text(profile.get("company_name"))
        if name:
            st.markdown(f"**{name}**")
        st.write(profile["_prose"])
        return

    name = _text(profile.get("company_name"))
    if name:
        st.subheader(name)
    website = _text(profile.get("website"))
    if website:
        href = website if website.startswith("http") else f"https://{website}"
        st.markdown(f"[{website}]({href})")
    place = [item for item in (_text(profile.get("industry")), _text(profile.get("headquarters"))) if item]
    if place:
        st.caption(" · ".join(place))
    company_type = _text(profile.get("company_type"))
    if company_type:
        st.caption(company_type)

    sources = [item for item in profile.get("sources") or [] if isinstance(item, dict) and _text(item.get("url"))]
    summary = _text(profile.get("executive_summary"))
    if summary:
        with st.container(border=True):
            st.markdown("**Company summary**")
            st.write(summary)
            if sources:
                with st.expander("View sources"):
                    _show_sources(sources)

    glance = []
    if _text(profile.get("industry")):
        glance.append(("Industry", _text(profile.get("industry"))))
    regions = _filled(profile.get("operating_regions"))
    if regions:
        glance.append(("Operating markets", ", ".join(regions)))
    if company_type:
        glance.append(("Company type", company_type))
    for metric in profile.get("scale_metrics") or []:
        if not isinstance(metric, dict) or not _text(metric.get("value")):
            continue
        label = _text(metric.get("metric")) or "Scale"
        value = _text(metric.get("value"))
        period = _text(metric.get("period"))
        if period:
            value = f"{value} ({period})"
        source = _text(metric.get("source_url"))
        if source:
            value = f"{value}  \n[Source]({source})"
        glance.append((label, value))
    if glance:
        with st.container(border=True):
            st.markdown("**Company at a glance**")
            left, right = st.columns(2)
            for index, (label, value) in enumerate(glance):
                with left if index % 2 == 0 else right:
                    st.caption(label.upper())
                    st.markdown(value)

    products = _filled(profile.get("products_and_services"))
    if products:
        with st.container(border=True):
            st.markdown("**Products and services**")
            _chips(products)
    focus = _filled(profile.get("business_focus"))
    if focus:
        with st.container(border=True):
            st.markdown("**Business focus**")
            _chips(focus)

    sections = (
        ("Mission and vision", profile.get("mission_and_vision")),
        ("Company history", profile.get("company_history")),
        ("Ownership", profile.get("ownership_summary")),
    )
    shown = [(title, _text(body)) for title, body in sections if _text(body)]
    if shown:
        with st.container(border=True):
            st.markdown("**Business overview**")
            for title, body in shown:
                with st.expander(title):
                    st.write(body)

    facts = [item for item in profile.get("key_facts") or [] if isinstance(item, dict) and _text(item.get("fact"))]
    if facts:
        with st.container(border=True):
            st.markdown("**Key facts**")
            for fact in facts:
                source = _text(fact.get("source_url"))
                if source:
                    st.markdown(f"{_text(fact.get('fact'))}  \n[Source]({source})")
                else:
                    st.write(_text(fact.get("fact")))

    if sources and not summary:
        with st.expander("View sources"):
            _show_sources(sources)


def _signal_insights(raw: str) -> dict:
    text = _text(raw)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("signals"), list):
        return {}
    parsed["signals"] = [item for item in parsed["signals"] if isinstance(item, dict)]
    return parsed


def _generate(domain: str, job) -> None:
    connection = open_database_connection()
    try:
        job(connection, account=domain)
    finally:
        connection.close()


def _proof_by_type(rows: list[dict]) -> dict:
    proof = {}
    for row in rows:
        evidence = row.get("evidence") or {}
        if isinstance(evidence, str):
            try:
                evidence = json.loads(evidence)
            except json.JSONDecodeError:
                evidence = {}
        proof[row.get("signal_type")] = evidence
    return proof


def render() -> None:
    database_connection = cached_read_only_connection()
    choices = select_priority_queue(
        database_connection,
        tiers=["A", "B", "C"],
        minimum_score=0,
        workflow_statuses=["new", "contacted", "replied", "meeting", "closed_won", "closed_lost", "disqualified"],
        search="",
        industry="Any",
        country="Any",
        business_type="Any",
        kev_only=False,
        signal="",
        owner="Any",
        row_limit=300,
    )
    labels = [f"{row['display_name']} ({row['account_domain']})" for row in choices]
    domains = [row["account_domain"] for row in choices]
    current = st.session_state.get("account")
    if current and current not in domains:
        domains.insert(0, current)
        labels.insert(0, current)
    if not domains:
        st.info("Pick an account from the Accounts table.")
        return
    index = domains.index(current) if current in domains else 0
    picked = st.selectbox("Account", labels, index=index)
    domain = domains[labels.index(picked)]
    st.session_state["account"] = domain
    database_connection = cached_read_only_connection()
    identity = select_account_page(database_connection, domain)
    if not identity:
        st.warning("No account with that domain.")
        return
    row = identity[0]
    live = select_account_evidence_context(database_connection, [domain])
    scan_date = live[0].get("scan_date") if live else None
    scan_date = str(scan_date)[:10] if scan_date else str(row.get("last_seen") or "")[:10]
    st.image(f"https://www.google.com/s2/favicons?domain={domain}&sz=64", width=32)
    st.subheader(row["display_name"])
    st.caption(
        f"{domain} · {account_type(row.get('status'))} · {PRIORITY_MARK.get(row.get('tier'), '⚪ P3')} · "
        f"Added {str(row.get('first_seen') or '')[:10]}"
    )
    if scan_date:
        st.caption(f"Based on public scans of {scan_date}")
    owner = st.text_input("Owner", row.get("owner") or "")
    if st.button("Save owner"):
        set_owner(domain, owner)
        st.rerun()
    overview, signals_tab, tech_tab, outreach = st.tabs(["Overview", "Signals", "Tech", "Outreach"])
    with overview:
        profile = _company_profile(row.get("company_overview"))
        if profile:
            if not _text(profile.get("company_name")):
                profile["company_name"] = row.get("company_overview_name")
            _show_company_profile(profile)
        else:
            st.info("No company overview yet.")
            if st.button("Generate overview"):
                with st.spinner("Writing the company overview…"):
                    _generate(domain, generate_company_overview)
                fresh = select_account_page(database_connection, domain)
                if fresh and _text(fresh[0].get("company_overview")):
                    st.rerun()
                st.error("The overview could not be written.")
        st.markdown("**Notes**")
        note = st.text_input("Add a note")
        if st.button("Save note") and note.strip():
            add_note(domain, note)
            st.rerun()
        status = st.selectbox("Status", WORKFLOW_STATUSES, index=WORKFLOW_STATUSES.index(row["status"]) if row["status"] in WORKFLOW_STATUSES else 0)
        if st.button("Update status"):
            set_status(domain, status)
            st.rerun()
        feedback = st.selectbox("Feedback", FEEDBACK)
        if st.button("Send feedback"):
            add_feedback(domain, feedback)
            st.rerun()
    with signals_tab:
        insights = _signal_insights(row.get("signal_insights"))
        signals = insights.get("signals") or []
        proof = _proof_by_type(select_account_detail(database_connection, domain))
        if not signals:
            st.info("No signal insights yet.")
            if st.button("Generate signal insights"):
                with st.spinner("Writing the signal insights…"):
                    _generate(domain, generate_signal_insights)
                fresh = select_account_page(database_connection, domain)
                if fresh and _signal_insights(fresh[0].get("signal_insights")).get("signals"):
                    st.rerun()
                st.error("The signal insights could not be written.")
        section_summary = _text(insights.get("section_summary"))
        if section_summary:
            st.markdown(f"**Summary:** {section_summary}")
        for insight in signals:
            with st.container(border=True):
                st.markdown(f"**{_text(insight.get('title')) or insight.get('signal_type')}**")
                priority = _text(insight.get("priority"))
                if priority:
                    st.markdown(f"**Priority:** {priority.capitalize()}")
                summary = _text(insight.get("summary"))
                if summary:
                    st.markdown(f"**Summary:** {summary}")
                evidence_summary = _text(insight.get("evidence_summary"))
                if evidence_summary:
                    st.markdown(f"**Evidence:** {evidence_summary}")
                details = [_text(detail) for detail in insight.get("evidence_details") or [] if _text(detail)]
                if details:
                    st.markdown("**Evidence details:** " + "; ".join(details))
                angle = _text(insight.get("product_angle"))
                if angle:
                    st.markdown(f"**Product angle:** {angle}")
                stored = proof.get(insight.get("signal_type"))
                if stored:
                    with st.expander("Show proof"):
                        st.code(json.dumps(stored, indent=2, default=str), language="json")
    with tech_tab:
        st.caption("Software visible on systems exposed to the internet.")
        st.dataframe(select_account_technology(database_connection, domain), hide_index=True, width="stretch")
    with outreach:
        raw_outreach = _text(row.get("outreach"))
        try:
            brief = json.loads(raw_outreach) if raw_outreach else None
        except json.JSONDecodeError:
            brief = None
        if not isinstance(brief, dict) or not brief.get("outreach_draft"):
            st.info("No outreach brief yet.")
            if st.button("Generate outreach"):
                with st.spinner("Writing the outreach brief…"):
                    _generate(domain, generate_outreach)
                fresh = select_account_page(database_connection, domain)
                saved = _text(fresh[0].get("outreach")) if fresh else ""
                if saved:
                    st.rerun()
                st.error("The outreach brief could not be written.")
        else:
            angle = brief.get("primary_outreach_angle") or {}
            draft = brief.get("outreach_draft") or {}
            personas = [item for item in brief.get("target_personas") or [] if isinstance(item, dict)]
            company = _text(brief.get("company_name")) or row.get("display_name")
            st.markdown("**Outreach**")
            st.caption(f"{company} · AI sales intelligence platform")
            st.write("Prepare a relevant first conversation using the company overview and the security findings.")
            if brief.get("review_required"):
                st.caption("Draft")
            with st.container(border=True):
                st.markdown("**Recommended outreach angle**")
                st.markdown(f"**{_text(angle.get('product_category'))}**")
                st.caption("Lead angle")
                if _text(angle.get("reason")):
                    st.write(angle.get("reason"))
                signals = ", ".join(_text(item) for item in angle.get("supporting_signal_types") or [] if _text(item))
                if signals:
                    st.markdown(f"**Supporting signals:** {signals}")
                if _text(angle.get("suggested_talking_point")):
                    st.markdown(f"**Suggested conversation:** {angle.get('suggested_talking_point')}")
                st.caption("This is a suggested discussion topic, not confirmation that the company has an active security incident.")
            with st.container(border=True):
                st.markdown("**Who should you contact?**")
                if brief.get("contact_status") == "not_identified":
                    st.caption("Suggested buyer roles. Individual contacts are not available.")
                for persona in personas:
                    title = _text(persona.get("job_title"))
                    if not title:
                        continue
                    st.markdown(f"**{title}**")
                    if _text(persona.get("reason")):
                        st.caption(persona.get("reason"))
            with st.container(border=True):
                st.markdown("**Draft first-touch email**")
                st.caption("No recipient identified")
                st.markdown(f"**Subject:** {_text(draft.get('subject'))}")
                st.write(_text(draft.get("body")))
                st.caption("Suggested draft. It does not name unverified technical findings and should be reviewed before sending.")
                email_text = f"Subject: {_text(draft.get('subject'))}\n\n{_text(draft.get('body'))}"
                role_text = "\n".join(
                    f"{_text(persona.get('job_title'))}: {_text(persona.get('reason'))}"
                    for persona in personas
                    if _text(persona.get("job_title"))
                )
                if hasattr(st, "copy_button"):
                    st.copy_button("Copy email", email_text)
                    if role_text:
                        st.copy_button("Copy target roles", role_text)
            secondary = [item for item in brief.get("secondary_angles") or [] if isinstance(item, dict)]
            if secondary:
                with st.expander("Other conversation angles"):
                    for item in secondary:
                        st.markdown(f"**{_text(item.get('product_category'))}**")
                        if _text(item.get("talking_point")):
                            st.write(item.get("talking_point"))
                        named = ", ".join(_text(signal) for signal in item.get("supporting_signal_types") or [] if _text(signal))
                        if named:
                            st.caption(f"Supporting signals: {named}")
