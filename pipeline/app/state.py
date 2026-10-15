"""Password gate and the words on the rep screen."""

from __future__ import annotations

import os
import sys

project_directory = os.getcwd()
if project_directory not in sys.path:
    sys.path.insert(0, project_directory)

from dotenv import load_dotenv

load_dotenv()

import streamlit as st

from pipeline.db.workflow import open_read_only_connection

ACTION_TIER = {"A": "P1", "B": "P2", "C": "P3", "D": "P3"}
PRIORITY_MARK = {"A": "🔴 P1", "B": "🟠 P2", "C": "⚪ P3", "D": "⚪ P3"}


def app_password() -> str:
    configured = os.environ.get("APP_PASSWORD", "").strip()
    if configured:
        return configured
    try:
        return str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        return ""


def require_password() -> None:
    configured = app_password()
    if not configured:
        st.error("Set APP_PASSWORD in the environment or in Streamlit secrets before opening the queue.")
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.title("Sales workspace")
    entered = st.text_input("Password", type="password")
    if st.button("Continue"):
        if entered == configured:
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("That password is wrong.")
    st.stop()


@st.cache_resource
def cached_read_only_connection():
    return open_read_only_connection()


def industry_label(value: str | None) -> str:
    if not value or str(value).strip().lower() == "none":
        return "Unclassified"
    return value


def account_type(status: str | None) -> str:
    status = status or "new"
    if status in {"replied", "meeting"}:
        return "Engaged"
    if status == "closed_won":
        return "Customer"
    return "Prospect"


def severity_class(need_weight: int | None, timing_weight: int | None = None) -> str:
    """One grouping rule. need >= 70 Urgent; else timing >= 50 Good timing; else need 0 Buying signal; else Hygiene."""
    need = int(need_weight or 0)
    timing = int(timing_weight or 0)
    if need >= 70:
        return "Urgent"
    if timing >= 50:
        return "Good timing"
    if need == 0:
        return "Buying signal"
    return "Hygiene"
