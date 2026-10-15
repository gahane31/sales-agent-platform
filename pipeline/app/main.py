"""Sales workspace. The rep pages stay free of scoring jargon."""

from __future__ import annotations

import os
import sys

project_directory = os.getcwd()
if project_directory not in sys.path:
    sys.path.insert(0, project_directory)

import streamlit as st

from pipeline.app.screens import account_detail, accounts, system
from pipeline.app.state import require_password

WORKSPACE = ["Accounts", "System"]


def main() -> None:
    st.set_page_config(page_title="Sales workspace", layout="wide")
    st.markdown(
        """
        <style>
        div[data-testid="stMetric"] {
            background: #171A20;
            padding: 0.75rem 0.9rem;
            border-radius: 10px;
            border: 1px solid #2C313A;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    require_password()
    if st.session_state.get("workspace") not in WORKSPACE:
        st.session_state["workspace"] = "Accounts"
    if st.session_state.get("open_account") and st.sidebar.button("Back to accounts"):
        st.session_state["open_account"] = False
        st.session_state["workspace"] = "Accounts"
        st.rerun()
    chosen = st.sidebar.radio("Workspace", WORKSPACE, key="workspace")
    if chosen != "Accounts" and st.session_state.get("open_account"):
        st.session_state["open_account"] = False
    if st.session_state.get("open_account"):
        account_detail.render()
        return
    if chosen == "System":
        system.render()
    else:
        accounts.render()


if __name__ == "__main__":
    main()
