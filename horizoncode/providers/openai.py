"""OpenAI provider — SSE streaming via Chat Completions API.

Uses the OpenAI Chat Completions API (``/v1/chat/completions``) with ``stream=True``.
Compatible with OpenAI-compatible proxies (Azure, local models, etc.).
"""

import json
import logging
from typing import AsyncIterator

import httpx

from horizoncode.providers.base import BaseProvider, StreamFrame, register_provider

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────

DEFAULT_MAX_TOKENS = 4096


# ── provider ───────────────────────────────────────────────────────────────


class OpenAIProvider(BaseProvider):
    """Provider for the OpenAI Chat Completions API (and compatible proxies)."""

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "content-type": "application/json",
                },
            )
        return self._client

    async def stream_chat(
        self, messages: list[dict], model: str
    ) -> AsyncIterator[StreamFrame]:
        """Stream a chat completion from the OpenAI Chat Completions API."""

        body = {
            "model": model,
            "messages": messages,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "stream": True,
        }

        try:
            client = await self._get_client()
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                json=body,
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    yield StreamFrame(
                        type="error",
                        text=f"OpenAI API error ({response.status_code}): {_summarize_error(error_text)}",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue

                    # SSE format: "data: <json>" or "data: [DONE]"
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()

                    if data_str == "[DONE]":
                        yield StreamFrame(type="done")
                        return

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("Failed to parse SSE data: %s", data_str[:100])
                        continue

                    # Extract content delta
                    choices = data.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield StreamFrame(
                                type="content",
                                text=content,
                                raw=data,
                            )

                # If we exit the loop without [DONE], still signal done
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
            logger.exception("Unexpected error in OpenAI provider")
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


def _summarize_error(body: bytes) -> str:
    """Extract a readable error message from the response body."""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            err = data.get("error", {})
            if isinstance(err, dict):
                return err.get("message", str(data))
            if isinstance(err, str):
                return err
            return str(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    text = body.decode("utf-8", errors="replace")[:200]
    return text


# ── registration ───────────────────────────────────────────────────────────

register_provider("openai", OpenAIProvider)
