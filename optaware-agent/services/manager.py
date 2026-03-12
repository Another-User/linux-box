"""High-level service management — start, stop, restart, enable, disable.

:class:`ServiceManager` wraps the low-level systemctl/docker calls and
orchestrates dependency ordering via :class:`~services.dependency.DependencyResolver`
and live status via :class:`~services.health.HealthChecker`.

All mutating operations default to ``dry_run=True`` so no changes are made
without explicit opt-in — in line with the OptAware *safe-by-default* principle.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field

from models.services import ServiceInfo, ServiceStatus, ServiceType

from services.dependency import DependencyResolver
from services.health import HealthChecker
from services.registry import ServiceRegistry

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 30  # seconds for systemctl operations


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ActionResult:
    """Outcome of a service management operation.

    Attributes:
        success: ``True`` if the operation completed (or would complete in
            dry-run mode) without error.
        message: Human-readable summary of the outcome.
        details: Arbitrary key/value metadata (e.g. dry_run flag, command
            that was run, stdout/stderr).
    """

    success: bool
    message: str
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ServiceManager
# ---------------------------------------------------------------------------


class ServiceManager:
    """Orchestrate lifecycle operations for managed services.

    Args:
        registry: The service registry to look up service definitions.
        resolver: Dependency resolver used to compute start/stop ordering.
        health_checker: Health checker used to verify post-action state.
    """

    def __init__(
        self,
        registry: ServiceRegistry,
        resolver: DependencyResolver,
        health_checker: HealthChecker,
    ) -> None:
        self._registry = registry
        self._resolver = resolver
        self._health = health_checker

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_error(self, name: str) -> ServiceInfo | ActionResult:
        """Return the ServiceInfo for *name* or an error ActionResult."""
        svc = self._registry.get(name)
        if svc is None:
            return ActionResult(
                success=False,
                message=f"Service {name!r} not found in registry",
                details={"service": name},
            )
        return svc

    def _run_systemctl(
        self,
        verb: str,
        unit: str,
        dry_run: bool,
    ) -> ActionResult:
        """Run ``systemctl <verb> <unit>`` and return an :class:`ActionResult`.

        Args:
            verb: systemctl verb, e.g. ``start``, ``stop``, ``restart``.
            unit: systemd unit name.
            dry_run: When ``True`` the command is logged but not executed.

        Returns:
            :class:`ActionResult` indicating success or failure.
        """
        cmd = ["systemctl", verb, unit]
        if dry_run:
            logger.info("[DRY-RUN] Would run: %s", " ".join(cmd))
            return ActionResult(
                success=True,
                message=f"[DRY-RUN] Would run: {' '.join(cmd)}",
                details={"dry_run": True, "command": cmd},
            )

        logger.info("Running: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError:
            return ActionResult(
                success=False,
                message="systemctl not found on this system",
                details={"command": cmd},
            )
        except subprocess.TimeoutExpired:
            return ActionResult(
                success=False,
                message=f"systemctl {verb} {unit} timed out after {_SUBPROCESS_TIMEOUT}s",
                details={"command": cmd, "timeout": _SUBPROCESS_TIMEOUT},
            )
        except OSError as exc:
            return ActionResult(
                success=False,
                message=f"OS error running systemctl: {exc}",
                details={"command": cmd, "error": str(exc)},
            )

        success = result.returncode == 0
        return ActionResult(
            success=success,
            message=(
                f"systemctl {verb} {unit} "
                + ("succeeded" if success else f"failed (exit {result.returncode})")
            ),
            details={
                "command": cmd,
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "dry_run": False,
            },
        )

    def _run_docker(
        self,
        verb: str,
        container: str,
        dry_run: bool,
    ) -> ActionResult:
        """Run ``docker <verb> <container>`` and return an :class:`ActionResult`.

        Args:
            verb: docker sub-command, e.g. ``start``, ``stop``, ``restart``.
            container: Docker container name or ID.
            dry_run: When ``True`` the command is logged but not executed.

        Returns:
            :class:`ActionResult` indicating success or failure.
        """
        cmd = ["docker", verb, container]
        if dry_run:
            logger.info("[DRY-RUN] Would run: %s", " ".join(cmd))
            return ActionResult(
                success=True,
                message=f"[DRY-RUN] Would run: {' '.join(cmd)}",
                details={"dry_run": True, "command": cmd},
            )

        logger.info("Running: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError:
            return ActionResult(
                success=False,
                message="docker not found on this system",
                details={"command": cmd},
            )
        except subprocess.TimeoutExpired:
            return ActionResult(
                success=False,
                message=f"docker {verb} {container} timed out after {_SUBPROCESS_TIMEOUT}s",
                details={"command": cmd, "timeout": _SUBPROCESS_TIMEOUT},
            )
        except OSError as exc:
            return ActionResult(
                success=False,
                message=f"OS error running docker: {exc}",
                details={"command": cmd, "error": str(exc)},
            )

        success = result.returncode == 0
        return ActionResult(
            success=success,
            message=(
                f"docker {verb} {container} "
                + ("succeeded" if success else f"failed (exit {result.returncode})")
            ),
            details={
                "command": cmd,
                "returncode": result.returncode,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "dry_run": False,
            },
        )

    def _lifecycle_action(
        self,
        name: str,
        verb: str,
        dry_run: bool,
    ) -> ActionResult:
        """Dispatch a lifecycle verb (start/stop/restart) to the right backend.

        Determines whether the service is managed by systemd or Docker and
        calls the corresponding helper.

        Args:
            name: Service name in the registry.
            verb: Lifecycle verb: ``start``, ``stop``, or ``restart``.
            dry_run: Whether to perform the operation or just log it.

        Returns:
            :class:`ActionResult` from the underlying operation.
        """
        result = self._get_or_error(name)
        if isinstance(result, ActionResult):
            return result
        svc: ServiceInfo = result

        if svc.systemd_unit:
            ar = self._run_systemctl(verb, svc.systemd_unit, dry_run)
        elif svc.docker_container:
            ar = self._run_docker(verb, svc.docker_container, dry_run)
        else:
            return ActionResult(
                success=False,
                message=(
                    f"Service {name!r} has neither a systemd_unit nor a "
                    "docker_container; cannot perform lifecycle actions"
                ),
                details={"service": name},
            )

        # Update registry status if not dry-run
        if ar.success and not dry_run:
            new_status_map = {
                "start": ServiceStatus.running,
                "stop": ServiceStatus.stopped,
                "restart": ServiceStatus.running,
            }
            new_status = new_status_map.get(verb, ServiceStatus.unknown)
            try:
                self._registry.update_status(name, new_status)
            except KeyError:
                pass  # already handled above

        return ar

    # ------------------------------------------------------------------
    # Public lifecycle API
    # ------------------------------------------------------------------

    def start_service(self, name: str, dry_run: bool = True) -> ActionResult:
        """Start the named service, honouring its dependency order.

        Dependencies are resolved and started first (in topological order).
        Each dependency start is also subject to *dry_run*.

        Args:
            name: Service name as registered in the registry.
            dry_run: When ``True`` (default), no system changes are made.

        Returns:
            :class:`ActionResult` for the top-level service start.  Dependency
            results are included in ``details["dependency_results"]``.
        """
        svc = self._get_or_error(name)
        if isinstance(svc, ActionResult):
            return svc

        # Resolve and start dependencies first
        try:
            order = self._resolver.resolve(name, self._registry)
        except (KeyError, ValueError) as exc:
            return ActionResult(
                success=False,
                message=f"Dependency resolution failed for {name!r}: {exc}",
                details={"service": name, "error": str(exc)},
            )

        dep_results: dict[str, dict] = {}
        for dep_name in order[:-1]:  # all but the target itself
            dep_svc = self._registry.get(dep_name)
            if dep_svc is None:
                continue
            dep_ar = self._lifecycle_action(dep_name, "start", dry_run)
            dep_results[dep_name] = {
                "success": dep_ar.success,
                "message": dep_ar.message,
            }
            if not dep_ar.success:
                logger.warning("Failed to start dependency %s: %s", dep_name, dep_ar.message)

        # Start the target service
        ar = self._lifecycle_action(name, "start", dry_run)
        ar.details["dependency_results"] = dep_results
        return ar

    def stop_service(self, name: str, dry_run: bool = True) -> ActionResult:
        """Stop the named service.

        Args:
            name: Service name as registered in the registry.
            dry_run: When ``True`` (default), no system changes are made.

        Returns:
            :class:`ActionResult` describing the outcome.
        """
        return self._lifecycle_action(name, "stop", dry_run)

    def restart_service(self, name: str, dry_run: bool = True) -> ActionResult:
        """Restart the named service.

        Args:
            name: Service name as registered in the registry.
            dry_run: When ``True`` (default), no system changes are made.

        Returns:
            :class:`ActionResult` describing the outcome.
        """
        return self._lifecycle_action(name, "restart", dry_run)

    def enable_service(self, name: str) -> ActionResult:
        """Enable the named service so it starts on boot (systemd only).

        This operation is never dry-run gated because it only modifies symlinks
        in ``/etc/systemd/system/``, not the running system state.  Docker-
        managed services are not supported by this method.

        Args:
            name: Service name as registered in the registry.

        Returns:
            :class:`ActionResult` describing the outcome.
        """
        result = self._get_or_error(name)
        if isinstance(result, ActionResult):
            return result
        svc: ServiceInfo = result

        if svc.systemd_unit:
            return self._run_systemctl("enable", svc.systemd_unit, dry_run=False)
        if svc.docker_container:
            return ActionResult(
                success=False,
                message=(
                    f"Service {name!r} is Docker-managed; "
                    "use restart policies in docker-compose to control autostart"
                ),
                details={"service": name},
            )
        return ActionResult(
            success=False,
            message=f"Service {name!r} has no systemd unit to enable",
            details={"service": name},
        )

    def disable_service(self, name: str) -> ActionResult:
        """Disable the named service so it does not start on boot (systemd only).

        Args:
            name: Service name as registered in the registry.

        Returns:
            :class:`ActionResult` describing the outcome.
        """
        result = self._get_or_error(name)
        if isinstance(result, ActionResult):
            return result
        svc: ServiceInfo = result

        if svc.systemd_unit:
            return self._run_systemctl("disable", svc.systemd_unit, dry_run=False)
        if svc.docker_container:
            return ActionResult(
                success=False,
                message=(
                    f"Service {name!r} is Docker-managed; "
                    "update restart policy in docker-compose to disable autostart"
                ),
                details={"service": name},
            )
        return ActionResult(
            success=False,
            message=f"Service {name!r} has no systemd unit to disable",
            details={"service": name},
        )

    def get_service_status(self, name: str) -> ServiceInfo:
        """Return a :class:`~models.services.ServiceInfo` with a fresh status.

        Runs a live health check, updates the registry, and returns the
        updated :class:`~models.services.ServiceInfo`.

        Args:
            name: Service name as registered in the registry.

        Returns:
            Updated :class:`~models.services.ServiceInfo`.

        Raises:
            KeyError: If *name* is not found in the registry.
        """
        svc = self._registry.get(name)
        if svc is None:
            raise KeyError(f"Service not found in registry: {name!r}")

        status, message = self._health.check_service(svc)
        logger.debug("Live status for %s: %s — %s", name, status.value, message)

        try:
            self._registry.update_status(name, status)
        except KeyError:
            pass

        updated = self._registry.get(name)
        assert updated is not None  # we just checked above
        return updated
