"""TUI 计划/执行模式与非历史 handoff 测试。"""

import pytest

from horizoncode.agent.loop import AgentLoop
from horizoncode.history import HistoryManager
from horizoncode.providers.base import BaseProvider, StreamFrame
from rich.console import Console

from horizoncode.tui.app import HorizonTUI
from horizoncode.tools.registry import ToolRegistry


class RecordingProvider(BaseProvider):
    """记录请求并返回一段简单文本的假 Provider。"""

    protocol = "openai"

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def stream_chat(self, messages, model, tools=None, system=None):
        self.requests.append({"messages": messages, "tools": tools, "system": system})
        yield StreamFrame(type="content", text="已执行")
        yield StreamFrame(type="done")


@pytest.mark.asyncio
async def test_do_uses_system_handoff_without_polluting_history():
    """/do 关闭规划模式并通过一次性系统补充传递接力语义。"""
    provider = RecordingProvider()
    history = HistoryManager()
    history.add("user", "先制定计划")
    loop = AgentLoop(provider, ToolRegistry(), history)
    loop.set_model("test-model")
    loop.plan_mode = True
    tui = object.__new__(HorizonTUI)
    tui._history = history
    tui._agent_loop = loop
    tui._pending_run = False
    tui._console = Console()

    tui._handle_do()
    assert loop.plan_mode is False
    assert tui._pending_run is True
    assert history.messages == [{"role": "user", "content": "先制定计划", "timestamp": history.messages[0]["timestamp"]}]

    _ = [event async for event in loop.run()]
    system = provider.requests[0]["system"]
    assert any(s.kind == "execution-handoff" for s in system.supplements)
    assert all(message.get("content") != "请按照上面的计划开始执行。" for message in history.messages)
