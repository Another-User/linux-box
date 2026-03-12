"""YAML-based configuration loader with overlay support for OptAware agent."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Optional

import yaml

from .defaults import DEFAULT_CONFIG
from .schema import OptAwareConfig

# Module-level singleton cache
_config_singleton: Optional[OptAwareConfig] = None


def _deep_merge(base: dict, overlay: dict) -> dict:
    """
    Recursively merge *overlay* into a copy of *base*.

    Keys present only in *base* are kept unchanged.
    Keys present in *overlay* win; if both values are dicts they are merged
    recursively rather than replaced wholesale.
    """
    result = deepcopy(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _unwrap(raw: dict) -> dict:
    """
    Accept either ``{optaware: {...}}`` or a flat dict and return the inner dict.
    """
    if "optaware" in raw and isinstance(raw["optaware"], dict):
        return raw["optaware"]
    return raw


def _load_yaml_file(path: str) -> dict:
    """Load a single YAML file and return its contents as a dict."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML file '{path}' must contain a mapping at the top level.")
    return _unwrap(data)


def load_config(path: str) -> OptAwareConfig:
    """
    Load and validate a configuration file, returning an :class:`OptAwareConfig`.

    The function supports:

    * A plain YAML file whose top-level keys match the config sections
      (``general:``, ``llm:``, …).
    * A YAML file wrapped under an ``optaware:`` top-level key.
    * **Config overlays** — if the loaded config contains a key
      ``extends`` whose value is the path to another YAML file, that file is
      loaded first as the base and the current file is merged on top.
      ``extends`` is resolved relative to the directory of the current file.
    * **Environment-specific overlays** — after loading the main file, the
      loader looks for a sibling file named
      ``<basename>.<environment>.yaml`` (e.g. ``config.prod.yaml``) and, if
      found, merges it on top.

    The result is cached as a process-level singleton accessible via
    :func:`get_config`.
    """
    global _config_singleton

    base_data: dict = deepcopy(DEFAULT_CONFIG)
    main_data: dict = _load_yaml_file(path)

    # Handle extends / base file
    extends_path = main_data.pop("extends", None)
    if extends_path is not None:
        if not os.path.isabs(extends_path):
            extends_path = os.path.join(os.path.dirname(os.path.abspath(path)), extends_path)
        base_file_data = _load_yaml_file(extends_path)
        # base file may itself have an 'extends' key — recursively handled
        base_file_data.pop("extends", None)
        base_data = _deep_merge(base_data, base_file_data)

    merged = _deep_merge(base_data, main_data)

    # Environment-specific overlay
    # Determine environment from the merged data before full validation
    env = merged.get("general", {}).get("environment", "dev")
    dir_name = os.path.dirname(os.path.abspath(path))
    basename = os.path.splitext(os.path.basename(path))[0]
    env_overlay_path = os.path.join(dir_name, f"{basename}.{env}.yaml")
    if os.path.isfile(env_overlay_path):
        env_data = _load_yaml_file(env_overlay_path)
        env_data.pop("extends", None)
        merged = _deep_merge(merged, env_data)

    config = OptAwareConfig.model_validate(merged)
    _config_singleton = config
    return config


def get_config() -> OptAwareConfig:
    """
    Return the cached :class:`OptAwareConfig` singleton.

    :raises RuntimeError: if :func:`load_config` has not been called yet.
    """
    if _config_singleton is None:
        raise RuntimeError(
            "Configuration has not been loaded. Call load_config(path) first."
        )
    return _config_singleton
