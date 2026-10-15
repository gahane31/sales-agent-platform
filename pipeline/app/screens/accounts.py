"""Accounts table. Row click opens the account."""

from __future__ import annotations

import csv
import io

import streamlit as st

from pipeline.app.state import (
    PRIORITY_MARK,
    account_type,
    cached_read_only_connection,
)
from pipeline.db.sql_statements import (
    select_domain_countries,
    select_priority_queue,
)
from pipeline.db.workflow import WORKFLOW_STATUSES


def render() -> None:
    st.title("Accounts")
    database_connection = cached_read_only_connection()
    countries = select_domain_countries(database_connection)
    filters = st.columns(4)
    search = filters[0].text_input("Search")
    owner = filters[1].selectbox("Owner", ["Any", "Unassigned"])
    priority = filters[2].multiselect("Priority", ["A", "B", "C"], default=["A", "B"], format_func=lambda item: PRIORITY_MARK[item])
    country = filters[3].selectbox("Country", ["Any"] + countries)
    rows = select_priority_queue(
        database_connection,
        tiers=priority or ["A", "B", "C"],
        minimum_score=0,
        workflow_statuses=list(WORKFLOW_STATUSES),
        search=search,
        industry="Any",
        country=country,
        business_type="Any",
        kev_only=False,
        signal="",
        owner=owner,
    )
    match_count = int(rows[0]["match_count"]) if rows else 0
    st.caption(f"Showing top {len(rows)} of {match_count} — download CSV for all.")
    display = [
        {
            "Logo": f"https://www.google.com/s2/favicons?domain={row['account_domain']}&sz=64",
            "Name": row["display_name"],
            "Type": account_type(row.get("workflow_status")),
            "Owner": row.get("owner") or "Unassigned",
            "Priority": PRIORITY_MARK.get(row["tier"], "⚪ P3"),
            "Score": int(row["composite_score"] or 0),
            "Date added": str(row.get("first_seen") or "")[:10],
            "Country": row.get("country") or "Unknown",
        }
        for row in rows
    ]
    event = st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Logo": st.column_config.ImageColumn("Logo"),
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100),
        },
        key=f"accounts_table_{st.session_state.get('accounts_table_key', 0)}",
    )
    selected = list(getattr(getattr(event, "selection", None), "rows", []) or [])
    if selected:
        chosen = rows[selected[0]]
        st.session_state["account"] = chosen["account_domain"]
        st.session_state["open_account"] = True
        st.session_state["accounts_table_key"] = st.session_state.get("accounts_table_key", 0) + 1
        st.rerun()
    export_rows = select_priority_queue(
        database_connection,
        tiers=priority or ["A", "B", "C"],
        minimum_score=0,
        workflow_statuses=list(WORKFLOW_STATUSES),
        search=search,
        industry="Any",
        country=country,
        business_type="Any",
        kev_only=False,
        signal="",
        owner=owner,
        row_limit=None,
    )
    buffer = io.StringIO()
    if export_rows:
        fields = [name for name in export_rows[0] if name != "match_count"]
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for row in export_rows:
            writer.writerow({name: row[name] for name in fields})
    st.download_button("Download CSV", buffer.getvalue(), file_name="accounts.csv", mime="text/csv")
