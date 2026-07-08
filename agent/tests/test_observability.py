import json
import logging
import logging.config

import pytest

from agent.observability import (
    configure_logging,
    current_trace_id,
    make_handler,
    uvicorn_log_config,
)


def test_json_format_emits_valid_json_with_expected_fields(capsys):
    configure_logging(level="INFO", fmt="json")
    logging.getLogger("test").info("hello", extra={"trade_id": 7})
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["trade_id"] == 7


def test_text_format_emits_plain_text_not_json(capsys):
    configure_logging(level="INFO", fmt="text")
    logging.getLogger("test").info("plain hello")
    out = capsys.readouterr().out.strip()
    assert "plain hello" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_configure_logging_is_idempotent(capsys):
    configure_logging(level="INFO", fmt="json")
    configure_logging(level="INFO", fmt="json")
    logging.getLogger("test").info("once")
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1  # not doubled by a second stacked handler


def test_make_handler_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert make_handler(run_id=1) is None


def test_make_handler_returns_handler_with_metadata_when_configured(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:1")
    handler = make_handler(run_id=42)
    assert handler is not None
    assert handler.metadata == {"run_id": 42}


def test_current_trace_id_none_for_no_handler():
    assert current_trace_id(None) is None


def test_current_trace_id_reads_last_trace_id_attribute():
    class Stub:
        last_trace_id = "trace-abc"

    assert current_trace_id(Stub()) == "trace-abc"


def test_uvicorn_log_config_disables_own_handlers_for_propagation():
    cfg = uvicorn_log_config()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert cfg["loggers"][name]["handlers"] == []
        assert cfg["loggers"][name]["propagate"] is True


def test_uvicorn_loggers_propagate_through_configured_root_formatter(capsys):
    configure_logging(level="INFO", fmt="json")
    logging.config.dictConfig(uvicorn_log_config())

    logging.getLogger("uvicorn.access").info('127.0.0.1:0 - "GET /health HTTP/1.1" 200')

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["logger"] == "uvicorn.access"
