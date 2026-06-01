"""Tests for the config layer (no API keys needed)."""

import os
import tempfile
from pathlib import Path

import yaml
import pytest

from horizoncode.config import (
    load_config,
    get_active_profile,
    _substitute_env_vars,
    _deep_merge,
    _validate_profile,
)


class TestEnvVarSubstitution:
    def test_simple_substitution(self):
        os.environ["H_TEST_KEY"] = "sk-test-abc123"
        result = _substitute_env_vars("${H_TEST_KEY}")
        assert result == "sk-test-abc123"

    def test_no_substitution_for_plain_string(self):
        result = _substitute_env_vars("plain-key-no-env")
        assert result == "plain-key-no-env"

    def test_unset_env_var_keeps_pattern(self):
        result = _substitute_env_vars("${NONEXISTENT_VAR_12345}")
        assert result == "${NONEXISTENT_VAR_12345}"


class TestDeepMerge:
    def test_override_scalar(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 99}

    def test_merge_nested(self):
        base = {"profiles": {"a": {"model": "gpt-4"}, "b": {"model": "claude"}}}
        override = {"profiles": {"a": {"model": "gpt-4o"}}}
        result = _deep_merge(base, override)
        assert result["profiles"]["a"]["model"] == "gpt-4o"
        assert result["profiles"]["b"]["model"] == "claude"

    def test_new_key_added(self):
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}


class TestValidateProfile:
    def test_valid_profile(self):
        _validate_profile("test", {
            "protocol": "openai",
            "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
        })

    def test_missing_field_raises(self):
        with pytest.raises(ValueError, match="missing"):
            _validate_profile("bad", {
                "protocol": "openai",
                "model": "gpt-4o",
            })

    def test_missing_multiple_fields(self):
        with pytest.raises(ValueError, match="api_key"):
            _validate_profile("bad", {
                "protocol": "openai",
            })


class TestLoadConfig:
    def test_load_with_env_var_substitution(self, tmp_path, monkeypatch):
        """Config should substitute ${VAR} in api_key."""
        monkeypatch.setenv("MY_SECRET", "sk-secret-999")

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({
            "profiles": {
                "test": {
                    "protocol": "openai",
                    "model": "gpt-4o",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "${MY_SECRET}",
                }
            }
        }))

        # Patch the user config path to use our temp file
        import horizoncode.config as cfg
        original = cfg.USER_CONFIG_PATH
        cfg.USER_CONFIG_PATH = config_file
        try:
            result = load_config(project_path=tmp_path / "nonexistent.yaml")
            assert result["profiles"]["test"]["api_key"] == "sk-secret-999"
        finally:
            cfg.USER_CONFIG_PATH = original

    def test_missing_required_field_raises(self, tmp_path):
        """A profile missing api_key should raise ValueError."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({
            "profiles": {
                "bad": {
                    "protocol": "openai",
                    "model": "gpt-4o",
                    "base_url": "https://api.openai.com/v1",
                    # missing api_key
                }
            }
        }))

        import horizoncode.config as cfg
        original = cfg.USER_CONFIG_PATH
        cfg.USER_CONFIG_PATH = config_file
        try:
            with pytest.raises(ValueError, match="missing"):
                load_config(project_path=tmp_path / "nonexistent.yaml")
        finally:
            cfg.USER_CONFIG_PATH = original

    def test_project_overrides_user(self, tmp_path):
        """Project-level config should override user-level fields."""
        user_file = tmp_path / "user_config.yaml"
        proj_file = tmp_path / "project_config.yaml"

        user_file.write_text(yaml.dump({
            "default_profile": "main",
            "profiles": {
                "main": {
                    "protocol": "openai",
                    "model": "gpt-4o",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-user",
                }
            }
        }))

        proj_file.write_text(yaml.dump({
            "profiles": {
                "main": {
                    "model": "gpt-4o-mini",  # override model
                }
            }
        }))

        import horizoncode.config as cfg
        orig_user = cfg.USER_CONFIG_PATH
        orig_proj = cfg.PROJECT_CONFIG_PATH
        cfg.USER_CONFIG_PATH = user_file
        try:
            result = load_config(project_path=proj_file)
            assert result["profiles"]["main"]["model"] == "gpt-4o-mini"
            assert result["profiles"]["main"]["protocol"] == "openai"  # not overridden
        finally:
            cfg.USER_CONFIG_PATH = orig_user
            cfg.PROJECT_CONFIG_PATH = orig_proj


class TestGetActiveProfile:
    def test_returns_profile_with_name(self):
        config = {
            "default_profile": "main",
            "profiles": {
                "main": {
                    "protocol": "openai",
                    "model": "gpt-4o",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-test",
                }
            }
        }
        profile = get_active_profile(config)
        assert profile["protocol"] == "openai"
        assert profile["name"] == "main"

    def test_no_profiles_raises(self):
        with pytest.raises(ValueError, match="No profiles"):
            get_active_profile({"profiles": {}})

    def test_unknown_default_raises(self):
        with pytest.raises(ValueError, match="not found"):
            get_active_profile({
                "default_profile": "nonexistent",
                "profiles": {
                    "main": {
                        "protocol": "openai",
                        "model": "gpt-4o",
                        "base_url": "https://api.openai.com/v1",
                        "api_key": "sk-test",
                    }
                }
            })
