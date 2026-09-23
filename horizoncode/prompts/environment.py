"""运行环境信息采集与安全格式化。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import platform
import subprocess


@dataclass(frozen=True)
class EnvironmentInfo:
    """允许注入系统提示的有限环境信息。"""

    working_directory: str
    operating_system: str
    shell: str
    current_date: str
    project_name: str
    git_repository: str
    git_branch: str
    git_status: str
    model: str

    def render(self) -> str:
        """将允许的环境字段渲染为稳定、可读的文本。"""
        return "\n".join(
            (
                f"工作目录：{self.working_directory}",
                f"操作系统：{self.operating_system}",
                f"Shell：{self.shell}",
                f"日期：{self.current_date}",
                f"项目：{self.project_name}",
                f"Git 仓库：{self.git_repository}",
                f"Git 分支：{self.git_branch}",
                f"Git 状态：{self.git_status}",
                f"当前模型：{self.model}",
            )
        )


def collect_environment(project_root: Path, model: str) -> EnvironmentInfo:
    """采集当前工作区的允许环境字段，不返回敏感值或完整 diff。"""
    root = project_root.resolve()
    is_repo = _git_output(root, "rev-parse", "--is-inside-work-tree") == "true"
    branch = _git_output(root, "branch", "--show-current") if is_repo else "无"
    status = "干净" if is_repo and not _git_output(root, "status", "--porcelain") else "有未提交变更"
    if not is_repo:
        status = "非 Git 仓库"

    shell = os.environ.get("SHELL") or os.environ.get("ComSpec") or "未知"
    operating_system = f"{platform.system()} {platform.release()}".strip()
    return EnvironmentInfo(
        working_directory=str(root),
        operating_system=operating_system or "未知",
        shell=shell,
        current_date=date.today().isoformat(),
        project_name=root.name or "未知",
        git_repository="是" if is_repo else "否",
        git_branch=branch or "分离 HEAD/未知",
        git_status=status,
        model=model or "未设置",
    )


def _git_output(project_root: Path, *args: str) -> str:
    """执行只读 Git 查询并隐藏命令失败细节。"""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()
