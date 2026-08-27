"""知识图谱画像桥（profile_bridge）单元测试。

覆盖 agent/core/extensions/profile_bridge.py:
- render_profile_json: get_profile JSON 渲染（正常/解析失败/空画像/token 限额裁剪）
- preload_kg_profile: 开关关闭/无客户端/成功拉取/调用异常 四路径
- build_sync_text: 画像条目汇总文本（含空画像）
- sync_to_kg: 成功/管线报错/本地画像为空
- settings 解析: [profile_bridge] 表字段映射

MCP 调用全部用假客户端模拟，不启动真实子进程、不依赖网络。

运行: pytest tests/core/test_profile_bridge.py

@author aceFelix
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from agent.core.extensions import profile_bridge as pb
from agent.core.memory.profile_store import ProfileEntry, ProfileStore


# =====================================================================
# 测试替身
# =====================================================================

class FakeMcpClient:
    """假 MCP 客户端：记录调用并按预设返回。"""

    def __init__(self, response: str = "{}", raise_exc: Exception | None = None):
        self.available = True
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[tuple] = []

    async def call_tool(self, server_name: str, tool_name: str, args: dict) -> str:
        self.calls.append((server_name, tool_name, args))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _profile_json() -> str:
    """模拟 acefelix get_profile 的真实返回结构。"""
    return json.dumps({
        "person": {"id": "ent_1", "name": "aceFelix", "properties": {"职业": "AI 工程师"}},
        "connections": [
            {"relation": "HAS_SKILL", "direction": "->", "entity": "Python"},
            {"relation": "MENTORED_BY", "direction": "<-", "entity": "老王"},
        ],
        "hint": "...",
    }, ensure_ascii=False)


def _settings(**overrides) -> NS:
    """带默认值的假 settings。"""
    base = dict(
        profile_bridge_enabled=True,
        profile_bridge_server="acefelix-knowledge",
        profile_bridge_token_limit=400,
    )
    base.update(overrides)
    return NS(**base)


@pytest.fixture(autouse=True)
def _reset_cache():
    """每个用例前后清空进程内画像缓存，避免用例间串扰。"""
    pb.reload_kg_profile_cache()
    yield
    pb.reload_kg_profile_cache()


# =====================================================================
# render_profile_json
# =====================================================================

class TestRenderProfileJson:
    def test_normal_render(self):
        section = pb.render_profile_json(_profile_json(), token_limit=500)
        assert section.startswith("# 关于用户（知识图谱画像）")
        assert "- 姓名: aceFelix" in section
        assert "- 职业: AI 工程师" in section
        assert "- HAS_SKILL: Python" in section
        # 反向关系带"（被）"前缀
        assert "（被）MENTORED_BY: 老王" in section
        # 冲突规则声明：图谱为唯一事实源
        assert "唯一事实源" in section

    def test_invalid_json_returns_empty(self):
        assert pb.render_profile_json("not json", token_limit=400) == ""

    def test_no_person_returns_empty(self):
        raw = json.dumps({"person": None, "note": "暂无"})
        assert pb.render_profile_json(raw, token_limit=400) == ""

    def test_token_limit_truncates(self):
        # 限额极小：只剩姓名一行，无实质内容 → 不值得注入
        section = pb.render_profile_json(_profile_json(), token_limit=1)
        assert section == ""

    def test_only_name_not_injected(self):
        raw = json.dumps({"person": {"name": "X"}, "connections": []}, ensure_ascii=False)
        assert pb.render_profile_json(raw, token_limit=500) == ""

    def test_url_properties_filtered(self):
        """头像等 URL 属性不注入（对 LLM 无信息量）"""
        raw = json.dumps({
            "person": {"name": "X", "properties": {
                "avatar": "http://127.0.0.1:8800/uploads/a.jpg",
                "role": "developer",
            }},
            "connections": [{"relation": "HAS_SKILL", "direction": "->", "entity": "Python"}],
        }, ensure_ascii=False)
        section = pb.render_profile_json(raw, token_limit=500)
        assert "avatar" not in section
        assert "- role: developer" in section


# =====================================================================
# preload_kg_profile（图谱 → jarvis 注入链路）
# =====================================================================

class TestPreloadKgProfile:
    @pytest.mark.asyncio
    async def test_disabled_returns_false(self):
        client = FakeMcpClient(_profile_json())
        ok = await pb.preload_kg_profile(client, _settings(profile_bridge_enabled=False))
        assert ok is False
        assert client.calls == []  # 未开启不发任何 MCP 调用

    @pytest.mark.asyncio
    async def test_no_client_returns_false(self):
        assert await pb.preload_kg_profile(None, _settings()) is False

    @pytest.mark.asyncio
    async def test_success_caches_section(self):
        client = FakeMcpClient(_profile_json())
        ok = await pb.preload_kg_profile(client, _settings())
        assert ok is True
        assert pb.kg_profile_section().startswith("# 关于用户（知识图谱画像）")
        # 调用了正确 server 的 get_profile
        assert client.calls[0][0] == "acefelix-knowledge"
        assert client.calls[0][1] == "get_profile"

    @pytest.mark.asyncio
    async def test_call_error_degrades_silently(self):
        client = FakeMcpClient(raise_exc=KeyError("server 未连接"))
        ok = await pb.preload_kg_profile(client, _settings())
        assert ok is False
        assert pb.kg_profile_section() == ""

    @pytest.mark.asyncio
    async def test_custom_server_name(self):
        client = FakeMcpClient(_profile_json())
        await pb.preload_kg_profile(client, _settings(profile_bridge_server="my-kg"))
        assert client.calls[0][0] == "my-kg"


# =====================================================================
# build_sync_text / sync_to_kg（jarvis → 图谱回写链路）
# =====================================================================

class TestSyncToKg:
    def _store(self, tmp_path: Path) -> ProfileStore:
        store = ProfileStore(path=tmp_path / "profile.json")
        store.upsert(ProfileEntry.new("习惯深夜写代码", "work_habit", 0.9))
        store.upsert(ProfileEntry.new("正在做知识图谱项目", "project", 0.8))
        return store

    def test_build_sync_text_lists_entries(self, tmp_path):
        text = pb.build_sync_text(self._store(tmp_path))
        assert "共 2 条" in text
        assert "习惯深夜写代码" in text
        assert "正在做知识图谱项目" in text

    def test_build_sync_text_empty_store(self, tmp_path):
        store = ProfileStore(path=tmp_path / "empty.json")
        assert pb.build_sync_text(store) == ""

    @pytest.mark.asyncio
    async def test_sync_dry_run_passes_flag(self, tmp_path):
        pipeline_result = {"dry_run": True, "gate": "passed", "created_entities": [],
                           "created_relations": [], "version": 3}
        client = FakeMcpClient(json.dumps(pipeline_result, ensure_ascii=False))
        outcome = await pb.sync_to_kg(client, _settings(), dry_run=True,
                                      store=self._store(tmp_path))
        assert outcome["ok"] is True
        assert outcome["result"]["gate"] == "passed"
        # 工具名与参数正确，dry_run 透传
        _, tool, args = client.calls[0]
        assert tool == "ingest_text"
        assert args["dry_run"] is True
        assert args["source"] == "jarvis-profile"
        assert "习惯深夜写代码" in args["text"]

    @pytest.mark.asyncio
    async def test_sync_pipeline_error(self, tmp_path):
        client = FakeMcpClient(json.dumps({"error": "LLM 调用失败"}, ensure_ascii=False))
        outcome = await pb.sync_to_kg(client, _settings(), dry_run=False,
                                      store=self._store(tmp_path))
        assert outcome["ok"] is False
        assert "LLM 调用失败" in outcome["error"]

    @pytest.mark.asyncio
    async def test_sync_empty_profile(self, tmp_path):
        store = ProfileStore(path=tmp_path / "empty.json")
        client = FakeMcpClient("{}")
        outcome = await pb.sync_to_kg(client, _settings(), dry_run=True, store=store)
        assert outcome["ok"] is False
        assert client.calls == []  # 画像为空不调 MCP

    @pytest.mark.asyncio
    async def test_sync_client_exception_wrapped(self, tmp_path):
        client = FakeMcpClient(raise_exc=RuntimeError("boom"))
        outcome = await pb.sync_to_kg(client, _settings(), dry_run=True,
                                      store=self._store(tmp_path))
        assert outcome["ok"] is False
        assert "boom" in outcome["error"]


# =====================================================================
# settings 解析：[profile_bridge] 表
# =====================================================================

class TestSettingsParsing:
    def test_profile_bridge_table_parsed(self):
        """settings.toml 的 [profile_bridge] 段映射到对应字段。"""
        from agent.config.settings import Settings, _apply_toml

        s = _apply_toml(Settings(), {
            "profile_bridge": {"enabled": True, "server": "my-kg", "token_limit": 250},
        })
        assert s.profile_bridge_enabled is True
        assert s.profile_bridge_server == "my-kg"
        assert s.profile_bridge_token_limit == 250

    def test_profile_bridge_defaults_off(self):
        """未配置时默认关闭（不空连 MCP）。"""
        from agent.config.settings import Settings

        s = Settings()
        assert s.profile_bridge_enabled is False
        assert s.profile_bridge_server == "acefelix-knowledge"
        assert s.profile_bridge_token_limit == 400
