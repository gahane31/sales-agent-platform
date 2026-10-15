"""One structured logger for the pipeline.

Each file asks for its own tag, for example get_logger("runner").
A log line is one JSON object with a timestamp, a level, that tag, a message,
and any extra fields passed to the call:

    logger.info("step started", step_path=step_path, run_mode=run_mode)

Set LOG_LEVEL to DEBUG, INFO, WARNING, or ERROR. The default is INFO.
Lines go to standard error.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

_CONFIGURED = False
_RESERVED_LOG_ARGUMENTS = {"exc_info", "stack_info", "stacklevel", "extra"}


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "component": getattr(record, "component", record.name),
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            event["fields"] = fields
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event, default=str, ensure_ascii=False)


class ComponentLogger(logging.LoggerAdapter):
    """Lets a call pass step_path=... instead of wrapping values in extra=."""

    def process(self, msg, kwargs):
        fields = {
            key: kwargs.pop(key)
            for key in list(kwargs)
            if key not in _RESERVED_LOG_ARGUMENTS
        }
        extra = dict(kwargs.get("extra") or {})
        merged_fields = dict(extra.get("fields") or {})
        merged_fields.update(fields)
        extra["fields"] = merged_fields
        extra["component"] = self.extra["component"]
        kwargs["extra"] = extra
        return msg, kwargs


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    pipeline_logger = logging.getLogger("sales_agent")
    pipeline_logger.setLevel(level)
    pipeline_logger.propagate = False
    if not pipeline_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(StructuredFormatter())
        pipeline_logger.addHandler(handler)
    _CONFIGURED = True


def get_logger(component: str) -> ComponentLogger:
    _configure()
    component_logger = logging.getLogger(f"sales_agent.{component}")
    return ComponentLogger(component_logger, {"component": component})
