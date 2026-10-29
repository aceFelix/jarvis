"""IMAP 主机名推导与账号解析（纯函数 + 假 Settings，无网络）。

这两个函数的职责是「让用户少填」，但少填不等于乱填——所以边界比正例更重要：

- `derive_imap_host` 只在首段**字面等于** `smtp` 时才换（`smtp.163.com` → `imap.163.com`）；
  自定义域名与厂商特例（Outlook）一律不猜，返回空串让用户显式配。
- `resolve_account` 的优先级是「显式配置 > 推导值」，且账号/授权码允许回退到 SMTP 那一套。

@author aceFelix
"""

from __future__ import annotations

import imaplib
from types import SimpleNamespace

import pytest

from agent.core.mail.imap import (
    DEFAULT_IMAP_PORT,
    _IMAP_CLIENT_ID,
    derive_imap_host,
    resolve_account,
    send_client_id,
)


def _settings(**overrides: object) -> SimpleNamespace:
    """造一份只带 email_* 字段的假 Settings（缺的属性由 getattr 默认值兜底）。"""
    base = {
        "email_imap_host": "",
        "email_imap_port": DEFAULT_IMAP_PORT,
        "email_imap_user": "",
        "email_imap_password": "",
        "email_imap_mailbox": "INBOX",
        "email_smtp_host": "",
        "email_smtp_user": "",
        "email_smtp_password": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDeriveImapHost:
    """按「同域前缀互换」惯例从 SMTP 主机推 IMAP 主机。"""

    @pytest.mark.parametrize(
        "smtp_host, expected",
        [
            ("smtp.163.com", "imap.163.com"),
            ("smtp.126.com", "imap.126.com"),
            ("smtp.qq.com", "imap.qq.com"),
            ("smtp.exmail.qq.com", "imap.exmail.qq.com"),   # 腾讯企业邮同样成立
            ("smtp.gmail.com", "imap.gmail.com"),
            ("SMTP.163.COM", "imap.163.com"),               # 大小写归一
            ("  smtp.163.com  ", "imap.163.com"),           # 前后空白归一
        ],
    )
    def test_common_vendors(self, smtp_host: str, expected: str) -> None:
        assert derive_imap_host(smtp_host) == expected

    def test_known_exception_is_mapped_explicitly(self) -> None:
        """Outlook 不是前缀互换（真实 IMAP 是 outlook.office365.com）。"""
        assert derive_imap_host("smtp.office365.com") == "outlook.office365.com"

    @pytest.mark.parametrize(
        "smtp_host",
        [
            "",
            "   ",
            "mail.mycorp.com",       # 自定义域名：推不出，让用户显式配
            "smtp163.com",           # 首段不是 smtp
            "smtp.com",              # 只有两段，拼不出合法主机名
            "pop.163.com",           # 不是 smtp
        ],
    )
    def test_unrecognized_returns_empty(self, smtp_host: str) -> None:
        """推不出就返回空串——猜错会报一个看不懂的连接失败，比空更糟。"""
        assert derive_imap_host(smtp_host) == ""


class TestResolveAccount:
    """账号解析：显式优先、按需回退、全缺才算未配置。"""

    def test_explicit_imap_host_wins_over_derivation(self) -> None:
        s = _settings(
            email_imap_host="mail.corp.example",
            email_smtp_host="smtp.163.com",
            email_smtp_user="a@163.com",
            email_smtp_password="code",
        )
        assert resolve_account(s).host == "mail.corp.example"

    def test_host_and_credentials_fall_back_to_smtp(self) -> None:
        """用户只配了发件那一套时，收件应当能直接跑起来。"""
        s = _settings(
            email_smtp_host="smtp.163.com",
            email_smtp_user="a@163.com",
            email_smtp_password="code",
        )
        acc = resolve_account(s)
        assert acc.host == "imap.163.com"      # 推导
        assert acc.user == "a@163.com"         # 回退
        assert acc.password == "code"          # 回退
        assert acc.port == DEFAULT_IMAP_PORT
        assert acc.mailbox == "INBOX"
        assert acc.configured is True

    def test_explicit_imap_credentials_win(self) -> None:
        s = _settings(
            email_smtp_host="smtp.163.com",
            email_smtp_user="smtp-user@163.com",
            email_smtp_password="smtp-code",
            email_imap_user="imap-user@163.com",
            email_imap_password="imap-code",
            email_imap_mailbox="Work",
        )
        acc = resolve_account(s)
        assert acc.user == "imap-user@163.com"
        assert acc.password == "imap-code"
        assert acc.mailbox == "Work"

    def test_nothing_configured_is_not_ready(self) -> None:
        """什么都没有时不能假报可用——否则会去连一个空主机名。"""
        acc = resolve_account(_settings())
        assert acc.configured is False

    def test_underivable_host_keeps_account_unconfigured(self) -> None:
        """自定义 SMTP 域名推不出 IMAP：账号/授权码齐了也不算可连。"""
        s = _settings(
            email_smtp_host="mail.mycorp.com",
            email_smtp_user="a@mycorp.com",
            email_smtp_password="code",
        )
        acc = resolve_account(s)
        assert acc.host == ""
        assert acc.configured is False


class _IdConn:
    """只记录 xatom 调用的假连接。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    def xatom(self, name: str, *args: str) -> tuple[str, list[bytes]]:
        if self._fail:
            raise imaplib.IMAP4.error("ID not supported")
        self.calls.append((name, args[0] if args else ""))
        return "OK", [b""]


class TestSendClientId:
    """登录后向服务器上报客户端身份（IMAP ``ID``）。

    网易 163/126 的硬要求：不发 ``ID`` 时 ``LOGIN`` 能成功，但随后的
    ``SELECT/EXAMINE`` 直接返回 ``Unsafe Login. Please contact kefu@188.com``
    （实机 2026-10）。这个错误文字很像“授权码不对”，实际上授权码完全正确。

    @author aceFelix
    """

    def test_registers_id_command_in_imaplib(self) -> None:
        """``imaplib`` 的命令表里默认没 ``ID``，不注册就会被状态机拒掉。"""
        assert "ID" in imaplib.Commands
        assert "AUTH" in imaplib.Commands["ID"]

    def test_sends_id_with_client_identity(self) -> None:
        conn = _IdConn()
        send_client_id(conn)
        assert conn.calls == [("ID", _IMAP_CLIENT_ID)]
        assert "jarvis" in conn.calls[0][1]

    def test_server_rejecting_id_does_not_raise(self) -> None:
        """不是所有服务器都认 ``ID``：发不出去就算了，交给 SELECT 报真错。"""
        send_client_id(_IdConn(fail=True))  # 不抛即通过

    def test_client_id_has_no_user_data(self) -> None:
        """上报内容只能是客户端标识，不能带任何账号/凭据信息。"""
        assert "@" not in _IMAP_CLIENT_ID
        assert "password" not in _IMAP_CLIENT_ID.lower()
