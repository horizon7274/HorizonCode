"""Ollama Provider —— 基于原生 ``/api/chat`` 的 NDJSON 流式调用。"""

import json
import logging
from typing import Any, AsyncIterator

import httpx

from horizoncode.prompts.models import SystemPrompt
from horizoncode.providers.base import BaseProvider, StreamFrame, Usage, register_provider
from horizoncode.tools.base import ToolCall

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):
    """Ollama 原生聊天 API 的 Provider 实现。

    将 Ollama 的 NDJSON 响应转换为统一的 :class:`StreamFrame`，并解析其
    原生工具调用格式。Ollama 的本地服务无需 API 密钥，保留该参数是为了
    符合统一的 Provider 构造契约。
    """

    protocol = "ollama"

    def __init__(self, api_key: str, base_url: str) -> None:
        """初始化 Ollama Provider。

        Args:
            api_key: 保持与统一 Provider 契约一致；本地 Ollama 不使用此值。
            base_url: Ollama 服务地址，例如 ``http://localhost:11434``。
        """
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建复用的异步 HTTP 客户端。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                headers={"content-type": "application/json"},
            )
        return self._client

    async def stream_chat(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        system: str | SystemPrompt | None = None,
    ) -> AsyncIterator[StreamFrame]:
        """调用 Ollama 原生聊天接口并逐帧返回模型输出。

        Args:
            messages: 已转换为 Ollama 消息格式的会话历史。
            model: 本地模型名称。
            tools: Ollama function tools 定义；未提供时不发送该字段。
            system: 可选系统提示，以 system 角色消息插入到消息列表最前。
        """
        body: dict[str, Any] = {
            "model": model,
            "messages": _ollama_messages(messages, system),
            "stream": True,
        }
        if tools:
            body["tools"] = tools

        try:
            client = await self._get_client()
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json=body,
            ) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    yield StreamFrame(
                        type="error",
                        text=(
                            f"Ollama API 错误 ({response.status_code}): "
                            f"{_summarize_error(error_text)}"
                        ),
                    )
                    return

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        logger.debug("Ollama NDJSON 数据解析失败: %s", line[:100])
                        continue

                    message = data.get("message", {})
                    if not isinstance(message, dict):
                        message = {}

                    thinking = message.get("thinking", "")
                    if isinstance(thinking, str) and thinking:
                        yield StreamFrame(type="thinking", text=thinking, raw=data)

                    content = message.get("content", "")
                    if isinstance(content, str) and content:
                        yield StreamFrame(type="content", text=content, raw=data)

                    tool_calls = message.get("tool_calls", [])
                    if isinstance(tool_calls, list):
                        for index, tool_call in enumerate(tool_calls):
                            call = _build_tool_call(tool_call, index)
                            if isinstance(call, ToolCall):
                                yield StreamFrame(type="tool_call", tool_call=call, raw=data)
                            else:
                                yield StreamFrame(type="error", text=call, raw=data)

                    if data.get("done") is True:
                        # 末帧携带 Token 用量统计
                        input_tokens = data.get("prompt_eval_count")
                        output_tokens = data.get("eval_count")
                        usage = (
                            Usage(input_tokens=input_tokens, output_tokens=output_tokens)
                            if input_tokens is not None or output_tokens is not None
                            else None
                        )
                        yield StreamFrame(type="done", raw=data, usage=usage)
                        return

                yield StreamFrame(type="done")

        except httpx.ConnectError:
            yield StreamFrame(
                type="error",
                text="无法连接 Ollama —— 请确认服务已启动且 base_url 正确。",
            )
        except httpx.TimeoutException:
            yield StreamFrame(type="error", text="请求超时 —— 模型可能繁忙，请重试。")
        except Exception as exc:
            logger.exception("Ollama Provider 发生未预期错误")
            yield StreamFrame(type="error", text=f"未预期错误: {exc}")

    async def close(self) -> None:
        """关闭底层 HTTP 客户端，释放连接资源。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _ollama_messages(
    messages: list[dict],
    system: str | SystemPrompt | None,
) -> list[dict]:
    """将结构化系统提示合并为 Ollama 首条 system 消息。"""
    if isinstance(system, SystemPrompt):
        if system.as_text():
            return [{"role": "system", "content": system.as_text()}] + messages
        return messages
    if system:
        return [{"role": "system", "content": system}] + messages
    return messages


def _build_tool_call(tool_call: object, index: int) -> ToolCall | str:
    """将 Ollama 原生工具调用转换为统一的 ``ToolCall``。"""
    if not isinstance(tool_call, dict):
        return "Ollama 工具调用格式无效"
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return "Ollama 工具调用缺少 function"

    name = function.get("name")
    arguments = function.get("arguments")
    if not isinstance(name, str) or not name:
        return "Ollama 工具调用缺少工具名称"
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            return f"Ollama 工具调用参数不是有效 JSON（{name}）: {exc}"
    if not isinstance(arguments, dict):
        return f"Ollama 工具调用参数必须是对象（{name}）"

    return ToolCall(id=f"ollama_call_{index}", name=name, arguments=arguments)


def _summarize_error(body: bytes) -> str:
    """从 Ollama 错误响应中提取可展示的错误信息。"""
    try:
        data = json.loads(body)
        if isinstance(data, dict) and isinstance(data.get("error"), str):
            return data["error"]
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return body.decode("utf-8", errors="replace")[:200]


register_provider("ollama", OllamaProvider)
