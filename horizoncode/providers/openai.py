"""OpenAI Provider —— 基于 SSE 的 Chat Completions API 流式调用。

调用 OpenAI Chat Completions API（``/v1/chat/completions``），``stream=True``。
兼容 OpenAI 协议代理（Azure、本地模型等）。
"""

import json
import logging
from typing import AsyncIterator

import httpx

from horizoncode.providers.base import BaseProvider, StreamFrame, register_provider

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────────

DEFAULT_MAX_TOKENS = 4096


# ── Provider ────────────────────────────────────────────────────────────────


class OpenAIProvider(BaseProvider):
    """OpenAI Chat Completions API 的 Provider 实现，兼容 OpenAI 协议代理。

    解析 SSE 事件流（``data: [DONE]`` 终止），将每个 delta 映射为 StreamFrame。
    """

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
        self, messages: list[dict], model: str
    ) -> AsyncIterator[StreamFrame]:
        """向 OpenAI Chat Completions API 发起流式聊天请求。

        Args:
            messages: 统一格式的消息列表。
            model: 模型名称（如 ``gpt-4o``）。
        """

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
                        text=f"OpenAI API 错误 ({response.status_code}): {_summarize_error(error_text)}",
                    )
                    return

                # 逐行解析 SSE 流
                async for line in response.aiter_lines():
                    if not line:
                        continue

                    # SSE 格式: "data: <json>" 或 "data: [DONE]"
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()

                    if data_str == "[DONE]":
                        yield StreamFrame(type="done")
                        return

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        logger.debug("SSE 数据解析失败: %s", data_str[:100])
                        continue

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

                # 如果循环正常结束但未收到 [DONE]，仍然发送完成信号
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


# ── 注册 ────────────────────────────────────────────────────────────────────

register_provider("openai", OpenAIProvider)
