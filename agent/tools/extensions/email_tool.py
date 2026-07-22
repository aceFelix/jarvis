"""邮件发送工具。

让 Jarvis 能够主动向用户或指定收件人发送邮件，常用于：
- 发送提醒、摘要、日报
- 转发重要通知到邮箱
- 把生成的报告/附件发送到指定邮箱

配置来源（按优先级）：
1. ``ToolContext.settings`` 中的邮件配置
2. ``~/.jarvis/settings.toml`` 中的 ``[email]`` 表
3. 工具参数中显式传入的收件人、主题、正文

安全说明：
- 发件账号密码/授权码只从配置读取，不会出现在工具参数或日志中。
- 默认 ASK 权限：发送邮件属于外发操作，执行前需要用户确认。
- 附件只接受本地文件路径，发送前会校验文件是否存在。

@author aceFelix
"""

from __future__ import annotations

import logging
import mimetypes
import smtplib
import uuid
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool

logger = logging.getLogger(__name__)

# 默认 163 邮箱配置（用户可在 settings.toml 中覆盖）
_DEFAULT_SMTP_HOST = "smtp.163.com"
_DEFAULT_SMTP_PORT = 465


class SendEmailTool(Tool):
    """发送邮件工具。

    根据 settings.toml 中的 ``[email]`` 配置连接 SMTP 服务器并发送邮件。
    如果配置未启用或缺失必要字段，会返回明确错误提示。
    """

    name = "SendEmail"
    description = (
        "发送邮件到指定收件人。"
        "用于用户说'发邮件给我'/'把结果发到邮箱'/'邮件提醒'等场景。"
        "收件人、主题、正文必填；支持抄送、密送和本地附件。"
        "如果用户没指定收件人，使用 settings.toml 中的 default_recipient。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "收件人邮箱地址。多个地址用英文逗号分隔。未提供时使用配置中的 default_recipient。",
            },
            "subject": {
                "type": "string",
                "description": "邮件主题。",
            },
            "body": {
                "type": "string",
                "description": "邮件正文。支持纯文本，可包含换行。",
            },
            "cc": {
                "type": "string",
                "description": "抄送地址，多个用英文逗号分隔（可选）。",
            },
            "bcc": {
                "type": "string",
                "description": "密送地址，多个用英文逗号分隔（可选）。",
            },
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "本地附件路径列表（可选）。路径相对于 workdir 或绝对路径。",
            },
        },
        "required": ["subject", "body"],
    }
    max_result_chars = 2000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        """发送邮件是外发写操作，返回 False。"""
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        """SMTP 连接不建议并发，返回 False。"""
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """发送邮件默认需要用户确认。"""
        to = args.get("to", "")
        subject = args.get("subject", "")
        return PermissionResult.ask(f"发送邮件给 {to or '默认收件人'}，主题: {subject or '(空)'}")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        """展示给用户的活动描述。"""
        if args is None:
            return "发送邮件"
        subject = args.get("subject", "")
        to = args.get("to", "")
        return f"发送邮件: {subject[:30]} → {to or '默认收件人'}"

    def _load_settings(self, ctx: ToolContext) -> Any:
        """从上下文或配置文件加载邮件设置。"""
        if ctx.settings is not None:
            return ctx.settings
        try:
            from agent.config.settings import load_settings

            return load_settings(ctx.workdir)
        except Exception as e:
            logger.warning("加载邮件配置失败: %s", e)
            return None

    def _build_email_addresses(self, raw: str) -> list[str]:
        """把逗号分隔的邮箱字符串拆分为列表并去空。"""
        return [addr.strip() for addr in raw.split(",") if addr.strip()]

    def _attach_file(self, msg: MIMEMultipart, path: Path) -> None:
        """把单个文件附加到邮件中。"""
        filename = path.name
        # 推测 MIME 类型
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)

        with path.open("rb") as f:
            part = MIMEBase(maintype, subtype)
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"',
        )
        msg.attach(part)

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行邮件发送。

        流程：
        1. 加载邮件配置并校验必填项。
        2. 解析收件人、抄送、密送。
        3. 构造邮件正文与附件。
        4. 连接 SMTP 服务器并发送。
        """
        settings = self._load_settings(ctx)
        if settings is None:
            return ToolResult.error("无法加载邮件配置，请检查 ~/.jarvis/settings.toml")

        # 邮件功能开关
        if not getattr(settings, "email_enabled", False):
            return ToolResult.error(
                "邮件功能未启用。请在 ~/.jarvis/settings.toml 中设置 [email].enabled = true。"
            )

        smtp_host = getattr(settings, "email_smtp_host", _DEFAULT_SMTP_HOST) or _DEFAULT_SMTP_HOST
        smtp_port = int(getattr(settings, "email_smtp_port", _DEFAULT_SMTP_PORT) or _DEFAULT_SMTP_PORT)
        smtp_user = getattr(settings, "email_smtp_user", "")
        smtp_password = getattr(settings, "email_smtp_password", "")
        sender = getattr(settings, "email_sender", "") or smtp_user
        default_recipient = getattr(settings, "email_default_recipient", "")

        if not smtp_user:
            return ToolResult.error("邮件配置不完整：缺少 smtp_user。请在 [email] 表中填写发件邮箱账号。")
        if not smtp_password:
            return ToolResult.error("邮件配置不完整：缺少 smtp_password（通常为邮箱授权码）。")
        if not sender:
            return ToolResult.error("邮件配置不完整：缺少 sender（发件人邮箱地址）。")

        # 解析收件人
        to_raw = args.get("to", "").strip()
        recipients = self._build_email_addresses(to_raw) if to_raw else []
        if not recipients:
            if default_recipient:
                recipients = [default_recipient]
            else:
                return ToolResult.error("未指定收件人，且配置中无 default_recipient。")

        cc_list = self._build_email_addresses(args.get("cc", ""))
        bcc_list = self._build_email_addresses(args.get("bcc", ""))
        subject = args.get("subject", "").strip()
        body = args.get("body", "").strip()
        attachment_paths = args.get("attachments", []) or []

        if not subject:
            return ToolResult.error("邮件主题不能为空。")
        if not body:
            return ToolResult.error("邮件正文不能为空。")

        # 构造邮件
        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        msg["Subject"] = subject
        # Message-ID 有助于部分邮件服务器反垃圾策略
        msg["Message-ID"] = f"<jarvis-{uuid.uuid4().hex}@{smtp_host}>"

        msg.attach(MIMEText(body, "plain", "utf-8"))

        # 处理附件
        workdir = Path(ctx.workdir) if ctx.workdir else Path.cwd()
        attached_names: list[str] = []
        for raw_path in attachment_paths:
            path = Path(raw_path)
            if not path.is_absolute():
                path = workdir / path
            path = path.resolve()
            if not path.is_file():
                return ToolResult.error(f"附件不存在: {path}")
            try:
                self._attach_file(msg, path)
                attached_names.append(path.name)
            except Exception as e:
                logger.warning("附加文件失败 %s: %s", path, e)
                return ToolResult.error(f"附加文件失败 {path}: {e}")

        # 所有实际接收者（用于 SMTP rcpt）
        all_recipients = recipients + cc_list + bcc_list

        # 连接 SMTP 并发送
        try:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
            try:
                server.login(smtp_user, smtp_password)
                server.sendmail(sender, all_recipients, msg.as_string())
            finally:
                server.quit()
        except smtplib.SMTPAuthenticationError as e:
            logger.warning("邮件认证失败: %s", e)
            return ToolResult.error(
                f"邮件认证失败：请检查 [email].smtp_user 和 [email].smtp_password（授权码）是否正确。"
            )
        except smtplib.SMTPException as e:
            logger.warning("SMTP 错误: %s", e)
            return ToolResult.error(f"SMTP 错误: {e}")
        except Exception as e:
            logger.warning("发送邮件失败: %s", e)
            return ToolResult.error(f"发送邮件失败: {e}")

        result = f"✓ 邮件已发送\n  收件人: {', '.join(recipients)}"
        if cc_list:
            result += f"\n  抄送: {', '.join(cc_list)}"
        if attached_names:
            result += f"\n  附件: {', '.join(attached_names)}"
        return ToolResult(data=result)
