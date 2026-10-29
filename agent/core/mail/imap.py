"""IMAP 底层：账号解析、只读连接、搜索、拉信封头与正文。

三条与感知层一致的口径（`core/percept/source_mail.py`），这里统一承接：

1. **一律只读**：`select(readonly=True)` + `BODY.PEEK` —— 读邮件绝不改
   \\Seen 标记。用户明确不要"标已读"，所以本模块不提供任何写状态的操作。
2. **UID 而非序号**：UID 在同一邮箱内稳定，序号会随删除变动。
3. **凭据回退**：`imap_password` 留空时沿用 `smtp_password`（163/QQ 同一个
   授权码），口径与其感知源 factory 完全一致，避免两处漂移。

连接策略：工具调用**每次现连现断**，不与感知源的 IDLE 长连接共用——那条连接
长期挂在 IDLE 上且是独占的，共用会导致两边互相收到对方的响应。

@author aceFelix
"""

from __future__ import annotations

import contextlib
import imaplib
from dataclasses import dataclass
from typing import Any, Iterator

from agent.core.mail.parse import decode_header_value, parse_message

#: 默认 IMAP 端口（993 = SSL；其余走 STARTTLS）
DEFAULT_IMAP_PORT = 993

#: 本客户端在 IMAP ``ID`` 命令里上报的身份（网易等厂商会看这个）。
#: 名称取项目名，版本只作标识，不含任何用户信息。
_IMAP_CLIENT_ID = '("name" "jarvis" "version" "1.0" "vendor" "jarvis")'

# imaplib 的命令状态表里**默认没有 ID**，直接 _simple_command 会被
# “command ID illegal in state AUTH” 拒掉（与 SEARCH 一样的机制）。
# 注册成 AUTH/SELECTED 可用，才能在登录后立刻上报。
if "ID" not in imaplib.Commands:  # pragma: no cover - 进程级一次性注册
    imaplib.Commands["ID"] = ("NONAUTH", "AUTH", "SELECTED")

#: 单次搜索最多返回多少封（新的在前）
DEFAULT_SEARCH_LIMIT = 20

#: 单次 FETCH 上限，防御性限制
MAX_FETCH_IDS = 100

#: 已知「不是同域前缀互换」的厂商特例（smtp 主机 → 真实 imap 主机）。
#: 推错比推不出更糟：推错会报一个看不懂的连接失败，推不出至少能叫用户显式配。
_IMAP_HOST_EXCEPTIONS: dict[str, str] = {
    "smtp.office365.com": "outlook.office365.com",
}


def derive_imap_host(smtp_host: str) -> str:
    """从 SMTP 主机名推断 IMAP 主机名（推不出返回空串）。

    **为什么需要这层推导**：桌面壳「邮箱配置」面板只渲染 SMTP 那几项
    （协议侧的 ``EMAIL_FIELDS`` 里其实有 imap_*，但面板 UI 尚未铺开），
    于是绝大多数用户的 ``[email]`` 表里根本没有 ``imap_host``——收件功能
    就一直是“未配置”，而用户在面板上看到的是“已启用 / 授权码已配置”，
    很容易以为已经配好了（2026-10 实机碰到）。

    而 163/126/QQ/Gmail/腾讯企业邮这些厂商的主机名是**同域前缀互换**的
    （``smtp.163.com`` ↔ ``imap.163.com``），按这个惯例推断能覆盖绝大多数情况。

    只在首段**字面等于** ``smtp`` 时才换：自定义域名（``mail.mycorp.com``）
    推不出来，宁可返回空串让用户显式配置。

    @author aceFelix
    """
    host = (smtp_host or "").strip().lower()
    if not host:
        return ""
    if host in _IMAP_HOST_EXCEPTIONS:
        return _IMAP_HOST_EXCEPTIONS[host]
    parts = host.split(".")
    if len(parts) < 3 or parts[0] != "smtp":
        return ""
    parts[0] = "imap"
    return ".".join(parts)


@dataclass(frozen=True)
class ImapAccount:
    """一个 IMAP 账号的连接参数。"""

    host: str
    port: int
    user: str
    password: str
    mailbox: str = "INBOX"

    @property
    def configured(self) -> bool:
        """host / user / password 三者齐备才算可连。"""
        return bool(self.host and self.user and self.password)


