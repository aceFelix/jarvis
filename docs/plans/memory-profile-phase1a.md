# Phase 1a：画像记忆（Profile Memory）实施计划

> 对应 [VISION.md](../VISION.md) 目标蓝图 · 维度一「记忆」的第一步
> 目标：JARVIS 从"每次初见"变成"记得你"——最小组间、最快体感

---

## 1. 背景与定位

JARVIS 当前只有会话内短期上下文（LayeredContext），每次启动都是"陌生人"。画像记忆是长期记忆三层架构（画像 / 情景 / 关系）中**工程量最小、用户体感最强**的一层：

- **存什么**：用户的偏好、习惯、背景事实（"习惯熬夜""文件按项目归类""常用 DeepSeek 和 GLM"）
- **不存什么**：对话原文（已在会话文件中）、具体事件经过（那是 Phase 1b 情景记忆的事）、实体关系网络（Phase 1c）

**一个重要澄清**：Phase 1a 是纯结构化条目，**用不到 embedding 向量检索**。已选定的 `tongyi-embedding-vision-flash` 是为 Phase 1b 情景记忆（海量模糊记忆按语义检索）准备的，本阶段只预留配置接口，不实际调用。本阶段唯一需要的外部能力是**文本 LLM 提炼**。

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│ 会话中（实时，零额外 LLM 成本）                            │
│   build_system_prompt() 注入高置信画像条目（限额 ~300 token）│
└─────────────────────────────────────────────────────────┘
                          │
                          ▼ 会话结束
┌─────────────────────────────────────────────────────────┐
│ 提炼管线（异步后台线程，不阻塞用户）                        │
│   save_session() 后触发                                   │
│   1. 取本会话消息（限额：最近 N 条）                        │
│   2. 用提炼模型（便宜模型）提取候选事实                      │
│   3. 与现有画像 diff → 新增 / 更新 / 冲突裁决 / 忽略         │
│   4. 写入 profile.json（原子写）                           │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼ 空闲时（daemon 定期，Phase 1a 末段）
┌─────────────────────────────────────────────────────────┐
│ 维护管线：置信度衰减、过期淘汰、低价值清理                    │
└─────────────────────────────────────────────────────────┘
```

**两条铁律**（吸取 skill 64k token 教训）：
1. 提炼永远异步，绝不在对话路径上跑 LLM
2. 注入永远限额，画像区 token 数硬封顶

---

## 3. 数据模型

存储位置：`~/.jarvis/memory/profile.json`（纯 JSON，用户可直接看、直接改）

```json
{
  "version": 1,
  "entries": [
    {
      "id": "ent_a1b2c3",
      "category": "work_habit",
      "content": "习惯深夜工作，早上 10 点前不喜欢被打扰",
      "confidence": 0.92,
      "source_session": "s_20260815_xxx",
      "created_at": "2026-08-15T02:30:00+08:00",
      "updated_at": "2026-08-15T02:30:00+08:00",
      "last_referenced_at": "2026-08-20T11:00:00+08:00",
      "ref_count": 3
    }
  ]
}
```

字段说明：

| 字段 | 用途 |
|------|------|
| `category` | 枚举：`identity`(身份背景) / `preference`(偏好) / `work_habit`(工作习惯) / `schedule`(作息) / `tool_usage`(工具使用) / `relationship`(联系人) / `project`(项目背景) / `other` |
| `confidence` | 0~1，提炼时 LLM 给出，被引用/复现则上调，长期不引用衰减 |
| `source_session` | 溯源：这条记忆从哪个会话来的（可回查） |
| `ref_count` / `last_referenced_at` | 维护管线的衰减依据 |

**上限控制**：条目总数上限 200 条，超出时按 `confidence × 新近度` 淘汰最低者。

---

## 4. 模块设计

### 4.1 存储层 `agent/core/memory/profile_store.py`（新建）

```python
class ProfileStore:
    def load(self) -> list[ProfileEntry]          # 启动时读 json
    def upsert(self, entry) -> None               # 新增/更新（按语义 id 或 LLM 裁决结果）
    def delete(self, entry_id) -> None            # /memory del 用
    def decay(self, days: int) -> int             # 维护管线：衰减+淘汰，返回清理数
    def render_for_prompt(self, token_limit: int) -> str
        # 按 confidence 排序，拼装注入文本，token 硬限额
