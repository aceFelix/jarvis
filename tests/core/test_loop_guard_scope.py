"""空转护栏的分寸：打断只留给人该管的事，工具名别名不求模型自觉。

## 起因（2026-11 实测一次会话）

改一个贪吃蛇页面的尺寸，用了 47 步、5 分钟、被 max_iterations 熔断，最后**交付的是
一个坏文件**：CSS 已换成新布局、HTML 还没跟上。复盘出的四处空转：

1. `FileRead` 重复读同一文件被空转检测判成"原地打转"，**逼用户连按三次 y**；
2. 模型把 `FileEdit` 写成 `Edit`、`FileWrite` 写成 `Write`，三次撞"未知工具"；
3. 熔断时只报"完成"，没人知道产物是半成品；
4. 提示词没拦住"为改一段 CSS 写 372 行验证脚本"。

本文件钉住前三条（第 4 条属提示词，另测）。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.core.policy.loop_detect import READ_ONLY_TOOLS, is_read_only


class TestReadOnlyGuard:
    @pytest.mark.parametrize("name", ["FileRead", "Grep", "Glob", "ScreenShot", "ToolSearch"])
    def test_read_tools_are_soft(self, name):
        """重复"看"不该升级成 ASK——那是把人从手上的事里拽出来。"""
        assert is_read_only(name) is True

    @pytest.mark.parametrize("name", ["FileEdit", "FileWrite", "Bash", "BashBackground"])
    def test_write_tools_keep_hard_brake(self, name):
        """反复改、反复执行仍是真风险，刹车保留。"""
        assert is_read_only(name) is False

    def test_unknown_tool_is_not_treated_as_readonly(self):
        """默认保守：没列进表的一律按有风险处理。"""
        assert is_read_only("SomeNewTool") is False

    def test_never_lists_nonexistent_tool(self):
        """表里只放真实存在的工具名——写错名字等于护栏静默失效。"""
        assert "ListDirectory" not in READ_ONLY_TOOLS


class TestToolAliases:
    def test_alias_maps_common_shorthands(self):
        from agent.core.orchestrator import _TOOL_ALIASES

        assert _TOOL_ALIASES["Edit"] == "FileEdit"
        assert _TOOL_ALIASES["Write"] == "FileWrite"
        assert _TOOL_ALIASES["Read"] == "FileRead"
        assert _TOOL_ALIASES["MultiEdit"] == "FileEdit"

    def test_aliases_never_point_at_themselves(self):
        """别名指向自己会变成死循环查表。"""
        from agent.core.orchestrator import _TOOL_ALIASES

        for src, dst in _TOOL_ALIASES.items():
            assert src != dst