def resolve_account(settings: Any) -> ImapAccount:
    """从 Settings 解析 IMAP 账号（含凭据与主机名回退口径）。

    回退规则：

    1. ``imap_host`` 留空时按 ``smtp_host`` 推断（见 :func:`derive_imap_host`）；
    2. ``imap_user`` / ``imap_password`` 留空时沿用 SMTP 那一套——163、QQ 等
       厂商收发共用同一个授权码，不强制用户录两遍。

    @author aceFelix
    """
    host = str(getattr(settings, "email_imap_host", "") or "").strip()
    if not host:
        host = derive_imap_host(str(getattr(settings, "email_smtp_host", "") or ""))
    return ImapAccount(
        host=host,
        port=int(getattr(settings, "email_imap_port", DEFAULT_IMAP_PORT) or DEFAULT_IMAP_PORT),
        user=str(
            getattr(settings, "email_imap_user", "")
            or getattr(settings, "email_smtp_user", "")
            or ""
        ),
        password=str(
            getattr(settings, "email_imap_password", "")
            or getattr(settings, "email_smtp_password", "")
            or ""
        ),
        mailbox=str(getattr(settings, "email_imap_mailbox", "INBOX") or "INBOX"),
    )


def imap_connect(host: str, port: int) -> Any:
    """建立 IMAP 连接：993 走 SSL，其余端口走 STARTTLS（143 等）。"""
    if int(port) == 993:
        return imaplib.IMAP4_SSL(host, int(port), timeout=30)
    conn = imaplib.IMAP4(host, int(port), timeout=30)
    conn.starttls()
    return conn


def send_client_id(conn: Any) -> None:
    """登录后向服务器上报客户端身份（``ID`` 命令）。

    **不发会怎样（实机踩过，2026-10）**：网易 163/126 的 IMAP 允许 `LOGIN`
    成功，但紧接着的 `SELECT/EXAMINE` 直接返回::

        EXAMINE Unsafe Login. Please contact kefu@188.com for help

    这段错误看起来像“授权码错了/账号不安全”，实际是**客户端没自报家门**。
    排查时极容易把人带到“重填授权码”的岔路上（而授权码完全是对的）。

    失败不影响流程：不是所有服务器都认 ``ID``，发不出去就算了——真正的
    问题会在随后的 SELECT 上暴露，比在这里抛错更好定位。

    @author aceFelix
    """
    try:
        conn.xatom("ID", _IMAP_CLIENT_ID)
    except Exception:
        return  # 服务器不认 ID：忽略，交给后续 SELECT 报真实错误


@contextlib.contextmanager
def open_inbox(account: ImapAccount) -> Iterator[Any]:
    """打开账号的收件箱（只读），退出时一定登出。

    用 contextmanager 而不是裸 try/finally：登出与 `logout` 里的异常都不该
    影响调用方拿到的结果（断连时 IMAP 服务器已经不可达，强行抛错只会盖掉
    真正的业务异常）。

    登录后先发 ``ID``：网易 163/126 不发就 ``SELECT`` 不进去，详见
    :func:`send_client_id`。
    """
    conn = imap_connect(account.host, account.port)
    try:
        conn.login(account.user, account.password)
        # 必须在 login 之后、select 之前：晚一步就会先撞上 Unsafe Login
        send_client_id(conn)
        # readonly=True：不把任何邮件标成已读，这是本模块的硬底线
        conn.select(account.mailbox, readonly=True)
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.logout()


def _uid_list(resp_data: list[Any]) -> list[str]:
    """把 IMAP SEARCH 的返回值解析成 UID 字符串列表。"""
    if not resp_data:
        return []
    raw = resp_data[0]
    if not raw:
        return []
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="replace")
    return [tok for tok in str(raw).split() if tok]


def _search_with_charset(conn: Any, charset: str | None, *criteria: Any) -> list[str]:
    """执行 UID SEARCH，charset 为 None 时走默认（ASCII）。"""
    args = [(charset.encode("ascii") if charset else None), *criteria]
    status, data = conn.uid("SEARCH", *args)
    if status != "OK":
        return []
    return _uid_list(data)


def search_uids(
    conn: Any,
    *criteria: Any,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[str]:
    """按 IMAP 条件搜索 UID，返回**新的在前**、最多 limit 个。

    中文关键字需要 UTF-8 charset，但并非所有服务器都支持——先带 charset 试，
    报错就退回不带 charset（此时中文多半搜不到，但至少不整条命令失败），
    最后再由调用方按"字面包含"在本地过滤。

    Args:
        conn: 已 select 的连接。
        criteria: IMAP 搜索条件，如 ``("SUBJECT", '"周报"')``。
        limit: 返回条数上限。

    Returns:
        UID 字符串列表，新的在前。
    """
    safe_limit = max(1, min(int(limit), MAX_FETCH_IDS))
    uids: list[str] = []
    ascii_only = all(
        isinstance(c, str) and c.isascii() for c in criteria
    )
    if ascii_only:
        uids = _search_with_charset(conn, None, *criteria)
    else:
        try:
            uids = _search_with_charset(conn, "UTF-8", *criteria)
        except (imaplib.IMAP4.error, UnicodeEncodeError, OSError):
            uids = []
        if not uids:
            # 退回 ASCII（会把中文关键字丢掉，由调用方本地过滤兜底）
            with contextlib.suppress(Exception):
                uids = _search_with_charset(conn, None, *criteria)
    # UID 升序 = 旧→新，取尾部再反转，得到"新的在前"
    return list(reversed(uids[-safe_limit:]))


def fetch_envelopes(conn: Any, uids: list[str]) -> list[dict[str, str]]:
    """批量拉信封头（FROM/SUBJECT/DATE/MESSAGE-ID），不含正文。

    用 ``BODY.PEEK`` 保证不置 \\Seen。
    """
    out: list[dict[str, str]] = []
    for uid in uids:
        try:
            status, data = conn.uid(
                "FETCH",
                uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])",
            )
        except Exception:
            continue
        if status != "OK" or not data:
            continue
        raw = _extract_literal(data)
        if not raw:
            continue
        import email as _email

        msg = _email.message_from_bytes(raw)
        out.append(
            {
                "uid": str(uid),
                "from": decode_header_value(msg.get("From", "")),
                "subject": decode_header_value(msg.get("Subject", "")),
                "date": decode_header_value(msg.get("Date", "")),
                "message_id": decode_header_value(msg.get("Message-ID", "")),
            }
        )
    return out


def _extract_literal(data: Any) -> bytes:
    """从 FETCH 返回里取出真正的字面量字节。

    IMAP 的 FETCH 响应形如 ``[(b'1 (BODY[] {123}', b'<123 bytes>'), b')']``，
    也可能被折成多段，这里只认"成对的 (头, 字面量)"。
    """
    if not data:
        return b""
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    # 少数服务器直接返回 bytes
    for item in data:
        if isinstance(item, bytes) and item and not item.startswith(b")"):
            return item
    return b""


def fetch_message(conn: Any, uid: str) -> dict[str, Any]:
    """拉一整封邮件并解析（正文已转纯文本，未截断）。

    Raises:
        RuntimeError: 拉取失败（UID 不存在 / 服务器拒绝）。
    """
    status, data = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError(f"拉取邮件失败（uid={uid}）: {status}")
    raw = _extract_literal(data)
    if not raw:
        raise RuntimeError(f"邮件内容为空（uid={uid}）")
    parsed = parse_message(raw)
    parsed["uid"] = str(uid)
    return parsed


def fetch_thread_headers(conn: Any, uid: str) -> dict[str, str]:
    """拉回复所需的线程头：Message-ID / References / Subject / From。

    「回复」必须在对方邮件客户端里显示成同一条对话，靠的就是
    ``In-Reply-To`` 与 ``References`` 两个头——只把原文贴进正文是不够的。
    """
    status, data = conn.uid(
        "FETCH",
        str(uid),
        "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID REFERENCES SUBJECT FROM DATE)])",
    )
    if status != "OK":
        raise RuntimeError(f"拉取邮件头失败（uid={uid}）: {status}")
    raw = _extract_literal(data)
    if not raw:
        raise RuntimeError(f"邮件头为空（uid={uid}）")
    import email as _email

    msg = _email.message_from_bytes(raw)
    return {
        "message_id": decode_header_value(msg.get("Message-ID", "")),
        "references": decode_header_value(msg.get("References", "")),
        "subject": decode_header_value(msg.get("Subject", "")),
        "from": decode_header_value(msg.get("From", "")),
        "date": decode_header_value(msg.get("Date", "")),
    }
