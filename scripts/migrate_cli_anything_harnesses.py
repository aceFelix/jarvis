#!/usr/bin/env python3
"""把 CLI-Anything 官方 harness 迁移为 Jarvis harness 格式。

用法：

```bash
python scripts/migrate_cli_anything_harnesses.py \
    --source ../CLI-Anything-main \
    --target ~/.jarvis/cli_anything \
    --include blender,gimp,godot,qgis,obsidian,drawio
```

迁移逻辑：
- 读取 ``<source>/skills/cli-anything-<id>/SKILL.md``
- 解析 frontmatter（name / description）
- 生成 Jarvis 格式的 ``<target>/<id>/SKILL.md``
- command 统一为 ``cli-anything-<id>``
- 参数保留 ``subcommand``（必填）和 ``json``（默认 true）

@author aceFelix
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.cli_anything.migrate import migrate_one


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 CLI-Anything harness 到 Jarvis")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="CLI-Anything 仓库根目录",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.home() / ".jarvis" / "cli_anything",
        help="Jarvis harness 输出目录（默认 ~/.jarvis/cli_anything）",
    )
    parser.add_argument(
        "--include",
        type=str,
        default="",
        help="只迁移指定 harness，逗号分隔，如 blender,gimp,godot",
    )
    parser.add_argument(
        "--exclude",
        type=str,
        default="",
        help="排除指定 harness，逗号分隔",
    )
    args = parser.parse_args()

    source_skills_dir = args.source / "skills"
    if not source_skills_dir.is_dir():
        print(f"[错误] 找不到 skills 目录: {source_skills_dir}")
        return 1

    include_set = set(args.include.split(",")) if args.include else set()
    exclude_set = set(args.exclude.split(",")) if args.exclude else set()

    args.target.mkdir(parents=True, exist_ok=True)

    success_count = 0
    for skill_dir in sorted(source_skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not skill_dir.name.startswith("cli-anything-"):
            continue

        harness_id = skill_dir.name.replace("cli-anything-", "")
        if include_set and harness_id not in include_set:
            continue
        if harness_id in exclude_set:
            continue

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            print(f"[跳过] {skill_dir}: 无 SKILL.md")
            continue

        _, ok = migrate_one(skill_md, args.target)
        if ok:
            success_count += 1
            print(f"[生成] {args.target / harness_id}/SKILL.md")
        else:
            print(f"[跳过] {skill_dir}: frontmatter 解析失败")

    print(f"\n完成：成功迁移 {success_count} 个 harness 到 {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
