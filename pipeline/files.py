"""Read a file that ships next to the calling module.

The database login is not read here. load_dotenv() uses the folder where the
command was started.
"""

from __future__ import annotations

import json
from pathlib import Path


def beside(anchor_file: str, relative_path: str) -> Path:
    return (Path(anchor_file).resolve().parent / relative_path).resolve()


def read_text(anchor_file: str, relative_path: str) -> str:
    return beside(anchor_file, relative_path).read_text()


def read_sql(anchor_file: str, relative_path: str) -> str:
    return read_text(anchor_file, relative_path)


def read_json(anchor_file: str, relative_path: str):
    return json.loads(read_text(anchor_file, relative_path))


def read_json_files(anchor_file: str, relative_directory: str) -> list:
    folder = beside(anchor_file, relative_directory)
    return [json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))]
