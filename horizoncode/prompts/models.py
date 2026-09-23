"""系统提示在稳定缓存区和动态补充区之间使用的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
import html
import re


_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class SystemSupplement:
    """一条不写入会话历史的系统级动态补充消息。

    Args:
        kind: 补充消息类型，例如 ``environment`` 或 ``task-mode``。
        content: 提供给模型的补充内容。
    """

    kind: str
    content: str

    def __post_init__(self) -> None:
        """校验补充消息类型，避免破坏特殊标签结构。"""
        if not _KIND_PATTERN.fullmatch(self.kind):
            raise ValueError(f"补充消息类型无效: {self.kind!r}")

    def render(self) -> str:
        """将补充消息渲染为带类型标签的系统文本。"""
        kind = html.escape(self.kind, quote=True)
        return (
            f'<horizoncode-supplement kind="{kind}">\n'
            f"{self.content}\n"
            "</horizoncode-supplement>"
        )


@dataclass(frozen=True)
class SystemPrompt:
    """描述稳定系统内容与运行时系统补充的结构化提示。

    稳定内容用于构造缓存前缀，补充消息必须在缓存边界之后发送。

    Args:
        stable: 稳定的系统提示正文。
        supplements: 不参与稳定缓存的动态系统补充。
    """

    stable: str
    supplements: tuple[SystemSupplement, ...] = ()

    def rendered_supplements(self) -> tuple[str, ...]:
        """返回按顺序渲染后的动态补充文本。"""
        return tuple(supplement.render() for supplement in self.supplements)

    def as_text(self) -> str:
        """将结构化提示合并为兼容 Ollama 的单段系统文本。"""
        parts = [part for part in (self.stable, *self.rendered_supplements()) if part]
        return "\n\n".join(parts)
