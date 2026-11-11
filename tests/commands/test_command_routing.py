"""斜杠命令路由的护栏测试。

钉的是**带参数命令的路由路径**：精确匹配只看整行，带参数必须靠前缀表接住，
否则参数被静默丢弃——2026-11-11 实测 `/cost --raw` 无任何输出，根因就在这里。

@author aceFelix
"""

from __future__ import annotations

from agent.commands.handlers.core_commands import handle_cost
from agent.commands.router import COMMAND_HANDLERS, PREFIX_HANDLERS


class TestCostRouting:
    """/cost 的两种写法都必须能到达同一个处理器。"""

    def test_bare_cost_is_registered_exactly(self):
        assert COMMAND_HANDLERS.get("/cost") is handle_cost

    def test_cost_alias_registered(self):
        assert COMMAND_HANDLERS.get("/c") is handle_cost

    def test_cost_with_args_is_registered_as_prefix(self):
        """/cost --raw 只能靠前缀表命中。"""
        assert ("/cost ", handle_cost) in PREFIX_HANDLERS

    def test_prefix_requires_trailing_space(self):
        """前缀带空格才不会吞掉 /costly 这类同前缀命令。"""
        assert "/costly".startswith("/cost") is True
        assert "/costly".startswith("/cost ") is False

    def test_prefix_matches_flag_forms(self):
        for line in ("/cost --raw", "/cost  --raw", "/cost --raw extra"):
            matched = next(
                (h for p, h in PREFIX_HANDLERS if line.lower().startswith(p)),
                None,
            )
            assert matched is handle_cost, line