```

要点：
- 原子写（临时文件 + `os.replace`，沿用会话恢复点的成熟做法）
- 读写加锁（提炼线程与主线程可能并发）

### 4.2 提炼层 `agent/core/memory/profile_refiner.py`（新建）

```python
class ProfileRefiner:
    def refine_session(self, messages: list, session_id: str) -> RefineReport
        # 1. 截取最近 N 条消息（默认 40 条，控制成本）
        # 2. 组装提炼 prompt（见下）
        # 3. 调用提炼模型，期望 JSON 输出
        # 4. 应用到 ProfileStore（含冲突裁决）
```

**提炼 prompt 核心约束**（输出必须是严格 JSON）：

```
从以下对话中提取关于用户的持久事实（值得长期记住的）。
规则：
- 只提取"多次出现或明确表达"的稳定事实，忽略一次性闲聊
- 每条给出 category（8 选 1）和 confidence（0~1）
- 与"已有画像"冲突时：给出 verdict = replace(附旧条目id) / supersede
- 没有值得记的就返回空数组——宁缺毋滥
输出：{"new": [...], "updates": [...], "ignore_reason": "..."}
```

**成本估算**：一次提炼 ≈ 输入 3~5k token + 输出几百 token，走 qwen-flash / deepseek-v4-flash 级别的模型，单次成本可忽略。

### 4.3 注入 `agent/prompts/system.py`（修改）

`build_system_prompt()` 增加可选参数 `profile_block: str | None`：

```python
# 关于用户（画像记忆）
- 习惯深夜工作，早上 10 点前不喜欢被打扰
- 文件按项目归类，项目根目录在 E:\2.MyProjects
```

注入位置：放在系统提示词**靠前但工具说明之前**（用户身份比工具更该优先）。由调用方（bootstrap / query_loop 初始化处）从 `ProfileStore.render_for_prompt()` 取文本传入，`system.py` 本身不依赖存储层，保持职责单一。

### 4.4 触发 `agent/core/memory/store.py`（修改）

`save_session()` 末尾追加：

```python
if settings.memory.enabled:
    threading.Thread(
        target=_run_refine_async, args=(messages, session_id), daemon=True
    ).start()
```

- 后台 daemon 线程，异常全部吞掉并写日志（提炼失败绝不能影响会话保存）
- 会话太短（< 6 条消息）或纯命令操作，直接跳过

### 4.5 命令 `agent/commands/handlers/memory_commands.py`（新建）

| 命令 | 功能 |
|------|------|
| `/memory` | 表格展示全部画像条目（id、category、内容、置信度、更新时间） |
| `/memory del <id>` | 删除指定条目 |
| `/memory add <文本>` | 手动添加（有些事用户想直接告诉 JARVIS 记住） |
| `/memory clear` | 清空画像（需确认） |
| `/memory refine` | 手动触发对当前会话立即提炼（调试用） |

注册到 `router.py` + 补全层（沿用 skill 命令的模式）。

### 4.6 配置 `agent/configs/settings.example.toml`（修改）+ `settings.py`

```toml
[memory]
enabled = true          # 总开关，关闭后不提炼也不注入
max_entries = 200       # 条目上限
inject_token_limit = 300  # 注入限额（token）
refine_min_messages = 6   # 会话至少多少条消息才触发提炼

[memory.refine_model]   # 独立提炼模型（默认回退主模型）
# provider = "dashscope"
# model = "qwen-flash"
# api_key = ""           # 留空则用顶层 LLM 配置

