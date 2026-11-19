"""常驻人设卡（P6）单元测试。

覆盖：输出接地校验、固定 section 顺序、长度裁剪、pending 状态、
生成/降级、重启补偿、输入采样。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent.config.settings import Settings
from agent.core.memory import profile_persona as PP
from agent.core.memory.profile_store import ProfileEntry, ProfileStore


class _FakeProvider:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _settings(**kw) -> Settings:
    base = {"profile_enabled": True, "profile_persona_enabled": True,
            "profile_persona_token_limit": 800}
    base.update(kw)
    return Settings(**base)


def _entry(content: str, category: str = "identity", conf: float = 0.9) -> ProfileEntry:
    return ProfileEntry.new(content, category, conf)


def _payload(entries: list[ProfileEntry], *, extra=None) -> str:
    sections = [
        {
            "key": "identity",
            "bullets": [{"text": "杭州的 AI 开发者", "sources": [entries[0].id]}],
        },
        {
            "key": "communication",
            "bullets": [{"text": "喜欢先给结论", "sources": [entries[0].id]}],
        },
    ]
    if extra:
        sections.extend(extra)
    return json.dumps({"sections": sections}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path, monkeypatch):
    """人设卡/状态文件钉到临时目录，绝不碰真实 ~/.jarvis。"""
    monkeypatch.setattr(PP, "memory_dir", lambda: tmp_path / "memory")
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    yield


class TestParse:
    def test_valid_output_keeps_fixed_order(self) -> None:
        entries = [_entry("杭州的 AI 开发者")]
        data = json.loads(json.dumps({"sections": [
            {"key": "communication", "bullets": [{"text": "喜欢先给结论", "sources": [entries[0].id]}]},
            {"key": "identity", "bullets": [{"text": "杭州的 AI 开发者", "sources": [entries[0].id]}]},
        ]}))
        persona = PP.parse_persona_output(data, entries, _settings())
        assert persona is not None
        assert [s.key for s in persona.sections] == ["identity", "communication"]

    def test_unknown_source_dropped(self) -> None:
        entries = [_entry("杭州的 AI 开发者")]
        data = {"sections": [{"key": "identity", "bullets": [
            {"text": "杭州的 AI 开发者", "sources": ["ent_missing"]},
        ]}]}
        assert PP.parse_persona_output(data, entries, _settings()) is None

    def test_bullet_without_source_dropped(self) -> None:
        entries = [_entry("杭州的 AI 开发者")]
        data = {"sections": [{"key": "identity", "bullets": [{"text": "杭州的 AI 开发者"}]}]}
        assert PP.parse_persona_output(data, entries, _settings()) is None

    def test_overlong_bullet_dropped(self) -> None:
        entries = [_entry("杭州的 AI 开发者")]
        data = {"sections": [{"key": "identity", "bullets": [
            {"text": "很长" * 60, "sources": [entries[0].id]},
        ]}]}
        assert PP.parse_persona_output(data, entries, _settings()) is None

    def test_duplicate_bullets_deduped(self) -> None:
        entries = [_entry("杭州的 AI 开发者")]
        data = {"sections": [{"key": "identity", "bullets": [
            {"text": "杭州的 AI 开发者", "sources": [entries[0].id]},
            {"text": "杭州的 AI 开发者。", "sources": [entries[0].id]},
        ]}]}
        persona = PP.parse_persona_output(data, entries, _settings())
        assert persona is not None
        assert len(persona.sections[0].bullets) == 1

    def test_trim_keeps_identity(self) -> None:
        identity = PP.PersonaSection(
            "identity", "他是谁", [PP.PersonaBullet("杭州的 AI 开发者", ["ent_x"])]
        )
        focus = PP.PersonaSection(
            "current_focus", "当前重点",
            [PP.PersonaBullet("正在升级记忆系统" * 8, ["ent_x"])],
        )
        identity_only = PP.Persona(sections=[identity])
        min_cost = PP._cost(PP.render_persona(identity_only), "pseudo")
        persona = PP.Persona(sections=[identity, focus], token_limit=min_cost + 1)
        persona.token_count = PP._cost(PP.render_persona(persona), "pseudo")
        PP._trim_to_limit(persona, _settings(profile_persona_token_limit=min_cost + 1))
        assert persona.token_count <= min_cost + 1
        assert any(s.key == "identity" for s in persona.sections)

    def test_render_contains_arbitration(self) -> None:
        entries = [_entry("杭州的 AI 开发者")]
        persona = PP.parse_persona_output(
            {"sections": [{"key": "identity", "bullets": [
                {"text": "杭州的 AI 开发者", "sources": [entries[0].id]},
            ]}]},
            entries, _settings(),
        )
        text = PP.render_persona(persona)
        assert "# 用户画像（常驻人设）" in text
        assert "以长期记忆为准" in text

    def test_sample_includes_permanent(self, tmp_path) -> None:
        store = ProfileStore(tmp_path / "p.json")
        perm = _entry("杭州", "identity", 0.5)
        store.upsert(perm)
        for i in range(30):
            store.upsert(_entry(f"习惯{i}", "schedule", 0.9))
        sample = PP._sample_entries(store, limit=5)
        assert any(e.id == perm.id for e in sample)


class TestState:
    def test_pending_roundtrip(self) -> None:
        assert PP.load_state()["pending"] is False
        PP.mark_pending()
        assert PP.load_state()["pending"] is True
        PP._clear_pending()
        assert PP.load_state()["pending"] is False

    def test_corrupt_state_is_not_pending(self, tmp_path) -> None:
        PP.state_path().write_text("{bad", encoding="utf-8")
        assert PP.load_state()["pending"] is False


class TestBuildFlow:
    def _fake_store(self, monkeypatch) -> ProfileStore:
        store = ProfileStore(PP.memory_dir() / "profile.json")
        store.upsert(_entry("杭州的 AI 开发者"))
        monkeypatch.setattr(PP, "ProfileStore", lambda: store)
        return store

    def test_build_then_skip_then_pending(self, monkeypatch) -> None:
        store = self._fake_store(monkeypatch)
        provider = _FakeProvider()
        monkeypatch.setattr(PP, "_build_refine_provider", lambda settings: provider)
        monkeypatch.setattr(
            PP, "_call_llm_sync",
            lambda provider, model, prompt, max_tokens=1500: _payload(store.entries()),
        )

        settings = _settings()
        assert PP.maybe_rebuild_async(settings, wait_seconds=5) is True
        persona = PP.load_persona()
        assert persona is not None
        assert persona.source_count == 1

        # 没变化：不重建
        assert PP.maybe_rebuild_async(settings, wait_seconds=5) is False

        # 新条目 + pending → 重建；source_count 跟全库
        store.upsert(_entry("喜欢先给结论", "preference", 0.8))
        PP.mark_pending()
        assert PP.maybe_rebuild_async(settings, wait_seconds=5) is True
        assert PP.load_persona().source_count == 2

    def test_failure_keeps_old_card(self, monkeypatch) -> None:
        store = self._fake_store(monkeypatch)
        monkeypatch.setattr(PP, "_build_refine_provider", lambda settings: _FakeProvider())
        monkeypatch.setattr(PP, "_call_llm_sync", lambda *a, **k: "not json")
        PP.mark_pending()
        assert PP.maybe_rebuild_async(_settings(), wait_seconds=5) is True
        assert PP.load_persona() is None  # 首次构建失败：仍无卡，回退旧注入

    def test_startup_only_when_pending_or_missing(self, monkeypatch) -> None:
        store = self._fake_store(monkeypatch)
        monkeypatch.setattr(PP, "_build_refine_provider", lambda settings: _FakeProvider())
        monkeypatch.setattr(
            PP, "_call_llm_sync",
            lambda provider, model, prompt, max_tokens=1500: _payload(store.entries()),
        )
        settings = _settings()

        # 无卡 + 有条目 → 启动首建
        assert PP.startup_check_async(settings) is True
        import time

        deadline = time.time() + 5
        while time.time() < deadline and PP.load_persona() is None:
            time.sleep(0.02)
        assert PP.load_persona() is not None

        # 有卡、未 pending → 正常启动不重建
        assert PP.startup_check_async(settings) is False


class TestDiscard:
    """2026-11-19：`/memory clear` 必须让人设卡失效。

    回归背景：清空画像后旧卡仍在磁盘上，注入层继续用它；而空库会让
    build_persona 返回 None，重建无法覆盖 → 旧卡永久残留。
    """

    def _write_card(self) -> None:
        PP._write_json(PP.persona_path(), {"version": 1, "sections": []})
        PP.mark_pending()

    def test_discard_removes_card_and_pending(self) -> None:
        self._write_card()
        assert PP.persona_path().is_file()
        assert PP.discard() is True
        assert not PP.persona_path().exists()
        assert not PP.state_path().exists()
        assert PP.load_persona() is None
        assert PP.load_state()["pending"] is False

    def test_discard_without_files_is_ok(self) -> None:
        assert PP.discard() is True

    def test_discard_refuses_while_building(self) -> None:
        """重建线程在跑时不动手：删了也会被 worker 写回。"""
        self._write_card()
        assert PP._in_flight.acquire(blocking=False)
        try:
            assert PP.discard() is False
            assert PP.persona_path().is_file()
        finally:
            PP._in_flight.release()
