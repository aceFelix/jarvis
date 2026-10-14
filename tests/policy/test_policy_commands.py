"""W1 命令处理器单测 —— /away /back /pending /lease 解析与状态联动。

用桩 ui 捕获输出，验证命令写盘的状态能被闸门（policy_gate）读到，
即命令 → 落盘 → 判定生效的闭环；以及 TTL/路径参数解析口径。

@author aceFelix
"""

from __future__ import annotations

from agent.core.policy import policy_gate
from agent.core.policy.state import get_pending_approvals
from agent.core.result import PermissionBehavior, PermissionResult
from agent.commands.handlers.policy_commands import (
    handle_away, handle_back, handle_lease, handle_pending,
)


class _Ui:
    """RichCLI 桩：记录 info/warn 文本供断言。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, text, *a, **k):
        self.messages.append(str(text))

    def warn(self, text, *a, **k):
        self.messages.append(f"WARN:{text}")

    def error(self, text, *a, **k):
        self.messages.append(f"ERR:{text}")


class _Ctx:
    """命令上下文桩：只需 ui。"""

    def __init__(self) -> None:
        self.ui = _Ui()


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _GateCtx:
    def __init__(self) -> None:
        self.workdir = ""


def test_away_back_cycle_reaches_gate(policy_paths):
    """/away 后闸门拦 ASK 入清单；/back 简报带出待批项并清空。"""
    ctx = _Ctx()
    assert handle_away(ctx, "/away") is True
    out = policy_gate(_Tool("FileWrite"), {"file_path": "a.md"}, _GateCtx(),
                      PermissionResult.ask("默认询问"))
    assert out.behavior == PermissionBehavior.DENY
    assert len(get_pending_approvals().list()) == 1

    assert handle_pending(ctx, "/pending") is True
    assert "待批清单 1 条" in ctx.ui.messages[-1]

    assert handle_back(ctx, "/back") is True
    assert "待批 1 条" in ctx.ui.messages[-1]
    assert get_pending_approvals().list() == []


def test_lease_grant_list_revoke(policy_paths):
    """/lease 签发 → 命中放行；list 可见；revoke 后回到 ASK。"""
    ctx = _Ctx()
    assert handle_lease(ctx, "/lease FileWrite ~/reports/** 30m") is True
    msg = ctx.ui.messages[-1]
    assert "已签发租约" in msg and "30 分钟" in msg

    out = policy_gate(_Tool("FileWrite"),
                      {"file_path": "~/reports/a.md"}, _GateCtx(),
                      PermissionResult.ask())
    assert out.behavior == PermissionBehavior.ALLOW

    ctx2 = _Ctx()
    assert handle_lease(ctx2, "/lease list") is True
    assert "生效中的租约 1 条" in ctx2.ui.messages[-1]
    # 提取 id 撤销
    import re
    lid = re.search(r"\[([0-9a-f]{8})\]", ctx2.ui.messages[-1]).group(1)
    assert handle_lease(ctx2, f"/lease revoke {lid}") is True
    out2 = policy_gate(_Tool("FileWrite"),
                       {"file_path": "~/reports/a.md"}, _GateCtx(),
                       PermissionResult.ask())
    assert out2.behavior == PermissionBehavior.ASK


def test_lease_default_ttl(policy_paths):
    """/lease 不带 TTL → 缺省 120 分钟。"""
    ctx = _Ctx()
    handle_lease(ctx, "/lease Bash")
    assert "有效期 120 分钟" in ctx.ui.messages[-1]


def test_lease_revoke_unknown(policy_paths):
    """撤销不存在的 id 给出警告。"""
    ctx = _Ctx()
    assert handle_lease(ctx, "/lease revoke zzzz") is True
    assert any(m.startswith("WARN:未找到") for m in ctx.ui.messages)


def test_back_without_away_reports_empty(policy_paths):
    """/back 无积压时提示没有待批请求。"""
    ctx = _Ctx()
    assert handle_back(ctx, "/back") is True
    assert "没有待批的请求" in ctx.ui.messages[-1]


def test_w1_commands_registered_in_slash_completion():
    """W1 四命令必须登记进 REPL 补全表 SLASH_COMMANDS——阶段 8 首版只注册
    了路由与帮助文本漏了补全，用户打 /l 弹不出 /lease（实测反馈），
    用此断言防再犯。@author aceFelix"""
    from agent.ui.cli import SLASH_COMMANDS

    names = {n.split()[0] for n, _ in SLASH_COMMANDS}
    assert {"/away", "/back", "/pending", "/lease"} <= names
