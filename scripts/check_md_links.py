"""扫描仓库所有 md 中的链接，报告指向不存在文件的死链。

用途：三栏 GUI 窗口壳退役后复查文档失效链接（只检查本地文件链接，
不检查 http/邮件/纯锚点链接）。可传目录参数指定扫描根（默认本仓库根）。

用法：
    python scripts/check_md_links.py [root_dir]

退出码：0 = 无死链；1 = 发现死链（便于接入 CI 门禁）。
@author aceFelix
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 厂商文档快照（外部站点抓取件）与依赖/构建产物目录不参与死链判定
SKIP_DIRS = {".venv", "node_modules", "build", "dist", ".git", "__pycache__",
             "dashscope-docs", "kimi-docs", "minimax-docs", "mimo-docs",
             "deepseek-docs", "zai-docs"}
# 匹配 markdown 链接目标：[text](target)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
# 非本地文件链接的前缀：外部站点、邮件、纯锚点、站点绝对路径、内联 data 资源
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "/", "data:")


def iter_md_files(root: Path) -> list[Path]:
    """列出 root 下参与死链判定的 md 文件（跳过快照与产物目录）。"""
    return [md for md in sorted(root.rglob("*.md"))
            if not any(part in SKIP_DIRS for part in md.parts)]


def resolve_target(md: Path, target: str) -> Path | None:
    """把链接目标解析为本地文件路径；非本地文件链接返回 None。

    口径：http/https/mailto/纯锚点/站点绝对路径/data 内联资源不算本地链接；
    `file:///` 前缀与盘符开头的按绝对路径处理，其余按 md 所在目录拼相对路径；
    Windows 下先把正斜杠归一化，再剥离 # 锚点片段。
    """
    if target.startswith(SKIP_PREFIXES):
        return None
    raw = target.removeprefix("file:///").replace("/", "\\")
    path_part = raw.split("#")[0]
    if not path_part:
        return None
    return Path(path_part) if re.match(r"^[A-Za-z]:", path_part) else md.parent / path_part


def local_link_targets(md: Path) -> list[tuple[str, Path]]:
    """返回 md 文件里的本地文件链接：(原始目标串, 解析后的路径)。"""
    text = md.read_text(encoding="utf-8", errors="ignore")
    out: list[tuple[str, Path]] = []
    for target in LINK_RE.findall(text):
        candidate = resolve_target(md, target)
        if candidate is not None:
            out.append((target, candidate))
    return out


def find_dead_links(root: Path) -> list[tuple[str, str]]:
    """扫描 root 下全部 md，返回死链列表 (仓库相对文件路径, 链接目标原文)。"""
    root = Path(root).resolve()
    dead: list[tuple[str, str]] = []
    for md in iter_md_files(root):
        for target, candidate in local_link_targets(md):
            try:
                ok = candidate.resolve().exists()
            except OSError:  # 路径非法（含保留名/过长）同样视为死链
                ok = False
            if not ok:
                dead.append((str(md.relative_to(root)), target))
    return dead


def main(argv: list[str]) -> int:
    """命令行入口：打印扫描结果，有死链时返回 1。"""
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[1]
    files = iter_md_files(root)
    dead = find_dead_links(root)
    print(f"scanned md files: {len(files)}")
    print(f"dead links: {len(dead)}")
    for f, t in dead:
        print(f"- {f}\n    -> {t}")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
