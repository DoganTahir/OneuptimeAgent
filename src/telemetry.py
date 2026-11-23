"""OpenTelemetry tracing setup for the Agent Observability POC."""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence

from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

DEFAULT_SERVICE_NAME = "oneuptime-agent-poc"
DEFAULT_EXPORT_MODE = "console"
OTLP_TRACES_PATH = "v1/traces"
ONEUPTIME_TOKEN_HEADER = "x-oneuptime-token"
DEFAULT_OTLP_TIMEOUT_SECONDS = 10.0

logger = logging.getLogger(__name__)


class TelemetryConfigError(ValueError):
    """Raised when required telemetry settings are missing or invalid."""


def build_otlp_traces_endpoint(base_endpoint: str) -> str:
    """Join a OneUptime ``.../otlp`` base URL with the OTLP HTTP traces path."""
    base = base_endpoint.strip().rstrip("/")
    if base.endswith(f"/{OTLP_TRACES_PATH}"):
        return base
    return f"{base}/{OTLP_TRACES_PATH}"


def get_export_mode() -> str:
    """Return the configured export mode (``console`` or ``oneuptime``)."""
    return os.getenv("OTEL_EXPORT_MODE", DEFAULT_EXPORT_MODE).strip().lower()


def create_oneuptime_exporter(
    *,
    timeout: float = DEFAULT_OTLP_TIMEOUT_SECONDS,
) -> OTLPSpanExporter:
    """Build an OTLP/HTTP exporter targeting OneUptime 14.x.

    Expects ``ONEUPTIME_OTLP_ENDPOINT`` in ``https://host/otlp`` form and appends
    ``/v1/traces``. TLS verification remains enabled (default).
    """
    endpoint = os.getenv("ONEUPTIME_OTLP_ENDPOINT", "").strip()
    token = os.getenv("ONEUPTIME_INGESTION_TOKEN", "").strip()

    missing: list[str] = []
    if not endpoint:
        missing.append("ONEUPTIME_OTLP_ENDPOINT")
    if not token:
        missing.append("ONEUPTIME_INGESTION_TOKEN")
    if missing:
        raise TelemetryConfigError(
            "OTEL_EXPORT_MODE=oneuptime requires: " + ", ".join(missing)
        )

    traces_endpoint = build_otlp_traces_endpoint(endpoint)
    logger.info("Configuring OneUptime OTLP/HTTP exporter for traces")

    return OTLPSpanExporter(
        endpoint=traces_endpoint,
        headers={ONEUPTIME_TOKEN_HEADER: token},
        timeout=timeout,
    )


def create_span_exporter() -> SpanExporter:
    """Create the span exporter for the active ``OTEL_EXPORT_MODE``."""
    mode = get_export_mode()
    if mode == "console":
        return ConsoleSpanExporter()
    if mode == "oneuptime":
        return SafeSpanExporter(create_oneuptime_exporter())
    raise TelemetryConfigError(
        f"Unsupported OTEL_EXPORT_MODE={mode!r}. Use 'console' or 'oneuptime'."
    )


class SafeSpanExporter(SpanExporter):
    """Delegate exporter that isolates timeout / connection failures."""

    def __init__(self, delegate: SpanExporter) -> None:
        self._delegate = delegate

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            return self._delegate.export(spans)
        except Exception as exc:  # noqa: BLE001 — export must not crash the app
            logger.error("Span export failed: %s", type(exc).__name__)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.error("Span exporter shutdown failed: %s", type(exc).__name__)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return bool(self._delegate.force_flush(timeout_millis))
        except Exception:  # noqa: BLE001
            return False


def setup_telemetry(*, exporter: SpanExporter | None = None) -> TracerProvider:
    """Configure a TracerProvider with console or OneUptime OTLP export.

    Reads ``OTEL_SERVICE_NAME`` and ``OTEL_EXPORT_MODE`` from the environment /
    ``.env``. Pass ``exporter`` to inject a test double and skip mode selection.
    """
    load_dotenv()
    service_name = os.getenv("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME)

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    span_exporter = exporter if exporter is not None else create_span_exporter()
    # SimpleSpanProcessor exports synchronously — reliable for console demos/tests.
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    trace.set_tracer_provider(provider)
    return provider


def get_tracer(name: str = __name__) -> trace.Tracer:
    """Return a tracer bound to the global TracerProvider."""
    return trace.get_tracer(name)


def shutdown_telemetry(provider: TracerProvider) -> None:
    """Flush pending spans and shut down the provider."""
    try:
        provider.force_flush()
    except Exception as exc:  # noqa: BLE001
        logger.error("Telemetry force_flush failed: %s", type(exc).__name__)
    try:
        provider.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.error("Telemetry shutdown failed: %s", type(exc).__name__)
