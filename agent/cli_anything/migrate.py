"""CLI-Anything harness 迁移工具。

把官方 ``SKILL.md`` 转换为 Jarvis harness 格式，供 market / 脚本复用。

@author aceFelix
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict[str, Any] | None:
    """解析 SKILL.md 的 YAML frontmatter。"""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        return None


def _extract_examples(body: str, cli_name: str, max_examples: int = 5) -> list[str]:
    """从正文中提取命令示例（``cli-anything-xxx ...`` 形式的代码块）。"""
    examples: list[str] = []
    for block in re.findall(r"```(?:bash|shell|powershell)?\s*\n(.*?)\n```", body, re.DOTALL):
        for line in block.splitlines():
            line = line.strip()
            if line.startswith(cli_name) and not line.endswith("--help"):
                if len(line) > 120:
                    line = line[:120] + "..."
                examples.append(line)
                if len(examples) >= max_examples:
                    return examples
    return examples


def _extract_when_to_use(description: str | None, body: str) -> str:
    """生成 when_to_use 字段。"""
    if description:
        return description.split(".")[0].strip() + "."
    first_para = body.strip().split("\n\n")[0].strip()
    if len(first_para) > 200:
        first_para = first_para[:200] + "..."
    return first_para


def _generate_skill_md(
    harness_id: str,
    name: str,
    description: str,
    when_to_use: str,
    examples: list[str],
) -> str:
    """生成 Jarvis 格式的 SKILL.md 内容。"""
    display_name = harness_id.capitalize()
    if name.startswith("cli-anything-"):
        display_name = name.replace("cli-anything-", "").replace("-", " ").title()

    frontmatter: dict[str, Any] = {
        "name": display_name,
        "id": harness_id,
        "description": description,
        "when_to_use": when_to_use,
        "trigger_words": [harness_id],
        "command": f"cli-anything-{harness_id}",
        "args": [
            {
                "name": "subcommand",
                "type": "string",
                "required": True,
                "description": '要执行的子命令，如 ``scene new -o scene.json``、``project info``、``--help``。参考 CLI-Anything 官方文档使用正确的子命令语法。',
            },
            {
                "name": "json",
                "type": "boolean",
                "required": False,
                "default": True,
                "description": "是否添加 ``--json`` 标志输出结构化 JSON（推荐开启）。",
            },
        ],
    }
    if examples:
        frontmatter["examples"] = examples

    frontmatter_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)

    return f"""---
{frontmatter_yaml}---

# {display_name} Harness

本 harness 通过 ``cli-anything-{harness_id}`` 命令控制 {display_name}。

## 使用方式

Jarvis 会把 ``subcommand`` 参数拼接在 ``cli-anything-{harness_id}`` 后面执行。

例如：

```bash
cli-anything-{harness_id} --json <subcommand>
```

## 命令参考

完整命令参考请查看 CLI-Anything 官方 ``skills/cli-anything-{harness_id}/SKILL.md``。

## 安装

使用前需要先安装对应 harness 包：

```bash
pip install cli-anything-{harness_id}
```

> 注：某些 harness 可能尚未发布到 PyPI，需要从源码安装。
"""


def migrate_one(
    source_skill_md: Path,
    target_dir: Path,
    harness_id: str | None = None,
) -> tuple[str, bool]:
    """迁移单个 harness。返回 (harness_id, success)。

    Args:
        source_skill_md: 官方 SKILL.md 路径。
        target_dir: 目标根目录（如 ``~/.jarvis/cli_anything/``）。
        harness_id: 可选，强制指定生成的 harness id。
            未指定时从 ``source_skill_md.parent.name`` 推断。
            远程下载的临时文件应显式传入此参数，避免目录名被当作 id。
    """
    if harness_id is None:
        harness_id = source_skill_md.parent.name.replace("cli-anything-", "")
    if not harness_id:
        return ("", False)

    text = source_skill_md.read_text(encoding="utf-8")
    meta = _parse_frontmatter(text)
    if meta is None:
        return (harness_id, False)

    name = str(meta.get("name", f"cli-anything-{harness_id}"))
    description = str(meta.get("description", f"CLI for {harness_id}"))
    body = _FRONTMATTER_RE.sub("", text, count=1).strip()

    when_to_use = _extract_when_to_use(description, body)
    examples = _extract_examples(body, f"cli-anything-{harness_id}")

    out_dir = target_dir / harness_id
    out_dir.mkdir(parents=True, exist_ok=True)

    skill_md = _generate_skill_md(harness_id, name, description, when_to_use, examples)
    (out_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    return (harness_id, True)
