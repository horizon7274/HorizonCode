"""内置工具的路径边界、文件操作、搜索和命令确认测试。"""

import sys

import pytest

from horizoncode.tools.base import ToolCall
from horizoncode.tools.command import ExecuteCommandTool
from horizoncode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from horizoncode.tools.paths import WorkspacePathGuard
from horizoncode.tools.registry import ToolRegistry, create_default_registry
from horizoncode.tools.search import GlobFilesTool, GrepCodeTool


@pytest.fixture
def guard(tmp_path):
    """创建隔离临时项目的路径守卫。"""
    return WorkspacePathGuard(tmp_path)


@pytest.mark.asyncio
async def test_read_rejects_path_outside_workspace(guard):
    result = await ReadFileTool(guard).execute({"path": "../secret.txt"})
    assert not result.ok
    assert result.error == "path_outside_workspace"


@pytest.mark.asyncio
async def test_write_and_unique_edit(guard):
    writer = WriteFileTool(guard)
    editor = EditFileTool(guard)
    written = await writer.execute({"path": "sample.txt", "content": "before\n"})
    assert written.ok
    edited = await editor.execute({"path": "sample.txt", "old_text": "before", "new_text": "after"})
    assert edited.ok
    assert (guard.root / "sample.txt").read_text(encoding="utf-8") == "after\n"


@pytest.mark.asyncio
async def test_edit_refuses_non_unique_match(guard):
    path = guard.root / "sample.txt"
    path.write_text("same same", encoding="utf-8")
    result = await EditFileTool(guard).execute({"path": "sample.txt", "old_text": "same", "new_text": "other"})
    assert result.error == "match_not_unique"
    assert path.read_text(encoding="utf-8") == "same same"


@pytest.mark.asyncio
async def test_glob_and_grep_return_locations(guard):
    (guard.root / "src").mkdir()
    (guard.root / "src" / "main.py").write_text("async def stream_chat():\n    pass\n", encoding="utf-8")
    globbed = await GlobFilesTool(guard).execute({"pattern": "src/**/*.py"})
    searched = await GrepCodeTool(guard).execute({"pattern": "stream_chat"})
    assert globbed.data["files"] == ["src/main.py"]
    assert searched.data["matches"][0]["path"] == "src/main.py"
    assert searched.data["matches"][0]["line"] == 1


@pytest.mark.asyncio
async def test_command_requires_confirmation(tmp_path):
    tool = ExecuteCommandTool(tmp_path)
    result = await tool.execute({"command": "this command must not run"}, confirm=lambda *_: False)
    assert not result.ok
    assert result.error == "denied"


@pytest.mark.asyncio
async def test_command_returns_exit_code(tmp_path):
    tool = ExecuteCommandTool(tmp_path)
    command = f'"{sys.executable}" -c "print(\'ok\')"'
    result = await tool.execute({"command": command}, confirm=lambda *_: True)
    assert result.ok
    assert result.data["exit_code"] == 0
    assert result.data["stdout"].strip() == "ok"


def test_default_registry_has_six_tools(tmp_path):
    registry = create_default_registry(tmp_path)
    assert len(registry.definitions_for("anthropic")) == 6
    assert {tool["name"] for tool in registry.definitions_for("anthropic")} == {
        "read_file", "write_file", "edit_file", "execute_command", "glob_files", "grep_code",
    }
    assert {tool["function"]["name"] for tool in registry.definitions_for("openai")} == {
        "read_file", "write_file", "edit_file", "execute_command", "glob_files", "grep_code",
    }
    assert {tool["function"]["name"] for tool in registry.definitions_for("ollama")} == {
        "read_file",
        "write_file",
        "edit_file",
        "execute_command",
        "glob_files",
        "grep_code",
    }


def test_registry_rejects_duplicate_name(tmp_path):
    registry = ToolRegistry()
    tool = ReadFileTool(WorkspacePathGuard(tmp_path))
    registry.register(tool)
    with pytest.raises(ValueError, match="重复"):
        registry.register(tool)
