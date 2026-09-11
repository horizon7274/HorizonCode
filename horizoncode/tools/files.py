"""项目内文本文件的读取、写入与唯一原文替换工具。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from horizoncode.tools.base import BaseTool, ToolDefinition, ToolResult
from horizoncode.tools.paths import PathOutsideWorkspaceError, WorkspacePathGuard

MAX_FILE_BYTES = 1024 * 1024


class ReadFileTool(BaseTool):
    """读取项目内 UTF-8 文本文件，可选按行截取。"""

    read_only = True

    definition = ToolDefinition(
        name="read_file",
        description="读取项目根目录内的 UTF-8 文本文件。需要时用 start_line 和 end_line 限定行范围。",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "项目根目录内的相对文件路径"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def __init__(self, paths: WorkspacePathGuard) -> None:
        self._paths = paths

    async def execute(self, arguments: dict[str, Any], *, confirm: object = None) -> ToolResult:
        """读取文件并返回文本、行范围和截断状态。"""
        try:
            path = self._paths.resolve_file(arguments.get("path"))
            return await asyncio.to_thread(self._read, path, arguments)
        except PathOutsideWorkspaceError as exc:
            return ToolResult.failure(self.definition.name, "path_outside_workspace", str(exc))
        except (TypeError, ValueError) as exc:
            return ToolResult.failure(self.definition.name, "invalid_arguments", str(exc))

    def _read(self, path: Path, arguments: dict[str, Any]) -> ToolResult:
        """在线程中执行有大小限制的文本读取。"""
        if not path.exists():
            return ToolResult.failure(self.definition.name, "not_found", f"文件不存在: {self._paths.relative(path)}")
        if not path.is_file():
            return ToolResult.failure(self.definition.name, "not_a_file", "目标不是普通文件")
        try:
            with path.open("rb") as file:
                raw = file.read(MAX_FILE_BYTES + 1)
        except OSError as exc:
            return ToolResult.failure(self.definition.name, "execution_failed", f"读取文件失败: {exc}")
        if b"\x00" in raw:
            return ToolResult.failure(self.definition.name, "binary_file", "不支持读取包含 NUL 字节的二进制文件")
        try:
            content = raw[:MAX_FILE_BYTES].decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure(self.definition.name, "binary_file", "不支持读取非 UTF-8 文本文件")
        truncated = len(raw) > MAX_FILE_BYTES
        lines = content.splitlines(keepends=True)
        start = arguments.get("start_line", 1)
        end = arguments.get("end_line", len(lines))
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            return ToolResult.failure(self.definition.name, "invalid_arguments", "行范围必须为从 1 开始的递增整数")
        selected = "".join(lines[start - 1:end])
        return ToolResult(
            tool_name=self.definition.name,
            ok=True,
            data={"path": self._paths.relative(path), "content": selected, "start_line": start, "end_line": min(end, len(lines)), "truncated": truncated},
        )


class WriteFileTool(BaseTool):
    """覆盖写入项目内的 UTF-8 文本文件。"""

    definition = ToolDefinition(
        name="write_file",
        description="以 UTF-8 文本覆盖写入项目根目录内的文件。父目录必须已经存在。",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    )

    def __init__(self, paths: WorkspacePathGuard) -> None:
        self._paths = paths

    async def execute(self, arguments: dict[str, Any], *, confirm: object = None) -> ToolResult:
        """写入文本；不会自动创建父目录。"""
        try:
            path = self._paths.resolve_file(arguments.get("path"))
            content = arguments.get("content")
            if not isinstance(content, str):
                raise ValueError("content 必须是字符串")
            return await asyncio.to_thread(self._write, path, content)
        except PathOutsideWorkspaceError as exc:
            return ToolResult.failure(self.definition.name, "path_outside_workspace", str(exc))
        except ValueError as exc:
            return ToolResult.failure(self.definition.name, "invalid_arguments", str(exc))

    def _write(self, path: Path, content: str) -> ToolResult:
        if not path.parent.exists():
            return ToolResult.failure(self.definition.name, "not_found", "父目录不存在，不会自动创建")
        if path.exists() and not path.is_file():
            return ToolResult.failure(self.definition.name, "not_a_file", "目标不是普通文件")
        try:
            if path.exists():
                with path.open("rb") as file:
                    if b"\x00" in file.read(MAX_FILE_BYTES):
                        return ToolResult.failure(self.definition.name, "binary_file", "不支持覆盖二进制文件")
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(self.definition.name, "execution_failed", f"写入文件失败: {exc}")
        return ToolResult(self.definition.name, True, {"path": self._paths.relative(path), "bytes_written": len(content.encode("utf-8"))})


class EditFileTool(BaseTool):
    """通过唯一原文匹配替换项目内文本文件的一段内容。"""

    definition = ToolDefinition(
        name="edit_file",
        description="在项目内 UTF-8 文件中将 old_text 的唯一出现替换为 new_text。old_text 必须恰好匹配一次。",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        },
    )

    def __init__(self, paths: WorkspacePathGuard) -> None:
        self._paths = paths

    async def execute(self, arguments: dict[str, Any], *, confirm: object = None) -> ToolResult:
        """执行唯一原文替换，匹配异常时不改动文件。"""
        try:
            path = self._paths.resolve_file(arguments.get("path"))
            old_text, new_text = arguments.get("old_text"), arguments.get("new_text")
            if not isinstance(old_text, str) or not old_text:
                raise ValueError("old_text 必须是非空字符串")
            if not isinstance(new_text, str):
                raise ValueError("new_text 必须是字符串")
            return await asyncio.to_thread(self._edit, path, old_text, new_text)
        except PathOutsideWorkspaceError as exc:
            return ToolResult.failure(self.definition.name, "path_outside_workspace", str(exc))
        except ValueError as exc:
            return ToolResult.failure(self.definition.name, "invalid_arguments", str(exc))

    def _edit(self, path: Path, old_text: str, new_text: str) -> ToolResult:
        if not path.exists():
            return ToolResult.failure(self.definition.name, "not_found", f"文件不存在: {self._paths.relative(path)}")
        if not path.is_file():
            return ToolResult.failure(self.definition.name, "not_a_file", "目标不是普通文件")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.failure(self.definition.name, "binary_file", "不支持编辑非 UTF-8 文本文件")
        except OSError as exc:
            return ToolResult.failure(self.definition.name, "execution_failed", f"读取文件失败: {exc}")
        count = content.count(old_text)
        if count == 0:
            return ToolResult.failure(self.definition.name, "match_not_found", "old_text 在文件中未匹配到，请读取最新内容后重试")
        if count > 1:
            return ToolResult.failure(self.definition.name, "match_not_unique", f"old_text 匹配到 {count} 次，必须唯一")
        try:
            path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(self.definition.name, "execution_failed", f"写入文件失败: {exc}")
        return ToolResult(self.definition.name, True, {"path": self._paths.relative(path), "replacements": 1})
