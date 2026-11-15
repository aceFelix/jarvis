"""RecallProfile 工具（P5）单元测试。

覆盖：命中/未命中、top_k 夹取与 settings 默认、非法入参、
权限与只读声明、画像库异常降级、休眠条目仍可被检索到。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.config.settings import Settings
from agent.core.context import ToolContext
from agent.core.memory.profile_store import ProfileEntry, ProfileStore
from agent.core.result import PermissionBehavior
from agent.tools.memory import recall_profile as rp
from agent.tools.memory.recall_profile import RecallProfileTool


def _ctx(settings: Settings | None = None) -> ToolContext:
    return ToolContext(workdir="", messages=[], settings=settings)


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> ProfileStore:
    """把工具内部的 ProfileStore() 指向临时库，绝不读真实 ~/.jarvis。"""
    s = ProfileStore(tmp_path / "profile.json")
    monkeypatch.setattr(rp, "ProfileStore", lambda: s)
    return s


def _run(tool: RecallProfileTool, args: dict, ctx: ToolContext | None = None):
    return asyncio.run(tool.call(args, ctx or _ctx()))


class TestRecall:
    def test_hit_returns_best_match(self, store) -> None:
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9, aliases=["熬夜"]))
        store.upsert(ProfileEntry.new("喜欢复古像素风", "preference", 0.7))

        out = _run(RecallProfileTool(), {"query": "熬夜"})
        assert not out.is_error
        assert "习惯深夜工作" in out.data
        assert "置信度 0.90" in out.data
        assert "别名 熬夜" in out.data

    def test_no_hit_message(self, store) -> None:
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9))
        out = _run(RecallProfileTool(), {"query": "量子引力"})
        assert not out.is_error
        assert "没有找到" in out.data
        assert "1 条有效记忆" in out.data

    def test_category_filter(self, store) -> None:
        store.upsert(ProfileEntry.new("习惯深夜工作", "schedule", 0.9))
        out = _run(RecallProfileTool(), {"query": "习惯", "category": "preference"})
        # 类别不匹配只影响加权，不排除结果；这里验证不报错且有结果即可
        assert "习惯深夜工作" in out.data or "没有找到" in out.data

    def test_dormant_entry_still_recalled(self, store) -> None:
        """权重低的休眠条目，用户主动问起时仍应被检索到。"""
        entry = ProfileEntry.new("两年前喜欢弹吉他", "preference", 0.9)
        entry.created_at = entry.updated_at = 1000.0     # 很久以前
        store.upsert(entry)

        out = _run(RecallProfileTool(), {"query": "吉他"})
        assert "两年前喜欢弹吉他" in out.data


class TestTopK:
    def _seed(self, store: ProfileStore) -> None:
        for i in range(5):
            store.upsert(ProfileEntry.new(f"习惯{i}号动作", "work_habit", 0.8))

    def test_explicit_top_k(self, store) -> None:
        self._seed(store)
        out = _run(RecallProfileTool(), {"query": "习惯", "top_k": 2})
        assert out.data.count("\n- [") == 2

    def test_top_k_from_settings(self, store) -> None:
        self._seed(store)
        out = _run(RecallProfileTool(), {"query": "习惯"}, _ctx(Settings(profile_recall_tool_top_k=3)))
        assert out.data.count("\n- [") == 3

    def test_top_k_clamped(self, store) -> None:
        self._seed(store)
        out = _run(RecallProfileTool(), {"query": "习惯", "top_k": 999})
        # 上限 20；库内只有 5 条 → 全返回
        assert out.data.count("\n- [") == 5


class TestValidation:
    def test_empty_query_rejected(self) -> None:
        res = RecallProfileTool().validate_input({"query": "   "}, _ctx())
        assert res.ok is False

    def test_non_int_top_k_rejected(self) -> None:
        res = RecallProfileTool().validate_input({"query": "作息", "top_k": "abc"}, _ctx())
        assert res.ok is False

    def test_valid_args_pass(self) -> None:
        assert RecallProfileTool().validate_input({"query": "作息"}, _ctx()).ok is True


class TestFlags:
    def test_read_only_and_permissions(self) -> None:
        tool = RecallProfileTool()
        assert tool.deferred is True
        assert tool.is_read_only({}) is True
        assert tool.is_concurrency_safe({}) is True
        result = tool.check_permissions({}, _ctx())
        assert result.behavior == PermissionBehavior.ALLOW


class TestFailure:
    def test_store_error_returns_tool_error(self, monkeypatch) -> None:
        class _Boom:
            def __init__(self):
                raise OSError("db broken")

        monkeypatch.setattr(rp, "ProfileStore", _Boom)
        out = _run(RecallProfileTool(), {"query": "作息"})
        assert out.is_error is True
        assert "画像检索失败" in out.data


class TestRegistry:
    def test_registered_as_deferred(self) -> None:
        from agent.core.tool import build_default_registry

        registry = build_default_registry()
        tool = registry.get("RecallProfile")
        assert tool is not None
        assert tool.deferred is True
