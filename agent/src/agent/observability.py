from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

_DEFAULT_RECORD_ATTRS = frozenset(vars(logging.makeLogRecord({})).keys())


class _JsonFormatter(logging.Formatter):
    """Render a ``LogRecord`` as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _DEFAULT_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Install a stdout formatter (json or text) on the root logger.

    Idempotent: replaces the root logger's handlers rather than stacking a
    new one on every call.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter() if fmt == "json" else logging.Formatter(_TEXT_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)


def uvicorn_log_config() -> dict:
    """A ``uvicorn`` ``log_config`` that skips its own handler setup.

    Uvicorn otherwise attaches its own (non-JSON, non-propagating) handlers
    to the ``uvicorn``/``uvicorn.access``/``uvicorn.error`` loggers, which
    bypasses whatever ``configure_logging`` set up on the root logger --
    that's why access logs showed up as plain text lines alongside our JSON
    ones. Declaring no handlers here and ``propagate: True`` sends those
    records up to the root logger's handler instead, so they pick up
    whichever format (json/text) ``configure_logging`` was given.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "%(message)s"},
            "access": {"format": "%(message)s"},
        },
        "loggers": {
            "uvicorn": {"handlers": [], "propagate": True},
            "uvicorn.error": {"handlers": [], "propagate": True},
            "uvicorn.access": {"handlers": [], "propagate": True},
        },
    }


def make_handler(run_id: int | str) -> CallbackHandler | None:
    """Build a Langfuse LangChain callback handler for a single run.

    Returns ``None`` when ``LANGFUSE_PUBLIC_KEY``/``LANGFUSE_SECRET_KEY`` aren't
    set -- tracing is fully optional. ``LANGFUSE_ADDRESS`` (this project's env
    var name, not the SDK's own ``LANGFUSE_HOST``) is passed explicitly to the
    ``Langfuse`` client's ``host=`` kwarg, which registers it as the active
    singleton instance that ``CallbackHandler`` then reuses via
    ``langfuse.get_client()`` -- no env var renaming needed.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        return None
    Langfuse(public_key=public_key, secret_key=secret_key,
              host=os.environ.get("LANGFUSE_ADDRESS"))
    handler = CallbackHandler(public_key=public_key)
    handler.metadata = {"run_id": run_id}
    return handler


def current_trace_id(handler: object | None) -> str | None:
    if handler is None:
        return None
    return getattr(handler, "last_trace_id", None)
