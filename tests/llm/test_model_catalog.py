"""在线模型目录（阶段 12 P4：首启不替用户决定模型）。

核心不是"能不能拉到"，而是**失败时绝不能装作拉到了**：返回空列表让上层走兜底，
并明确告诉用户"没拉到在线列表"，否则用户会以为内置推荐就是厂商当前的全部。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from agent.llm import model_catalog


def _client_with(models: list[str]) -> SimpleNamespace:
    page = SimpleNamespace(data=[SimpleNamespace(id=m) for m in models])
    return SimpleNamespace(models=SimpleNamespace(list=lambda: page), close=lambda: None)


class TestFetchOnline:
    def test_openai_compatible_ids(self):
        client = _client_with(["m-a", "m-b"])
        with patch.object(model_catalog, "_make_client", return_value=client):
            assert model_catalog.fetch_online(api_key="sk", base_url="http://x/v1") == ["m-a", "m-b"]

    def test_empty_key_never_builds_client(self):
        """没凭据就不发请求（也不能让 SDK 拿空 key 去撞 401）。"""
        with patch.object(model_catalog, "_make_client") as make:
            assert model_catalog.fetch_online(api_key="") == []
            assert model_catalog.fetch_online(api_key="   ") == []
        make.assert_not_called()

    def test_network_failure_returns_empty(self):
        client = SimpleNamespace(
            models=SimpleNamespace(list=lambda: (_ for _ in ()).throw(RuntimeError("timeout"))),
            close=lambda: None,
        )
        with patch.object(model_catalog, "_make_client", return_value=client):
            assert model_catalog.fetch_online(api_key="sk") == []

    def test_client_construction_failure_returns_empty(self):
        with patch.object(model_catalog, "_make_client", return_value=None):
            assert model_catalog.fetch_online(api_key="sk") == []

    def test_duplicates_and_blank_ids_dropped(self):
        client = _client_with(["a", "a", "", "b"])
        with patch.object(model_catalog, "_make_client", return_value=client):
            assert model_catalog.fetch_online(api_key="sk") == ["a", "b"]

    def test_iterable_page_without_data_attr(self):
        """有的 SDK 版本分页对象可直接迭代、没有 .data。"""
        client = SimpleNamespace(
            models=SimpleNamespace(list=lambda: iter([SimpleNamespace(id="m1")])),
            close=lambda: None,
        )
        with patch.object(model_catalog, "_make_client", return_value=client):
            assert model_catalog.fetch_online(api_key="sk") == ["m1"]

    def test_client_closed_even_on_failure(self):
        closed: list[bool] = []
        client = SimpleNamespace(
            models=SimpleNamespace(list=lambda: (_ for _ in ()).throw(RuntimeError("x"))),
            close=lambda: closed.append(True),
        )
        with patch.object(model_catalog, "_make_client", return_value=client):
            model_catalog.fetch_online(api_key="sk")
        assert closed == [True]

    def test_anthropic_branch_uses_anthropic_sdk(self):
        """anthropic 原生协议不能走 OpenAI 客户端（端点与鉴权头都不同）。"""
        calls: list[str] = []

        class _FakeAnthropic:
            def __init__(self, **_kwargs):
                calls.append("anthropic")
                self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[]))

            def close(self):
                return None

        with patch("anthropic.Anthropic", _FakeAnthropic):
            model_catalog.fetch_online(api_format="anthropic", api_key="sk")
        assert calls == ["anthropic"]


class TestFetchWithReason:
    """失败原因必须带回来：否则“没给 key”和“给了但拉失败”在用户眼里一模一样。"""

    def test_no_key_reason_is_explicit(self):
        models, reason = model_catalog.fetch_with_reason(api_key="")
        assert models == [] and "未提供 API Key" in reason

    def test_network_error_reason_includes_exception(self):
        client = SimpleNamespace(
            models=SimpleNamespace(list=lambda: (_ for _ in ()).throw(RuntimeError("timed out"))),
            close=lambda: None,
        )
        with patch.object(model_catalog, "_make_client", return_value=client):
            models, reason = model_catalog.fetch_with_reason(api_key="sk")
        assert models == []
        assert "RuntimeError" in reason and "timed out" in reason

    def test_success_has_empty_reason(self):
        with patch.object(model_catalog, "_make_client",
                          return_value=_client_with(["m1"])):
            models, reason = model_catalog.fetch_with_reason(api_key="sk")
        assert models == ["m1"] and reason == ""

    def test_empty_page_is_a_reason_not_silence(self):
        with patch.object(model_catalog, "_make_client",
                          return_value=_client_with([])):
            models, reason = model_catalog.fetch_with_reason(api_key="sk")
        assert models == [] and "空" in reason


class TestRankForPicker:
    def test_preferred_floats_to_top(self):
        assert model_catalog.rank_for_picker(["a", "b", "c"], "c") == ["c", "a", "b"]

    def test_unknown_preferred_keeps_order(self):
        assert model_catalog.rank_for_picker(["a", "b"], "zzz") == ["a", "b"]

    def test_limit_truncates(self):
        assert model_catalog.rank_for_picker(["a", "b", "c"], limit=2) == ["a", "b"]

    def test_does_not_mutate_input(self):
        src = ["a", "b"]
        model_catalog.rank_for_picker(src, "b")
        assert src == ["a", "b"]
