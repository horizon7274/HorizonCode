"""Ollama Provider 的 NDJSON 流与错误处理测试。"""

import json

import httpx
import pytest

from horizoncode.providers.ollama import OllamaProvider


@pytest.mark.asyncio
async def test_stream_chat_converts_ollama_ndjson_and_tool_call():
    """Ollama 的文本、思考与工具调用会映射为统一帧。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=(
                b'{"message":{"role":"assistant","thinking":"plan"},"done":false}\n'
                b'{"message":{"role":"assistant","content":"answer","tool_calls":['
                b'{"function":{"name":"read_file","arguments":{"path":"README.md"}}}'
                b']},"done":true}\n'
            ),
        )

    provider = OllamaProvider(api_key="", base_url="http://localhost:11434")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    frames = [
        frame
        async for frame in provider.stream_chat(
            [{"role": "user", "content": "read README"}],
            "qwen2.5-coder:7b",
            tools=[{"type": "function", "function": {"name": "read_file"}}],
        )
    ]
    await provider.close()

    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["body"]["stream"] is True
    assert captured["body"]["tools"][0]["type"] == "function"
    assert [frame.type for frame in frames] == ["thinking", "content", "tool_call", "done"]
    assert frames[2].tool_call is not None
    assert frames[2].tool_call.id == "ollama_call_0"
    assert frames[2].tool_call.arguments == {"path": "README.md"}


@pytest.mark.asyncio
async def test_stream_chat_returns_clear_connection_error():
    """本地服务不可达时返回错误帧而不是抛出异常。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    provider = OllamaProvider(api_key="", base_url="http://localhost:11434")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    frames = [
        frame
        async for frame in provider.stream_chat(
            [{"role": "user", "content": "hello"}], "qwen2.5-coder:7b"
        )
    ]
    await provider.close()

    assert len(frames) == 1
    assert frames[0].type == "error"
    assert "无法连接 Ollama" in frames[0].text
