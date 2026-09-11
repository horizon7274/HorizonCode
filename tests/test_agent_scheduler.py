"""工具分批调度器测试：并发、串行、混合降级、计划模式与取消。"""

import asyncio
import time

import pytest

from horizoncode.agent.scheduler import SchedulerOutcome, ToolScheduler
from horizoncode.history import HistoryManager
from horizoncode.tools.base import BaseTool, ToolCall, ToolDefinition, ToolResult
from horizoncode.tools.registry import ToolRegistry


class FakeTool(BaseTool):
    """可脚本化的假工具：记录执行顺序，可延迟，可注入副作用。"""

    def __init__(
        self,
        name: str,
        *,
        read_only: bool = False,
        delay: float = 0.0,
        fail: bool = False,
        on_execute=None,
    ) -> None:
        self.definition = ToolDefinition(name=name, description="fake", input_schema={})
        self.read_only = read_only
        self._delay = delay
        self._fail = fail
        self._on_execute = on_execute
        self.executions: list[dict] = []

    async def execute(self, arguments: dict, *, confirm=None) -> ToolResult:
        self.executions.append(arguments)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._on_execute is not None:
            self._on_execute()
        if self._fail:
            return ToolResult.failure(self.definition.name, "boom", "fake failure")
        return ToolResult(self.definition.name, True, {"args": arguments})


def _make_registry(*tools: FakeTool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _calls(*names: str) -> list[ToolCall]:
    return [ToolCall(id=f"call_{i}", name=name, arguments={"i": i}) for i, name in enumerate(names)]


@pytest.mark.asyncio
async def test_read_only_batch_runs_concurrently():
    """三个只读调用并发执行：总耗时明显小于串行。"""
    delay = 0.15
    tools = [FakeTool(f"r{i}", read_only=True, delay=delay) for i in range(3)]
    registry = _make_registry(*tools)
    history = HistoryManager()
    scheduler = ToolScheduler(registry, history)

    start = time.monotonic()
    events = [e async for e in scheduler.execute_round(_calls("r0", "r1", "r2"), SchedulerOutcome())]
    elapsed = time.monotonic() - start

    assert elapsed < delay * 3 * 0.8  # 串行需 ~0.45s，并发应 ~0.15s
    assert sum(1 for e in events if type(e).__name__ == "ToolResultEvent") == 3
    for tool in tools:
        assert len(tool.executions) == 1


@pytest.mark.asyncio
async def test_mixed_batch_degrades_to_serial_in_model_order():
    """读写混合批整体退化为串行，且执行顺序与模型给出的顺序一致。"""
    order: list[str] = []

    def note(name: str):
        def _note():
            order.append(name)
        return _note

    tools = [
        FakeTool("read_a", read_only=True, delay=0.05, on_execute=note("read_a")),
        FakeTool("write_b", delay=0.05, on_execute=note("write_b")),
        FakeTool("read_c", read_only=True, delay=0.05, on_execute=note("read_c")),
    ]
    registry = _make_registry(*tools)
    history = HistoryManager()
    scheduler = ToolScheduler(registry, history)

    events = [
        e
        async for e in scheduler.execute_round(
            _calls("read_a", "write_b", "read_c"), SchedulerOutcome()
        )
    ]

    assert order == ["read_a", "write_b", "read_c"]
    results = [e.result for e in events if type(e).__name__ == "ToolResultEvent"]
    assert [r.tool_name for r in results] == ["read_a", "write_b", "read_c"]
    # 结果成对写入历史
    assert len(history.get_messages({"tool_result"})) == 3


@pytest.mark.asyncio
async def test_unknown_tool_counted_and_returned():
    """未注册工具返回 not_found 并计数，不抛异常。"""
    registry = _make_registry(FakeTool("read_a", read_only=True))
    history = HistoryManager()
    scheduler = ToolScheduler(registry, history)

    outcome = SchedulerOutcome()
    events = [e async for e in scheduler.execute_round(_calls("nope"), outcome)]

    outcome_result = [e.result for e in events if type(e).__name__ == "ToolResultEvent"][0]
    assert outcome_result.ok is False
    assert outcome_result.error == "not_found"
    assert outcome.unknown_tool_count == 1


@pytest.mark.asyncio
async def test_plan_mode_denies_side_effect_tools():
    """计划模式下有副作用的调用被拒绝，只读调用正常执行。"""
    tools = [FakeTool("read_a", read_only=True), FakeTool("write_b")]
    registry = _make_registry(*tools)
    history = HistoryManager()
    scheduler = ToolScheduler(registry, history, plan_mode=True)

    outcome = SchedulerOutcome()
    events = [e async for e in scheduler.execute_round(_calls("read_a", "write_b"), outcome)]

    results = [e.result for e in events if type(e).__name__ == "ToolResultEvent"]
    assert results[0].ok is True
    assert results[1].ok is False
    assert results[1].error == "plan_mode_denied"
    assert "计划模式下不可用" in results[1].message
    assert outcome.denied_by_plan == 1
    assert tools[1].executions == []  # 未实际执行


@pytest.mark.asyncio
async def test_cancel_writes_paired_failure_results():
    """取消后未执行的调用以失败结果成对写入历史，不留孤儿调用。"""
    cancel_event = asyncio.Event()

    def trigger_cancel():
        cancel_event.set()

    tools = [
        FakeTool("write_a", on_execute=trigger_cancel),
        FakeTool("write_b"),
        FakeTool("write_c"),
    ]
    registry = _make_registry(*tools)
    history = HistoryManager()
    scheduler = ToolScheduler(registry, history, cancel_event=cancel_event)

    calls = _calls("write_a", "write_b", "write_c")
    events = [e async for e in scheduler.execute_round(calls, SchedulerOutcome())]

    results = [e.result for e in events if type(e).__name__ == "ToolResultEvent"]
    assert [r.tool_name for r in results] == ["write_a", "write_b", "write_c"]
    assert results[0].ok is True
    assert results[1].error == "cancelled"
    assert results[2].error == "cancelled"
    assert tools[1].executions == []
    assert tools[2].executions == []
    # 历史：每个调用都有成对结果
    tool_results = history.get_messages({"tool_result"})
    assert [m["tool_call_id"] for m in tool_results] == [c.id for c in calls]
