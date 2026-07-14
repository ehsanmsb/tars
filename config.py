"""Application configuration sourced from environment variables.

All runtime knobs are read from the environment so the same image can be
reused across namespaces/services without rebuilding.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_CHECK_INTERVAL_SECONDS = 300


class ConfigurationError(ValueError):
    """Raised when the application is misconfigured."""


@dataclass(frozen=True)
class AppConfig:
    """Immutable, validated application configuration."""

    namespace: str
    service_name: str
    check_interval: int


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigurationError(
            f"Required environment variable {name} is not set or is empty"
        )
    return value


def _parse_check_interval(raw: str) -> int:
    try:
        interval = int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            f"CHECK_INTERVAL must be an integer number of seconds, got {raw!r}"
        ) from exc
    if interval <= 0:
        raise ConfigurationError(
            f"CHECK_INTERVAL must be a positive integer, got {interval}"
        )
    return interval


def load_config() -> AppConfig:
    """Read and validate configuration from the environment."""
    return AppConfig(
        namespace=_required("NAMESPACE"),
        service_name=_required("SERVICE_NAME"),
        check_interval=_parse_check_interval(
            os.environ.get("CHECK_INTERVAL", str(DEFAULT_CHECK_INTERVAL_SECONDS))
        ),
    )
