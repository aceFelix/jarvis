"""系统提示（system prompt）单元测试。

覆盖 agent/prompts/system.py 的「交付自查纪律」段：
- build_system_prompt 输出包含交付自查纪律核心要求
- 网页自查要点（布局/对齐/溢出/层级/交互元素）齐备
- 核心原则包含「完成即自查」
- 该段在思考模式开/关两种形态下都存在（思考规范替换不影响它）

背景：先生反馈 Jarvis 写完网页不自查就报完成、布局质量差，
故在 system prompt 中加入强制性自查纪律，此测试锁定关键文案防回归。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.tool import ToolRegistry
from agent.prompts.system import build_system_prompt


@pytest.fixture
def prompt() -> str:
    """默认（思考开启）构建的系统提示。"""
    return build_system_prompt(".", ToolRegistry())


class TestSelfCheckDiscipline:
    """交付自查纪律段内容完整性。"""

    def test_section_exists(self, prompt):
        """交付自查纪律段存在且强调未自查不算完成。"""
        assert "# 交付自查纪律" in prompt
        assert "没有自查的产出不算完成" in prompt

    def test_browser_self_check_required(self, prompt):
        """网页任务必须用内置浏览器工具截图自查（非外部 CLI）。"""
        assert "BrowserNavigate" in prompt
        assert "BrowserScreenshot" in prompt
        assert "亲眼看过" in prompt

    def test_layout_check_points(self, prompt):
        """布局自查要点齐备：对齐、间距、溢出、层级。"""
        for keyword in ("对齐", "间距", "重叠", "层级"):
            assert keyword in prompt, f"自查要点缺少: {keyword}"

    def test_fix_before_report(self, prompt):
        """要求发现问题先修复再报告，且如实汇报。"""
        assert "先修复" in prompt
        assert "如实汇报" in prompt

    def test_code_verification(self, prompt):
        """代码任务要求验证后再报告，无法验证要明说。"""
        assert "未实际运行验证" in prompt

    def test_file_protocol_for_static_pages(self, prompt):
        """静态 HTML 自查指引用 file:// 协议，无需启动 dev server。"""
        assert "file://" in prompt


class TestCorePrinciples:
    """核心原则包含自查要求。"""

    def test_complete_then_check_principle(self, prompt):
        """核心原则第 6 条「完成即自查」。"""
        assert "完成即自查" in prompt


class TestThinkingModeVariants:
    """思考模式开/关两种形态下自查纪律都在。"""

    def test_no_thinking_mode_keeps_section(self):
        """关闭思考模式时，交付自查纪律段不被思考规范替换逻辑误删。"""
        p = build_system_prompt(".", ToolRegistry(), enable_thinking=False)
        assert "# 交付自查纪律" in p
        # 思考规范段被替换为无思考版本
        assert "深度思考已关闭" in p


class _FakeScheduleTool:
    """最小化的 ScheduleReminder 替身，仅用于验证条件注入触发。"""

    name = "ScheduleReminder"
    description = "安排定时提醒"
    input_schema = {"type": "object", "properties": {}}
    deferred = True

    async def call(self, args, ctx):  # pragma: no cover - 不实际调用
        raise NotImplementedError


class TestScheduleSectionConditional:
    """「# 日程提醒」段按环境能力条件化注入。

    终端 CLI 不注册提醒工具，若不条件化，模型会反复 ToolSearch 搜索不存在
    的工具空转（见 fixlogs/schedule-prompt-conditional-fix.md）。
    """

    def test_absent_when_no_schedule_tool(self):
        """未注册提醒工具（终端 CLI 场景）时不注入日程提醒段。"""
        p = build_system_prompt(".", ToolRegistry())
        assert "# 日程提醒" not in p
        assert "ScheduleReminder" not in p

    def test_present_when_schedule_tool_registered(self):
        """注册了提醒工具（serve/桌面宿主场景）时注入日程提醒段。"""
        reg = ToolRegistry()
        reg.register(_FakeScheduleTool())
        p = build_system_prompt(".", reg)
        assert "# 日程提醒" in p
        assert "ScheduleReminder" in p
        assert "ListSchedule" in p
        assert "CancelSchedule" in p


