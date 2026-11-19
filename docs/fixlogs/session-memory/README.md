# 会话 · 记忆 · 上下文

> [修复与复盘日志索引](../README.md) 的第 7 类，共 10 篇。
> 新增一篇时在本表补一行；文件名与路径保持 `<主题>-<简短描述>-fix.md`。

| 文件 | 主题 |
|---|---|
| [session-auto-resume-removal.md](session-auto-resume-removal.md) | 下线 auto-latest 全局指针（多项目串台） |
| [session-ready-first-bubble-swallow.md](session-ready-first-bubble-swallow.md) | `session_ready` 清屏吞掉首发气泡 |
| [session-title-fix.md](session-title-fix.md) | 会话标题生成失效 |
| [profile-duplicate-memory-fix.md](profile-duplicate-memory-fix.md) | 画像记忆重复入库（存储层确定性查重） |
| [profile-refine-auth-fix.md](profile-refine-auth-fix.md) | 画像提炼鉴权失败（提炼模型 key 解析链） |
| [profile-refine-tail-lost-fix.md](profile-refine-tail-lost-fix.md) | 尾部增量漏提炼（短尾巴被两个门槛同时挡住） |
| [profile-decay-reference-wiring-fix.md](profile-decay-reference-wiring-fix.md) | 衰减依据是死字段（last_referenced_at/ref_count 没人写） |
| [profile-update-category-downgrade-fix.md](profile-update-category-downgrade-fix.md) | update 漏写类别把 identity/permanent 降级成 other/stateful |
| [profile-refine-speculation-fix.md](profile-refine-speculation-fix.md) | 把推断当事实（“可能与出差有关”） |
| [profile-clear-orphan-persona-fix.md](profile-clear-orphan-persona-fix.md) | 清空画像后旧人设卡与事件轨残留 |
