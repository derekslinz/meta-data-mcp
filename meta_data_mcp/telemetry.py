"""Optional OpenTelemetry tracing setup.

Call ``configure_otel()`` once at server startup. All configuration is driven
by standard OTEL env vars — no code changes needed to switch exporters:

    OTEL_SDK_DISABLED=true              skip setup entirely (set in stdio mode)
    OTEL_SERVICE_NAME=meta-data-mcp    service name tag (default shown)
    OTEL_EXPORTER_OTLP_ENDPOINT=...    send spans to this OTLP HTTP endpoint
    OTEL_TRACES_EXPORTER=console       dump spans to stderr for local debugging

Install the optional extras to enable:
    pip install 'meta-data-mcp[otel]'

If the SDK is not installed, ``configure_otel()`` is a silent no-op.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_configured = False


def configure_otel() -> None:
    """Wire up OpenTelemetry tracing. Safe to call even without the SDK installed."""
    global _configured
    if _configured:
        return
    _configured = True

    if os.environ.get("OTEL_SDK_DISABLED", "").lower() in ("1", "true", "yes", "on"):
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return

    service_name = os.environ.get("OTEL_SERVICE_NAME", "meta-data-mcp")
    resource = Resource({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    exporter_name = os.environ.get("OTEL_TRACES_EXPORTER", "").lower()

    if exporter_name == "console":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        log.info("OpenTelemetry: console exporter active (service=%s)", service_name)
    elif otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
            )
            log.info(
                "OpenTelemetry: OTLP exporter → %s (service=%s)",
                otlp_endpoint,
                service_name,
            )
        except ImportError:
            log.warning(
                "OTEL_EXPORTER_OTLP_ENDPOINT is set but "
                "opentelemetry-exporter-otlp-proto-http is not installed. "
                "Run: pip install 'meta-data-mcp[otel]'"
            )

    trace.set_global_tracer_provider(provider)

    # Auto-instrument every httpx request so provider API calls get spans.
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        log.debug("OpenTelemetry: httpx instrumented")
    except ImportError:
        pass
