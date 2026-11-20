"""Structured JSON logging with a per-request correlation id (contextvar)."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_REDACT = ("authorization", "api_key", "signature", "secret", "payment_method")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": round(time.time(), 3), "level": record.levelname.lower(), "logger": record.name,
               "msg": record.getMessage(), "request_id": request_id.get()}
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            out.update({k: ("[redacted]" if any(r in k.lower() for r in _REDACT) else v) for k, v in extra.items()})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def configure(level: str | None = None) -> None:
    import os
    level = level or os.environ.get("AGENTPAY_LOG_LEVEL", "INFO")
    root = logging.getLogger("agentpay")
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(level)
    root.propagate = False


def log(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    logger.log(level, msg, extra={"fields": fields})
