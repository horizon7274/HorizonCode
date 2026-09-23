"""OpenAI Provider —— 基于 SSE 的 Chat Completions API 流式调用。

调用 OpenAI Chat Completions API（``/v1/chat/completions``），``stream=True``。
兼容 OpenAI 协议代理（Azure、本地模型等）。
"""

import json
import logging
from typing import Any, AsyncIterator

import httpx

from horizoncode.prompts.models import SystemPrompt
from horizoncode.providers.base import BaseProvider, StreamFrame, Usage, register_provider
from horizoncode.tools.base import ToolCall

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────────

DEFAULT_MAX_TOKENS = 4096


# ── Provider ────────────────────────────────────────────────────────────────


class OpenAIProvider(BaseProvider):
    """OpenAI Chat Completions API 的 Provider 实现，兼容 OpenAI 协议代理。

    解析 SSE 事件流（``data: [DONE]`` 终止），将每个 delta 映射为 StreamFrame。
    """

    protocol = "openai"

    def __init__(self, api_key: str, base_url: str) -> None:
        """初始化 OpenAI Provider。

        Args:
            api_key: API 认证密钥（Bearer token）。
            base_url: API 基础 URL（如 ``https://api.openai.com/v1``）。
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
                    "Authorization": f"Bearer {self._api_key}",
                    "content-type": "application/json",
                },
            )
        return self._client

    async def stream_chat(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        system: str | SystemPrompt | None = None,
    ) -> AsyncIterator[StreamFrame]:
        """向 OpenAI Chat Completions API 发起流式聊天请求。

        Args:
            messages: 统一格式的消息列表。
            model: 模型名称（如 ``gpt-4o``）。
            system: 可选系统提示，以 system 角色消息插入到消息列表最前。
        """

        body = {
            "model": model,
            "messages": _openai_messages(messages, system),
            "max_tokens": DEFAULT_MAX_TOKENS,
            "stream": True,
            # 请求在流末尾附带 usage 统计的独立 chunk
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools

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
                        text=f"OpenAI API 错误 ({response.status_code}): {_summarize_error(error_text)}",
                    )
                    return

                # 按 index 缓存不同调用的 JSON 参数分片。
                tool_chunks: dict[int, dict[str, str]] = {}
                # 流末尾 usage chunk 携带的 Token 用量
                usage: Usage | None = None

                # 逐行解析 SSE 流
                async for line in response.aiter_lines():
                    if not line:
                        continue

                    # SSE 格式: "data: <json>" 或 "data: [DONE]"
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()

                    if data_str == "[DONE]":
                        for index in sorted(tool_chunks):
                            call = _build_tool_call(tool_chunks[index])
                            if isinstance(call, ToolCall):
                                yield StreamFrame(type="tool_call", tool_call=call)
                            else:
                                yield StreamFrame(type="error", text=call)
                        yield StreamFrame(type="done", usage=usage)
                        return

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("SSE 数据解析失败: %s", data_str[:100])
                        continue

                    # usage chunk（include_usage 开启后出现在 [DONE] 之前，choices 为空）
                    raw_usage = data.get("usage")
                    if isinstance(raw_usage, dict):
                        usage = Usage(
                            input_tokens=_optional_int(
                                raw_usage.get("prompt_tokens", raw_usage.get("input_tokens"))
                            ),
                            output_tokens=_optional_int(
                                raw_usage.get("completion_tokens", raw_usage.get("output_tokens"))
                            ),
                            cached_input_tokens=_cached_tokens(raw_usage),
                        )

                    # 提取 content delta
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
                        for chunk in delta.get("tool_calls", []):
                            index = chunk.get("index")
                            if not isinstance(index, int):
                                continue
                            cached = tool_chunks.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            if chunk.get("id"):
                                cached["id"] = chunk["id"]
                            function = chunk.get("function", {})
                            if function.get("name"):
                                cached["name"] = function["name"]
                            if function.get("arguments"):
                                cached["arguments"] += function["arguments"]

                for index in sorted(tool_chunks):
                    call = _build_tool_call(tool_chunks[index])
                    if isinstance(call, ToolCall):
                        yield StreamFrame(type="tool_call", tool_call=call)
                    else:
                        yield StreamFrame(type="error", text=call)
                # 如果循环正常结束但未收到 [DONE]，仍然发送完成信号
                yield StreamFrame(type="done", usage=usage)

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
            logger.exception("OpenAI Provider 发生未预期错误")
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


def _openai_messages(
    messages: list[dict],
    system: str | SystemPrompt | None,
) -> list[dict]:
    """将旧字符串或结构化 system prompt 转成 OpenAI 消息列表。"""
    if isinstance(system, SystemPrompt):
        system_messages: list[dict] = []
        if system.stable:
            system_messages.append({"role": "system", "content": system.stable})
        system_messages.extend(
            {"role": "system", "content": supplement.render()}
            for supplement in system.supplements
        )
        return system_messages + messages
    if system:
        return [{"role": "system", "content": system}] + messages
    return messages


def _cached_tokens(raw_usage: dict[str, Any]) -> int | None:
    """兼容 Chat Completions 与代理响应中的缓存命中字段。"""
    for details_key in ("prompt_tokens_details", "input_tokens_details"):
        details = raw_usage.get(details_key)
        if isinstance(details, dict):
            value = _optional_int(details.get("cached_tokens"))
            if value is not None:
                return value
    return _optional_int(raw_usage.get("cached_tokens"))


def _optional_int(value: object) -> int | None:
    """只接受 JSON 整数用量，避免异常值破坏累计计算。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _summarize_error(body: bytes) -> str:
    """从 API 响应 body 中提取可读的错误信息。"""
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


def _build_tool_call(chunk: dict[str, str]) -> ToolCall | str:
    """将 OpenAI 的分片参数组合为完整工具调用或可展示的错误。"""
    try:
        arguments = json.loads(chunk["arguments"])
    except json.JSONDecodeError as exc:
        return f"工具调用参数不是有效 JSON（{chunk.get('name', 'unknown')}）: {exc}"
    if not isinstance(arguments, dict) or not chunk["id"] or not chunk["name"]:
        return "工具调用缺少 id、名称或对象类型参数"
    return ToolCall(id=chunk["id"], name=chunk["name"], arguments=arguments)


# ── 注册 ────────────────────────────────────────────────────────────────────

register_provider("openai", OpenAIProvider)
