"""流式双路收集器测试。"""

import asyncio

import pytest

from horizoncode.agent.collector import RoundState, StreamCollector
from horizoncode.providers.base import StreamFrame, Usage
from horizoncode.tools.base import ToolCall


def _frames(*frames: StreamFrame):
    async def _gen():
        for frame in frames:
            yield frame
    return _gen()


@pytest.mark.asyncio
async def test_collector_streams_events_and_accumulates_state():
    """文本/思考增量实时产出事件，同时攒齐完整内容与工具调用。"""
    call = ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})
    frames = [
        StreamFrame(type="thinking", text="想"),
        StreamFrame(type="content", text="你"),
        StreamFrame(type="content", text="好"),
        StreamFrame(type="tool_call", tool_call=call),
        StreamFrame(type="done", usage=Usage(input_tokens=5, output_tokens=7)),
    ]

    state = RoundState()
    events = [event async for event in StreamCollector().collect(_frames(*frames), state)]

    assert [type(e).__name__ for e in events] == [
        "ThinkingDeltaEvent",
        "TextDeltaEvent",
        "TextDeltaEvent",
    ]
    assert state.content == "你好"
    assert state.thinking == "想"
    assert state.tool_calls == [call]
    assert state.usage == Usage(input_tokens=5, output_tokens=7)
    assert state.error is None
    assert state.cancelled is False


@pytest.mark.asyncio
async def test_collector_captures_error_frame():
    state = RoundState()
    events = [
        event
        async for event in StreamCollector().collect(
            _frames(StreamFrame(type="error", text="boom")), state
        )
    ]
    assert events == []
    assert state.error == "boom"


@pytest.mark.asyncio
async def test_collector_stops_on_cancel_and_marks_state():
    """取消信号置位后停止消费剩余帧，并标记 cancelled。"""
    cancel_event = asyncio.Event()
    cancel_event.set()
    frames = [
        StreamFrame(type="content", text="first"),
        StreamFrame(type="content", text="second"),
    ]

    state = RoundState()
    events = [
        event
        async for event in StreamCollector(cancel_event).collect(_frames(*frames), state)
    ]

    assert events == []  # 一帧都没消费
    assert state.cancelled is True
    assert state.content == ""


@pytest.mark.asyncio
async def test_collector_cancel_mid_stream_keeps_partial_content():
    """流中途取消时保留已收到的半截内容。"""
    cancel_event = asyncio.Event()
    state = RoundState()

    async def _gen():
        yield StreamFrame(type="content", text="partial ")
        cancel_event.set()
        yield StreamFrame(type="content", text="never")

    events = [
        event async for event in StreamCollector(cancel_event).collect(_gen(), state)
    ]

    assert len(events) == 1
    assert state.content == "partial "
    assert state.cancelled is True
