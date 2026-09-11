"""工具分批调度器：按安全性把一批工具调用分组执行。

规则（与 docs/agent-loop/spec.md 一致）:
  - 一批调用全部为只读工具 → 并发执行；
  - 一批中含任意有副作用的调用（含未知工具，无法证明只读）→ 整批退化为按模型给出的顺序串行执行；
  - 计划模式下，有副作用的调用不执行，直接以失败结果回灌。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from horizoncode.agent.events import AgentEvent, ToolCallStartEvent, ToolResultEvent
from horizoncode.tools.base import (
    BaseTool,
    ConfirmationCallback,
    ToolCall,
    ToolResult,
)
from horizoncode.tools.registry import ToolRegistry


@dataclass
class SchedulerOutcome:
    """一轮调度后的统计结果，由调度器填充、主循环读取。

    属性:
        unknown_tool_count: 本轮调用中未注册工具的次数（供停止条件累计）。
        denied_by_plan: 计划模式下被拒绝的有副作用调用次数。
    """

    unknown_tool_count: int = 0
    denied_by_plan: int = 0


class ToolScheduler:
    """把一轮的工具调用按安全规则分批执行并回灌历史。

    工具调用对应的 ``assistant_tool_calls`` 记录由主循环负责写入；
    调度器只负责执行并把每个 ``tool_result`` 成对写入历史。

    Args:
        registry: 工具注册中心。
        history: 会话历史管理器（结果回灌）。
        confirm: 透传给工具的确认回调（如命令执行的 y/N 确认）。
        plan_mode: 计划模式开关；置位时有副作用的工具一律拒绝执行。
        cancel_event: 取消信号；置位后未开始的调用以"已取消"失败结果成对写入。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        history,  # HistoryManager
        *,
        confirm: ConfirmationCallback | None = None,
        plan_mode: bool = False,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        self._registry = registry
        self._history = history
        self._confirm = confirm
        self._plan_mode = plan_mode
        self._cancel_event = cancel_event

    async def execute_round(
        self,
        calls: list[ToolCall],
        outcome: SchedulerOutcome,
    ) -> AsyncIterator[AgentEvent]:
        """执行一轮的全部工具调用，实时产出开始/结果事件。

        Args:
            calls: 模型本轮请求的工具调用列表。
            outcome: 由调用方创建、调度器填充的统计对象。
        """
        if not calls:
            return

        # 判定整批执行方式：全部可证明只读 → 并发；否则串行
        tools = [self._registry.get(call.name) for call in calls]
        can_parallelize = all(
            tool is not None and tool.read_only for tool in tools
        )

        if can_parallelize:
            async for event in self._run_parallel(calls, outcome):
                yield event
        else:
            async for event in self._run_serial(calls, outcome):
                yield event

    async def _run_parallel(
        self,
        calls: list[ToolCall],
        outcome: SchedulerOutcome,
    ) -> AsyncIterator[AgentEvent]:
        """并发执行一批只读调用，结果按调用顺序产出。"""
        for call in calls:
            yield ToolCallStartEvent(call)

        results = await asyncio.gather(
            *(self._execute_one(call, outcome) for call in calls)
        )
        for call, result in zip(calls, results):
            self._history.add_tool_result(call, result)
            yield ToolResultEvent(result)

    async def _run_serial(
        self,
        calls: list[ToolCall],
        outcome: SchedulerOutcome,
    ) -> AsyncIterator[AgentEvent]:
        """按顺序串行执行一批调用；取消后剩余调用以失败结果成对写入。"""
        for call in calls:
            if self._cancel_event is not None and self._cancel_event.is_set():
                self._record_cancelled(call)
                yield ToolResultEvent(
                    ToolResult.failure(call.name, "cancelled", "用户取消了本次任务")
                )
                continue

            yield ToolCallStartEvent(call)
            result = await self._execute_one(call, outcome)
            self._history.add_tool_result(call, result)
            yield ToolResultEvent(result)

    async def _execute_one(self, call: ToolCall, outcome: SchedulerOutcome) -> ToolResult:
        """执行单个调用，处理计划模式拒绝与未知工具计数。"""
        tool: BaseTool | None = self._registry.get(call.name)

        if self._plan_mode and tool is not None and not tool.read_only:
            outcome.denied_by_plan += 1
            return ToolResult.failure(
                call.name,
                "plan_mode_denied",
                "计划模式下不可用：该工具会修改项目，请先产出计划，等待用户执行 /do",
            )

        result = await self._registry.execute(call, confirm=self._confirm)
        if result.error == "not_found":
            outcome.unknown_tool_count += 1
        return result

    def _record_cancelled(self, call: ToolCall) -> None:
        """为因取消而未执行的调用写入成对的失败结果，保持历史完整。"""
        self._history.add_tool_result(
            call,
            ToolResult.failure(call.name, "cancelled", "用户取消了本次任务"),
        )
