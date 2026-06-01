"""Conversation history persistence.

Saves each session as a JSON file under ``~/.horizoncode/sessions/``.
File naming: ``YYYY-MM-DD_HHMMSS_<truncated-first-message>.json``.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# ── constants ──────────────────────────────────────────────────────────────

SESSIONS_DIR = Path.home() / ".horizoncode" / "sessions"
MAX_FILENAME_PREVIEW = 40  # characters from the first user message for the filename

# Characters that are unsafe / annoying in filenames
_FILENAME_SANITIZE_RE = re.compile(r"[^\w\-\s]")


# ── public API ─────────────────────────────────────────────────────────────


class HistoryManager:
    """Manages conversation history for the current session.

    Collects messages during a session and persists them on exit.
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def add(self, role: str, content: str) -> None:
        """Append a message to the in-memory history.

        Args:
            role: One of ``"user"``, ``"assistant"``, ``"thinking"``.
            content: The message text.
        """
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    def get_messages(self, roles: set[str] | None = None) -> list[dict]:
        """Return messages, optionally filtered by *roles* (e.g. ``{"user", "assistant"}``)."""
        if roles is None:
            return list(self.messages)
        return [m for m in self.messages if m["role"] in roles]

    def get_api_messages(self) -> list[dict]:
        """Return messages formatted for the LLM API (user/assistant roles only)."""
        return [
            {"role": m["role"], "content": m["content"]}
            for m in self.messages
            if m["role"] in ("user", "assistant")
        ]

    def save(self) -> Path | None:
        """Persist the current session to a JSON file.

        Returns the path of the saved file, or ``None`` if there are no messages.
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
        """Return a list of saved session files, newest first."""
        if not SESSIONS_DIR.exists():
            return []
        files = sorted(SESSIONS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
        return files

    @staticmethod
    def load_session(filepath: Path) -> dict | None:
        """Load a session from a JSON file. Returns the session dict or ``None``."""
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    # ── internal ────────────────────────────────────────────────────────

    def _make_filename(self) -> str:
        """Generate a filename for the current session."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        # Extract the first user message for the preview
        first_user_msg = ""
        for m in self.messages:
            if m["role"] == "user":
                first_user_msg = m["content"]
                break

        # Sanitize and truncate
        preview = _FILENAME_SANITIZE_RE.sub("", first_user_msg).strip()
        preview = preview[:MAX_FILENAME_PREVIEW].replace(" ", "_")
        if not preview:
            preview = "empty"

        return f"{timestamp}_{preview}.json"
