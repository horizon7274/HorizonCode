"""Tests for the provider layer (no API keys needed)."""

import json

import httpx
import pytest

from horizoncode.providers.base import StreamFrame, Usage, get_provider
from horizoncode.providers.anthropic import AnthropicProvider
from horizoncode.providers.ollama import OllamaProvider
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

    def test_returns_ollama_provider(self):
        p = get_provider("ollama", api_key="", base_url="http://localhost:11434")
        assert isinstance(p, OllamaProvider)

    def test_unknown_protocol_raises(self):
        with pytest.raises(ValueError, match="未知协议"):
            get_provider("unknown", api_key="sk-test", base_url="http://localhost")


class TestProviderInit:
    def test_anthropic_strips_trailing_slash(self):
        p = AnthropicProvider(api_key="sk-test", base_url="https://api.anthropic.com/")
        assert p._base_url == "https://api.anthropic.com"

    def test_openai_strips_trailing_slash(self):
        p = OpenAIProvider(api_key="sk-test", base_url="https://api.openai.com/v1/")
        assert p._base_url == "https://api.openai.com/v1"

    def test_ollama_strips_trailing_slash(self):
        p = OllamaProvider(api_key="", base_url="http://localhost:11434/")
        assert p._base_url == "http://localhost:11434"


class TestUsageParsing:
    """三个 Provider 的流式 usage 解析。"""

    @staticmethod
    def _sse(lines: list[str]) -> bytes:
        return ("\n".join(lines) + "\n").encode("utf-8")

    @pytest.mark.asyncio
    async def test_anthropic_parses_usage(self):
        """message_start 与 message_delta 中的 usage 会随 done 帧产出。"""
        body = self._sse(
            [
                'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":12,"output_tokens":1}}}',
                'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}',
                'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":34}}',
                'event: message_stop\ndata: {"type":"message_stop"}',
            ]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body)

        provider = AnthropicProvider(api_key="sk-test", base_url="https://api.anthropic.com")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        frames = [frame async for frame in provider.stream_chat([{"role": "user", "content": "hi"}], "claude-3-5-haiku-20241022")]
        await provider.close()

        done = frames[-1]
        assert done.type == "done"
        assert done.usage is not None
        assert done.usage.input_tokens == 12
        assert done.usage.output_tokens == 34

    @pytest.mark.asyncio
    async def test_openai_requests_usage_and_parses_it(self):
        """请求体带 stream_options.include_usage，末尾 usage chunk 被解析。"""
        body = self._sse(
            [
                'data: {"choices":[{"delta":{"content":"hi"}}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":34}}',
                "data: [DONE]",
            ]
        )
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, content=body)

        provider = OpenAIProvider(api_key="sk-test", base_url="https://api.openai.com/v1")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        frames = [frame async for frame in provider.stream_chat([{"role": "user", "content": "hi"}], "gpt-4o")]
        await provider.close()

        assert captured["body"]["stream_options"] == {"include_usage": True}
        done = frames[-1]
        assert done.type == "done"
        assert done.usage == Usage(input_tokens=12, output_tokens=34)

    @pytest.mark.asyncio
    async def test_ollama_parses_usage_from_done_frame(self):
        """末帧的 prompt_eval_count / eval_count 映射为 usage。"""
        body = (
            b'{"message":{"role":"assistant","content":"answer"},"done":true,'
            b'"prompt_eval_count":12,"eval_count":34}\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body)

        provider = OllamaProvider(api_key="", base_url="http://localhost:11434")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        frames = [frame async for frame in provider.stream_chat([{"role": "user", "content": "hi"}], "qwen2.5-coder:7b")]
        await provider.close()

        done = frames[-1]
        assert done.type == "done"
        assert done.usage == Usage(input_tokens=12, output_tokens=34)

    @pytest.mark.asyncio
    async def test_missing_usage_yields_none(self):
        """响应不含 usage 字段时正常结束且 usage 为 None。"""
        body = (
            b'{"message":{"role":"assistant","content":"answer"},"done":true}\n'
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body)

        provider = OllamaProvider(api_key="", base_url="http://localhost:11434")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        frames = [frame async for frame in provider.stream_chat([{"role": "user", "content": "hi"}], "qwen2.5-coder:7b")]
        await provider.close()

        assert frames[-1].type == "done"
        assert frames[-1].usage is None

    @pytest.mark.asyncio
    async def test_system_prompt_positions(self):
        """system 提示按协议注入：anthropic 顶层字段，openai/ollama 首条消息。"""
        captured: dict = {}

        def ollama_handler(request: httpx.Request) -> httpx.Response:
            captured["ollama"] = json.loads(request.content)
            return httpx.Response(200, content=b'{"message":{"content":"ok"},"done":true}\n')

        provider = OllamaProvider(api_key="", base_url="http://localhost:11434")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(ollama_handler))
        _ = [frame async for frame in provider.stream_chat(
            [{"role": "user", "content": "hi"}], "qwen2.5-coder:7b", system="be brief"
        )]
        await provider.close()
        assert captured["ollama"]["messages"][0] == {"role": "system", "content": "be brief"}

        def anthropic_handler(request: httpx.Request) -> httpx.Response:
            captured["anthropic"] = json.loads(request.content)
            return httpx.Response(200, content=self._sse(['event: message_stop\ndata: {"type":"message_stop"}']))

        provider = AnthropicProvider(api_key="sk-test", base_url="https://api.anthropic.com")
        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler))
        _ = [frame async for frame in provider.stream_chat(
            [{"role": "user", "content": "hi"}], "claude-3-5-haiku-20241022", system="be brief"
        )]
        await provider.close()
        assert captured["anthropic"]["system"] == "be brief"
