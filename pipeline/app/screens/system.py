"""What the model work cost."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import altair as alt
import streamlit as st

from pipeline.app.eval_summary import summarize
from pipeline.app.state import cached_read_only_connection
from pipeline.db.sql_statements import (
    select_daily_model_spend,
    select_eval_skills,
    select_full_account_cost,
    select_last_pipeline_run,
    select_latest_eval_run,
    select_previous_eval_run,
    select_recent_model_runs,
    select_system_cost,
)

AGENT_NAMES = {
    "company-overview": "Company overview",
    "signal-insights": "Signal insights",
    "outreach": "Outreach",
    "eval": "Eval",
}
WINDOWS = ("Today", "Last 7 days", "This month")


def _usd(amount, places: int = 2) -> str:
    return f"{float(amount):.{places}f} USD"


def _of(spent, ceiling) -> str:
    return f"{_usd(spent)} of {_usd(ceiling)}"


def _chart_start(choice: str):
    today = datetime.now(timezone.utc).date()
    if choice == "Today":
        return today
    if choice == "This month":
        return today.replace(day=1)
    return today - timedelta(days=6)


def _budget(cost: dict, last_run: dict | None, full_accounts: int, full_cost: float) -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stMetricValue"] {
            font-size: 1rem;
            line-height: 1.3;
        }
        div[data-testid="stMetricValue"] > div {
            white-space: normal;
            overflow-wrap: anywhere;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("**Model spend**")
    today, month, last, each = st.columns(4)
    today.metric("Today", _of(cost["day_spent"], cost["day_ceiling"]))
    month.metric("This month", _of(cost["month_spent"], cost["month_ceiling"]))
    if last_run:
        last.metric("Last pipeline run", _of(last_run["cost_usd"], cost["run_ceiling"]))
    else:
        last.metric("Last pipeline run", _of(0, cost["run_ceiling"]))
    if full_accounts:
        each.metric("Average per account", _usd(full_cost / full_accounts, 4))
    else:
        each.metric("Average per account", "—")
    note = (
        "A full account has an accepted company overview, signal insights, and outreach. "
        f"{full_accounts} accounts have all three. One run stops at {_usd(cost['run_ceiling'])}."
    )
    if last_run is None:
        note += " No shared pipeline run yet. A solo agent run still counts toward the day and the month."
    st.caption(note)


def _chart(database_connection, day_ceiling: float) -> None:
    st.markdown("**Spend per day**")
    choice = st.radio("Period", WINDOWS, index=1, horizontal=True, label_visibility="collapsed")
    rows = select_daily_model_spend(database_connection, _chart_start(choice))
    st.caption(f"Account work by agent. Eval is its own color. A day stops at {_usd(day_ceiling)}.")
    plotted = [
        {"day": row["day"], "agent": AGENT_NAMES.get(row["skill"], row["skill"]), "cost_usd": float(row["cost_usd"] or 0)}
        for row in rows
    ]
    peak = 0.0
    by_day: dict[str, float] = {}
    for row in plotted:
        by_day[row["day"]] = by_day.get(row["day"], 0.0) + row["cost_usd"]
    if by_day:
        peak = max(by_day.values())
    top = peak * 1.25 if peak else 0.05
    chart = (
        alt.Chart(alt.Data(values=plotted))
        .mark_bar()
        .encode(
            x=alt.X("day:N", title=None, sort=None),
            y=alt.Y(
                "cost_usd:Q",
                title="USD",
                stack="zero",
                scale=alt.Scale(domain=[0, top], nice=False, clamp=True),
            ),
            color=alt.Color("agent:N", title=None),
        )
        .properties(height=280)
    )
    st.altair_chart(chart, width="stretch", theme="streamlit")


def _runs(rows: list[dict]) -> None:
    st.markdown("**Recent runs**")
    if not rows:
        st.caption("No model runs yet.")
        return
    shown = []
    for row in rows:
        started = row["started"]
        if isinstance(started, datetime):
            started = started.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        agents = ", ".join(AGENT_NAMES.get(name.strip(), name.strip()) for name in str(row["agents"] or "").split(","))
        shown.append(
            {
                "Started": started,
                "Run": row["run_id"],
                "Agents": agents,
                "Calls": int(row["calls"] or 0),
                "Cost": _usd(row["cost_usd"]),
                "Kept": int(row["kept"] or 0),
                "Rejected": int(row["rejected"] or 0),
                "Stopped": row["stopped"] or "",
            }
        )
    st.dataframe(shown, hide_index=True, width="stretch")


def _when(value) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return ""


def _agent_name(skill: str) -> str:
    known = AGENT_NAMES.get(skill)
    if known:
        return known
    return skill.replace("-", " ").replace("_", " ").capitalize()


def _percent_html(percent: int) -> str:
    return f"<div style='font-size:1.8rem;font-weight:600;line-height:1.1'>{percent}%</div>"


def _score_column(column, title: str, score: dict) -> None:
    column.markdown(f"**{title}**")
    column.markdown(_percent_html(score["percent"]), unsafe_allow_html=True)
    column.caption(f"Model — {score['model']}")
    column.caption(f"{score['passed']} of {score['total']} checks passed")


def _eval_skill(skill: str, latest: dict, summary: dict) -> None:
    st.markdown(f"**{_agent_name(skill)}**")
    when = _when(latest.get("run_at"))
    if when:
        st.caption(when)
    this, previous = st.columns(2)
    _score_column(this, "This run", summary["current"])
    if summary["previous"]:
        _score_column(previous, "Previous run", summary["previous"])
    else:
        previous.markdown("**Previous run**")
        previous.caption("No previous run")


def _eval(database_connection) -> None:
    st.markdown("**Eval**")
    skills = select_eval_skills(database_connection)
    shown = 0
    for skill in skills:
        latest = select_latest_eval_run(database_connection, skill)
        if latest is None:
            continue
        earlier = None
        if latest.get("prompt_checksum"):
            earlier = select_previous_eval_run(
                database_connection,
                skill,
                latest["prompt_checksum"],
                latest.get("model") or "",
            )
        summary = summarize(latest, earlier)
        if summary is None:
            continue
        if shown:
            st.divider()
        shown += 1
        _eval_skill(skill, latest, summary)
    if not shown:
        st.caption("No eval yet.")


def render() -> None:
    st.title("System")
    database_connection = cached_read_only_connection()
    cost = select_system_cost(database_connection)
    full_accounts, full_cost = select_full_account_cost(database_connection)
    with st.container(border=True):
        _budget(cost, select_last_pipeline_run(database_connection), full_accounts, full_cost)
        _chart(database_connection, cost["day_ceiling"])
        _runs(select_recent_model_runs(database_connection))
    with st.container(border=True):
        _eval(database_connection)
