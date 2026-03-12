"""Configuration validation for OptAware agent."""

import os
from .schema import OptAwareConfig


def validate_config(config: OptAwareConfig) -> list[str]:
    """
    Validate the given OptAwareConfig and return a list of warning/error strings.

    An empty list means the configuration is valid. Non-empty entries describe
    problems that were found; entries prefixed with 'ERROR:' are hard failures,
    entries prefixed with 'WARNING:' are advisory.
    """
    issues: list[str] = []

    # --- General ---
    if not config.general.hostname:
        issues.append("ERROR: general.hostname must not be empty.")

    if not os.path.isabs(config.general.data_dir):
        issues.append(
            f"WARNING: general.data_dir '{config.general.data_dir}' is not an absolute path."
        )
    elif not os.path.exists(config.general.data_dir):
        issues.append(
            f"WARNING: general.data_dir '{config.general.data_dir}' does not exist."
        )

    # --- LLM ---
    if config.llm.provider in ("anthropic", "openai") and not config.llm.api_key:
        issues.append(
            f"WARNING: llm.api_key is empty for provider '{config.llm.provider}'. "
            "API calls will fail unless the key is supplied via environment variable."
        )

    if config.llm.cost_limit_daily == 0.0:
        issues.append(
            "WARNING: llm.cost_limit_daily is 0.0; no spending guard will be applied."
        )

    # --- Perception ---
    if config.perception.metric_interval_sec < 5:
        issues.append(
            f"WARNING: perception.metric_interval_sec is {config.perception.metric_interval_sec}s, "
            "which may cause high CPU usage. Consider a value >= 5."
        )

    # --- Planning ---
    if config.planning.approval_mode == "auto" and config.planning.dry_run_default:
        issues.append(
            "WARNING: planning.approval_mode is 'auto' but dry_run_default is True. "
            "Actions will be simulated and never actually executed until dry_run_default is False."
        )

    if config.planning.approval_mode == "auto" and not config.planning.dry_run_default:
        issues.append(
            "WARNING: planning.approval_mode is 'auto' with dry_run_default=False. "
            "The agent will execute actions without human approval."
        )

    if config.planning.max_concurrent_actions > 10:
        issues.append(
            f"WARNING: planning.max_concurrent_actions is {config.planning.max_concurrent_actions}, "
            "which is unusually high and may overwhelm the system."
        )

    # --- Knowledge ---
    if not config.knowledge.vector_store_url.startswith(("http://", "https://")):
        issues.append(
            f"ERROR: knowledge.vector_store_url '{config.knowledge.vector_store_url}' "
            "must begin with http:// or https://."
        )

    if not config.knowledge.index_name:
        issues.append("ERROR: knowledge.index_name must not be empty.")

    # --- Docker ---
    if config.docker.socket_path and not os.path.exists(config.docker.socket_path):
        issues.append(
            f"WARNING: docker.socket_path '{config.docker.socket_path}' does not exist. "
            "Docker operations will fail."
        )

    # --- Portal ---
    if config.portal.enabled and not config.portal.secret_key:
        issues.append(
            "ERROR: portal.secret_key must be set when portal.enabled is True."
        )

    if config.portal.enabled and config.portal.port < 1024:
        issues.append(
            f"WARNING: portal.port {config.portal.port} is a privileged port (<1024). "
            "The process may need elevated privileges to bind."
        )

    return issues
