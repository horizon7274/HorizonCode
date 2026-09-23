"""HorizonCode TUI 交互界面。

使用 ``prompt_toolkit`` 处理输入（多行、快捷键）、``rich`` 处理输出渲染（样式、Markdown）。
两者不会同时控制终端：输入时只有 prompt_toolkit 活跃，Agent 事件渲染时只有 rich 活跃。

界面不再直接消费 Provider 流，而是订阅 AgentLoop 的异步事件流并按事件类型渲染，
与 Agent 内核彻底解耦。
"""

import logging
import signal
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from horizoncode.agent.events import (
    AgentEvent,
    FinishedEvent,
    IterationEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallStartEvent,
    ToolResultEvent,
    UsageEvent,
    FINISH_CANCELLED,
    FINISH_COMPLETED,
    FINISH_MAX_ITERATIONS,
    FINISH_STREAM_ERROR,
    FINISH_UNKNOWN_TOOL,
)
from horizoncode.agent.loop import AgentLoop

if TYPE_CHECKING:
    from horizoncode.history import HistoryManager

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────────

# /do 切回执行模式时使用的接力语义（由 AgentLoop 作为系统补充注入）
DO_HANDOFF_PROMPT = "请按照上面的计划开始执行。"

# prompt_toolkit 配色：绿色提示符，白色输入文本
PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "bold #00aa00",
        "input": "#ffffff",
    }
)

# ── 快捷键绑定 ──────────────────────────────────────────────────────────────

bindings = KeyBindings()


@bindings.add("escape", "enter")
def _(event: object) -> None:
    """Alt+Enter 在输入中插入换行（多行输入）。"""
    event.app.current_buffer.insert_text("\n")


# ── TUI 应用 ────────────────────────────────────────────────────────────────


