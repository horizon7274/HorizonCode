"""TUI application for HorizonCode.

Uses ``prompt_toolkit`` for input handling and ``rich`` for styled output.
The two libraries never control the terminal simultaneously:
prompt_toolkit is active only during input; rich handles all output rendering.
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

if TYPE_CHECKING:
    from horizoncode.providers.base import BaseProvider
    from horizoncode.history import HistoryManager

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────────

PROMPT_STYLE = Style.from_dict(
    {
        "prompt": "bold #00aa00",
        "input": "#ffffff",
    }
)

# ── key bindings───────────────────────────────────────────────────────────

bindings = KeyBindings()


@bindings.add("escape", "enter")
def _(event: object) -> None:
    """Alt+Enter inserts a newline (multi-line input)."""
    event.app.current_buffer.insert_text("\n")


# ── TUI application ────────────────────────────────────────────────────────


class HorizonTUI:
    """Interactive terminal chat interface for HorizonCode."""

    def __init__(
        self,
        provider: "BaseProvider",
        history_manager: "HistoryManager",
        model: str,
    ) -> None:
        self._provider = provider
        self._history = history_manager
        self._model = model
        self._console = Console()
        self._session: PromptSession = PromptSession(
            style=PROMPT_STYLE,
            key_bindings=bindings,
            multiline=False,  # Enter sends, Alt+Enter inserts newline
        )
        self._streaming_cancelled = False

    # ── public API ─────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the main interaction loop."""
        self._print_welcome()

        while True:
            try:
                user_input = await self._get_input()
            except EOFError:
                # Ctrl+D
                break
            except KeyboardInterrupt:
                # Ctrl+C on empty input → treat as exit
                self._console.print("\n[dim]Use /exit or Ctrl+D to quit.[/dim]")
                continue

            if user_input is None:
                continue

            user_input = user_input.strip()

            if not user_input:
                continue

            # Handle slash commands
            if user_input.startswith("/"):
                should_exit = self._handle_command(user_input)
                if should_exit:
                    break
                continue

            # Add user message to history and process
            self._history.add("user", user_input)
            self._console.print()  # blank line before AI response

            await self._stream_response()

            self._console.print()  # blank line after AI response

    # ── input ──────────────────────────────────────────────────────────

    async def _get_input(self) -> str | None:
        """Get user input via prompt_toolkit (async)."""
        try:
            result = await self._session.prompt_async(
                [("class:prompt", "> "), ("class:input", "")],
            )
            return result
        except KeyboardInterrupt:
            # Ctrl+C during input → clear buffer
            return None

    # ── streaming ─────────────────────────────────────────────────────

    async def _stream_response(self) -> None:
        """Stream the AI response, rendering each chunk in real-time.

        Installs a temporary SIGINT handler so Ctrl+C cancels streaming
        instead of killing the process. Restores the previous handler
        when done (prompt_toolkit needs its own handler during input).
        """
        api_messages = self._history.get_api_messages()
        full_content = ""
        full_thinking = ""
        thinking_displayed = False
        self._streaming_cancelled = False

        # Install cancellation handler for the duration of streaming
        old_handler = signal.signal(
            signal.SIGINT,
            lambda _signum, _frame: setattr(self, "_streaming_cancelled", True),
        )

        try:
            async for frame in self._provider.stream_chat(
                api_messages, self._model
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
                            title="Error",
                            border_style="red",
                        )
                    )
                    return

        finally:
            # Restore previous signal handler for prompt_toolkit
            signal.signal(signal.SIGINT, old_handler)

        # Record and finalize
        if full_thinking:
            self._history.add("thinking", full_thinking)

        if full_content:
            self._history.add("assistant", full_content)
            self._console.print()  # trailing newline

        if self._streaming_cancelled:
            self._console.print(
                Text(" [cancelled]", style="dim yellow"),
            )

    # ── commands ───────────────────────────────────────────────────────

    def _handle_command(self, text: str) -> bool:
        """Handle a slash command. Returns ``True`` if the app should exit."""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()

        if cmd == "/exit":
            self._console.print("[dim]Goodbye![/dim]")
            return True
        elif cmd == "/help":
            self._print_help()
        else:
            self._console.print(
                Text(f"Unknown command: {cmd}. Type /help for available commands.", style="yellow")
            )
        return False

    # ── display ────────────────────────────────────────────────────────

    def _print_welcome(self) -> None:
        """Print the welcome banner using rich Panel for automatic alignment."""
        content = Text()
        content.append("HorizonCode v0.1.0\n", style="bold white")
        content.append(f"Model: {self._model}\n", style="dim")
        content.append("/help for commands  /exit to quit", style="dim")
        self._console.print(
            Panel(content, border_style="bold green", padding=(0, 1))
        )

    def _print_help(self) -> None:
        """Print available commands."""
        help_text = Text()
        help_text.append("Available commands:\n", style="bold underline")
        help_text.append("  /exit", style="bold yellow")
        help_text.append("    Exit HorizonCode\n")
        help_text.append("  /help", style="bold yellow")
        help_text.append("    Show this help message\n")
        help_text.append("\nKeyboard shortcuts:\n", style="bold underline")
        help_text.append("  Enter", style="bold cyan")
        help_text.append("       Send message\n")
        help_text.append("  Alt+Enter", style="bold cyan")
        help_text.append("   Insert newline\n")
        help_text.append("  Ctrl+C", style="bold cyan")
        help_text.append("      Cancel AI generation\n")
        help_text.append("  Ctrl+D", style="bold cyan")
        help_text.append("      Exit HorizonCode\n")
        self._console.print(help_text)


