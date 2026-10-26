"""待发窗口未结案时的「等价外发命令」权限收紧（W3.2 绕道硬拦，口径 A 收窄版）。

回归来源（2026-10-21 实机）：不可逆外发动作被拦进 30 秒窗口后，模型改用
``Bash curl`` 把同一件事做掉了——沙箱对中风险命令默认自动放行，窗口拦一条
tool_use 却放行等价通路等于没拦。本文件锁两件事：

1. ``looks_network_outbound`` 只认外发通路（判的是通路，不是危险度）；
2. 窗口里有未结案项时，外发类中风险命令从 ALLOW 降级成 ASK，其余命令照旧，
   且 ``actions.hold_blocks_equivalent = false`` 能退回旧行为。

复盘：docs/fixlogs/w3-delay-window-bypass-fix.md
@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.core.actions import get_window
from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior
from agent.core.sandbox.risk_scorer import RiskLevel, RiskScorer, looks_network_outbound
from agent.tools.bash import BashTool

_scorer = RiskScorer()


def _settings(**over) -> SimpleNamespace:
    """沙箱 + 账本相关字段（其余权限判定不读）。"""
    base = {
        "sandbox_enabled": True,
        "sandbox_auto_allow_medium": True,
        "actions_hold_blocks_equivalent": True,
        "workdir": ".",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(settings=None) -> ToolContext:
    return ToolContext(
        workdir=".", messages=[], settings=settings if settings is not None else _settings(),
    )


def _open_hold(label: str = "给 a@b.c 发一封信") -> str:
    """在窗口里压一条未结案动作（fire 是空动作，测试只关心登记表状态）。"""
    rec_id = "hold-test-1"
    get_window().schedule(rec_id, 30, lambda: None, label=label)
    return rec_id


@pytest.fixture
def clean_window():
    """用例收尾清空登记表：窗口是进程级单例，不能漏给下一个测试。"""
    yield
    get_window().clear()


class TestOutboundDetection:
    """通路判定：外发算、本机操作不算。"""

    @pytest.mark.parametrize("cmd", [
        "curl -s -m 20 https://example.com",
        "wget http://10.0.0.1/x.bin -O /tmp/x",
        "python -c \"import smtplib;s=smtplib.SMTP('smtp.x.com')\"",
        "node fetcher.js && curl https://api.x.com/y",
        "Invoke-WebRequest -Uri https://x.com",
        "nc 1.2.3.4 25",
    ])
    def test_outbound_hits(self, cmd):
        assert looks_network_outbound(cmd), cmd

    @pytest.mark.parametrize("cmd", [
        "ls -la",
        "git commit -m 'w3'",
        "mkdir out",
        "python scripts/build.py",            # 跑本地脚本，无网络库线索
        "grep -r TODO agent",
    ])
    def test_local_commands_not_flagged(self, cmd):
        assert not looks_network_outbound(cmd), cmd

    def test_empty_command_is_not_outbound(self):
        assert not looks_network_outbound("")
        assert not looks_network_outbound("   ")


class TestHoldGate:
    """窗口未结案 → 外发中风险命令不再自动放行。"""

    def test_baseline_medium_outbound_is_auto_allowed(self, clean_window):
        """没预约时一切照旧：沙箱开启 + 中风险 = 自动放行（既有设计）。"""
        cmd = "curl -s https://example.com"
        assert _scorer.score_command(cmd) == RiskLevel.MEDIUM
        res = BashTool().check_permissions({"command": cmd}, _ctx())
        assert res.behavior == PermissionBehavior.ALLOW

    def test_outbound_becomes_ask_while_held(self, clean_window):
        """核心回归：窗口里压着未结案动作时，等价外发命令必须问人。"""
        _open_hold()
        res = BashTool().check_permissions(
            {"command": "curl -s https://example.com"}, _ctx())
        assert res.behavior == PermissionBehavior.ASK, "放行等价通路＝窗口形同虚设"
        assert "待发窗口" in (res.reason or "") and "curl" in (res.reason or "")

    def test_unrelated_medium_command_unaffected(self, clean_window):
        """收窄：只收外发通路，本机中风险命令不该跟着一起被问。"""
        _open_hold()
        res = BashTool().check_permissions({"command": "git commit -m 'w3'"}, _ctx())
        assert res.behavior == PermissionBehavior.ALLOW

    def test_switch_off_restores_auto_allow(self, clean_window):
        """``hold_blocks_equivalent = false`` 退回"只靠文案约束"的旧行为。"""
        _open_hold()
        ctx = _ctx(settings=_settings(actions_hold_blocks_equivalent=False))
        res = BashTool().check_permissions({"command": "curl -s https://example.com"}, ctx)
        assert res.behavior == PermissionBehavior.ALLOW

    def test_gate_released_after_window_clears(self, clean_window):
        """结案（取消/到点）后必须恢复自动放行，不能把收紧留成永久状态。"""
        rec_id = _open_hold()
        assert get_window().cancel(rec_id) is True
        res = BashTool().check_permissions({"command": "curl -s https://example.com"}, _ctx())
        assert res.behavior == PermissionBehavior.ALLOW

    def test_pending_labels_explain_the_ask(self, clean_window):
        """登记表自带展示文本：权限层不为了取名字去重读整本账本。"""
        rec_id = _open_hold(label="给 boss@x.com 发一封信")
        assert get_window().pending_labels() == ["给 boss@x.com 发一封信"]
        get_window().cancel(rec_id)
        assert get_window().pending_labels() == []
