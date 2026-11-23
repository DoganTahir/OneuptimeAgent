"""Shared pytest fixtures for OpenTelemetry isolation between tests."""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.util._once import Once


@pytest.fixture(autouse=True)
def reset_tracer_provider() -> None:
    """Reset the global TracerProvider so each test can configure its own."""
    trace._TRACER_PROVIDER = None  # noqa: SLF001
    trace._TRACER_PROVIDER_SET_ONCE = Once()  # noqa: SLF001
    yield
    provider = trace.get_tracer_provider()
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()
    trace._TRACER_PROVIDER = None  # noqa: SLF001
    trace._TRACER_PROVIDER_SET_ONCE = Once()  # noqa: SLF001
