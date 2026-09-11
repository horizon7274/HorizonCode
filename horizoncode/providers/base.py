"""LLM 后端的抽象 Provider 接口。

定义了每个 Provider 必须实现的契约，以及根据协议名创建 Provider 实例的工厂函数。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Any

from horizoncode.tools.base import ToolCall

# ── 数据结构 ────────────────────────────────────────────────────────────────


@dataclass
class Usage:
    """一次 LLM 响应的 Token 用量。

    属性:
        input_tokens: 输入（提示）Token 数；Provider 未提供时为 ``None``。
        output_tokens: 输出（补全）Token 数；Provider 未提供时为 ``None``。
    """

    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class StreamFrame:
    """流式响应中的单个数据帧。

    属性:
        type: 帧类型，取值为 ``"thinking"``、``"content"``、``"error"``、``"done"``。
        text: 文本内容（``done`` 帧为空，``error`` 帧为错误信息）。
        raw: Provider 返回的原始数据（用于调试/日志）。
        usage: 本轮响应的 Token 用量，仅 ``done`` 帧可能携带。
    """

    type: str  # "thinking" | "content" | "error" | "done"
    text: str = ""
    raw: object = None
    tool_call: ToolCall | None = None
    usage: Usage | None = None


# ── 抽象接口 ────────────────────────────────────────────────────────────────


class BaseProvider(ABC):
    """LLM Provider 抽象基类。

    每个 Provider 必须实现 :meth:`stream_chat` 方法，接收消息列表和模型名称，
    返回 :class:`StreamFrame` 的异步迭代器。

    **统一消息格式**（所有 Provider 通用）::

        [
            {"role": "system", "content": "..."},   # 可选
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."},
            ...
        ]

    **帧序列约定**::

        thinking*  content*  done
        或:  (thinking|content)*  error

    子类应捕获所有异常并以 ``error`` 帧形式返回，不应让异常向上传播——
    TUI 层不应因 API 调用失败而崩溃。
    """

    protocol: str

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
    ) -> AsyncIterator[StreamFrame]:
        """对流式聊天请求，逐帧返回响应。

        Args:
            messages: 消息字典列表，每个字典含 ``role`` 和 ``content`` 键。
            model: 要使用的模型名称（Provider 相关）。
            tools: 已转换为当前 Provider 协议的工具声明；``None`` 表示不启用工具。
            system: 可选的系统提示；Provider 会按各自协议的正确位置注入。

        Yields:
            :class:`StreamFrame` 实例，随响应生成逐个产出。
        """
        ...


# ── 工厂函数 ────────────────────────────────────────────────────────────────

# Provider 注册表：协议名 → Provider 类
_PROVIDER_REGISTRY: dict[str, type[BaseProvider]] = {}


def register_provider(protocol: str, provider_cls: type[BaseProvider]) -> None:
    """将 Provider 类注册到指定协议名。"""
    _PROVIDER_REGISTRY[protocol] = provider_cls


def get_provider(protocol: str, api_key: str, base_url: str) -> BaseProvider:
    """根据协议名创建对应的 Provider 实例。

    Args:
        protocol: 协议标识符（如 ``"anthropic"``、``"openai"``）。
        api_key: 用于认证的 API 密钥。
        base_url: API 端点的基础 URL。

    Returns:
        :class:`BaseProvider` 实例。

    Raises:
        ValueError: 协议名未注册时抛出。
    """
    cls = _PROVIDER_REGISTRY.get(protocol)
    if cls is None:
        raise ValueError(
            f"未知协议 '{protocol}'。"
            f"可用: {', '.join(sorted(_PROVIDER_REGISTRY))}"
        )
    return cls(api_key=api_key, base_url=base_url)
