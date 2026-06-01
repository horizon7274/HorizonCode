"""Tests for the history persistence layer."""

from horizoncode.history import HistoryManager


class TestHistoryManager:
    def test_add_and_get_messages(self):
        hm = HistoryManager()
        hm.add("user", "Hello")
        hm.add("assistant", "Hi there!")

        assert len(hm.messages) == 2
        assert hm.messages[0]["role"] == "user"
        assert hm.messages[0]["content"] == "Hello"

    def test_get_api_messages_filters_thinking(self):
        hm = HistoryManager()
        hm.add("user", "What is 2+2?")
        hm.add("thinking", "Let me think...")
        hm.add("assistant", "4")

        api_msgs = hm.get_api_messages()
        assert len(api_msgs) == 2
        roles = {m["role"] for m in api_msgs}
        assert roles == {"user", "assistant"}

    def test_save_creates_file(self, tmp_path):
        import horizoncode.history as hist_mod
        original_dir = hist_mod.SESSIONS_DIR
        hist_mod.SESSIONS_DIR = tmp_path

        try:
            hm = HistoryManager()
            hm.add("user", "Test message")
            saved = hm.save()
            assert saved is not None
            assert saved.exists()
            assert saved.suffix == ".json"
        finally:
            hist_mod.SESSIONS_DIR = original_dir

    def test_save_empty_returns_none(self):
        hm = HistoryManager()
        assert hm.save() is None

    def test_get_messages_role_filter(self):
        hm = HistoryManager()
        hm.add("user", "Q1")
        hm.add("assistant", "A1")
        hm.add("user", "Q2")

        user_msgs = hm.get_messages(roles={"user"})
        assert len(user_msgs) == 2
        assert all(m["role"] == "user" for m in user_msgs)

    def test_filename_contains_timestamp_and_preview(self, tmp_path):
        import horizoncode.history as hist_mod
        original_dir = hist_mod.SESSIONS_DIR
        hist_mod.SESSIONS_DIR = tmp_path

        try:
            hm = HistoryManager()
            hm.add("user", "Hello world")
            saved = hm.save()
            assert saved is not None
            fname = saved.name
            # Format: YYYY-MM-DD_HHMMSS_<preview>.json
            assert "_Hello_world" in fname or "_hello_world" in fname
            assert "202" in fname  # year
        finally:
            hist_mod.SESSIONS_DIR = original_dir
