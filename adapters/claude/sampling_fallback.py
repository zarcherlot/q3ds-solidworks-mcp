"""Credential-backed server-side fallback for MCP Sampling."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from config import SamplingFallbackConfig, SamplingFallbackConfigurationError


_SANITIZED_PROVIDER_ERROR = (
    "server-side Sampling provider request failed; verify the configured endpoint, "
    "model, credentials, and OpenAI-compatible Chat Completions support"
)


class CredentialSafeSamplingHandler:
    """Prevent provider exceptions from carrying credentials into MCP errors or traces."""

    def __init__(self, delegate: Callable[..., Any]) -> None:
        if not callable(delegate):
            raise TypeError("delegate must be callable")
        self._delegate = delegate

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = self._delegate(*args, **kwargs)
            if inspect.isawaitable(result):
                return await result
            return result
        except Exception:
            raise RuntimeError(_SANITIZED_PROVIDER_ERROR) from None


def build_sampling_fallback_handler(
    config: SamplingFallbackConfig,
) -> CredentialSafeSamplingHandler | None:
    """Build the OpenAI-compatible fallback only when explicitly enabled."""
    if not config.enabled:
        return None
    if config.api_key is None or config.model is None:
        raise SamplingFallbackConfigurationError(
            "enabled server-side Sampling fallback requires an API key and model"
        )

    try:
        from fastmcp.client.sampling.handlers.openai import OpenAISamplingHandler
        from openai import AsyncOpenAI
    except ImportError:
        raise SamplingFallbackConfigurationError(
            "server-side Sampling fallback requires the FastMCP OpenAI extra; "
            "install the repository requirements"
        ) from None

    client_options: dict[str, str] = {"api_key": config.api_key}
    if config.base_url is not None:
        client_options["base_url"] = config.base_url
    client = AsyncOpenAI(**client_options)
    delegate = OpenAISamplingHandler(default_model=config.model, client=client)
    return CredentialSafeSamplingHandler(delegate)
