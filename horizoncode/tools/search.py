"""项目内文件发现和代码内容搜索工具。"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from horizoncode.tools.base import BaseTool, ToolDefinition, ToolResult
from horizoncode.tools.paths import PathOutsideWorkspaceError, WorkspacePathGuard

MAX_RESULTS = 100
MAX_SCAN_BYTES = 1024 * 1024


class GlobFilesTool(BaseTool):
    """按 glob 模式列出项目根目录内的文件。"""

    read_only = True

    definition = ToolDefinition(
        name="glob_files",
        description="按 glob 模式列出项目根目录内的文件；发现文件名和目录时优先使用本专用工具，例如 horizoncode/**/*.py。",
        input_schema={"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"], "additionalProperties": False},
    )

    def __init__(self, paths: WorkspacePathGuard) -> None:
        self._paths = paths

    async def execute(self, arguments: dict[str, Any], *, confirm: object = None) -> ToolResult:
        """匹配文件并以稳定顺序返回。"""
        try:
            pattern = self._paths.validate_glob(arguments.get("pattern"))
            return await asyncio.to_thread(self._glob, pattern)
        except PathOutsideWorkspaceError as exc:
            return ToolResult.failure(self.definition.name, "path_outside_workspace", str(exc))
        except ValueError as exc:
            return ToolResult.failure(self.definition.name, "invalid_arguments", str(exc))

    def _glob(self, pattern: str) -> ToolResult:
        try:
            paths = sorted(
                (path for path in self._paths.root.glob(pattern) if path.is_file() and self._paths.contains(path)),
                key=lambda path: self._paths.relative(path),
            )
            files = [self._paths.relative(path) for path in paths[:MAX_RESULTS]]
        except OSError as exc:
            return ToolResult.failure(self.definition.name, "execution_failed", f"查找文件失败: {exc}")
        return ToolResult(self.definition.name, True, {"files": files, "truncated": len(paths) > MAX_RESULTS})


class GrepCodeTool(BaseTool):
    """在项目内 UTF-8 文本文件中搜索文本或正则表达式。"""

    read_only = True

    definition = ToolDefinition(
        name="grep_code",
        description="搜索项目内 UTF-8 文本文件；搜索代码内容时优先使用本专用工具，返回匹配所在的文件、行号、行文本和匹配位置。",
        input_schema={
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "regex": {"type": "boolean", "default": False}},
            "required": ["pattern"],
            "additionalProperties": False,
        },
    )

    def __init__(self, paths: WorkspacePathGuard) -> None:
        self._paths = paths

    async def execute(self, arguments: dict[str, Any], *, confirm: object = None) -> ToolResult:
        """执行有结果数量限制的递归代码搜索。"""
        pattern = arguments.get("pattern")
        regex = arguments.get("regex", False)
        if not isinstance(pattern, str) or not pattern or not isinstance(regex, bool):
            return ToolResult.failure(self.definition.name, "invalid_arguments", "pattern 必须是非空字符串，regex 必须是布尔值")
        try:
            compiled = re.compile(pattern if regex else re.escape(pattern))
        except re.error as exc:
            return ToolResult.failure(self.definition.name, "invalid_arguments", f"无效正则表达式: {exc}")
        return await asyncio.to_thread(self._grep, compiled)

    def _grep(self, compiled: re.Pattern[str]) -> ToolResult:
        matches: list[dict[str, Any]] = []
        try:
            files = sorted(
                (path for path in self._paths.root.rglob("*") if path.is_file() and self._paths.contains(path)),
                key=lambda path: self._paths.relative(path),
            )
            for path in files:
                try:
                    raw = path.read_bytes()[:MAX_SCAN_BYTES]
                    if b"\x00" in raw:
                        continue
                    text = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    for match in compiled.finditer(line):
                        matches.append({"path": self._paths.relative(path), "line": line_number, "text": line, "start": match.start(), "end": match.end()})
                        if len(matches) >= MAX_RESULTS:
                            return ToolResult(self.definition.name, True, {"matches": matches, "truncated": True})
        except OSError as exc:
            return ToolResult.failure(self.definition.name, "execution_failed", f"搜索文件失败: {exc}")
        return ToolResult(self.definition.name, True, {"matches": matches, "truncated": False})
