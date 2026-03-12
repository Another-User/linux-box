"""OptAware services package.

Provides service registry, dependency resolution, health checking, and the
high-level service manager used by the daemon and CLI layers.

Typical usage::

    from services import ServiceRegistry, DependencyResolver, HealthChecker
    from services.manager import ServiceManager, ActionResult
    from services.registry import get_registry

    registry = get_registry()
    registry.load_from_manifest("/etc/optaware/service_manifest.yaml")

    resolver = DependencyResolver()
    checker = HealthChecker()
    manager = ServiceManager(registry, resolver, checker)

    result = manager.start_service("nginx", dry_run=False)
    print(result.message)
"""

from services.dependency import DependencyResolver
from services.health import HealthChecker
from services.registry import ServiceRegistry, get_registry

__all__ = [
    "ServiceRegistry",
    "DependencyResolver",
    "HealthChecker",
    "get_registry",
]
