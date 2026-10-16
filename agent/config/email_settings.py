"""邮箱服务配置：读取、校验、外科式落盘与运行时生效。

背景（aceFelix）：管家服务「邮箱服务」面板需要让桌面前端查看当前发件邮箱、修改
邮箱账号并配置 SMTP 服务信息（host/port/账号/授权码/发件人/默认收件人）。
W2（阶段 9）加了 IMAP **收件**配置（mail 事件源只需读信封头），同一张 [email]
表、同一套 secret 口径。

为什么不复用 desktop_settings.py 的白名单：
- 那边只支持 bool/int/float/time，且注释明确「密钥（api_key 等）一律不入 SCHEMA」；
- 其 `_toml_literal` 用 f'"{value}"' 不做转义，无法安全写入含引号/反斜杠的授权码。
故本模块独立承载 email 域配置，遵循与模型编辑 `has_key` 相同的安全范式：
- 读：`read_email_config` **不回传授权码明文**，只回 `has_password` 布尔；
- 写：`email.config.set` 中授权码**留空 = 保持原值**（与 ModelEditPayload.api_key 同口径）。

字段映射真源：`EMAIL_FIELDS`（协议键 → settings.toml [email] 字段 + Settings 属性 + 类型）。
落盘为「单节多字段」外科式写 ~/.jarvis/settings.toml（保留注释与其他字段/节）。

@author aceFelix
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SETTINGS_PATH = Path.home() / ".jarvis" / "settings.toml"


@dataclass(frozen=True)
class EmailField:
    """单个邮箱配置项的元数据。"""

    key: str  # 协议键（email.config.get/set payload 字段名）
    field: str  # settings.toml [email] 表字段名
    attr: str  # Settings 实例属性名
    kind: str  # bool / int / str
    secret: bool = False  # True=授权码类，读不回传明文、写留空=保持


# 协议键顺序即前端表单渲染顺序
EMAIL_FIELDS: tuple[EmailField, ...] = (
    EmailField("enabled", "enabled", "email_enabled", "bool"),
    EmailField("smtp_host", "smtp_host", "email_smtp_host", "str"),
    EmailField("smtp_port", "smtp_port", "email_smtp_port", "int"),
    EmailField("smtp_user", "smtp_user", "email_smtp_user", "str"),
    EmailField("smtp_password", "smtp_password", "email_smtp_password", "str", secret=True),
    EmailField("sender", "sender", "email_sender", "str"),
    EmailField("default_recipient", "default_recipient", "email_default_recipient", "str"),
    # 收件（W2 mail 源）：imap_password 可与 smtp_password 相同（163/QQ 同一个授权码），
    # 留空时源回退到 smtp_password，所以不强制用户录两遍。
    EmailField("imap_host", "imap_host", "email_imap_host", "str"),
    EmailField("imap_port", "imap_port", "email_imap_port", "int"),
    EmailField("imap_user", "imap_user", "email_imap_user", "str"),
    EmailField("imap_password", "imap_password", "email_imap_password", "str", secret=True),
    EmailField("imap_mailbox", "imap_mailbox", "email_imap_mailbox", "str"),
)

FIELD_BY_KEY: dict[str, EmailField] = {f.key: f for f in EMAIL_FIELDS}

# SMTP 端口合法区间（含）
PORT_MIN = 1
PORT_MAX = 65535


def read_email_config(settings: Any) -> dict[str, Any]:
    """读取邮箱配置运行时值（两类授权码都只回布尔，绝不回明文）。

    Args:
        settings: 全局 Settings 实例。

    Returns:
        非秘密字段的运行时值 + `has_password`（SMTP 授权码是否已配）
        + `has_imap_password`（IMAP 可用凭据：自身或回退到 SMTP 授权码）
        + `imap_ready`（host/账号/凭据三者齐备，面板据此显示“收件已就绪”）。
    """
    result: dict[str, Any] = {}
    for f in EMAIL_FIELDS:
        if f.secret:
            continue
        value = getattr(settings, f.attr, None)
        if f.kind == "bool":
            result[f.key] = bool(value)
        elif f.kind == "int":
            result[f.key] = int(value) if value is not None else None
        else:
            result[f.key] = "" if value is None else str(value)
    smtp_password = bool(getattr(settings, "email_smtp_password", ""))
    imap_password = bool(getattr(settings, "email_imap_password", ""))
    result["has_password"] = smtp_password
    # IMAP 允许回退到 SMTP 授权码，所以“有凭据”= 两者任一已配
    result["has_imap_password"] = imap_password or smtp_password
    result["imap_ready"] = bool(
        str(getattr(settings, "email_imap_host", "") or "").strip()
        and str(getattr(settings, "email_imap_user", "")
                or getattr(settings, "email_smtp_user", "") or "").strip()
        and result["has_imap_password"]
    )
    return result


def validate_email_config(data: dict[str, Any]) -> dict[str, Any]:
    """校验 email.config.set 的入参并返回规范化后的补丁（仅含实际提交的键）。

    规则：
    - 未知键直接忽略（协议只认 EMAIL_FIELDS 内的字段）；
    - 类型不符 / 端口超范围抛 ValueError（回执 ok=false）；
    - 授权码为 secret（SMTP 与 IMAP 两个）：**空字符串 = 保持原值不变**（不写入补丁），
      非空才纳入补丁；非字符串（None 等）同样视为「未修改」。

    Args:
        data: 协议 payload（可含一个或多个邮箱配置字段）。

    Returns:
        规范化补丁 ``{key: value}``；可能为空 dict（调用方据此判「无有效修改」）。
    """
    patch: dict[str, Any] = {}
    for key, value in data.items():
        field = FIELD_BY_KEY.get(key)
        if field is None:
            continue
        if field.secret:
            # 授权码留空 = 保持不变（明文不回传、不覆盖）
            if isinstance(value, str) and value != "":
                patch[key] = value
            continue
        if field.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须为布尔值")
            patch[key] = value
        elif field.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} 必须为整数")
            if value < PORT_MIN or value > PORT_MAX:
                raise ValueError(f"{key} 必须在 {PORT_MIN}-{PORT_MAX} 之间")
            patch[key] = value
        else:  # str
            if not isinstance(value, str):
                raise ValueError(f"{key} 必须为字符串")
            patch[key] = value
    return patch


def apply_email_config(settings: Any, patch: dict[str, Any]) -> None:
    """把规范化补丁写进运行时 Settings 实例（SendEmailTool 每次现读，立即生效）。"""
    for key, value in patch.items():
        setattr(settings, FIELD_BY_KEY[key].attr, value)


def _toml_literal(field: EmailField, value: Any) -> str:
    """配置值转 TOML 字面量（字符串做转义，避免授权码含引号/反斜杠时写坏文件）。"""
    if field.kind == "bool":
        return "true" if value else "false"
    if field.kind == "int":
        return str(int(value))
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_email_config(patch: dict[str, Any]) -> bool:
    """外科式落盘多个邮箱配置项到 ~/.jarvis/settings.toml 的 [email] 表。

    - 节存在：逐字段替换已有行；节内缺失的字段插在节头后（保持注释与其他字段）；
    - 节不存在：文件末尾追加 [email] 节；文件不存在则创建最小文件。
    返回 True 表示成功；IO 失败返回 False（调用方回执报错，不改运行时）。
    """
    if not patch:
        return True
    lines = {FIELD_BY_KEY[k].field: _toml_literal(FIELD_BY_KEY[k], v) for k, v in patch.items()}
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_PATH.exists():
            body = "\n".join(f"{f} = {v}" for f, v in lines.items())
            SETTINGS_PATH.write_text(f"[email]\n{body}\n", encoding="utf-8")
            return True

        content = SETTINGS_PATH.read_text(encoding="utf-8")
        content = _upsert_section(content, "email", lines)
        SETTINGS_PATH.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False


def _upsert_section(content: str, section: str, lines: dict[str, str]) -> str:
    """在 TOML 文本中就地更新指定节的多个字段（节缺失则追加到末尾）。"""
    # 节头匹配不用 \s*$（\s 会吞换行导致后续切片偏移），只容忍行内空白
    section_match = re.search(rf"^\[{re.escape(section)}\][ \t]*$", content, re.MULTILINE)
    if not section_match:
        body = "\n".join(f"{f} = {v}" for f, v in lines.items())
        return content.rstrip() + f"\n\n[{section}]\n{body}\n"

    head_end = section_match.end() + 1  # 跳过节头后的换行
    rest = content[head_end:]
    nxt = re.search(r"^\[", rest, re.MULTILINE)
    sec_end = head_end + (nxt.start() if nxt else len(rest))
    section_text = content[head_end:sec_end]

    # 先替换已有字段，再收集缺失字段插到节头后
    missing: list[str] = []
    for field, literal in lines.items():
        if re.search(rf"^{re.escape(field)}\s*=", section_text, re.MULTILINE):
            section_text = re.sub(
                rf"^{re.escape(field)}\s*=.*$", f"{field} = {literal}", section_text, flags=re.MULTILINE
            )
        else:
            missing.append(f"{field} = {literal}")
    if missing:
        section_text = "\n".join(missing) + "\n" + section_text

    return content[:head_end] + section_text + content[sec_end:]
