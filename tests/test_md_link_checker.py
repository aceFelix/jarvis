"""scripts/check_md_links.py 死链扫描工具的单元测试。

覆盖：本地链接判定口径（相对 / 绝对 / 外部 / 锚点 / data 资源）、跳过目录规则、
跨平台判定（CI 检出路径与开发机不同，绝对链接按仓根目录名重定位；仓外路径不误报）、
代码示例不判定、未入库链接检出（CI 只检出被跟踪的文件）、
死链检出结果、命令行退出码，以及全仓文档零死链 + 链接全部入库两道门禁
（三栏 GUI 窗口壳退役后文档口径整改的回归保障）。

@author aceFelix
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = REPO_ROOT / "scripts" / "check_md_links.py"


@pytest.fixture(scope="module")
def checker():
    """按文件路径加载工具脚本（scripts/ 不是包，无法直接 import）。"""
    spec = importlib.util.spec_from_file_location("check_md_links", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_repo(tmp_path: Path, name: str = "jarvis") -> Path:
    """造一个名为 name 的假仓根（含 docs/ 子目录），用于模拟不同平台的检出路径。"""
    root = tmp_path / name
    (root / "docs").mkdir(parents=True)
    return root


def test_relative_dead_link_detected(tmp_path, checker):
    """正常路径：相对链接指向存在的文件不算死链，指向不存在文件的被检出。"""
    root = _make_repo(tmp_path)
    (root / "docs" / "target.md").write_text("# 目标文档", encoding="utf-8")
    (root / "README.md").write_text(
        "[有效](docs/target.md)\n[失效](docs/missing.md)\n", encoding="utf-8"
    )
    assert checker.find_dead_links(root) == [("README.md", "docs/missing.md")]


def test_duplicate_target_reported_once(tmp_path, checker):
    """同一文件内重复出现的同一目标只报告一次，避免刷屏。"""
    root = _make_repo(tmp_path)
    (root / "a.md").write_text("[1](nope.md) [2](nope.md)\n", encoding="utf-8")
    assert checker.find_dead_links(root) == [("a.md", "nope.md")]


def test_anchor_fragment_stripped(tmp_path, checker):
    """链接尾部的 # 锚点片段不参与文件存在性判定。"""
    root = _make_repo(tmp_path)
    (root / "docs" / "a.md").write_text("占位", encoding="utf-8")
    (root / "docs" / "b.md").write_text("[章节](a.md#某节)", encoding="utf-8")
    assert checker.find_dead_links(root) == []


def test_non_local_links_ignored(tmp_path, checker):
    """外部站点 / 邮件 / 纯锚点 / 站点绝对路径 / data 内联资源一律跳过。"""
    root = _make_repo(tmp_path)
    (root / "README.md").write_text(
        "[外](https://example.com/x.md) [外2](http://example.com/x.md) "
        "[邮](mailto:a@b.com) [锚](#sec) [站](/docs/x.md) "
        "![图](data:image/png;base64,AAAA)\n",
        encoding="utf-8",
    )
    assert checker.find_dead_links(root) == []


def test_links_in_code_samples_not_judged(tmp_path, checker):
    """行内代码与围栏代码块里的 `[x](y)` 只是写法示例，不参与死链判定。"""
    root = _make_repo(tmp_path)
    (root / "a.md").write_text(
        "行内：`[示例](nope.md)`\n\n"
        "```\n围栏：[示例](nope2.md)\n```\n\n"
        "[真链接](also-nope.md)\n",
        encoding="utf-8",
    )
    assert checker.find_dead_links(root) == [("a.md", "also-nope.md")]


def test_skip_dirs_excluded(tmp_path, checker):
    """产物目录与厂商文档快照目录内的死链不报告（避免外部抓取件误报）。"""
    root = _make_repo(tmp_path)
    for sub in ("build", ".venv", "dashscope-docs"):
        d = root / sub
        d.mkdir()
        (d / "x.md").write_text("[失效](nope.md)", encoding="utf-8")
    assert checker.find_dead_links(root) == []
    # 同名目录之外的文件仍会被扫描，证明跳过规则是按路径段生效的
    (root / "notes.md").write_text("[失效](nope.md)", encoding="utf-8")
    assert checker.find_dead_links(root) == [("notes.md", "nope.md")]


def test_repo_relative_candidates_posix_checkout(checker):
    """纯函数口径：POSIX 绝对路径按仓根名切出仓内相对路径（不含任何分隔符残留）。

    CI 检出路径 /home/runner/work/jarvis/jarvis 里 `jarvis` 出现两次，故有两个候选，
    由调用方按「任一存在即有效」裁决。
    """
    path = "/home/runner/work/jarvis/jarvis/docs/guide/desktop.md"
    assert checker.repo_relative_candidates(path, "jarvis") == [
        "jarvis/docs/guide/desktop.md",
        "docs/guide/desktop.md",
    ]


def test_absolute_file_url_relocated_by_repo_root(tmp_path, checker):
    """`file:///` 绝对链接按仓根目录名重定位后判定：换机器、换检出路径结论不变。"""
    root = _make_repo(tmp_path)
    (root / "docs" / "target.md").write_text("占位", encoding="utf-8")
    (root / "a.md").write_text(
        "[有效](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/target.md)\n"
        "[失效](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/gone.md)\n",
        encoding="utf-8",
    )
    assert checker.find_dead_links(root) == [
        ("a.md", "file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/gone.md")
    ]


