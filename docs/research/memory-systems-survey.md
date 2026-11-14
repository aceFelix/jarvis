# AI 记忆系统开源项目调研（记忆体系解耦 · 对标交付物）

> 作者：aceFelix　日期：2026-11-13　状态：**调研完成，结论用于校准《画像记忆提炼源头治理》计划**
>
> 目的：在自研 P0/P1/P2 之前，先判定市面成熟记忆系统能否**直接引入** jarvis。
> 结论：**没有一个可直接嵌入**，但有三套设计必须借鉴；本文逐条记录依据，供后续复核。

## 一、调研方法与信息源

按「一手来源优先」：star / 语言 / license / 活跃度取 **GitHub REST API** 实时字段；
依赖重量取各仓 **`pyproject.toml` 原文**；机制描述取 **README 原文**。抓取日期均为 **2026-11-13**。

| 对象 | 信息源 | 核实状态 |
|---|---|---|
| mem0 | `api.github.com/repos/mem0ai/mem0` + 仓库根 `pyproject.toml` | ✅ 完整（含全部硬依赖） |
| graphiti | `api.github.com/repos/getzep/graphiti` + `pyproject.toml` | ✅ 完整 |
| langmem | `api.github.com/repos/langchain-ai/langmem` + `pyproject.toml` | ✅ 完整 |
| memU | `api.github.com/repos/NevaMind-AI/memU` + `pyproject.toml` | ✅ 完整 |
| memobase | `api.github.com/repos/memodb-io/memobase` + `readme.md` 全文 430 行 | ✅ 完整（部署形态与 buffer 机制已确认） |
| cognee / letta / Memori / MemOS / A-mem | `api.github.com/repos/*` 元数据 | 🟡 元数据完整，**依赖清单与机制未逐个取原文** |
| 各项目 benchmark 名次 | 各仓 README 自述（LOCOMO 等） | 🔴 **自报数据，未独立复现** |

> 说明：cognee / letta / Memori / MemOS / A-mem 五项的机制描述来自其官方 README 与公开文档口径，
> 本文只用于「是否适配」的粗判；若后续要移植其算法，必须回到源码逐条核对。

## 二、一手数据总表

