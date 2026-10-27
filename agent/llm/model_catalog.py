"""在线模型目录：让厂商自己回答"你账号里有哪些模型"。

为什么需要它（阶段 12 P4 实测后修正）：首启原本内置一张"各厂商默认模型"表。
那张表有两个治不好的病：

1. **会过时**——厂商型号几个月一换，表里写死的 `gpt-4o`、`deepseek-chat` 后来一个
   太老、一个被官方弃用，用户照着选第一次调用就报错；
2. **替用户做了决定**——jarvis 是要给别人用的，"你喜欢哪个模型"不该由我们替人回答。

所以首启改成：选厂商 → 给 key → **实时拉出这个账号真实可用的模型让用户自己挑**。
在线拉取失败（断网、key 无效、厂商不支持 `/models`）时，回落到内置推荐款并明确告知，
同时始终保留"手动输入模型名"——**列表永远不能成为用户用不了的借口**。

只负责"列出 id"，不排序优劣、不推荐"最强"：那是用户的决定，不是我们的。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

#: 拉取目录的超时（秒）。首启是阻塞式交互，不能让一次网络抖动把人卡在半路。
DEFAULT_TIMEOUT_S = 10.0


def fetch_online(*, api_format: str = "openai", base_url: str = "",
                 api_key: str = "", timeout: float = DEFAULT_TIMEOUT_S) -> list[str]:
    """只要模型 id 列表时用这个（`fetch_with_reason` 的简化封装）。"""
    return fetch_with_reason(
        api_format=api_format, base_url=base_url, api_key=api_key, timeout=timeout
    )[0]


def fetch_with_reason(*, api_format: str = "openai", base_url: str = "",
                      api_key: str = "", timeout: float = DEFAULT_TIMEOUT_S
                      ) -> tuple[list[str], str]:
    """从厂商拉取模型 id 列表，**并带失败原因**。

    为什么要带原因：只返回空列表时，用户看到的是“怎么只有一个模型可选”，而
    “没给 key”“key 无效”“网络超时”“厂商不支持”四种情况长得一模一样——
    他根本不知道该补 key 还是该检查网络。原因必须带回给上层说出口。

    Args:
        api_format: 厂商协议（``openai`` / ``anthropic``，其余按 OpenAI 兼容试）。
        base_url: 厂商接入地址；空则用 SDK 默认。
        api_key: 为空直接返回（``[]``, ``未提供 API Key``），不发请求。
        timeout: 单次请求超时秒数。

    Returns:
        ``(模型 id 列表, 失败原因)``；成功时原因为空串。
    """
    if not api_key.strip():
        return [], "未提供 API Key，未发起在线查询"
    client = _make_client(api_format=api_format, base_url=base_url,
                          api_key=api_key, timeout=timeout)
    if client is None:
        return [], "无法创建客户端（SDK 未安装或 base_url 非法）"
    try:
        models = _read_ids(client)
        return models, ("" if models else "厂商返回了空的模型列表")
    except Exception as e:  # noqa: BLE001 - 拉目录是增强，不是首启的前置条件
        # 只留类型与一句话，避免把带 base_url/堆栈的长错误糊到首启界面上
        return [], f"{type(e).__name__}: {e}"[:160]
    finally:
        _close(client)


def _make_client(*, api_format: str, base_url: str, api_key: str,
                 timeout: float) -> Any | None:
    """按协议构造 SDK 客户端；缺依赖/构造失败返回 None。"""
    kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout}
    if base_url.strip():
        kwargs["base_url"] = base_url.strip()
    try:
        if api_format == "anthropic":
            from anthropic import Anthropic

            return Anthropic(**kwargs)
        from openai import OpenAI

        return OpenAI(**kwargs)
    except Exception:  # noqa: BLE001 - 没装 SDK 或 base_url 非法都走兜底
        return None


def _read_ids(client: Any) -> list[str]:
    """读出模型 id（两个 SDK 的 ``models.list()`` 都返回带 ``data`` 的分页对象）。"""
    page = client.models.list()
    rows = getattr(page, "data", None)
    if rows is None:                       # 有的版本直接可迭代
        rows = list(page)
    seen: list[str] = []
    for item in rows:
        mid = str(getattr(item, "id", "") or "").strip()
        if mid and mid not in seen:
            seen.append(mid)
    return seen


def _close(client: Any) -> None:
    """尽力关闭底层连接（不影响主流程）。"""
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass


def rank_for_picker(models: list[str], preferred: str = "", *,
                    limit: int = 0) -> list[str]:
    """给选择器排序：内置推荐款提前，其余保持厂商原顺序。

    注意这不是"推荐强弱"，只是**让用户最容易找到常见款**；排在后面的照样能选，
    选择器也支持输入字符筛选。

    Args:
        models: 在线拉到的 id 列表。
        preferred: 内置推荐款（存在则提到最前，便于老用户一眼认出）。
        limit: >0 时截断（防止某些厂商返回上百个模型把首启淹没；0 = 不截断）。

    Returns:
        新的列表（不改原列表）。
    """
    ordered = list(models)
    if preferred in ordered:
        ordered.remove(preferred)
        ordered.insert(0, preferred)
    if limit > 0:
        ordered = ordered[:limit]
    return ordered
