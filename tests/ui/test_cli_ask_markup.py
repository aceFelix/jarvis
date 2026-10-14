# -*- coding: utf-8 -*-
"""权限确认提示被 Rich 标记吞掉方括号的回归测试。

背景：RichCLI.ask_user 把 prompt 直接拼进 Rich 标记传给 Prompt.ask，
prompt 里的 [y]/[a]（以及工具参数 JSON 的数组方括号）会被 Rich 当作
样式标签整体吞掉——屏幕上「输入 [y] 允许 / [a] 总是允许」的按键名丢失。
修复：prompt 先经 markup.escape 转义；prompt_suffix="" 消除双冒号。
同族问题：warn/error 未转义时，/diff 在非 git 目录把 git usage（内含
`[=<n>[/<m>]]`）透传进 ui.warn 直接抛 MarkupError 崩掉整个 REPL（2026-10）。

作者：aceFelix
"""
from __future__ import annotations

import io

import pytest

rich = pytest.importorskip("rich")

from rich.console import Console  # noqa: E402

import agent.ui.cli as cli_mod  # noqa: E402
from agent.ui.cli import RichCLI  # noqa: E402


class _Utf8Buffer(io.StringIO):
    """固定 UTF-8 编码的渲染 buffer（避免 Windows 控制台编码干扰断言）。"""

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"


def _capture_prompt_ask(monkeypatch) -> dict:
    """打桩 Prompt.ask：记录渲染参数并固定返回 "y"。

    不触碰真实 stdin，只验证 ask_user 交给 Rich 的标记字符串是否正确。
    """
    calls: dict = {}

    def fake_ask(text, *args, **kwargs):  # noqa: ANN001, ANN202
        calls["text"] = text
        calls["kwargs"] = kwargs
        return "y"

    monkeypatch.setattr(cli_mod.Prompt, "ask", staticmethod(fake_ask))
    return calls


def _render(markup: str) -> str:
    """把 Rich 标记字符串渲染成纯文本输出（与终端实际显示一致）。"""
    buf = _Utf8Buffer()
    Console(file=buf, width=120, force_terminal=False).print(markup, end="")
    return buf.getvalue()


def test_ask_user_preserves_bracket_key_hints(monkeypatch):
    """确认行的 [y]/[a] 应原样显示——修复前会被 Rich 当样式标签吞掉。"""
    calls = _capture_prompt_ask(monkeypatch)
    ui = RichCLI(boot_animation=False)
    answer = ui.ask_user("输入 [y] 允许 / [a] 总是允许 / 其他键拒绝: ")
    assert answer == "y"
    out = _render(calls["text"])
    assert "[y]" in out and "[a]" in out


def test_ask_user_survives_json_brackets(monkeypatch):
    """工具参数 JSON 含数组方括号时不报错，且内容完整保留。"""
    calls = _capture_prompt_ask(monkeypatch)
    ui = RichCLI(boot_animation=False)
    prompt = '工具 Bash 请求执行:\n{\n  "paths": ["a", "b"]\n}\n\n输入 [y] 允许: '
    ui.ask_user(prompt)
    out = _render(calls["text"])
    assert '["a", "b"]' in out


def test_ask_user_no_double_colon_suffix(monkeypatch):
    """去掉 prompt 自带的结尾冒号，由 Prompt.ask 默认 ": " 后缀补回（单冒号）。"""
    calls = _capture_prompt_ask(monkeypatch)
    ui = RichCLI(boot_animation=False)
    ui.ask_user("确认执行: ")
    # 标记文本不应以 ": " 结尾（否则默认后缀叠加成双冒号）
    assert not calls["text"].startswith("[bold magenta]❓ 确认执行: ")
    out = _render(calls["text"])
    assert out.rstrip().endswith("确认执行")


def test_ask_user_kwargs_match_real_prompt_signature(monkeypatch):
    """签名守卫：ask_user 实际传给 Prompt.ask 的关键字参数必须都存在于
    真实 rich API 中——prompt_suffix 在部分 rich 版本不存在，传了直接
    TypeError 炸掉权限确认（用户实测报出的回归，必须有此守卫）。"""
    import inspect

    # 真实签名必须在打桩前取（打桩后 Prompt.ask 已是替身函数）
    real_params = set(inspect.signature(cli_mod.Prompt.ask).parameters)
    calls = _capture_prompt_ask(monkeypatch)
    ui = RichCLI(boot_animation=False)
    ui.ask_user("确认执行: ")
    for key in calls["kwargs"]:
        assert key in real_params, f"Prompt.ask 不支持参数 {key}"


# ---- warn / error 的标记安全（/diff 非 git 目录崩溃案，2026-10）----

class _ConsoleHarness:
    """用独立 buffer 的 Console 替换 RichCLI._console，捕获实际渲染。"""

    def __init__(self) -> None:
        self.buf = _Utf8Buffer()
        self.console = Console(file=self.buf, width=120, force_terminal=False)

    def feed(self, ui: RichCLI) -> None:
        ui._console = self.console

    def text(self) -> str:
        return self.buf.getvalue()


_GIT_USAGE_SNIPPET = "usage: git diff --no-index [<options>] <path> <path>\n    -B, --break-rewrites[=<n>[/<m>]]"


def test_warn_survives_git_usage_brackets():
    """git 报错文本含 [=<n>[/<m>]] 时 warn 不崩溃且内容原样显示
    （修复前：Rich 把 [/ 开头方括号当闭合标签抛 MarkupError 直接崩掉 REPL）。"""
    harness = _ConsoleHarness()
    ui = RichCLI(boot_animation=False)
    harness.feed(ui)
    ui.warn(f"git diff 失败: {_GIT_USAGE_SNIPPET}")
    out = harness.text()
    assert "[=<n>[/<m>]]" in out


def test_error_survives_markup_error_message():
    """异常消息本身含闭合标记（如 MarkupError 的报信）时 error 不能二次崩溃。"""
    harness = _ConsoleHarness()
    ui = RichCLI(boot_animation=False)
    harness.feed(ui)
    ui.error("/diff 执行失败: MarkupError: closing tag '[/<m>]' at position 40 doesn't match any open tag")
    out = harness.text()
    assert "'[/<m>]'" in out


def test_warn_error_plain_text_unaffected():
    """普通文案不受转义影响，内容完整、前缀符号保留。"""
    harness = _ConsoleHarness()
    ui = RichCLI(boot_animation=False)
    harness.feed(ui)
    ui.warn("端口号无效: abc")
    ui.error("找不到配置文件")
    out = harness.text()
    assert "端口号无效: abc" in out and "找不到配置文件" in out
    assert "⚠️" in out and "❌" in out
