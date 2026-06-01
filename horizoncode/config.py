"""HorizonCode 配置层。

从用户级和项目级路径加载 YAML 配置，支持环境变量替换和多 profile。
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml

# ── 常量 ────────────────────────────────────────────────────────────────────

USER_CONFIG_DIR = Path.home() / ".horizoncode"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.yaml"
LOCAL_CONFIG_PATH = Path.cwd() / ".horizoncode" / "config.yaml"
PROJECT_CONFIG_PATH = Path.cwd() / "horizoncode.yaml"

# Profile 必填字段
REQUIRED_FIELDS = {"protocol", "model", "base_url", "api_key"}
# 环境变量引用模式: ${VAR_NAME}
ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")

# ── 内部辅助 ────────────────────────────────────────────────────────────────


def _substitute_env_vars(value: str) -> str:
    """将字符串中的 ``${ENV_VAR}`` 模式替换为环境变量的值。

    如果环境变量不存在，保留原始模式不做替换。
    """
    if not isinstance(value, str):
        return value

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return ENV_VAR_PATTERN.sub(_replace, value)


def _deep_substitute(obj: Any) -> Any:
    """递归替换对象中所有字符串值里的环境变量引用。"""
    if isinstance(obj, str):
        return _substitute_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _deep_substitute(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_substitute(item) for item in obj]
    return obj


def _deep_merge(base: dict, override: dict) -> dict:
    """递归将 *override* 合并到 *base*，override 中的值优先。"""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_profile(name: str, profile: dict) -> None:
    """校验 profile 是否包含所有必填字段，缺少则抛出 ``ValueError``。"""
    if not isinstance(profile, dict):
        raise ValueError(
            f"Profile '{name}' 必须是一个映射，收到了 {type(profile).__name__}"
        )
    missing = REQUIRED_FIELDS - set(profile.keys())
    if missing:
        raise ValueError(
            f"Profile '{name}' 缺少必填字段: {', '.join(sorted(missing))}"
        )


# ── 公开 API ────────────────────────────────────────────────────────────────


def load_config(project_path: Path | None = None) -> dict:
    """加载并返回合并后的 HorizonCode 配置。

    加载顺序（后者覆盖前者）:
        1. ``~/.horizoncode/config.yaml``（用户级）
        2. ``./.horizoncode/config.yaml``（项目本地配置）
        3. ``./horizoncode.yaml``（项目级）
        4. 通过 ``-c`` 显式指定的 *project_path*（最高优先级）

    Returns:
        字典，包含:
        - ``default_profile``: 当前激活的 profile 名称
        - ``profiles``: profile 名 → {protocol, model, base_url, api_key} 的映射
    """
    config: dict = {"profiles": {}}

    # 1. 用户级配置
    if USER_CONFIG_PATH.exists():
        raw = _read_yaml(USER_CONFIG_PATH)
    else:
        raw = {}

    user_profiles = raw.get("profiles", {})
    if "default_profile" in raw:
        config["default_profile"] = raw["default_profile"]

    # 2. 项目本地配置 (./.horizoncode/config.yaml)
    if LOCAL_CONFIG_PATH.exists():
        local_raw = _read_yaml(LOCAL_CONFIG_PATH)
        local_profiles = local_raw.get("profiles", {})
        user_profiles = _deep_merge(user_profiles, local_profiles)
        if "default_profile" in local_raw:
            config["default_profile"] = local_raw["default_profile"]

    # 3. 项目级配置 (./horizoncode.yaml 或 -c 指定)
    proj_path = project_path or PROJECT_CONFIG_PATH
    if proj_path.exists():
        proj_raw = _read_yaml(proj_path)
        project_profiles = proj_raw.get("profiles", {})
        user_profiles = _deep_merge(user_profiles, project_profiles)
        if "default_profile" in proj_raw:
            config["default_profile"] = proj_raw["default_profile"]

    # 替换环境变量并校验
    config["profiles"] = _deep_substitute(user_profiles)
    for name, profile in config["profiles"].items():
        _validate_profile(name, profile)

    # 未指定 default_profile 时使用第一个 profile
    if "default_profile" not in config and config["profiles"]:
        config["default_profile"] = next(iter(config["profiles"]))

    return config


def get_active_profile(config: dict) -> dict:
    """从 *config* 中返回当前激活的 profile 字典。

    Raises:
        ValueError: 没有配置任何 profile，或 default_profile 不存在时。
    """
    profiles: dict = config.get("profiles", {})
    if not profiles:
        raise ValueError("没有配置任何 profile，请在 config.yaml 中添加至少一个 profile。")

    default = config.get("default_profile")
    if default is None:
        default = next(iter(profiles))
    if default not in profiles:
        raise ValueError(
            f"默认 profile '{default}' 在配置中不存在，"
            f"可用的 profile: {', '.join(sorted(profiles))}"
        )
    return {**profiles[default], "name": default}


def _read_yaml(path: Path) -> dict:
    """读取 YAML 文件并返回解析后的字典（空文件返回空字典）。"""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if data is not None else {}
