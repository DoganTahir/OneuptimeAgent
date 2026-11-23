"""Application entry point for the OneUptime AI Agent Observability POC."""

from __future__ import annotations

import logging
import sys

from src.telemetry import (
    TelemetryConfigError,
    get_export_mode,
    get_tracer,
    report_export_verification,
    setup_telemetry,
    shutdown_telemetry,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("oneuptime-ai-agent-poc")


def create_invoke_agent_span() -> None:
    """Create a demo ``invoke_agent`` span with GenAI semantic attributes."""
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("invoke_agent") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.agent.name", "DemoAgent")


def main() -> int:
    """Start the POC application and emit a demo agent span."""
    logger.info("OneUptime AI Agent Observability POC started")
    logger.info("Infrastructure ready — agent, LLM, and OTEL wiring come next")

    try:
        provider = setup_telemetry()
    except TelemetryConfigError as exc:
        logger.error("Telemetry configuration error: %s", exc)
        return 1

    try:
        create_invoke_agent_span()
    finally:
        shutdown_telemetry(provider)

    # Real OTLP result is only meaningful for oneuptime mode during manual runs.
    if get_export_mode() == "oneuptime":
        return report_export_verification()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
