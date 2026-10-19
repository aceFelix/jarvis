"""W2 感知层的桌面可改配置（agent/config/percept_settings.py）。

覆盖：读快照的缺省口径、写侧白名单校验（未知键报错而非静默忽略）、bool/int/list
三类规范化、列表去重与上限护栏、外科式落盘（保留注释/其他字段/其他节、节缺失
追加、文件缺失创建）、落盘字面量能被我自己的 TOML 读回（round-trip）。

落盘一律 `patch.object(Path, "home", ...)` 重定向到 tmp_path，不碰真实配置。

@author aceFelix
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config.percept_settings import (
    FIELD_BY_KEY,
    LIST_MAX_ITEMS,
    PERCEPT_CONFIG_FIELDS,
    apply_percept_config,
    read_percept_config,
    save_percept_config,
    validate_percept_config,
)
from agent.config.settings import Settings


def _toml(home: Path, content: str) -> Path:
    """在临时 home 下写入 ~/.jarvis/settings.toml。"""
    path = home / ".jarvis" / "settings.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---- 读侧 ----

def test_read_uses_factory_defaults():
    """裸 Settings：总开关与逐源全 False、名单为空（面板据此显"未启用"）。"""
    snap = read_percept_config(Settings())
    assert snap["enabled"] is False
    assert snap["window_enabled"] is False
    assert snap["file_watch_dirs"] == []
    assert snap["window_linger_minutes"] == 0
    # 读侧键集合与白名单严格一致（面板不需要猜字段名）
    assert set(snap) == {f.key for f in PERCEPT_CONFIG_FIELDS}


def test_read_reflects_runtime_values():
    """读的是运行时 Settings 实例，不是磁盘文件（改了没落盘也能看见当前状态）。"""
    settings = Settings()
    settings.percept_enabled = True
    settings.percept_vcs_enabled = True
    settings.percept_vcs_repos = ["E:/repo/a", " E:/repo/b "]
    snap = read_percept_config(settings)
    assert snap["enabled"] is True and snap["vcs_enabled"] is True
    assert snap["vcs_repos"] == ["E:/repo/a", "E:/repo/b"]   # 顺带 strip


def test_read_tolerates_missing_attribute():
    """字段不存在的对象（老配置/替身）也不炸，按缺省返回。"""
    class _Bare:
        pass

    snap = read_percept_config(_Bare())
    assert snap["log_events"] is True          # 出厂缺省 = 留痕开
    assert snap["vision_auto_stop_minutes"] == 0
    assert snap["file_watch_dirs"] == []


# ---- 写侧校验 ----

def test_validate_bool_int_and_list():
    """三类字段各自校验通过并返回规范化补丁。"""
    patch_ = validate_percept_config({
        "enabled": True,
        "window_linger_minutes": 45,
        "file_watch_dirs": ["E:/Downloads", "E:/Downloads", "  "],
    })
    assert patch_ == {
        "enabled": True,
        "window_linger_minutes": 45,
        "file_watch_dirs": ["E:/Downloads"],      # 去重 + 丢空项
    }


def test_validate_rejects_unknown_key():
    """白名单外的键直接报错：静默忽略会把"打不开的开关"变成"点了没反应"。"""
    with pytest.raises(ValueError, match="不支持"):
        validate_percept_config({"dedup_window_s": 10})


def test_validate_rejects_wrong_types():
    """bool 要真布尔（"true" 字符串拒绝）、int 要整数（bool 是 int 子类，显式排除）。"""
    with pytest.raises(ValueError):
        validate_percept_config({"enabled": "true"})
    with pytest.raises(ValueError):
        validate_percept_config({"window_linger_minutes": True})
    with pytest.raises(ValueError):
        validate_percept_config({"file_watch_dirs": "E:/Downloads"})
    with pytest.raises(ValueError):
        validate_percept_config({"file_watch_dirs": [1, 2]})


def test_validate_int_range():
    """分钟类字段限制在 0..1440（0 = 关闭该特性）。"""
    assert validate_percept_config({"vision_auto_stop_minutes": 0}) == {
        "vision_auto_stop_minutes": 0
    }
    with pytest.raises(ValueError):
        validate_percept_config({"vision_auto_stop_minutes": -1})
    with pytest.raises(ValueError):
        validate_percept_config({"window_linger_minutes": 1441})


def test_validate_list_caps_items():
    """名单条目数有上限，防止面板把配置写成巨型数组。"""
    many = [f"/dir/{i}" for i in range(LIST_MAX_ITEMS)]
    assert len(validate_percept_config({"vcs_repos": many})["vcs_repos"]) == LIST_MAX_ITEMS
    with pytest.raises(ValueError):
        validate_percept_config({"vcs_repos": many + ["/one/more"]})


def test_pending_source_switches_are_not_editable():
    """未装配的收编位（calendar/system/schedule）不入白名单——不给假可用开关。"""
    for key in ("calendar_enabled", "system_enabled", "schedule_enabled"):
        assert key not in FIELD_BY_KEY
        with pytest.raises(ValueError):
            validate_percept_config({key: True})


# ---- 运行时生效 ----

def test_apply_writes_runtime_settings():
    """补丁落到 Settings 的属性名（percept_ 前缀）上，供宿主重装配时读取。"""
    settings = Settings()
    apply_percept_config(settings, {"enabled": True, "mail_watch_senders": ["boss@"]})
    assert settings.percept_enabled is True
    assert settings.percept_mail_watch_senders == ["boss@"]


# ---- 落盘 ----

def test_save_creates_section_and_parses_back(tmp_path):
    """文件里没有 [percept] 节 → 追加到末尾，且写出的字面量能 TOML 读回。"""
    with patch.object(Path, "home", return_value=tmp_path):
        path = _toml(tmp_path, 'model = "qwen-plus"\n')
        assert save_percept_config({
            "enabled": True,
            "window_enabled": False,
            "file_watch_dirs": ["E:/Downloads", "E:/Desktop"],
            "window_linger_minutes": 45,
        }) is True
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["model"] == "qwen-plus"                 # 其他键不动
        assert data["percept"]["enabled"] is True
        assert data["percept"]["window_enabled"] is False
        assert data["percept"]["file_watch_dirs"] == ["E:/Downloads", "E:/Desktop"]
        assert data["percept"]["window_linger_minutes"] == 45


def test_save_preserves_other_lines(tmp_path):
    """只动目标字段行：其他行的注释、同节其他字段、同文件其他节都原样保留。

    已知取舍（与 settings.set / email.config.set 同口径）：**被改写那一行自己的
    行内注释**会随整行替换丢失——值已经是面板替你改的，留着旧注释反而会误导。
    """
    with patch.object(Path, "home", return_value=tmp_path):
        path = _toml(
            tmp_path,
            "# 顶部注释\n[percept]\nenabled = false  # 总开关\n"
            "dedup_window_s = 300\n\n[tts]\nvolume = 50\n",
        )
        assert save_percept_config({"enabled": True}) is True
        content = path.read_text(encoding="utf-8")
        assert "# 顶部注释" in content
        assert "enabled = true" in content
        assert "# 总开关" not in content                      # 目标行注释随整行替换丢失
        assert "dedup_window_s = 300" in content              # 同节其他字段
        assert "volume = 50" in content                        # 其他节


def test_save_inserts_missing_field_into_existing_section(tmp_path):
    """节已存在但缺该字段：插在节头后，仍属 [percept] 节内。"""
    with patch.object(Path, "home", return_value=tmp_path):
        path = _toml(tmp_path, "[percept]\nenabled = true\n")
        assert save_percept_config({"mail_watch_senders": ["妈妈"]}) is True
        content = path.read_text(encoding="utf-8")
        assert content.index('mail_watch_senders = ["妈妈"]') > content.index("[percept]")
        assert tomllib.loads(content)["percept"]["mail_watch_senders"] == ["妈妈"]


def test_save_creates_minimal_file(tmp_path):
    """连文件都没有 → 创建只含 [percept] 的最小文件。"""
    with patch.object(Path, "home", return_value=tmp_path):
        assert save_percept_config({"vision_enabled": True}) is True
        content = (tmp_path / ".jarvis" / "settings.toml").read_text(encoding="utf-8")
        assert content == "[percept]\nvision_enabled = true\n"


def test_save_empty_patch_touches_nothing(tmp_path):
    """空补丁直接成功且不碰磁盘（调用方在此之前已拦 ValueError，这里守底层）。"""
    with patch.object(Path, "home", return_value=tmp_path):
        assert save_percept_config({}) is True
        assert not (tmp_path / ".jarvis" / "settings.toml").exists()


def test_save_escapes_quotes_in_list_items(tmp_path):
    """名单项含引号/反斜杠时不能写坏配置文件（走 toml_writer 的统一转义）。"""
    with patch.object(Path, "home", return_value=tmp_path):
        path = _toml(tmp_path, "[percept]\n")
        assert save_percept_config({"window_exclude_apps": ['we"ird', "C:\\path"]}) is True
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["percept"]["window_exclude_apps"] == ['we"ird', "C:\\path"]
