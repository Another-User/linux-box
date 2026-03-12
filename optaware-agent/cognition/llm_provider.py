"""
LLMProvider — Abstract base and concrete provider implementations for LLM APIs.

Supports Anthropic, OpenAI, and any OpenAI-compatible local endpoint (e.g.
Ollama, LM Studio, vLLM).  All providers use httpx for async HTTP with
automatic exponential-backoff retries and configurable timeouts.
"""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

_DEFAULT_RETRIES: int = 3
_RETRY_BASE_DELAY: float = 1.0   # seconds
_RETRY_MAX_DELAY: float = 30.0   # seconds
_RETRY_JITTER: float = 0.25      # fraction of delay to randomise

# HTTP status codes that warrant a retry attempt
_RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Default HTTP timeout settings (seconds)
_DEFAULT_CONNECT_TIMEOUT: float = 10.0
_DEFAULT_READ_TIMEOUT: float = 120.0


async def _with_retries(
    fn,
    *,
    retries: int = _DEFAULT_RETRIES,
    base_delay: float = _RETRY_BASE_DELAY,
    max_delay: float = _RETRY_MAX_DELAY,
) -> Any:
    """Call an async callable with exponential-backoff retries.

    Args:
        fn: Async callable with no arguments (use functools.partial or lambdas).
        retries: Maximum number of retry attempts after the initial call.
        base_delay: Starting back-off delay in seconds.
        max_delay: Maximum back-off delay cap in seconds.

    Returns:
        Return value of fn on success.

    Raises:
        The last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await fn()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_exc = exc
            if attempt == retries:
                break
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = delay * _RETRY_JITTER * random.random()
            wait = delay + jitter
            logger.warning(
                "Network error on attempt %d/%d: %s. Retrying in %.1fs.",
                attempt + 1,
                retries + 1,
                exc,
                wait,
            )
            await asyncio.sleep(wait)
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code not in _RETRYABLE_STATUS or attempt == retries:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = delay * _RETRY_JITTER * random.random()
            # Honour Retry-After header if present
            retry_after = exc.response.headers.get("retry-after")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = delay + jitter
            else:
                wait = delay + jitter
            logger.warning(
                "HTTP %d on attempt %d/%d. Retrying in %.1fs.",
                exc.response.status_code,
                attempt + 1,
                retries + 1,
                wait,
            )
            await asyncio.sleep(wait)

    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract base for LLM API providers.

    Concrete subclasses must implement `send` and `model_name`.
    """

    @abstractmethod
    async def send(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        """Send a conversation to the LLM and return the assistant's reply.

        Args:
            messages: List of message dicts with "role" and "content" keys.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature (0.0 = deterministic).

        Returns:
            The assistant's response text.
        """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Canonical model identifier used for this provider instance."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model_name!r})"


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    """LLM provider for the Anthropic Messages API (claude-* models).

    Docs: https://docs.anthropic.com/en/api/messages
    """

    _API_URL: str = "https://api.anthropic.com/v1/messages"
    _API_VERSION: str = "2023-06-01"

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5",
        *,
        retries: int = _DEFAULT_RETRIES,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = _DEFAULT_READ_TIMEOUT,
    ) -> None:
        """
        Args:
            api_key: Anthropic API key.
            model: Model identifier, e.g. "claude-sonnet-4-5".
            retries: Max retry attempts on transient errors.
            connect_timeout: Connection timeout in seconds.
            read_timeout: Read timeout in seconds.
        """
        self._api_key = api_key
        self._model = model
        self._retries = retries
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=30.0,
            pool=5.0,
        )

    @property
    def model_name(self) -> str:
        return self._model

    async def send(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        # Anthropic separates system messages from the conversation array
        system_content: str | None = None
        conversation: list[dict] = []
        for msg in messages:
            if msg.get("role") == "system":
                # Concatenate multiple system messages
                system_content = (
                    (system_content + "\n\n" + msg["content"])
                    if system_content
                    else msg["content"]
                )
            else:
                conversation.append({"role": msg["role"], "content": msg["content"]})

        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": conversation,
        }
        if system_content:
            payload["system"] = system_content

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._API_VERSION,
            "content-type": "application/json",
        }

        async def _call() -> str:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._API_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                # Response shape: {"content": [{"type": "text", "text": "..."}], ...}
                content_blocks = data.get("content", [])
                texts = [
                    block["text"]
                    for block in content_blocks
                    if block.get("type") == "text"
                ]
                return "\n".join(texts)

        result: str = await _with_retries(_call, retries=self._retries)
        return result


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    """LLM provider for the OpenAI Chat Completions API (gpt-* models).

    Docs: https://platform.openai.com/docs/api-reference/chat
    """

    _API_URL: str = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        *,
        retries: int = _DEFAULT_RETRIES,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = _DEFAULT_READ_TIMEOUT,
    ) -> None:
        """
        Args:
            api_key: OpenAI API key.
            model: Model identifier, e.g. "gpt-4o", "gpt-4o-mini".
            retries: Max retry attempts on transient errors.
            connect_timeout: Connection timeout in seconds.
            read_timeout: Read timeout in seconds.
        """
        self._api_key = api_key
        self._model = model
        self._retries = retries
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=30.0,
            pool=5.0,
        )

    @property
    def model_name(self) -> str:
        return self._model

    async def send(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": m["role"], "content": m["content"]} for m in messages
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async def _call() -> str:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._API_URL, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("OpenAI API returned no choices in response.")
                return choices[0]["message"]["content"]

        result: str = await _with_retries(_call, retries=self._retries)
        return result


# ---------------------------------------------------------------------------
# Local / OpenAI-compatible provider
# ---------------------------------------------------------------------------


class LocalProvider(LLMProvider):
    """LLM provider for local OpenAI-compatible endpoints.

    Compatible with Ollama, LM Studio, vLLM, llama.cpp server, and any other
    service that implements the OpenAI Chat Completions API shape.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "llama3",
        *,
        retries: int = _DEFAULT_RETRIES,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = 180.0,
    ) -> None:
        """
        Args:
            base_url: Base URL of the local API server.
            model: Model name as recognised by the local server.
            retries: Max retry attempts on transient errors.
            connect_timeout: Connection timeout in seconds.
            read_timeout: Read timeout — local models can be slow.
        """
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._retries = retries
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=30.0,
            pool=5.0,
        )
        self._api_url = f"{self._base_url}/v1/chat/completions"

    @property
    def model_name(self) -> str:
        return self._model

    async def send(
        self,
        messages: list[dict],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": m["role"], "content": m["content"]} for m in messages
            ],
        }

        async def _call() -> str:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(self._api_url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ValueError("Local provider returned no choices in response.")
                return choices[0]["message"]["content"]

        result: str = await _with_retries(_call, retries=self._retries)
        return result


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def get_provider(config: dict) -> LLMProvider:
    """Instantiate an LLMProvider from a configuration dictionary.

    The config dict must contain at least a ``provider`` key. Additional keys
    are forwarded to the provider constructor.

    Supported ``provider`` values: "auto", "anthropic", "openai", "local".

    When ``provider`` is ``"auto"``, hardware detection runs to find the
    best available option and falls back to the Anthropic API.

    Example config::

        {
            "provider": "auto",
            "api_key": "sk-ant-...",
            "fallback_provider": "anthropic"
        }

    Args:
        config: Configuration mapping.

    Returns:
        Configured LLMProvider instance.

    Raises:
        KeyError: If the "provider" key is missing.
        ValueError: If the provider name is not recognised.
        RuntimeError: If "auto" mode cannot find any provider.
    """
    provider_name = config.get("provider", "").lower().strip()
    if not provider_name:
        raise KeyError("Config must include a 'provider' key.")

    # Extract shared optional kwargs
    retries: int = int(config.get("retries", _DEFAULT_RETRIES))
    connect_timeout: float = float(config.get("connect_timeout", _DEFAULT_CONNECT_TIMEOUT))
    read_timeout: float = float(config.get("read_timeout", _DEFAULT_READ_TIMEOUT))

    # Auto mode — detect hardware and pick the best provider
    if provider_name == "auto":
        return _auto_resolve(config, retries, connect_timeout, read_timeout)

    if provider_name == "anthropic":
        api_key: str = config.get("api_key") or _env_key("ANTHROPIC_API_KEY")
        model: str = config.get("model") or "claude-sonnet-4-5"
        return AnthropicProvider(
            api_key=api_key,
            model=model,
            retries=retries,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )

    if provider_name == "openai":
        api_key = config.get("api_key") or _env_key("OPENAI_API_KEY")
        model = config.get("model") or "gpt-4o"
        return OpenAIProvider(
            api_key=api_key,
            model=model,
            retries=retries,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )

    if provider_name in ("local", "ollama", "lmstudio", "vllm"):
        base_url: str = config.get("base_url", config.get("local_url", "http://localhost:11434"))
        model = config.get("model") or "llama3"
        return LocalProvider(
            base_url=base_url,
            model=model,
            retries=retries,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )

    raise ValueError(
        f"Unknown provider '{provider_name}'. "
        "Supported values: 'auto', 'anthropic', 'openai', 'local'."
    )


def _env_key(var_name: str) -> str:
    """Read an API key from an environment variable."""
    import os
    return os.environ.get(var_name, "")


def _auto_resolve(
    config: dict,
    retries: int,
    connect_timeout: float,
    read_timeout: float,
) -> LLMProvider:
    """Auto-detect hardware and return the best available provider.

    Fallback chain:
      1. Running local LLM service (Ollama, vLLM, etc.)
      2. Anthropic API (if key available)
      3. OpenAI API (if key available)
    """
    from cognition.hardware_detector import detect_hardware

    profile = detect_hardware()

    # 1. Try running local services
    for svc in profile.local_services:
        if svc.running and svc.models:
            model = config.get("model") or svc.models[0]
            logger.info(
                "Auto-detected local %s at %s with model %s",
                svc.name, svc.url, model,
            )
            return LocalProvider(
                base_url=svc.url,
                model=model,
                retries=retries,
                connect_timeout=connect_timeout,
                read_timeout=max(read_timeout, 180.0),
            )

    # 2. Anthropic API fallback
    anthropic_key = (
        config.get("api_key")
        or config.get("fallback_api_key")
        or _env_key("ANTHROPIC_API_KEY")
    )
    if anthropic_key:
        model = config.get("model") or "claude-sonnet-4-5"
        logger.info("Auto-fallback to Anthropic API (%s)", model)
        return AnthropicProvider(
            api_key=anthropic_key,
            model=model,
            retries=retries,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )

    # 3. OpenAI API fallback
    openai_key = (
        config.get("api_key")
        or config.get("fallback_api_key")
        or _env_key("OPENAI_API_KEY")
    )
    if openai_key:
        model = config.get("model") or "gpt-4o"
        logger.info("Auto-fallback to OpenAI API (%s)", model)
        return OpenAIProvider(
            api_key=openai_key,
            model=model,
            retries=retries,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )

    # 4. Log hardware recommendation and fail gracefully
    logger.error(
        "No LLM provider available. Hardware: %d GPUs (%d MiB VRAM), "
        "%d CPU cores, %d MiB RAM. Recommendation: %s",
        len(profile.gpus), profile.total_vram_mb,
        profile.cpu_cores, profile.ram_mb,
        profile.recommendation_reason,
    )
    raise RuntimeError(
        f"No LLM provider available. {profile.recommendation_reason}\n"
        "Set llm.api_key or ANTHROPIC_API_KEY to use the Claude API fallback."
    )
