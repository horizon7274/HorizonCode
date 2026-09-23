"""结构化提示接入 AgentLoop 的测试。"""

import pytest

from horizoncode.agent.events import FinishedEvent, UsageEvent
from horizoncode.agent.loop import AgentLoop
from horizoncode.history import HistoryManager
from horizoncode.providers.base import BaseProvider, StreamFrame, Usage
from horizoncode.tools.registry import ToolRegistry


class UsageProvider(BaseProvider):
    """返回预设用量并记录每轮结构化提示的假 Provider。"""

    protocol = "openai"

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[dict] = []

    async def stream_chat(self, messages, model, tools=None, system=None):
        self.requests.append({"messages": messages, "tools": tools, "system": system})
        self.calls += 1
        if self.calls == 1:
            yield StreamFrame(type="content", text="完成")
            yield StreamFrame(
                type="done",
                usage=Usage(input_tokens=10, output_tokens=2, cached_input_tokens=4),
            )
        else:
            yield StreamFrame(type="done", usage=Usage(input_tokens=3, output_tokens=1))


@pytest.mark.asyncio
async def test_agent_loop_carries_cache_usage_and_structured_prompt():
    """AgentLoop 传递结构化提示并累计缓存命中用量。"""
    provider = UsageProvider()
    history = HistoryManager()
    history.add("user", "完成任务")
    loop = AgentLoop(provider, ToolRegistry(), history)
    loop.set_model("test-model")

    events = [event async for event in loop.run()]
    usage_events = [event for event in events if isinstance(event, UsageEvent)]
    finished = next(event for event in events if isinstance(event, FinishedEvent))

    assert provider.requests[0]["system"].stable
    assert provider.requests[0]["system"].supplements
    assert usage_events[0].total_cached == 4
    assert finished.total_cached == 4
