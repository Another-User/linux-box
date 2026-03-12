"""Service registry — central store for all managed services.

The registry is a singleton (accessed via `get_registry()`) that holds
`ServiceInfo` objects keyed by service name.  It can be populated
programmatically or by loading a ``service_manifest.yaml`` file.
"""

from __future__ import annotations

import logging
from typing import Optional

from models.services import ServiceInfo, ServiceStatus, ServiceType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry class
# ---------------------------------------------------------------------------


class ServiceRegistry:
    """Thread-safe in-memory store for :class:`~models.services.ServiceInfo` objects.

    Typical usage::

        registry = get_registry()
        registry.load_from_manifest("/etc/optaware/service_manifest.yaml")
        web = registry.get("nginx")
    """

    def __init__(self) -> None:
        self._services: dict[str, ServiceInfo] = {}

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def register(self, service: ServiceInfo) -> None:
        """Add or replace a service in the registry.

        If a service with the same name already exists it is overwritten and
        a debug log message is emitted.

        Args:
            service: The :class:`~models.services.ServiceInfo` to register.
        """
        if service.name in self._services:
            logger.debug("Overwriting existing service registration: %s", service.name)
        self._services[service.name] = service
        logger.debug("Registered service: %s (%s)", service.name, service.service_type)

    def update_status(self, name: str, status: ServiceStatus) -> None:
        """Update the runtime status of a registered service.

        Args:
            name: Service identifier.
            status: New :class:`~models.services.ServiceStatus` value.

        Raises:
            KeyError: If *name* is not found in the registry.
        """
        if name not in self._services:
            raise KeyError(f"Service not found in registry: {name!r}")
        self._services[name] = self._services[name].model_copy(update={"status": status})
        logger.debug("Updated status of %s to %s", name, status)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[ServiceInfo]:
        """Return the :class:`~models.services.ServiceInfo` for *name*, or ``None``.

        Args:
            name: Service identifier.

        Returns:
            Matching :class:`~models.services.ServiceInfo` or ``None``.
        """
        return self._services.get(name)

    def get_all(self) -> list[ServiceInfo]:
        """Return every registered service as an unordered list."""
        return list(self._services.values())

    def get_by_type(self, stype: ServiceType) -> list[ServiceInfo]:
        """Return all services whose ``service_type`` equals *stype*.

        Args:
            stype: :class:`~models.services.ServiceType` to filter by.

        Returns:
            List of matching :class:`~models.services.ServiceInfo` objects.
        """
        return [s for s in self._services.values() if s.service_type == stype]

    def get_by_status(self, status: ServiceStatus) -> list[ServiceInfo]:
        """Return all services whose current ``status`` equals *status*.

        Args:
            status: :class:`~models.services.ServiceStatus` to filter by.

        Returns:
            List of matching :class:`~models.services.ServiceInfo` objects.
        """
        return [s for s in self._services.values() if s.status == status]

    # ------------------------------------------------------------------
    # Manifest loading
    # ------------------------------------------------------------------

    def load_from_manifest(self, path: str) -> None:
        """Populate the registry from a ``service_manifest.yaml`` file.

        Delegates parsing to :func:`services.manifest_loader.load_manifest`
        and registers every returned :class:`~models.services.ServiceInfo`.

        Args:
            path: Filesystem path to the YAML manifest file.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the YAML structure is invalid.
        """
        # Import here to avoid circular imports between registry and loader.
        from services.manifest_loader import load_manifest  # noqa: PLC0415

        services = load_manifest(path)
        for svc in services:
            self.register(svc)
        logger.info("Loaded %d service(s) from manifest: %s", len(services), path)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._services)

    def __contains__(self, name: object) -> bool:
        return name in self._services

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ServiceRegistry services={list(self._services.keys())}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: Optional[ServiceRegistry] = None


def get_registry() -> ServiceRegistry:
    """Return the process-wide :class:`ServiceRegistry` singleton.

    The instance is created lazily on first call and reused on all subsequent
    calls.  This is intentionally *not* thread-locked because registry
    initialisation happens once during startup before concurrent access begins.

    Returns:
        The global :class:`ServiceRegistry` instance.
    """
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = ServiceRegistry()
        logger.debug("Created new ServiceRegistry singleton")
    return _registry
