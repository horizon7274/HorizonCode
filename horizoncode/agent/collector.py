"""流式双路收集器。

消费 Provider 的流式帧，实现"双路"输出：
  1. 实时路：thinking / content 增量即时转换为 Agent 事件（供界面流式渲染）；
  2. 攒中路：把完整文本、思考、工具调用、用量、错误累积进 :class:`RoundState`，
     供 AgentLoop 在本轮结束后判断走向并写入历史。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator

import asyncio

from horizoncode.agent.events import AgentEvent, TextDeltaEvent, ThinkingDeltaEvent
from horizoncode.providers.base import StreamFrame, Usage
from horizoncode.tools.base import ToolCall


@dataclass
class RoundState:
    """一轮 LLM 流式响应的累积结果，由收集器实时填充。

    属性:
        content: 攒齐的完整正文文本。
        thinking: 攒齐的完整思考文本。
        tool_calls: 本轮解析出的全部工具调用（按模型给出的顺序）。
        usage: 本轮 Token 用量；Provider 未提供时为 ``None``。
        error: 流中出现的错误描述；无错误时为 ``None``。
        cancelled: 因取消信号中断收集时为 ``True``（此时内容是半截的）。
    """

    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None
    error: str | None = None
    cancelled: bool = False


class StreamCollector:
    """把 Provider 流式帧拆分为实时事件与累积状态的双路收集器。

    Args:
        cancel_event: 可选的取消信号；置位后收集器停止消费剩余帧，
            并把 ``RoundState.cancelled`` 标记为 ``True``。
    """

    def __init__(self, cancel_event: asyncio.Event | None = None) -> None:
        self._cancel_event = cancel_event

    async def collect(
        self,
        frames: AsyncIterator[StreamFrame],
        state: RoundState,
    ) -> AsyncIterator[AgentEvent]:
        """消费一轮流式帧：实时产出文本/思考事件，同时填充 *state*。

        Args:
            frames: Provider ``stream_chat`` 返回的异步帧迭代器。
            state: 由调用方创建、收集器填充的累积状态对象。
        """
        async for frame in frames:
            if self._cancel_event is not None and self._cancel_event.is_set():
                state.cancelled = True
                break

            if frame.type == "thinking":
                state.thinking += frame.text
                yield ThinkingDeltaEvent(frame.text)
            elif frame.type == "content":
                state.content += frame.text
                yield TextDeltaEvent(frame.text)
            elif frame.type == "tool_call" and frame.tool_call is not None:
                state.tool_calls.append(frame.tool_call)
            elif frame.type == "error":
                state.error = frame.text
            elif frame.type == "done":
                state.usage = frame.usage
