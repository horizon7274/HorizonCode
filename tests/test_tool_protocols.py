"""工具调用流式拼接及工具消息历史转换测试。"""

from horizoncode.history import HistoryManager
from horizoncode.providers.anthropic import _build_tool_call as build_anthropic_call
from horizoncode.providers.openai import _build_tool_call as build_openai_call
from horizoncode.tools.base import ToolCall, ToolResult


def test_openai_tool_call_json_fragments_are_combined_before_building():
    call = build_openai_call({"id": "call_1", "name": "read_file", "arguments": '{"path":"a.py"}'})
    assert isinstance(call, ToolCall)
    assert call.arguments == {"path": "a.py"}


def test_anthropic_tool_call_json_fragments_are_combined_before_building():
    call = build_anthropic_call({"id": "toolu_1", "name": "glob_files", "arguments": '{"pattern":"**/*.py"}'})
    assert isinstance(call, ToolCall)
    assert call.arguments == {"pattern": "**/*.py"}


def test_history_generates_tool_messages_for_both_protocols():
    history = HistoryManager()
    history.add("user", "read it")
    call = ToolCall(id="call_1", name="read_file", arguments={"path": "README.md"})
    history.add_tool_calls([call])
    history.add_tool_result(call, ToolResult("read_file", True, {"content": "hello"}))

    openai = history.get_api_messages("openai")
    assert openai[1]["tool_calls"][0]["id"] == "call_1"
    assert openai[2]["role"] == "tool"
    assert openai[2]["tool_call_id"] == "call_1"

    anthropic = history.get_api_messages("anthropic")
    assert anthropic[1]["content"][0]["type"] == "tool_use"
    assert anthropic[2]["content"][0]["tool_use_id"] == "call_1"

    ollama = history.get_api_messages("ollama")
    assert ollama[1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert ollama[2]["role"] == "tool"
    assert ollama[2]["tool_name"] == "read_file"
