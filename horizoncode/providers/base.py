"""Abstract provider interface for LLM backends.

Defines the contract that every provider must implement, plus a factory
function that returns the correct provider for a given protocol.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

# ── data structures ────────────────────────────────────────────────────────


@dataclass
class StreamFrame:
    """A single chunk emitted during streaming.

    Attributes:
        type: One of ``"thinking"``, ``"content"``, ``"error"``, ``"done"``.
        text: The text payload (empty for ``done`` frames, error message for ``error``).
        raw: Optional provider-specific raw data (for debugging / logging).
    """

    type: str  # "thinking" | "content" | "error" | "done"
    text: str = ""
    raw: object = None


# ── abstract interface ─────────────────────────────────────────────────────


class BaseProvider(ABC):
    """Abstract base class for LLM providers.

    Every provider must implement :meth:`stream_chat`, which accepts a list
    of messages and the model name, and returns an async iterator of
    :class:`StreamFrame` objects.

    **Message format** (consistent across all providers)::

        [
            {"role": "system", "content": "..."},   # optional
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."},
            ...
        ]

    **Expected frame sequence**::

        thinking*  content*  done
        or:  (thinking|content)*  error

    Subclasses should catch all exceptions and emit them as ``error`` frames
    rather than letting them propagate — the TUI layer should never crash due
    to an API failure.
    """

    @abstractmethod
    async def stream_chat(
        self, messages: list[dict], model: str
    ) -> AsyncIterator[StreamFrame]:
        """Stream a chat completion for the given *messages*.

        Args:
            messages: List of message dicts with ``role`` and ``content`` keys.
            model: The model name to use (provider-specific).

        Yields:
            :class:`StreamFrame` instances as the response is generated.
        """
        ...


# ── factory ────────────────────────────────────────────────────────────────

_PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {}


def register_provider(protocol: str, provider_cls: type[BaseProvider]) -> None:
    """Register a provider class for the given *protocol* string."""
    _PROVIDER_REGISTRY[protocol] = provider_cls


def get_provider(protocol: str, api_key: str, base_url: str) -> BaseProvider:
    """Create and return a provider instance for the given *protocol*.

    Args:
        protocol: Protocol identifier (e.g. ``"anthropic"``, ``"openai"``).
        api_key: API key for authentication.
        base_url: Base URL for the API endpoint.

    Returns:
        A :class:`BaseProvider` instance.

    Raises:
        ValueError: If *protocol* is not registered.
    """
    cls = _PROVIDER_REGISTRY.get(protocol)
    if cls is None:
        raise ValueError(
            f"Unknown protocol '{protocol}'. "
            f"Available: {', '.join(sorted(_PROVIDER_REGISTRY))}"
        )
    return cls(api_key=api_key, base_url=base_url)
