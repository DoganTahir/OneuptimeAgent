"""Tests for OpenTelemetry tracing configuration and span production."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.main import create_invoke_agent_span, main
from src.telemetry import (
    DEFAULT_SERVICE_NAME,
    setup_telemetry,
    shutdown_telemetry,
)


def test_setup_telemetry_default_service_name(monkeypatch) -> None:
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    exporter = InMemorySpanExporter()

    provider = setup_telemetry(exporter=exporter)
    try:
        attrs = dict(provider.resource.attributes)
        assert attrs["service.name"] == DEFAULT_SERVICE_NAME
        assert DEFAULT_SERVICE_NAME == "oneuptime-agent-poc"
    finally:
        shutdown_telemetry(provider)


def test_setup_telemetry_reads_otel_service_name(monkeypatch) -> None:
    monkeypatch.setenv("OTEL_SERVICE_NAME", "custom-agent-service")
    exporter = InMemorySpanExporter()

    provider = setup_telemetry(exporter=exporter)
    try:
        attrs = dict(provider.resource.attributes)
        assert attrs["service.name"] == "custom-agent-service"
    finally:
        shutdown_telemetry(provider)


def test_invoke_agent_span_has_gen_ai_attributes() -> None:
    exporter = InMemorySpanExporter()
    provider = setup_telemetry(exporter=exporter)
    try:
        create_invoke_agent_span()
        provider.force_flush()

        spans = exporter.get_finished_spans()
        assert len(spans) == 1

        span = spans[0]
        assert span.name == "invoke_agent"
        assert span.attributes["gen_ai.operation.name"] == "invoke_agent"
        assert span.attributes["gen_ai.agent.name"] == "DemoAgent"
    finally:
        shutdown_telemetry(provider)


def test_main_returns_zero_and_exports_span(monkeypatch) -> None:
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(
        "src.main.setup_telemetry",
        lambda: setup_telemetry(exporter=exporter),
    )

    assert main() == 0

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "invoke_agent"
    assert spans[0].attributes["gen_ai.operation.name"] == "invoke_agent"
    assert spans[0].attributes["gen_ai.agent.name"] == "DemoAgent"
