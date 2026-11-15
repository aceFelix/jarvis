"""画像记忆（Phase 1a）单元测试。

覆盖: ProfileStore 存取/原子写/限额渲染/上限淘汰、
Refiner JSON 容错解析、对话截取过滤、异步触发节流。

@author aceFelix
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from agent.config.settings import Settings
from agent.core.message import Message, TextContent
from agent.core.memory.profile_store import (
    ProfileEntry,
    ProfileStore,
)
from agent.core.memory import profile_refiner as refiner


@pytest.fixture(autouse=True)
def _isolate_refine_ledger(tmp_path):
    """把提炼水位台账钉到临时目录：用例间互不影响，也不碰真实 ~/.jarvis。

    2026-11 P0 起，提炼节流从模块级 `_last_refine_ts` 改成跨进程台账；
    不隔离的话第一个用例会把 `last_refine_ts` 写进真实用户目录，
    后续用例（或用户自己）会被莫名节流。
    @author aceFelix
    """
    from agent.core.memory import profile_events, profile_ledger as ledger

    ledger.configure_ledger_path(tmp_path / "memory" / "refine_ledger.json")
    profile_events.configure_events_path(tmp_path / "memory" / "events.jsonl")
    yield
    ledger.reset_for_tests()
    profile_events.reset_for_tests()


@pytest.fixture(autouse=True)
def _no_real_llm_client(monkeypatch):
    """禁止本文件用例构造真 LLM 客户端（上面那条泄漏线程能活 1.6s 的原因）。

    默认 `Settings()` 的 `api_format` 是 `openai`（不是 mock），所以
    `_build_refine_provider` 会真去 `agent.bootstrap._build_provider` 建 `AsyncOpenAI`：
    实测一次 1624 ms（全部耗在后台线程里，就是泄漏线程的寿命），而在配了
    `OPENAI_API_KEY` 的机器上还会从测试里发出真实请求、读写用户真实画像库。

    抛异常本身不够——`refine_session` 会把异常吃掉变成 `report.error`，在子线程里没人
    看见，所以这里只登记调用、在收尾时断言（收尾顺序在 `_no_refine_worker_leak` 之后，
    线程已 drain 完，不会漏记）。mock 分支在进工厂前就返回 None，不受影响。

    @author aceFelix
    """
    from agent import bootstrap

    hits: list[str] = []

    def _forbidden(*args, **kwargs):
        hits.append("_build_provider")
        raise AssertionError("测试禁止构造真 LLM provider，请隔离 _build_refine_provider")

    monkeypatch.setattr(bootstrap, "_build_provider", _forbidden)
    yield
    if hits:
        pytest.fail("用例期间调到了真 LLM 工厂（后台提炼线程逃逸到真网络路径）")


@pytest.fixture(autouse=True)
def _no_refine_worker_leak():
    """收口后台提炼线程，不让它滑到后续用例里（2026-11-03 偶发独红的根因闸）。

    `maybe_refine_async` 起的是 daemon 线程且无 join 入口。2026-11-03 用临时看门狗
    插件实测：用例结束后该线程还会活约 1.6 s——默认 `Settings()` 的 `api_format` 是
    `openai`（不是 mock），于是线程里真的在跑 `_build_refine_provider` → 构造
    `AsyncOpenAI` → 直到报 `Missing credentials` 才退；它漂进了本文件 13 个后续用例。

    危害不只是脏数据：只要线程启动被 GIL 延迟到下一个用例装好 monkeypatch 之后，它就会
    读到**那个用例**装在 `refiner._build_refine_provider` 上的 fake，把它的
    `thinking_state` 多撑一轮——`test_full_pipeline` 在全量跑时偶发断言失败、单跑必绿就
    是这个竞态（把窗口撑开后已稳定复现，泄漏线程单独就贡献了 [False, True] 两项）。

    @author aceFelix
    """
    yield
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not [t for t in threading.enumerate() if t.name == "profile-refiner"]:
            return
        time.sleep(0.02)
    pytest.fail("profile-refiner 后台线程未在 5s 内退出，会拿到后续用例的 monkeypatch")


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
        # JSON 可解析且带 version（P2a 起为 3）
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 3


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

    def test_throttle_second_call_skipped(self, monkeypatch):
        # 必须隔离真 provider：不隔离时后台线程会去建真的 AsyncOpenAI 客户端（实测 1.6s），
        # 有 OPENAI_API_KEY 的机器上还会从测试里发出真实请求、并读写用户真实画像库
        monkeypatch.setattr(refiner, "_build_refine_provider", lambda settings: None)
        msgs = [_msg("user", "hi")] * 10
        assert refiner.maybe_refine_async(msgs, "s", self._settings()) is True
        # 间隔内第二次调用直接跳过（首轮刚写过 last_refine_ts）
        assert refiner.maybe_refine_async(msgs, "s", self._settings()) is False

    def test_worker_thread_does_not_outlive_test(self, monkeypatch):
        """守住本文件 autouse fixture 依赖的那条不变量：节流触发后台线程后，线程必须
        在本用例内退出，而不是漂到后面的用例里去读它们装的 patch。

        @author aceFelix
        """
        monkeypatch.setattr(refiner, "_build_refine_provider", lambda settings: None)
        assert refiner.maybe_refine_async(
            [_msg("user", "hi")] * 10, "s", self._settings()
        ) is True
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if not [t for t in threading.enumerate() if t.name == "profile-refiner"]:
                return
            time.sleep(0.02)
        pytest.fail("后台提炼线程未在用例内退出")


# ─────────────────────────────────────────────────────────────
# refine_session 端到端（fake provider）
# ─────────────────────────────────────────────────────────────

class _FakeProvider:
    """提炼链路 fake：支持脚本化多次回复（抽取段 / 裁决段）。"""

    def __init__(self, reply: str | list[str]):
        self._replies = list(reply) if isinstance(reply, list) else [reply]
        self.prompts: list[str] = []
        self.thinking_state: list[bool] = []

    def is_thinking_enabled(self) -> bool:
        return True

    def set_thinking_enabled(self, enabled: bool) -> None:
        self.thinking_state.append(enabled)

    async def stream(self, **kwargs):
        from agent.llm.base import TextDelta
        self.prompts.append(kwargs["messages"][-1].get_text())
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        yield TextDelta(text=reply)

    async def close(self) -> None:
        pass


class TestRefineSessionE2E:
    def test_full_pipeline(self, tmp_path, monkeypatch):
        store = ProfileStore(tmp_path / "profile.json")
        old = ProfileEntry.new("以前用 DeepSeek", "tool_usage", 0.6, "s_old")
        store.upsert(old)

        extract = json.dumps({
            "candidates": [
                {"content": "习惯深夜工作", "category": "schedule", "confidence": 0.9},
                {"content": "", "category": "bad", "confidence": 0.5},  # 空 content → 硬闸拒
            ]
        }, ensure_ascii=False)
        resolve = json.dumps({
            "facts": [
                {"op": "add", "content": "习惯深夜工作", "category": "schedule",
                 "confidence": 0.9, "aliases": ["熬夜", "晚睡", "作息晚"]},
                {"op": "update", "target_id": old.id, "content": "已改用 GLM",
                 "category": "tool_usage", "confidence": 0.95, "aliases": ["GLM"]},
                {"op": "noop", "target_id": "ent_not_exist"},
            ]
        }, ensure_ascii=False)
        fake = _FakeProvider([extract, resolve])
        monkeypatch.setattr(refiner, "_build_refine_provider", lambda settings: fake)

        settings = Settings(profile_enabled=True)
        msgs = [_msg("user", "我最近都改用 GLM 了，经常熬夜写代码到两三点")]
        report = refiner.refine_session(msgs, "s_new", settings, store=store)

        assert report.error == ""
        assert report.extracted == 1 and report.rejected == 1
        assert report.added == 1
        assert report.updated == 1
        assert report.noop == 1
        # 旧条目被替换
        assert store.get(old.id) is None
        entries = store.entries()
        contents = [e.content for e in entries]
        assert "习惯深夜工作" in contents
        assert "已改用 GLM" in contents
        assert "以前用 DeepSeek" not in contents
        assert len(store) == 2
        # 别名落盘
        new_entry = next(e for e in entries if e.content == "习惯深夜工作")
        assert "熬夜" in new_entry.aliases
        # 两段各开关一次思考
        assert fake.thinking_state == [False, True, False, True]

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

    def test_events_empty_hint(self, tmp_path):
        ui = self._run("/memory events", tmp_path / "p.json")
        assert any("事件轨为空" in m for m in ui.messages)

    def test_events_shows_records(self, tmp_path):
        """P2c：`/memory events` 展示未过期事件与到期时间。"""
        from agent.core.memory import profile_events

        now = time.time()
        profile_events.append_event(
            "schedule", "下周三面试", source_session="s1",
            ts=now, expire_at=now + 7 * 86400,
        )
        ui = self._run("/memory events", tmp_path / "p.json")
        joined = "\n".join(ui.messages)
        assert "下周三面试" in joined
        assert "到期" in joined

    def test_unknown_subcommand_usage(self, tmp_path):
        ui = self._run("/memory bogus", tmp_path / "p.json")
        assert any("用法" in m for m in ui.messages)

    def test_list_shows_last_refine_telemetry(self, tmp_path):
        """P1d：列表头部展示上轮提炼计数（新增/跳过/拒绝），让人看得见提炼真的跑了。"""
        from agent.core.memory import profile_ledger as ledger

        path = tmp_path / "p.json"
        ProfileStore(path).upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9))
        ledger.advance("default", 3, now=time.time(), report={"added": 1, "noop": 2, "rejected": 1})

        ui = self._run("/memory", path)
        joined = "\n".join(ui.messages)
        assert "上次提炼" in joined
        assert "新增 1" in joined and "跳过 2" in joined and "拒绝 1" in joined

    def test_empty_store_still_shows_telemetry(self, tmp_path):
        """库为空时也要能看到上轮提炼“拒绝/跳过”的原因类信息。"""
        from agent.core.memory import profile_ledger as ledger

        ledger.advance("default", 3, now=time.time(), report={"rejected": 2})
        ui = self._run("/memory", tmp_path / "p.json")
        joined = "\n".join(ui.messages)
        assert "画像记忆为空" in joined
        assert "拒绝 2" in joined

    def test_list_reports_invalidated_history_count(self, tmp_path):
        """P2b：失效条目不展示，但告诉用户它们作为历史还在。"""
        path = tmp_path / "p.json"
        store = ProfileStore(path)
        alive = ProfileEntry.new("习惯凌晨两点睡", "schedule", 0.9)
        dead = ProfileEntry.new("以前用 DeepSeek", "tool_usage", 0.6)
        store.upsert(alive)
        store.upsert(dead)
        store.invalidate(dead.id, superseded_by=alive.id)

        ui = self._run("/memory", path)
        joined = "\n".join(ui.messages)
        assert "习惯凌晨两点睡" in joined
        assert "以前用 DeepSeek" not in joined
        assert "另有 1 条已被新事实取代" in joined

    def test_list_shows_reference_count(self, tmp_path):
        """续命接线后：被再次确认过的事实在列表里显示“提及 N 次”。"""
        path = tmp_path / "p.json"
        store = ProfileStore(path)
        e = ProfileEntry.new("习惯凌晨两点睡", "schedule", 0.9)
        store.upsert(e)
        store.touch_reference(e.id, now=5000.0)

        ui = self._run("/memory", path)
        joined = "\n".join(ui.messages)
        assert "提及 1 次" in joined


# ─────────────────────────────────────────────────────────────
# M3: 衰减维护
# ─────────────────────────────────────────────────────────────

class TestFreshnessWeight:
    """衰减改为降权：weight = confidence × 新鲜度；不删条目、不改 confidence。"""

    def test_weight_decays_but_data_untouched(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("过时的习惯", "work_habit", 0.8)
        e.created_at = e.updated_at = 1000.0
        store.upsert(e)

        # 120 天 = 4 个半衰期 → 0.8 × 0.0625 = 0.05 < 0.10 → 休眠
        now = 1000.0 + 120 * 86400
        assert store.audit_freshness(now=now) == 1
        weight, entry = store.weighted_entries(now=now)[0]
        assert weight == pytest.approx(0.05, abs=0.01)
        assert entry.confidence == pytest.approx(0.8)   # 数据没被改写
        assert len(store) == 1                          # 没被删除

    def test_recent_entry_weight_lower_but_active(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("近期习惯", "work_habit", 0.8)
        e.created_at = e.updated_at = 1000.0
        store.upsert(e)

        now = 1000.0 + 30 * 86400                       # 1 个半衰期
        assert store.audit_freshness(now=now) == 0
        weight, _ = store.weighted_entries(now=now)[0]
        assert weight == pytest.approx(0.4, abs=0.01)

    def test_manual_and_permanent_never_lose_weight(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        manual = ProfileEntry.new("用户亲写", "other", 0.99, source_session="manual")
        manual.created_at = manual.updated_at = 1000.0
        identity = ProfileEntry.new("姓名是艾斯", "identity", 0.9)
        identity.created_at = identity.updated_at = 1000.0
        store.upsert(manual)
        store.upsert(identity)

        now = 1000.0 + 365 * 86400
        weights = {e.content: w for w, e in store.weighted_entries(now=now)}
        assert weights["用户亲写"] == pytest.approx(0.99)
        assert weights["姓名是艾斯"] == pytest.approx(0.9)
        assert store.audit_freshness(now=now) == 0

    def test_injection_skips_dormant(self, tmp_path):
        """休眠条目不注入，但仍留在库里。"""
        store = ProfileStore(tmp_path / "p.json")
        old = ProfileEntry.new("很久前提过的习惯", "work_habit", 0.5)
        old.created_at = old.updated_at = time.time() - 400 * 86400
        fresh = ProfileEntry.new("刚说的偏好", "preference", 0.5)
        store.upsert(old)
        store.upsert(fresh)

        text = store.render_for_prompt()
        assert "刚说的偏好" in text
        assert "很久前提过的习惯" not in text
        assert len(store) == 2

    def test_prune_drops_lowest_weight_first(self, tmp_path):
        """容量急时才删：先淘汰权重最低（老且不高置信）的。"""
        store = ProfileStore(tmp_path / "p.json")
        stale = ProfileEntry.new("老的高置信", "other", 0.9)
        stale.created_at = stale.updated_at = time.time() - 120 * 86400
        fresh = ProfileEntry.new("新的低置信", "other", 0.4)
        store.upsert(stale)
        store.upsert(fresh)

        assert store.prune_over_limit(1) == 1
        assert [e.content for e in store.entries()] == ["新的低置信"]

    def test_audit_never_deletes(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("一年没提的习惯", "work_habit", 0.6)
        e.created_at = e.updated_at = 1000.0
        store.upsert(e)

        assert store.audit_freshness(now=1000.0 + 365 * 86400) == 1
        assert len(store) == 1
        assert store.entries()[0].confidence == pytest.approx(0.6)


class TestLifecycle:
    """P2a：生命周期分层（permanent 不降权 / stateful 半衰降权 / episodic 到期出清）。"""

    def test_default_lifecycle_by_category(self):
        from agent.core.memory.profile_store import default_lifecycle_for_category

        for cat in ("identity", "relationship", "aspiration"):
            assert default_lifecycle_for_category(cat) == "permanent"
        for cat in ("preference", "schedule", "work_habit", "project", "other"):
            assert default_lifecycle_for_category(cat) == "stateful"

    def test_new_derives_and_validates_lifecycle(self):
        assert ProfileEntry.new("姓名是艾斯", "identity").lifecycle == "permanent"
        assert ProfileEntry.new("喜欢猫", "preference").lifecycle == "stateful"
        # 显式非法值 → stateful，不抛
        assert ProfileEntry.new("喜欢猫", "preference", lifecycle="bogus").lifecycle == "stateful"
        assert ProfileEntry.new("临时事项", "other", lifecycle="episodic").lifecycle == "episodic"

    def test_legacy_file_without_lifecycle_derives(self, tmp_path):
        """version<=2 的老库没有 lifecycle 字段：按类别派生，不写盘迁移。"""
        path = tmp_path / "p.json"
        path.write_text(
            json.dumps({
                "version": 2,
                "entries": [
                    {"id": "ent_a", "category": "identity", "content": "姓名是艾斯"},
                    {"id": "ent_b", "category": "preference", "content": "喜欢猫"},
                ],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        by_id = {e.id: e for e in ProfileStore(path).entries()}
        assert by_id["ent_a"].lifecycle == "permanent"
        assert by_id["ent_b"].lifecycle == "stateful"

    def test_permanent_never_loses_weight(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("姓名是艾斯", "identity", 0.9)
        e.created_at = e.updated_at = 1000.0
        store.upsert(e)

        # 一年后：permanent 权重不变（stateful 同条件已休眠）
        now = 1000.0 + 365 * 86400
        assert store.audit_freshness(now=now) == 0
        assert store.weighted_entries(now=now)[0][0] == pytest.approx(0.9)

    def test_stateful_weight_decays_by_half_life(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("喜欢复古像素风", "preference", 0.8)
        e.created_at = e.updated_at = 1000.0
        store.upsert(e)

        now = 1000.0 + 30 * 86400
        assert store.audit_freshness(now=now) == 0
        assert store.weighted_entries(now=now)[0][0] == pytest.approx(0.4, abs=0.01)

    def test_episodic_expires_at(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        expired = ProfileEntry.new("明天面试", "other", 0.9, lifecycle="episodic", expire_at=2000.0)
        expired.created_at = expired.updated_at = 1000.0
        alive = ProfileEntry.new("下周体检", "other", 0.9, lifecycle="episodic", expire_at=9999.0)
        alive.created_at = alive.updated_at = 1000.0
        store.upsert(expired)
        store.upsert(alive)

        # 到期出清是显式语义（不是衰减），仍物理删除
        assert store.expire_episodic(now=3000.0) == 1
        left = store.entries()
        assert len(left) == 1 and left[0].content == "下周体检"

    def test_episodic_without_expire_just_loses_weight(self, tmp_path):
        """没给到期时间就不是“临时事项”：只降权，不删除。"""
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("临时任务", "other", 0.8, lifecycle="episodic")
        e.created_at = e.updated_at = 1000.0
        store.upsert(e)

        now = 1000.0 + 30 * 86400
        assert store.audit_freshness(now=now) == 0
        assert store.weighted_entries(now=now)[0][0] == pytest.approx(0.4, abs=0.01)
        assert len(store) == 1

    def test_merge_keeps_more_stable_lifecycle(self, tmp_path):
        """permanent 不能被同事实的 stateful 再报降级；expire_at 取大。"""
        store = ProfileStore(tmp_path / "p.json")
        base = ProfileEntry.new("姓名是艾斯", "identity", 0.8, lifecycle="permanent")
        store.upsert(base)

        incoming = ProfileEntry.new(
            "姓名是艾斯。", "identity", 0.9, lifecycle="stateful", expire_at=1234.0
        )
        kept, merged = store.merge_or_add(incoming)
        assert merged is True
        assert kept.lifecycle == "permanent"
        assert kept.expire_at == pytest.approx(1234.0)

    def test_dedup_keeps_most_stable_lifecycle(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9, lifecycle="stateful"))
        store.upsert(ProfileEntry.new("习惯深夜工作。", "schedule", 0.5, lifecycle="permanent"))
        assert store.dedup_all() == 1
        assert store.entries()[0].lifecycle == "permanent"


class TestSoftDelete:
    """P2b：失效条目不再注入/召回/展示，但保留在磁盘上可追溯。"""

    def test_invalidate_hides_from_active_views(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("在做一个项目", "project", 0.9)
        store.upsert(e)

        assert store.invalidate(e.id, superseded_by="ent_new", now=100.0) is True
        assert store.entries() == []
        assert len(store.entries(include_invalidated=True)) == 1
        assert store.render_for_prompt() == ""
        assert store.get(e.id) is None
        history = store.get(e.id, include_invalidated=True)
        assert history is not None
        assert history.superseded_by == "ent_new"
        assert history.invalidated_at == pytest.approx(100.0)

    def test_invalidate_is_idempotent(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("在做一个项目", "project", 0.9)
        store.upsert(e)
        assert store.invalidate(e.id, now=100.0) is True
        assert store.invalidate(e.id, now=200.0) is False
        assert store.get(e.id, include_invalidated=True).invalidated_at == pytest.approx(100.0)

    def test_history_fields_persisted(self, tmp_path):
        path = tmp_path / "p.json"
        store = ProfileStore(path)
        e = ProfileEntry.new("以前用 DeepSeek", "tool_usage", 0.6)
        store.upsert(e)
        store.invalidate(e.id, superseded_by="ent_next")

        data = json.loads(path.read_text(encoding="utf-8"))
        row = next(r for r in data["entries"] if r["id"] == e.id)
        assert row["invalidated_at"] > 0
        assert row["superseded_by"] == "ent_next"

    def test_recall_similar_skips_invalidated(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("习惯深夜工作", "schedule", 0.9, aliases=["熬夜"])
        store.upsert(e)
        store.invalidate(e.id)
        assert store.recall_similar("习惯深夜工作") == []

    def test_merge_does_not_resurrect_invalidated(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("在做一个项目", "project", 0.9)
        store.upsert(e)
        store.invalidate(e.id, superseded_by="ent_new")

        kept, merged = store.merge_or_add(ProfileEntry.new("在做一个项目", "project", 0.9))
        assert merged is False and kept.id != e.id
        assert len(store.entries()) == 1
        assert len(store.entries(include_invalidated=True)) == 2

    def test_audit_skips_invalidated(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("早就过时的", "other", 0.9)
        e.created_at = e.updated_at = 1000.0
        store.upsert(e)
        store.invalidate(e.id, now=1000.0)

        # 失效条目权重为 0、不计休眠；历史仍在
        assert store.audit_freshness(now=1000.0 + 365 * 86400) == 0
        assert store.weighted_entries(now=1000.0 + 365 * 86400) == []
        assert len(store.entries(include_invalidated=True)) == 1

    def test_prune_drops_invalidated_first(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        alive = ProfileEntry.new("有效事实", "other", 0.5)
        dead = ProfileEntry.new("失效事实", "other", 0.99)
        store.upsert(alive)
        store.upsert(dead)
        store.invalidate(dead.id)

        assert store.prune_over_limit(1) == 1
        assert [e.content for e in store.entries()] == ["有效事实"]

    def test_dedup_preserves_invalidated_history(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9))
        store.upsert(ProfileEntry.new("习惯深夜工作。", "schedule", 0.5))
        dead = ProfileEntry.new("习惯深夜工作", "schedule", 0.8)
        store.upsert(dead)
        store.invalidate(dead.id)

        assert store.dedup_all() == 1          # 两条有效重复归并
        assert len(store.entries()) == 1
        assert len(store.entries(include_invalidated=True)) == 2  # 历史保留


class TestReferenceWiring:
    """续命接线：被再次提及/确认要真正刷新衰减依据（2026-11-15 修复）。"""

    def test_touch_reference_updates_fields(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("习惯深夜工作", "schedule", 0.9)
        store.upsert(e)
        assert e.ref_count == 0 and e.last_referenced_at == 0.0

        assert store.touch_reference(e.id, now=5000.0) is True
        touched = store.entries()[0]
        assert touched.ref_count == 1
        assert touched.last_referenced_at == pytest.approx(5000.0)
        assert touched.confidence == pytest.approx(0.9)   # 只续命，不改置信

    def test_touch_reference_skips_missing_and_invalidated(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("在做一个项目", "project", 0.9)
        store.upsert(e)
        store.invalidate(e.id)

        assert store.touch_reference("ent_ghost") is False
        assert store.touch_reference(e.id) is False

    def test_merge_counts_as_confirmation(self, tmp_path):
        store = ProfileStore(tmp_path / "p.json")
        first, _ = store.merge_or_add(ProfileEntry.new("习惯深夜工作", "schedule", 0.6))
        assert first.ref_count == 0

        kept, merged = store.merge_or_add(ProfileEntry.new("习惯深夜工作。", "schedule", 0.9))
        assert merged is True
        assert kept.ref_count == 1
        assert kept.last_referenced_at > 0
        assert kept.confidence == pytest.approx(0.9)

    def test_weight_basis_uses_last_reference(self, tmp_path):
        """老条目但最近被提及过 → 按“最近提及”算新鲜度，不该被打入休眠。"""
        store = ProfileStore(tmp_path / "p.json")
        e = ProfileEntry.new("喜欢复古像素风", "preference", 0.8)
        e.created_at = e.updated_at = 1000.0        # 两年前创建
        store.upsert(e)
        store.touch_reference(e.id, now=1000.0 + 700 * 86400)  # 最近刚被提及

        now = 1000.0 + 730 * 86400                   # 距最近提及 30 天 = 一个半衰期
        assert store.audit_freshness(now=now) == 0
        assert store.weighted_entries(now=now)[0][0] == pytest.approx(0.4, abs=0.01)


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


class TestProfileBoundary:
    """画像只记“人”，不记“事/系统”（2026-11-15 用户校正）。"""

    def test_prompt_has_person_only_boundaries(self):
        prompt = refiner._build_prompt("用户: 我在改端口", [])
        # 正面：性格/愿望/心思必须入类别表
        for cat in ("personality", "aspiration", "mindset"):
            assert cat in prompt
        # 反面：排除项与判据必须在 prompt 里
        assert "画像不是什么" in prompt
        assert "端口" in prompt and "路径" in prompt
        assert "MEMORY.md" in prompt
        assert "5 年后还成立吗" in prompt

    def test_new_categories_accepted(self):
        for cat in ("personality", "aspiration", "mindset"):
            e = ProfileEntry.new("x", cat, 0.8)
            assert e.category == cat

    def test_tech_detail_prompt_lists_exclusions(self):
        """技术实现与临时事件必须出现在排除清单中。"""
        prompt = refiner._build_prompt("用户: x", [])
        for kw in ("技术实现细节", "一次性事件", "环境/网络状态"):
            assert kw in prompt


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
        assert refiner.maybe_refine_async(msgs, "s", s) is False


# ─────────────────────────────────────────────────────────────
# 深重复去重（2026-11 修复：完全相同的记忆被反复提炼入库）
# ─────────────────────────────────────────────────────────────

class TestContentDedup:
    """归一化与相似度判据。"""

    def test_normalize_fullwidth_space_and_punct(self):
        from agent.core.memory.profile_store import normalize_profile_content
        # 全角转半角、去空白、剥句尾标点
        assert normalize_profile_content("Ｈｅｌｌｏ Ｗｏｒｌｄ。") == "helloworld"
        assert normalize_profile_content("习惯深夜工作 ") == "习惯深夜工作"

    def test_similarity_identical_is_one(self):
        from agent.core.memory.profile_store import profile_content_similarity
        assert profile_content_similarity("习惯深夜工作", "习惯深夜工作") == 1.0

    def test_similarity_near_duplicate_high(self):
        from agent.core.memory.profile_store import profile_content_similarity
        # 仅尾字差异，应判为深重复（≥0.85）
        assert profile_content_similarity("习惯深夜工作", "习惯深夜工作。") >= 0.85

    def test_similarity_distinct_low(self):
        from agent.core.memory.profile_store import profile_content_similarity
        # 措辞差异的语义近条不应被自动合并（交个提炼 LLM）
        s = profile_content_similarity(
            "职业是Agent开发者", "职业是Agent应用开发工程师")
        assert s < 0.85


class TestMergeOrAdd:
    def test_exact_duplicate_merges_not_adds(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        first = ProfileEntry.new("习惯深夜工作", "schedule", 0.9, "s1")
        kept, merged = store.merge_or_add(first)
        assert merged is False and len(store) == 1
        # 同一事实再次以高置信写入 → 合并，置信取大，不新增
        again = ProfileEntry.new("习惯深夜工作", "schedule", 0.6, "s2")
        kept2, merged2 = store.merge_or_add(again)
        assert merged2 is True
        assert len(store) == 1
        assert kept2.id == first.id
        assert kept2.confidence == pytest.approx(0.9)  # 取较大值，不被低置信拉低

    def test_source_session_manual_preserved_on_merge(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        manual = ProfileEntry.new("用火狐浏览器", "tool_usage", 0.99, "manual")
        store.merge_or_add(manual)
        _, merged = store.merge_or_add(
            ProfileEntry.new("用火狐浏览器", "tool_usage", 0.7, "refine"))
        assert merged is True
        assert store.entries()[0].source_session == "manual"

    def test_distinct_entries_not_merged(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        store.merge_or_add(ProfileEntry.new("职业是Agent开发者", "identity", 0.9))
        _, merged = store.merge_or_add(
            ProfileEntry.new("职业是Agent应用开发工程师", "identity", 0.9))
        assert merged is False
        assert len(store) == 2


class TestDedupAll:
    def test_retroactive_cleanup_merges_existing_dups(self, tmp_path):
        # 模拟修复前写入的重复库：同一句记了三遍
        store = ProfileStore(tmp_path / "profile.json")
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9, "s1"))
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9, "s2"))
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9, "s3"))
        assert len(store) == 3
        merged = store.dedup_all()
        assert merged == 2
        assert len(store) == 1
        # 幂等：再次归并无新增可合并
        assert store.dedup_all() == 0

    def test_duplicate_group_report_preview_no_write(self, tmp_path):
        path = tmp_path / "profile.json"
        store = ProfileStore(path)
        store.upsert(ProfileEntry.new("用火狐浏览器", "tool_usage", 0.85, "s1"))
        store.upsert(ProfileEntry.new("用火狐浏览器", "tool_usage", 0.85, "s2"))
        store.upsert(ProfileEntry.new("用WPS处理文档", "tool_usage", 0.6, "s3"))
        groups = store.duplicate_group_report()
        assert len(groups) == 1 and len(groups[0]) == 2
        # 预览不写盘：盘上仍是 3 条
        assert len(ProfileStore(path)) == 3


class TestRefineDedupIntegration:
    def test_rereported_existing_fact_merges(self, tmp_path, monkeypatch):
        """核心场景：LLM 把旧事实当新事实再报一次，不得产生重复条目。"""
        store = ProfileStore(tmp_path / "profile.json")
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9, "s_old"))

        # 裁决段仍报 add（模型没看清），存储层查重闸必须合并而不是新增
        extract = json.dumps({
            "candidates": [{"content": "习惯深夜工作", "category": "schedule", "confidence": 0.9}]
        }, ensure_ascii=False)
        resolve = json.dumps({
            "facts": [{"op": "add", "content": "习惯深夜工作",
                       "category": "schedule", "confidence": 0.9}]
        }, ensure_ascii=False)
        monkeypatch.setattr(refiner, "_build_refine_provider",
                            lambda s: _FakeProvider([extract, resolve]))
        report = refiner.refine_session(
            [_msg("user", "我经常熬夜")], "s_new", Settings(profile_enabled=True),
            store=store)
        assert report.error == ""
        assert report.added == 0     # 未当新增
        assert report.merged >= 1    # 被查重闸合并
        assert len(store) == 1


class TestMemoryDedupCommand:
    def _ctx(self, ui, settings):
        from types import SimpleNamespace
        return SimpleNamespace(ui=ui, settings=settings)

    def _run(self, cmd, store_path, seed=None):
        import asyncio
        from agent.commands.handlers.session_commands import handle_memory
        from agent.core.memory import profile_store as ps_mod
        settings = Settings(profile_enabled=True)
        monkey_store = ProfileStore(store_path)
        for c in (seed or []):
            monkey_store.upsert(ProfileEntry.new(c, "schedule", 0.9, "s"))
        orig = ps_mod.ProfileStore
        ps_mod.ProfileStore = lambda: monkey_store
        try:
            ui = _FakeUI()
            asyncio.run(handle_memory(self._ctx(ui, settings), cmd))
            return ui, monkey_store
        finally:
            ps_mod.ProfileStore = orig

    def test_dedup_preview_lists_without_yes(self, tmp_path):
        path = tmp_path / "p.json"
        ui, store = self._run(
            "/memory dedup", path,
            seed=["习惯深夜工作", "习惯深夜工作", "习惯深夜工作"])
        assert any("可归并 2 条" in m for m in ui.messages)
        assert len(store) == 3  # 预览未真删

    def test_dedup_yes_merges(self, tmp_path):
        path = tmp_path / "p.json"
        ui, store = self._run(
            "/memory dedup", path,
            seed=["习惯深夜工作", "习惯深夜工作", "习惯深夜工作"])
        ui2, store2 = self._run("/memory dedup yes", path, seed=None)
        # 第二次 _run 新建实例从盘读回上次预演（未改），这里直接对已有 3 条执行 yes
        assert any("已归并" in m for m in ui2.messages)
        assert len(store2) == 1

    def test_dedup_no_dups_hint(self, tmp_path):
        ui, _ = self._run("/memory dedup", tmp_path / "p.json",
                          seed=["习惯深夜工作", "用火狐浏览器"])
        assert any("没有可归并" in m for m in ui.messages)

    def test_add_duplicate_merges(self, tmp_path):
        path = tmp_path / "p.json"
        self._run("/memory add 习惯深夜工作", path)
        ui, store = self._run("/memory add 习惯深夜工作", path)
        assert any("已并入相似记忆" in m for m in ui.messages)
        assert len(store) == 1
