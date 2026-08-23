"""画像记忆（Phase 1a）单元测试。

覆盖: ProfileStore 存取/原子写/限额渲染/上限淘汰、
Refiner JSON 容错解析、对话截取过滤、异步触发节流。

@author aceFelix
"""

from __future__ import annotations

import json

import pytest

from agent.config.settings import Settings
from agent.core.message import Message, TextContent
from agent.core.memory.profile_store import (
    ProfileEntry,
    ProfileStore,
)
from agent.core.memory import profile_refiner as refiner


# ─────────────────────────────────────────────────────────────
# ProfileStore
# ─────────────────────────────────────────────────────────────

class TestProfileStore:
    def test_upsert_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "profile.json"
        store = ProfileStore(path)
        entry = ProfileEntry.new("习惯深夜工作", "schedule", 0.9, "s1")
        store.upsert(entry)

        # 新实例从盘上读回
        store2 = ProfileStore(path)
        assert len(store2) == 1
        loaded = store2.entries()[0]
        assert loaded.content == "习惯深夜工作"
        assert loaded.category == "schedule"
        assert loaded.confidence == pytest.approx(0.9)
        assert loaded.source_session == "s1"

    def test_upsert_updates_same_id(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        e = ProfileEntry.new("用 DeepSeek", "tool_usage", 0.6)
        store.upsert(e)
        e.confidence = 0.95
        e.content = "改用 GLM"
        store.upsert(e)
        assert len(store) == 1
        assert store.entries()[0].content == "改用 GLM"

    def test_delete(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        e = ProfileEntry.new("x", "other", 0.5)
        store.upsert(e)
        assert store.delete(e.id) is True
        assert len(store) == 0
        assert store.delete("not_exist") is False

    def test_render_for_prompt_token_limit(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        store.upsert(ProfileEntry.new("a" * 100, "other", 0.9))   # 高置信长条 ≈25 token
        store.upsert(ProfileEntry.new("b" * 20, "other", 0.5))    # 低置信短条 ≈5 token
        # 限额 15 token：长条（25 token）放不下被跳过，短条（5 token）应保留
        text = store.render_for_prompt(token_limit=15)
        assert "b" * 20 in text
        assert "a" * 100 not in text
        assert text.startswith("# 关于用户")

    def test_render_empty(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        assert store.render_for_prompt() == ""

    def test_prune_over_limit(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        for i in range(10):
            store.upsert(ProfileEntry.new(f"条目{i}", "other", i / 10))
        dropped = store.prune_over_limit(5)
        assert dropped == 5
        assert len(store) == 5
        # 留下的是置信度最高的 5 条
        confs = [e.confidence for e in store.entries()]
        assert confs == sorted(confs, reverse=True)
        assert min(confs) == pytest.approx(0.5)

    def test_invalid_category_falls_back(self):
        e = ProfileEntry.new("x", "not_a_category", 0.5)
        assert e.category == "other"

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "profile.json"
        path.write_text("not json {", encoding="utf-8")
        store = ProfileStore(path)
        assert len(store) == 0

    def test_no_tmp_file_left(self, tmp_path):
        path = tmp_path / "profile.json"
        store = ProfileStore(path)
        store.upsert(ProfileEntry.new("x", "other", 0.5))
        assert not (tmp_path / "profile.json.tmp").exists()
        # JSON 可解析且带 version
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1


# ─────────────────────────────────────────────────────────────
# JSON 容错解析
# ─────────────────────────────────────────────────────────────

class TestParseProfileJson:
    def test_plain_json(self):
        raw = '{"new": [{"category": "schedule", "content": "熬夜", "confidence": 0.9}], "updates": []}'
        data = refiner._parse_profile_json(raw)
        assert len(data["new"]) == 1

    def test_markdown_fenced(self):
        raw = '```json\n{"new": [], "updates": []}\n```'
        assert refiner._parse_profile_json(raw) == {"new": [], "updates": []}

    def test_json_with_prose(self):
        raw = '好的，以下是提取结果：\n{"new": [{"content": "c"}]}\n以上。'
        data = refiner._parse_profile_json(raw)
        assert data["new"][0]["content"] == "c"

    def test_garbage_returns_empty(self):
        assert refiner._parse_profile_json("模型抽风了") == {}
        assert refiner._parse_profile_json("") == {}


# ─────────────────────────────────────────────────────────────
# 对话截取
# ─────────────────────────────────────────────────────────────

def _msg(role: str, text: str) -> Message:
    return Message(role=role, content=[TextContent(text=text)])


class TestCollectDialog:
    def test_filters_commands_and_keeps_roles(self):
        msgs = [
            _msg("user", "/model deepseek"),      # 命令 → 过滤
            _msg("user", "我习惯用 DeepSeek 写代码"),
            _msg("assistant", "好的，已了解"),
        ]
        text = refiner._collect_dialog_text(msgs)
        assert "DeepSeek" in text
        assert "/model" not in text
        assert "用户:" in text and "贾维斯:" in text

    def test_truncates_to_window(self):
        msgs = [_msg("user", f"消息{i}") for i in range(100)]
        text = refiner._collect_dialog_text(msgs)
        # 只保留最近 40 条
        assert "消息99" in text
        assert "消息10" not in text
        assert "消息60" in text  # 第 60 条起保留


# ─────────────────────────────────────────────────────────────
# 异步触发节流
# ─────────────────────────────────────────────────────────────

class TestMaybeRefineAsync:
    def _settings(self, **kw) -> Settings:
        base = {"profile_enabled": True, "profile_refine_min_messages": 6}
        base.update(kw)
        return Settings(**base)

    def test_disabled_never_triggers(self):
        msgs = [_msg("user", "hi")] * 10
        assert refiner.maybe_refine_async(msgs, "s", self._settings(profile_enabled=False)) is False

    def test_too_few_messages(self):
        msgs = [_msg("user", "hi")] * 3
        assert refiner.maybe_refine_async(msgs, "s", self._settings()) is False

    def test_throttle_second_call_skipped(self):
        refiner._last_refine_ts = 0.0  # 重置节流状态
        msgs = [_msg("user", "hi")] * 10
        assert refiner.maybe_refine_async(msgs, "s", self._settings()) is True
        # 间隔内第二次调用直接跳过
        assert refiner.maybe_refine_async(msgs, "s", self._settings()) is False


# ─────────────────────────────────────────────────────────────
# refine_session 端到端（fake provider）
# ─────────────────────────────────────────────────────────────

class _FakeProvider:
    """提炼链路 fake：is_thinking_enabled/set_thinking_enabled + stream 收集。"""

    def __init__(self, reply: str):
        self._reply = reply
        self.thinking_state: list[bool] = []

    def is_thinking_enabled(self) -> bool:
        return True

    def set_thinking_enabled(self, enabled: bool) -> None:
        self.thinking_state.append(enabled)

    async def stream(self, **kwargs):
        from agent.llm.base import TextDelta
        yield TextDelta(text=self._reply)

    async def close(self) -> None:
        pass


class TestRefineSessionE2E:
    def test_full_pipeline(self, tmp_path, monkeypatch):
        store = ProfileStore(tmp_path / "profile.json")
        old = ProfileEntry.new("以前用 DeepSeek", "tool_usage", 0.6, "s_old")
        store.upsert(old)

        reply = json.dumps({
            "new": [
                {"category": "schedule", "content": "习惯深夜工作", "confidence": 0.9},
                {"category": "bad", "content": "", "confidence": 0.5},  # 空 content → 跳过
            ],
            "updates": [
                {"replace_id": old.id, "category": "tool_usage",
                 "content": "已改用 GLM", "confidence": 0.95},
                {"replace_id": "ent_not_exist", "content": "幻觉条目", "confidence": 0.9},
            ],
        }, ensure_ascii=False)
        fake = _FakeProvider(reply)
        monkeypatch.setattr(refiner, "_build_refine_provider", lambda settings: fake)

        settings = Settings(profile_enabled=True)
        msgs = [_msg("user", "我最近都改用 GLM 了，经常熬夜写代码到两三点")]
        report = refiner.refine_session(msgs, "s_new", settings, store=store)

        assert report.error == ""
        assert report.added == 1
        assert report.updated == 1
        # 旧条目被替换
        assert store.get(old.id) is None
        contents = [e.content for e in store.entries()]
        assert "习惯深夜工作" in contents
        assert "已改用 GLM" in contents
        assert "以前用 DeepSeek" not in contents
        # 幻觉 replace_id 未产生重复条目
        assert len(store) == 2
        # 提炼时关闭了思考
        assert fake.thinking_state == [False, True]

    def test_mock_without_refine_model_skips(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        settings = Settings(api_format="mock")  # 无独立提炼模型
        report = refiner.refine_session(
            [_msg("user", "内容")], "s", settings, store=store
        )
        assert report.skipped is True
        assert len(store) == 0


# ─────────────────────────────────────────────────────────────
# M2: system prompt 注入（缓存 + reload）
# ─────────────────────────────────────────────────────────────

class TestProfileSection:
    def setup_method(self):
        from agent.prompts import system as sys_mod
        sys_mod._PROFILE_CACHE = None

    def test_inject_and_cache(self, tmp_path, monkeypatch):
        from agent.prompts import system as sys_mod
        from agent.core.memory import profile_store as ps_mod

        store = ProfileStore(tmp_path / "profile.json")
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9))
        monkeypatch.setattr(ps_mod, "ProfileStore", lambda: store)

        settings = Settings(profile_enabled=True)
        text1 = sys_mod._profile_section(settings)
        assert "习惯深夜工作" in text1
        assert text1.startswith("# 关于用户")

        # 再入库新条目 → 缓存不刷新（同会话稳定，缓存友好）
        store.upsert(ProfileEntry.new("新条目", "other", 0.9))
        text2 = sys_mod._profile_section(settings)
        assert text2 == text1

        # reload 后刷新
        sys_mod.reload_profile_cache()
        text3 = sys_mod._profile_section(settings)
        assert "新条目" in text3

    def test_disabled_returns_empty(self):
        from agent.prompts import system as sys_mod

        assert sys_mod._profile_section(Settings(profile_enabled=False)) == ""
        assert sys_mod._profile_section(None) == ""


# ─────────────────────────────────────────────────────────────
# M2: /memory 命令
# ─────────────────────────────────────────────────────────────

class _FakeUI:
    def __init__(self):
        self.messages: list[str] = []

    def info(self, msg: str) -> None:
        self.messages.append(msg)

    def warn(self, msg: str) -> None:
        self.messages.append(msg)


class TestMemoryCommand:
    def _ctx(self, ui, settings):
        from types import SimpleNamespace
        return SimpleNamespace(ui=ui, settings=settings)

    def _run(self, cmd: str, store_path, settings=None):
        import asyncio
        from agent.commands.handlers.session_commands import handle_memory
        from agent.core.memory import profile_store as ps_mod

        settings = settings or Settings()
        # 指向临时存储，不污染真实 ~/.jarvis
        monkey_store = ProfileStore(store_path)
        orig = ps_mod.ProfileStore
        ps_mod.ProfileStore = lambda: monkey_store
        try:
            ui = _FakeUI()
            asyncio.run(handle_memory(self._ctx(ui, settings), cmd))
            return ui
        finally:
            ps_mod.ProfileStore = orig

    def test_list_empty_hint(self, tmp_path):
        ui = self._run("/memory", tmp_path / "p.json")
        assert any("画像记忆为空" in m for m in ui.messages)

    def test_add_and_list_and_del(self, tmp_path):
        path = tmp_path / "p.json"
        ui = self._run("/memory add 习惯用 GLM 写代码", path)
        assert any("已记住" in m for m in ui.messages)

        ui = self._run("/memory", path)
        assert any("习惯用 GLM 写代码" in m for m in ui.messages)
        entry_id = [e.id for e in ProfileStore(path).entries()][0]

        ui = self._run(f"/memory del {entry_id}", path)
        assert any("已删除" in m for m in ui.messages)
        assert len(ProfileStore(path)) == 0

    def test_del_not_found(self, tmp_path):
        ui = self._run("/memory del ent_not_exist", tmp_path / "p.json")
        assert any("未找到" in m for m in ui.messages)

    def test_clear_requires_yes(self, tmp_path):
        path = tmp_path / "p.json"
        self._run("/memory add a", path)
        ui = self._run("/memory clear", path)
        assert any("确认" in m for m in ui.messages)
        assert len(ProfileStore(path)) == 1

        ui = self._run("/memory clear yes", path)
        assert any("已清空" in m for m in ui.messages)
        assert len(ProfileStore(path)) == 0

    def test_add_empty_usage(self, tmp_path):
        ui = self._run("/memory add", tmp_path / "p.json")
        assert any("用法" in m for m in ui.messages)

    def test_unknown_subcommand_usage(self, tmp_path):
        ui = self._run("/memory bogus", tmp_path / "p.json")
        assert any("用法" in m for m in ui.messages)


# ─────────────────────────────────────────────────────────────
# M3: 衰减维护
# ─────────────────────────────────────────────────────────────

class TestDecay:
    def test_old_entry_decays_and_removed(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("过时的习惯", "work_habit", 0.8)
        # 伪造 90 天前创建（3 个半衰期 → 0.8 * 0.125 = 0.1 < floor 0.15 → 删除）
        e.created_at -= 90 * 86400
        e.updated_at = e.created_at
        store.upsert(e)

        removed = store.decay()
        assert removed == 1
        assert len(store) == 0

    def test_recent_entry_kept_with_decayed_confidence(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("近期习惯", "work_habit", 0.8)
        # 30 天前 = 1 个半衰期 → 0.8 * 0.5 = 0.4 > floor → 保留但衰减
        e.created_at -= 30 * 86400
        e.updated_at = e.created_at
        store.upsert(e)

        removed = store.decay()
        assert removed == 0
        assert len(store) == 1
        assert store.entries()[0].confidence == pytest.approx(0.4, abs=0.01)

    def test_manual_entry_never_decays(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("用户亲写", "other", 0.99, source_session="manual")
        e.created_at -= 365 * 86400  # 一年前
        e.updated_at = e.created_at
        store.upsert(e)

        assert store.decay() == 0
        assert store.entries()[0].confidence == pytest.approx(0.99)

    def test_fresh_entry_untouched(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        store.upsert(ProfileEntry.new("刚记的", "other", 0.9))
        assert store.decay() == 0
        assert store.entries()[0].confidence == pytest.approx(0.9)


class TestProfileMaintenanceTask:
    def test_engine_registers_and_fires(self):
        from agent.core.daemon.proactive import (
            ProactiveConfig,
            ProactiveEngine,
            _PROFILE_MAINT_NOTE,
        )

        class _FakeScheduler:
            def __init__(self):
                self.tasks: list = []

            def add_task(self, **kw):
                from types import SimpleNamespace
                import uuid
                t = SimpleNamespace(id=f"t_{uuid.uuid4().hex[:6]}", **kw)
                self.tasks.append(t)
                return t

            def list_pending(self):
                return self.tasks

        sched = _FakeScheduler()
        engine = ProactiveEngine(
            scheduler=sched, config=ProactiveConfig(), on_notify=lambda m: None
        )
        engine.start()
        notes = [t.note for t in sched.tasks]
        assert _PROFILE_MAINT_NOTE in notes
        maint = [t for t in sched.tasks if t.note == _PROFILE_MAINT_NOTE][0]
        assert maint.repeat == "daily"


# ─────────────────────────────────────────────────────────────
# M3: /memory refine 命令
# ─────────────────────────────────────────────────────────────

class TestMemoryRefineCommand:
    def test_refine_too_few_messages(self, tmp_path):
        import asyncio
        from types import SimpleNamespace
        from agent.commands.handlers.session_commands import handle_memory

        ui = _FakeUI()
        ctx = SimpleNamespace(
            ui=ui, settings=Settings(profile_enabled=True),
            messages=[_msg("user", "hi")],
        )
        asyncio.run(handle_memory(ctx, "/memory refine"))
        assert any("太少" in m for m in ui.messages)


# ─────────────────────────────────────────────────────────────
# 配置映射
# ─────────────────────────────────────────────────────────────

class TestSettingsMapping:
    def test_defaults(self):
        s = Settings()
        assert s.profile_enabled is True
        assert s.profile_max_entries == 200
        assert s.profile_inject_token_limit == 300
        assert s.profile_refine_model == ""

    def test_maybe_refine_async_respects_min_messages_default(self):
        s = Settings()  # 默认 6 条
        msgs = [_msg("user", "hi")] * 5
        refiner._last_refine_ts = 0.0
        assert refiner.maybe_refine_async(msgs, "s", s) is False
