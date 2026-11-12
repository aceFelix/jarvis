"""工具输出里的密钥脱敏。

## 为什么必须有这一层（2026-11 实测）

用户让 jarvis 看某个模型的 `api_key` 在哪，它 `grep` 了 `models.toml`——于是两把真实
密钥被原样贴进了工具结果，进而进了上下文、会话日志和渲染面板。它事后意识到"回复里
不要复述"，但**工具输出已经进去了**，补救不了。

结论：凭据不该靠模型自觉不回显，而应该在**读的时候就模糊掉**。模型仍然能判断
"这个键设过了没有、多长"，但拿不到真值。

## 判据

只脱敏"赋值形态 + 值像凭据"的片段，避免误伤：

- 键名命中 ``api_key / access_key / secret / token / password / authorization``；
- 值长度 ≥ 12 且不含空白（``test`` 这种假值、``已设置`` 这种中文占位不算）。

宁漏不误伤：把一句正常说明脱成 ``***`` 比漏一把真 key 更常见也更烦人。

@author aceFelix
"""

from __future__ import annotations

import re

#: 键名（等号/冒号左侧）
_SECRET_KEYS = (
    "api[_-]?key", "access[_-]?key", "secret", "token",
    "password", "passwd", "authorization", "private[_-]?key",
)

#: 形如 ``api_key = "sk-xxx"`` / ``"token": "abc"`` / ``default_api_key: sk-xxx``
#: 分组：1 键名 2 键后的可选引号 3 分隔符 4 值前引号 5 值
#:
#: 键名用 ``(?<![A-Za-z0-9])`` 而不是 ``\b``：``default_api_key`` 里下划线不是词边界，
#: ``\bapi_key`` 匹配不上，真 key 会整条漏过去（实测踩到）。
_ASSIGN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(" + "|".join(_SECRET_KEYS) + r")\b"
    r"([\"']?)"
    r"(\s*[=:]\s*)"
    r"([\"']?)"
    r"([^\s\"',]+)"
)

#: 值里允许出现的字符（真凭据通常是无空格的长串）；中文说明不算
_VALUE_OK_RE = re.compile(r"^[A-Za-z0-9_\-./+=:]+$")

#: 明显是占位/假值，不用脱敏（否则把 "test" 也糊掉，反而看不出人写了什么）
_PLACEHOLDERS = frozenset({
    "test", "null", "none", "true", "false", "changeme", "password", "xxx", "***",
})

#: 太短的值不当凭据（"hunter2" 这种照样脱，但 3 位以内基本是占位/单位）
_MIN_LEN = 6


def _mask(value: str) -> str:
    """保留"已设置 + 长度"，丢掉真值。"""
    return f"***（已设置，{len(value)} 位）"


def redact_secrets(text: str) -> str:
    """把文本里的凭据值替换掉；没有命中就原样返回（幂等、不改其它内容）。"""
    if not text:
        return text

    def _sub(m: re.Match[str]) -> str:
        key, pre_q, sep, quote, value = m.groups()
        if len(value) < _MIN_LEN or not _VALUE_OK_RE.match(value):
            return m.group(0)          # 不像凭据（占位/中文说明/路径），别动
        if value.lower() in _PLACEHOLDERS:
            return m.group(0)
        return f"{key}{pre_q}{sep}{quote}{_mask(value)}"

    return _ASSIGN_RE.sub(_sub, text)


def has_secret(text: str) -> bool:
    """这段文本里是否含凭据值（供测试与审计用）。"""
    return redact_secrets(text) != text
