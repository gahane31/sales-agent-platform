#!/usr/bin/env python3
"""Run the normalization SQL in order.

The SQL files own their transactions. This script does not rewrite them.
The database connection lives in db/postgres_client.py.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import psycopg
from psycopg.pq import ExecStatus, TransactionStatus

project_directory = str(Path.cwd())
if project_directory not in sys.path:
    sys.path.insert(0, project_directory)

from pipeline.files import beside, read_sql
from pipeline.logger import get_logger
from pipeline.db.postgres_client import open_database_connection
from pipeline.db.sql_statements import (
    insert_etl_run,
    update_etl_run,
)

logger = get_logger("runner")

SHARED_BUDGET_JOBS = {
    "jobs/generate_company_overview.py",
    "jobs/generate_signal_insights.py",
    "jobs/generate_outreach.py",
}


class PipelineStep(NamedTuple):
    number: int
    description: str
    path: str


# Run with --setup. A new database, or a change to a table, column, or seed list.
SETUP_STEPS = [
    PipelineStep(1, "Create the functions that read an IP, a time, and a domain name out of a raw Shodan record", "setup/00_helper_functions.sql"),
    PipelineStep(2, "Create the scan, account, and pipeline-run tables", "setup/01_schema.sql"),
    PipelineStep(3, "Load the public suffix list used to find a registrable domain", "setup/05_public_suffix_list_load.sql"),
    PipelineStep(4, "Load the infrastructure domains that are not customers", "setup/02_infra_seed.sql"),
    PipelineStep(5, "Load the buying signals, the phone-call wording, and the risky ports", "setup/03_signal_config.sql"),
    PipelineStep(6, "Load industry fit scores and the words that detect an industry", "setup/04_icp_config.sql"),
    PipelineStep(7, "Create the tables for workflow status, notes, and feedback", "setup/migrations/01_workflow_tables.sql"),
    PipelineStep(8, "Create the tables for model calls, prompts, evals, and search cache", "setup/migrations/02_ai_tables.sql"),
]

# Run with --all after new rows are in shodan_records. No table or column change.
DATA_STEPS = [
    PipelineStep(1, "Save each Shodan record: one row for an IP and a port", "etl/01_scan_records.sql"),
    PipelineStep(2, "Save the domain names found on each record", "etl/02_scan_domains.sql"),
    PipelineStep(3, "Mark infrastructure domains, and mark records that name a customer", "etl/03_refresh_flags.sql"),
    PipelineStep(4, "Save the certificate on each record that has one", "etl/04_scan_ssl.sql"),
    PipelineStep(5, "Save the web page title and server on each record that has one", "etl/05_scan_http.sql"),
    PipelineStep(6, "Save the software names found on each record", "etl/06_scan_components.sql"),
    PipelineStep(7, "Save the catalog product codes found on each record", "etl/07_scan_cpes.sql"),
    PipelineStep(8, "Save the vulnerabilities found on each record", "etl/08_scan_vulns.sql"),
    PipelineStep(9, "Keep one account for each customer domain", "etl/09_domains.sql"),
    PipelineStep(10, "Add indexes that are not there yet, and refresh table statistics", "etl/10_indexes.sql"),
    PipelineStep(11, "Rebuild the buying signals for every account", "etl/11_signals.sql"),
    PipelineStep(12, "Fill a blank country, company name, industry, or business type from the scan", "etl/12_enrich_account_domains.sql"),
    PipelineStep(13, "Rebuild need, fit, timing, reach, and the tier for every account", "etl/13_account_scores.sql"),
    PipelineStep(14, "Write a company overview for the top accounts", "jobs/generate_company_overview.py"),
    PipelineStep(15, "Write signal insights for the top accounts", "jobs/generate_signal_insights.py"),
    PipelineStep(16, "Write an outreach brief for the top accounts", "jobs/generate_outreach.py"),
    # The view file lives in setup/ because it is a definition. It runs here,
    # after the score and signal tables exist.
    PipelineStep(17, "Rebuild the queue and the account page the app opens", "setup/06_views.sql"),
]

ALL_STEPS = SETUP_STEPS + DATA_STEPS

STEPS_THAT_REBUILD_EVERY_ROW = {
    "03_refresh_flags.sql",
    "09_domains.sql",
    "11_signals.sql",
    "13_account_scores.sql",
}


def step_matches_requested_name(step_path: str, requested_name: str) -> bool:
    """Accept either the file stem (02_scan_domains) or the file name (02_scan_domains.sql)."""
    step = Path(step_path)
    return step.stem == requested_name or step.name == requested_name


def find_step(path: str) -> PipelineStep:
    for step in ALL_STEPS:
        if step.path == path:
            return step
    raise KeyError(path)


def choose_pipeline_steps(
    selected_steps: list[PipelineStep],
    single_step_name: str | None,
    start_from_step: str | None,
) -> list[PipelineStep]:
    if single_step_name:
        matching_steps = [
            step
            for step in ALL_STEPS
            if step_matches_requested_name(step.path, single_step_name)
        ]
        if not matching_steps:
            known_step_names = [Path(step.path).stem for step in ALL_STEPS]
            logger.error(
                "unknown step",
                requested_name=single_step_name,
                known_step_names=known_step_names,
            )
            raise SystemExit(1)
        return matching_steps
    if start_from_step:
        for index, step in enumerate(selected_steps):
            step_name = Path(step.path).stem
            starts_here = step.path.startswith("etl/") and (
                step_name == start_from_step or step_name.startswith(f"{start_from_step}_")
            )
            if starts_here:
                return selected_steps[index:]
        logger.error("unknown start step", start_from_step=start_from_step)
        raise SystemExit(1)
    return list(selected_steps)


def format_query_result(query_result) -> str:
    column_names = []
    for column_index in range(query_result.nfields):
        column_name = query_result.fname(column_index) or b"?"
        column_names.append(column_name.decode())
    printed_lines = ["\t".join(column_names)]
    rows_to_print = min(query_result.ntuples, 80)
    for row_index in range(rows_to_print):
        cell_values = []
        for column_index in range(query_result.nfields):
            cell = query_result.get_value(row_index, column_index)
            cell_values.append("" if cell is None else cell.decode("utf-8", errors="replace"))
        printed_lines.append("\t".join(cell_values))
    if query_result.ntuples > rows_to_print:
        printed_lines.append(f"... {query_result.ntuples - rows_to_print} more rows")
    return "\n".join(printed_lines)


def changed_row_count(query_result) -> int:
    command_status = query_result.command_status or b""
    command_name = command_status.split()[0] if command_status else b""
    if command_name not in {b"INSERT", b"UPDATE", b"DELETE"}:
        return 0
    return query_result.command_tuples or 0


def run_sql_script(database_connection: psycopg.Connection, sql_script: str) -> tuple[int, str | None]:
    database_connection.pgconn.send_query(sql_script.encode())
    rows_changed = 0
    error_message: str | None = None
    while True:
        query_result = database_connection.pgconn.get_result()
        if query_result is None:
            break
        query_status = ExecStatus(query_result.status)
        if query_status == ExecStatus.TUPLES_OK:
            logger.info(
                "query result",
                row_count=query_result.ntuples,
                result=format_query_result(query_result),
            )
        elif query_status == ExecStatus.COMMAND_OK:
            rows_changed += changed_row_count(query_result)
        elif query_status in {ExecStatus.FATAL_ERROR, ExecStatus.BAD_RESPONSE, ExecStatus.NONFATAL_ERROR}:
            error_message = query_result.get_error_message() or query_status.name
        elif query_status != ExecStatus.EMPTY_QUERY:
            message = query_result.get_error_message()
            if message:
                error_message = message
        query_result.clear()
    if database_connection.pgconn.transaction_status != TransactionStatus.IDLE:
        database_connection.pgconn.exec_(b"ROLLBACK")
        while True:
            leftover_result = database_connection.pgconn.get_result()
            if leftover_result is None:
                break
            leftover_result.clear()
    return rows_changed, error_message


def record_step_start(database_connection: psycopg.Connection, step_path: str, run_mode: str) -> int | None:
    try:
        return insert_etl_run(database_connection, step_path, run_mode, status="running")
    except psycopg.errors.UndefinedTable:
        logger.debug("run log skipped", reason="etl_runs does not exist yet", step_path=step_path)
        return None


def record_step_finish(
    database_connection: psycopg.Connection,
    etl_run_id: int | None,
    *,
    status: str,
    rows_changed: int,
    error_message: str | None,
) -> None:
    if etl_run_id is None:
        return
    update_etl_run(database_connection, etl_run_id, status, rows_changed, error_message)


def run_python_step(database_connection: psycopg.Connection, step_file: Path, step_path: str, pipeline_run_id: str) -> None:
    import importlib.util

    loaded_module = importlib.util.spec_from_file_location(step_file.stem, step_file)
    if loaded_module is None or loaded_module.loader is None:
        raise RuntimeError(f"cannot load {step_file}")
    python_step = importlib.util.module_from_spec(loaded_module)
    loaded_module.loader.exec_module(python_step)
    if step_path in SHARED_BUDGET_JOBS:
        python_step.main(database_connection, run_id=pipeline_run_id)
    else:
        python_step.main(database_connection)


def run_pipeline_step(database_connection: psycopg.Connection, step: PipelineStep, pipeline_run_id: str) -> None:
    step_path = step.path
    step_file = beside(__file__, step_path)
    run_mode = "full-refresh" if step_file.name in STEPS_THAT_REBUILD_EVERY_ROW else "incremental"
    logger.info(
        "step started",
        step_number=step.number,
        description=step.description,
        step_path=step_path,
        run_mode=run_mode,
    )
    started_at = time.monotonic()
    etl_run_id = record_step_start(database_connection, step_path, run_mode)
    rows_changed = 0
    error_message = None
    try:
        if step_file.suffix == ".py":
            run_python_step(database_connection, step_file, step_path, pipeline_run_id)
        else:
            rows_changed, error_message = run_sql_script(database_connection, read_sql(__file__, step_path))
    except SystemExit as exit_signal:
        exit_code = exit_signal.code
        if exit_code in (0, None):
            raise
        error_message = exit_code if isinstance(exit_code, str) else f"step exited with code {exit_code}"
    except Exception as step_error:
        error_message = str(step_error)
    elapsed_seconds = round(time.monotonic() - started_at, 1)
    if error_message:
        record_step_finish(
            database_connection,
            etl_run_id,
            status="error",
            rows_changed=rows_changed,
            error_message=error_message[:4000],
        )
        logger.error(
            "step failed",
            step_number=step.number,
            description=step.description,
            step_path=step_path,
            elapsed_seconds=elapsed_seconds,
            error=error_message,
        )
        raise SystemExit(1)
    record_step_finish(
        database_connection,
        etl_run_id,
        status="ok",
        rows_changed=rows_changed,
        error_message=None,
    )
    logger.info(
        "step finished",
        step_path=step_path,
        rows_affected=rows_changed,
        elapsed_seconds=elapsed_seconds,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Shodan normalization SQL.")
    parser.add_argument("--setup", action="store_true", help="Create tables, columns, and seed lists. Use on a new database or after a definition change.")
    parser.add_argument("--all", action="store_true", help="Load records into accounts, signals, and scores.")
    parser.add_argument("--only", dest="single_step_name", help="Run one file, by stem (for example 02_scan_domains).")
    parser.add_argument("--from", dest="start_from_step", help="Run this etl number and everything after it in the selected group.")
    arguments = parser.parse_args()
    run_setup = arguments.setup
    run_data = arguments.all
    single_step_name = arguments.single_step_name
    start_from_step = arguments.start_from_step
    if not any((run_setup, run_data, single_step_name, start_from_step)):
        parser.error("pass --setup, --all, --only, or --from")
    if start_from_step and not any((run_setup, run_data)):
        run_data = True
    selected_steps: list[PipelineStep] = []
    if run_setup:
        selected_steps.extend(SETUP_STEPS)
    if run_data:
        selected_steps.extend(DATA_STEPS)
    pipeline_steps = choose_pipeline_steps(selected_steps, single_step_name, start_from_step)
    pipeline_run_id = f"pipeline-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}"
    logger.info("pipeline run", run_id=pipeline_run_id)
    database_connection = open_database_connection()
    try:
        for step in pipeline_steps:
            run_pipeline_step(database_connection, step, pipeline_run_id)
    finally:
        database_connection.close()


if __name__ == "__main__":
    main()
