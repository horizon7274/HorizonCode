"""需要用户逐次确认的项目内命令执行工具。"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

from horizoncode.tools.base import BaseTool, ConfirmationCallback, ToolDefinition, ToolResult

COMMAND_TIMEOUT_SECONDS = 30
MAX_COMMAND_OUTPUT_CHARS = 20_000


class ExecuteCommandTool(BaseTool):
    """在固定项目根目录执行经用户确认的 shell 命令。"""

    definition = ToolDefinition(
        name="execute_command",
        description="在当前项目根目录执行 shell 命令。每一条命令都必须由用户确认。",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"], "additionalProperties": False},
    )

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root.resolve()

    async def execute(self, arguments: dict[str, Any], *, confirm: ConfirmationCallback | None = None) -> ToolResult:
        """确认后运行命令，捕获输出、退出码和超时。"""
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult.failure(self.definition.name, "invalid_arguments", "command 必须是非空字符串")
        if confirm is None:
            return ToolResult.failure(self.definition.name, "denied", "命令执行需要用户确认")
        try:
            approved = confirm(command, {"cwd": str(self._project_root), "timeout_seconds": COMMAND_TIMEOUT_SECONDS})
            if inspect.isawaitable(approved):
                approved = await approved
        except Exception as exc:
            return ToolResult.failure(self.definition.name, "denied", f"无法获取用户确认: {exc}")
        if not approved:
            return ToolResult.failure(self.definition.name, "denied", "用户拒绝执行命令")
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self._project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=COMMAND_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                process.kill()
                stdout, stderr = await process.communicate()
                stdout_text, stdout_truncated = _truncate(stdout.decode("utf-8", errors="replace"))
                stderr_text, stderr_truncated = _truncate(stderr.decode("utf-8", errors="replace"))
                return ToolResult(
                    tool_name=self.definition.name,
                    ok=False,
                    error="timeout",
                    message=f"命令超过 {COMMAND_TIMEOUT_SECONDS} 秒，已终止",
                    data={"stdout": stdout_text, "stderr": stderr_text, "stdout_truncated": stdout_truncated, "stderr_truncated": stderr_truncated},
                )
        except OSError as exc:
            return ToolResult.failure(self.definition.name, "execution_failed", f"无法启动命令: {exc}")
        stdout_text, stdout_truncated = _truncate(stdout.decode("utf-8", errors="replace"))
        stderr_text, stderr_truncated = _truncate(stderr.decode("utf-8", errors="replace"))
        return ToolResult(self.definition.name, True, {"exit_code": process.returncode, "stdout": stdout_text, "stderr": stderr_text, "stdout_truncated": stdout_truncated, "stderr_truncated": stderr_truncated})


def _truncate(value: str) -> tuple[str, bool]:
    """将命令输出限制为可安全回灌模型的大小。"""
    return (value[:MAX_COMMAND_OUTPUT_CHARS], len(value) > MAX_COMMAND_OUTPUT_CHARS)
