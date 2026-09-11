"""AgentLoop 主循环测试：ReAct 多轮、停止条件、Plan Mode 与历史一致性。"""

import asyncio

import pytest

from horizoncode.agent.events import (
    FINISH_CANCELLED,
    FINISH_COMPLETED,
    FINISH_MAX_ITERATIONS,
    FINISH_STREAM_ERROR,
    FINISH_UNKNOWN_TOOL,
    FinishedEvent,
    TextDeltaEvent,
    ToolResultEvent,
    UsageEvent,
)
from horizoncode.agent.loop import AgentLoop
from horizoncode.history import HistoryManager
from horizoncode.providers.base import BaseProvider, StreamFrame, Usage
from horizoncode.tools.base import BaseTool, ToolCall, ToolDefinition, ToolResult
from horizoncode.tools.registry import ToolRegistry


class ScriptedProvider(BaseProvider):
    """按调用次数返回预设帧序列的假 Provider。"""

    protocol = "openai"

    def __init__(self, scripts: list[list[StreamFrame]]) -> None:
        self.scripts = scripts
        self.calls = 0
        self.requests: list[dict] = []

    async def stream_chat(self, messages, model, tools=None, system=None):
        self.requests.append({"messages": messages, "tools": tools, "system": system})
        index = min(self.calls, len(self.scripts) - 1)
        self.calls += 1
        for frame in self.scripts[index]:
            yield frame


class RecordingTool(BaseTool):
    """可记录执行次数的简单假工具。"""

    def __init__(self, name: str, *, read_only: bool = False) -> None:
        self.definition = ToolDefinition(name=name, description=name, input_schema={})
        self.read_only = read_only
        self.calls: list[dict] = []

    async def execute(self, arguments, *, confirm=None):
        self.calls.append(arguments)
        return ToolResult(self.definition.name, True, {"value": "ok"})


def registry_with(*tools: BaseTool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def finish(events: list) -> FinishedEvent:
    return next(event for event in reversed(events) if isinstance(event, FinishedEvent))


@pytest.mark.asyncio
async def test_loop_reacts_after_tool_result_and_completes():
    """第一轮工具调用后回灌结果，第二轮无工具调用则完成。"""
    tool = RecordingTool("read_file", read_only=True)
    call = ToolCall("c1", "read_file", {"path": "a.py"})
    provider = ScriptedProvider(
        [
            [
                StreamFrame(type="content", text="我先读取文件"),
                StreamFrame(type="tool_call", tool_call=call),
                StreamFrame(type="done", usage=Usage(2, 3)),
            ],
            [
                StreamFrame(type="content", text="文件内容已确认"),
                StreamFrame(type="done", usage=Usage(4, 5)),
            ],
        ]
    )
    history = HistoryManager()
    history.add("user", "读取 a.py")
    loop = AgentLoop(provider, registry_with(tool), history)
    loop.set_model("test-model")

    events = [event async for event in loop.run()]

    assert provider.calls == 2
    assert tool.calls == [{"path": "a.py"}]
    assert finish(events).reason == FINISH_COMPLETED
    assert finish(events).iterations == 2
    assert sum(isinstance(event, UsageEvent) for event in events) == 2
    assert history.messages[-1]["role"] == "assistant"
    assert history.messages[-1]["content"] == "文件内容已确认"
    # 第二轮请求包含 assistant 工具调用与 tool 结果
    assert any(message["role"] == "tool" for message in provider.requests[1]["messages"])


@pytest.mark.asyncio
async def test_loop_stops_at_configured_iteration_limit():
    """持续请求工具时达到 max_iterations 截断。"""
    tool = RecordingTool("read_file", read_only=True)
    call = ToolCall("c", "read_file", {"path": "a.py"})
    provider = ScriptedProvider(
        [[StreamFrame(type="tool_call", tool_call=call), StreamFrame(type="done")]]
    )
    history = HistoryManager()
    history.add("user", "循环")
    loop = AgentLoop(provider, registry_with(tool), history, max_iterations=3)
    loop.set_model("test-model")

    events = [event async for event in loop.run()]

    assert finish(events).reason == FINISH_MAX_ITERATIONS
    assert finish(events).iterations == 3
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_loop_stops_after_three_unknown_tools():
    """未知工具累计三次后终止，不再发起第四轮请求。"""
    calls = [ToolCall(f"c{i}", f"missing_{i}", {}) for i in range(3)]
    scripts = [
        [StreamFrame(type="tool_call", tool_call=call), StreamFrame(type="done")]
        for call in calls
    ]
    provider = ScriptedProvider(scripts)
    history = HistoryManager()
    history.add("user", "测试未知工具")
    loop = AgentLoop(provider, registry_with(), history)
    loop.set_model("test-model")

    events = [event async for event in loop.run()]

    assert finish(events).reason == FINISH_UNKNOWN_TOOL
    assert provider.calls == 3
    assert len(history.get_messages({"tool_result"})) == 3


@pytest.mark.asyncio
async def test_loop_stops_on_stream_error():
    """Provider error 帧转为 stream_error 结束，不写入半截 assistant。"""
    provider = ScriptedProvider(
        [[StreamFrame(type="content", text="半截"), StreamFrame(type="error", text="网络失败")]]
    )
    history = HistoryManager()
    history.add("user", "请求")
    loop = AgentLoop(provider, registry_with(), history)
    loop.set_model("test-model")

    events = [event async for event in loop.run()]

    assert finish(events).reason == FINISH_STREAM_ERROR
    assert finish(events).detail == "网络失败"
    assert not any(message["role"] == "assistant" for message in history.messages)


@pytest.mark.asyncio
async def test_loop_cancel_keeps_tool_calls_paired():
    """取消后已解析工具调用仍写入对应取消结果。"""
    call = ToolCall("c1", "missing", {})
    provider = ScriptedProvider(
        [[StreamFrame(type="tool_call", tool_call=call), StreamFrame(type="done")]]
    )
    history = HistoryManager()
    history.add("user", "取消")
    loop = AgentLoop(provider, registry_with(), history)
    loop.set_model("test-model")
    loop.cancel_event.set()

    events = [event async for event in loop.run()]

    assert finish(events).reason == FINISH_CANCELLED
    assert len(history.get_messages({"assistant_tool_calls"})) == 0


@pytest.mark.asyncio
async def test_plan_mode_filters_tools_and_denies_side_effect_call():
    """计划模式只暴露只读工具，并对幻觉写调用返回失败结果。"""
    reader = RecordingTool("read_file", read_only=True)
    writer = RecordingTool("write_file", read_only=False)
    write_call = ToolCall("w1", "write_file", {"path": "a", "content": "x"})
    provider = ScriptedProvider(
        [
            [StreamFrame(type="tool_call", tool_call=write_call), StreamFrame(type="done")],
            [StreamFrame(type="content", text="计划完成"), StreamFrame(type="done")],
        ]
    )
    history = HistoryManager()
    history.add("user", "制定计划")
    loop = AgentLoop(provider, registry_with(reader, writer), history)
    loop.set_model("test-model")
    loop.plan_mode = True

    events = [event async for event in loop.run()]

    first_request_tools = provider.requests[0]["tools"]
    names = {tool["function"]["name"] for tool in first_request_tools}
    assert names == {"read_file"}
    assert provider.requests[0]["system"] is not None
    assert writer.calls == []
    denied = [event.result for event in events if isinstance(event, ToolResultEvent)]
    assert denied[0].error == "plan_mode_denied"
    assert finish(events).reason == FINISH_COMPLETED
