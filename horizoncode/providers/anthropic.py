"""Anthropic Claude provider — SSE streaming with extended thinking support.

Uses the Anthropic Messages API (``/v1/messages``) with ``stream=True``.
"""

import json
import logging
from typing import AsyncIterator

import httpx

from horizoncode.providers.base import BaseProvider, StreamFrame, register_provider

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
THINKING_BUDGET_TOKENS = 2048


# ── provider ───────────────────────────────────────────────────────────────


class AnthropicProvider(BaseProvider):
    """Provider for the Anthropic Claude API with extended thinking support."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
            )
        return self._client

    async def stream_chat(
        self, messages: list[dict], model: str
    ) -> AsyncIterator[StreamFrame]:
        """Stream a chat completion from the Anthropic Messages API."""

        # Detect if model supports extended thinking (Sonnet 4+, Opus 4+)
        enable_thinking = _should_enable_thinking(model)

        body = {
            "model": model,
            "messages": messages,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "stream": True,
        }

        if enable_thinking:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": THINKING_BUDGET_TOKENS,
            }

        try:
            client = await self._get_client()
            async with client.stream(
                "POST",
                f"{self._base_url}/v1/messages",
                json=body,
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    yield StreamFrame(
                        type="error",
                        text=f"Anthropic API error ({response.status_code}): {_summarize_error(error_text)}",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    # SSE format: "event: <type>" then "data: <json>"
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                        continue

                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("Failed to parse SSE data: %s", data_str[:100])
                        continue

                    # Handle ping events
                    if isinstance(data, dict) and data.get("type") == "ping":
                        continue

                    # Handle content_block_delta
                    if isinstance(data, dict) and data.get("type") == "content_block_delta":
                        delta = data.get("delta", {})
                        delta_type = delta.get("type", "")

                        if delta_type == "thinking_delta":
                            yield StreamFrame(
                                type="thinking",
                                text=delta.get("thinking", ""),
                                raw=data,
                            )
                        elif delta_type == "text_delta":
                            yield StreamFrame(
                                type="content",
                                text=delta.get("text", ""),
                                raw=data,
                            )

                    # Handle error event
                    if isinstance(data, dict) and data.get("type") == "error":
                        yield StreamFrame(
                            type="error",
                            text=data.get("error", {}).get("message", str(data)),
                        )

                yield StreamFrame(type="done")

        except httpx.ConnectError as exc:
            yield StreamFrame(
                type="error",
                text=f"Connection failed — check network and base_url: {exc}",
            )
        except httpx.TimeoutException:
            yield StreamFrame(
                type="error",
                text="Request timed out — the model may be busy, please retry.",
            )
        except Exception as exc:
            logger.exception("Unexpected error in Anthropic provider")
            yield StreamFrame(
                type="error",
                text=f"Unexpected error: {exc}",
            )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── helpers ────────────────────────────────────────────────────────────────


def _should_enable_thinking(model: str) -> bool:
    """Determine whether extended thinking should be enabled for a given model.

    Enabled for models known to support thinking (Claude 3.5 Sonnet+, Opus 4+, Sonnet 4+).
    Disabled for Haiku models and unknown models.
    """
    model_lower = model.lower()
    # Haiku doesn't support thinking
    if "haiku" in model_lower:
        return False
    # Claude 3 Opus doesn't support thinking
    if model_lower.startswith("claude-3-opus"):
        return False
    # Claude 3.5 Sonnet and later, Claude 4 models all support it
    if "claude-3-5" in model_lower:
        return True
    if "claude-4" in model_lower or "claude-opus-4" in model_lower or "claude-sonnet-4" in model_lower:
        return True
    # For unknown/anthropic-compatible models, default to off (safer)
    return False


def _summarize_error(body: bytes) -> str:
    """Extract a readable error message from the response body."""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            err = data.get("error", {})
            if isinstance(err, dict):
                return err.get("message", str(data))
            return str(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    text = body.decode("utf-8", errors="replace")[:200]
    return text


# ── registration ───────────────────────────────────────────────────────────

register_provider("anthropic", AnthropicProvider)
