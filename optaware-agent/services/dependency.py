"""Dependency resolution and start/stop ordering for managed services.

The :class:`DependencyResolver` performs topological sorts over the directed
dependency graph encoded in each service's ``dependencies`` field and detects
circular dependency cycles using DFS-based back-edge detection.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.registry import ServiceRegistry

logger = logging.getLogger(__name__)


class DependencyResolver:
    """Resolve dependency graphs for :class:`~services.registry.ServiceRegistry` entries.

    All methods are stateless and accept the registry as an explicit argument so
    the resolver can be shared or constructed once and reused safely.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, service_name: str, registry: "ServiceRegistry") -> list[str]:
        """Return a topologically sorted list of dependencies for *service_name*.

        The returned list includes *service_name* itself as the last element —
        i.e. every service in the list must be started *before* the last one.

        Args:
            service_name: The service whose transitive dependencies should be
                resolved.
            registry: Registry to look up dependency information from.

        Returns:
            Ordered list of service names from deepest dependency to
            *service_name* itself.

        Raises:
            KeyError: If *service_name* is not found in the registry.
            ValueError: If a circular dependency is detected.
        """
        if registry.get(service_name) is None:
            raise KeyError(f"Service not found in registry: {service_name!r}")

        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()  # nodes on current DFS path (grey set)

        def _dfs(name: str) -> None:
            if name in visiting:
                raise ValueError(
                    f"Circular dependency detected while resolving {service_name!r}: "
                    f"cycle contains {name!r}"
                )
            if name in visited:
                return

            visiting.add(name)
            svc = registry.get(name)
            if svc is not None:
                for dep in svc.dependencies:
                    _dfs(dep.service_name)
            visiting.discard(name)
            visited.add(name)
            order.append(name)

        _dfs(service_name)
        logger.debug("Resolved dependencies for %s: %s", service_name, order)
        return order

    def check_circular(self, registry: "ServiceRegistry") -> list[list[str]]:
        """Detect all circular dependency chains in the registry.

        Uses iterative DFS with a path stack to identify every cycle.  Each
        cycle is reported as an ordered list of service names that form the
        loop, ending with the node that closes the cycle.

        Args:
            registry: Registry to analyse.

        Returns:
            List of cycles; each cycle is itself a ``list[str]`` of service
            names.  Returns an empty list when no cycles exist.
        """
        cycles: list[list[str]] = []
        visited: set[str] = set()
        all_services = [svc.name for svc in registry.get_all()]

        def _dfs_cycle(name: str, path: list[str], on_path: set[str]) -> None:
            if name in on_path:
                # Found a cycle — extract the loop portion
                cycle_start = path.index(name)
                cycle = path[cycle_start:] + [name]
                cycles.append(cycle)
                return
            if name in visited:
                return

            visited.add(name)
            on_path.add(name)
            path.append(name)

            svc = registry.get(name)
            if svc is not None:
                for dep in svc.dependencies:
                    _dfs_cycle(dep.service_name, path, on_path)

            path.pop()
            on_path.discard(name)

        for svc_name in all_services:
            if svc_name not in visited:
                _dfs_cycle(svc_name, [], set())

        if cycles:
            logger.warning("Detected %d circular dependency cycle(s)", len(cycles))
        return cycles

    def get_start_order(
        self,
        services: list[str],
        registry: "ServiceRegistry",
    ) -> list[str]:
        """Return the optimal start order for a collection of services.

        Performs a single Kahn's-algorithm topological sort over the subgraph
        induced by *services* (and their transitive dependencies that also
        appear in *services*).  Dependencies are started before dependents.

        Args:
            services: Names of services to order.
            registry: Registry providing dependency information.

        Returns:
            Ordered list in which each service appears after all its
            dependencies.  Services not found in the registry are silently
            skipped.

        Raises:
            ValueError: If a circular dependency prevents a valid ordering.
        """
        # Expand to include all transitive dependencies that exist in the
        # registry, even if they were not explicitly listed.
        expanded: set[str] = set()
        for name in services:
            if registry.get(name) is not None:
                try:
                    for dep_name in self.resolve(name, registry):
                        expanded.add(dep_name)
                except (KeyError, ValueError):
                    expanded.add(name)

        # Build adjacency lists limited to the expanded set.
        in_degree: dict[str, int] = {n: 0 for n in expanded}
        adj: dict[str, list[str]] = {n: [] for n in expanded}

        for name in expanded:
            svc = registry.get(name)
            if svc is None:
                continue
            for dep in svc.dependencies:
                if dep.service_name in expanded:
                    # dep.service_name → name edge (dep must come first)
                    adj[dep.service_name].append(name)
                    in_degree[name] += 1

        # Kahn's BFS topological sort
        queue: deque[str] = deque(n for n, deg in in_degree.items() if deg == 0)
        order: list[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbour in adj[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if len(order) != len(expanded):
            remaining = expanded - set(order)
            raise ValueError(
                f"Circular dependency prevents valid start ordering. "
                f"Remaining services: {sorted(remaining)}"
            )

        logger.debug("Start order for %s: %s", services, order)
        return order

    def get_stop_order(
        self,
        services: list[str],
        registry: "ServiceRegistry",
    ) -> list[str]:
        """Return the optimal stop order for a collection of services.

        This is simply the reverse of :meth:`get_start_order` — dependents are
        stopped before their dependencies.

        Args:
            services: Names of services to order.
            registry: Registry providing dependency information.

        Returns:
            Ordered list in which each service appears before its dependencies.

        Raises:
            ValueError: If a circular dependency prevents a valid ordering.
        """
        start = self.get_start_order(services, registry)
        stop = list(reversed(start))
        logger.debug("Stop order for %s: %s", services, stop)
        return stop
