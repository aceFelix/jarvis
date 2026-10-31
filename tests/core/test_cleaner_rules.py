"""cleaners.toml 规则库的解析、展开与白名单校验。

覆盖的核心风险：**一条错规则进了清单就等于一次误删**，所以这里逐条钉住
"什么样的规则必须被拒"。

@author aceFelix
"""

from __future__ import annotations

import os

import pytest

from agent.core.maintenance import rules as rules_mod
from agent.core.maintenance.rules import (
    ALLOWED_COMMAND_BINARIES,
    CleanerRule,
    expand_path,
    expand_path_list,
    load_rulebook,
    match_any,
    parse_rule,
)

# 内置规则库与它承载的路径全是 Windows 语义（%LOCALAPPDATA%、盘符、反斜杠分隔）。
# POSIX 上没有这些环境变量，expand_path 会原样保留占位符继而判为非绝对路径——
# 那是**正确行为**，不是缺陷。所以这类用例打平台门禁，而不是把期望值改成假路径
# 去迁就平台（那样在 Linux 上通过、却不再验证真实的 Windows 场景）。
_WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="规则库与内置规则均为 Windows 语义（%VAR% / 盘符）"
)


def _base(**over) -> dict:
    """一条最小合法规则，测试里按需覆盖字段。"""
    data = {
        "id": "t1",
        "name": "测试项",
        "kind": "paths",
        "paths": ["%LOCALAPPDATA%\\Temp"],
        "category": "temp",
        "risk": "low",
        "requires_admin": False,
        "desc": "删了会怎样：不会怎样，这是测试。",
    }
    data.update(over)
    return data


# ---------------------------------------------------------------------------
# 合法规则
# ---------------------------------------------------------------------------


@_WINDOWS_ONLY
def test_parse_minimal_rule():
    rule = parse_rule(_base())
    assert rule.id == "t1"
    assert rule.kind == "paths"
    assert rule.risk_rank == 0
    assert rule.default_selected() is True
    assert rule.paths and all(os.path.isabs(p) for p in rule.paths)


@_WINDOWS_ONLY
def test_high_risk_is_not_selected_by_default():
    """high 风险默认不勾——PLAN 第八节坑 1 的直接规避。"""
    rule = parse_rule(_base(risk="high"))
    assert rule.default_selected() is False


@_WINDOWS_ONLY
def test_medium_risk_is_selected():
    assert parse_rule(_base(risk="medium")).default_selected() is True


@_WINDOWS_ONLY
def test_paths_accepts_single_string():
    """``paths`` 写单个字符串也要能用（TOML 手写时常这样）。"""
    rule = parse_rule(_base(paths="%SYSTEMROOT%\\Temp"))
    assert len(rule.paths) == 1


