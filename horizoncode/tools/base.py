"""工具系统的统一领域模型与抽象接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


ConfirmationCallback = Callable[[str, dict[str, Any]], bool | Awaitable[bool]]


@dataclass(frozen=True)
class ToolDefinition:
    """描述可供模型调用的工具。

    Args:
        name: 工具的稳定名称。
        description: 告知模型何时使用该工具的说明。
        input_schema: JSON Schema 格式的参数定义。
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """Provider 解析完成的一次工具调用请求。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """一次工具执行的结构化结果。

    ``ok`` 为 ``False`` 时，``error`` 含机器可读的错误类别，``message``
    提供给模型和用户阅读的原因。
    """

    tool_name: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    message: str = ""

    @classmethod
    def failure(cls, tool_name: str, error: str, message: str) -> "ToolResult":
        """构造失败结果。"""
        return cls(tool_name=tool_name, ok=False, error=error, message=message)

    def to_dict(self) -> dict[str, Any]:
        """返回可写入历史和发送给 Provider 的 JSON 兼容对象。"""
        result: dict[str, Any] = {"tool_name": self.tool_name, "ok": self.ok}
        if self.data:
            result["data"] = self.data
        if not self.ok:
            result["error"] = self.error
            result["message"] = self.message
        return result

    def summary(self) -> str:
        """生成供终端展示的一行摘要。"""
        if self.ok:
            return f"{self.tool_name}: 已完成"
        return f"{self.tool_name}: {self.message}"


class BaseTool(ABC):
    """所有本地工具必须实现的统一接口。"""

    definition: ToolDefinition

    # 是否为纯只读工具（不产生任何副作用）。Agent 循环据此决定
    # 同批工具调用能否并发执行，Plan Mode 也据此过滤可用工具。
    read_only: bool = False

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        *,
        confirm: ConfirmationCallback | None = None,
    ) -> ToolResult:
        """执行调用并以结构化结果返回，预期错误不应向上抛出。"""
        ...
