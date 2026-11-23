"""OpenTelemetry tracing setup for the Agent Observability POC."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import requests
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
OTLP_TRACES_SUFFIX = f"/otlp/{OTLP_TRACES_PATH}"
ONEUPTIME_TOKEN_HEADER = "x-oneuptime-token"
DEFAULT_OTLP_TIMEOUT_SECONDS = 10.0

logger = logging.getLogger(__name__)

# Active reporter for oneuptime mode (manual runs verify export outcome via this).
_active_safe_exporter: SafeSpanExporter | None = None


class TelemetryConfigError(ValueError):
    """Raised when required telemetry settings are missing or invalid."""


@dataclass(frozen=True)
class ExportOutcome:
    """Result of a single span export attempt (collector accept ≠ UI visibility)."""

    success: bool
    category: str
    detail: str
    status_code: int | None = None


def build_otlp_traces_endpoint(base_endpoint: str) -> str:
    """Join a OneUptime ``.../otlp`` base URL with the OTLP HTTP traces path."""
    base = base_endpoint.strip().rstrip("/")
    if base.endswith(f"/{OTLP_TRACES_PATH}"):
        return base
    return f"{base}/{OTLP_TRACES_PATH}"


def validate_otlp_traces_endpoint(endpoint: str) -> str:
    """Ensure the resolved URL ends with ``/otlp/v1/traces`` exactly once."""
    traces_endpoint = build_otlp_traces_endpoint(endpoint)
    normalized = traces_endpoint.rstrip("/")

    if normalized.count(f"/{OTLP_TRACES_PATH}") != 1:
        raise TelemetryConfigError(
            f"OTLP path /{OTLP_TRACES_PATH} must appear exactly once in the endpoint"
        )
    if normalized.count("/otlp/") != 1:
        raise TelemetryConfigError(
            "OTLP base path /otlp/ must appear exactly once in the endpoint"
        )
    if not normalized.endswith(OTLP_TRACES_SUFFIX):
        raise TelemetryConfigError(
            "ONEUPTIME_OTLP_ENDPOINT must resolve to a URL ending with "
            f"{OTLP_TRACES_SUFFIX} (got a URL that does not)"
        )
    return traces_endpoint


def get_export_mode() -> str:
    """Return the configured export mode (``console`` or ``oneuptime``)."""
    return os.getenv("OTEL_EXPORT_MODE", DEFAULT_EXPORT_MODE).strip().lower()


def get_safe_exporter() -> SafeSpanExporter | None:
    """Return the active ``SafeSpanExporter`` when running in oneuptime mode."""
    return _active_safe_exporter


def get_last_export_outcome() -> ExportOutcome | None:
    """Return the most recent export outcome, if any."""
    if _active_safe_exporter is None:
        return None
    return _active_safe_exporter.last_outcome


def classify_http_status(status_code: int) -> ExportOutcome:
    """Map an HTTP status code to a safe, user-visible export outcome."""
    if 200 <= status_code < 300:
        return ExportOutcome(
            True,
            "success",
            f"OTLP export accepted by collector (HTTP {status_code})",
            status_code,
        )
    mapping: dict[int, tuple[str, str]] = {
        401: ("http_401", "OTLP export unauthorized (HTTP 401) — check ingestion token"),
        403: ("http_403", "OTLP export forbidden (HTTP 403)"),
        404: ("http_404", "OTLP export endpoint not found (HTTP 404)"),
        429: ("http_429", "OTLP export rate limited (HTTP 429)"),
    }
    if status_code in mapping:
        category, detail = mapping[status_code]
        return ExportOutcome(False, category, detail, status_code)
    if 500 <= status_code < 600:
        return ExportOutcome(
            False,
            "http_5xx",
            f"OTLP export server error (HTTP {status_code})",
            status_code,
        )
    return ExportOutcome(
        False,
        "export_failed",
        f"OTLP export failed (HTTP {status_code})",
        status_code,
    )


def classify_exception(exc: BaseException) -> ExportOutcome:
    """Classify timeout/connection errors without logging sensitive exception text."""
    # Never use str(exc): requests errors often embed full URLs.
    exc_name = type(exc).__name__
    lowered = exc_name.lower()

    if isinstance(exc, TimeoutError) or "timeout" in lowered:
        return ExportOutcome(False, "timeout", "OTLP export timed out")
    if isinstance(exc, ConnectionError) or "connection" in lowered:
        return ExportOutcome(False, "connection", "OTLP connection error")
    return ExportOutcome(
        False,
        "export_failed",
        f"OTLP export raised {exc_name}",
    )


class ExportStatusTracker:
    """Captures the last HTTP status from the OTLP requests session."""

    def __init__(self) -> None:
        self.last_status_code: int | None = None

    def record_status(self, status_code: int) -> None:
        self.last_status_code = status_code


def _build_tracked_session(tracker: ExportStatusTracker) -> requests.Session:
    """Create a requests session that records status codes without logging URLs."""
    session = requests.Session()
    original_request = session.request

    def tracked_request(method: str, url: str, **kwargs):  # type: ignore[no-untyped-def]
        # Intentionally do not log url — may contain sensitive query parameters.
        response = original_request(method, url, **kwargs)
        tracker.record_status(response.status_code)
        return response

    session.request = tracked_request  # type: ignore[method-assign]
    return session


def create_oneuptime_exporter(
    *,
    timeout: float = DEFAULT_OTLP_TIMEOUT_SECONDS,
    status_tracker: ExportStatusTracker | None = None,
) -> tuple[OTLPSpanExporter, ExportStatusTracker]:
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

    traces_endpoint = validate_otlp_traces_endpoint(endpoint)
    tracker = status_tracker if status_tracker is not None else ExportStatusTracker()

    # Log only that configuration succeeded — never endpoint query strings or token.
    logger.info(
        "Configuring OneUptime OTLP/HTTP exporter (path ends with %s)",
        OTLP_TRACES_SUFFIX,
    )

    exporter = OTLPSpanExporter(
        endpoint=traces_endpoint,
        headers={ONEUPTIME_TOKEN_HEADER: token},
        timeout=timeout,
        session=_build_tracked_session(tracker),
    )
    return exporter, tracker


def create_span_exporter() -> SpanExporter:
    """Create the span exporter for the active ``OTEL_EXPORT_MODE``."""
    global _active_safe_exporter

    mode = get_export_mode()
    if mode == "console":
        _active_safe_exporter = None
        return ConsoleSpanExporter()
    if mode == "oneuptime":
        otlp_exporter, tracker = create_oneuptime_exporter()
        safe = SafeSpanExporter(
            otlp_exporter,
            status_provider=lambda: tracker.last_status_code,
        )
        _active_safe_exporter = safe
        return safe
    raise TelemetryConfigError(
        f"Unsupported OTEL_EXPORT_MODE={mode!r}. Use 'console' or 'oneuptime'."
    )


class SafeSpanExporter(SpanExporter):
    """Delegate exporter that reports success/failure distinctly and safely."""

    def __init__(
        self,
        delegate: SpanExporter,
        *,
        status_provider: Callable[[], int | None] | None = None,
    ) -> None:
        self._delegate = delegate
        self._status_provider = status_provider
        self.last_outcome: ExportOutcome | None = None
        self.success_count = 0
        self.failure_count = 0

    def _record(self, outcome: ExportOutcome) -> None:
        self.last_outcome = outcome
        if outcome.success:
            self.success_count += 1
        else:
            self.failure_count += 1

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._delegate.export(spans)
        except Exception as exc:  # noqa: BLE001 — export must not crash the app
            outcome = classify_exception(exc)
            self._record(outcome)
            logger.error("Span export FAILURE [%s]: %s", outcome.category, outcome.detail)
            return SpanExportResult.FAILURE

        if result == SpanExportResult.SUCCESS:
            status = self._status_provider() if self._status_provider else None
            if status is not None and not (200 <= status < 300):
                # Defensive: never treat a non-2xx HTTP status as success.
                outcome = classify_http_status(status)
                self._record(outcome)
                logger.error(
                    "Span export FAILURE [%s]: %s",
                    outcome.category,
                    outcome.detail,
                )
                return SpanExportResult.FAILURE

            outcome = ExportOutcome(
                True,
                "success",
                "OTLP export accepted by collector",
                status,
            )
            self._record(outcome)
            logger.info("Span export SUCCESS: %s", outcome.detail)
            return SpanExportResult.SUCCESS

        # Delegate returned FAILURE — surface it; do not treat as success.
        status = self._status_provider() if self._status_provider else None
        outcome = (
            classify_http_status(status)
            if status is not None
            else ExportOutcome(
                False,
                "export_failed",
                "OTLP export returned FAILURE from exporter",
            )
        )
        if outcome.success:
            # Status tracker saw 2xx but exporter still failed (should be rare).
            outcome = ExportOutcome(
                False,
                "export_failed",
                "OTLP export returned FAILURE despite HTTP 2xx",
                status,
            )
        self._record(outcome)
        logger.error("Span export FAILURE [%s]: %s", outcome.category, outcome.detail)
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception as exc:  # noqa: BLE001
            outcome = classify_exception(exc)
            logger.error(
                "Span exporter shutdown FAILURE [%s]: %s",
                outcome.category,
                outcome.detail,
            )

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
    global _active_safe_exporter

    load_dotenv()
    service_name = os.getenv("OTEL_SERVICE_NAME", DEFAULT_SERVICE_NAME)

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if exporter is not None:
        span_exporter = exporter
        _active_safe_exporter = exporter if isinstance(exporter, SafeSpanExporter) else None
    else:
        span_exporter = create_span_exporter()

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


def report_export_verification() -> int:
    """Log collector export verification (separate from OneUptime UI checks).

    Returns ``0`` on export success, ``1`` on failure/missing outcome.
    UI visibility must be confirmed manually in OneUptime and is not implied here.
    """
    outcome = get_last_export_outcome()
    if outcome is None:
        logger.error(
            "Export verification: FAILURE — no export outcome recorded "
            "(was a span exported?)"
        )
        return 1

    if outcome.success:
        logger.info("Export verification: SUCCESS — %s", outcome.detail)
        logger.info(
            "UI verification (manual, separate step): confirm the invoke_agent / "
            "DemoAgent span appears in the OneUptime Agent Observability UI"
        )
        return 0

    logger.error(
        "Export verification: FAILURE [%s] — %s",
        outcome.category,
        outcome.detail,
    )
    logger.info(
        "UI verification skipped: collector did not accept the export "
        "(fix visibility cannot succeed until export succeeds)"
    )
    return 1