class _FakeTrackGoalTool:
    """最小化的 TrackGoal 替身，仅用于验证目标脊柱段条件注入。"""

    name = "TrackGoal"
    description = "把要等条件的事落成跨会话目标台账"
    input_schema = {"type": "object", "properties": {}}
    deferred = False

    async def call(self, args, ctx):  # pragma: no cover - 不实际调用
        raise NotImplementedError


class TestPromptTextBlocksSplit:
    """静态提示词正文拆到 ``text_blocks.py`` 后的等价护栏。

    拆分动机是单文件 800 行上限（阶段 10 收尾切片），属行为不变的重构：
    提示词逐字节进 LLM 前缀缓存，所以下面四条分别钉住“同一对象”“内容还在”
    “注入条件未坏”“行数不回弹”。
    @author aceFelix
    """

    BLOCKS = (
        "_BASE_PROMPT",
        "_NO_THINKING_SECTION",
        "_SCHEDULE_SECTION",
        "_PLATFORM_WINDOWS",
        "_PLATFORM_MACOS",
        "_PLATFORM_LINUX",
        "_GOAL_SPINE_SECTION",
        "_SELF_CONFIG_SECTION",
    )

    def test_system_reexports_the_same_objects(self):
        """旧引用不碎：``system`` 里的同名常量就是 ``text_blocks`` 里那一个对象。"""
        from agent.prompts import system, text_blocks

        for name in self.BLOCKS:
            assert getattr(system, name) is getattr(text_blocks, name), name

    def test_moved_content_still_reaches_the_prompt(self, prompt):
        """搬走的是正文不是逻辑：人设行与平台段仍然逐字出现在拼装结果里。"""
        assert "你是Jarvis" in prompt
        assert "# 平台操作规范" in prompt
        assert "# 思考规范（思维链）" in prompt

    def test_goal_section_still_conditional(self):
        """拆文件没改变注入口径：没注册 TrackGoal 就不告模型有这台工具。"""
        without = build_system_prompt(".", ToolRegistry())
        assert "# 跨会话目标" not in without

        reg = ToolRegistry()
        reg.register(_FakeTrackGoalTool())
        with_tool = build_system_prompt(".", reg)
        assert "# 跨会话目标" in with_tool

    def test_both_prompt_files_within_line_cap(self):
        """行数上限护栏：两个提示词文件都不得回到 800 行以上。"""
        from agent.prompts import system, text_blocks

        for mod in (system, text_blocks):
            path = Path(mod.__file__)
            lines = path.read_text(encoding="utf-8").splitlines()
            assert len(lines) <= 800, f"{path.name}: {len(lines)} 行"


class _FakeConfigSetTool:
    """最小化的 ConfigSet 替身，仅用于验证自配置段条件注入。"""

    name = "ConfigSet"
    description = "修改 jarvis 的配置项"
    input_schema = {"type": "object", "properties": {}}
    deferred = False

    async def call(self, args, ctx):  # pragma: no cover - 不实际调用
        raise NotImplementedError


class TestSelfConfigSection:
    """「# 调自己的配置」段按工具是否在场条件化注入（阶段 12）。

    子代理的受限工具集里往往没有 ConfigSet（也不该有——它不该替用户改全局
    配置），不判断就会让它去调一个手上没有的工具。同理：禁改沙箱/凭据、
    confirm 项不代用户答复，这几条纪律必须逐字进提示。

    @author aceFelix
    """

    def test_absent_when_no_config_tool(self):
        """默认 ToolRegistry（没有 ConfigSet）时不注入。"""
        p = build_system_prompt(".", ToolRegistry())
        assert "# 调自己的配置" not in p

    def test_present_when_config_tool_registered(self):
        reg = ToolRegistry()
        reg.register(_FakeConfigSetTool())
        p = build_system_prompt(".", reg)
        assert "# 调自己的配置" in p
        assert "ConfigList" in p and "ConfigSet" in p

    def test_section_states_the_three_disciplines(self):
        """三条最容易越界的纪律不能被删：只按明确要求改 / 不碰沙箱凭据 / 不代人确认。"""
        from agent.prompts.text_blocks import _SELF_CONFIG_SECTION as block

        assert "只按用户明确要求改" in block
        assert "沙箱与凭据一律不碰" in block
        assert "confirm" in block and "说三件事" in block

    def test_default_registry_gets_the_section(self):
        """真宿主（默认工具表含两个 Config 工具）里该段必须在。"""
        from agent.core.tool import build_default_registry

        assert "# 调自己的配置" in build_system_prompt(".", build_default_registry())

