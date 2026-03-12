"""OptAware configuration package.

Public API
----------
OptAwareConfig  — top-level Pydantic config model
load_config     — load YAML config from a file path, cache the result
get_config      — return the cached singleton (must call load_config first)
"""

from .schema import (
    OptAwareConfig,
    GeneralConfig,
    ServicesConfig,
    LLMConfig,
    PerceptionConfig,
    PlanningConfig,
    KnowledgeConfig,
    DockerConfig,
    PortalConfig,
)
from .loader import load_config, get_config
from .validator import validate_config
from .defaults import DEFAULT_CONFIG

__all__ = [
    "OptAwareConfig",
    "GeneralConfig",
    "ServicesConfig",
    "LLMConfig",
    "PerceptionConfig",
    "PlanningConfig",
    "KnowledgeConfig",
    "DockerConfig",
    "PortalConfig",
    "load_config",
    "get_config",
    "validate_config",
    "DEFAULT_CONFIG",
]
