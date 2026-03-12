"""Parse ``service_manifest.yaml`` into :class:`~models.services.ServiceInfo` objects.

The manifest YAML has two top-level keys — ``core`` and ``elective`` — each
containing a list of service definitions.  Example shape::

    core:
      - name: nginx
        display_name: "NGINX Web Server"
        systemd_unit: nginx.service
        port: 80
        dependencies:
          - service_name: openssl
            required: true
        health_check_cmd: "nginx -t"
        config_paths:
          - /etc/nginx/nginx.conf
        log_paths:
          - /var/log/nginx/error.log

    elective:
      - name: redis
        display_name: "Redis Cache"
        docker_container: redis
        port: 6379
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from models.services import ServiceDependency, ServiceInfo, ServiceStatus, ServiceType

logger = logging.getLogger(__name__)


def _parse_service(raw: dict[str, Any], stype: ServiceType) -> ServiceInfo:
    """Convert a raw YAML dictionary to a :class:`~models.services.ServiceInfo`.

    Unknown fields are silently ignored (``extra="ignore"`` is set on the
    Pydantic model).

    Args:
        raw: Dictionary of YAML fields for a single service entry.
        stype: Whether this service is ``core`` or ``elective``.

    Returns:
        Populated :class:`~models.services.ServiceInfo` instance.

    Raises:
        ValueError: If the ``name`` field is missing or empty.
        pydantic.ValidationError: If field values fail Pydantic validation.
    """
    name = raw.get("name", "").strip()
    if not name:
        raise ValueError(f"Service entry is missing a 'name' field: {raw!r}")

    # Parse dependencies
    raw_deps: list[dict[str, Any]] = raw.get("dependencies", []) or []
    dependencies: list[ServiceDependency] = []
    for dep_entry in raw_deps:
        if isinstance(dep_entry, str):
            # Shorthand: just a service name string
            dependencies.append(ServiceDependency(service_name=dep_entry, required=True))
        elif isinstance(dep_entry, dict):
            dep_name = dep_entry.get("service_name") or dep_entry.get("name", "")
            if dep_name:
                dependencies.append(
                    ServiceDependency(
                        service_name=dep_name,
                        required=bool(dep_entry.get("required", True)),
                    )
                )
            else:
                logger.warning(
                    "Dependency entry in service %r is missing 'service_name': %r",
                    name,
                    dep_entry,
                )

    # Normalise list fields — allow a plain string as a single-item list
    config_paths = _coerce_str_or_list(raw.get("config_paths") or raw.get("config_path"))
    log_paths = _coerce_str_or_list(raw.get("log_paths") or raw.get("log_path"))

    return ServiceInfo(
        name=name,
        display_name=raw.get("display_name", name),
        service_type=stype,
        status=ServiceStatus.unknown,
        systemd_unit=raw.get("systemd_unit") or None,
        docker_container=raw.get("docker_container") or None,
        config_paths=config_paths,
        log_paths=log_paths,
        dependencies=dependencies,
        health_check_cmd=raw.get("health_check_cmd") or None,
        port=raw.get("port") or None,
    )


def _coerce_str_or_list(value: Any) -> list[str]:
    """Convert *value* to a list of strings.

    * ``None`` → ``[]``
    * ``str`` → ``[str]``
    * ``list`` → ``list`` (as-is, filtering ``None``/empty items)

    Args:
        value: Raw YAML value.

    Returns:
        List of non-empty strings.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)] if value else []


def load_manifest(path: str) -> list[ServiceInfo]:
    """Parse *path* as a ``service_manifest.yaml`` and return all services.

    Handles both ``core`` and ``elective`` sections.  Entries that fail
    validation are logged and skipped so one bad entry does not prevent the
    rest from loading.

    Args:
        path: Filesystem path to the YAML manifest file.

    Returns:
        List of :class:`~models.services.ServiceInfo` objects in the order
        they appear in the manifest (core first, then elective).

    Raises:
        FileNotFoundError: If *path* does not exist.
        yaml.YAMLError: If the file is not valid YAML.
        ValueError: If the top-level structure is not a mapping.
    """
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Service manifest not found: {path!r}")

    with manifest_path.open("r", encoding="utf-8") as fh:
        raw_yaml = yaml.safe_load(fh)

    if not isinstance(raw_yaml, dict):
        raise ValueError(
            f"Service manifest must be a YAML mapping at the top level; "
            f"got {type(raw_yaml).__name__!r} in {path!r}"
        )

    services: list[ServiceInfo] = []

    for section_key, stype in (("core", ServiceType.core), ("elective", ServiceType.elective)):
        section = raw_yaml.get(section_key) or []
        if not isinstance(section, list):
            logger.warning(
                "Manifest section %r in %s is not a list; skipping",
                section_key,
                path,
            )
            continue

        for idx, entry in enumerate(section):
            if not isinstance(entry, dict):
                logger.warning(
                    "Manifest section %r entry %d is not a mapping; skipping: %r",
                    section_key,
                    idx,
                    entry,
                )
                continue
            try:
                svc = _parse_service(entry, stype)
                services.append(svc)
                logger.debug("Loaded service from manifest: %s (%s)", svc.name, stype.value)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to parse service entry %d in section %r of %s: %s",
                    idx,
                    section_key,
                    path,
                    exc,
                )

    logger.info(
        "Loaded %d service(s) from manifest %s",
        len(services),
        path,
    )
    return services
