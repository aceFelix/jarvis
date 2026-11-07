"""打包版能力门禁（agent/capabilities/gate.py）单测。

不触碰真实环境：模块直查与浏览器内核目录都 monkeypatch 成替身，
只验证门禁的判定规则：

- 八组 extras 的模块直查缺失要报错；
- 双重浏览器内核（无头壳 + 完整 chromium）缺一即红；
- 能力表里的 Python 包即使标记 optional，打包版也必须 OK；
- 必需资产缺失要报错，可选资产（如 OCR 权重）允许运行时下载；
- 用户凭据（secret）不计入安装包能力，缺 key 不应让构建失败。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.capabilities import gate
from agent.capabilities.registry import (
    CapState,
    CapabilityReport,
    ReqState,
    RequirementStatus,
    get_meta,
)


def _report(name: str, reqs: list[RequirementStatus], state: str = CapState.OK) -> CapabilityReport:
    return CapabilityReport(name=name, title=name, state=state, requirements=reqs)


def _all_ok_reports() -> dict[str, CapabilityReport]:
    """按真实能力表生成“全部就绪”的报告（只认声明里的 kind/label）。"""
    reports: dict[str, CapabilityReport] = {}
    for name in gate.PACKAGED_REQUIRED_CAPABILITIES:
        meta = get_meta(name)
        assert meta is not None, name
        reqs: list[RequirementStatus] = []
        for r in meta.python_reqs:
            reqs.append(RequirementStatus(kind="python", label=r.label, state=ReqState.OK))
        for r in meta.binaries:
            reqs.append(RequirementStatus(kind="binary", label=r.name, state=ReqState.OK,
                                          optional=r.optional))
        for r in meta.assets:
            reqs.append(RequirementStatus(kind="asset", label=r.label, state=ReqState.OK,
                                          optional=r.optional))
        for r in meta.secrets:
            reqs.append(RequirementStatus(kind="secret", label=r.label, state=ReqState.OK,
                                          optional=r.optional))
        for r in meta.platforms:
            reqs.append(RequirementStatus(kind="platform", label="平台", state=ReqState.OK,
                                          optional=r.optional))
        reports[name] = _report(name, reqs)
    return reports


@pytest.fixture()
def ready(monkeypatch):
    """让模块直查与双内核都通过，返回全绿能力报告。"""
    monkeypatch.setattr(gate, "_module_state", lambda name: "ok")
    monkeypatch.setattr(
        gate, "_browser_kernels",
        lambda: {"root": "C:/browsers", "headless": "ok", "full": "ok"},
    )
    return _all_ok_reports()


def test_all_ready_passes(ready) -> None:
    result = gate.evaluate(ready)
    assert result["ok"] is True
    assert result["failures"] == []
    assert set(result["capabilities"]) == set(gate.PACKAGED_REQUIRED_CAPABILITIES)


def test_missing_extra_module_fails(ready, monkeypatch) -> None:
    monkeypatch.setattr(gate, "_module_state", lambda name: "missing" if name == "pyaudio" else "ok")
    result = gate.evaluate(ready)
    assert result["ok"] is False
    assert any("pyaudio" in f for f in result["failures"])


def test_optional_python_must_be_ok_in_package(ready) -> None:
    # vision.detect 的 mediapipe/opencv/paddleocr 在能力表里都是 optional，
    # 但“打包版全能力”要求它们真的在包里。
    name = "vision.detect"
    ready[name] = _report(name, [
        RequirementStatus(kind="python", label="mediapipe", state=ReqState.MISSING, optional=True),
        RequirementStatus(kind="asset", label="OCR 模型权重", state=ReqState.MISSING, optional=True),
    ], state=CapState.DEGRADED)
    result = gate.evaluate(ready)
    assert result["ok"] is False
    assert any("mediapipe" in f for f in result["failures"])
    # 可选资产缺失本身不报错
    assert not any("OCR" in f for f in result["failures"])


def test_missing_browser_kernel_fails(ready, monkeypatch) -> None:
    monkeypatch.setattr(
        gate, "_browser_kernels",
        lambda: {"root": "C:/browsers", "headless": "missing", "full": "ok"},
    )
    result = gate.evaluate(ready)
    assert result["ok"] is False
    assert any("无头内核" in f for f in result["failures"])


def test_missing_full_chromium_fails(ready, monkeypatch) -> None:
    monkeypatch.setattr(
        gate, "_browser_kernels",
        lambda: {"root": "C:/browsers", "headless": "ok", "full": "missing"},
    )
    result = gate.evaluate(ready)
    assert result["ok"] is False
    assert any("完整 chromium" in f for f in result["failures"])


def test_secret_missing_is_not_a_packaging_failure(ready) -> None:
    """语音 key 属于用户配置，不应让安装包构建失败。"""
    name = "voice.half"
    ready[name] = _report(name, [
        RequirementStatus(kind="python", label="dashscope", state=ReqState.OK),
        RequirementStatus(kind="python", label="pyaudio", state=ReqState.OK),
        RequirementStatus(kind="secret", label="语音服务密钥", state=ReqState.MISSING,
                          optional=False),
    ], state=CapState.MISSING)
    result = gate.evaluate(ready)
    assert result["ok"] is True


def test_probe_failed_is_a_packaging_failure(ready) -> None:
    name = "browser.selfcheck"
    ready[name] = _report(name, [
        RequirementStatus(kind="python", label="playwright", state=ReqState.OK),
        RequirementStatus(kind="asset", label="playwright 浏览器内核",
                          state=ReqState.PROBE_FAILED),
    ], state=CapState.PROBE_FAILED)
    result = gate.evaluate(ready)
    assert result["ok"] is False
    assert any("探针失败" in f for f in result["failures"])


def test_unregistered_capability_fails(ready) -> None:
    ready.pop("mcp.connect")
    result = gate.evaluate(ready)
    assert result["ok"] is False
    assert any("未登记" in f for f in result["failures"])
