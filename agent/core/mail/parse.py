"""邮件 MIME 解析：头解码、正文提取、HTML 转文本、截断。

为什么不复用感知层的同名函数：`core/percept/source_mail.py` 里那几个（
`decode_header_value` / `parse_envelope`）本来是"信封头解析"的局部实现，被
`EmailBox` 工具也要用。把口径搬到这里，让 percept 反过来 import，避免两处各写
一份解码规则（RFC2047 的坑：中文主题不解码会原样播成 `=?UTF-8?B?5oCO5LmI?=`）。

@author aceFelix
"""

from __future__ import annotations

import email
import email.header
import email.message
import re
from typing import Any

#: 单封邮件正文回传给模型的默认上限（字符）
DEFAULT_BODY_LIMIT = 8000

#: 附件只列名字，超过这个数也只报前 N 个（防一封邮件挂 50 个附件刷屏）
MAX_ATTACHMENT_NAMES = 20


def decode_header_value(raw: Any) -> str:
    """把 MIME 编码的邮件头（如 ``=?UTF-8?B?...?=``）解成可读串。

    中文邮件系统的主题/发件人几乎都是 RFC2047 编码，不解码会把
    ``=?UTF-8?B?5oCO5LmI?=`` 这种原样回传出去。

    ⚠️ 千万不能先把 ``raw`` 转成 str：``msg.get()`` 对非 ASCII 头可能返回
    ``email.header.Header`` 对象，而 ``str(Header)`` 走的是它的 ascii 输出路径，
    中文会直接变成一串 U+FFFD（实测"同一封"→ 9 个替换字符）。
    ``decode_header`` 对 Header 有专门的解码分支，必须把原对象交给它。
    """
    if not raw:
        return ""
    target: Any = raw
    if isinstance(raw, bytes):
        target = raw.decode("utf-8", errors="replace")
    try:
        parts = email.header.decode_header(target)
    except (ValueError, UnicodeDecodeError, TypeError):
        return str(target).strip()
    out: list[str] = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(charset or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(str(chunk))
    return "".join(out).strip()


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BR_RE = re.compile(r"<\s*(br|/p|/div|/tr|/li)\s*/?>", re.IGNORECASE)
_MULTI_NL_RE = re.compile(r"\n{3,}")
_SPACES_RE = re.compile(r"[ \t]{2,}")


def html_to_text(html: str) -> str:
    """把 HTML 正文粗转成纯文本。

    刻意不引第三方 HTML 解析器：邮件正文只需要"能读"，不需要排版保真；
    多加一个依赖换来的排版收益很低，而正则在这里足够稳（先剥 script/style，
    再把块级标签换成换行，最后去标签与多余空白）。
    """
    if not html:
        return ""
    text = _SCRIPT_RE.sub(" ", html)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    import html as html_mod

    text = html_mod.unescape(text)
    text = _SPACES_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def _repair_mojibake(text: str) -> str:
    """修复「UTF-8 字节被当成 latin-1」的乱码。

    很多客户端把非 ASCII 的附件名直接写成裸 UTF-8 字节而不做 RFC2231 编码，
    ``email`` 包按 latin-1 解出来就是 ``æx8a¥è¡¨.pdf`` 这种。反向编成
    latin-1 再按 UTF-8 解一次通常能还原；对真正的 latin-1 文本会失败，
    那就保持原样（不能把正常名字改坏）。
    """
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


_FILENAME_RE = re.compile(
    r"""filename(\*)?\s*=\s*(?:"([^"]*)"|([^;\s]+))""", re.IGNORECASE
)


def _attachment_name(part: email.message.Message) -> str:
    """取附件文件名。

    不能直接用 ``get_filename()``：它内部对「裸 UTF-8 字节的 filename」解码
    失败后返回的是一串 U+FFFD，信息已经丢了（实测「报表.pdf」→ 6 个替换字符）。
    所以先自己从原始 Content-Disposition 取字面量再修复 mojibake，
    取不到才退回 ``get_filename()``。

    @author aceFelix
    """
    disp = part.get("Content-Disposition")
    if disp:
        text = disp if isinstance(disp, str) else str(disp)
        m = _FILENAME_RE.search(text)
        if m:
            is_rfc2231 = bool(m.group(1))
            raw = m.group(2) if m.group(2) is not None else m.group(3)
            if raw:
                if is_rfc2231:
                    # RFC2231: charset'lang'percent-encoded
                    import urllib.parse

                    segs = raw.split("'", 2)
                    if len(segs) == 3:
                        return urllib.parse.unquote(
                            segs[2], encoding=segs[0] or "utf-8", errors="replace"
                        )
                    return urllib.parse.unquote(raw, errors="replace")
                return _repair_mojibake(decode_header_value(raw))
    name = part.get_filename()
    return _repair_mojibake(name) if name else ""


def _walk_body(msg: email.message.Message) -> tuple[str, bool]:
    """找可读正文，返回 (文本, 是否 HTML)。"""
    # 单段邮件
    if not msg.is_multipart():
        ctype = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if payload is None:  # 有些邮件是未编码的 str payload
            raw = msg.get_payload()
            plain = str(raw or "")
            return (html_to_text(plain) if ctype == "text/html" else plain, ctype == "text/html")
        charset = msg.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            text = payload.decode("utf-8", errors="replace")
        if ctype == "text/html":
            return (html_to_text(text), True)
        return (text, False)

    # multipart：优先 text/plain，其次 text/html（常见做法是先登记 html 备胎）
    html_fallback = ""
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/plain" and not part.get_filename():
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    text = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    text = payload.decode("utf-8", errors="replace")
                if text.strip():
                    return (text, False)
        elif ctype == "text/html" and not part.get_filename() and not html_fallback:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                try:
                    html_fallback = payload.decode(charset, errors="replace")
                except (LookupError, UnicodeDecodeError):
                    html_fallback = payload.decode("utf-8", errors="replace")
    if html_fallback:
        return (html_to_text(html_fallback), True)
    return ("", False)


def parse_message(raw: bytes) -> dict[str, Any]:
    """解析整封邮件，返回结构化字段。

    Args:
        raw: ``FETCH BODY[]`` 拿到的原始字节。

    Returns:
        ``{from, to, subject, date, message_id, references, body, is_html, attachments}``。
        正文已转成纯文本但**未截断**（截断交给调用方，便于它决定上限）。
    """
    msg = email.message_from_bytes(raw or b"")
    body, is_html = _walk_body(msg)
    attachments: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_filename() or "attachment" in str(
                part.get("Content-Disposition") or ""
            ).lower():
                name = _attachment_name(part)
                if name:
                    attachments.append(name)
    return {
        "from": decode_header_value(msg.get("From", "")),
        "to": decode_header_value(msg.get("To", "")),
        "subject": decode_header_value(msg.get("Subject", "")),
        "date": decode_header_value(msg.get("Date", "")),
        "message_id": decode_header_value(msg.get("Message-ID", "")),
        "references": decode_header_value(msg.get("References", "")),
        "body": body.strip(),
        "is_html": is_html,
        "attachments": attachments[:MAX_ATTACHMENT_NAMES],
    }


def truncate(text: str, limit: int = DEFAULT_BODY_LIMIT) -> tuple[str, bool]:
    """截断长正文，返回 (文本, 是否被截断)。

    为什么一定要截：正文会整段进对话上下文，一封长邮件能吃掉半个窗口；
    更糟的是超长不可信内容会放大提示注入的影响面（见 email_box 模块顶部）。
    """
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True
