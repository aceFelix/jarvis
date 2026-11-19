# 常驻用户画像记忆调研（"一直懂我"，而不是"这一分钟才想起"）

> 调研日期：2026-11-18 ｜ 作者：aceFelix ｜ 状态：调研定稿；**已立 P6**，实施细节见
> [plans/profile-memory-p6-persona-impl.md](../plans/profile-memory-p6-persona-impl.md)
>
> 触发问题：按需检索只能让助手在“被问到”时想起用户；要让它**始终知道我是一个什么样的人**，
> 是不是必须把全部画像塞进上下文？全量注入的 token 成本怎么办？有没有不依赖本地大模型的折中方案？

## 一、结论先行

1. **没有任何一家被核实的实现，把全部原始记忆逐条常驻注入。**
   它们都常驻一份**蒸馏过的“人设/核心记忆”**（Letta 的 core memory、Claude 的 injected context /
   Topics、ChatGPT 的 saved memories + memory summary、Memobase 的 profile slots），
   原文留在**归档层**，需要时再检索。
2. **“一直懂我”靠的不是“全量”，而是“蒸馏层的稳定性”。**
   Anthropic 明确写：注入层（injected context）每次会话都会加载，因此**必须保持相对小**；
   细节要下沉到下层并留下链接。Claude Code 的自动记忆甚至只加载 `MEMORY.md` 的
   **前 200 行 / 25KB 作为索引**，不加载全部。
3. **“全量加载”只在很小的时候可行。**
   经验阈值：画像总量 ≤ **约 2k tokens（80–120 条短事实）** 时，全量注入既便宜又不伤注意力；
   超过后，收益递减、成本线性上涨，而且**指令越长模型依从性越差**（Anthropic 原话）。
4. **正确形态是三层**：常驻人设卡（小、稳定、缓存友好）＋ 动态少量细节 ＋ 全量归档（可检索、可列举）。
5. 对 jarvis：**不需要向量库、不需要本地大模型、不引新依赖**。
   用现有提炼模型在后台生成一张 `persona` 人设卡（约 600–800 tokens），常驻注入；
   原始条目继续全量保留，靠 `RecallProfile` 的 `search/list` 兜底。
   实施细节见 [P6 方案](../plans/profile-memory-p6-persona-impl.md)。

## 二、一手资料怎么说的

| 系统 | 常驻注入什么 | 归档/检索 | 维护机制 | 核实状态 |
|---|---|---|---|---|
| **Anthropic（研究）** | **Injected Context**：每次会话预加载的少量关键记忆 | Transcripts（可搜索日志）→ Notes → Wiki 三层在下 | **Dreaming**：后台对照原始记录纠错、建 wiki、把细节下沉、只把少量提升到注入层 | ✅ 官方研究原文 |
| **Claude 应用** | Memory Topics（按话题的短文件） | 过去的聊天可搜索引用 | 边聊边更新；可查看/编辑/删除每个话题文件 | 🟡 官方帮助中心摘要 |
| **Claude API** | `/memories` 目录由模型自行读写；任务开始前检查 | 目录持久化，客户端自行存储 | 模型用 view/create/str_replace 等六种文件操作维护 | 🟡 官方文档区域受限，机制经多源交叉 |
| **ChatGPT** | “已保存的记忆” + “记忆摘要”；聊天记录引用是检索而非全存 | 原始聊天仍保留 | 持续更新“最值得记住”的摘要；可查看/删除 | ✅ 官方帮助中心 |
| **Letta / MemGPT** | **Core Memory Blocks**：常驻 system prompt、模型可自编辑；示例上限 5000 字符/块（persona + human） | Recall（消息检索）+ Archival（归档，按需分页） | 模型用 memory 工具改块；Sleep-time Compute 后台整理 | ✅ 官方文档 |
| **Memobase** | `context()` 返回**预编译的用户画像**（结构化 slots） | 事件库按语义/时间检索 | 缓冲区批量提炼（1024 token / 1 小时） | ✅ 官方文档 |
| **Mem0** | 按查询检索用户/会话/Agent 记忆 | 向量 + 实体库 | 抽取 → 四操作（ADD/UPDATE/DELETE/NOOP） | ✅ 官方文档结构 + 二手细节 |
| **Zep / Graphiti** | 按查询组装 context block | 时序知识图谱（事实带有效期） | 双时间轴、Episode 血缘 | 🟡 官方 landing + 二手论文解读 |
| **claude-mem（社区）** | 每次检索 top 3–5 条相关记忆注入 | 本地记忆库 + 语义检索 | 会话中自动抽取、摘要 | 🟡 社区项目，非官方 |

