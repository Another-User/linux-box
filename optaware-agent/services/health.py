"""Health checking for managed services.

:class:`HealthChecker` provides individual probe methods (systemd, docker,
port, command) and a bulk :meth:`~HealthChecker.check_all` that iterates the
whole registry.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
from typing import TYPE_CHECKING

from models.services import ServiceInfo, ServiceStatus

if TYPE_CHECKING:
    from services.registry import ServiceRegistry

logger = logging.getLogger(__name__)

# Timeouts used for external process calls
_SUBPROCESS_TIMEOUT = 10  # seconds
_PORT_TIMEOUT = 5  # seconds


class HealthChecker:
    """Probe services using systemd, docker, port, or command-based checks.

    All methods are pure functions (no state is mutated) so a single instance
    can be shared safely across threads.
    """

    # ------------------------------------------------------------------
    # Individual probe methods
    # ------------------------------------------------------------------

    def check_systemd(self, unit: str) -> ServiceStatus:
        """Query the active state of a systemd *unit*.

        Runs ``systemctl is-active <unit>`` and maps the output to a
        :class:`~models.services.ServiceStatus`.

        Args:
            unit: The systemd unit name, e.g. ``nginx.service``.

        Returns:
            :attr:`ServiceStatus.running` if the unit is active,
            :attr:`ServiceStatus.stopped` if inactive,
            :attr:`ServiceStatus.degraded` if failed/activating/deactivating,
            :attr:`ServiceStatus.unknown` on any other output or error.
        """
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            state = result.stdout.strip().lower()
            logger.debug("systemctl is-active %s → %r", unit, state)
        except FileNotFoundError:
            logger.warning("systemctl not found; cannot check unit %s", unit)
            return ServiceStatus.unknown
        except subprocess.TimeoutExpired:
            logger.warning("Timeout querying systemd unit %s", unit)
            return ServiceStatus.unknown
        except OSError as exc:
            logger.error("OS error querying systemd unit %s: %s", unit, exc)
            return ServiceStatus.unknown

        if state == "active":
            return ServiceStatus.running
        if state in ("inactive", "disabled"):
            return ServiceStatus.stopped
        if state in ("failed", "activating", "deactivating", "reloading"):
            return ServiceStatus.degraded
        return ServiceStatus.unknown

    def check_docker(self, container: str) -> ServiceStatus:
        """Query the status of a Docker *container*.

        Runs ``docker inspect --format '{{.State.Status}}' <container>``
        and maps the Docker state string to a
        :class:`~models.services.ServiceStatus`.

        Args:
            container: Docker container name or ID.

        Returns:
            Appropriate :class:`~models.services.ServiceStatus`.
        """
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    container,
                ],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError:
            logger.warning("docker not found; cannot check container %s", container)
            return ServiceStatus.unknown
        except subprocess.TimeoutExpired:
            logger.warning("Timeout querying docker container %s", container)
            return ServiceStatus.unknown
        except OSError as exc:
            logger.error("OS error querying docker container %s: %s", container, exc)
            return ServiceStatus.unknown

        if result.returncode != 0:
            # Non-zero exit typically means the container does not exist.
            logger.debug(
                "docker inspect returned %d for %s: %s",
                result.returncode,
                container,
                result.stderr.strip(),
            )
            return ServiceStatus.stopped

        try:
            state_obj = json.loads(result.stdout.strip())
            docker_status = state_obj.get("Status", "").lower()
            running = state_obj.get("Running", False)
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.error("Failed to parse docker inspect output for %s: %s", container, exc)
            return ServiceStatus.unknown

        logger.debug("Docker container %s state: %s (running=%s)", container, docker_status, running)

        if running:
            return ServiceStatus.running
        if docker_status in ("exited", "created", "dead"):
            return ServiceStatus.stopped
        if docker_status in ("restarting", "removing"):
            return ServiceStatus.degraded
        if docker_status == "paused":
            return ServiceStatus.stopped
        return ServiceStatus.unknown

    def check_port(self, port: int, host: str = "localhost") -> bool:
        """Attempt a TCP connection to *host*:*port*.

        Args:
            port: TCP port number.
            host: Hostname or IP address (default ``"localhost"``).

        Returns:
            ``True`` if the connection succeeds within the timeout,
            ``False`` otherwise.
        """
        try:
            with socket.create_connection((host, port), timeout=_PORT_TIMEOUT):
                logger.debug("Port check %s:%d → open", host, port)
                return True
        except (OSError, socket.timeout):
            logger.debug("Port check %s:%d → closed/unreachable", host, port)
            return False

    def check_command(self, cmd: str) -> tuple[bool, str]:
        """Execute an arbitrary shell command as a health probe.

        The command is run via the system shell (``/bin/sh -c``).  A zero
        exit code is interpreted as healthy.

        Args:
            cmd: Shell command string to execute.

        Returns:
            A ``(success, message)`` tuple where *success* is ``True`` for
            exit code 0 and *message* contains stdout + stderr combined.
        """
        try:
            result = subprocess.run(
                cmd,
                shell=True,  # noqa: S602
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            msg = f"Health check command timed out after {_SUBPROCESS_TIMEOUT}s: {cmd!r}"
            logger.warning(msg)
            return False, msg
        except OSError as exc:
            msg = f"Failed to execute health check command {cmd!r}: {exc}"
            logger.error(msg)
            return False, msg

        output = (result.stdout + result.stderr).strip()
        success = result.returncode == 0
        logger.debug(
            "Health check command %r → exit=%d output=%r",
            cmd,
            result.returncode,
            output[:200],
        )
        return success, output

    # ------------------------------------------------------------------
    # Composite service check
    # ------------------------------------------------------------------

    def check_service(self, service: ServiceInfo) -> tuple[ServiceStatus, str]:
        """Determine the health of a single service using all available probes.

        Probe priority:
        1. ``health_check_cmd`` — if set, run it; status is ``running`` on
           success or ``degraded`` on failure.
        2. ``systemd_unit`` — query systemd.
        3. ``docker_container`` — query docker.
        4. ``port`` — TCP port check; ``running`` if open, ``unknown`` if not.
        5. If no probe is configured, return ``(unknown, "no probe configured")``.

        Args:
            service: The service to probe.

        Returns:
            A ``(status, message)`` tuple.
        """
        # 1. Command-based health check (highest specificity)
        if service.health_check_cmd:
            ok, output = self.check_command(service.health_check_cmd)
            if ok:
                return ServiceStatus.running, f"Health command succeeded: {output[:200]}"
            return ServiceStatus.degraded, f"Health command failed: {output[:200]}"

        # 2. systemd unit check
        if service.systemd_unit:
            status = self.check_systemd(service.systemd_unit)
            msg = f"systemd unit {service.systemd_unit!r} is {status.value}"
            return status, msg

        # 3. Docker container check
        if service.docker_container:
            status = self.check_docker(service.docker_container)
            msg = f"Docker container {service.docker_container!r} is {status.value}"
            return status, msg

        # 4. TCP port check
        if service.port is not None:
            open_ = self.check_port(service.port)
            if open_:
                return ServiceStatus.running, f"Port {service.port} is open"
            return ServiceStatus.unknown, f"Port {service.port} is not reachable"

        return ServiceStatus.unknown, "No health probe configured for this service"

    # ------------------------------------------------------------------
    # Bulk check
    # ------------------------------------------------------------------

    def check_all(
        self,
        registry: "ServiceRegistry",
    ) -> dict[str, tuple[ServiceStatus, str]]:
        """Run :meth:`check_service` for every service in *registry*.

        This is a synchronous, sequential operation.  For large registries
        consider running concurrently with ``asyncio`` or ``ThreadPoolExecutor``
        in the caller.

        Args:
            registry: Registry whose services should all be probed.

        Returns:
            Mapping of service name to ``(status, message)`` tuples.
        """
        results: dict[str, tuple[ServiceStatus, str]] = {}
        for svc in registry.get_all():
            try:
                status, message = self.check_service(svc)
            except Exception as exc:  # noqa: BLE001
                logger.error("Unexpected error checking service %s: %s", svc.name, exc)
                status, message = ServiceStatus.unknown, f"Check raised exception: {exc}"
            results[svc.name] = (status, message)
            logger.debug("Health check %s → %s: %s", svc.name, status.value, message[:120])
        return results
