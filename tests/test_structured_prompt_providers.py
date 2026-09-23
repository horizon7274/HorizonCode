"""结构化系统提示与缓存用量的 Provider 测试。"""

import json

import httpx
import pytest

from horizoncode.prompts.models import SystemPrompt, SystemSupplement
from horizoncode.providers.anthropic import AnthropicProvider
from horizoncode.providers.openai import OpenAIProvider
from horizoncode.providers.ollama import OllamaProvider


def _sse(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.mark.asyncio
async def test_anthropic_structured_system_and_cache_usage():
    """Anthropic 将稳定提示标记缓存，动态补充保持在缓存边界之后。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(
                [
                    'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":12,"cache_creation_input_tokens":7,"cache_read_input_tokens":3}}}',
                    'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":4}}',
                    'event: message_stop\ndata: {"type":"message_stop"}',
                ]
            ),
        )

    provider = AnthropicProvider("key", "https://api.anthropic.com")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prompt = SystemPrompt("稳定规则", (SystemSupplement("environment", "动态环境"),))
    frames = [
        frame
        async for frame in provider.stream_chat(
            [{"role": "user", "content": "hi"}],
            "claude-test",
            system=prompt,
        )
    ]
    await provider.close()

    blocks = captured["body"]["system"]
    assert blocks[0]["text"] == "稳定规则"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "动态环境" in blocks[1]["text"]
    usage = frames[-1].usage
    assert usage is not None
    assert usage.cache_creation_input_tokens == 7
    assert usage.cache_read_input_tokens == 3
    assert usage.output_tokens == 4


@pytest.mark.asyncio
async def test_openai_structured_system_and_cached_tokens():
    """OpenAI 保持稳定 system 前缀，并解析缓存命中 Token。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse(
                [
                    'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":4,"prompt_tokens_details":{"cached_tokens":8}}}',
                    "data: [DONE]",
                ]
            ),
        )

    provider = OpenAIProvider("key", "https://api.openai.com/v1")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prompt = SystemPrompt("稳定规则", (SystemSupplement("task-mode", "动态模式"),))
    frames = [
        frame
        async for frame in provider.stream_chat(
            [{"role": "user", "content": "hi"}],
            "gpt-test",
            system=prompt,
        )
    ]
    await provider.close()

    messages = captured["body"]["messages"]
    assert messages[0] == {"role": "system", "content": "稳定规则"}
    assert messages[1]["role"] == "system"
    assert "动态模式" in messages[1]["content"]
    assert messages[-1]["role"] == "user"
    assert frames[-1].usage is not None
    assert frames[-1].usage.cached_input_tokens == 8


@pytest.mark.asyncio
async def test_ollama_structured_system_keeps_cache_fields_empty():
    """Ollama 合并系统文本但不伪造缓存命中字段。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b'{"message":{"content":"ok"},"done":true}\n')

    provider = OllamaProvider("", "http://localhost:11434")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prompt = SystemPrompt("稳定规则", (SystemSupplement("environment", "动态环境"),))
    frames = [
        frame
        async for frame in provider.stream_chat(
            [{"role": "user", "content": "hi"}],
            "local",
            system=prompt,
        )
    ]
    await provider.close()

    assert "稳定规则" in captured["body"]["messages"][0]["content"]
    assert "动态环境" in captured["body"]["messages"][0]["content"]
    assert frames[-1].usage is None
