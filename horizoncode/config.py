"""Configuration layer for HorizonCode.

Loads YAML configuration from user-level and project-level paths,
with environment variable substitution and multi-profile support.
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml

# ── constants ──────────────────────────────────────────────────────────────
USER_CONFIG_DIR = Path.home() / ".horizoncode"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.yaml"
LOCAL_CONFIG_PATH = Path.cwd() / ".horizoncode" / "config.yaml"
PROJECT_CONFIG_PATH = Path.cwd() / "horizoncode.yaml"

REQUIRED_FIELDS = {"protocol", "model", "base_url", "api_key"}
ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")

# ── helpers ────────────────────────────────────────────────────────────────


def _substitute_env_vars(value: str) -> str:
    """Replace ``${ENV_VAR}`` patterns in *value* with environment variable values."""
    if not isinstance(value, str):
        return value

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return ENV_VAR_PATTERN.sub(_replace, value)


def _deep_substitute(obj: Any) -> Any:
    """Recursively substitute env vars in all string values."""
    if isinstance(obj, str):
        return _substitute_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _deep_substitute(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_substitute(item) for item in obj]
    return obj


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*. Override values take precedence."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_profile(name: str, profile: dict) -> None:
    """Raise ``ValueError`` if *profile* is missing required fields."""
    if not isinstance(profile, dict):
        raise ValueError(
            f"Profile '{name}' must be a mapping, got {type(profile).__name__}"
        )
    missing = REQUIRED_FIELDS - set(profile.keys())
    if missing:
        raise ValueError(
            f"Profile '{name}' is missing required field(s): {', '.join(sorted(missing))}"
        )


# ── public API ─────────────────────────────────────────────────────────────


def load_config(project_path: Path | None = None) -> dict:
    """Load and return the merged HorizonCode configuration.

    Resolution order (later overrides earlier):
        1. ``~/.horizoncode/config.yaml`` (user-level)
        2. ``./.horizoncode/config.yaml`` (local project config)
        3. ``./horizoncode.yaml`` (project-level)
        4. *project_path* if explicitly provided (takes highest priority)

    Returns a dict with keys:
        - ``default_profile``: name of the active profile
        - ``profiles``: dict of profile_name → {protocol, model, base_url, api_key}
    """
    config: dict = {"profiles": {}}

    # 1. user-level
    if USER_CONFIG_PATH.exists():
        raw = _read_yaml(USER_CONFIG_PATH)
    else:
        raw = {}

    user_profiles = raw.get("profiles", {})
    if "default_profile" in raw:
        config["default_profile"] = raw["default_profile"]

    # 2. local project config (./.horizoncode/config.yaml)
    if LOCAL_CONFIG_PATH.exists():
        local_raw = _read_yaml(LOCAL_CONFIG_PATH)
        local_profiles = local_raw.get("profiles", {})
        user_profiles = _deep_merge(user_profiles, local_profiles)
        if "default_profile" in local_raw:
            config["default_profile"] = local_raw["default_profile"]

    # 3. project-level (./horizoncode.yaml or explicit -c)
    proj_path = project_path or PROJECT_CONFIG_PATH
    if proj_path.exists():
        proj_raw = _read_yaml(proj_path)
        project_profiles = proj_raw.get("profiles", {})
        user_profiles = _deep_merge(user_profiles, project_profiles)
        if "default_profile" in proj_raw:
            config["default_profile"] = proj_raw["default_profile"]

    # Substitute env vars and validate
    config["profiles"] = _deep_substitute(user_profiles)
    for name, profile in config["profiles"].items():
        _validate_profile(name, profile)

    # Set default_profile if not specified
    if "default_profile" not in config and config["profiles"]:
        config["default_profile"] = next(iter(config["profiles"]))

    return config


def get_active_profile(config: dict) -> dict:
    """Return the currently active profile dict from *config*.

    Raises ``ValueError`` if no profiles are configured or the default_profile
    doesn't exist.
    """
    profiles: dict = config.get("profiles", {})
    if not profiles:
        raise ValueError("No profiles configured. Add at least one profile in config.yaml.")

    default = config.get("default_profile")
    if default is None:
        default = next(iter(profiles))
    if default not in profiles:
        raise ValueError(
            f"Default profile '{default}' not found in configured profiles: "
            f"{', '.join(sorted(profiles))}"
        )
    return {**profiles[default], "name": default}


def _read_yaml(path: Path) -> dict:
    """Read a YAML file and return the parsed dict (or empty dict on empty file)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if data is not None else {}
