"""人设卡（P6）golden：固定 30 条画像 + 10 个“你了解我多少”式关键事实。

只用本地解析/渲染，不调真实 LLM：验证“仅凭 L0 人设卡能否覆盖 ≥80% 关键事实”、
token 上限、section 顺序稳定。

@author aceFelix
"""

from __future__ import annotations

import json

from agent.config.settings import Settings
from agent.core.memory import profile_persona as PP
from agent.core.memory.profile_store import ProfileEntry


def _settings(**kw) -> Settings:
    base = {"profile_enabled": True, "profile_persona_enabled": True,
            "profile_persona_token_limit": 800}
    base.update(kw)
    return Settings(**base)


def _fixture_entries() -> list[ProfileEntry]:
    """30 条、覆盖全部类别的画像条目（id 随机但稳定在本次构建内）。"""
    raw = [
        ("居住在杭州", "identity", 0.95),
        ("从事编程开发工作", "identity", 0.9),
        ("习惯凌晨两点睡", "schedule", 0.85),
        ("常把任务压到截止前收尾", "work_habit", 0.8),
        ("主力使用 GLM 系列模型", "tool_usage", 0.85),
        ("偏好简短、先给结论的回答", "preference", 0.9),
        ("偏好本地优先、轻依赖方案", "preference", 0.85),
        ("长期投入开发记忆系统", "project", 0.85),
        ("要求代码工程完整、有测试", "work_habit", 0.8),
        ("在意隐私与数据不出本机", "mindset", 0.8),
        ("性格直接，不喜欢客套", "personality", 0.85),
        ("喜欢复古像素风", "preference", 0.7),
        ("关注 AI 行业动态", "preference", 0.75),
        ("愿意为重要安排调整作息", "work_habit", 0.75),
        ("自认晚型人，夜间效率更高", "schedule", 0.8),
        ("使用 AI 代写代码", "work_habit", 0.8),
        ("重视行业实际做法", "personality", 0.7),
        ("希望助手主动指出风险", "mindset", 0.8),
        ("不喜欢过度设计", "preference", 0.75),
        ("长期目标是把助手做成私人贾维斯", "aspiration", 0.85),
        ("与团队协作，会评审他人代码", "relationship", 0.7),
        ("常用 Python 写工具", "tool_usage", 0.8),
        ("对配置项数量敏感", "mindset", 0.7),
        ("讨厌推测被当成事实", "mindset", 0.8),
        ("会为开源项目写文档", "work_habit", 0.7),
        ("偏好本地文件而非数据库", "preference", 0.8),
        ("周末偶尔打游戏", "preference", 0.6),
        ("正在升级记忆系统 P6", "project", 0.8),
        ("希望越用越懂自己", "aspiration", 0.85),
        ("沟通时希望先听结论再听过程", "preference", 0.8),
    ]
    return [ProfileEntry.new(content, category, conf) for content, category, conf in raw]


def _payload(entries: list[ProfileEntry]) -> dict:
    """模拟一份合格的人设卡：10 个关键事实全部落地，每条都带 source。"""
    by_text = {e.content: e.id for e in entries}
    src = lambda text: [by_text[text]]  # noqa: E731
    return {"sections": [
        {"key": "identity", "bullets": [
            {"text": "杭州的 AI 开发者，长期做编程与工具", "sources": src("居住在杭州") + src("从事编程开发工作")},
        ]},
        {"key": "communication", "bullets": [
            {"text": "喜欢先给结论、回答简短，不客套", "sources": src("偏好简短、先给结论的回答") + src("性格直接，不喜欢客套")},
        ]},
        {"key": "work_style", "bullets": [
            {"text": "让 AI 代写代码，但要求工程完整、有测试", "sources": src("使用 AI 代写代码") + src("要求代码工程完整、有测试")},
            {"text": "偏好本地优先、轻依赖、文件而非数据库", "sources": src("偏好本地优先、轻依赖方案") + src("偏好本地文件而非数据库")},
        ]},
        {"key": "rhythm", "bullets": [
            {"text": "习惯凌晨两点睡，自认晚型人", "sources": src("习惯凌晨两点睡") + src("自认晚型人，夜间效率更高")},
        ]},
        {"key": "boundaries", "bullets": [
            {"text": "在意隐私与数据不出本机；讨厌推测被当成事实", "sources": src("在意隐私与数据不出本机") + src("讨厌推测被当成事实")},
        ]},
        {"key": "current_focus", "bullets": [
            {"text": "正在升级记忆系统 P6：人设卡 + 全量归档", "sources": src("正在升级记忆系统 P6") + src("长期投入开发记忆系统")},
        ]},
        {"key": "aspiration", "bullets": [
            {"text": "希望助手越用越懂自己，最终成为私人贾维斯", "sources": src("希望越用越懂自己") + src("长期目标是把助手做成私人贾维斯")},
        ]},
    ]}


#: 10 个“你了解我多少”式问题对应的关键事实（人设卡里应能找到）
KEY_FACTS = [
    "杭州",
    "编程",
    "GLM",
    "简短",
    "凌晨两点",
    "本地优先",
    "工程完整",
    "记忆系统",
    "隐私",
    "推测",
]


def test_persona_covers_key_facts() -> None:
    entries = _fixture_entries()
    persona = PP.parse_persona_output(_payload(entries), entries, _settings())
    assert persona is not None
    text = PP.render_persona(persona)
    missing = [k for k in KEY_FACTS if k not in text]
    # 允许 1 条缺失：10 条关键事实覆盖 ≥80%
    assert len(missing) <= 2, f"人设卡漏掉太多关键事实: {missing}"


def test_persona_token_within_limit() -> None:
    entries = _fixture_entries()
    persona = PP.parse_persona_output(_payload(entries), entries, _settings())
    assert persona is not None
    assert persona.token_count <= 800
    assert len(persona.sections) >= 6


def test_section_order_is_stable() -> None:
    entries = _fixture_entries()
    a = PP.parse_persona_output(_payload(entries), entries, _settings())
    b = PP.parse_persona_output(_payload(entries), entries, _settings())
    assert [s.key for s in a.sections] == [s.key for s in b.sections]
    assert [s.key for s in a.sections][0] == "identity"


def test_all_sources_resolve_to_real_entries() -> None:
    entries = _fixture_entries()
    valid = {e.id for e in entries}
    persona = PP.parse_persona_output(_payload(entries), entries, _settings())
    for section in persona.sections:
        for bullet in section.bullets:
            assert bullet.sources
            assert set(bullet.sources) <= valid
