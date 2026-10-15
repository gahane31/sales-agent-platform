"""Open the Supabase Postgres connection. The only place that reads the database login."""

from __future__ import annotations

import os

import psycopg
from dotenv import load_dotenv

from pipeline.logger import get_logger
from pipeline.db.sql_statements import set_statement_timeout

logger = get_logger("postgres_client")


def load_env() -> None:
    """Read .env from the folder the command was started in."""
    load_dotenv()


class PostgresClient:
    def connection(self, *, user: str | None = None, password: str | None = None) -> psycopg.Connection:
        load_env()
        database_password = password if password is not None else os.environ.get("password", "")
        if not database_password:
            logger.error("database password is missing")
            raise RuntimeError("password is missing from .env")
        database_host = os.environ.get("host", "")
        database_user = user if user is not None else os.environ.get("user", "postgres")
        database_port = int(os.environ.get("port", "5432"))
        database_name = os.environ.get("database", "postgres")
        connection_attempts = [(database_host, database_port, database_user)]
        if database_host.startswith("db.") and database_host.endswith(".supabase.co"):
            project_ref = database_host.removeprefix("db.").removesuffix(".supabase.co")
            role_name = database_user.split(".", 1)[0]
            connection_attempts.append(
                ("aws-0-ap-northeast-1.pooler.supabase.com", 5432, f"{role_name}.{project_ref}")
            )
        last_connection_error: Exception | None = None
        for host, port, user in connection_attempts:
            if not host:
                continue
            try:
                database_connection = psycopg.connect(
                    host=host,
                    port=port,
                    dbname=database_name,
                    user=user,
                    password=database_password,
                    sslmode="require",
                    connect_timeout=20,
                    autocommit=True,
                )
                set_statement_timeout(database_connection)
                logger.info(
                    "database connected",
                    user=user,
                    host=host,
                    port=port,
                    database=database_name,
                )
                return database_connection
            except psycopg.Error as connection_error:
                last_connection_error = connection_error
                logger.warning(
                    "database connection failed",
                    user=user,
                    host=host,
                    port=port,
                    error=str(connection_error),
                )
        logger.error("database connection failed", reason="no remaining hosts")
        raise RuntimeError("could not connect to Postgres") from last_connection_error


def open_database_connection() -> psycopg.Connection:
    return PostgresClient().connection()


def open_app_connection() -> psycopg.Connection:
    """The queue uses the restricted role when APP_DB_USER and APP_DB_PASSWORD are set."""
    load_env()
    app_user = os.environ.get("APP_DB_USER", "").strip()
    app_password = os.environ.get("APP_DB_PASSWORD", "").strip()
    if app_user and app_password:
        return PostgresClient().connection(user=app_user, password=app_password)
    return PostgresClient().connection()