[memory.embedding]      # Phase 1b 预留，本阶段不生效
# model = "tongyi-embedding-vision-flash"
# provider = "dashscope"
```

`settings.py` 增加 `MemorySettings`（pydantic），沿用现有配置读取与迁移机制。

---

## 5. 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 提炼时机 | 会话结束后异步 | 不增加对话延迟（响应速度红线） |
| 提炼模型 | 独立可配，默认回退主模型 | 用户拍板：便宜模型跑提炼 |
| 注入策略 | 全量排序 + token 限额 | 画像条目少（≤200），无需检索；限额防膨胀 |
| 冲突处理 | LLM 裁决（replace/supersede）+ 时间戳新盖旧 | 用户习惯会变，旧记忆必须让位 |
| 遗忘 | confidence 衰减 + 上限淘汰 | 防画像自相矛盾、越用越臃肿 |
| 隐私 | 纯本地 JSON，用户可看可改可删 | VISION 安全维度：管家记得什么必须透明 |
| embedding | 本阶段不启用 | 画像无需向量检索；tongyi-embedding-vision-flash 留给 1b |

---

## 6. 里程碑

### M1：存储 + 提炼（核心链路）✅ 2026-08-16 完成
- [x] `ProfileStore`（load/upsert/delete/原子写/锁）
- [x] `ProfileRefiner`（提炼 prompt、JSON 解析容错、冲突裁决）
- [x] 提炼模型独立配置（settings + provider 路由复用）
- [x] `save_session()` 异步触发（`_auto_save` 挂载 + 10 分钟节流 + 防重入）
- **验收**：跑一段真实对话，退出会话后 `profile.json` 出现合理条目；提炼失败不影响会话保存

### M2：注入 + 命令（用户可见）✅ 2026-08-16 完成
- [x] `build_system_prompt()` 注入画像块（限额 300 token，进程内缓存保前缀稳定）
- [x] `/memory` 系列命令（查看/add/del/clear/refine/file；add/del/clear 后重建 system prompt 当前会话即生效）
- **验收**：新会话中 JARVIS 能自然引用画像（如主动避开早上打扰）；`/memory` 可管理条目

### M3：维护 + 打磨（长期健康）✅ 2026-08-16 完成
- [x] `decay()` 衰减与淘汰 + daemon 定期任务（ProactiveEngine 每日 03:30 静默维护，受 `profile_enabled` 开关控制）
- [x] `/memory refine` 手动触发
- [x] 单元测试：36 个（提炼 JSON 容错、并发读写、限额注入、衰减逻辑、命令全链路）
- [x] 文档：README 记忆章节 + 架构文档 `09-记忆与压缩.md` 第十四节
- **验收**：`pytest tests/core/test_profile_memory.py` 全绿（36 passed）；全量回归 1537 passed

---

## 7. 验收标准（对齐 VISION 成功标准）

1. 问 JARVIS "你知道我几点睡觉吗"——能从画像答出（而不是"我不知道"）
2. 换个新会话、甚至重启 JARVIS，它仍然记得你的习惯
3. `/memory` 里看到的每一条，都能说出是从哪次对话来的（source_session 溯源）
4. 对话响应速度与引入前无可感知差异（异步提炼 + 限额注入）

---

## 8. 为 Phase 1b/1c 预留的接口

- `memory/embedding.py`（1b）：DashScope multimodal embedding 客户端，配置已预留
- `ProfileEntry.source_session` 字段：1b 情景记忆可通过它反查原始会话
- `render_for_prompt(token_limit)` 的限额注入模式：1b 情景记忆检索结果注入沿用同一模式
- `memory/graph.py`（1c）：SQLite 三元组表，与画像共用提炼管线的消息截取与异步触发机制

---

**文档版本**：v1.0 | **创建**：2026-08-15 | **状态**：待评审
**关联**：[VISION.md](../VISION.md) 维度一 | 决策记录：embedding 选 tongyi-embedding-vision-flash（1b 启用）、首期仅画像、提炼模型独立可配
