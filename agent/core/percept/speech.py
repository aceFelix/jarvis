r"""感知事件的口播版 —— 把"给人眼看的摘要"改写成"给人耳听的一句话"。

为什么要单独一层：`payload.summary` 是给面板与 `events.jsonl` 留痕写的，里面
带着进程名（`explorer.exe`）、完整路径（`C:\Users\…\.jarvis`）和冒号分隔——
这些在屏幕上一眼能懂，交给 TTS 就成了"切到 explorer 点 e x e 冒号 C 冒号
反斜杠"的机器念法（2026-10 人工走查反馈）。

本层**只做播报视图，不改留痕**：事实仍以 `summary` 与 payload 原始字段为准，
可查可回放；口播文本是派生值，不落盘，免得 jsonl 里出现"同一件事两种写法"。

与 `agent/voice/tts_text.normalize_for_speech` 的分工：那个函数管"任意文本
的读法"（路径、后缀、时间、标点），是通用安全网；本模块管"这句话该说什么"
（应用名友好化、耳听不下的样本列表省略），是感知层专属语义。两者叠加使用。

@author aceFelix
"""

from __future__ import annotations

import re
from typing import Any

#: 进程名 → 口语应用名。键为小写且已剥掉 `.exe`；未命中的走 `friendly_app`
#: 的兜底（剥后缀），所以这张表只需要收录"念出来别扭"的那些。
_APP_NAMES: dict[str, str] = {
    "explorer": "资源管理器",
    "mintty": "终端",
    "windowsterminal": "终端",
    "wt": "终端",
    "cmd": "命令行",
    "powershell_ise": "PowerShell",
    "electron": "桌面工作台",
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "code": "VS Code",
    "qoder cn ide": "Qoder",
    "qoder": "Qoder",
    "typora": "Typora",
    "notepad++": "记事本",
    "notepad": "记事本",
    "snipaste": "截图工具",
    "shellexperiencehost": "系统面板",
    "searchui": "系统搜索",
    "taskmgr": "任务管理器",
    "applicationframehost": "系统应用",
    "wechatapp": "微信",
    "wechat": "微信",
    "dingtalk": "钉钉",
    "feishu": "飞书",
    "qq": "QQ",
    "telegram": "Telegram",
    "discord": "Discord",
    "cloudmusic": "网易云音乐",
    "spotify": "Spotify",
    "winword": "Word",
    "excel": "Excel",
    "powerpnt": "PowerPoint",
    "teams": "Teams",
    "ms-teams": "Teams",
}

_EXE = re.compile(r"(?i)\.exe$")
# 窗口标题里的"文档 - 工作区 - 应用"尾巴：耳朵只需要第一段
_TITLE_TAIL = re.compile(r"\s+[-–—]\s+\S.*$", re.DOTALL)
# 面板截断留下的省略号
_TITLE_ELLIPSIS = re.compile(r"(…|\.{3})\s*$")
# 终端提示符样式的标题（`MINGW64:/e/...`、`~/proj`）：念它没有信息量
_TERM_PROMPT = re.compile(r"(?i)^[a-z0-9._-]*:?[~/]\\?[\w/.~+-]*$")
_RE_TRAILING_JUNK = re.compile(r"[，、\-–—\s]+$")

#: 口播时标题保留的最大字数（留痕里是 240，耳朵只需要一瞥）
_TITLE_SPEECH_MAX = 24


def _trim_junk(text: str) -> str:
    """去掉尾部悬空的分隔符（"BLUEPRINT -" 念起来像说一半断掉了）。"""
    return _RE_TRAILING_JUNK.sub("", text).strip()


def friendly_app(process: str) -> str:
    """进程名 → 能念出口的应用名。

    未命中映射表时剥掉 `.exe` 原样返回：宁可念"Ollama"也不念
    "Ollama 点 e x e"，也不假装知道它是什么。
    """
    raw = str(process or "").strip()
    if not raw:
        return ""
    stem = _EXE.sub("", raw).strip()
    return _APP_NAMES.get(stem.lower(), stem or raw)


def _spoken_title(title: str) -> str:
    """窗口标题 → 口播片段；不值得念的（终端提示符、空）返回空串。"""
    text = str(title or "").strip()
    if not text:
        return ""
    # 先剥面板截断的省略号：不先剥，“MINGW64:/e/…” 这种提示符就认不出来了
    text = _TITLE_ELLIPSIS.sub("", text)
    if not text or _TERM_PROMPT.match(text):
        return ""
    text = _TITLE_TAIL.sub("", text)             # 只留第一段（文档/页面名）
    text = _trim_junk(text)
    if len(text) > _TITLE_SPEECH_MAX:
        text = _trim_junk(text[:_TITLE_SPEECH_MAX])
    return text


def spoken_summary(event: Any) -> str:
    """感知事件 → 一句可以念出来的话。

    只给"念出来确实更好听"的两类事件建专用句式（window / file），其余
    源直接沿用 `summary`：VCS 与来信的摘要本来就是散文（"jarvis 有 8 项
    未提交变更"），套模板反而生硬。
    """
    from agent.voice.tts_text import normalize_for_speech

    source = str(getattr(event, "source", "") or "")
    kind = str(getattr(event, "kind", "") or "")
    payload = getattr(event, "payload", None)
    data = payload if isinstance(payload, dict) else {}

    if source == "window":
        process = str(data.get("process") or "")
        title = _spoken_title(str(data.get("title") or ""))
        if not process and not title:
            # 源没给结构化字段（老事件、自定义源）时退回摘要：
            # 播报不能比留痕更模糊，“切到别的应用”不如原句有用。
            return normalize_for_speech(event.summary())
        app = friendly_app(process)
        if kind == "focus_linger":
            minutes = data.get("minutes", "")
            return normalize_for_speech(
                f"你在 {app or '这个应用'} 连续待了 {minutes} 分钟了"
            )
        line = f"切到 {app or '另一个窗口'}"
        return normalize_for_speech(f"{line}，{title}" if title else line)

    if source == "file":
        name = str(data.get("dir_name") or "")
        if not name:
            return normalize_for_speech(event.summary())
        added = data.get("added") or 0
        if kind == "dir_grew":
            # 样本文件名不进播报：耳朵记不住五个文件名，屏幕上照旧有
            return normalize_for_speech(f"{name} 里堆了 {added} 个新文件")
        samples = [str(s) for s in (data.get("samples") or []) if str(s)]
        if len(samples) == 1:
            return normalize_for_speech(f"{name} 新增了 {samples[0]}")
        return normalize_for_speech(event.summary())

    return normalize_for_speech(event.summary())
