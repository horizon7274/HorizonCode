"""Agent 自主循环模块。

包含 ReAct 式循环内核：事件定义（events）、流式双路收集器（collector）、
工具分批调度器（scheduler）与主循环（loop）。内核与 TUI 通过异步事件流解耦。
"""

from horizoncode.agent.events import (
    FinishedEvent,
    IterationEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallStartEvent,
    ToolResultEvent,
    UsageEvent,
    AgentEvent,
)
from horizoncode.agent.loop import AgentLoop

__all__ = [
    "AgentEvent",
    "FinishedEvent",
    "IterationEvent",
    "TextDeltaEvent",
    "ThinkingDeltaEvent",
    "ToolCallStartEvent",
    "ToolResultEvent",
    "UsageEvent",
    "AgentLoop",
]
