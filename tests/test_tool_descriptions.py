"""内置工具描述与系统规则的双重强化测试。"""

from pathlib import Path

from horizoncode.tools.registry import create_default_registry


def _definitions(root: Path, protocol: str) -> dict[str, dict]:
    """返回指定协议下按工具名索引的工具声明。"""
    definitions = create_default_registry(root).definitions_for(protocol)
    if protocol == "anthropic":
        return {item["name"]: item for item in definitions}
    return {item["function"]["name"]: item for item in definitions}


def test_tool_descriptions_reinforce_selection_and_read_before_edit(tmp_path: Path):
    """三个协议都保留关键工具规则描述。"""
    for protocol in ("openai", "anthropic", "ollama"):
        definitions = _definitions(tmp_path, protocol)
        descriptions = {
            name: item["description"] if protocol == "anthropic" else item["function"]["description"]
            for name, item in definitions.items()
        }
        assert "专用工具" in descriptions["glob_files"]
        assert "专用工具" in descriptions["grep_code"]
        assert "read_file" in descriptions["edit_file"]
        assert "新建文件" in descriptions["write_file"]
        assert "后备" in descriptions["execute_command"]


def test_default_tool_read_only_classification_is_preserved(tmp_path: Path):
    """默认六个工具的只读边界保持不变。"""
    registry = create_default_registry(tmp_path)
    assert {
        tool.definition.name for tool in registry._tools.values() if tool.read_only
    } == {"read_file", "glob_files", "grep_code"}
    assert {
        tool.definition.name for tool in registry._tools.values() if not tool.read_only
    } == {"write_file", "edit_file", "execute_command"}
