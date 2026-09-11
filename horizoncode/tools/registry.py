"""工具注册中心和 Provider Schema 转换。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from horizoncode.tools.base import BaseTool, ConfirmationCallback, ToolCall, ToolResult
from horizoncode.tools.command import ExecuteCommandTool
from horizoncode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from horizoncode.tools.paths import WorkspacePathGuard
from horizoncode.tools.search import GlobFilesTool, GrepCodeTool


class ToolRegistry:
    """集中管理可用工具，并转换为不同 Provider 的声明格式。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具；名称重复时拒绝覆盖。"""
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"工具名称重复: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> BaseTool | None:
        """按名称取得工具，不存在时返回 ``None``。"""
        return self._tools.get(name)

    def definitions_for(self, protocol: str, *, read_only: bool = False) -> list[dict[str, Any]]:
        """生成指定 Provider 协议所需的工具声明。

        Args:
            protocol: 目标 Provider 协议（anthropic / openai / ollama）。
            read_only: 为 ``True`` 时只返回声明为只读的工具（Plan Mode 使用）。
        """
        selected = (
            tool for tool in self._tools.values() if not read_only or tool.read_only
        )
        if protocol == "anthropic":
            return [{"name": tool.definition.name, "description": tool.definition.description, "input_schema": tool.definition.input_schema} for tool in selected]
        if protocol in ("openai", "ollama"):
            return [{"type": "function", "function": {"name": tool.definition.name, "description": tool.definition.description, "parameters": tool.definition.input_schema}} for tool in selected]
        raise ValueError(f"不支持的工具协议: {protocol}")

    async def execute(self, call: ToolCall, *, confirm: ConfirmationCallback | None = None) -> ToolResult:
        """查找并执行调用；未知工具也返回可回灌的失败结果。"""
        tool = self.get(call.name)
        if tool is None:
            return ToolResult.failure(call.name, "not_found", f"未注册工具: {call.name}")
        return await tool.execute(call.arguments, confirm=confirm)


def create_default_registry(project_root: Path) -> ToolRegistry:
    """创建包含六个核心工具的默认注册中心。"""
    paths = WorkspacePathGuard(project_root)
    registry = ToolRegistry()
    for tool in (ReadFileTool(paths), WriteFileTool(paths), EditFileTool(paths), ExecuteCommandTool(paths.root), GlobFilesTool(paths), GrepCodeTool(paths)):
        registry.register(tool)
    return registry
