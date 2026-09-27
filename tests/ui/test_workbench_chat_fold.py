"""workbench 前端「对话区降噪」契约测试（思考块 / 工具组折叠）。

背景：2026-09 工作台中栏对齐桌面壳降噪口径——任务结束后思考内容收起成一行
标题、本轮连续工具调用聚合成一条框，避免长任务把中栏刷成十几条独立卡片。

工作台前端无构建链与 jsdom 测试环境（DOM 行为测试在桌面壳侧
``jarvis-desktop/test/renderer/components.test.tsx``），故此处对
``assets/app.js`` 与 ``assets/style.css`` 做结构契约断言，防止后续重构把
折叠结构改回展开态、或把选择器/文案改到与桌面壳不一致。实机验收步骤见
``docs/test/TEST_CHECKLIST.md``。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

_ASSETS = Path(__file__).resolve().parents[2] / "agent" / "ui" / "workbench" / "assets"
_APP_JS = (_ASSETS / "app.js").read_text(encoding="utf-8")
_STYLE_CSS = (_ASSETS / "style.css").read_text(encoding="utf-8")


def _fn(source: str, name: str) -> str:
    """截取 app.js 中顶层函数 name 的源码（至首个 4 空格缩进的闭括号）。

    顶层函数体内部块的闭括号缩进均 >= 8 空格，故该边界是可靠的函数尾。
    """
    start = source.index(f"function {name}(")
    end = source.index("\n    }", start)
    return source[start:end]


class TestThinkingFold:
    """思考块折叠：details 结构 + 字数标题 + 本轮结束自动收起。"""

    def test_thinking_renders_as_details_with_char_counter(self) -> None:
        body = _fn(_APP_JS, "appendThinking")
        assert "createElement('details')" in body
        assert "'thinking-block'" in body
        assert "'thinking-body'" in body
        assert "思考过程 · ${thinkingChars} 字" in body

    def test_thinking_text_lives_in_body_only(self) -> None:
        """防回退：全文写在 .thinking-body 内，折叠块本身不再直接承载文本。"""
        assert "thinkingBlock.textContent" not in _APP_JS

    def test_thinking_expands_while_streaming(self) -> None:
        assert "thinkingBlock.open = true;" in _APP_JS

    def test_thinking_auto_collapses_when_assistant_finishes(self) -> None:
        body = _fn(_APP_JS, "finishAssistant")
        assert "thinkingBlock.open = false" in body


class TestToolGroupFold:
    """工具组聚合：延迟成组（单条不包组）、失败标红、全部完成后自动收起一次。"""

    def test_single_tool_card_stays_flat(self) -> None:
        """单条工具不包组：首条先平铺，仅记住锚点等待可能的第二条。"""
        body = _fn(_APP_JS, "registerToolCard")
        assert "if (lastToolCard)" in body
        assert "lastToolCard = card;" in body

    def test_second_consecutive_tool_creates_group_and_recalls_first(self) -> None:
        body = _fn(_APP_JS, "registerToolCard")
        assert "createToolGroup()" in body
        assert "groupBody.appendChild(lastToolCard)" in body

    def test_history_summary_card_never_enters_group(self) -> None:
        """历史回放的「历史工具调用 ×N」汇总卡无 toolUseId，不入组且切断连续性。"""
        body = _fn(_APP_JS, "registerToolCard")
        assert "if (!toolUseId)" in body

    def test_add_tool_card_registers_instead_of_appending_blindly(self) -> None:
        assert "registerToolCard(card, toolUseId);" in _fn(_APP_JS, "addToolCard")

    def test_group_summary_shows_count_failure_and_running(self) -> None:
        body = _fn(_APP_JS, "refreshToolGroup")
        assert "工具调用 ×${total}" in body
        assert "✗${failed} 失败" in body
        assert "执行中：${running}" in body

    def test_group_highlighted_when_any_tool_failed(self) -> None:
        assert "classList.toggle('error', failed > 0)" in _fn(_APP_JS, "refreshToolGroup")

    def test_group_auto_collapses_only_once_when_all_done(self) -> None:
        body = _fn(_APP_JS, "refreshToolGroup")
        assert "done === total" in body
        assert "root.dataset.autoCollapsed !== '1'" in body
        assert "root.open = false" in body

    def test_tool_result_marks_state_and_refreshes_group(self) -> None:
        body = _fn(_APP_JS, "fillToolResult")
        assert "card.dataset.state = isError ? 'error' : 'ok';" in body
        assert "closest('.tool-group')" in body

    def test_tool_card_summary_shows_run_state(self) -> None:
        """单卡标题带状态后缀（… 执行中 / ✓ / ✗），与桌面壳 tool-card 一致。"""
        assert "summary.textContent = `${name} …`;" in _fn(_APP_JS, "addToolCard")
        assert "${card.dataset.name || name} ${isError ? '✗' : '✓'}" in _fn(_APP_JS, "fillToolResult")

    def test_non_tool_content_breaks_tool_run(self) -> None:
        """AI 文本 / 系统提示 / 用户消息上屏即切断连续性，下一批工具另起一组。"""
        for name in ("appendAssistantText", "addSystemMessage", "addUserBubble"):
            assert "endToolRun()" in _fn(_APP_JS, name), name


class TestFoldStyles:
    """折叠相关样式选择器（工作台单皮肤，与桌面壳三主题同名同语义）。"""

    def test_thinking_block_styles(self) -> None:
        assert ".thinking-block > summary" in _STYLE_CSS
        assert ".thinking-block .thinking-body" in _STYLE_CSS
        assert ".thinking-block[open] > summary::before" in _STYLE_CSS

    def test_tool_group_styles(self) -> None:
        for selector in (
            ".tool-group {",
            ".tool-group > summary",
            ".tool-group .tool-group-body",
            ".tool-group .tool-card { margin: 0; }",
            ".tool-group.error",
            ".tool-group-fail",
            ".tool-group-run",
        ):
            assert selector in _STYLE_CSS, selector
