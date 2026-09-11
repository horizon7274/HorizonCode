"""HorizonCode TUI 交互界面。

使用 ``prompt_toolkit`` 处理输入（多行、快捷键）、``rich`` 处理输出渲染（样式、Markdown）。
两者不会同时控制终端：输入时只有 prompt_toolkit 活跃，流式输出时只有 rich 活跃。
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

from horizoncode.tools.base import ToolCall
from horizoncode.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from horizoncode.providers.base import BaseProvider
    from horizoncode.history import HistoryManager

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────────────

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

    负责用户输入、流式渲染 AI 回复、命令处理和历史记录。
    """

    def __init__(
        self,
        provider: "BaseProvider",
        history_manager: "HistoryManager",
        model: str,
        tools: ToolRegistry,
    ) -> None:
        """初始化 TUI。

        Args:
            provider: LLM Provider 实例。
            history_manager: 会话历史管理器。
            model: 当前使用的模型名称（仅用于展示）。
            tools: 本次会话可调用的项目内工具注册中心。
        """
        self._provider = provider
        self._history = history_manager
        self._model = model
        self._tools = tools
        self._console = Console()
        self._session: PromptSession = PromptSession(
            style=PROMPT_STYLE,
            key_bindings=bindings,
            multiline=False,  # Enter 发送，Alt+Enter 换行
        )
        self._streaming_cancelled = False

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
                continue

            # 添加用户消息并获取 AI 回复
            self._history.add("user", user_input)
            self._console.print()  # AI 回复前的空行

            await self._stream_response()

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

    # ── 流式响应 ─────────────────────────────────────────────────────────

    async def _stream_response(self) -> None:
        """流式获取并渲染 AI 回复。

        在流式输出期间临时安装 SIGINT 处理器，使 Ctrl+C 能中断生成
        而不杀死进程。流式结束后恢复原处理器（交给 prompt_toolkit 处理输入）。
        """
        protocol = self._provider.protocol
        api_messages = self._history.get_api_messages(protocol=protocol)
        full_content = ""
        full_thinking = ""
        tool_calls: list[ToolCall] = []
        thinking_displayed = False
        self._streaming_cancelled = False

        # 临时安装取消信号处理器
        old_handler = signal.signal(
            signal.SIGINT,
            lambda _signum, _frame: setattr(self, "_streaming_cancelled", True),
        )

        try:
            async for frame in self._provider.stream_chat(
                api_messages,
                self._model,
                tools=self._tools.definitions_for(protocol),
            ):
                if self._streaming_cancelled:
                    break

                if frame.type == "thinking":
                    if not thinking_displayed:
                        self._console.print()
                        thinking_displayed = True
                    full_thinking += frame.text
                    self._console.print(frame.text, end="", style="dim italic")
                    self._console.file.flush()  # type: ignore[union-attr]

                elif frame.type == "content":
                    full_content += frame.text
                    self._console.print(frame.text, end="")
                    self._console.file.flush()  # type: ignore[union-attr]

                elif frame.type == "error":
                    self._console.print()
                    self._console.print(
                        Panel(
                            Text(frame.text, style="bold red"),
                            title="错误",
                            border_style="red",
                        )
                    )
                    return

                elif frame.type == "tool_call" and frame.tool_call is not None:
                    tool_calls.append(frame.tool_call)

        finally:
            # 恢复原信号处理器
            signal.signal(signal.SIGINT, old_handler)

        # 记录并收尾
        if full_thinking:
            self._history.add("thinking", full_thinking)

        if tool_calls:
            self._history.add_tool_calls(tool_calls, content=full_content)
        elif full_content:
            self._history.add("assistant", full_content)
            self._console.print()  # 回复结束的换行

        if tool_calls:
            self._console.print()
            await self._execute_tool_calls(tool_calls)

        if self._streaming_cancelled:
            self._console.print(
                Text(" [已取消]", style="dim yellow"),
            )

    async def _execute_tool_calls(self, calls: list[ToolCall]) -> None:
        """按模型响应中的原始顺序执行独立调用，并将结果记入历史。"""
        for call in calls:
            self._console.print(
                f"[工具调用] {call.name}",
                style="bold cyan",
            )

            result = await self._tools.execute(call, confirm=self._confirm_command)
            self._history.add_tool_result(call, result)
            style = "green" if result.ok else "yellow"
            self._console.print(f"[工具] {result.summary()}", style=style)

    async def _confirm_command(self, command: str, details: dict) -> bool:
        """在执行命令前显示关键信息并等待用户明确同意。"""
        self._console.print()
        self._console.print(
            Panel(
                f"命令: {command}\n工作目录: {details['cwd']}\n超时: {details['timeout_seconds']} 秒",
                title="模型请求执行命令",
                border_style="yellow",
            )
        )
        answer = await self._session.prompt_async("允许执行？[y/N] ")
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
        else:
            self._console.print(
                Text(f"未知命令: {cmd}。输入 /help 查看可用命令。", style="yellow")
            )
        return False

    # ── 界面渲染 ─────────────────────────────────────────────────────────

    def _print_welcome(self) -> None:
        """打印欢迎面板。"""
        content = Text()
        content.append("HorizonCode v0.2.0\n", style="bold white")
        content.append(f"Model: {self._model}\n", style="dim")
        content.append("/help 查看命令  /exit 退出", style="dim")
        self._console.print(Panel(content, border_style="bold green", padding=(0, 1)))

    def _print_help(self) -> None:
        """打印帮助信息。"""
        help_text = Text()
        help_text.append("可用命令:\n", style="bold underline")
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
        help_text.append("      取消 AI 生成\n")
        help_text.append("  Ctrl+D", style="bold cyan")
        help_text.append("      退出 HorizonCode\n")
        self._console.print(help_text)
