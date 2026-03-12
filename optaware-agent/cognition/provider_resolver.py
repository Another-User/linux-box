"""LLM provider auto-resolution with fallback chain.

Resolves the best available LLM provider at runtime based on:
  1. Hardware detection (GPUs, VRAM, local services)
  2. Explicit config (user overrides)
  3. Fallback chain: local GPU → local CPU → Anthropic API → OpenAI API

The resolver runs once at startup and caches the result.  It can be
re-triggered on config reload (SIGHUP) or via the API.

Usage::

    from cognition.provider_resolver import resolve_provider

    provider = await resolve_provider(config.llm)
    response = await provider.send(messages)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from cognition.hardware_detector import HardwareProfile, detect_hardware
from cognition.llm_provider import (
    AnthropicProvider,
    LLMProvider,
    LocalProvider,
    OpenAIProvider,
)

logger = logging.getLogger("optaware.cognition.provider_resolver")

# Module-level cache
_cached_profile: Optional[HardwareProfile] = None
_cached_provider: Optional[LLMProvider] = None


def get_hardware_profile(force_refresh: bool = False) -> HardwareProfile:
    """Return the cached hardware profile, detecting on first call.

    Args:
        force_refresh: Re-run hardware detection even if cached.
    """
    global _cached_profile
    if _cached_profile is None or force_refresh:
        _cached_profile = detect_hardware()
    return _cached_profile


async def resolve_provider(
    llm_config: object,
    *,
    force_refresh: bool = False,
) -> LLMProvider:
    """Resolve the best LLM provider based on config and hardware.

    If ``llm_config.provider`` is ``"auto"``, runs hardware detection and
    selects the best available option.  Otherwise, uses the explicit config.

    The fallback chain for auto mode:
      1. Running local LLM service with models loaded
      2. GPU available → recommend local with Ollama
      3. Sufficient RAM/CPU → local CPU inference
      4. Anthropic API (if api_key set or ANTHROPIC_API_KEY env var)
      5. OpenAI API (if api_key set or OPENAI_API_KEY env var)
      6. Raise error — no provider available

    Args:
        llm_config: The ``LLMConfig`` object from OptAware config.
        force_refresh: Re-detect hardware even if cached.

    Returns:
        A configured :class:`LLMProvider` instance.

    Raises:
        RuntimeError: If no provider can be resolved.
    """
    global _cached_provider

    provider_name = getattr(llm_config, "provider", "auto")
    api_key = getattr(llm_config, "api_key", "")
    model = getattr(llm_config, "model", "")
    base_url = getattr(llm_config, "local_url", "")
    fallback_provider = getattr(llm_config, "fallback_provider", "anthropic")
    fallback_api_key = getattr(llm_config, "fallback_api_key", "")

    # If not auto mode, use explicit config directly
    if provider_name != "auto":
        return _build_explicit_provider(provider_name, api_key, model, base_url)

    # Auto mode — detect hardware and pick the best option
    profile = get_hardware_profile(force_refresh=force_refresh)

    # Try each option in the fallback chain
    provider = _try_local_service(profile, model)
    if provider:
        logger.info("Auto-resolved to local provider: %s", provider.model_name)
        _cached_provider = provider
        return provider

    provider = _try_local_gpu(profile, base_url, model)
    if provider:
        logger.info("Auto-resolved to local GPU provider: %s", provider.model_name)
        _cached_provider = provider
        return provider

    provider = _try_api_fallback(
        fallback_provider, api_key, fallback_api_key, model,
    )
    if provider:
        logger.info("Auto-resolved to API fallback: %s", provider.model_name)
        _cached_provider = provider
        return provider

    raise RuntimeError(
        "No LLM provider available. Either:\n"
        "  1. Install Ollama (curl -fsSL https://ollama.com/install.sh | sh) and pull a model\n"
        "  2. Set llm.api_key with your Anthropic API key\n"
        "  3. Set ANTHROPIC_API_KEY or OPENAI_API_KEY environment variable\n"
        "  4. Set llm.provider to 'anthropic' or 'openai' explicitly"
    )


def _build_explicit_provider(
    provider_name: str, api_key: str, model: str, base_url: str,
) -> LLMProvider:
    """Build a provider from explicit config (non-auto mode)."""
    if provider_name == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        return AnthropicProvider(api_key=key, model=model or "claude-sonnet-4-5")

    if provider_name == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        return OpenAIProvider(api_key=key, model=model or "gpt-4o")

    if provider_name in ("local", "ollama", "lmstudio", "vllm"):
        url = base_url or "http://localhost:11434"
        return LocalProvider(base_url=url, model=model or "llama3")

    raise ValueError(f"Unknown provider: {provider_name}")


def _try_local_service(
    profile: HardwareProfile, preferred_model: str,
) -> Optional[LLMProvider]:
    """Try to use a running local LLM service."""
    for svc in profile.local_services:
        if not svc.running or not svc.models:
            continue

        # Use preferred model if it's available on this service
        model = preferred_model if preferred_model in svc.models else svc.models[0]

        # Ollama uses a different API path
        if svc.name == "ollama":
            return LocalProvider(
                base_url=svc.url,
                model=model,
                read_timeout=180.0,
            )

        # OpenAI-compatible services
        return LocalProvider(
            base_url=svc.url,
            model=model,
            read_timeout=180.0,
        )

    return None


def _try_local_gpu(
    profile: HardwareProfile, base_url: str, preferred_model: str,
) -> Optional[LLMProvider]:
    """If GPU is available and Ollama is installed (but not running), suggest it."""
    if not profile.has_gpu:
        return None

    # We detected a GPU but no running service — can't auto-start Ollama
    # Just log the recommendation
    if profile.recommended_provider == "local":
        logger.warning(
            "GPU detected (%d MiB VRAM) but no local LLM service is running. "
            "Recommended: %s. Falling back to API provider.",
            profile.total_vram_mb,
            profile.recommendation_reason,
        )
    return None


def _try_api_fallback(
    fallback_provider: str,
    primary_api_key: str,
    fallback_api_key: str,
    model: str,
) -> Optional[LLMProvider]:
    """Try API providers as fallback."""

    # Try Anthropic first (default fallback)
    anthropic_key = (
        primary_api_key
        or fallback_api_key
        or os.environ.get("ANTHROPIC_API_KEY", "")
    )
    if anthropic_key and fallback_provider in ("anthropic", ""):
        return AnthropicProvider(
            api_key=anthropic_key,
            model=model or "claude-sonnet-4-5",
        )

    # Try OpenAI
    openai_key = (
        primary_api_key
        or fallback_api_key
        or os.environ.get("OPENAI_API_KEY", "")
    )
    if openai_key and fallback_provider == "openai":
        return OpenAIProvider(
            api_key=openai_key,
            model=model or "gpt-4o",
        )

    # Try any available API key from env
    anthropic_env = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_env:
        return AnthropicProvider(
            api_key=anthropic_env,
            model="claude-sonnet-4-5",
        )

    openai_env = os.environ.get("OPENAI_API_KEY", "")
    if openai_env:
        return OpenAIProvider(
            api_key=openai_env,
            model="gpt-4o",
        )

    return None


# ------------------------------------------------------------------
# Status / introspection
# ------------------------------------------------------------------


def get_provider_status() -> dict:
    """Return current provider resolution status for the API/portal."""
    profile = get_hardware_profile()
    return {
        "hardware": profile.to_dict(),
        "cached_provider": repr(_cached_provider) if _cached_provider else None,
        "recommended_provider": profile.recommended_provider,
        "recommended_model": profile.recommended_model,
        "recommendation_reason": profile.recommendation_reason,
    }
