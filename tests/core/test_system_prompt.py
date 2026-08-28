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
