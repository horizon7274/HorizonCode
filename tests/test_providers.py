"""Tests for the provider layer (no API keys needed)."""

import pytest

from horizoncode.providers.base import StreamFrame, get_provider
from horizoncode.providers.anthropic import AnthropicProvider
from horizoncode.providers.openai import OpenAIProvider


class TestStreamFrame:
    def test_defaults(self):
        sf = StreamFrame(type="done")
        assert sf.type == "done"
        assert sf.text == ""
        assert sf.raw is None

    def test_with_text(self):
        sf = StreamFrame(type="content", text="Hello")
        assert sf.text == "Hello"


class TestGetProvider:
    def test_returns_anthropic_provider(self):
        p = get_provider("anthropic", api_key="sk-test", base_url="https://api.anthropic.com")
        assert isinstance(p, AnthropicProvider)

    def test_returns_openai_provider(self):
        p = get_provider("openai", api_key="sk-test", base_url="https://api.openai.com/v1")
        assert isinstance(p, OpenAIProvider)

    def test_unknown_protocol_raises(self):
        with pytest.raises(ValueError, match="Unknown protocol"):
            get_provider("unknown", api_key="sk-test", base_url="http://localhost")


class TestProviderInit:
    def test_anthropic_strips_trailing_slash(self):
        p = AnthropicProvider(api_key="sk-test", base_url="https://api.anthropic.com/")
        assert p._base_url == "https://api.anthropic.com"

    def test_openai_strips_trailing_slash(self):
        p = OpenAIProvider(api_key="sk-test", base_url="https://api.openai.com/v1/")
        assert p._base_url == "https://api.openai.com/v1"
