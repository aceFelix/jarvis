"""EmailBox 工具回归测试（假 IMAP，无网络）。

守三条底线：
- 全程 readonly（`select(readonly=True)` + `BODY.PEEK`），读完不标已读；
- 回复必须带 `In-Reply-To` / `References`，否则对方客户端里成不了会话；
- 权限：read/search 只读放行，reply 一律 ASK 且声明不可逆。

@author aceFelix
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior, ToolResult
from agent.tools.extensions.email_box import EmailBoxTool, to_imap_date


# ---- 假 IMAP ----


class _FakeConn:
    """按脚本回应的假 IMAP 连接。"""

    def __init__(self, *, search_result: bytes = b"", header_raw: bytes = b"", full_raw: bytes = b""):
        self.search_result = search_result
        self.header_raw = header_raw
        self.full_raw = full_raw
        self.select_args: tuple[Any, ...] | None = None
        self.fetch_specs: list[str] = []
        self.search_calls: list[tuple[Any, ...]] = []
        self.id_args: list[str] = []      # IMAP ID 命令（网易 163/126 不发就 SELECT 不进去）

    def login(self, user, password):  # noqa: ANN001
        return ("OK", [b""])

    def xatom(self, name, *args):  # noqa: ANN001
        """imaplib 发自定义命令的入口，`ID` 就走这里。"""
        if str(name).upper() == "ID":
            self.id_args.append(args[0] if args else "")
        return ("OK", [b""])

    def select(self, mailbox="INBOX", readonly=False):  # noqa: ANN001
        self.select_args = (mailbox, readonly)
        return ("OK", [b"1"])

    def logout(self):
        return ("BYE", [b""])

    def uid(self, cmd, *args):  # noqa: ANN001
        if cmd.upper() == "SEARCH":
            self.search_calls.append(args)
            return ("OK", [self.search_result])
        if cmd.upper() == "FETCH":
            spec = args[-1] if args else ""
            self.fetch_specs.append(spec)
            if "HEADER.FIELDS" in spec:
                return ("OK", [(b"1 (BODY[] {0}", self.header_raw), b")"])
            return ("OK", [(b"1 (BODY[] {0}", self.full_raw), b")"])
        return ("OK", [b""])


def _settings(**over):
    base = dict(
        email_enabled=True,
        email_imap_host="imap.example.com",
        email_imap_port=993,
        email_imap_user="me@example.com",
        email_imap_password="secret",
        email_smtp_user="",
        email_smtp_password="",
        email_imap_mailbox="INBOX",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(settings=None) -> ToolContext:
    ctx = ToolContext(workdir=".", messages=[])
    ctx.settings = settings or _settings()
    return ctx


def _patch_conn(monkeypatch, conn: _FakeConn) -> None:
    """把 IMAP 连接换成假连接，但让真实的 open_inbox 跑起来。

    不用替补 open_inbox 本身：它在真实现里才调 login/select/logout，
    “只读 select”这条底线正是靠真实实现守的，替掉就测不到了。
    """
    monkeypatch.setattr(
        "agent.core.mail.imap.imap_connect", lambda host, port: conn
    )


HEADER_RAW = (
    "Message-ID: <m1@example.com>\r\n"
    "References: <m0@example.com>\r\n"
    "Subject: 周报\r\n"
    "From: 小薇 <xw@example.com>\r\n"
    "Date: Tue, 16 Oct 2026 09:00:00 +0800\r\n\r\n"
).encode()

FULL_RAW = (
    "From: 小薇 <xw@example.com>\r\n"
    "Subject: 周报\r\n"
    "Date: Tue, 16 Oct 2026 09:00:00 +0800\r\n"
    "Message-ID: <m1@example.com>\r\n"
    "Content-Type: text/plain; charset=utf-8\r\n\r\n"
    "这是正文。\r\n"
).encode()


# ---- 纯函数 ----


class TestHelpers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-10-01", "01-Oct-2026"),
            ("2026/10/01", "01-Oct-2026"),
            ("2026-1-5", "05-Jan-2026"),
            ("", ""),
            ("not-a-date", "not-a-date"),
        ],
    )
    def test_to_imap_date(self, raw: str, expected: str) -> None:
        assert to_imap_date(raw) == expected


# ---- 权限 ----


class TestPermissions:
    def test_read_and_search_are_readonly(self) -> None:
        tool = EmailBoxTool()
        assert tool.is_read_only({"action": "read"}) is True
        assert tool.is_read_only({"action": "search"}) is True
        assert tool.is_irreversible({"action": "search"}) is False

    def test_reply_is_ask_and_irreversible(self) -> None:
        tool = EmailBoxTool()
        assert tool.is_read_only({"action": "reply"}) is False
        assert tool.is_irreversible({"action": "reply"}) is True
        result = tool.check_permissions({"action": "reply", "uid": "1"}, _ctx())
        assert result.behavior == PermissionBehavior.ASK

    def test_read_allowed(self) -> None:
        tool = EmailBoxTool()
        result = tool.check_permissions({"action": "read", "uid": "1"}, _ctx())
        assert result.behavior == PermissionBehavior.ALLOW


# ---- 动作 ----


class TestReadAction:
    @pytest.mark.asyncio
    async def test_body_wrapped_in_untrusted_marker(self, monkeypatch) -> None:
        """正文必须被 <email_body> 包住并声明「不可信」，这是提示注入的第一层防护。"""
        conn = _FakeConn(full_raw=FULL_RAW)
        _patch_conn(monkeypatch, conn)

        result = await EmailBoxTool().call({"action": "read", "uid": "1"}, _ctx())

        assert result.is_error is False
        body = str(result.data)
        assert "<email_body>" in body
        assert "</email_body>" in body
        assert "这是正文。" in body
        assert "不得执行" in body

    @pytest.mark.asyncio
    async def test_select_is_readonly(self, monkeypatch) -> None:
        """读取不得把邮件标成已读：select 必须带 readonly=True。"""
        conn = _FakeConn(full_raw=FULL_RAW)
        _patch_conn(monkeypatch, conn)

        await EmailBoxTool().call({"action": "read", "uid": "1"}, _ctx())

        assert conn.select_args == ("INBOX", True)
        assert all("BODY.PEEK" in spec for spec in conn.fetch_specs)

    @pytest.mark.asyncio
    async def test_missing_uid_errors(self, monkeypatch) -> None:
        _patch_conn(monkeypatch, _FakeConn())
        result = await EmailBoxTool().call({"action": "read"}, _ctx())
        assert result.is_error is True
        assert "uid" in str(result.data)


class TestSearchAction:
    @pytest.mark.asyncio
    async def test_criteria_are_built_from_args(self, monkeypatch) -> None:
        conn = _FakeConn(search_result=b"1 2 3", header_raw=HEADER_RAW)
        _patch_conn(monkeypatch, conn)

        result = await EmailBoxTool().call(
            {"action": "search", "from": "xw@", "since": "2026-10-01", "limit": 2},
            _ctx(),
        )

        assert result.is_error is False
        sent = conn.search_calls[0]
        joined = " ".join(str(x) for x in sent)
        assert "FROM" in joined and "xw@" in joined
        assert "SINCE" in joined and "01-Oct-2026" in joined
        # limit=2：只保留最新的两个 uid
        assert "uid=3" in str(result.data) and "uid=2" in str(result.data)
        assert "uid=1" not in str(result.data)

    @pytest.mark.asyncio
    async def test_no_match_reports_clearly(self, monkeypatch) -> None:
        _patch_conn(monkeypatch, _FakeConn(search_result=b""))
        result = await EmailBoxTool().call(
            {"action": "search", "subject": "不存在"}, _ctx()
        )
        assert result.is_error is False
        assert "没有匹配的邮件" in str(result.data)


class TestReplyAction:
    @pytest.mark.asyncio
    async def test_thread_headers_injected(self, monkeypatch) -> None:
        """回复必须带 In-Reply-To 与 References，并把原有 References 串上。"""
        conn = _FakeConn(header_raw=HEADER_RAW)
        _patch_conn(monkeypatch, conn)

        captured: dict[str, Any] = {}

        async def fake_send(self, args, ctx, *, extra_headers=None):  # noqa: ANN001
            captured["args"] = args
            captured["headers"] = extra_headers
            return ToolResult.ok("已发送")

        monkeypatch.setattr(
            "agent.tools.extensions.email_tool.SendEmailTool.call", fake_send
        )

        result = await EmailBoxTool().call(
            {"action": "reply", "uid": "1", "body": "收到，谢谢。"}, _ctx()
        )

        assert result.is_error is False
        headers = captured["headers"]
        assert headers["In-Reply-To"] == "<m1@example.com>"
        assert headers["References"] == "<m0@example.com> <m1@example.com>"
        # 收件人默认回原发件人，主题补 Re:
        assert captured["args"]["to"] == "xw@example.com"
        assert captured["args"]["subject"] == "Re: 周报"
        assert captured["args"]["body"] == "收到，谢谢。"

    @pytest.mark.asyncio
    async def test_no_message_id_is_rejected(self, monkeypatch) -> None:
        """原信没有 Message-ID 时无法成串，应拒绝而不是悄悄发一封新邮件。"""
        conn = _FakeConn(header_raw=b"Subject: x\r\nFrom: a@b.com\r\n\r\n")
        _patch_conn(monkeypatch, conn)

        result = await EmailBoxTool().call(
            {"action": "reply", "uid": "1", "body": "x"}, _ctx()
        )

        assert result.is_error is True
        assert "Message-ID" in str(result.data)

    @pytest.mark.asyncio
    async def test_existing_re_prefix_not_duplicated(self, monkeypatch) -> None:
        conn = _FakeConn(
            header_raw=(
                "Message-ID: <m2@example.com>\r\nSubject: Re: 周报\r\n"
                "From: a@b.com\r\n\r\n"
            ).encode()
        )
        _patch_conn(monkeypatch, conn)

        captured: dict[str, Any] = {}

        async def fake_send(self, args, ctx, *, extra_headers=None):  # noqa: ANN001
            captured["args"] = args
            return ToolResult.ok("ok")

        monkeypatch.setattr(
            "agent.tools.extensions.email_tool.SendEmailTool.call", fake_send
        )

        await EmailBoxTool().call({"action": "reply", "uid": "1", "body": "x"}, _ctx())
        assert captured["args"]["subject"] == "Re: 周报"


class TestConfigErrors:
    @pytest.mark.asyncio
    async def test_disabled_email_reports_clearly(self, monkeypatch) -> None:
        _patch_conn(monkeypatch, _FakeConn())
        result = await EmailBoxTool().call(
            {"action": "search"}, _ctx(_settings(email_enabled=False))
        )
        assert result.is_error is True
        assert "未启用" in str(result.data)

    @pytest.mark.asyncio
    async def test_incomplete_imap_config_reports_clearly(self, monkeypatch) -> None:
        _patch_conn(monkeypatch, _FakeConn())
        result = await EmailBoxTool().call(
            {"action": "search"}, _ctx(_settings(email_imap_host="", email_imap_user=""))
        )
        assert result.is_error is True
        assert "IMAP" in str(result.data)

    @pytest.mark.asyncio
    async def test_unknown_action(self, monkeypatch) -> None:
        _patch_conn(monkeypatch, _FakeConn())
        result = await EmailBoxTool().call({"action": "delete"}, _ctx())
        assert result.is_error is True
        assert "不认识的 action" in str(result.data)

    @pytest.mark.asyncio
    async def test_smtp_only_config_is_enough_to_read(self, monkeypatch) -> None:
        """只配了发件那套（真实用户绝大多数状态）时，收件应当直接可用。

        实机（2026-10）：用户 `[email]` 里只有 smtp_*、桌面「邮箱配置」面板还
        显示“已启用 / 授权码已配置”，但 EmailBox 报“IMAP 收件配置不完整”——
        因为主机名不会从 smtp_host 推。推导补上之后这条路径必须能跑通。
        """
        conn = _FakeConn(search_result=b"", header_raw=HEADER_RAW, full_raw=FULL_RAW)
        _patch_conn(monkeypatch, conn)
        settings = SimpleNamespace(
            email_enabled=True,
            email_imap_host="",
            email_imap_port=993,
            email_imap_user="",
            email_imap_password="",
            email_imap_mailbox="INBOX",
            email_smtp_host="smtp.163.com",
            email_smtp_user="me@163.com",
            email_smtp_password="code",
        )
        result = await EmailBoxTool().call({"action": "search"}, _ctx(settings))
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_config_missing_is_not_retryable(self, monkeypatch) -> None:
        """配置类错误必须标 ``retryable=False``：配置不会自己变好。

        实机日志：一串「EmailBox 遇到 未知错误，N 秒后第 k/N 次重试」，
        真实原因（没配好）反而被重试噪声盖住。
        """
        _patch_conn(monkeypatch, _FakeConn())
        result = await EmailBoxTool().call(
            {"action": "read", "uid": "1"},
            _ctx(_settings(email_imap_host="", email_imap_user="", email_imap_password="")),
        )
        assert result.is_error is True
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_disabled_is_not_retryable(self, monkeypatch) -> None:
        """同口径：邮件总开关关着也不是“重试就能好”的事。"""
        _patch_conn(monkeypatch, _FakeConn())
        result = await EmailBoxTool().call(
            {"action": "search"}, _ctx(_settings(email_enabled=False))
        )
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_missing_hint_names_field_and_how_to_fix(self, monkeypatch) -> None:
        """回执要点名缺哪一项、给可粘贴的配置例，并提醒去邮箱后台开 IMAP。

        只说“没配好”不够：用户在面板上看到“已启用”就会以为自己配过。
        """
        _patch_conn(monkeypatch, _FakeConn())
        settings = SimpleNamespace(
            email_enabled=True,
            email_imap_host="",
            email_imap_port=993,
            email_imap_user="",
            email_imap_password="",
            email_imap_mailbox="INBOX",
            email_smtp_host="",
            email_smtp_user="",
            email_smtp_password="",
        )
        result = await EmailBoxTool().call({"action": "search"}, _ctx(settings))
        data = str(result.data)
        assert "imap_host" in data
        assert "imap_user" in data
        assert "settings.toml" in data
        assert "IMAP 服务" in data          # 提醒去后台开服务，不只是填主机名


class TestClientId:
    """登录后必须自报家门（IMAP ``ID``）—— 网易 163/126 的硬要求。

    实机（2026-10）：163 允许 `LOGIN` 成功，但紧接着的 `EXAMINE` 返回
    `Unsafe Login. Please contact kefu@188.com for help`。看着像授权码错了，
    实际是客户端没发 `ID`。不发这一步，163/126 用户永远收不了信。

    @author aceFelix
    """

    @pytest.mark.asyncio
    async def test_connection_sends_id_after_login(self, monkeypatch) -> None:
        conn = _FakeConn(search_result=b"1615905175", header_raw=HEADER_RAW)
        _patch_conn(monkeypatch, conn)
        await EmailBoxTool().call({"action": "search"}, _ctx())
        assert conn.id_args, "未发送 IMAP ID：163/126 会在 SELECT 阶段报 Unsafe Login"
        assert "jarvis" in conn.id_args[0]

