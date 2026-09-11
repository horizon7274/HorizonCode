"""Agent 循环对外发布的异步事件定义。

AgentLoop 内核运行时产生的所有可观察状态变化都封装为 AgentEvent 子类，
通过异步队列推送给界面层。界面只依赖事件类型，不直接接触 Provider、
工具注册中心或历史管理器——这是 Agent 内核与 TUI 解耦的边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from horizoncode.providers.base import Usage
from horizoncode.tools.base import ToolCall, ToolResult

# 任务结束原因
FINISH_COMPLETED = "completed"  # 模型不再请求工具，任务自然完成
FINISH_MAX_ITERATIONS = "max_iterations"  # 达到迭代上限（兜底截断）
FINISH_CANCELLED = "cancelled"  # 用户取消（Ctrl+C）
FINISH_UNKNOWN_TOOL = "unknown_tool"  # 连续调用未注册工具超过阈值
FINISH_STREAM_ERROR = "stream_error"  # LLM 流式输出出错


@dataclass
class AgentEvent:
    """所有 Agent 事件的基类，仅用于类型分发。"""


@dataclass
class ThinkingDeltaEvent(AgentEvent):
    """模型思考过程的一段增量文本。"""

    text: str


@dataclass
class TextDeltaEvent(AgentEvent):
    """模型回复正文的一段增量文本。"""

    text: str


@dataclass
class ToolCallStartEvent(AgentEvent):
    """模型请求的一次工具调用即将执行。"""

    call: ToolCall


@dataclass
class ToolResultEvent(AgentEvent):
    """一次工具执行结束，携带结构化结果。"""

    result: ToolResult


@dataclass
class IterationEvent(AgentEvent):
    """一轮推理-行动迭代开始。

    属性:
        iteration: 当前轮次，从 1 开始。
        max_iterations: 迭代上限（0 表示未知/不限）。
    """

    iteration: int
    max_iterations: int = 0


@dataclass
class UsageEvent(AgentEvent):
    """Token 用量更新。

    属性:
        round_usage: 本轮响应的用量（Provider 未提供时字段为 None）。
        total_input / total_output: 整个任务累计的输入/输出 Token。
    """

    round_usage: Usage
    total_input: int = 0
    total_output: int = 0


@dataclass
class FinishedEvent(AgentEvent):
    """任务结束，必为事件流最后一个事件。

    属性:
        reason: 结束原因，取值为模块顶部 FINISH_* 常量之一。
        iterations: 实际执行的轮数。
        detail: 面向用户的补充说明（如截断时的进度信息）。
    """

    reason: str
    iterations: int = 0
    detail: str = ""
    total_input: int = 0
    total_output: int = 0
