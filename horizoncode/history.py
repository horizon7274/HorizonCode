"""对话历史持久化。

每次会话保存为一个 JSON 文件，存放在 ``~/.horizoncode/sessions/`` 目录下。
文件命名格式: ``YYYY-MM-DD_HHMMSS_<首条用户消息摘要>.json``。
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from horizoncode.tools.base import ToolCall, ToolResult

# ── 常量 ────────────────────────────────────────────────────────────────────

SESSIONS_DIR = Path.home() / ".horizoncode" / "sessions"
MAX_FILENAME_PREVIEW = 40  # 文件名中首条消息的截取长度

# 文件名不安全字符的正则
_FILENAME_SANITIZE_RE = re.compile(r"[^\w\-\s]")


# ── 公开 API ────────────────────────────────────────────────────────────────


class HistoryManager:
    """管理当前会话的对话历史。

    会话期间收集消息，退出时持久化到 JSON 文件。
    """

    def __init__(self) -> None:
        """初始化空的消息列表。"""
        self.messages: list[dict] = []

    def add(self, role: str, content: str) -> None:
        """向内存中的历史记录追加一条消息。

        Args:
            role: 消息角色，取值为 ``"user"``、``"assistant"``、``"thinking"``。
            content: 消息文本内容。
        """
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def get_messages(self, roles: set[str] | None = None) -> list[dict]:
        """返回消息列表，可通过 *roles* 按角色过滤。

        Args:
            roles: 要保留的角色集合（如 ``{"user", "assistant"}``），
                   ``None`` 表示不过滤。
        """
        if roles is None:
            return list(self.messages)
        return [m for m in self.messages if m["role"] in roles]

    def get_api_messages(self, protocol: str | None = None) -> list[dict]:
        """按 Provider 协议返回 API 消息。

        未传入协议时保持 v0.1 的文本消息过滤行为，供旧调用兼容。
        """
        if protocol == "openai":
            return self._get_openai_messages()
        if protocol == "anthropic":
            return self._get_anthropic_messages()
        if protocol == "ollama":
            return self._get_ollama_messages()
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.messages
            if m["role"] in ("user", "assistant")
        ]

    def add_tool_calls(self, calls: list[ToolCall], content: str = "") -> None:
        """记录同一 assistant 响应中的文本和全部工具调用。"""
        self.messages.append(
            {
                "role": "assistant_tool_calls",
                "calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in calls
                ],
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def add_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        """记录一个工具调用及其带关联 ID 的结构化结果。"""
        self.messages.append(
            {
                "role": "tool_result",
                "tool_call_id": call.id,
                "name": call.name,
                "result": result.to_dict(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def save(self) -> Path | None:
        """将当前会话保存到 JSON 文件。

        Returns:
            保存的文件路径，如果没有消息则返回 ``None``。
        """
        if not self.messages:
            return None

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        filename = self._make_filename()
        filepath = SESSIONS_DIR / filename

        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "session_id": filename.replace(".json", ""),
                    "created_at": self.messages[0].get("timestamp", ""),
                    "messages": self.messages,
                },
                fh,
                ensure_ascii=False,
                indent=2,
            )
        return filepath

    @staticmethod
    def list_sessions() -> list[Path]:
        """列出所有已保存的会话文件，按修改时间降序排列。"""
        if not SESSIONS_DIR.exists():
            return []
        files = sorted(SESSIONS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
        return files

    @staticmethod
    def load_session(filepath: Path) -> dict | None:
        """从 JSON 文件加载一个会话，返回会话字典或 ``None``（解析失败时）。"""
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    # ── 内部方法 ─────────────────────────────────────────────────────────

    def _make_filename(self) -> str:
        """根据当前会话内容生成文件名。"""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        # 取首条用户消息作为文件名预览
        first_user_msg = ""
        for m in self.messages:
            if m["role"] == "user":
                first_user_msg = m["content"]
                break

        # 清理并截断
        preview = _FILENAME_SANITIZE_RE.sub("", first_user_msg).strip()
        preview = preview[:MAX_FILENAME_PREVIEW].replace(" ", "_")
        if not preview:
            preview = "empty"

        return f"{timestamp}_{preview}.json"

    def _get_openai_messages(self) -> list[dict[str, Any]]:
        """将内部记录转换为 OpenAI Chat Completions 消息。"""
        messages: list[dict[str, Any]] = []
        for message in self.messages:
            role = message["role"]
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": message["content"]})
            elif role == "assistant_tool_calls":
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content", ""),
                        "tool_calls": [
                            {
                                "id": call["id"],
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                                },
                            }
                            for call in message["calls"]
                        ],
                    }
                )
            elif role == "tool_result":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message["tool_call_id"],
                        "content": json.dumps(message["result"], ensure_ascii=False),
                    }
                )
        return messages

    def _get_anthropic_messages(self) -> list[dict[str, Any]]:
        """将内部记录转换为 Anthropic Messages API 消息。"""
        messages: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []

        def flush_results() -> None:
            if pending_results:
                messages.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for message in self.messages:
            role = message["role"]
            if role == "tool_result":
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message["tool_call_id"],
                        "content": json.dumps(message["result"], ensure_ascii=False),
                    }
                )
                continue
            flush_results()
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": message["content"]})
            elif role == "assistant_tool_calls":
                messages.append(
                    {
                        "role": "assistant",
                        "content": (
                            ([{"type": "text", "text": message["content"]}] if message.get("content") else [])
                            + [
                                {"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["arguments"]}
                                for call in message["calls"]
                            ]
                        ),
                    }
                )
        flush_results()
        return messages

    def _get_ollama_messages(self) -> list[dict[str, Any]]:
        """将内部记录转换为 Ollama 原生聊天 API 消息。"""
        messages: list[dict[str, Any]] = []
        for message in self.messages:
            role = message["role"]
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": message["content"]})
            elif role == "assistant_tool_calls":
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content", ""),
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "index": index,
                                    "name": call["name"],
                                    "arguments": call["arguments"],
                                },
                            }
                            for index, call in enumerate(message["calls"])
                        ],
                    }
                )
            elif role == "tool_result":
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": message["name"],
                        "content": json.dumps(message["result"], ensure_ascii=False),
                    }
                )
        return messages
