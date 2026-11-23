"""Tests for OneUptime OTLP/HTTP exporter configuration (no live network)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from src.telemetry import (
    ONEUPTIME_TOKEN_HEADER,
    OTLP_TRACES_SUFFIX,
    ExportOutcome,
    SafeSpanExporter,
    TelemetryConfigError,
    build_otlp_traces_endpoint,
    classify_exception,
    classify_http_status,
    create_oneuptime_exporter,
    create_span_exporter,
    get_export_mode,
    get_last_export_outcome,
    report_export_verification,
    setup_telemetry,
    shutdown_telemetry,
    validate_otlp_traces_endpoint,
)


def test_build_otlp_traces_endpoint_appends_v1_traces() -> None:
    assert (
        build_otlp_traces_endpoint("https://example.com/otlp")
        == "https://example.com/otlp/v1/traces"
    )
    assert (
        build_otlp_traces_endpoint("https://example.com/otlp/")
        == "https://example.com/otlp/v1/traces"
    )


def test_build_otlp_traces_endpoint_idempotent() -> None:
    full = "https://example.com/otlp/v1/traces"
    assert build_otlp_traces_endpoint(full) == full


def test_validate_otlp_traces_endpoint_requires_otlp_v1_traces() -> None:
    assert (
        validate_otlp_traces_endpoint("https://example.com/otlp")
        == f"https://example.com{OTLP_TRACES_SUFFIX}"
    )


def test_validate_otlp_traces_endpoint_rejects_missing_otlp_segment() -> None:
    with pytest.raises(TelemetryConfigError, match=r"/otlp"):
        validate_otlp_traces_endpoint("https://example.com")


def test_validate_otlp_traces_endpoint_rejects_doubled_traces_path() -> None:
    with pytest.raises(TelemetryConfigError, match="exactly once"):
        validate_otlp_traces_endpoint("https://example.com/otlp/v1/traces/v1/traces")


def test_default_export_mode_is_console(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORT_MODE", raising=False)
    assert get_export_mode() == "console"


def test_oneuptime_mode_missing_endpoint_raises(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORT_MODE", "oneuptime")
    monkeypatch.delenv("ONEUPTIME_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("ONEUPTIME_INGESTION_TOKEN", "dummy-token")

    with pytest.raises(TelemetryConfigError, match="ONEUPTIME_OTLP_ENDPOINT") as exc_info:
        create_oneuptime_exporter()

    assert "dummy-token" not in str(exc_info.value)


def test_oneuptime_mode_missing_token_raises(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORT_MODE", "oneuptime")
    monkeypatch.setenv("ONEUPTIME_OTLP_ENDPOINT", "https://example.com/otlp")
    monkeypatch.delenv("ONEUPTIME_INGESTION_TOKEN", raising=False)

    with pytest.raises(TelemetryConfigError, match="ONEUPTIME_INGESTION_TOKEN"):
        create_oneuptime_exporter()


def test_create_oneuptime_exporter_config_mocked(monkeypatch) -> None:
    monkeypatch.setenv("ONEUPTIME_OTLP_ENDPOINT", "https://example.com/otlp")
    monkeypatch.setenv("ONEUPTIME_INGESTION_TOKEN", "test-ingestion-token")

    captured: dict = {}

    def fake_otlp(**kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return MagicMock(name="OTLPSpanExporter")

    monkeypatch.setattr("src.telemetry.OTLPSpanExporter", fake_otlp)

    exporter, tracker = create_oneuptime_exporter(timeout=5.0)
    assert exporter is not None
    assert tracker.last_status_code is None

    assert captured["endpoint"] == "https://example.com/otlp/v1/traces"
    assert captured["endpoint"].endswith(OTLP_TRACES_SUFFIX)
    assert captured["endpoint"].count("/v1/traces") == 1
    assert ONEUPTIME_TOKEN_HEADER in captured["headers"]
    assert captured["headers"][ONEUPTIME_TOKEN_HEADER] == "test-ingestion-token"
    assert captured["timeout"] == 5.0
    assert "certificate_file" not in captured
    assert "session" in captured


def test_create_span_exporter_oneuptime_wraps_safe(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORT_MODE", "oneuptime")
    monkeypatch.setenv("ONEUPTIME_OTLP_ENDPOINT", "https://example.com/otlp")
    monkeypatch.setenv("ONEUPTIME_INGESTION_TOKEN", "test-ingestion-token")

    monkeypatch.setattr(
        "src.telemetry.OTLPSpanExporter",
        lambda **kwargs: MagicMock(name="OTLPSpanExporter"),
    )

    exporter = create_span_exporter()
    assert isinstance(exporter, SafeSpanExporter)


def test_safe_span_exporter_reports_exception_as_failure() -> None:
    delegate = MagicMock()
    delegate.export.side_effect = TimeoutError("connection timed out to https://x?token=secret")

    wrapper = SafeSpanExporter(delegate)
    result = wrapper.export([])

    assert result == SpanExportResult.FAILURE
    assert wrapper.last_outcome is not None
    assert wrapper.last_outcome.success is False
    assert wrapper.last_outcome.category == "timeout"
    assert "secret" not in wrapper.last_outcome.detail
    assert wrapper.failure_count == 1
    assert wrapper.success_count == 0


def test_safe_span_exporter_reports_delegate_failure() -> None:
    delegate = MagicMock()
    delegate.export.return_value = SpanExportResult.FAILURE

    wrapper = SafeSpanExporter(delegate, status_provider=lambda: 401)
    result = wrapper.export([])

    assert result == SpanExportResult.FAILURE
    assert wrapper.last_outcome == classify_http_status(401)
    assert wrapper.last_outcome.success is False


def test_safe_span_exporter_does_not_treat_non_2xx_as_success() -> None:
    delegate = MagicMock()
    delegate.export.return_value = SpanExportResult.SUCCESS

    wrapper = SafeSpanExporter(delegate, status_provider=lambda: 403)
    result = wrapper.export([])

    assert result == SpanExportResult.FAILURE
    assert wrapper.last_outcome is not None
    assert wrapper.last_outcome.success is False
    assert wrapper.last_outcome.category == "http_403"


def test_safe_span_exporter_records_success() -> None:
    delegate = MagicMock()
    delegate.export.return_value = SpanExportResult.SUCCESS

    wrapper = SafeSpanExporter(delegate, status_provider=lambda: 200)
    result = wrapper.export([])

    assert result == SpanExportResult.SUCCESS
    assert wrapper.last_outcome is not None
    assert wrapper.last_outcome.success is True
    assert wrapper.success_count == 1


def test_safe_span_exporter_swallows_shutdown_errors() -> None:
    delegate = MagicMock()
    delegate.shutdown.side_effect = ConnectionError("connection reset")

    SafeSpanExporter(delegate).shutdown()
    delegate.shutdown.assert_called_once()


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, "http_401"),
        (403, "http_403"),
        (404, "http_404"),
        (429, "http_429"),
        (500, "http_5xx"),
        (503, "http_5xx"),
    ],
)
def test_classify_http_status_categories(status: int, category: str) -> None:
    outcome = classify_http_status(status)
    assert outcome.success is False
    assert outcome.category == category
    assert outcome.status_code == status


def test_classify_exception_never_embeds_exception_text() -> None:
    exc = ConnectionError("Failed to connect to https://host/otlp?token=super-secret")
    outcome = classify_exception(exc)
    assert outcome.success is False
    assert outcome.category == "connection"
    assert "super-secret" not in outcome.detail
    assert "https://" not in outcome.detail


def test_report_export_verification_success(monkeypatch, caplog) -> None:
    import logging

    import src.telemetry as telemetry

    safe = SafeSpanExporter(MagicMock())
    safe.last_outcome = ExportOutcome(True, "success", "OTLP export accepted by collector", 200)
    monkeypatch.setattr(telemetry, "_active_safe_exporter", safe)

    with caplog.at_level(logging.INFO):
        assert report_export_verification() == 0

    text = "\n".join(r.message for r in caplog.records)
    assert "Export verification: SUCCESS" in text
    assert "UI verification (manual" in text
    assert "Authorization" not in text
    assert "x-oneuptime-token" not in text
    assert "test-ingestion-token" not in text


def test_report_export_verification_failure(monkeypatch, caplog) -> None:
    import logging

    import src.telemetry as telemetry

    safe = SafeSpanExporter(MagicMock())
    safe.last_outcome = ExportOutcome(False, "http_401", "OTLP export unauthorized (HTTP 401)", 401)
    monkeypatch.setattr(telemetry, "_active_safe_exporter", safe)

    with caplog.at_level(logging.INFO):
        assert report_export_verification() == 1

    text = "\n".join(r.message for r in caplog.records)
    assert "Export verification: FAILURE" in text
    assert get_last_export_outcome() is not None
    assert get_last_export_outcome().success is False


def test_setup_telemetry_console_mode_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_EXPORT_MODE", "console")
    monkeypatch.delenv("ONEUPTIME_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("ONEUPTIME_INGESTION_TOKEN", raising=False)

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    provider = setup_telemetry(exporter=exporter)
    try:
        assert dict(provider.resource.attributes)["service.name"]
    finally:
        shutdown_telemetry(provider)
