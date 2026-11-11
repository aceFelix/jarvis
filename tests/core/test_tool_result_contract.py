"""``ToolResult`` 工厂方法契约守卫。

## 事故（2026-11-11）

``ToolResult`` dataclass 早就有了 ``trace_meta`` 字段，但 ``ok()`` 工厂方法没透传它。
新增图片窗口时，``ScreenShot`` / ``CameraShot`` / ``BrowserScreenshot`` 三处写成
``ToolResult.ok(..., trace_meta=...)``，真实调用直接抛：

    TypeError: ToolResult.ok() got an unexpected keyword argument 'trace_meta'

三个截图/拍照工具整条挂掉，重试三次全是同一个错。**测试没拦住**，因为此前没有任何
用例真的跑过 ``call()``（需要 pyautogui / 摄像头 / 浏览器），而 ``crawl.py`` /
``extract.py`` 用的是 ``ToolResult(data=..., trace_meta=...)`` 直接构造，不受影响。

## 本守卫

用 AST 扫 ``agent/`` 下所有 ``ToolResult.ok(...)`` / ``ToolResult.error(...)`` 调用，
把实参 keyword 名与**签名这一个真源**比对。加字段忘了改工厂这类漏改，从此在 CI 红，
而不是等用户点一下截图。

@author aceFelix
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from agent.core.result import ToolResult

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "agent"


def _factory_params(name: str) -> set[str]:
    """工厂方法的可接受 keyword 名（classmethod 已绑定，签名里没有 cls）。"""
    return set(inspect.signature(getattr(ToolResult, name)).parameters)


def _iter_factory_calls():
    """遍历 ``ToolResult.ok(...)`` / ``ToolResult.error(...)`` 的调用节点。"""
    for path in sorted(AGENT_DIR.rglob("*.py")):
        source = path.read_bytes().decode("utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in ("ok", "error")):
                continue
            owner = func.value
            if isinstance(owner, ast.Name) and owner.id == "ToolResult":
                yield path, func.attr, node


class TestFactoryKeywordContract:
    def test_guard_sees_calls(self) -> None:
        """守卫不能是空转：全仓至少应有十来处工厂调用。"""
        calls = list(_iter_factory_calls())
        assert len(calls) >= 10, f"只扫到 {len(calls)} 处 ToolResult.ok/error 调用"

    def test_every_keyword_is_accepted(self) -> None:
        problems: list[str] = []
        for path, factory, node in _iter_factory_calls():
            allowed = _factory_params(factory)
            for keyword in node.keywords:
                if keyword.arg is None:  # **expr 展开：静态无法判断，跳过
                    continue
                if keyword.arg not in allowed:
                    where = path.relative_to(REPO_ROOT).as_posix()
                    problems.append(
                        f"{where}:{node.lineno} ToolResult.{factory}({keyword.arg}=...) "
                        f"不在签名里（可用：{sorted(allowed)}）"
                    )
        assert not problems, "工厂方法漏透传（加了字段忘了改 classmethod）：\n" + "\n".join(problems)


class TestTraceMetaPassthrough:
    """字段本身与两条工厂路径都要能带 ``trace_meta``。"""

    def test_ok_accepts_trace_meta(self) -> None:
        result = ToolResult.ok("x", trace_meta={"image": {"path": "a.png"}})
        assert result.trace_meta == {"image": {"path": "a.png"}}
        assert result.is_error is False

    def test_ok_defaults_to_empty_dict(self) -> None:
        assert ToolResult.ok("x").trace_meta == {}

    def test_error_rejects_unknown_keyword(self) -> None:
        """``error()`` 目前不收 trace_meta；真需要时先改签名，而不是绕过守卫。"""
        with pytest.raises(TypeError):
            ToolResult.error("boom", trace_meta={})  # type: ignore[call-arg]
