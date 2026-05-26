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


class _JsonFormatter(logging.Formatter):
    """One JSON object per log record, written to a single line."""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            obj["stack"] = self.formatStack(record.stack_info)
        return json.dumps(obj, ensure_ascii=False)


_TEXT_FMT = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"


def configure_logging() -> None:
    """Configure root logger from LOG_LEVEL / LOG_FORMAT env vars."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_format = os.environ.get("LOG_FORMAT", "text").lower()

    handler = logging.StreamHandler(sys.stderr)
    if log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_TEXT_FMT, datefmt=_DATE_FMT))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
