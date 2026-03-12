"""OptAware main daemon — orchestrates all subsystems."""

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import NoReturn

from config import OptAwareConfig, load_config
from event_processor import EventProcessor
from logging_setup import new_correlation_id, setup_logging
from scheduler import Scheduler
from services.registry import ServiceRegistry, get_registry

logger = logging.getLogger("optaware.daemon")

# Default interval (seconds) between scheduler ticks.
_SCHEDULER_TICK_INTERVAL: float = 1.0

# Timeout (seconds) to wait for the event-processor task to finish on shutdown.
_SHUTDOWN_TIMEOUT: float = 10.0


class OptAwareDaemon:
    """
    Top-level daemon that owns and coordinates all OptAware subsystems.

    Lifecycle::

        daemon = OptAwareDaemon(config_path="/etc/optaware/optaware.yaml")
        daemon.start()   # blocks until SIGTERM / SIGINT
    """

    def __init__(self, config_path: str = "/etc/optaware/optaware.yaml") -> None:
        self._config_path: str = config_path
        self._config: OptAwareConfig = load_config(config_path)
        self._running: bool = False
        self._reload_requested: bool = False

        # Boot logging as early as possible so subsystem init is observable.
        setup_logging(
            level=self._config.general.log_level,
            log_file=os.path.join(self._config.general.data_dir, "logs", "optaware.log")
            if os.path.isdir(os.path.join(self._config.general.data_dir, "logs"))
            else None,
        )

        logger.info(
            "OptAware daemon initialising",
            extra={"config_path": config_path, "environment": self._config.general.environment},
        )

        # Subsystems
        self._registry: ServiceRegistry = get_registry()
        self._scheduler: Scheduler = Scheduler()
        self._event_processor: EventProcessor = EventProcessor()

        # asyncio task handle for the event-processor loop.
        self._processor_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Public lifecycle methods
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Main entry point.  Runs the asyncio event loop until a stop signal is
        received or :meth:`stop` is called.
        """
        new_correlation_id()
        logger.info("Starting OptAware daemon (pid=%d)", os.getpid())
        try:
            asyncio.run(self._async_start())
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt — shutting down.")
        logger.info("OptAware daemon exited cleanly.")

    def stop(self) -> None:
        """
        Request a graceful shutdown.

        This may be called from a signal handler or from another coroutine.
        The main loop will stop after the current scheduler tick and any
        in-flight event dispatches complete (up to :data:`_SHUTDOWN_TIMEOUT`).
        """
        logger.info("Stop requested.")
        self._running = False
        self._event_processor.stop()

    def reload_config(self) -> None:
        """
        Reload the YAML configuration file in response to SIGHUP.

        The new configuration is applied to the subsystems that support live
        reload.  Subsystems that require a full restart are logged as warnings.
        """
        logger.info("Reloading configuration from '%s'", self._config_path)
        try:
            new_cfg = load_config(self._config_path)
        except Exception:
            logger.exception("Failed to reload config — keeping existing configuration.")
            return

        old_log_level = self._config.general.log_level
        self._config = new_cfg

        if new_cfg.general.log_level != old_log_level:
            setup_logging(level=new_cfg.general.log_level)
            logger.info("Log level changed to '%s'", new_cfg.general.log_level)

        logger.info("Configuration reloaded successfully.")
        self._reload_requested = False

    # ------------------------------------------------------------------
    # Async internals
    # ------------------------------------------------------------------

    async def _async_start(self) -> None:
        """Set up signal handlers and run :meth:`_main_loop`."""
        self._setup_signal_handlers()
        self._running = True

        # Start the event-processor as a background task.
        self._processor_task = asyncio.create_task(
            self._event_processor.process_loop(),
            name="event-processor",
        )

        try:
            await self._main_loop()
        finally:
            await self._async_stop()

    async def _main_loop(self) -> None:
        """
        Async main loop: tick the scheduler and check for reload requests on
        every iteration until :attr:`_running` is set to *False*.
        """
        logger.info("Main loop running.")
        while self._running:
            # Handle config-reload requests raised by SIGHUP.
            if self._reload_requested:
                self.reload_config()

            await self._scheduler.tick()
            await asyncio.sleep(_SCHEDULER_TICK_INTERVAL)

        logger.info("Main loop exiting.")

    async def _async_stop(self) -> None:
        """Gracefully tear down subsystems with a timeout."""
        logger.info("Shutting down subsystems (timeout=%ss).", _SHUTDOWN_TIMEOUT)

        if self._processor_task is not None and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._processor_task),
                    timeout=_SHUTDOWN_TIMEOUT,
                )
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        logger.info("All subsystems stopped.")

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        """Register POSIX signal handlers on the running event loop."""
        loop = asyncio.get_running_loop()

        def _handle_sigterm() -> None:
            logger.info("Received SIGTERM.")
            self.stop()

        def _handle_sigint() -> None:
            logger.info("Received SIGINT.")
            self.stop()

        def _handle_sighup() -> None:
            logger.info("Received SIGHUP — scheduling config reload.")
            self._reload_requested = True

        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
        loop.add_signal_handler(signal.SIGINT, _handle_sigint)
        loop.add_signal_handler(signal.SIGHUP, _handle_sighup)
        logger.debug("Signal handlers registered (SIGTERM, SIGINT, SIGHUP).")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="optaware",
        description="OptAware — AI-powered Linux server management agent",
    )
    parser.add_argument(
        "--config",
        default="/etc/optaware/optaware.yaml",
        metavar="PATH",
        help="Path to the YAML configuration file (default: /etc/optaware/optaware.yaml).",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        default=False,
        help="Run in the foreground (do not daemonise).  Implied when stdout is a TTY.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        metavar="LEVEL",
        help="Override the log level from config (DEBUG|INFO|WARNING|ERROR|CRITICAL).",
    )
    return parser


def main() -> NoReturn:
    """CLI entry point installed as the ``optaware`` script."""
    parser = _build_argument_parser()
    args = parser.parse_args()

    # Apply --log-level override before the daemon initialises its own logging
    # so that early startup messages respect the requested level.
    if args.log_level:
        setup_logging(level=args.log_level)

    daemon = OptAwareDaemon(config_path=args.config)

    # When --log-level was provided it takes precedence over the config value.
    if args.log_level:
        setup_logging(level=args.log_level)

    daemon.start()
    sys.exit(0)


if __name__ == "__main__":
    main()
