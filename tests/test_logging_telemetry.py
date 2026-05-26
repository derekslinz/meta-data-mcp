"""Tests for logging_config and telemetry modules."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch


from meta_data_mcp.logging_config import _JsonFormatter, configure_logging
from meta_data_mcp.telemetry import configure_otel


# ---------------------------------------------------------------------------
# logging_config
# ---------------------------------------------------------------------------


def test_configure_logging_default_level(monkeypatch):
    """LOG_LEVEL defaults to INFO."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_respects_log_level(monkeypatch):
    """LOG_LEVEL=DEBUG sets the root logger to DEBUG."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_invalid_level_falls_back_to_info(monkeypatch):
    """An unrecognised LOG_LEVEL value falls back to INFO."""
    monkeypatch.setenv("LOG_LEVEL", "NONSENSE")
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_json_format(monkeypatch):
    """LOG_FORMAT=json installs a _JsonFormatter on the root handler."""
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging()
    root = logging.getLogger()
    assert any(isinstance(h.formatter, _JsonFormatter) for h in root.handlers)


def test_configure_logging_text_format(monkeypatch):
    """LOG_FORMAT=text (default) does NOT install a _JsonFormatter."""
    monkeypatch.setenv("LOG_FORMAT", "text")
    configure_logging()
    root = logging.getLogger()
    assert not any(isinstance(h.formatter, _JsonFormatter) for h in root.handlers)


def test_json_formatter_output():
    """_JsonFormatter produces valid JSON with expected keys."""
    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    line = formatter.format(record)
    obj = json.loads(line)
    assert obj["level"] == "WARNING"
    assert obj["logger"] == "test.logger"
    assert obj["msg"] == "hello world"
    assert "ts" in obj


def test_json_formatter_includes_exception():
    """_JsonFormatter includes exc field when exc_info is present."""
    formatter = _JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="oops",
        args=(),
        exc_info=exc_info,
    )
    obj = json.loads(formatter.format(record))
    assert "exc" in obj
    assert "ValueError" in obj["exc"]


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------


def test_configure_otel_disabled(monkeypatch):
    """OTEL_SDK_DISABLED=true skips all setup."""
    import meta_data_mcp.telemetry as tel

    tel._configured = False
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    with patch.dict("sys.modules", {"opentelemetry": None}):
        configure_otel()
    # Reaches here without ImportError — disabled path short-circuits before import.


def test_configure_otel_no_sdk(monkeypatch):
    """Missing opentelemetry SDK is silently ignored."""
    import meta_data_mcp.telemetry as tel

    tel._configured = False
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)

    with patch.dict(
        "sys.modules",
        {
            "opentelemetry": None,
            "opentelemetry.sdk": None,
            "opentelemetry.sdk.trace": None,
            "opentelemetry.sdk.resources": None,
            "opentelemetry.sdk.trace.export": None,
        },
    ):
        configure_otel()  # must not raise


def test_configure_otel_console_exporter(monkeypatch):
    """OTEL_TRACES_EXPORTER=console wires ConsoleSpanExporter."""
    import sys

    import meta_data_mcp.telemetry as tel

    tel._configured = False
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    mock_provider = MagicMock()
    mock_console_exporter_cls = MagicMock()
    mock_processor = MagicMock()
    mock_trace_mod = MagicMock()
    mock_resource_cls = MagicMock(return_value=MagicMock())

    # Build a fake opentelemetry module tree so the lazy imports inside
    # configure_otel() resolve to our mocks.
    fake_otel = MagicMock()
    fake_otel_sdk = MagicMock()
    fake_otel_sdk_trace = MagicMock()
    fake_otel_sdk_trace.TracerProvider = MagicMock(return_value=mock_provider)
    fake_otel_sdk_resources = MagicMock()
    fake_otel_sdk_resources.Resource = mock_resource_cls
    fake_otel_sdk_resources.SERVICE_NAME = "service.name"
    fake_otel_sdk_trace_export = MagicMock()
    fake_otel_sdk_trace_export.BatchSpanProcessor = MagicMock(
        return_value=mock_processor
    )
    fake_otel_sdk_trace_export.ConsoleSpanExporter = mock_console_exporter_cls
    fake_otel.trace = mock_trace_mod

    fake_httpx_instr = MagicMock()
    fake_httpx_instr.HTTPXClientInstrumentor.return_value.instrument = MagicMock()

    modules = {
        "opentelemetry": fake_otel,
        "opentelemetry.trace": mock_trace_mod,
        "opentelemetry.sdk": fake_otel_sdk,
        "opentelemetry.sdk.trace": fake_otel_sdk_trace,
        "opentelemetry.sdk.resources": fake_otel_sdk_resources,
        "opentelemetry.sdk.trace.export": fake_otel_sdk_trace_export,
        "opentelemetry.instrumentation": MagicMock(),
        "opentelemetry.instrumentation.httpx": fake_httpx_instr,
    }
    with patch.dict(sys.modules, modules):
        configure_otel()

    mock_provider.add_span_processor.assert_called_once()


def test_configure_otel_idempotent(monkeypatch):
    """configure_otel() called twice only configures once."""
    import meta_data_mcp.telemetry as tel

    tel._configured = False
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    configure_otel()
    assert tel._configured is True

    # Second call must return immediately — the _configured guard prevents re-entry.
    configure_otel()
    assert tel._configured is True  # unchanged; idempotent
