"""共享 HTTP 连接池的关闭边界（2026-10 事故回归）。

事故经过（实机日志）：
    1. 主对话正常跑，provider A 复用全局共享 httpx client
    2. 记忆画像提炼新建临时 provider B（同样是 OpenAIProvider，共用同一个池）
    3. 提炼请求 404 失败，finally 里照旧执行 provider.close()
    4. close() 里是 AsyncOpenAI.close() —— 它会把传入的 http_client 一并
       aclose()，于是**全局连接池被关死**
    5. 主对话下一次请求直接
       RuntimeError: Cannot send a request, as the client has been closed
       被 openai SDK 包成「Connection error」
    6. 只有重启进程才恢复 —— 表现就是「刚启动第一句正常，之后每次必挂」

结论：provider 是短命对象，连接池是进程级资源，两者绝不能混在一个 close 里。

@author aceFelix
"""

from __future__ import annotations

import pytest

import agent.llm.openai_provider as op


@pytest.fixture
async def clean_pool():
    """用例前后都重置共享池，避免相互污染、也避免把脏池留给别的测试。"""
    await op.close_shared_http_client()
    yield
    await op.close_shared_http_client()


async def test_provider_close_keeps_shared_pool_alive(clean_pool) -> None:
    """provider.close() 之后，共享连接池必须仍然可用。"""
    pool = op._get_shared_http_client()
    assert pool.is_closed is False

    provider = op.OpenAIProvider(
        api_key="test-key", base_url="https://example.invalid/v1"
    )
    await provider.close()

    assert pool.is_closed is False, (
        "provider.close() 把全局共享连接池关掉了 —— "
        "任何临时 provider 下班都会让主对话报 client has been closed"
    )
    # 再次取到必须是同一个池子，不能被顺带重置
    assert op._get_shared_http_client() is pool


async def test_multiple_providers_share_one_pool(clean_pool) -> None:
    """多个 provider 共用同一个池；关掉其中一个不影响另一个继续取到它。"""
    p1 = op.OpenAIProvider(api_key="k1", base_url="https://example.invalid/v1")
    p2 = op.OpenAIProvider(api_key="k2", base_url="https://example.invalid/v1")
    pool = op._get_shared_http_client()

    await p1.close()

    assert pool.is_closed is False
    assert op._get_shared_http_client() is pool
    # p2 未受影响，仍可在池上工作（这里只看池状态，不发真实请求）
    assert p2._client is not None


async def test_close_shared_http_client_really_closes(clean_pool) -> None:
    """进程退出用的 close_shared_http_client() 才是真正关池子的地方。"""
    pool = op._get_shared_http_client()
    assert pool.is_closed is False

    await op.close_shared_http_client()

    assert pool.is_closed is True
    assert op._shared_http_client is None

    # 关掉之后再取，应当自动重建一个新池（is_closed 检测）
    rebuilt = op._get_shared_http_client()
    assert rebuilt is not pool
    assert rebuilt.is_closed is False


async def test_dedicated_client_is_physically_isolated(clean_pool) -> None:
    """dedicated_client=True 的 provider 用独占池，关它不伤共享池。

    记忆画像提炼就是走这条路：后台任务与主对话必须物理隔开，
    否则提炼一旦关闭/占用连接就会拖死前台会话。
    """
    shared = op._get_shared_http_client()

    provider = op.OpenAIProvider(
        api_key="k", base_url="https://example.invalid/v1", dedicated_client=True
    )
    own = provider._own_pool
    assert own is not None, "dedicated_client=True 应当建独占池"
    assert own is not shared, "独占池不能与共享池是同一个对象"

    await provider.close()

    assert own.is_closed is True, "独占池应当被 provider.close() 关闭"
    assert shared.is_closed is False, "关独占池不能连带共享池"
    assert op._get_shared_http_client() is shared
