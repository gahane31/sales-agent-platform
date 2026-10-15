"""Read llm_settings. Values already decoded by the driver are returned as-is."""

from __future__ import annotations

import json

import psycopg

from pipeline.db.sql_statements import select_llm_setting


def get_setting(conn: psycopg.Connection, key: str, default):
    row = select_llm_setting(conn, key)
    if row is None:
        return default
    value = row[0]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