关键原文（Anthropic，2026-11）：

> “Injected memories use up valuable tokens, and an incorrect note can be carried into every session.
> … the top layer of the memory hierarchy has to stay relatively small, and much of dreaming's work
> here is keeping it that way—by moving some details down the hierarchy and leaving links behind.”

> “Claude doesn't load all of its auto memory into every session's context window.
> It loads only the first 200 lines or 25 KB of MEMORY.md. This header serves as an index.”

另一个反直觉结论（同文）：宝可梦实验里，**写了 413 个记忆文件的版本输给了只写 9 个文件的版本**。
差别不在容量，而在于“写进去的是不是经过验证、可复用的判断”。

## 三、为什么“全量注入”不划算

### 3.1 成本可以算，但注意力不能

按每轮都注入计算（300 轮/天）：

| 画像规模 | 注入 tokens/轮 | tokens/天 | 按 ¥1/M 计 | 按 $3/M 计 |
|---|---:|---:|---:|---:|
| 人设卡 800 tokens | 800 | 24 万 | ¥0.24/天 | $0.72/天 |
| 200 条原文（约 4k） | 4,000 | 120 万 | ¥1.2/天 | $3.6/天 |
| 1000 条原文（约 20k） | 20,000 | 600 万 | ¥6/天 | $18/天 |

价格只是量级示意（各厂商/缓存命中差异很大；稳定前缀通常还有缓存折扣）。
真正的问题不是“付不付得起”，而是：

- **指令越长，模型对每条信息的依从性越差**（Anthropic 明确警告）；
- 关键事实会被淹没在大量低价值条目里，回答反而更不像“懂你”；
- 人物画像需要的是**稳定的判断**，不是流水账。

### 3.2 什么时候可以全量

- 画像 ≤ **2k tokens**（约 80–120 条短事实）：全量注入可行，实现也最简单；
- 超过后：人设卡 + 归档检索的混合方案，质量与成本都更好；
- 百万上下文解决的是“装得下”，解决不了“每条都被认真对待”。

## 四、给 jarvis 的方案：P6 常驻人设卡

### 4.1 三层结构

```
┌─ L0 人设卡 persona（常驻，600–900 tokens，稳定顺序 → 前缀缓存友好）
│    他是谁 / 怎么跟他说话 / 工作方式 / 当前重点 / 生活节奏 / 在意的人 / 雷区
├─ L1 动态细节（少量，最多 3–5 条：最近变化、当前关注）
└─ L2 全量归档（所有条目，不设上限；RecallProfile search/list 按需读取）
```

L0 解决“一直懂我”；L2 解决“全记住、不丢失”；L1 解决“最近的事别滞后”。

### 4.2 人设卡长什么样

```markdown
## 他是谁
- 杭州，AI 应用开发者；偏好本地优先、轻依赖、可离线。
## 怎么跟他说话
- 中文，先给结论；不要铺垫和客套；代码要能直接跑。
## 工作方式
- 主力用 GLM / DeepSeek 等 API；让 AI 代写代码，但要求工程完整、有测试。
- 常把任务压到临近截止才收尾。
## 当前重点
- J.A.R.V.I.S 画像记忆升级：P0–P5 已落地，P6 人设卡待定。
## 生活节奏
- 习惯凌晨一两点睡；愿意为重要安排调整作息。
## 在意的事
- 记忆要“越用越懂我”，不接受按需检索式的“这一分钟才想起”。
## 雷区
- 不要把推断当事实；不要引重依赖 / 向量数据库 / 云端 embedding。
```

注意：这些是**综合判断**，不是原文条目；原文仍在 L2，可追溯、可纠正。

### 4.3 生成与更新（后台，绝不进对话路径）

| 项 | 设计 |
|---|---|
| 生成器 | 复用现有提炼模型（`[memory.refine]`），一次 LLM 调用 |
| 输入 | 分层采样：全部 `permanent` + 每类 top 15（按权重）+ 最近 50 条变更 + 上一版人设卡（保持连续性） |
| 输出 | JSON：`{"sections": [{"title": "...", "bullets": [{"text": "...", "sources": ["ent_x"]}]}]}` |
| 防幻觉 | 每条 bullet 必须带 source id；id 不在库中 → 丢弃；丢弃率过高 → 保留旧卡 |
| 长度 | 硬上限 `profile_persona_token_limit`（默认 800，按现有预算口径计量） |
| 时机 | **只在正常退出时重建**；异常退出靠 pending 标记 + 下次启动补偿；新建/切会话不重建；`/memory persona rebuild` 手动；无时间/条数参数 |
| 失败 | 保留上一版；静默写 diag，绝不影响对话 |
| 版本 | `~/.jarvis/memory/persona.json` 记录 `updated_at / source_count / sections` |

