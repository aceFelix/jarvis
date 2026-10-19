"""settings.toml 的「单节多字段」外科式写入工具。

为什么单独成模块（aceFelix，2026-10）：桌面面板要能改配置，就必须原地改
`~/.jarvis/settings.toml` 里某一节的几个键，同时**保住用户的注释与其他字段**。
这套逻辑先出现在 `desktop_settings.save_setting`（单字段），又出现在
`email_settings._upsert_section`（多字段），两份实现同构——第三处（W2 感知层的
`percept_settings`）再抄一遍就是三份漂移风险，故抽成唯一实现。

行为约定（三份实现里原本一致的那部分）：

- 节存在：逐字段替换已有行；节内没有的字段插在节头后（注释与其他行原样保留）；
- 节不存在：文件末尾追加新节；
- 文件不存在：创建只含该节的最小文件；
- 只用标准库、不解析整个 TOML（不引入 tomli-w 这类写库，也不丢注释）。

@author aceFelix
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

#: 用户级配置文件的相对位置（不用模块级常量存全路径：`Path.home()` 必须在**调用时**
#: 求值，测试靠 patch `Path.home` 指向 tmp_path，import 时就拼好路径会测不到。
JARVIS_DIRNAME = ".jarvis"
SETTINGS_FILENAME = "settings.toml"


def settings_path() -> Path:
    """当前用户级 settings.toml（每次现取 home，便于测试隔离与 HOME 变动）。"""
    return Path.home() / JARVIS_DIRNAME / SETTINGS_FILENAME


def str_literal(value: str) -> str:
    """字符串 → TOML 基本字符串字面量（转义反斜杠与引号）。

    授权码、邮箱地址这类用户输入可能含引号/反斜杠，不做转义会写坏整份配置。
    """
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def list_literal(items: Iterable[str]) -> str:
    """字符串列表 → TOML 数组字面量（``["a", "b"]``，逐项走 str_literal）。"""
    body = ", ".join(str_literal(str(item)) for item in items)
    return f"[{body}]"


def bool_literal(value: bool) -> str:
    """布尔 → ``true`` / ``false``。"""
    return "true" if value else "false"


def upsert_section(content: str, section: str, lines: dict[str, str]) -> str:
    """在 TOML 文本里就地更新指定节的多个字段（节缺失则追加到末尾）。

    Args:
        content: 整份 TOML 文本。
        section: 节名（不含方括号）。
        lines: ``{字段名: 已格式化的 TOML 字面量}``，按插入顺序书写。

    Returns:
        改写后的完整文本；未命中的字段一律插在节头之后。
    """
    # 节头匹配不用 \s*$（\s 会吞换行导致后续切片偏移），只容忍行内空白
    section_match = re.search(rf"^\[{re.escape(section)}\][ \t]*$", content, re.MULTILINE)
    if not section_match:
        body = "\n".join(f"{field} = {literal}" for field, literal in lines.items())
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
                rf"^{re.escape(field)}\s*=.*$",
                f"{field} = {literal}",
                section_text,
                flags=re.MULTILINE,
            )
        else:
            missing.append(f"{field} = {literal}")
    if missing:
        section_text = "\n".join(missing) + "\n" + section_text

    return content[:head_end] + section_text + content[sec_end:]


def save_section(section: str, lines: dict[str, str], *, path: Path | str | None = None) -> bool:
    """外科式落盘若干字段到指定节（空补丁直接成功，不碰文件）。

    Args:
        section: TOML 节名，如 ``percept`` / ``email`` / ``tts``。
        lines: ``{字段名: TOML 字面量}``。
        path: 目标文件，默认用户级 settings.toml（测试可注入临时路径）。

    Returns:
        True 表示写入成功；IO 失败返回 False——调用方据此回执报错，
        **不要**再改运行时 Settings，避免「本次生效、重启回退」的口径分裂。
    """
    if not lines:
        return True
    target = Path(path) if path else settings_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            body = "\n".join(f"{field} = {literal}" for field, literal in lines.items())
            target.write_text(f"[{section}]\n{body}\n", encoding="utf-8")
            return True
        content = target.read_text(encoding="utf-8")
        target.write_text(upsert_section(content, section, lines), encoding="utf-8")
        return True
    except OSError:
        return False
