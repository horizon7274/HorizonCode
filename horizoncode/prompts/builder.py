"""结构化系统提示的组合器。"""

from __future__ import annotations

from pathlib import Path

from horizoncode.prompts.environment import EnvironmentInfo, collect_environment
from horizoncode.prompts.models import SystemPrompt, SystemSupplement
from horizoncode.prompts.modules import PromptModule, fixed_modules, render_modules


class PromptBuilder:
    """按优先级构造稳定系统提示和运行时系统补充消息。

    Args:
        project_root: 当前项目根目录。
        custom_instructions: 预留的自定义指令插槽，不负责加载来源。
        active_skills: 预留的 Skill 插槽，不负责发现或激活 Skill。
        long_term_memory: 预留的长期记忆插槽，不负责记忆检索。
    """

    def __init__(
        self,
        project_root: Path,
        *,
        custom_instructions: str | None = None,
        active_skills: str | None = None,
        long_term_memory: str | None = None,
    ) -> None:
        self._project_root = project_root.resolve()
        self._optional = (
            PromptModule("custom-instructions", custom_instructions or ""),
            PromptModule("active-skills", active_skills or ""),
            PromptModule("long-term-memory", long_term_memory or ""),
        )

    def build(
        self,
        *,
        model: str,
        plan_mode: bool,
        round_number: int,
        handoff: bool = False,
        environment: EnvironmentInfo | None = None,
    ) -> SystemPrompt:
        """构造一次模型请求使用的结构化系统提示。"""
        stable_modules = fixed_modules() + tuple(
            module for module in self._optional if module.content.strip()
        )
        stable = render_modules(stable_modules)
        environment_info = environment or collect_environment(self._project_root, model)
        supplements = [
            SystemSupplement("environment", environment_info.render()),
            SystemSupplement(
                "task-mode",
                _task_mode_content(plan_mode=plan_mode, round_number=round_number),
            ),
        ]
        if handoff:
            supplements.append(
                SystemSupplement(
                    "execution-handoff",
                    "用户已经确认此前的计划。现在退出规划阶段，按计划开始执行，必要时重新读取最新文件内容并验证每个修改。",
                )
            )
        return SystemPrompt(stable=stable, supplements=tuple(supplements))


def _task_mode_content(*, plan_mode: bool, round_number: int) -> str:
    """按请求轮次生成完整或精简的当前任务模式提醒。"""
    full = round_number == 1 or (round_number >= 5 and round_number % 5 == 0)
    if plan_mode:
        if full:
            return (
                "当前处于规划模式。只能使用只读工具（read_file、glob_files、grep_code）进行调研，"
                "不得执行任何修改或命令。先充分了解现状，再输出清晰、可执行的计划，等待用户输入 /do。"
            )
        return "仍处于规划模式：只读调研，不修改文件、不执行命令；完成调研后输出计划并等待 /do。"
    if full:
        return (
            "当前处于执行模式。可以使用可见工具完成用户任务，但必须遵守系统约束："
            "专用工具优先，已有文件先读后改，命令仅作后备并等待用户确认，完成后验证结果。"
        )
    return "仍处于执行模式：专用工具优先，已有文件先读后改，命令需确认，修改后验证。"
