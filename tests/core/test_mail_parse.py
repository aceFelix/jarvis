"""邮件 MIME 解析回归测试（纯函数，无网络）。

重点守一条实测踩过的坑：``msg.get("Subject")`` 对非 ASCII 头返回的是
``email.header.Header`` 对象而不是 str，先 ``str()`` 再解码会把中文变成
一串 U+FFFD（"同一封" → 9 个替换字符）。

@author aceFelix
"""

from __future__ import annotations

import email

from agent.core.mail.parse import (
    DEFAULT_BODY_LIMIT,
    decode_header_value,
    html_to_text,
    parse_message,
    truncate,
)


def _raw(subject: str, body: str, extra: str = "") -> bytes:
    return (
        f"From: 小薇 <xw@example.com>\r\n"
        f"To: me@example.com\r\n"
        f"Subject: {subject}\r\n"
        f"Date: Tue, 16 Oct 2026 09:00:00 +0800\r\n"
        f"{extra}"
        f"\r\n{body}\r\n"
    ).encode()


class TestDecodeHeaderValue:
    def test_header_object_is_not_stringified_first(self) -> None:
        """非 ASCII 原生头：msg.get() 给的可能是 Header 对象，必须原样交给 decode_header。"""
        msg = email.message_from_bytes(_raw("同一封", "正文"))
        value = msg.get("Subject")
        assert decode_header_value(value) == "同一封"

    def test_rfc2047_encoded_header(self) -> None:
        """标准 MIME 编码头（=?UTF-8?B?...?=）正常解码。"""
        encoded = email.header.Header("周报摘要", "utf-8").encode()
        assert decode_header_value(encoded) == "周报摘要"

    def test_empty_and_none(self) -> None:
        assert decode_header_value("") == ""
        assert decode_header_value(None) == ""

    def test_bytes_input_falls_back(self) -> None:
        assert decode_header_value("hello".encode()) == "hello"


class TestHtmlToText:
    def test_strips_tags_and_scripts(self) -> None:
        html = "<html><style>p{color:red}</style><body><p>第一段</p><p>第二段</p></body></html>"
        text = html_to_text(html)
        assert "第一段" in text
        assert "第二段" in text
        assert "<p>" not in text
        assert "color:red" not in text

    def test_unescapes_entities(self) -> None:
        assert "a<b" in html_to_text("<div>a&lt;b</div>")


class TestParseMessage:
    def test_prefers_text_plain(self) -> None:
        raw = (
            "From: a@b.com\r\nSubject: t\r\n"
            'Content-Type: multipart/alternative; boundary="X"\r\n\r\n'
            "--X\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n纯文本版本\r\n"
            "--X\r\nContent-Type: text/html; charset=utf-8\r\n\r\n<b>HTML 版本</b>\r\n"
            "--X--\r\n"
        ).encode()
        parsed = parse_message(raw)
        assert parsed["body"] == "纯文本版本"
        assert parsed["is_html"] is False

    def test_falls_back_to_html(self) -> None:
        raw = (
            "From: a@b.com\r\nSubject: t\r\n"
            "Content-Type: text/html; charset=utf-8\r\n\r\n<p>只有 HTML</p>\r\n"
        ).encode()
        parsed = parse_message(raw)
        assert "只有 HTML" in parsed["body"]
        assert parsed["is_html"] is True

    def test_collects_attachment_names(self) -> None:
        """RFC2231 编码的中文附件名（主流客户端的写法）正确解码。"""
        raw = (
            "From: a@b.com\r\nSubject: t\r\n"
            'Content-Type: multipart/mixed; boundary="X"\r\n\r\n'
            "--X\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n正文\r\n"
            "--X\r\nContent-Type: application/pdf\r\n"
            "Content-Disposition: attachment; filename*=utf-8''%E6%8A%A5%E8%A1%A8.pdf\r\n\r\nAAAA\r\n"
            "--X--\r\n"
        ).encode()
        parsed = parse_message(raw)
        assert parsed["attachments"] == ["报表.pdf"]
        assert "AAAA" not in parsed["body"]

    def test_plain_utf8_filename_does_not_crash(self) -> None:
        """裸 UTF-8 文件名（不符 RFC 的客户端）不崩，也不把正文误当附件。

        这种写法在 ``message_from_bytes`` 阶段就已经丢信息（变成 U+FFFD），
        无法可靠还原；这里只保证不崩且名字非空，不要求还原成中文。
        """
        raw = (
            "From: a@b.com\r\nSubject: t\r\n"
            'Content-Type: multipart/mixed; boundary="X"\r\n\r\n'
            "--X\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n正文\r\n"
            "--X\r\nContent-Type: application/pdf\r\n"
            'Content-Disposition: attachment; filename="报表.pdf"\r\n\r\nAAAA\r\n'
            "--X--\r\n"
        ).encode()
        parsed = parse_message(raw)
        assert len(parsed["attachments"]) == 1
        assert parsed["attachments"][0].endswith(".pdf")

    def test_plain_text_attachment_not_mistaken_for_body(self) -> None:
        """text/plain 附件不能当成正文：否则附件内容会被当成邮件正文回传。"""
        raw = (
            "From: a@b.com\r\nSubject: t\r\n"
            'Content-Type: multipart/mixed; boundary="X"\r\n\r\n'
            "--X\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n真正的正文\r\n"
            "--X\r\nContent-Type: text/plain; charset=utf-8\r\n"
            'Content-Disposition: attachment; filename="log.txt"\r\n\r\n附件内容\r\n'
            "--X--\r\n"
        ).encode()
        parsed = parse_message(raw)
        assert "真正的正文" in parsed["body"]
        assert "附件内容" not in parsed["body"]
        assert parsed["attachments"] == ["log.txt"]

    def test_thread_headers_present(self) -> None:
        raw = _raw("主题", "正文", extra="Message-ID: <m1@x>\r\nReferences: <m0@x>\r\n")
        parsed = parse_message(raw)
        assert parsed["message_id"] == "<m1@x>"
        assert parsed["references"] == "<m0@x>"


class TestTruncate:
    def test_short_text_untouched(self) -> None:
        text, cut = truncate("短正文", 100)
        assert text == "短正文"
        assert cut is False

    def test_long_text_cut_and_flagged(self) -> None:
        text, cut = truncate("啊" * (DEFAULT_BODY_LIMIT + 50))
        assert len(text) == DEFAULT_BODY_LIMIT
        assert cut is True
