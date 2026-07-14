"""Structured JSON logging.

Every log record is emitted as a single-line JSON object on stdout so the
output is easy to read and ingest.

Callers pass structured fields through the standard ``extra=`` keyword.
Anything in ``extra`` is merged into the JSON payload; the framework's own
LogRecord attributes are filtered out so they never leak into the output.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

LOGGER_NAME = "tars"

# Attributes that the ``logging`` framework populates on every LogRecord.
# They are intentionally excluded from the structured JSON payload.
_RESERVED_LOGRECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a :class:`LogRecord` as a compact JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
        }

        # Merge structured context supplied by the caller via ``extra=``.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOGRECORD_ATTRS:
                continue
            payload[key] = value

        # ``message`` is emitted last to match the documented field order.
        payload["message"] = record.getMessage()

        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging() -> logging.Logger:
    """Configure the application logger to emit JSON to stdout."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger
