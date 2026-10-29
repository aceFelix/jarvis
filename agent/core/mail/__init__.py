"""邮件收发底层：IMAP 只读访问与 MIME 解析。

分层：
- ``parse.py`` —— 纯函数（头解码、正文提取、HTML 转文本、截断），无 IO，好测。
- ``imap.py``  —— 连接、搜索、拉信封头/正文/线程头。

感知层（``core/percept/source_mail.py``）与工具层（``tools/extensions/email_box.py``）
共用这里的口径：**一律 ``BODY.PEEK`` + readonly**，读邮件不改 \\Seen 标记。

@author aceFelix
"""

from __future__ import annotations

from agent.core.mail.imap import (
    DEFAULT_SEARCH_LIMIT,
    ImapAccount,
    fetch_envelopes,
    fetch_message,
    fetch_thread_headers,
    imap_connect,
    open_inbox,
    resolve_account,
    search_uids,
)
from agent.core.mail.parse import (
    DEFAULT_BODY_LIMIT,
    decode_header_value,
    html_to_text,
    parse_message,
    truncate,
)

__all__ = [
    "DEFAULT_BODY_LIMIT",
    "DEFAULT_SEARCH_LIMIT",
    "ImapAccount",
    "decode_header_value",
    "fetch_envelopes",
    "fetch_message",
    "fetch_thread_headers",
    "html_to_text",
    "imap_connect",
    "open_inbox",
    "parse_message",
    "resolve_account",
    "search_uids",
    "truncate",
]