def test_command_rule_with_allowlisted_binary():
    rule = parse_rule(_base(
        id="c1", kind="command", paths=None,
        clean_cmd=["dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
        analyze_cmd=["dism.exe", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore"],
    ))
    assert rule.is_command is True
    assert rule.clean_cmd[0] == "dism.exe"
    assert rule.analyze_cmd


# ---------------------------------------------------------------------------
# 必须被拒的规则
# ---------------------------------------------------------------------------


def test_reject_missing_desc():
    """desc 是强制字段：没有「删了会怎样」的说明就不许出现在用户面前。"""
    with pytest.raises(ValueError, match="desc"):
        parse_rule(_base(desc=""))


def test_reject_missing_id():
    with pytest.raises(ValueError, match="id"):
        parse_rule(_base(id=""))


def test_reject_bad_category():
    with pytest.raises(ValueError, match="category"):
        parse_rule(_base(category="weird"))


def test_reject_bad_risk():
    with pytest.raises(ValueError, match="risk"):
        parse_rule(_base(risk="extreme"))


def test_reject_bad_kind():
    with pytest.raises(ValueError, match="kind"):
        parse_rule(_base(kind="magic"))


def test_reject_wildcard_in_path():
    """路径级通配一错就是灾难——要限定文件请用 include。"""
    with pytest.raises(ValueError, match="通配符"):
        parse_rule(_base(paths=["%LOCALAPPDATA%\\Temp\\*.tmp"]))


def test_reject_relative_path():
    with pytest.raises(ValueError, match="绝对路径"):
        parse_rule(_base(paths=["relative\\dir"]))


@_WINDOWS_ONLY
def test_reject_root_path():
    """盘根绝不许进清单：一次误操作就是整盘。"""
    with pytest.raises(ValueError, match="盘根"):
        parse_rule(_base(paths=[os.path.splitdrive(os.getcwd())[0] + "\\"]))


def test_reject_command_not_in_whitelist():
    """规则文件用户可改，没有白名单就能让它跑任意命令。"""
    with pytest.raises(ValueError, match="白名单"):
        parse_rule(_base(
            id="evil", kind="command", paths=None,
            clean_cmd=["rm.exe", "-rf", "C:\\Users"],
        ))


def test_reject_command_without_clean_cmd():
    with pytest.raises(ValueError, match="clean_cmd"):
        parse_rule(_base(id="c2", kind="command", paths=None))


def test_allowed_binaries_are_the_known_ones():
    """白名单扩容的守门人：这里是**精确等值**，加一条命令就必须显式改这里。

    刻意不做成子集断言——白名单是整个维护层最危险的一张表，它变大了应该有人
    停下想一想，而不是测试默默通过。
    """
    assert ALLOWED_COMMAND_BINARIES == {
        "dism.exe", "vssadmin.exe", "powercfg.exe", "cleanmgr.exe"
    }


# ---------------------------------------------------------------------------
# 路径展开
# ---------------------------------------------------------------------------


def test_expand_env_var(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST_DIR", "C:\\somewhere")
    assert expand_path("%JARVIS_TEST_DIR%\\x") == "C:\\somewhere\\x"


def test_expand_unknown_var_kept_verbatim():
    """未知变量保留原样，交由校验环节判为非绝对路径而拒绝。

    悄悄替换成空串更危险：路径会退化成父目录。
    """
    got = expand_path("%JARVIS_NO_SUCH_VAR_XYZ%\\a")
    assert "%JARVIS_NO_SUCH_VAR_XYZ%" in got


@_WINDOWS_ONLY
def test_expand_tilde():
    assert expand_path("~\\sub").startswith(str(os.path.expanduser("~")))


@_WINDOWS_ONLY
def test_expand_all_drives(monkeypatch):
    monkeypatch.setattr(rules_mod, "_local_drives", lambda: ["C:", "D:"])
    got = expand_path_list("%ALLDRIVES%\\$Recycle.Bin")
    assert got == ["C:\\$Recycle.Bin", "D:\\$Recycle.Bin"]


# ---------------------------------------------------------------------------
# 通配匹配
# ---------------------------------------------------------------------------


def test_match_any_returns_pattern():
    assert match_any("app.lock", ("*.lock", "*.db")) == "*.lock"
    assert match_any("app.txt", ("*.lock",)) is None


def test_cleanmgr_rule_has_no_analyze_cmd():
    """cleanmgr 没有分析模式：analyze_cmd 空着，且命令里必须带槽号。"""
    rule = load_rulebook().by_id("cleanmgr_profile")
    assert rule is not None
    assert rule.is_command is True
    assert rule.analyze_cmd == ()
    assert rule.clean_cmd[0] == "cleanmgr.exe"
    assert any(arg.startswith("/sagerun:") for arg in rule.clean_cmd), \
        "/sagerun 缺槽号就等于没指定配置"
    # 前提条件必须写在 desc 里，否则用户跑完没反应会以为是 bug
    assert "/sageset" in rule.desc


def test_whitelist_rejects_arbitrary_binaries():
    """白名单只能放行清理类命令；解释器/脚本宿主一律不许进。"""
    for dang in ("cmd.exe", "powershell.exe", "bash.exe", "python.exe", "rm.exe"):
        assert dang not in ALLOWED_COMMAND_BINARIES


# ---------------------------------------------------------------------------
# load_rulebook
# ---------------------------------------------------------------------------


@_WINDOWS_ONLY
def test_load_builtin_rulebook_has_all_rules():
    """内置规则库必须一条都没被拒——被拒意味着 TOML 写错了。"""
    book = load_rulebook()
    assert not book.rejected, f"内置规则被拒：{book.rejected}"
    ids = set(book.ids())
    # PLAN 4.1 的首批清单至少这些要在
    for must in ("user_temp", "win_temp", "win_update_cache", "windows_old",
                 "winsxs_cleanup", "recycle_bin", "browser_cache", "cleanmgr_profile"):
        assert must in ids, f"缺少规则 {must}"
    # 刻意不收录的
    for never in ("prefetch", "node_modules"):
        assert never not in ids, f"不该收录 {never}"


def test_load_rulebook_missing_file_reports(tmp_path):
    book = load_rulebook(tmp_path / "nope.toml")
    assert book.rules == []
    assert book.rejected and "不存在" in book.rejected[0][1]


def test_load_rulebook_bad_toml_reports_not_raises(tmp_path):
    """TOML 语法错误必须报出来：静默退回空表会让 /disk 显示"没什么可清"。"""
    bad = tmp_path / "bad.toml"
    bad.write_text("[[cleaner]\nid = 'x'\n", encoding="utf-8")
    book = load_rulebook(bad)
    assert book.rules == []
    assert book.rejected and "解析失败" in book.rejected[0][1]


@_WINDOWS_ONLY
def test_load_rulebook_duplicate_id_keeps_first(tmp_path):
    f = tmp_path / "dup.toml"
    f.write_text(
        "[[cleaner]]\nid='a'\nname='A'\nkind='paths'\n"
        "paths=['%LOCALAPPDATA%\\\\Temp']\ncategory='temp'\nrisk='low'\n"
        "requires_admin=false\ndesc='d'\n"
        "[[cleaner]]\nid='a'\nname='A2'\nkind='paths'\n"
        "paths=['%LOCALAPPDATA%\\\\Temp']\ncategory='temp'\nrisk='low'\n"
        "requires_admin=false\ndesc='d2'\n",
        encoding="utf-8",
    )
    book = load_rulebook(f)
    assert len(book.rules) == 1
    assert any("重复" in why for _rid, why in book.rejected)


@_WINDOWS_ONLY
def test_load_rulebook_one_bad_rule_does_not_kill_others(tmp_path):
    """一张表里写错一条不该让整套清理瘫痪。"""
    f = tmp_path / "mixed.toml"
    f.write_text(
        "[[cleaner]]\nid='good'\nname='G'\nkind='paths'\n"
        "paths=['%LOCALAPPDATA%\\\\Temp']\ncategory='temp'\nrisk='low'\n"
        "requires_admin=false\ndesc='ok'\n"
        "[[cleaner]]\nid='bad'\nname='B'\nkind='paths'\n"
        "paths=['%LOCALAPPDATA%\\\\Temp']\ncategory='nope'\nrisk='low'\n"
        "requires_admin=false\ndesc='x'\n",
        encoding="utf-8",
    )
    book = load_rulebook(f)
    assert [r.id for r in book.rules] == ["good"]
    assert book.rejected and book.rejected[0][0] == "bad"


def test_rulebook_by_id():
    book = load_rulebook()
    first = book.ids()[0]
    rule = book.by_id(first)
    assert isinstance(rule, CleanerRule)
    assert book.by_id("no-such-rule") is None