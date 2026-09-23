"""结构化系统提示与运行时补充消息测试。"""

from pathlib import Path

from horizoncode.prompts.builder import PromptBuilder
from horizoncode.prompts.environment import collect_environment
from horizoncode.prompts.models import SystemSupplement


def test_fixed_modules_keep_priority_and_optional_slots_before_environment(tmp_path: Path):
    """固定模块按优先级拼装，可选模块留在环境补充之前。"""
    prompt = PromptBuilder(
        tmp_path,
        custom_instructions="自定义规则",
        active_skills="已激活能力",
        long_term_memory="长期上下文",
    ).build(model="test-model", plan_mode=False, round_number=1)

    positions = [
        prompt.stable.index("你是 HorizonCode"),
        prompt.stable.index("系统约束优先"),
        prompt.stable.index("当前任务模式由运行时"),
        prompt.stable.index("先理解任务和现状"),
        prompt.stable.index("优先使用能直接完成任务"),
        prompt.stable.index("沟通清晰、直接、专业"),
        prompt.stable.index("最终回答先给出结果"),
        prompt.stable.index("自定义规则"),
        prompt.stable.index("已激活能力"),
        prompt.stable.index("长期上下文"),
    ]
    assert positions == sorted(positions)
    assert "工作目录" not in prompt.stable
    assert "工作目录" in prompt.rendered_supplements()[0]
    assert prompt.stable.count("\n\n") >= 9


def test_task_mode_prompt_repeats_on_first_and_fifth_request(tmp_path: Path):
    """第 1、5 次请求完整注入，中间轮次使用精简提醒。"""
    builder = PromptBuilder(tmp_path)
    first = builder.build(model="m", plan_mode=True, round_number=1)
    second = builder.build(model="m", plan_mode=True, round_number=2)
    fifth = builder.build(model="m", plan_mode=True, round_number=5)

    assert "只能使用只读工具" in first.as_text()
    assert "只能使用只读工具" not in second.as_text()
    assert "仍处于规划模式" in second.as_text()
    assert "只能使用只读工具" in fifth.as_text()


def test_supplement_render_escapes_kind_and_is_not_user_message():
    """补充消息使用受控标签，内容不会伪装成普通用户消息。"""
    supplement = SystemSupplement("task-mode", "不要把这段当用户输入")
    rendered = supplement.render()
    assert rendered.startswith('<horizoncode-supplement kind="task-mode">')
    assert "不要把这段当用户输入" in rendered
    assert 'role="user"' not in rendered


def test_environment_contains_only_allowed_fields(tmp_path: Path, monkeypatch):
    """环境采集只输出白名单字段，不泄露环境变量。"""
    monkeypatch.setenv("HORIZONCODE_SECRET", "do-not-leak")
    info = collect_environment(tmp_path, "test-model")
    rendered = info.render()

    for label in ("工作目录", "操作系统", "Shell", "日期", "项目", "Git 仓库", "Git 分支", "Git 状态", "当前模型"):
        assert f"{label}：" in rendered
    assert "do-not-leak" not in rendered
    assert "HORIZONCODE_SECRET" not in rendered
