"""LSP 结果格式化函数的回归测：URI → 路径这条链必须真的能跑。

2026-11-05 用 `ruff check --select F821` 扫全仓时撞出来的第二起同类事故：
`agent/tools/extensions/lsp_tool.py` 里 `_format_result()` 写了**函数内**的
`from agent.lsp.client import uri_to_path`，而真正用这个名字的是四个模块级函数
（`_format_location` / `_format_call_items` / `_format_incoming_calls` /
`_format_outgoing_calls`）。函数内导入只对导入所在的那个函数可见，所以分发器自己
不崩、四个 callee 一碰到带 `uri` 的结果就 NameError。

也就是说：只要用户启用了 `[lsp]` 并让模型跳一次定义/查一次引用，LSP 工具就必然报错，
而这条错误还会被自愈层当成可重试错误反复重试。此前没有任何用例覆盖它——`tests/` 下
连一个 lsp 相关测都没有（`glob tests/**/*lsp*` 为 0）。

URI 一律用 `path_to_uri()` 现场生成，不用硬编码的 `file:///E:/...`，这样 Linux CI
上也能跑（`uri_to_path` 对盘符的处理是平台相关的）。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.lsp.client import path_to_uri
from agent.tools.extensions import lsp_tool


def _loc(path: Path, line: int = 41) -> dict:
    """造一个 LSP Location（line 用 0 基，输出应显示 +1）。"""
    return {"uri": path_to_uri(str(path)), "range": {"start": {"line": line, "character": 4}}}


def test_module_level_import_of_uri_to_path() -> None:
    """`uri_to_path` 必须是模块级名字。

    这条直接钉住根因：函数内导入只对那个函数可见，callee 拿不到。写成模块级
    才是四个格式化函数共享的事实。
    """
    assert getattr(lsp_tool, "uri_to_path", None) is not None, (
        "uri_to_path 不在 lsp_tool 的模块命名空间里，四个格式化函数会重新 NameError"
    )


def test_format_location_absolute_path(tmp_path: Path) -> None:
    """不在工作区内的 Location 念绝对路径，行号 +1。"""
    target = tmp_path / "elsewhere.py"
    out = lsp_tool._format_location(_loc(target, line=41), cwd=str(tmp_path / "not_here"))

    assert "elsewhere.py" in out
    assert ":42" in out, f"行号没做 0 基 → 1 基转换：{out!r}"


def test_format_location_prefers_relative_path_inside_workdir(tmp_path: Path) -> None:
    """工作区内的 Location 折成相对路径——模型据此才能直接 Read/Edit。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    target = pkg / "mod.py"

    out = lsp_tool._format_location(_loc(target), cwd=str(tmp_path))

    assert "pkg" in out and "mod.py" in out
    assert str(tmp_path) not in out, f"工作区内的结果不该带绝对前缀：{out!r}"


def test_format_locations_survives_missing_uri(tmp_path: Path) -> None:
    """服务端返回没有 uri 的残缺结果时不许崩，退成 <unknown>。"""
    out = lsp_tool._format_location({"range": {"start": {"line": 3}}}, cwd=str(tmp_path))

    assert "<unknown>" in out


@pytest.mark.parametrize(
    "operation,result",
    [
        # Location[]（findReferences 的形状）与 LocationLink[]（部分服务端的
        # goToDefinition 形状：只有 targetUri / targetRange，没有 uri / range）。
        # 两种都必须真的走到 uri_to_path——上一版测把结果套了一层
        # {"location": {...}}， callee 拿到的是缺 uri 的空对象，走的是
        # “<unknown>”分支，测绿了但什么都没测到。
        ("goToDefinition", [_loc(Path("a.py"), 7)]),
        ("goToDefinition", [{"targetUri": path_to_uri("a.py"),
                            "targetRange": {"start": {"line": 7, "character": 0}}}]),
        ("findReferences", [_loc(Path("a.py"), 7), _loc(Path("b.py"), 12)]),
        ("goToImplementation", [_loc(Path("impl.py"), 3)]),
        ("prepareCallHierarchy", [{"name": "f", "kind": 12, "uri": path_to_uri("a.py"),
                                   "range": {"start": {"line": 7}}}]),
        ("incomingCalls", [{"from": {"name": "caller", "kind": 12, "uri": path_to_uri("a.py"),
                                     "range": {"start": {"line": 9}}}, "fromRanges": [{}]}]),
        ("outgoingCalls", [{"to": {"name": "callee", "kind": 12, "uri": path_to_uri("b.py"),
                                   "range": {"start": {"line": 9}}}, "fromRanges": [{}, {}]}]),
        ("workspaceSymbol", [{"name": "sym", "kind": 5,
                              "location": _loc(Path("c.py"), 2)}]),
    ],
)
def test_format_result_paths_that_reach_the_uri_helper(operation: str, result: list, tmp_path: Path) -> None:
    """分发器 → callee 全程跑通：这几条操作都会走到 uri_to_path。

    这条测红 = 又有人把导入挪回函数内，或者 uri 解析改坏了。
    """
    out = lsp_tool._format_result(operation, result, cwd=str(tmp_path))

    assert isinstance(out, str) and out, f"{operation} 返回空"
    # 行号必须出现且不能是“缺 uri”的占位符——否则说明根本没解析到路径
    assert "<unknown>" not in out, f"{operation} 退成了占位符，uri 没被解析：{out!r}"
    assert any(ch.isdigit() for ch in out), f"{operation} 的输出里没有行号：{out!r}"


def test_format_result_none_and_empty_shapes(tmp_path: Path) -> None:
    """空结果给的是人话，不是异常也不是 'None'。"""
    assert lsp_tool._format_result("goToDefinition", None, cwd=str(tmp_path)) == "无结果"
    assert "无入站调用" in lsp_tool._format_result("incomingCalls", [], cwd=str(tmp_path))
    assert "无出站调用" in lsp_tool._format_result("outgoingCalls", [], cwd=str(tmp_path))
    assert "无调用层次项" in lsp_tool._format_result("prepareCallHierarchy", [], cwd=str(tmp_path))
    assert "无文档符号" in lsp_tool._format_result("documentSymbol", [], cwd=str(tmp_path))


def test_incoming_and_outgoing_report_call_counts(tmp_path: Path) -> None:
    """调用处数直接来自 fromRanges，模型要靠它判断影响面。"""
    incoming = lsp_tool._format_incoming_calls(
        [{"from": {"name": "a", "kind": 12, "uri": path_to_uri("a.py"),
                   "range": {"start": {"line": 3}}}, "fromRanges": [{}, {}, {}]}],
        cwd=str(tmp_path),
    )
    outgoing = lsp_tool._format_outgoing_calls(
        [{"to": {"name": "b", "kind": 12, "uri": path_to_uri("b.py"),
                 "range": {"start": {"line": 5}}}, "fromRanges": [{}]}],
        cwd=str(tmp_path),
    )

    assert "3 处调用" in incoming
    assert "1 处调用" in outgoing
    assert "a.py" in incoming and "b.py" in outgoing