class HorizonTUI:
    """HorizonCode 的交互式终端聊天界面。

    负责用户输入、订阅并渲染 Agent 事件流、斜杠命令处理和历史记录。
    """

    def __init__(
        self,
        history_manager: "HistoryManager",
        model: str,
        agent_loop: AgentLoop,
    ) -> None:
        """初始化 TUI。

        Args:
            history_manager: 会话历史管理器。
            model: 当前使用的模型名称（仅用于展示）。
            agent_loop: Agent 循环内核（含工具注册中心与 Provider）。
        """
        self._history = history_manager
        self._model = model
        self._agent_loop = agent_loop
        self._agent_loop.confirm = self._confirm_command
        self._console = Console()
        self._session: PromptSession = PromptSession(
            style=PROMPT_STYLE,
            key_bindings=bindings,
            multiline=False,  # Enter 发送，Alt+Enter 换行
        )
        # 流式文本是否正在输出中（用于在切换事件类型前补换行）
        self._inline_active = False
        # 当前行的流式内容类型：thinking 或 content
        self._inline_kind: str | None = None
        # /do 注入接力提示后置位，输入循环据此运行一次 Agent 循环
        self._pending_run = False

    # ── 公开 API ─────────────────────────────────────────────────────────

    async def run(self) -> None:
        """启动主交互循环。"""
        self._print_welcome()

        while True:
            try:
                user_input = await self._get_input()
            except EOFError:
                # Ctrl+D 退出
                break
            except KeyboardInterrupt:
                # 空输入时 Ctrl+C → 提示退出方式
                self._console.print("\n[dim]使用 /exit 或 Ctrl+D 退出。[/dim]")
                continue

            if user_input is None:
                continue

            user_input = user_input.strip()

            if not user_input:
                continue

            # 处理斜杠命令
            if user_input.startswith("/"):
                should_exit = self._handle_command(user_input)
                if should_exit:
                    break
                # /do 注入接力提示后同步驱动一次完整任务循环
                if self._pending_run:
                    self._pending_run = False
                    await self._run_agent()
                    self._console.print()
                continue

            # 添加用户消息并运行 Agent 自主循环
            self._history.add("user", user_input)
            self._console.print()  # AI 回复前的空行

            await self._run_agent()

            self._console.print()  # AI 回复后的空行

    # ── 输入处理 ─────────────────────────────────────────────────────────

    async def _get_input(self) -> str | None:
        """通过 prompt_toolkit 异步获取用户输入。"""
        try:
            result = await self._session.prompt_async(
                [("class:prompt", "> "), ("class:input", "")],
            )
            return result
        except KeyboardInterrupt:
            # 输入期间 Ctrl+C → 清空缓冲区
            return None

    # ── Agent 循环驱动与事件渲染 ─────────────────────────────────────────

    async def _run_agent(self) -> None:
        """运行 Agent 循环并渲染事件流。

        在循环期间临时安装 SIGINT 处理器：Ctrl+C 置位取消信号，
        循环在当前步骤后优雅退出（历史保持成对完整），随后恢复原处理器。
        """
        self._agent_loop.cancel_event.clear()
        self._inline_active = False
        self._inline_kind = None

        old_handler = signal.signal(
            signal.SIGINT,
            lambda _signum, _frame: self._agent_loop.cancel_event.set(),
        )

        try:
            async for event in self._agent_loop.run():
                self._render_event(event)
        except KeyboardInterrupt:
            # 某些终端/Provider 可能让 Ctrl+C 以异常形式抵达；
            # 思考期间只取消当前任务，不让异常退出 HorizonCode 主循环。
            self._agent_loop.cancel_event.set()
            self._end_inline()
            self._console.print(Text(" [已取消当前任务]", style="dim yellow"))
        finally:
            signal.signal(signal.SIGINT, old_handler)

    def _render_event(self, event: AgentEvent) -> None:
        """按事件类型渲染单个 Agent 事件。"""
        if isinstance(event, ThinkingDeltaEvent):
            self._ensure_inline()
            self._inline_kind = "thinking"
            self._console.print(event.text, end="", style="dim italic")
            self._console.file.flush()  # type: ignore[union-attr]
        elif isinstance(event, TextDeltaEvent):
            # 正式回复必须从思考文字的下一行开始。
            if self._inline_kind == "thinking":
                self._console.print()
                self._inline_kind = None
            self._ensure_inline()
            self._inline_kind = "content"
            self._console.print(event.text, end="")
            self._console.file.flush()  # type: ignore[union-attr]
        elif isinstance(event, ToolCallStartEvent):
            self._end_inline()
            # 工具调用状态单独占一行，避免确认面板紧贴在同一行。
            self._console.print(f"⚙ {event.call.name}", style="bold cyan")
        elif isinstance(event, ToolResultEvent):
            style = "green" if event.result.ok else "yellow"
            self._console.print(f"→ {event.result.summary()}", style=style)
        elif isinstance(event, IterationEvent):
            self._end_inline()
            if event.max_iterations > 0:
                self._console.print(
                    f"[dim]· 第 {event.iteration}/{event.max_iterations} 轮[/dim]"
                )
            else:
                self._console.print(f"[dim]· 第 {event.iteration} 轮[/dim]")
        elif isinstance(event, UsageEvent):
            self._end_inline()
            round_in = event.round_usage.input_tokens
            round_out = event.round_usage.output_tokens
            cache_parts = []
            if event.round_usage.cache_read_input_tokens is not None:
                cache_parts.append(f"缓存读 {event.round_usage.cache_read_input_tokens}")
            if event.round_usage.cache_creation_input_tokens is not None:
                cache_parts.append(f"缓存写 {event.round_usage.cache_creation_input_tokens}")
            if event.round_usage.cached_input_tokens is not None:
                cache_parts.append(f"命中 {event.round_usage.cached_input_tokens}")
            cache_text = f"，{' / '.join(cache_parts)}" if cache_parts else ""
            self._console.print(
                f"[dim]tokens ↑{round_in if round_in is not None else '?'} "
                f"↓{round_out if round_out is not None else '?'}{cache_text}"
                f"（累计 ↑{event.total_input} ↓{event.total_output}）[/dim]"
            )
        elif isinstance(event, FinishedEvent):
            self._end_inline()
            self._render_finished(event)

    def _render_finished(self, event: FinishedEvent) -> None:
        """渲染任务结束状态。"""
        cache_parts = []
        if event.total_cache_read:
            cache_parts.append(f"缓存读 {event.total_cache_read}")
        if event.total_cache_creation:
            cache_parts.append(f"缓存写 {event.total_cache_creation}")
        if event.total_cached:
            cache_parts.append(f"命中 {event.total_cached}")
        cache_text = f"，{' / '.join(cache_parts)}" if cache_parts else ""
        usage = f"[dim]（{event.iterations} 轮，tokens ↑{event.total_input} ↓{event.total_output}{cache_text}）[/dim]"

        if event.reason == FINISH_COMPLETED:
            self._console.print(f"[dim]任务完成{usage}[/dim]")
        elif event.reason == FINISH_CANCELLED:
            self._console.print(Text(" [已取消]", style="dim yellow"))
        elif event.reason == FINISH_MAX_ITERATIONS:
            self._console.print(
                Panel(
                    Text(
                        f"已达到迭代上限（{event.iterations} 轮）。{event.detail}",
                        style="yellow",
                    ),
                    title="任务被截断",
                    border_style="yellow",
                )
            )
        elif event.reason == FINISH_UNKNOWN_TOOL:
            self._console.print(
                Panel(
                    Text(
                        f"模型反复调用不存在的工具，任务已终止。{event.detail}",
                        style="yellow",
                    ),
                    title="任务被截断",
                    border_style="yellow",
                )
            )
        elif event.reason == FINISH_STREAM_ERROR:
            self._console.print(
                Panel(
                    Text(event.detail or "模型输出中断。", style="bold red"),
                    title="错误",
                    border_style="red",
                )
            )

    def _ensure_inline(self) -> None:
        """进入流式文本输出：需要时先补一个换行。"""
        if not self._inline_active:
            self._inline_active = True

    def _end_inline(self) -> None:
        """结束流式文本输出：需要时补换行，让后续行从行首开始。"""
        if self._inline_active:
            self._console.print()
            self._inline_active = False
        self._inline_kind = None

    async def _confirm_command(self, command: str, details: dict) -> bool:
        """在执行命令前显示关键信息并等待用户明确同意。"""
        self._end_inline()
        self._console.print(
            Panel(
                f"命令: {command}\n工作目录: {details['cwd']}\n运行时限: {details['timeout_seconds']} 秒（超时后命令将被终止）",
                title="模型请求执行命令",
                border_style="yellow",
            )
        )
        try:
            answer = await self._session.prompt_async("允许执行？[y/N] ")
        except KeyboardInterrupt:
            # 确认提示中的 Ctrl+C 也只取消当前任务，回到主输入循环。
            self._agent_loop.cancel_event.set()
            self._console.print(Text(" [已取消当前任务]", style="dim yellow"))
            return False
        return answer.strip().lower() in {"y", "yes"}

    # ── 命令处理 ─────────────────────────────────────────────────────────

    def _handle_command(self, text: str) -> bool:
        """处理斜杠命令，返回 ``True`` 表示应退出程序。"""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd == "/exit":
            self._console.print("[dim]再见！[/dim]")
            return True
        elif cmd == "/help":
            self._print_help()
        elif cmd == "/plan":
            self._handle_plan()
        elif cmd == "/do":
            self._handle_do()
        else:
            self._console.print(
                Text(f"未知命令: {cmd}。输入 /help 查看可用命令。", style="yellow")
            )
        return False

    def _handle_plan(self) -> None:
        """切换计划模式：普通 ↔ 计划。

        进入计划模式后，后续对话只向模型开放只读工具；再次输入 /plan 退出。
        """
        self._agent_loop.plan_mode = not self._agent_loop.plan_mode
        if self._agent_loop.plan_mode:
            self._console.print(
                Panel(
                    "已进入计划模式：模型只能使用只读工具调研代码。\n"
                    "描述你的任务，模型会先产出执行计划；确认后输入 /do 开始执行。",
                    title="Plan Mode",
                    border_style="cyan",
                )
            )
        else:
            self._console.print("[dim]已退出计划模式，恢复全部工具。[/dim]")

    def _handle_do(self) -> None:
        """从计划模式切回全工具执行。

        设置一次性的系统级接力补充后，由 run() 检测待执行标志并运行循环
        （命令处理是同步的，实际循环在返回输入前同步驱动）。接力内容不写入历史。
        """
        if not self._agent_loop.plan_mode:
            self._console.print(
                Text("当前不在计划模式，无需 /do。", style="yellow")
            )
            return

        self._agent_loop.plan_mode = False
        self._agent_loop.request_execution_handoff()
        self._pending_run = True
        self._console.print("[cyan]已切回执行模式，开始按计划执行……[/cyan]")
        self._console.print()

    # ── 界面渲染 ─────────────────────────────────────────────────────────

    def _print_welcome(self) -> None:
        """打印欢迎面板。"""
        content = Text()
        content.append("HorizonCode v0.2.0\n", style="bold white")
        content.append(f"Model: {self._model}\n", style="dim")
        content.append("/help 查看命令  /plan 计划模式  /do 执行计划  /exit 退出", style="dim")
        self._console.print(Panel(content, border_style="bold green", padding=(0, 1)))

    def _print_help(self) -> None:
        """打印帮助信息。"""
        help_text = Text()
        help_text.append("可用命令:\n", style="bold underline")
        help_text.append("  /plan", style="bold yellow")
        help_text.append("    进入/退出计划模式（只开放只读工具，先出计划）\n")
        help_text.append("  /do", style="bold yellow")
        help_text.append("      按计划开始执行（恢复全部工具）\n")
        help_text.append("  /exit", style="bold yellow")
        help_text.append("    退出 HorizonCode\n")
        help_text.append("  /help", style="bold yellow")
        help_text.append("    显示此帮助信息\n")
        help_text.append("\n快捷键:\n", style="bold underline")
        help_text.append("  Enter", style="bold cyan")
        help_text.append("       发送消息\n")
        help_text.append("  Alt+Enter", style="bold cyan")
        help_text.append("   插入换行\n")
        help_text.append("  Ctrl+C", style="bold cyan")
        help_text.append("      取消当前任务\n")
        help_text.append("  Ctrl+D", style="bold cyan")
        help_text.append("      退出 HorizonCode\n")
        self._console.print(help_text)
