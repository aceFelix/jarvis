"""提醒的静默送达口径：通道解析 + 静默邮件（2026-10-22）。

从 `agent/serve/hub.py` 拆出（aceFelix）：hub 已经承载 serve 事件队列、感知总线、
任务脊柱接线与提醒编排，把“静默时段怎么送达”这段（通道解析 + 邮件正文 + 副作用
入账）独立出来后职责更纯——这段逻辑与事件队列无关，也不该受 hub 的行数预算挤压。

口径要点（机制详解见 `docs/architecture/16-W1判断层.md` 第四节）：

- 用户自设提醒命中静默时段/日额度时**降级投递**：气泡与系统通知照投、TTS 不朗读；
- `sms` 通道是留位，尚未接短信服务商，配了警告一次并忽略，绝不假装已发；
- 邮件是外发不可逆动作，发成功必须记一条 `reversible=False` 的副作用账。

@author aceFelix
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jarvis.serve.quiet")

#: 提醒在预算权重表里的源名（`policy/budget.py::_DEFAULT_SOURCE_WEIGHTS["schedule"]`）。
#: 名字沿用权重表而不是 kind（reminder），两边各叫各的会让人误以为有两个源。
REMINDER_NOTIFY_SOURCE = "schedule"
REMINDER_NOTIFY_KIND = "reminder"
REMINDER_NOTIFY_TITLE = "贾维斯提醒"
#: 用户明确要过的提醒，紧迫度给到与简报同级（否则乘上权重后容易被门槛误裁）
REMINDER_URGENCY = 0.9

#: 静默时段/额度用尽时的送达通道。`sms` 是**留位**：尚未接服务商（要 AccessKey、
#: 签名与模板备案），配了会警告并忽略，绝不假装已发。
QUIET_CHANNEL_SCREEN = "screen"
QUIET_CHANNEL_EMAIL = "email"
QUIET_CHANNEL_SMS = "sms"
QUIET_CHANNELS_IMPLEMENTED = frozenset({QUIET_CHANNEL_SCREEN, QUIET_CHANNEL_EMAIL})

QUIET_EMAIL_SUBJECT_PREFIX = "贾维斯静默提醒 · "


def parse_channels(raw: object) -> tuple[set[str], list[str]]:
    """把配置里的通道列表拆成（已实现集合, 未识别名单）。

    只负责解析、不写日志：警告该由调用方决定刷几次——提醒到期是高频回调，
    每条都刷一遍“sms 未接入”本身就成了新的打扰。大小写与首尾空白都收。

    Args:
        raw: `Settings.notify_reminder_channels` 原值（TOML 数组，异常类型也不抛）。

    Returns: 已实现的通道集合 + 未识别（含未接入）的通道名列表。

    @author aceFelix
    """
    out: set[str] = set()
    unknown: list[str] = []
    for item in (raw or []):
        ch = str(item).strip().lower()
        if not ch:
            continue
        if ch in QUIET_CHANNELS_IMPLEMENTED:
            out.add(ch)
        else:
            unknown.append(ch)
    return out, unknown


def build_quiet_email(settings: Any, task: Any) -> dict[str, str]:
    """静默提醒邮件的收件人/主题/正文。

    `to` 留空时不在这里填默认收件人——回退 `[email].default_recipient` 是
    SendEmailTool 自己的规则，在这里再实现一遍就会有两份口径。
    """
    to = str(getattr(settings, "notify_quiet_email_to", "") or "")
    return {
        "to": to,
        "subject": f"{QUIET_EMAIL_SUBJECT_PREFIX}{task.content[:30]}",
        "body": (
            f"原定 {task.trigger_at} 的提醒（当时处于静默时段，未语音播报）：\n\n"
            f"    {task.content}\n"
        ),
    }


def send_quiet_email(settings: Any, task: Any) -> tuple[bool, str]:
    """发一封静默提醒邮件并记账，返回 (是否送达, 摘要或失败原因)。

    不进 `[actions]` 延时窗口：这封信的目的就是“此刻送达”，再拖 30 秒就违背初衷；
    但**要记入副作用账本**（外发不可逆，`/undo` 得能如实说撤不回）。发信失败既不
    回补气泡也不重投——降级投递本身已经是一条送达记录。

    ⚠ 内部是阻塞 SMTP IO（timeout 30s），调用方必须放在自己的工作线程里
    （serve 侧见 `hub._send_quiet_email`）。

    @author aceFelix
    """
    from agent.tools.extensions.email_tool import send_email_now

    args = build_quiet_email(settings, task)
    try:
        detail = send_email_now(settings, args)
    except Exception as e:
        logger.warning("静默提醒邮件发送失败（不重投气泡）: %s", e)
        return False, f"{type(e).__name__}: {e}"
    note_effect(settings, args["to"], args["subject"])
    logger.info("静默提醒已改邮件送达: %s", detail.splitlines()[0] if detail else "")
    return True, detail


def note_effect(settings: Any, to: str, subject: str) -> None:
    """给已发出的静默邮件记一条不可逆副作用账（`/undo` 据此如实报“撤不回”）。"""
    try:
        from agent.core.actions import UNDO_NONE, EffectRecord, get_journal

        get_journal().record(EffectRecord(
            tool="SendEmail",
            args={"to": to, "subject": subject},
            preview=f"静默提醒邮件 → {to or '默认收件人'}",
            reversible=False,
            undo_spec={"kind": UNDO_NONE, "reason": "该动作已离开本机，无法撤回"},
            session_id="serve",
            workdir=str(getattr(settings, "workdir", "") or ""),
            permission_mode=str(
                getattr(settings, "permission_mode", "default") or "default"
            ),
        ))
    except Exception as e:  # 入账失败不影响已经发出去的信，只让账本少一笔
        logger.warning("静默邮件入账失败（不影响已发出的信）: %s", e)
