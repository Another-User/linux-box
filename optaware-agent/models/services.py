"""Pydantic data models for OptAware service management."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ServiceStatus(str, Enum):
    """Operational status of a managed service."""

    running = "running"
    stopped = "stopped"
    degraded = "degraded"
    unknown = "unknown"
    starting = "starting"
    stopping = "stopping"


class ServiceType(str, Enum):
    """Category of a managed service."""

    core = "core"
    elective = "elective"


class ServiceDependency(BaseModel):
    """Describes a dependency relationship between services."""

    model_config = ConfigDict(extra="ignore")

    service_name: str
    required: bool = True


class ServiceInfo(BaseModel):
    """Full descriptor for a managed service."""

    model_config = ConfigDict(extra="ignore")

    name: str
    display_name: str = ""
    service_type: ServiceType = ServiceType.elective
    status: ServiceStatus = ServiceStatus.unknown
    systemd_unit: Optional[str] = None
    docker_container: Optional[str] = None
    config_paths: list[str] = Field(default_factory=list)
    log_paths: list[str] = Field(default_factory=list)
    dependencies: list[ServiceDependency] = Field(default_factory=list)
    health_check_cmd: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
