"""Anthropic Claude Provider —— 基于 SSE 的流式响应，支持 extended thinking。

调用 Anthropic Messages API（``/v1/messages``），``stream=True``。
"""

import json
import logging
from typing import Any, AsyncIterator

import httpx

from horizoncode.providers.base import BaseProvider, StreamFrame, register_provider
from horizoncode.tools.base import ToolCall

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────────

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
THINKING_BUDGET_TOKENS = 2048


# ── Provider ────────────────────────────────────────────────────────────────


class AnthropicProvider(BaseProvider):
    """Anthropic Claude API 的 Provider 实现，支持 extended thinking。

    通过 SSE 流式解析响应，将原始事件映射为统一的 StreamFrame 帧序列。
    支持通过 base_url 指向 Anthropic 兼容代理（如智谱 GLM）。
    """

    protocol = "anthropic"

    def __init__(self, api_key: str, base_url: str) -> None:
        """初始化 Anthropic Provider。

        Args:
            api_key: API 认证密钥（x-api-key）。
            base_url: API 基础 URL，尾部斜杠会被自动去除。
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建复用的 httpx 异步客户端。"""
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
        self,
        messages: list[dict],
        model: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamFrame]:
        """向 Anthropic Messages API 发起流式聊天请求。

        解析 SSE 事件流，区分 thinking_delta（思考过程）和 text_delta（正文），
        映射为对应的 StreamFrame 帧。所有异常都被捕获并以 error 帧返回。

        Args:
            messages: 统一格式的消息列表。
            model: 模型名称（如 ``claude-sonnet-4-6``）。
        """

        # 检测模型是否支持 extended thinking（Sonnet 4+、Opus 4+）
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
        if tools:
            body["tools"] = tools

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
                        text=f"Anthropic API 错误 ({response.status_code}): {_summarize_error(error_text)}",
                    )
                    return

                # 以内容块 index 缓存 input_json_delta 参数分片。
                tool_chunks: dict[int, dict[str, str]] = {}

                # 逐行解析 SSE 流
                async for line in response.aiter_lines():
                    if not line:
                        continue

                    # SSE 格式: "event: <type>" 后跟 "data: <json>"
                    if line.startswith("event: "):
                        event_type = line[7:].strip()
                        continue

                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("SSE 数据解析失败: %s", data_str[:100])
                        continue

                    # 跳过 ping 心跳事件
                    if isinstance(data, dict) and data.get("type") == "ping":
                        continue

                    # 处理内容增量事件
                    if isinstance(data, dict) and data.get("type") == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            index = data.get("index")
                            if isinstance(index, int):
                                initial_input = block.get("input", {})
                                tool_chunks[index] = {
                                    "id": str(block.get("id", "")),
                                    "name": str(block.get("name", "")),
                                    "arguments": json.dumps(initial_input) if initial_input else "",
                                }

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
                        elif delta_type == "input_json_delta":
                            index = data.get("index")
                            if isinstance(index, int) and index in tool_chunks:
                                tool_chunks[index]["arguments"] += delta.get("partial_json", "")

                    if isinstance(data, dict) and data.get("type") == "content_block_stop":
                        index = data.get("index")
                        if isinstance(index, int) and index in tool_chunks:
                            call = _build_tool_call(tool_chunks.pop(index))
                            if isinstance(call, ToolCall):
                                yield StreamFrame(type="tool_call", tool_call=call)
                            else:
                                yield StreamFrame(type="error", text=call)

                    # 处理 API 返回的错误事件
                    if isinstance(data, dict) and data.get("type") == "error":
                        yield StreamFrame(
                            type="error",
                            text=data.get("error", {}).get("message", str(data)),
                        )

                yield StreamFrame(type="done")

        except httpx.ConnectError as exc:
            yield StreamFrame(
                type="error",
                text=f"连接失败 —— 请检查网络和 base_url: {exc}",
            )
        except httpx.TimeoutException:
            yield StreamFrame(
                type="error",
                text="请求超时 —— 模型可能繁忙，请重试。",
            )
        except Exception as exc:
            logger.exception("Anthropic Provider 发生未预期错误")
            yield StreamFrame(
                type="error",
                text=f"未预期错误: {exc}",
            )

    async def close(self) -> None:
        """关闭底层 HTTP 客户端，释放连接资源。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── 辅助函数 ────────────────────────────────────────────────────────────────


def _should_enable_thinking(model: str) -> bool:
    """判断给定模型是否应启用 extended thinking。

    对已知支持 thinking 的模型（Claude 3.5 Sonnet+、Opus 4+、Sonnet 4+）返回 True，
    对 Haiku、Claude 3 Opus 及未知模型返回 False。
    """
    model_lower = model.lower()
    # Haiku 不支持 thinking
    if "haiku" in model_lower:
        return False
    # Claude 3 Opus 不支持 thinking
    if model_lower.startswith("claude-3-opus"):
        return False
    # Claude 3.5 Sonnet 及更高版本、Claude 4 系列均支持
    if "claude-3-5" in model_lower:
        return True
    if "claude-4" in model_lower or "claude-opus-4" in model_lower or "claude-sonnet-4" in model_lower:
        return True
    # 未知模型 / 兼容代理默认关闭（更安全）
    return False


def _summarize_error(body: bytes) -> str:
    """从 API 响应的 body 中提取可读的错误信息。"""
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


def _build_tool_call(chunk: dict[str, str]) -> ToolCall | str:
    """将 Anthropic 工具内容块中的 JSON 参数转换为统一调用。"""
    try:
        arguments = json.loads(chunk["arguments"] or "{}")
    except json.JSONDecodeError as exc:
        return f"工具调用参数不是有效 JSON（{chunk.get('name', 'unknown')}）: {exc}"
    if not isinstance(arguments, dict) or not chunk["id"] or not chunk["name"]:
        return "工具调用缺少 id、名称或对象类型参数"
    return ToolCall(id=chunk["id"], name=chunk["name"], arguments=arguments)


# ── 注册 ────────────────────────────────────────────────────────────────────

register_provider("anthropic", AnthropicProvider)