### 4.4 注入与缓存

- 人设卡放在 system prompt 最前（画像段内），顺序固定；
- 只在重建时变化 → 同一会话内逐字节稳定，命中前缀缓存；
- L1 动态条目放在人设卡之后（变化不影响 L0 前缀）；
- 总注入预算：人设卡 800 + 动态 3–5 条 ≈ 1k tokens，远低于现在的 700 token 限额放大后的原始条目量。

### 4.5 与现有 P0–P5 的关系

| 现有能力 | P6 后 |
|---|---|
| 全量条目 + 无上限 | 保留，作为 L2 |
| `RecallProfile(search)` | 保留，查具体事实 |
| `RecallProfile(list)` | 保留，回答“你了解我多少”时翻全库 |
| `profile_inject_core_max=12` 原始条目注入 | 降为 **3–5 条动态补充**；主体由人设卡承担 |
| 休眠/降权 | 保留：人设卡只从**有效条目**生成；休眠条目不进 L0，但仍在 L2 可检索 |

### 4.6 验收指标

1. 人设卡 ≤ 800 tokens，单次重建 1 次 LLM 调用、≤ 20 秒；
2. 10 个“你了解我多少”式问题：仅凭 L0 回答能覆盖 ≥80% 的关键事实（人工/LLM 评审）；
3. 重建失败率 0 影响对话（旧卡继续用）；
4. 同库重复重建结果稳定（temperature 低、sections 顺序固定）；
5. 注入 token 实测：相对“全量画像注入”（200 条 ≈ 4k tokens）下降 ≥70%；
   相对当前“核心 12 条”会略增（约 240 → 1000 tokens），但换来的是常驻人设 + 覆盖度提升，
   并用缓存与只读前缀抵消重复成本。

### 4.7 配置草案（只加 2 个键）

```toml
profile_persona_enabled = true       # 常驻人设卡（P6）
profile_persona_token_limit = 800    # 人设卡 token 上限
```

## 五、风险与对策

| 风险 | 对策 |
|---|---|
| 摘要失真/固化错误 | 每条 bullet 带 source id 校验；保留原文；`/memory persona` 可查看、可重建 |
| 人设卡漂移 | 重建时带上一版做连续性约束；重大变更才改稳定段落 |
| 缓存失效 | 重建只在退出时发生，且 L1 放在 L0 之后 |
| 成本失控 | 人设卡硬上限 800 tokens；输入采样有上限；失败不重试轰炸 |
| 误删/丢记忆 | L2 不设上限；只降权不删除；episodic 到期除外 |

## 六、明确不做

- 不把全部原始条目逐轮注入（成本 + 依从性双输）；
- 不引入向量库 / 图数据库 / 云端 embedding（违背本地优先铁律）；
- 不要求用户本地部署模型（人设卡由现有云端提炼模型生成，失败静默）。

## 七、参考（核实日期 2026-11-18）

- Anthropic Research, *The components of agent memory*（2026-11）：https://www.anthropic.com/research/components-of-agent-memory
- Letta Docs, *Memory blocks (core memory)*：https://docs.letta.com/guides/agents/memory-blocks
- Letta Docs, *Introduction to stateful agents*：https://docs.letta.com/guides/agents/memory
- Memobase Docs, *Retrieving the Memory Prompt*：https://docs.memobase.io/features/context.md
- Memobase Docs, *Profile Fundamentals*：https://docs.memobase.io/features/profile/profile.md
- Memobase Docs, *Performance and Cost*：https://docs.memobase.io/cost.md
- OpenAI Help Center, *Memory in ChatGPT*：https://help.openai.com/zh-hans-cn/articles/8590148-memory-in-chatgpt
- OpenAI, *Memory and new controls for ChatGPT*：https://openai.com/index/memory-and-new-controls-for-chatgpt/
- Claude Help Center, *Chat search and memory*：https://support.claude.com/zh-CN/articles/11817273
- 本仓既有项目对比（依赖/许可证/Star）：[memory-systems-survey.md](memory-systems-survey.md)
