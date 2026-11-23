"""Smoke tests for the application entry point."""

from src.main import main


def test_main_returns_zero() -> None:
    assert main() == 0
