"""Centralized logging configuration.

Call ``configure_logging()`` once at server startup. Controlled by env vars:

    LOG_LEVEL   DEBUG | INFO | WARNING | ERROR  (default: INFO)
    LOG_FORMAT  text | json                     (default: text)

JSON mode emits one compact object per record — compatible with Loki,
CloudWatch, Datadog, and any log aggregator that accepts NDJSON.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

# Valid Python logging level names. Anything else falls back to INFO.
_PYTHON_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"})

_TEXT_FMT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"

# Handler installed by the most recent configure_logging() call.
# We remove only our own handler on re-configuration, leaving any handlers
# installed by pytest (caplog), uvicorn, or third-party code intact.
_installed_handler: logging.Handler | None = None


class _JsonFormatter(logging.Formatter):
    """One JSON object per log record, written to a single line."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # exc_info=(None, None, None) is a truthy tuple — guard on the type explicitly.
        if record.exc_info and record.exc_info[0] is not None:
            obj["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            obj["stack"] = self.formatStack(record.stack_info)
        return json.dumps(obj, ensure_ascii=False)


def configure_logging() -> None:
    """Configure root logger from LOG_LEVEL / LOG_FORMAT env vars."""
    global _installed_handler

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    if level_name not in _PYTHON_LEVELS:
        # Unknown level — fall back silently so startup never fails.
        level_name = "INFO"
    level = getattr(logging, level_name)

    log_format = os.environ.get("LOG_FORMAT", "text").lower()

    handler = logging.StreamHandler(sys.stderr)
    if log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FMT, datefmt=_DATE_FMT))

    root = logging.getLogger()
    root.setLevel(level)

    # Remove only the handler we previously installed — do not touch handlers
    # added by pytest (caplog), uvicorn, or other libraries.
    if _installed_handler is not None:
        root.removeHandler(_installed_handler)

    root.addHandler(handler)
    _installed_handler = handler
