"""Entry point for the Topology Aware Routing monitor.

Continuously verifies whether Topology Aware Routing is active for a Service
and emits one structured JSON record per check. Runs forever, sleeping
``CHECK_INTERVAL`` seconds between checks, until it receives SIGTERM/SIGINT.
"""

from __future__ import annotations

import threading
import time
from typing import NoReturn

from kubernetes.client import CoreV1Api, DiscoveryV1Api

from config import AppConfig, ConfigurationError, load_config
from kubernetes_client import KubernetesConfigError, load_api_clients
from logger import setup_logging
from tar_checker import check_tar

# Fallbacks for the startup-failure path where config is not yet available.
_UNKNOWN = "unknown"

# Static fallback used only if pyfiglet is not installed (e.g. a slim image that
# omitted the cosmetic dependency). The app runs fine without pyfiglet.
TARS_BANNER_FALLBACK = "TARS - Topology Aware Routing Service"

# Figlet font used for the startup banner. ``standard`` matches the look of the
# original hand-drawn banner; change to e.g. "slant", "small", or "big" for a
# different style (list available fonts with: python -m pyfiglet -l).
TARS_BANNER_FONT = "standard"


def _render_banner() -> str:
    """Render the TARS banner with pyfiglet, gracefully degrading to text."""
    try:
        import pyfiglet  # imported lazily so a missing dep never blocks startup
    except ImportError:
        return TARS_BANNER_FALLBACK
    return pyfiglet.figlet_format("TARS", font=TARS_BANNER_FONT).rstrip()


def print_banner() -> None:
    """Print the project name when the monitor starts."""
    print(_render_banner(), flush=True)


def _log_startup_error(message: str, error: str) -> None:
    """Emit a structured INFO record for failures before config is available."""
    logger = setup_logging()
    logger.info(
        message,
        extra={
            "namespace": _UNKNOWN,
            "service": _UNKNOWN,
            "error": error,
            "check_duration_ms": 0,
        },
    )


def run_check(
    core_v1: CoreV1Api,
    discovery_v1: DiscoveryV1Api,
    config: AppConfig,
    logger,
) -> None:
    """Execute one check pass and log the result (or the failure)."""
    start = time.monotonic()
    try:
        result = check_tar(core_v1, discovery_v1, config.namespace, config.service_name)
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "Topology Aware Routing check completed",
            extra={
                "namespace": config.namespace,
                "service": config.service_name,
                "tar_enabled": result.tar_enabled,
                "annotation_present": result.annotation_present,
                "topology_mode_present": result.topology_mode_present,
                "topology_aware_hints_present": result.topology_aware_hints_present,
                "endpoint_slices": result.endpoint_slices,
                "total_endpoints": result.total_endpoints,
                "ready_endpoints": result.ready_endpoints,
                "hinted_endpoints": result.hinted_endpoints,
                "check_duration_ms": duration_ms,
                "status": result.status,
                "disabled_reason": result.disabled_reason,
            },
        )
    except Exception as exc:  # noqa: BLE001 - log and keep monitoring
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "Topology Aware Routing check failed",
            extra={
                "namespace": config.namespace,
                "service": config.service_name,
                "error": str(exc),
                "check_duration_ms": duration_ms,
            },
        )


def run_forever(config: AppConfig, logger) -> NoReturn:
    """Main loop: check, sleep, repeat, until asked to stop."""
    core_v1, discovery_v1 = load_api_clients()

    stop_event = threading.Event()

    def _request_stop(signum, _frame):  # noqa: ANN001 - signal callback
        stop_event.set()

    import signal

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    logger.info(
        "Starting Topology Aware Routing monitor",
        extra={
            "namespace": config.namespace,
            "service": config.service_name,
            "check_interval": config.check_interval,
        },
    )

    while not stop_event.is_set():
        run_check(core_v1, discovery_v1, config, logger)
        # Interruptible sleep: SIGTERM/SIGINT wake us immediately.
        stop_event.wait(config.check_interval)

    logger.info(
        "Topology Aware Routing monitor stopped",
        extra={
            "namespace": config.namespace,
            "service": config.service_name,
        },
    )


def main() -> None:
    """Wire configuration, logging and the main loop together."""
    print_banner()

    try:
        config = load_config()
    except ConfigurationError as exc:
        _log_startup_error("Configuration error, cannot start", str(exc))
        raise SystemExit(1)

    logger = setup_logging()

    try:
        run_forever(config, logger)
    except KubernetesConfigError as exc:
        logger.info(
            "Kubernetes configuration error, cannot start",
            extra={
                "namespace": config.namespace,
                "service": config.service_name,
                "error": str(exc),
                "check_duration_ms": 0,
            },
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
