"""画像提炼两段式裁决（profile_resolve）单元测试。

覆盖：候选硬闸、四操作解析与分派、幻觉 target 降级、逐条召回、
别名规则（生成开关 / 并集 / 版本兼容）、recall_similar 打分。

@author aceFelix
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core.memory import profile_resolve as PR
from agent.core.memory import profile_store as PS
from agent.core.memory.profile_store import ProfileEntry, ProfileStore


def _store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(tmp_path / "profile.json")


def _report() -> SimpleNamespace:
    return SimpleNamespace(
        added=0, updated=0, merged=0, deleted=0, noop=0, extracted=0, rejected=0
    )


class TestValidateCandidate:
    def test_valid(self) -> None:
        c = PR.validate_candidate({"content": " 习惯深夜工作 ", "category": "schedule",
                                   "confidence": 0.9})
        assert c is not None
        assert c.content == "习惯深夜工作"
        assert c.category == "schedule"
        assert c.confidence == pytest.approx(0.9)

    def test_empty_rejected(self) -> None:
        assert PR.validate_candidate({"content": "  "}) is None
        assert PR.validate_candidate({}) is None
        assert PR.validate_candidate("不是 dict") is None  # type: ignore[arg-type]

    def test_over_40_chars_rejected(self) -> None:
        assert PR.validate_candidate({"content": "字" * 41}) is None
        assert PR.validate_candidate({"content": "字" * 40}) is not None

    def test_invalid_category_falls_back(self) -> None:
        c = PR.validate_candidate({"content": "x", "category": "不存在的类别"})
        assert c is not None and c.category == "other"

    def test_confidence_clamped(self) -> None:
        low = PR.validate_candidate({"content": "x", "confidence": 0.01})
        high = PR.validate_candidate({"content": "y", "confidence": 5})
        assert low is not None and low.confidence == pytest.approx(0.3)
        assert high is not None and high.confidence == pytest.approx(0.95)


class TestParseDecisions:
    def test_ops_and_aliases(self) -> None:
        data = {"facts": [
            {"op": "add", "content": "习惯深夜工作", "category": "schedule",
             "confidence": 0.9, "aliases": ["熬夜", "晚睡"]},
            {"op": "new", "content": "喜欢猫", "category": "preference"},   # 同义词归一
            {"op": "update", "target_id": "ent_a", "content": "已改用 GLM"},
            {"op": "delete", "target_id": "ent_b", "reason": "已失效"},
            {"op": "noop", "target_id": "ent_c"},
        ]}
        decisions, rejected = PR.parse_decisions(data)
        assert rejected == 0
        assert [d.op for d in decisions] == ["add", "add", "update", "delete", "noop"]
        assert decisions[0].aliases == ["熬夜", "晚睡"]

    def test_unknown_op_rejected(self) -> None:
        decisions, rejected = PR.parse_decisions({"facts": [{"op": "爽文", "content": "x"}]})
        assert decisions == [] and rejected == 1

    def test_update_without_target_rejected(self) -> None:
        _, rejected = PR.parse_decisions({"facts": [{"op": "update", "content": "x"}]})
        assert rejected == 1

    def test_add_over_40_rejected(self) -> None:
        _, rejected = PR.parse_decisions({"facts": [{"op": "add", "content": "字" * 41}]})
        assert rejected == 1


class TestExtract:
    def test_single_call_without_existing_profile(self) -> None:
        calls: list[tuple[str, int]] = []

        def call(_p, _m, prompt, *, max_tokens=0):
            calls.append((prompt, max_tokens))
            return json.dumps({"candidates": [
                {"content": "习惯深夜工作", "category": "schedule", "confidence": 0.9},
                {"content": "字" * 41, "category": "other", "confidence": 0.5},
            ]}, ensure_ascii=False)

        cands, rejected = PR.extract_candidates(None, "m", "用户: 我常熬夜", call)
        assert len(cands) == 1 and rejected == 1
        assert len(calls) == 1 and calls[0][1] == 900
        # 抽取段刻意不带旧画像
        assert "[ent_" not in calls[0][0]
        assert "画像不是什么" in calls[0][0]


class TestResolvePrompt:
    def test_recalls_and_alias_rules(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        old = ProfileEntry.new("习惯深夜工作", "schedule", 0.9, "s0", aliases=["熬夜"])
        store.upsert(old)
        cand = PR.Candidate("习惯深夜工作", "schedule", 0.9)
        PR.attach_recalls([cand], store, 5)
        prompt = PR.build_resolve_prompt([cand])
        assert f"[{old.id}]" in prompt
        assert "aliases" in prompt and "禁止再 add" in prompt
        assert "noop" in prompt

    def test_alias_disabled(self) -> None:
        cand = PR.Candidate("x", "other", 0.5)
        prompt = PR.build_resolve_prompt([cand], include_aliases=False)
        assert "不要输出 aliases" in prompt


class TestApply:
    def test_add_update_delete_noop(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        upd = ProfileEntry.new("以前用 DeepSeek", "tool_usage", 0.6)
        dele = ProfileEntry.new("需要早上9点被叫醒", "schedule", 0.8)
        store.upsert(upd)
        store.upsert(dele)
        report = _report()
        decisions = [
            PR.Decision("add", "习惯深夜工作", "schedule", 0.9, ["熬夜"]),
            PR.Decision("update", "已改用 GLM", "tool_usage", 0.95, ["GLM"], target_id=upd.id),
            PR.Decision("delete", target_id=dele.id),
            PR.Decision("noop", target_id=upd.id),
        ]
        PR.apply_decisions(decisions, store, "s1", report)

        contents = [e.content for e in store.entries()]
        assert "习惯深夜工作" in contents
        assert "已改用 GLM" in contents
        assert "以前用 DeepSeek" not in contents
        assert "需要早上9点被叫醒" not in contents
        assert report.added == 1 and report.updated == 1
        assert report.deleted == 1 and report.noop == 1

    def test_hallucinated_update_degrades_to_add(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        report = _report()
        PR.apply_decisions(
            [PR.Decision("update", "喜欢猫", "preference", 0.8, target_id="ent_ghost")],
            store, "s1", report,
        )
        assert [e.content for e in store.entries()] == ["喜欢猫"]
        assert report.added == 1 and report.updated == 0


class TestSoftDeleteOnUpdate:
    """P2b：update 不物理删旧值，打失效标记并指向新条目。"""

    def test_update_invalidates_target_with_pointer(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        old = ProfileEntry.new("职业是后端工程师", "identity", 0.8)
        store.upsert(old)
        report = _report()
        PR.apply_decisions(
            [PR.Decision("update", "职业是Agent开发者", "identity", 0.9, target_id=old.id)],
            store, "s1", report,
        )
        active = store.entries()
        assert [e.content for e in active] == ["职业是Agent开发者"]
        history = store.get(old.id, include_invalidated=True)
        assert history is not None and history.invalidated_at > 0
        assert history.superseded_by == active[0].id
        assert report.updated == 1

    def test_update_same_wording_keeps_target_active(self, tmp_path: Path) -> None:
        """update 只是同义重述 → 合并回目标本身，不能把目标标成失效。"""
        store = _store(tmp_path)
        old = ProfileEntry.new("习惯深夜工作", "schedule", 0.8)
        store.upsert(old)
        PR.apply_decisions(
            [PR.Decision("update", "习惯深夜工作。", "schedule", 0.9, target_id=old.id)],
            store, "s1", _report(),
        )
        assert len(store.entries()) == 1
        assert store.get(old.id).invalidated_at == 0

    def test_update_on_invalidated_target_degrades_to_add(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        old = ProfileEntry.new("以前用 DeepSeek", "tool_usage", 0.6)
        store.upsert(old)
        store.invalidate(old.id, superseded_by="ent_new")
        report = _report()
        PR.apply_decisions(
            [PR.Decision("update", "已改用 GLM", "tool_usage", 0.9, target_id=old.id)],
            store, "s1", report,
        )
        assert report.added == 1
        assert [e.content for e in store.entries()] == ["已改用 GLM"]


class TestRecallSimilar:
    def test_alias_hit_ranks_first(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.upsert(ProfileEntry.new("用火狐浏览器", "tool_usage", 0.9, aliases=["浏览器"]))
        store.upsert(ProfileEntry.new("习惯凌晨两点睡", "schedule", 0.9, aliases=["熬夜", "晚睡"]))
        hits = store.recall_similar("我一般熬夜到两点", top_k=2)
        assert hits and hits[0].content == "习惯凌晨两点睡"

    def test_category_bonus(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        # 内容相同、类别不同：同类别候选应拿到 +0.15 排在前面
        pref = ProfileEntry.new("养猫", "preference", 0.9)
        other = ProfileEntry.new("养猫", "other", 0.9)
        store.upsert(pref)
        store.upsert(other)
        hits = store.recall_similar("养猫", category="preference", top_k=1)
        assert hits and hits[0].category == "preference"

    def test_top_k_and_empty_query(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        for i in range(5):
            store.upsert(ProfileEntry.new(f"喜欢猫{i}", "preference", 0.9))
        assert len(store.recall_similar("喜欢猫", top_k=2)) == 2
        assert store.recall_similar("", top_k=5) == []


class TestAliasStorage:
    def test_merge_union_capped(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        first = ProfileEntry.new("习惯深夜工作", "schedule", 0.9, aliases=["熬夜", "晚睡"])
        store.merge_or_add(first)
        again = ProfileEntry.new("习惯深夜工作", "schedule", 0.95, aliases=["晚睡", "作息晚", "凌晨"])
        kept, merged = store.merge_or_add(again)
        assert merged is True
        assert kept.aliases[:2] == ["熬夜", "晚睡"]
        assert "作息晚" in kept.aliases and "凌晨" in kept.aliases
        assert len(kept.aliases) <= 8

    def test_v1_file_compatible(self, tmp_path: Path) -> None:
        path = tmp_path / "profile.json"
        path.write_text(json.dumps({
            "version": 1,
            "entries": [{"id": "ent_old", "category": "schedule", "content": "习惯深夜工作",
                         "confidence": 0.9}],
        }, ensure_ascii=False), encoding="utf-8")
        store = ProfileStore(path)
        entries = store.entries()
        assert entries[0].aliases == []
        # 写回时升到 version 3（P2a 加 lifecycle/expire_at）
        store.upsert(ProfileEntry.new("喜欢猫", "preference", 0.8))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 3
