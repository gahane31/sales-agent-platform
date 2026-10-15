#!/usr/bin/env python3
"""Load a JSONL file into public.shodan_records in small batches.

Each line is stored as one row in the data column. Progress is written to a
checkpoint so a failed run can continue without inserting the same rows again.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

project_directory = Path.cwd()
if str(project_directory) not in sys.path:
    sys.path.insert(0, str(project_directory))

from pipeline.logger import get_logger

logger = get_logger("import_shodan_records")

DEFAULT_INPUT = project_directory / "data" / "shodan_sample_100k.jsonl"
DEFAULT_CHECKPOINT = project_directory / "data" / ".shodan_import_checkpoint.json"
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def save_checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


def load_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _contains_nul(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, list):
        return any(_contains_nul(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_nul(key) or _contains_nul(item) for key, item in value.items())
    return False


def _strip_nuls(value: object) -> object:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_strip_nuls(item) for item in value]
    if isinstance(value, dict):
        return {_strip_nuls(key): _strip_nuls(item) for key, item in value.items()}
    return value


def row_payload(line: bytes) -> bytes:
    stripped = line.strip().replace(b"\x00", b"")
    record = json.loads(stripped)
    if not isinstance(record, dict):
        raise ValueError("line is not a JSON object")
    # Postgres jsonb rejects \\u0000. Keep the original JSON unless a real NUL is present.
    if b"\\u0000" not in stripped or not _contains_nul(record):
        return b'{"data":' + stripped + b"}"
    cleaned = json.dumps({"data": _strip_nuls(record)}, ensure_ascii=False, separators=(",", ":"))
    return cleaned.encode("utf-8")


class SupabaseInserter:
    def __init__(self, base_url: str, api_key: str, timeout: float) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"expected an https Supabase URL, got {base_url}")
        self.host = parsed.hostname
        self.api_key = api_key
        self.timeout = timeout
        self.connection: http.client.HTTPSConnection | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connect(self) -> http.client.HTTPSConnection:
        if self.connection is None:
            self.connection = http.client.HTTPSConnection(self.host, timeout=self.timeout)
        return self.connection

    def insert(self, body: bytes) -> None:
        connection = self._connect()
        try:
            connection.request(
                "POST",
                "/rest/v1/shodan_records",
                body=body,
                headers={
                    "apikey": self.api_key,
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
            )
            response = connection.getresponse()
            payload = response.read().decode("utf-8", errors="replace")
            status = response.status
            retry_after = response.getheader("Retry-After")
        except Exception:
            self.close()
            raise
        if status >= 400:
            self.close()
            raise InsertError(status, payload[:300], retry_after)


class InsertError(Exception):
    def __init__(self, status: int, detail: str, retry_after: str | None) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.retry_after = retry_after


def insert_batch(inserter: SupabaseInserter, rows: list[bytes], attempts: int) -> None:
    body = b"[" + b",".join(rows) + b"]"
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            inserter.insert(body)
            return
        except InsertError as exc:
            if exc.status not in RETRYABLE_STATUS or attempt == attempts:
                raise RuntimeError(str(exc)) from exc
            wait = delay
            if exc.retry_after and exc.retry_after.replace(".", "", 1).isdigit():
                wait = float(exc.retry_after)
            logger.warning(
                "import batch failed",
                status=exc.status,
                attempt=attempt,
                attempts=attempts,
                retry_in_seconds=round(wait, 1),
            )
            time.sleep(wait)
            delay = min(delay * 2, 60)
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            inserter.close()
            if attempt == attempts:
                raise RuntimeError(f"network error: {exc}") from exc
            logger.warning(
                "import batch failed",
                error=str(exc),
                attempt=attempt,
                attempts=attempts,
                retry_in_seconds=round(delay, 1),
            )
            time.sleep(delay)
            delay = min(delay * 2, 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load JSONL rows into shodan_records in batches.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--batch-rows", type=int, default=50)
    parser.add_argument("--batch-bytes", type=int, default=800_000)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--attempts", type=int, default=6)
    args = parser.parse_args()

    load_dotenv(project_directory / ".env")
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required in .env")
    if not args.input.exists():
        raise RuntimeError(f"input not found: {args.input}")

    input_key = str(args.input.resolve())
    checkpoint = load_checkpoint(args.checkpoint)
    if checkpoint.get("done") and checkpoint.get("input") == input_key:
        logger.info(
            "import already finished",
            inserted=checkpoint.get("inserted"),
            input=str(args.input),
        )
        return
    skip = int(checkpoint["inserted"]) if checkpoint.get("input") == input_key else 0

    inserter = SupabaseInserter(url, key, args.timeout)
    inserted = skip
    seen = 0
    batch: list[bytes] = []
    batch_bytes = 0
    started = time.monotonic()

    def flush() -> None:
        nonlocal inserted, batch, batch_bytes
        if not batch:
            return
        count = len(batch)
        insert_batch(inserter, batch, args.attempts)
        inserted += count
        save_checkpoint(args.checkpoint, {"input": input_key, "inserted": inserted, "done": False})
        elapsed = time.monotonic() - started
        logger.info("import progress", inserted=inserted, elapsed_seconds=round(elapsed))
        batch = []
        batch_bytes = 0

    try:
        with args.input.open("rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                seen += 1
                if seen <= skip:
                    continue
                try:
                    payload = row_payload(line)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise RuntimeError(f"line {seen}: {exc}") from exc
                row_would_overflow = batch and (
                    len(batch) >= args.batch_rows or batch_bytes + len(payload) > args.batch_bytes
                )
                if row_would_overflow:
                    flush()
                batch.append(payload)
                batch_bytes += len(payload)
                if batch_bytes >= args.batch_bytes:
                    flush()
            flush()
    finally:
        inserter.close()

    save_checkpoint(args.checkpoint, {"input": input_key, "inserted": inserted, "done": True})
    logger.info("import finished", inserted=inserted)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        logger.error("import stopped", error=str(exc))
        raise SystemExit(1) from exc
