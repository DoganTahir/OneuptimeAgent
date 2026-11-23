"""Tests for OneUptime OTLP/HTTP exporter configuration (no live network)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from src.telemetry import (
    ONEUPTIME_TOKEN_HEADER,
    SafeSpanExporter,
    TelemetryConfigError,
    build_otlp_traces_endpoint,
    create_oneuptime_exporter,
    create_span_exporter,
    get_export_mode,
    setup_telemetry,
    shutdown_telemetry,
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

    create_oneuptime_exporter(timeout=5.0)

    assert captured["endpoint"] == "https://example.com/otlp/v1/traces"
    assert ONEUPTIME_TOKEN_HEADER in captured["headers"]
    assert captured["headers"][ONEUPTIME_TOKEN_HEADER] == "test-ingestion-token"
    assert captured["timeout"] == 5.0
    assert "certificate_file" not in captured


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


def test_safe_span_exporter_swallows_export_errors() -> None:
    delegate = MagicMock()
    delegate.export.side_effect = TimeoutError("connection timed out")

    wrapper = SafeSpanExporter(delegate)
    result = wrapper.export([])

    assert result == SpanExportResult.FAILURE
    delegate.export.assert_called_once()


def test_safe_span_exporter_swallows_shutdown_errors() -> None:
    delegate = MagicMock()
    delegate.shutdown.side_effect = ConnectionError("connection reset")

    SafeSpanExporter(delegate).shutdown()
    delegate.shutdown.assert_called_once()


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
