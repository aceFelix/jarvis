# 画像记忆 / 长期记忆

> [实施计划索引](../README.md) 的第 1 类，共 5 篇。

| 文档 | 内容 | 状态 |
|---|---|---|
| [memory-profile-phase1a.md](memory-profile-phase1a.md) | Phase 1a 画像记忆初版：提炼 / 存储 / 注入的全链路设计 | 历史基线（现由 P0–P2 取代） |
| [profile-memory-p0-impl.md](profile-memory-p0-impl.md) | P0：提炼水位增量 + 三触发 + P4 评测集（**空闲触发已于 2026-11-17 下线**） | 已落地 |
| [profile-memory-p1-impl.md](profile-memory-p1-impl.md) | P1：两段式裁决（抽取 → 逐条召回 → 四操作）+ 别名 + 注入侧改造 | 已落地（P1a–P1d） |
| [profile-memory-p2-impl.md](profile-memory-p2-impl.md) | P2：lifecycle 分层 + 软删除失效链 + 事件轨；衰减改降权休眠 | 已落地（P2a–P2c） |
| [profile-memory-p6-persona-impl.md](profile-memory-p6-persona-impl.md) | P6：常驻人设卡 + `RecallProfile` list 全量分页；全文归档不变 | 已落地（P6a–P6f） |
