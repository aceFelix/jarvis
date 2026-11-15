"""画像检索 golden 集（P4）：15 组「提问 → 期望条目」。

口径（与 PLAN §P4 对齐）：

- **别名 + bigram 前两级**：15 组里至少 12 组能进 top-3；
- **关闭别名降级但不报错**：清空别名后命中数不增加，且每次调用都正常返回。

这批用例是后续调 half-life / 别名权重 / 注入预算的回归安全网——
改任何召回参数先跑这里。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.core.memory.profile_store import ProfileEntry, ProfileStore

# (提问, 期望命中的条目内容, 条目类别, 别名)
CASES: list[tuple[str, str, str, list[str]]] = [
    ("我作息怎么样", "习惯凌晨两点左右睡觉", "schedule", ["熬夜", "晚睡", "作息"]),
    ("我是做什么工作的", "职业是Agent应用开发工程师", "identity", ["职业", "工作", "程序员"]),
    ("我平时用什么编辑器", "常用 VS Code 写代码", "tool_usage", ["编辑器", "IDE", "vscode"]),
    ("我养的宠物叫什么", "养了一只叫团子的猫", "relationship", ["宠物", "猫", "团子"]),
    ("我住在哪个城市", "住在杭州", "identity", ["城市", "住哪", "杭州"]),
    ("我喜欢什么风格", "喜欢复古像素风和荧光绿", "preference", ["风格", "审美", "像素"]),
    ("我有什么目标", "想做一个开源的AI Agent", "aspiration", ["目标", "愿望", "开源"]),
    ("我的性格怎么样", "性格内向，不喜欢无效社交", "personality", ["性格", "内向", "社交"]),
    ("周末一般干什么", "周末喜欢在家打游戏", "preference", ["周末", "游戏", "娱乐"]),
    ("我做事有什么习惯", "习惯先做计划再动手", "work_habit", ["计划", "规划", "习惯"]),
    ("我爱喝咖啡吗", "每天要喝三杯咖啡", "preference", ["咖啡", "饮品"]),
    ("我日语学到哪了", "在学日语，准备N2", "project", ["日语", "学习", "N2"]),
    ("我对噪音敏感吗", "对噪音很敏感，需要安静环境", "mindset", ["噪音", "安静", "敏感"]),
    ("我用什么写文档", "用 WPS 处理文档", "tool_usage", ["办公软件", "文档", "wps"]),
    ("我的职业规划是什么", "希望三年内转做架构师", "aspiration", ["架构师", "职业规划", "转岗"]),
]

TOP_K = 3
MIN_HITS = 12


def _seed(store: ProfileStore, *, with_aliases: bool = True) -> None:
    for _query, content, category, aliases in CASES:
        store.upsert(
            ProfileEntry.new(
                content, category, 0.85,
                aliases=aliases if with_aliases else None,
            )
        )


def _hit_count(store: ProfileStore) -> int:
    hits = 0
    for query, content, _category, _aliases in CASES:
        top = [e.content for e in store.recall_similar(query, top_k=TOP_K)]
        hits += 1 if content in top else 0
    return hits


def test_recall_golden_hits_top3(tmp_path: Path) -> None:
    """15 组提问，别名+bigram 两级召回至少 12 组能进 top-3。"""
    store = ProfileStore(tmp_path / "p.json")
    _seed(store)
    hits = _hit_count(store)
    assert hits >= MIN_HITS, f"检索命中不足：{hits}/{len(CASES)}（要求 ≥{MIN_HITS}）"


def test_recall_degrades_without_aliases(tmp_path: Path) -> None:
    """关闭别名：命中数不增加、每次调用都正常返回（不报错）。"""
    store = ProfileStore(tmp_path / "p.json")
    _seed(store, with_aliases=False)
    hits_no_alias = _hit_count(store)

    assert hits_no_alias <= _hit_count_with_alias(tmp_path)


def _hit_count_with_alias(tmp_path: Path) -> int:
    store = ProfileStore(tmp_path / "with_alias.json")
    _seed(store)
    return _hit_count(store)


@pytest.mark.parametrize("query,content", [(q, c) for q, c, *_ in CASES])
def test_each_case_returns_something(tmp_path: Path, query: str, content: str) -> None:
    """兜底：每个提问至少能返回非空结果（不要求命中，只保证不静默）。"""
    store = ProfileStore(tmp_path / "p.json")
    _seed(store)
    assert store.recall_similar(query, top_k=TOP_K), f"{query!r} 召回为空"
    assert content  # 防用例表被写空
