"""scripts/check_md_links.py 死链扫描工具的单元测试。

覆盖：本地链接判定口径（相对 / 绝对 / 外部 / 锚点 / data 资源）、跳过目录规则、
死链检出结果、命令行退出码，以及全仓文档零死链门禁（三栏 GUI 窗口壳退役后
文档口径整改的回归保障）。

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


def test_relative_dead_link_detected(tmp_path, checker):
    """正常路径：相对链接指向存在的文件不算死链，指向不存在文件的被检出。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "target.md").write_text("# 目标文档", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[有效](docs/target.md)\n[失效](docs/missing.md)\n", encoding="utf-8"
    )
    assert checker.find_dead_links(tmp_path) == [("README.md", "docs/missing.md")]


def test_anchor_fragment_stripped(tmp_path, checker):
    """链接尾部的 # 锚点片段不参与文件存在性判定。"""
    (tmp_path / "a.md").write_text("占位", encoding="utf-8")
    (tmp_path / "b.md").write_text("[章节](a.md#某节)", encoding="utf-8")
    assert checker.find_dead_links(tmp_path) == []


def test_non_local_links_ignored(tmp_path, checker):
    """外部站点 / 邮件 / 纯锚点 / 站点绝对路径 / data 内联资源一律跳过。"""
    (tmp_path / "README.md").write_text(
        "[外](https://example.com/x.md) [外2](http://example.com/x.md) "
        "[邮](mailto:a@b.com) [锚](#sec) [站](/docs/x.md) "
        "![图](data:image/png;base64,AAAA)\n",
        encoding="utf-8",
    )
    assert checker.find_dead_links(tmp_path) == []


def test_skip_dirs_excluded(tmp_path, checker):
    """产物目录与厂商文档快照目录内的死链不报告（避免外部抓取件误报）。"""
    for sub in ("build", ".venv", "dashscope-docs"):
        d = tmp_path / sub
        d.mkdir()
        (d / "x.md").write_text("[失效](nope.md)", encoding="utf-8")
    assert checker.find_dead_links(tmp_path) == []
    # 同名目录之外的文件仍会被扫描，证明跳过规则是按路径段生效的
    (tmp_path / "notes.md").write_text("[失效](nope.md)", encoding="utf-8")
    assert checker.find_dead_links(tmp_path) == [("notes.md", "nope.md")]


def test_file_url_treated_as_absolute(tmp_path, checker):
    """file:/// 前缀按绝对路径解析，不与 md 所在目录拼接。"""
    md = tmp_path / "docs" / "a.md"
    md.parent.mkdir()
    md.write_text("[绝对](file:///C:/other/x.md)", encoding="utf-8")
    pairs = checker.local_link_targets(md)
    assert len(pairs) == 1
    resolved = str(pairs[0][1])
    assert resolved.replace("\\", "/").endswith("C:/other/x.md")
    assert not resolved.startswith(str(md.parent))


def test_main_exit_code_and_report(tmp_path, checker, capsys):
    """CLI 入口：有死链返回 1 并逐条打印，无死链返回 0。"""
    (tmp_path / "ok.md").write_text("[自](ok.md)", encoding="utf-8")
    assert checker.main(["check_md_links.py", str(tmp_path)]) == 0

    (tmp_path / "bad.md").write_text("[失效](gone.md)", encoding="utf-8")
    assert checker.main(["check_md_links.py", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "dead links: 1" in out
    assert "gone.md" in out


def test_repo_docs_have_zero_dead_links(checker):
    """全仓文档零死链门禁：文档口径整改后不得遗留指向不存在文件的本地链接。"""
    dead = checker.find_dead_links(REPO_ROOT)
    assert dead == [], f"存在失效链接：{dead}"
