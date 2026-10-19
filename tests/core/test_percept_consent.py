"""首次启动感知询问的记录（W2 第 7 步）—— `agent/core/percept/consent.py`。

这里验的是**该不该问**与**记不记得住**这两件事：投递时机与文案在 serve 宿主
（`hub.py`，见 tests/serve/test_hub_percept.py），配置白名单在
tests/config/test_percept_settings.py。

三条容易被写坏的口径都有对应用例：
- 「只问一次」靠**文件存在性**兑现：内容读坏了也算问过（否则长期运行的宿主会
  每次启动都弹同一条引导）；
- 「已经在用 / 手改过配置的人不再问」：总开关开着，或 settings.toml 里已有
  `[percept]` 表；
- 落盘原子性：临时文件 + `os.replace`，写失败只降级为“下次可能再问一次”，绝不抛。

@author aceFelix
"""

from __future__ import annotations

import json

import pytest

import agent.config.toml_writer as toml_writer_mod
import agent.core.percept.consent as consent_mod
from agent.config.settings import Settings
from agent.core.percept.consent import (
    CHOICES,
    configured_in_toml,
    has_record,
    mark_asked,
    needs_ask,
    read_consent,
    record_choice,
)


@pytest.fixture
def consent_env(tmp_path, monkeypatch):
    """把询问记录与用户级 settings.toml 都重定向到 tmp_path（绝不碰真实 ~/.jarvis）。"""
    path = tmp_path / "percept" / "consent.json"
    monkeypatch.setattr(consent_mod, "consent_path", lambda: path)
    monkeypatch.setattr(toml_writer_mod, "settings_path", lambda: tmp_path / "settings.toml")
    return path


def _write_raw(path, text: str) -> None:
    """直接写文件内容（用于制造“坏 JSON”这类非正常状态）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---- 读写与降级 ----

def test_no_file_means_no_record(consent_env):
    """没文件：读回 None、has_record False（新用户）。"""
    assert consent_env.exists() is False
    assert read_consent() is None
    assert has_record() is False


def test_mark_asked_writes_readable_record(consent_env):
    """mark_asked：落一份含 asked_at / choice 空值的记录，可回读。"""
    record = mark_asked(master_enabled=False)
    assert record["asked_at"]
    assert record["choice"] == ""
    assert record["master_enabled"] is False
    on_disk = json.loads(consent_env.read_text(encoding="utf-8"))
    assert on_disk["asked_at"] == record["asked_at"]
    assert read_consent()["asked_at"] == record["asked_at"]


def test_mark_asked_is_idempotent_and_keeps_choice(consent_env):
    """重复 mark_asked 不覆盖已有表态（“已问过”不该把 choice 洗掉）。"""
    record_choice("off")
    again = mark_asked(master_enabled=False)
    assert again["choice"] == "off"
    assert read_consent()["choice"] == "off"


def test_read_corrupt_file_returns_none_but_record_exists(consent_env):
    """文件存在但内容坏：读侧回 None 不抛，has_record 仍为 True（不再问）。"""
    _write_raw(consent_env, "{不是 JSON")
    assert read_consent() is None
    assert has_record() is True


def test_read_non_dict_json_is_none(consent_env):
    """顶层是数组/字符串的记录视为无效（不回一个形状不对的 dict）。"""
    _write_raw(consent_env, "[1, 2]")
    assert read_consent() is None


def test_mark_asked_survives_write_failure(consent_env, monkeypatch):
    """落盘失败只降级：内存记录照旧返回，异常不外溢。"""
    monkeypatch.setattr(consent_mod.os, "replace", lambda *a, **kw: (_ for _ in ()).throw(OSError("磁盘只读")))
    record = mark_asked(master_enabled=True)
    assert record["asked_at"]
    assert consent_env.exists() is False


# ---- 表态 ----

def test_record_choice_accepts_only_known_values(consent_env):
    """非法表态直接报错（不静默忽略，否则写出一眼看不懂的文件）。"""
    with pytest.raises(ValueError):
        record_choice("maybe")
    for choice in CHOICES:
        record = record_choice(choice)
        assert record["choice"] == choice


def test_record_choice_without_prior_ask_marks_question_asked(consent_env):
    """用户在面板上表态 = 等价于问过（asked_at 补 None 占位，chosen_at 记录时间）。"""
    record = record_choice("on")
    assert record["asked_at"] is None
    assert record["chosen_at"]
    assert has_record() is True


# ---- needs_ask 的四条判据 ----

def test_needs_ask_for_brand_new_user(consent_env):
    """什么都没配过、也没记录：该问一次。"""
    assert needs_ask(Settings(percept_enabled=False)) is True


def test_needs_ask_false_when_master_on(consent_env):
    """总开关开着：用户已经在用，不必科普。"""
    assert needs_ask(Settings(percept_enabled=True)) is False


def test_needs_ask_false_when_toml_has_percept_section(consent_env, tmp_path):
    """手改过 settings.toml 的 `[percept]`（哪怕全关）：也算做过选择，不问。"""
    (tmp_path / "settings.toml").write_text(
        "[percept]\nenabled = false\n", encoding="utf-8"
    )
    assert configured_in_toml() is True
    assert needs_ask(Settings(percept_enabled=False)) is False


def test_configured_in_toml_tolerates_missing_or_broken_file(consent_env, tmp_path):
    """配置文件缺失或语法坏：判“没配过”（该问，而不是因为读不到就不问）。"""
    assert configured_in_toml() is False
    (tmp_path / "settings.toml").write_text(" [[[ bad", encoding="utf-8")
    assert configured_in_toml() is False


def test_needs_ask_false_after_asked(consent_env):
    """问过就翻篇：第二次启动不再问（“只出现一次”的落点在这里）。"""
    mark_asked()
    assert needs_ask(Settings(percept_enabled=False)) is False
