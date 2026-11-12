"""提示词门禁：提到的东西必须真实存在。

## 为什么要有这条

2026-11-11 实测：用户问"把权限模式切回 default"，模型答"按 **Shift+Tab** 循环切换"——
主循环根本没有这个绑定（全仓只有 `terminal_picker.py` 里的 `s-tab`，那是选择器内部的）。
它没人可以查，就顺手编了一个。

**幻觉没法靠"下次注意"解决，只能靠门禁**：提示词里出现的每个斜杠命令都要在命令注册表里
真实存在，每个快捷键都要在允许清单里登记过（新加一个就得在这里写一行并说明它绑在哪）。
这条测试红了，说明有人往提示词里写了不存在的东西。

@author aceFelix
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROMPT_FILES = ("text_blocks.py", "system.py")
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "agent" / "prompts"

#: 形如 ``/cost`` 的命令提及。排除三类误报：
#: 1. 前面是字母数字/斜杠/点 → ``E:/x``、``/usr/bin`` 的续段；
#: 2. 后面紧跟 ``/`` → ``/bin/bash``、``/home/<user>``、``once/daily/weekly`` 的路径与枚举；
#: 3. 前面是 ``<`` → ``</available-deferred-tools>`` 这类标签。
_SLASH_CMD_RE = re.compile(r"(?<![\w/.<])/([a-z][a-z0-9][a-z0-9-]*)(?![/\w])")
#: 形如 ``Shift+Tab`` / ``Ctrl+P`` 的快捷键提及
_SHORTCUT_RE = re.compile(r"\b(?:Shift|Ctrl|Alt|Cmd|Meta)\+[A-Za-z]+\b")

#: 允许出现在提示词里的快捷键 → 它真实绑在哪。
#: 空 = 提示词不许提快捷键。要加就在这儿登记，并在注释里写清绑定位置。
ALLOWED_SHORTCUTS: dict[str, str] = {}


def _registry() -> tuple[set[str], set[str]]:
    from agent.commands.router import COMMAND_HANDLERS, PREFIX_HANDLERS

    exact = {k.lower() for k in COMMAND_HANDLERS}
    prefix = {p.strip().split(" ")[0].lower() for p, _ in PREFIX_HANDLERS}
    return exact, prefix


def _texts() -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for name in PROMPT_FILES:
        p = PROMPTS_DIR / name
        if p.is_file():
            out.append((name, p.read_text(encoding="utf-8").splitlines()))
    return out


def test_prompt_files_are_found():
    """文件列表写错会让整条门禁静默失效——先钉住它确实读到了东西。"""
    texts = _texts()
    assert texts, f"没找到任何提示词文件：{PROMPTS_DIR}"
    assert sum(len(lines) for _n, lines in texts) > 100


def test_slash_commands_mentioned_in_prompts_really_exist():
    exact, prefix = _registry()
    known = exact | prefix
    bad: list[str] = []
    for name, lines in _texts():
        for n, line in enumerate(lines, 1):
            for m in _SLASH_CMD_RE.finditer(line):
                cmd = "/" + m.group(1)
                if cmd not in known:
                    bad.append(f"{name}:{n} 提到 {cmd}")
    assert bad == [], "提示词里提到不存在的斜杠命令（会诱导模型编造操作）：\n" + "\n".join(bad)


def test_prompt_shortcuts_are_registered():
    bad: list[str] = []
    for name, lines in _texts():
        for n, line in enumerate(lines, 1):
            for m in _SHORTCUT_RE.finditer(line):
                if m.group(0) not in ALLOWED_SHORTCUTS:
                    bad.append(f"{name}:{n} 提到未登记的快捷键 {m.group(0)}")
    assert bad == [], (
        "提示词里出现未登记的快捷键；幻觉快捷键就是从这儿来的：\n" + "\n".join(bad)
    )


def test_mode_switch_has_an_authoritative_mapping():
    """权限模式 ↔ `/mode` 的映射必须写在提示词里。

    没有这条映射时，模型只能猜：实测它猜成 Shift+Tab。这条测试保证它一直有得查。
    """
    body = "\n".join(line for _n, lines in _texts() for line in lines)
    assert "/mode" in body, "提示词没告诉模型权限模式的运行时入口是 /mode"
    for word in ("default", "plan", "accept_edits", "yolo"):
        assert word in body, f"提示词没列全模式取值：缺 {word}"