| 项目 | Stars | 语言 | License | 最近推送 | 形态 | 硬依赖（节选一手清单） |
|---|---:|---|---|---|---|---|
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | 68.9k | Python | Apache-2.0 | 2026-11-06 | 记忆层 / 平台 | `qdrant-client`、`pydantic`、`openai`、`httpx`、**`posthog`**、`sqlalchemy`、`protobuf` |
| [topoteretes/cognee](https://github.com/topoteretes/cognee) | 33.3k | Python | Apache-2.0 | 2026-11-13 | 记忆平台（图 + 向量） | 重（图数据库 + 向量库组合） |
| [getzep/graphiti](https://github.com/getzep/graphiti) | 32.8k | Python | Apache-2.0 | 2026-11-11 | 时序知识图谱 | **`neo4j`**、`pydantic`、`openai`、`numpy`、`posthog` |
| [letta-ai/letta](https://github.com/letta-ai/letta) | 25.4k | Python/TS | Apache-2.0 | 2026-10-18 | Agent 平台（含服务端） | 平台级（服务端 + DB） |
| [MemoriLabs/Memori](https://github.com/MemoriLabs/Memori) | 17.6k | Python | ⚠️ NOASSERTION | 2026-10-03 | agent-native 基础设施 | 中重（未逐条核） |
| [NevaMind-AI/memU](https://github.com/NevaMind-AI/memU) | 14.6k | Python | ⚠️ NOASSERTION | 2026-11-13 | **文件式个人记忆** CLI | `httpx`、`numpy`、`openai`、`pydantic`、**`sqlmodel` + `alembic`**、`pendulum` |
| [MemTensor/MemOS](https://github.com/MemTensor/MemOS) | 12.4k | **TypeScript** | Apache-2.0 | 2026-11-11 | 记忆 OS（MCP 插件） | 中（主实现非 Python） |
| [memodb-io/memobase](https://github.com/memodb-io/memobase) | 3.0k | Python | Apache-2.0 | **2026-01-11（停更）** | **用户画像式记忆服务器** | `FastAPI` + `Postgres` + `Redis`，全 Docker 化 |
| [langchain-ai/langmem](https://github.com/langchain-ai/langmem) | 1.8k | Python | MIT | 2026-11-03 | LangChain 记忆库 | `langchain`、`langgraph`、`langsmith`、`trustcall`、`langchain-*` 全家桶 |
| [agiresearch/A-mem](https://github.com/agiresearch/A-mem) | 1.2k | Python | MIT | 2025-12-12 | 论文实现（卡片笔记） | 轻（代码量小，无服务端） |

## 三、关键机制（只记与 jarvis 直接相关的事实）

### 3.1 memobase —— 用户画像式记忆（**与 jarvis 模型最同源**）

README 原文要点：

- 定位就是 **user profile-based memory**：结构化画像（`basic_info` / `demographics` / `education` /
  `interest` / `psychological` / `work` …，每项含 `content` / `id` / `created_at`）+ **用户事件时间线**。
  这几乎就是 jarvis `profile.json` 想长成的样子。
- **明确「不在 hot path 写记忆」**：插入的是 `ChatBlob`，先进每个用户的 buffer；
  buffer 变大约 **1024 token**、或空闲约 **1 小时**、或显式 `flush()`（会话关闭时）才真正提炼落库。
- 注入接口 `context(max_token_size=500, prefer_topics=["basic_info"])`：**按主题偏好 + token 预算**打包，
  直接可塞进 prompt；官方称在线延迟 < 100ms。
- 成本控制：单次运行 LLM 调用固定约 **3 次**（0.0.40 从 3–10 次降下来，省 40–50% token）。
- 部署：FastAPI + Postgres + Redis，Docker 化，带 auth / caching / telemetry；有 Python/Node/Go SDK 与 MCP。
- 风险：**2026-01 后停止推送**；组织已转向新产品 Acontext（README News 首条）。

### 3.2 mem0 —— 四操作裁决（ADD / UPDATE / DELETE / NOOP）

社区最主流的「记忆层」，其写入管线是业界事实标准：抽取候选事实 → 检索相似旧记忆 → 交 LLM 裁决
→ **四选一落库**。关键在 **NOOP 出口**：拿不准时不写，而不是硬塞一条新的。

代价：OSS 包硬依赖 `qdrant-client` + `sqlalchemy` + `protobuf` + **`posthog`（遥测 SDK）**，
且版本迭代快（现 2.2.1），嵌入成本高。

### 3.3 graphiti（Zep）—— 双时间轴知识图谱

边带 `valid_at` / `invalid_at`，新事实**让旧边失效而非删除**，可回答「去年职业是什么」。
代价：硬依赖 `neo4j`（或 falkordb / kuzu），jarvis 没有图数据库，不适用。

### 3.4 letta（MemGPT）—— core / archival / recall 三层

常驻可编辑的 core memory 块 + 向量检索的 archival + 原始对话 recall，配合自我编辑工具与
后台「睡眠期整理」。它验证了「常驻少量 + 按需检索大量」的分层思路。

### 3.5 memU —— 文件式个人记忆 + 宿主适配器

自我描述是 **"Personal memory as files"**，并给 Claude Code / Cursor / Codex 等宿主做了
专用 sidecar 命令（`memu-claude-code` 等）。思路与 jarvis「单机 + 文件 + 作为宿主」高度相似。
代价：`sqlmodel` + `alembic`（SQLite/Postgres 迁移体系），license 为 NOASSERTION。

### 3.6 langmem / A-mem

- langmem：LangGraph 生态的记忆工具与 prompt 优化器；依赖整个 LangChain 栈，嵌入等于引入框架。
- A-mem：Zettelkasten 卡片式（笔记构建 → 链接生成 → 记忆演化），代码轻，适合读思路不宜直接用
  （2025-12 后基本停更）。

## 四、按 jarvis 硬约束判定

jarvis 的三条硬约束：**单用户本地（无服务端）**、**严格控依赖（uv.lock）**、**本地优先/隐私**。

| 项目 | 无服务端 | 零重依赖 | 隐私本地 | Python 嵌入 | License 清晰 | 维护活跃 | 结论 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|---|
| mem0 | ✅（可本地向量库） | ❌ | ⚠️ posthog 硬依赖 | ✅ | ✅ | ✅ | **借设计** |
| graphiti | ❌ 需图库 | ❌ | ⚠️ posthog | ✅ | ✅ | ✅ | 不适用 |
| cognee | ❌ | ❌ | 🟡 | ✅ | ✅ | ✅ | 不适用 |
| letta | ❌ 平台 | ❌ | 🟡 | 🟡 | ✅ | ✅ | 借分层 |
| Memori | 🟡 | ❌ | 🟡 | ✅ | ⚠️ | ✅ | 观望 |
| memU | 🟡 | ❌ | 🟡 | ✅ | ⚠️ | ✅ | 读思路 |
| MemOS | ✅（MCP） | 🟡 | 🟡 | ❌ TS 主 | ✅ | ✅ | 看接口 |
| **memobase** | ❌ 需 PG+Redis | ❌ | 🟡 | ✅ | ✅ | ❌ 停更 | **借设计**（最贴） |
| langmem | ✅ | ❌ 全家桶 | 🟡 | ❌ 绑生态 | ✅ | ✅ | 不适用 |
| A-mem | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 学术参考 |

**判定：无一项可 `pip install` 直接进 jarvis。** 唯一「能跑起来」的路径是把 memobase 自建服务当
MCP server 接进来——但那要额外三个服务、迁移现有 `profile.json`、且上游已停更，性价比不成立。

## 五、改变设计的五个发现

这五条比「用谁」更重要，直接校准了
[PLAN-profile-memory-upgrade.md](../roadmap/PLAN-profile-memory-upgrade.md) 的 P0/P1/P2：

1. **P0 的方向被验证，但少一个触发条件。** memobase 的 buffer 是**三触发**：阈值（~1024 token）/
   **空闲超时（~1h）** / 会话关闭显式 flush。我们原计划只有「增量水位 + 退出终态」，缺**空闲超时**——
   用户聊到一半走开，那批增量会一直不落库。
2. **`NOOP` 是协议必需品，不是可选优化。** mem0 的实践证明：只给 ADD/UPDATE 两个出口，
   模型在「像重复但拿不准」时只能 ADD，重复是协议逼出来的。这坐实了 P1 的四操作设计。
3. **真正的「检索」发生在注入侧，而非提炼侧。** memobase 的 `context(max_token_size, prefer_topics)`
   说明成熟做法是「**按主题偏好 + token 预算**打包」。jarvis 现状是「按置信度排序 + 放不下就 continue」，
   既无主题维度，预算还是 `len/4` 的伪 token 口径（对中文严重低估，300 单位≈1200 字符）。
   → P1 应增设「注入侧改造」。
4. **profile / event 必须分轨。** memobase 同时维护结构化画像与**时间线事件**；graphiti 用双时间轴
   表达「何时为真」。jarvis 只有扁平事实、无时间维度，回答不了「上周我说过什么」。
   → P2 应从「生命周期字段」升级为「profile / event 双轨」。
5. **向量不是前置条件。** 所有对标项目都依赖 embedding，但 memobase 强调低延迟靠「画像 + SQL」
   而非向量检索；字面/关键词召回足以覆盖大部分去重场景。jarvis 维持 P3 后置、默认关闭是合理的。

### 需要警惕的三点

- **遥测**：mem0、graphiti 都把 `posthog` 列为硬依赖。本地优先产品若引入需确认可完全关闭。
- **License**：Memori、memU 为 `NOASSERTION`，商用/分发前必须法务确认。
- **自报 benchmark**：LOCOMO 等名次均为各仓自述，未独立复现，不可作为选型唯一依据。

## 六、S0 前置核实清单（若要把某条设计真正移植）

| # | 待核实 | 为什么必须核实 |
|---|---|---|
| 1 | memobase `flush` 的阈值/空闲判定实现（`src/server` 源码） | 我们只抄「三触发」结论，参数取多少要按 jarvis 会话长度重定 |
| 2 | mem0 update-resolver 的 prompt 与 NOOP 触发条件 | 决定 P1 的 few-shot 怎么写才不让模型滥用 NOOP |
| 3 | memU host adapter 如何发现宿主会话 | jarvis 自身就是宿主，若其检测机制有启发可省掉我们的水位台账 |
| 4 | `context(prefer_topics)` 的主题优先级算法 | 决定「注入侧改造」是否需要 category 权重表 |
| 5 | Memori / memU 的 License 实际条款 | 任何代码/MIT 之外的复用都要先确认 |

## 七、结论与下一步

1. **不引入任何依赖**，继续自研，保持 `profile.json` 纯 JSON 可读可编辑；
2. **采纳五条发现**，其中三条（空闲超时、注入侧主题预算、profile/event 双轨）已写入
   [PLAN-profile-memory-upgrade.md](../roadmap/PLAN-profile-memory-upgrade.md)；
3. 向量召回维持 P3 后置、默认关闭；
4. 若要进一步深入，优先读 memobase 源码（同源度最高）与 mem0 的 resolver prompt，
   而不是引入它们的包。
