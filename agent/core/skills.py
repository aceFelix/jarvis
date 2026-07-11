"""Skill 系统 —— 可复用的能力包（Markdown 指令 + 可选脚本）。

Skill 是"一段 Markdown 指令，按需加载进 system prompt"，让贾维斯获得
特定领域的专业能力。比 MCP 轻——不需要起子进程、不涉及 RPC，纯文本注入。

致敬 ClaudeCode 的 skills/ 机制，大幅精简:
- 只支持目录格式: <skill-name>/SKILL.md
- 加载位置: ~/.jarvis/skills/（用户级）+ <workdir>/.jarvis/skills/（项目级）
- frontmatter 只解析 name/description/when_to_use/trigger_words
- 不做参数替换、不做 shell 注入、不做条件路径匹配

SKILL.md 格式示例:
    ---
    name: git-helper
    description: Git 版本控制专家
    when_to_use: 当用户需要 git 操作时
    trigger_words: git, 提交, 分支, 合并
    ---
    # Git 助手
    你现在是 Git 专家，遵循以下规范...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Skill:
    """一个已加载的 Skill。"""
    name: str                          # skill 名（目录名）
    description: str = ""              # 一句话描述
    when_to_use: str = ""              # 何时使用（给模型看的提示）
    trigger_words: list[str] = field(default_factory=list)  # 触发词
    content: str = ""                  # Markdown 正文（frontmatter 之后）
    source: str = "user"               # user / project
    path: str = ""                     # SKILL.md 绝对路径


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL
)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 YAML frontmatter（极简版，不依赖 PyYAML）。

    只支持 `key: value` 行格式，不支持嵌套/列表/多行字符串。
    返回 (frontmatter_dict, markdown_body)。
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    fm_text = m.group(1)
    body = m.group(2)

    fm: dict[str, str] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()

    return fm, body


def _load_one_skill(skill_dir: Path, source: str) -> Skill | None:
    """加载单个 skill 目录。返回 None 表示无效。"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except Exception:
        return None

    fm, body = _parse_frontmatter(raw)
    name = fm.get("name", skill_dir.name)
    description = fm.get("description", "")
    when_to_use = fm.get("when_to_use", "")
    trigger_str = fm.get("trigger_words", "")
    trigger_words = [
        w.strip() for w in trigger_str.split(",") if w.strip()
    ]

    return Skill(
        name=name,
        description=description,
        when_to_use=when_to_use,
        trigger_words=trigger_words,
        content=body.strip(),
        source=source,
        path=str(skill_md),
    )


def _scan_skills_dir(base: Path, source: str) -> list[Skill]:
    """扫描一个 skills 目录，加载所有子目录的 SKILL.md。"""
    if not base.exists() or not base.is_dir():
        return []
    skills: list[Skill] = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        skill = _load_one_skill(entry, source)
        if skill:
            skills.append(skill)
    return skills


def load_skills(workdir: str) -> list[Skill]:
    """加载所有 skill（用户级 + 项目级）。

    优先级: 项目级 > 用户级（项目级更具体，同名时覆盖用户级）。
    """
    user_dir = Path.home() / ".jarvis" / "skills"
    proj_dir = Path(workdir) / ".jarvis" / "skills"

    user_skills = _scan_skills_dir(user_dir, "user")
    proj_skills = _scan_skills_dir(proj_dir, "project")

    # 合并: 项目级同名覆盖用户级
    by_name: dict[str, Skill] = {}
    for s in user_skills:
        by_name[s.name] = s
    for s in proj_skills:
        by_name[s.name] = s  # 项目级覆盖

    return list(by_name.values())


def skills_to_prompt(skills: list[Skill]) -> str:
    """把 skill 列表拼成注入 system prompt 的段落。

    每个 skill 输出: 名字、描述、when_to_use、正文指令。
    无 skill 返回空字符串。
    """
    if not skills:
        return ""

    parts: list[str] = ["# 可用技能（Skills）\n"]
    parts.append("以下是你可以调用的技能包，每个技能包含专业领域指令。")
    parts.append("当用户的需求匹配某个技能时，按该技能的指令行事。\n")

    for s in skills:
        parts.append(f"## 技能: {s.name}")
        if s.description:
            parts.append(f"**描述**: {s.description}")
        if s.when_to_use:
            parts.append(f"**使用时机**: {s.when_to_use}")
        if s.trigger_words:
            parts.append(f"**触发词**: {', '.join(s.trigger_words)}")
        parts.append("")  # 空行
        parts.append(s.content)
        parts.append("")  # 技能间空行

    return "\n".join(parts) + "\n"


def skills_section(workdir: str) -> str:
    """加载 skill 并返回 prompt 段落（无 skill 返回空串）。"""
    return skills_to_prompt(load_skills(workdir))


def list_skill_files(workdir: str) -> dict[str, list[Path]]:
    """返回各来源的 skill 目录路径（供 /skills 命令展示）。"""
    user_dir = Path.home() / ".jarvis" / "skills"
    proj_dir = Path(workdir) / ".jarvis" / "skills"
    return {"user": user_dir, "project": proj_dir}
