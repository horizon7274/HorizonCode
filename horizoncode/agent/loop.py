"""AgentLoop 主循环：ReAct 式自主推理-行动循环。

每轮：取历史 → 调 LLM（流式，双路收集）→ 无工具调用则结束；
有工具调用则分批执行、结果回灌、进入下一轮。实现全部停止条件，
并通过异步事件流对外发布过程。内核不依赖任何界面代码。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from horizoncode.agent.collector import RoundState, StreamCollector
from horizoncode.agent.events import (
    FINISH_CANCELLED,
    FINISH_COMPLETED,
    FINISH_MAX_ITERATIONS,
    FINISH_STREAM_ERROR,
    FINISH_UNKNOWN_TOOL,
    AgentEvent,
    FinishedEvent,
    IterationEvent,
    ToolResultEvent,
    UsageEvent,
)
from horizoncode.agent.scheduler import SchedulerOutcome, ToolScheduler
from horizoncode.history import HistoryManager
from horizoncode.providers.base import BaseProvider, Usage
from horizoncode.tools.base import ConfirmationCallback, ToolResult
from horizoncode.tools.registry import ToolRegistry

# 连续调用未注册工具的终止阈值
UNKNOWN_TOOL_LIMIT = 3

# Plan Mode 下注入给模型的系统提示
PLAN_MODE_SYSTEM_PROMPT = (
    "当前处于计划模式：你只能使用只读工具（读文件、搜索）查看代码，"
    "不能执行任何修改操作。请先充分调研，然后产出一份清晰、可执行的修改计划，"
    "等待用户确认后再实际执行。"
)


class AgentLoop:
    """驱动"LLM ↔ 工具"多轮循环的 Agent 内核。

    用法::

        loop = AgentLoop(provider, registry, history, max_iterations=25)
        async for event in loop.run():
            ...  # 按事件类型渲染

    用户消息应先由调用方写入历史。取消通过 :attr:`cancel_event` 触发
    （如 Ctrl+C 信号处理器中调用 ``cancel_event.set()``），循环会在
    当前步骤后优雅退出，并保证历史中 assistant 调用与工具结果成对。

    Attributes:
        cancel_event: 取消信号，置位后循环在当前步骤后终止。
        plan_mode: 计划模式开关；置位时只向模型暴露只读工具，
            且有副作用的调用一律拒绝执行。
    """

    def __init__(
        self,
        provider: BaseProvider,
        registry: ToolRegistry,
        history: HistoryManager,
        *,
        max_iterations: int = 25,
        unknown_tool_limit: int = UNKNOWN_TOOL_LIMIT,
        confirm: ConfirmationCallback | None = None,
    ) -> None:
        """初始化 Agent 循环。

        Args:
            provider: LLM Provider 实例。
            registry: 工具注册中心。
            history: 会话历史管理器（循环读写）。
            max_iterations: 迭代上限（兜底安全网），达到后强制终止。
            unknown_tool_limit: 连续（任务内累计）调用未注册工具的终止阈值。
            confirm: 透传给工具的确认回调。
        """
        self._provider = provider
        self._registry = registry
        self._history = history
        self._model: str = ""
        self._max_iterations = max(1, max_iterations)
        self._unknown_tool_limit = max(1, unknown_tool_limit)
        # 确认回调公开为属性，界面层在构造后注入（如命令执行的 y/N 确认）
        self.confirm = confirm
        self.cancel_event = asyncio.Event()
        self.plan_mode = False

    def set_model(self, model: str) -> None:
        """设置本轮会话使用的模型名称（在 ``run`` 之前调用）。"""
        self._model = model

    async def run(self) -> AsyncIterator[AgentEvent]:
        """运行自主循环，逐个产出过程事件，以 :class:`FinishedEvent` 结尾。"""
        protocol = self._provider.protocol
        collector = StreamCollector(self.cancel_event)
        total_input = 0
        total_output = 0
        unknown_total = 0
        iterations_done = 0

        for iteration in range(1, self._max_iterations + 1):
            if self.cancel_event.is_set():
                # 顶部取消（上一轮结束后/启动前即已取消）
                yield FinishedEvent(
                    FINISH_CANCELLED,
                    iterations_done,
                    total_input=total_input,
                    total_output=total_output,
                )
                return

            iterations_done = iteration
            yield IterationEvent(iteration, self._max_iterations)

            messages = self._history.get_api_messages(protocol)
            tools = self._registry.definitions_for(protocol, read_only=self.plan_mode)
            system = PLAN_MODE_SYSTEM_PROMPT if self.plan_mode else None

            state = RoundState()
            async for event in collector.collect(
                self._provider.stream_chat(messages, self._model, tools=tools, system=system),
                state,
            ):
                yield event

            # 累计并发布本轮用量
            if state.usage is not None:
                total_input += state.usage.input_tokens or 0
                total_output += state.usage.output_tokens or 0
                yield UsageEvent(state.usage, total_input, total_output)

            # 收集中途被取消：半截内容照常入历史，未执行的调用以取消结果成对写入
            if state.cancelled:
                self._record_interrupted_round(state)
                yield FinishedEvent(
                    FINISH_CANCELLED,
                    iterations_done,
                    total_input=total_input,
                    total_output=total_output,
                )
                return

            # 流式输出出错：不把半截内容写入历史，直接终止
            if state.error is not None:
                yield FinishedEvent(
                    FINISH_STREAM_ERROR,
                    iterations_done,
                    detail=state.error,
                    total_input=total_input,
                    total_output=total_output,
                )
                return

            if state.thinking:
                self._history.add("thinking", state.thinking)

            # 模型不再请求工具 → 任务自然完成
            if not state.tool_calls:
                if state.content:
                    self._history.add("assistant", state.content)
                yield FinishedEvent(
                    FINISH_COMPLETED,
                    iterations_done,
                    total_input=total_input,
                    total_output=total_output,
                )
                return

            # 有工具调用：记录调用、分批执行、结果回灌
            self._history.add_tool_calls(state.tool_calls, content=state.content)
            scheduler = ToolScheduler(
                self._registry,
                self._history,
                confirm=self.confirm,
                plan_mode=self.plan_mode,
                cancel_event=self.cancel_event,
            )
            outcome = SchedulerOutcome()
            async for event in scheduler.execute_round(state.tool_calls, outcome):
                yield event

            unknown_total += outcome.unknown_tool_count
            if unknown_total >= self._unknown_tool_limit:
                yield FinishedEvent(
                    FINISH_UNKNOWN_TOOL,
                    iterations_done,
                    detail=f"模型累计 {unknown_total} 次调用未注册工具",
                    total_input=total_input,
                    total_output=total_output,
                )
                return

            if self.cancel_event.is_set():
                # 调度器已为未执行的调用写入成对的取消结果
                yield FinishedEvent(
                    FINISH_CANCELLED,
                    iterations_done,
                    total_input=total_input,
                    total_output=total_output,
                )
                return

        # 唯一能走出的路径：迭代上限耗尽（其余分支均已 return）
        yield FinishedEvent(
            FINISH_MAX_ITERATIONS,
            self._max_iterations,
            detail=f"已达迭代上限 {self._max_iterations} 轮，输入后续指令（如\"继续\"）可接着执行",
            total_input=total_input,
            total_output=total_output,
        )

    def _record_interrupted_round(self, state: RoundState) -> None:
        """把被取消打断的一轮内容写入历史，并保证调用与结果成对。"""
        if state.thinking:
            self._history.add("thinking", state.thinking)

        if state.tool_calls:
            self._history.add_tool_calls(state.tool_calls, content=state.content)
            for call in state.tool_calls:
                self._history.add_tool_result(
                    call,
                    ToolResult.failure(call.name, "cancelled", "用户取消了本次任务"),
                )
        elif state.content:
            self._history.add("assistant", state.content)
