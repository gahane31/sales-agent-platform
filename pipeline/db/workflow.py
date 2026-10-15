"""Database access for the queue. Writes are limited to the workflow tables."""

from __future__ import annotations

from pipeline.db.postgres_client import open_app_connection
from pipeline.db.sql_statements import (
    insert_account_feedback,
    insert_account_note,
    insert_account_workflow,
    set_default_transaction_read_only,
)

WORKFLOW_STATUSES = (
    "new",
    "contacted",
    "replied",
    "meeting",
    "closed_won",
    "closed_lost",
    "disqualified",
)


def open_read_only_connection():
    database_connection = open_app_connection()
    set_default_transaction_read_only(database_connection)
    return database_connection


def open_write_connection():
    return open_app_connection()


def set_status(domain: str, status: str, owner: str | None = None) -> None:
    if status not in WORKFLOW_STATUSES:
        raise ValueError(status)
    with open_write_connection() as database_connection:
        insert_account_workflow(database_connection, domain, status, owner)


def add_note(domain: str, note: str) -> None:
    text = note.strip()
    if not text:
        return
    with open_write_connection() as database_connection:
        insert_account_note(database_connection, domain, text)


def add_feedback(domain: str, feedback_type: str, note: str | None = None) -> None:
    with open_write_connection() as database_connection:
        insert_account_feedback(database_connection, domain, feedback_type, note)


def set_owner(domain: str, owner: str | None) -> None:
    text = (owner or "").strip() or None
    with open_write_connection() as database_connection:
        database_connection.execute(
            """
            insert into public.account_workflow (account_domain, status, owner)
            values (%s, 'new', %s)
            on conflict (account_domain) do update
              set owner = excluded.owner,
                  updated_at = now()
            """,
            (domain, text),
        ).close()