def test_absolute_link_with_multiple_root_name_segments(tmp_path, checker):
    """同名层级取不到时不误报：任一重定位候选存在即算有效。"""
    root = _make_repo(tmp_path)
    (root / "docs" / "x.md").write_text("占位", encoding="utf-8")
    (root / "a.md").write_text("[链接](file:///home/ci/jarvis/jarvis/docs/x.md)",
                               encoding="utf-8")
    assert checker.find_dead_links(root) == []


def test_foreign_absolute_link_not_judged(tmp_path, checker):
    """指向兄弟仓库的绝对链接不判定（本仓检不出对方文件，判死即误报）。"""
    root = _make_repo(tmp_path)
    (root / "a.md").write_text(
        "[桌面壳](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S"
        "/jarvis-desktop/src/main/index.ts)\n",
        encoding="utf-8",
    )
    assert checker.find_dead_links(root) == []
    assert checker.link_candidates(root / "a.md", "file:///e:/x/jarvis-desktop/y.ts", root) == []


def test_relative_link_escaping_repo_not_judged(tmp_path, checker):
    """越出仓根的相对链接不判定（同上：兄弟仓库文件不在本仓检出范围内）。"""
    root = _make_repo(tmp_path)
    (root / "docs" / "a.md").write_text("[兄弟仓](../../../jarvis-desktop/src/main/index.ts)\n",
                                        encoding="utf-8")
    assert checker.find_dead_links(root) == []


def test_local_link_targets_pairs(tmp_path, checker):
    """local_link_targets 返回 (原文, 待判定路径) 对，且只含需判定的本地链接。"""
    root = _make_repo(tmp_path)
    md = root / "docs" / "a.md"
    md.write_text("[内](b.md) [外](https://x.com/b.md)", encoding="utf-8")
    pairs = checker.local_link_targets(md, root)
    assert [t for t, _ in pairs] == ["b.md"]
    assert pairs[0][1] == md.parent / "b.md"


def test_untracked_link_detected(tmp_path, checker, monkeypatch):
    """本机存在但未被 git 跟踪的链接被检出：CI 只检出跟踪文件，这类链接上 CI 必缺。

    这正是「本地全绿、CI 报死链」的第二类成因（目标写在 .gitignore 排除的本地目录里），
    单靠文件存在性检查永远抓不到。
    """
    root = _make_repo(tmp_path)
    (root / "docs" / "local-only.md").write_text("本地专属", encoding="utf-8")
    (root / "docs" / "tracked.md").write_text("已入库", encoding="utf-8")
    (root / "a.md").write_text("[未入库](docs/local-only.md) [已入库](docs/tracked.md)",
                               encoding="utf-8")
    # 模拟 git 索引：只跟踪 docs/tracked.md（docs 为其祖先目录）
    monkeypatch.setattr(checker, "tracked_paths", lambda r: ({"docs/tracked.md"}, {"docs"}))
    assert checker.find_untracked_links(root) == [("a.md", "docs/local-only.md")]


def test_untracked_check_skipped_without_git(tmp_path, checker, monkeypatch):
    """无 git 环境（源码分发件、非仓根）时该类判定整体跳过，不误报。"""
    root = _make_repo(tmp_path)
    (root / "docs" / "x.md").write_text("占位", encoding="utf-8")
    (root / "a.md").write_text("[链接](docs/x.md)", encoding="utf-8")
    monkeypatch.setattr(checker, "tracked_paths", lambda r: None)
    assert checker.find_untracked_links(root) == []


def test_main_exit_code_and_report(tmp_path, checker, capsys, monkeypatch):
    """CLI 入口：有死链返回 1 并逐条打印，无死链返回 0。"""
    # 固定为“无 git 环境”，使退出码只由死链决定（不依赖临时目录上方是否有仓）
    monkeypatch.setattr(checker, "tracked_paths", lambda r: None)
    root = _make_repo(tmp_path)
    (root / "ok.md").write_text("[自](ok.md)", encoding="utf-8")
    assert checker.main(["check_md_links.py", str(root)]) == 0

    (root / "bad.md").write_text("[失效](gone.md)", encoding="utf-8")
    assert checker.main(["check_md_links.py", str(root)]) == 1
    out = capsys.readouterr().out
    assert "dead links: 1" in out
    assert "gone.md" in out


def test_repo_docs_have_zero_dead_links(checker):
    """全仓文档零死链门禁：文档口径整改后不得遗留指向不存在文件的本地链接。"""
    dead = checker.find_dead_links(REPO_ROOT)
    assert dead == [], f"存在失效链接：{dead}"


def test_repo_docs_links_are_all_git_tracked(checker):
    """全仓文档链接入库门禁：链接目标必须存在于 git 索引，否则 CI 检出后必缺。

    无 git 环境（如从 sdist 跑测试）时 tracked_paths 返回 None，本用例自动跳过。
    """
    if checker.tracked_paths(REPO_ROOT) is None:
        pytest.skip("当前不在 git 仓库内，无法校验链接目标的入库状态")
    untracked = checker.find_untracked_links(REPO_ROOT)
    assert untracked == [], f"链接目标未入 git（CI 上不存在）：{untracked}"
