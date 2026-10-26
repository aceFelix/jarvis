"""`send_email_now` —— 不经模型、不跑权限判定的直发入口。

这条函数被两个调用方共用：桌面「管家服务 → 邮件发送」表单（`WorkbenchAPI.send_email`）
与静默时段的提醒改道（`serve/hub._deliver_quiet_email`）。这里验它的三条契约：
工具报错必须转成 ValueError（调用方据此决定"失败不重投"）、成功回工具摘要、
**不得触发 check_permissions**（点表单/静默改道都已经是用户表过态的动作）。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.core.result import ToolResult
from agent.tools.extensions.email_tool import SendEmailTool, send_email_now


class _Settings:
    """只带 workdir 的 settings 桩（send_email_now 只从这里取 workdir）。"""

    workdir = "E:/lab"


def test_tool_error_becomes_value_error(monkeypatch):
    """配置未启用等错误由工具给出，本层转成 ValueError 让调用方兜住。"""
    monkeypatch.setattr(
        SendEmailTool, "call",
        lambda self, args, ctx: ToolResult.error("邮件功能未启用"),
    )

    with pytest.raises(ValueError, match="邮件功能未启用"):
        send_email_now(_Settings(), {"subject": "s", "body": "b"})


def test_success_returns_tool_summary_and_skips_permission_check(monkeypatch):
    """成功回工具的摘要文本；全程不跑 check_permissions（用户已经表过态）。"""
    seen: dict[str, object] = {}
    asked: list[str] = []

    async def _fake_call(self, args, ctx):
        seen["args"] = args
        seen["workdir"] = ctx.workdir
        return ToolResult(data="✓ 邮件已发送\n  收件人: me@x.com")

    def _spy(self, args, ctx):
        asked.append("check_permissions")
        return None

    monkeypatch.setattr(SendEmailTool, "call", _fake_call)
    monkeypatch.setattr(SendEmailTool, "check_permissions", _spy)

    msg = send_email_now(
        _Settings(), {"to": "me@x.com", "subject": "s", "body": "b"},
    )

    assert "已发送" in msg
    assert seen["args"]["to"] == "me@x.com"
    assert seen["workdir"] == "E:/lab"        # 附件相对路径要靠它
    assert asked == [], "直发入口不该再问一次权限"
