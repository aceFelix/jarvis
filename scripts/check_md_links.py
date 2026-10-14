"""扫描仓库所有 md 中的链接，报告指向不存在文件的死链。

用途：三栏 GUI 窗口壳退役后复查文档失效链接（只判定仓内本地文件链接）。
可传目录参数指定扫描根（默认本仓库根）。

跨平台口径：CI 的检出路径与开发机不同（如 /home/runner/work/jarvis/jarvis），
文档里写死的 `file:///` 绝对路径按「仓根目录名」重定位后再判定，因此同一份
文档在 Windows / Linux 上结论一致；无法落到本仓的路径（外链、越出仓根的相对
链接、指向兄弟仓库的绝对路径）一律跳过而非误报。

用法：
    python scripts/check_md_links.py [root_dir]

退出码：0 = 无死链；1 = 发现死链（便于接入 CI 门禁）。
@author aceFelix
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 厂商文档快照（外部站点抓取件）与依赖/构建产物目录不参与死链判定
SKIP_DIRS = {".venv", "node_modules", "build", "dist", ".git", "__pycache__",
             "dashscope-docs", "kimi-docs", "minimax-docs", "mimo-docs",
             "deepseek-docs", "zai-docs"}
# 匹配 markdown 链接目标：[text](target)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
# 围栏代码块与行内代码：里面的 [text](target) 只是写法示例，不会被渲染成链接，
# 故提取链接前先剔除（否则文档举反例时会被误判为死链）
CODE_FENCE_RE = re.compile(r"^(```|~~~).*?^\1$", re.DOTALL | re.MULTILINE)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# 非本地文件链接的前缀：外部站点、邮件、纯锚点、站点绝对路径、内联 data 资源
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "/", "data:")
FILE_URL_PREFIX = "file:///"
# Windows 盘符开头的绝对路径（POSIX 根路径已由 "/" 前缀归入外链）
DRIVE_RE = re.compile(r"^[A-Za-z]:")


def iter_md_files(root: Path) -> list[Path]:
    """列出 root 下参与死链判定的 md 文件（跳过快照与产物目录）。"""
    return [md for md in sorted(root.rglob("*.md"))
            if not any(part in SKIP_DIRS for part in md.parts)]


def repo_relative_candidates(path_part: str, root_name: str) -> list[str]:
    """把绝对路径按仓根目录名切成「仓内相对路径」候选（可能有多层同名）。

    例：`e:/2.MyProjects/.../jarvis/docs/a.md` + root_name=`jarvis`
    → `docs/a.md`；CI 上再拼上当次检出根即可判定。切出正斜杠形式，
    与 git 路径口径一致，且不含任何平台专属分隔符。
    """
    parts = [p for p in re.split(r"[\\/]+", path_part) if p]
    return ["/".join(parts[i + 1:]) for i, p in enumerate(parts)
            if p == root_name and i + 1 < len(parts)]


def link_candidates(md: Path, target: str, root: Path) -> list[Path]:
    """把链接目标解析为待判定路径列表；返回空列表表示本工具不判定它。

    不判定的三类：外部链接（SKIP_PREFIXES）、纯锚点片段、以及落不到本仓的
    路径（越出仓根的相对链接、指向兄弟仓库等仓外绝对路径）。
    """
    if target.startswith(SKIP_PREFIXES):
        return []
    is_file_url = target.startswith(FILE_URL_PREFIX)
    path_part = target.removeprefix(FILE_URL_PREFIX).split("#")[0]
    if not path_part:
        return []
    if is_file_url or DRIVE_RE.match(path_part):
        return [root / rel for rel in repo_relative_candidates(path_part, root.name)]
    # 相对链接：先按字面拼出路径，再用 normpath 判断是否越出仓根（不依赖文件存在性）
    norm = os.path.normpath(str(md.parent / path_part))
    root_norm = os.path.normpath(str(root))
    if norm != root_norm and not norm.startswith(root_norm + os.sep):
        return []
    return [md.parent / path_part]


def link_targets(md: Path) -> list[str]:
    """提取 md 中真正参与渲染的链接目标（已剔除代码块与行内代码里的示例写法）。"""
    text = md.read_text(encoding="utf-8", errors="ignore")
    text = CODE_FENCE_RE.sub("", text)
    return LINK_RE.findall(INLINE_CODE_RE.sub("", text))


def local_link_targets(md: Path, root: Path) -> list[tuple[str, Path]]:
    """返回 md 文件里需判定的本地链接：(链接目标原文, 待判定路径)。"""
    return [(t, c) for t in link_targets(md) for c in link_candidates(md, t, root)]


def _exists(path: Path) -> bool:
    """存在性判断：非法路径（Windows 保留名、超长路径等）按不存在处理。"""
    try:
        return path.exists()
    except OSError:
        return False


def find_dead_links(root: Path) -> list[tuple[str, str]]:
    """扫描 root 下全部 md，返回死链列表 (仓库相对文件路径, 链接目标原文)。

    同一文件内重复出现的目标只报告一次；绝对链接只要有任一重定位结果存在
    即算有效（同名层级取不到时不误报）。
    """
    root = Path(root).resolve()
    dead: list[tuple[str, str]] = []
    for md in iter_md_files(root):
        rel_md = str(md.relative_to(root))
        grouped: dict[str, list[Path]] = {}
        for target, candidate in local_link_targets(md, root):
            grouped.setdefault(target, []).append(candidate)
        for target, candidates in grouped.items():
            if not any(_exists(p) for p in candidates):
                dead.append((rel_md, target))
    return dead


def count_links(files: list[Path], root: Path) -> tuple[int, int]:
    """统计 (需判定链接数, 跳过链接数)，供命令行输出交代覆盖面。"""
    judged = skipped = 0
    for md in files:
        for target in set(link_targets(md)):
            if link_candidates(md, target, root):
                judged += 1
            else:
                skipped += 1
    return judged, skipped


def main(argv: list[str]) -> int:
    """命令行入口：打印扫描结果，有死链时返回 1。"""
    root = Path(argv[1]).resolve() if len(argv) > 1 else Path(__file__).resolve().parents[1]
    root = Path(root).resolve()
    files = iter_md_files(root)
    judged, skipped = count_links(files, root)
    dead = find_dead_links(root)
    print(f"scanned md files: {len(files)}")
    print(f"local links judged: {judged} | skipped (外链/纯锚点/仓外): {skipped}")
    print(f"dead links: {len(dead)}")
    for f, t in dead:
        print(f"- {f}\n    -> {t}")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
