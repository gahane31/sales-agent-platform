"""Register prompt files and load the active one. Versions sort by number, not by text."""

from __future__ import annotations

import hashlib

import psycopg

from pipeline.db.sql_statements import (
    activate_newest_prompt,
    activate_prompt_registry_if_none_active,
    insert_prompt_registry,
    select_active_prompt_registry_path,
    select_prompt_registry_path,
)
from pipeline.files import beside

PROMPTS = beside(__file__, "prompts")
PIPELINE = beside(__file__, "..")


def register_prompts(conn: psycopg.Connection) -> None:
    if not PROMPTS.exists():
        return
    present = []
    for skill_dir in PROMPTS.iterdir():
        if not skill_dir.is_dir():
            continue
        present.append(skill_dir.name)
        for path in skill_dir.glob("v*.md"):
            version = path.stem
            text = path.read_text()
            checksum = hashlib.sha256(text.encode()).hexdigest()
            insert_prompt_registry(
                conn,
                skill_dir.name,
                version,
                str(path.relative_to(PIPELINE)),
                checksum,
            )
            activate_prompt_registry_if_none_active(conn, skill_dir.name, version)
        activate_newest_prompt(conn, skill_dir.name)
    if present:
        conn.execute(
            "update public.prompt_registry set is_active = false where not (skill = any(%s))",
            (present,),
        ).close()


def load_prompt(conn: psycopg.Connection, skill: str, version: str | None = None) -> str:
    register_prompts(conn)
    if version:
        row = select_prompt_registry_path(conn, skill, version)
    else:
        row = select_active_prompt_registry_path(conn, skill)
    if row is None:
        raise RuntimeError(f"no prompt registered for {skill}")
    return (PIPELINE / row[0]).read_text()
