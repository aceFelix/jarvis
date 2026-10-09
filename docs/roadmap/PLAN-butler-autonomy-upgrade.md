# 管家形态升级计划（阶段 8–11）：从被动执行器到主动且可信的代理

> 状态：**待评审**（本文档只做设计、排依赖、切片，不代表已开工）。
> 定位：既有的 P1/P2/P3 清单是"能力愿望单"，本文档回答一个更前置的问题——
> **已有的零件（调度器/感知器/记忆/审计/沙箱）之间缺哪几个接口，才能让"主动陪伴"和"无人值守"真的成立。**
>
> @author aceFelix

---

## 0. 问题陈述

横向能力已经不缺：100+ 工具、双模语音、三层记忆、五层权限、沙箱、多端同会话、桌面壳都已落地
（见 [BLUEPRINT.md](../BLUEPRINT.md) 的现状自评与能力模型）。但"真正的管家"体验仍然不成立，卡在三处**接口**上：

| # | 现象 | 根因 |
|---|---|---|
| 1 | 「主动」实际上只是闹钟 | `Scheduler` 只吃时钟、`VisionWatcher` 只吃摄像头；**没有工作现场的事件流**（前台窗口、文件/仓库变更、邮件/日历订阅） |
| 2 | 一扩感知就会从"不够主动"掉进"太吵" | **缺"该不该打扰我"的决策层**：只有 `max_escalate` 防重复，没有重要度评分、聚合、静默时段、渠道选择 |
| 3 | 不敢让它无人值守 | 任务是**一次性的"到点说一句话"**（`ScheduleTask`），活不过一次对话；行动只有工作区文件可回滚，邮件/消息/系统设置没有副作用账本 |

外加两条独立的"最后一公里"：

- **实时语音不能干活**（本轮实测：工具 schema 数与建响应延迟近似线性 ≈12ms/个，
  2 个工具 0.67s、38 个 0.89s、294 个 3.45s、450 个 4.89s）。把全量工具塞进实时链路不可行，
  正解不是砍工具，而是**口手分离**（见 W4）。
- **长期记忆是"提炼文本"而非"可查询事实"**：缺结构化实体、时效、来源与冲突消解（见 W4）。

### 已完成与缺口的对照

| 支柱 | 已有零件 | 缺口 |
|---|---|---|
| 感知 | `Scheduler`（`~/.jarvis/schedule.json` 持久化 + 错过补偿）、`VisionWatcher`（mediapipe 手势/人脸事件流）、`SystemMonitor`（阈值告警 + 600s 冷却）、`ProactiveEngine`（简报/截止/日历四源） | 事件源单一；各源之间无统一总线，无法交叉触发 |
| 判断 | 五层权限管线（fail-closed）、`permissions.yaml`、`max_escalate` 防轰炸 | 高风险确认靠 instructions 让模型自判（不可靠）；无授权租约、无打扰预算、无无人值守策略 |
| 兑现 | `ScheduleTask`（once/daily/weekly）、`Subagent`/`Team`/`TaskList`（单次查询内）、沙箱 + shadow-git 文件回滚、操作审计 | 无跨会话/跨天任务脊柱；无条件等待；无可撤销副作用账本 |
| 记忆 | `store` / `profile_store` / `profile_refiner` / `prune` / `file_state` / `compactor` | 无实体-关系-时间结构化；无时效与出处；无冲突消解 |

---

## 1. 三支柱总览

```
   感知层 W2（事件）            判断层 W1（闸门）             兑现层 W3（行动）
 ┌─────────────────┐        ┌──────────────────┐        ┌──────────────────┐
 │ 时钟（已有）      │        │ 策略引擎          │        │ 任务脊柱          │
 │ 摄像头（已有）    │──事件──▶│ 授权租约          │──放行──▶│ （目标/步骤/条件） │
 │ 前台窗口  新增    │        │ 打扰预算/重要度    │        │ 副作用账本 + 撤销  │
 │ 文件·仓库  新增   │        │ 无人值守模式       │        │ /undo 与事后简报   │
 │ 邮件·日历  新增   │        └──────────────────┘        └──────────────────┘
 └─────────────────┘                  ▲                            ▲
                                      │                            │
            贯穿：结构化长期记忆（W4.2）· 操作审计（已有）· 口手分离（W4.1）
```

依赖顺序：**W1 → W2 → W3 → W4**（依次对应总路线图的**阶段 8 / 9 / 10 / 11**）。
理由：先有闸门再扩感知（否则主动性=骚扰）；先有持久任务再谈无人值守；W4 是体验补齐，属收益而非地基。

---

## 2. W1 · 判断层：确定性策略引擎 + 打扰预算（前置闸门）

### 2.1 现状与问题

- 高风险操作的确认目前由 **instructions 引导模型自行判断**（`realtime_talk.default_instructions()` 与
  主 prompt 都是这个路子）。模型判断不可靠，且不可审计。
- 权限系统（`agent/permissions/`，五层管线）擅长回答"这个工具调用能不能做"，
  但回答不了"**这次调用值不值得打断用户**"以及"**在无人值守时该不该做**"。

### 2.2 设计

**① 策略表（policy-as-code）** — 新增 `agent/core/policy/engine.py`，规则源 `~/.jarvis/policy.yaml`：

```yaml
rules:
  - name: 只读查询免打扰
    match: {tool: ["FileRead", "Glob", "Grep", "WebSearch"]}
    effect: allow
  - name: 项目内写入免打扰（限定目录）
    match: {tool: ["FileWrite", "FileEdit"], path_within: ["~/work/**"]}
    effect: allow
  - name: 外发类必须先看预览
    match: {tool: ["SendMail", "SendMessage"]}
    effect: dry_run            # 先出预览，等确认
  - name: 无人值守时一律不做破坏性操作
    match: {tool: ["Bash"], destructive: true, away_mode: true}
    effect: deny
```

- 与既有五层管线的关系：策略引擎**不改动既有语义**，作为规则来源接入同一套合并语义
  （`DENY > ASK > ALLOW`），默认值仍 fail-closed。
- 效果取值：`allow` / `ask` / `deny` / `dry_run`（dry_run 是新增语义，见 W3.2）。

**② 授权租约（lease）** — `agent/core/policy/lease.py`，落盘 `~/.jarvis/leases.json`：

```json
{"id": "…", "scope": {"path_within": ["~/work/reports/**"], "tools": ["FileWrite"]},
 "granted_at": "…", "expires_at": "…", "granted_by": "user", "note": "这批报告今天随便改"}
```

- 命中租约 → 免打断直接执行；`expires_at` 到期自动失效。
- 用户可一屏查看/一键撤销所有生效租约（桌面设置面板 + `/lease list|revoke`）。
- 这是**减少打断的主要手段**：先说"接下来 2 小时允许动 `~/reports`"，而不是每次都弹确认。

**③ 打扰预算（notify budget）** — `agent/core/policy/budget.py`：

- `daily_max`（每日主动播报次数上限）、`quiet_hours`（静默时段只入库不打扰）、
  `aggregate_window_s`（同源事件 5 分钟内合并成一条）、`min_salience`（低于门槛不播报）、
  `channels`（桌面通知 / TTS / 手机推送 的优先级与降级顺序）。
- **重要度评分** = 来源权重 × 时效紧迫度 × 与当前活跃任务的相关性 × 该来源历史接受率
  （接受率来自审计日志，形成"越用越懂你"的闭环）。
- 落点：`ProactiveEngine` 的 `on_notify` 之前插入 budget 过滤，而不是在每个提醒源里各写一遍。

**④ 无人值守模式（away mode）**：

- 判定：复用 `VisionWatcher`（人脸离开）+ 键鼠活动空闲时长，或手动 `/away`。
- 行为：away 期间只执行 `allow` 级策略 + 记入待办队列；不可逆动作一律排队等回来批。
- 回归：一条事后简报——"先生，您不在期间我处理了 3 件事，其中 1 件待您确认"，附可撤销入口。

### 2.3 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/policy/engine.py`（新增） | 策略匹配 + 效果判定，接入既有权限合并语义 |
| `agent/core/policy/lease.py`（新增） | 租约签发/校验/过期/撤销 |
| `agent/core/policy/budget.py`（新增） | 打扰预算 + 重要度评分 + 聚合/静默 |
| `agent/core/daemon/proactive.py` | `on_notify` 前插入 budget 过滤；away mode 分流 |
| `agent/permissions/*` | 规则来源扩展（不改合并语义） |
| `agent/ui/workbench/engine.py` | 待批清单面板 + 租约管理 |
| `agent/config/settings.py` + `configs/permissions.yaml` | `[policy]` / `[proactive.notify_budget]` 配置项 |
| `tests/core/test_policy_engine.py`（新增） | allow/ask/deny/dry_run 四态 × 租约命中/过期 |

### 2.4 验收

- [ ] 同一操作在"有租约/无租约/away"三种情境下得到确定的三种结果，且可解释（命中哪条规则）。
- [ ] 静默时段内的提醒只入库不播报；解除后聚合为一条。
- [ ] 租约过期后行为立刻回到 `ask`，无需重启。
- [ ] 既有权限行为零回归（五层管线测试全绿）。

### 2.5 风险

规则漏配导致误放行 → 策略引擎默认 fail-closed，未命中任何规则时回落到既有管线结论。

---

## 3. W2 · 感知层：统一事件总线

### 3.1 设计

新增 `agent/core/percept/`，把"时钟/摄像头"扩成"工作现场"，但**默认全关**：

```python
@dataclass
class PerceptEvent:
    ts: str; source: str; kind: str          # 如 vcs / file / window / mail / calendar
    payload: dict; salience_hint: float      # 源给出的初判，最终由 W1 评分
    dedup_key: str                           # 同一件事 5 分钟只算一次
```

| 事件源 | 采集方式 | 节流/约束 |
|---|---|---|
| 前台窗口 `window` | Windows `GetForegroundWindow` + 进程名/标题 | 5s 采样；标题正则脱敏；应用排除名单 |
| 文件守望 `file` | 复用 `core/memory/file_state.py` 的目录快照 | 只订阅白名单目录；inotify/轮询自适应 |
| 仓库/CI `vcs` | `git status` 差分 + 构建/CI 结果尾行 | 最小间隔 60s；仅订阅登记过的仓库 |
| 邮件 `mail` | IMAP 只读（IDLE） | 只取信封头（发件人/主题/时间），不拉正文 |
| 日历 `calendar` | 已有 `CalendarSource` 升级为事件源 | 每 30 分钟 |
| 系统 `system` | 已有 `SystemMonitor` | 沿用 600s 冷却 |

- **Scheduler 降级为事件源之一**：`schedule.fired` 也进总线，交由 W1 统一决定播报与否。
- 成本控制硬约束：**本地规则/阈值先判，不每次调 LLM**；只有通过 salience 门槛的事件才进
  一次轻量 LLM 生成话术（可离线降级为模板文案）。
- 隐私：每个源独立开关 + 状态可见（"正在观察：前台窗口 / 构建状态"）+ 数据仅本地落盘、
  不随对话上传；采集范围可一键导出查看。

### 3.2 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/percept/bus.py`（新增） | 事件模型 + 订阅/分发 + 去重 + 节流 |
| `agent/core/percept/source_window.py` 等（新增，一源一文件） | 各采集器 |
| `agent/core/daemon/proactive.py` | 改为订阅总线（保留既有四源，行为不变） |
| `agent/core/daemon/vision_watcher.py` | 事件接入总线（统一出口） |
| `agent/config/settings.py` + `configs/settings.example.toml` | `[percept.*]` 开关与白名单 |
| `tests/core/test_percept_bus.py`（新增） | 去重/节流/静默/降级 |

### 3.3 验收

- [ ] 构造四类事件（构建失败 / 目录堆积 / 前台切到某应用超时 / 收到特定来件），
      经 W1 预算后各自产出**恰好一条**聚合提醒。
- [ ] 常驻 CPU 增量 < 1%（空载 10 分钟均值），无源开启时为 0。
- [ ] 任一源异常不影响其他源（沿用 `ProactiveEngine` 的优雅降级惯例）。

---

## 4. W3 · 兑现层：持久任务脊柱 + 可撤销行动

### 4.1 任务脊柱（让"替我盯着"成立）

现状：`ScheduleTask` 只有 `trigger_at + content`，是闹钟不是任务。
新增 `agent/core/tasks/spine.py`，落盘 `~/.jarvis/tasks/<id>.json`：

```json
{"id":"…","goal":"把本周会议纪要与待办整理成一页周报并发给团队",
 "steps":[{"desc":"收集本周纪要","state":"done"},
          {"desc":"生成周报","state":"running"},
          {"desc":"等张三确认数据","state":"blocked","wait":{"kind":"mail","from":"zhangsan@…"}},
          {"desc":"发送","state":"pending"}],
 "attempts":{"…":2}, "budget":{"tokens":…,"usd":…}, "session_id":"…", "created_at":"…"}
```

- **条件等待 `wait`**：支持 `time` / `file_exists` / `percept_event`（订阅 W2 的事件，
  例如"等对方的附件到了再归档"）。
- **断点续跑**：daemon 重启后恢复；失败按退避重试；阻塞超过 N 天主动来问用户（走 W1 预算）。
- 与既有零件的关系：`TaskList`（多 Agent 协作）= 一次查询内的并行子任务；
  spine = **跨会话、跨天**的目标台账，两者用 `parent_task_id` 关联，不重复造轮子。
- 用户面：桌面右栏"任务中心"已存在，将其数据源从"本次会话任务"扩展为 spine 视图。

### 4.2 可撤销行动（让"敢放手"成立）

新增 `agent/core/actions/journal.py`，追加写 `~/.jarvis/actions.jsonl`：

| 类别 | 例子 | 处理 |
|---|---|---|
| 可逆 | 文件写入/删除、移动重命名、系统设置变更、安装软件 | 执行前生成 undo_spec（文件走既有 shadow-git 快照；设置导出旧值；安装记卸载命令） |
| 不可逆 | 发邮件、发消息、提交代码、对外 API 写操作 | 强制 `dry_run` 预览 → 二次确认（语音/桌面）→ **延时发送窗口**（默认 30s 内可撤回） |

- 新增 `/undo`（撤销上一步）与"撤销该任务的全部副作用"（按 `task_id` 批量补偿）。
- 与审计的关系：既有操作审计是**事后记录**，账本是**可执行的补偿计划**，两者共用一条记录。

### 4.3 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/tasks/spine.py`（新增） | 任务模型 + 持久化 + 条件等待 + 续跑 + 退避 |
| `agent/core/actions/journal.py`（新增） | 副作用账本 + undo_spec + 不可逆延时窗口 |
| `agent/tools/collaboration/task_*.py` | 与 spine 关联（`parent_task_id`） |
| `agent/commands/*` | `/undo` `/tasks` 命令 |
| `agent/core/daemon/*` | daemon 启动时恢复 spine 与未决窗口 |
| `tests/core/test_task_spine.py`、`test_action_journal.py`（新增） | 条件等待命中、重启续跑、撤销/不可逆拒撤销 |

### 4.4 验收

- [ ] 关掉终端再打开，任务仍在跑；到点/条件满足自动推进下一步。
- [ ] 条件等待命中前不重复调 LLM（无轮询烧钱）。
- [ ] 任意任务的副作用可一条命令回滚（不可逆项明确报告"此项无法撤销，已发往 X"）。
- [ ] 不可逆动作在延时窗口内确实未发出，可撤回。

---

## 5. W4 · 体验补齐

### 5.1 口手分离：让实时语音真的能干活

**实测证据**（官方 endpoint，文本触发轮）：

| 工具 schema 数 | `response.created` 延迟 | 结果 |
|---|---|---|
| 2 | 0.67s | completed |
| 38 | 0.89s | completed |
| 120 | 1.65s | completed |
| 294 | 3.45s | completed |
| 450 | 4.89s | completed |

服务端始终会建响应，但延迟随工具数线性增长。294 个工具时 3.4s 的窗口里，
**响应救援（1.8s 补发）与 server_vad 尾音重检**会把它掐死，表现为"半天不回复"——
这正是 [realtime-talk-tools-mode-default-fix.md](../fixlogs/realtime-talk-tools-mode-default-fix.md) 的根因。

两级方案：

- **L1 按轮 tool-RAG（低成本，先做）**：用当前轮意图检索出 top-K（8~12）工具，
  只在这几轮挂上。按上表，≤38 个工具延迟 <0.9s，可接受。
  实现落点：`realtime_tools.py` 新增 `select_tools_for_turn(intent, k)`，
  `set_tools` 支持会话内可更新的工具子集（**注意**：`session.update` 只能在 IDLE 期发，
  需靠"每轮结束后重挂"而非轮内更新）。
- **L2 手口分离（形态正解）**：实时链路只负责**听、答、判意图**；识别到"要动手"即把任务
  交给后台 Agent（复用 `Subagent`），结果以**语音摘要 + 桌面卡片**异步回报，
  期间用户可继续闲聊、不阻塞、不需要等工具跑完。

约束：在 W4 完成并经实机验证前，`realtime_tools_mode` 默认值保持 `builtin`，不再改动。
与 [PLAN-future-realtime-voice-upgrade.md](PLAN-future-realtime-voice-upgrade.md) 的分工：
那份管"待机/唤醒/计费"，本节的 L1/L2 管"能干活的最后一公里"。

### 5.2 结构化长期记忆

现状：`profile_refiner` 产出的是**提炼文本**；管家需要的是**可查询、有时效、有出处**的事实。

- 三元组为主：(人/项目/账号/承诺/偏好) — 关系 — (值/时间)，落 `~/.jarvis/memory/facts.jsonl`。
- 每条带元数据：`source`（哪次对话/哪个文件）、`confidence`、`valid_from/valid_until`、
  `last_confirmed`。过期的偏好自动降权，而不是永久生效。
- **冲突消解**：新事实覆盖旧事实时保留历史链，并在下一次相关对话里主动追问
  （"上次您说周五前要交报告，现在还算数吗？"）。
- 接入既有 MCP 知识图谱（画像记忆）而不是另起一套存储。

### 5.3 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/voice/realtime_tools.py` | `select_tools_for_turn()`（tool-RAG） |
| `agent/voice/realtime_engine.py` | 每轮重挂工具子集；"要动手"意图 → 转交后台 |
| `agent/ui/workbench/engine.py` | 后台执行结果的异步卡片回报 |
| `agent/core/memory/facts.py`（新增） | 结构化事实存储 + 时效 + 冲突链 |
| `agent/core/memory/profile_refiner.py` | 提炼结果同时写入结构化事实 |
| `tests/voice/test_tool_rag.py`、`tests/core/test_facts_memory.py`（新增） | 选择命中率、时效降权、冲突消解 |

---

## 6. 排期与规模

| 阶段 | 内容 | 规模 | 新增依赖 | 依赖前置 |
|---|---|---|---|---|
| **W1** 判断层 | 策略引擎 + 租约 + 打扰预算 + away mode | M | 无 | — |
| **W2** 感知层 | 事件总线 + 4 个新事件源 | M | 无（IMAP 用标准库 `imaplib`） | W1 |
| **W3** 兑现层 | 任务脊柱 + 副作用账本 + `/undo` | L | 无 | W1、W2（条件等待需要事件） |
| **W4** 体验补齐 | tool-RAG + 手口分离 + 结构化记忆 | M | 无 | W3（异步回报复用任务脊柱） |

每阶段的共同要求：单元测试 + 真实场景走查 + 修复复盘（依 `.trae/rules/`）。

**明确不做（本期）**：

- IoT / 智能家居接入（除非定位从"电脑管家"扩到"居家管家"，见第 8 节待决策）。
- 云端全时环境感知（计费量级跳档，沿用 voice 蓝图的 L3 判断）。
- 更换/引入第三方调度或工作流引擎（既有 `Scheduler` + 自研 spine 足够，避免重依赖）。

---

## 7. 里程碑验收总表

- [ ] W1：策略四态可解释 + 租约过期即回 `ask` + 静默时段聚合 + 权限零回归
- [ ] W2：四类事件各产出恰好一条聚合提醒 + 常驻 CPU < 1% + 单源故障隔离
- [ ] W3：重启续跑 + 条件等待不烧轮询 + 副作用可回滚 + 不可逆有延时窗口
- [ ] W4：按轮挂 ≤12 工具时建响应 < 1s + 语音说"帮我查一下 XX"能异步拿到结果 + 记忆带时效与出处

---

## 8. 待决策项（需你拍板）

| # | 问题 | 影响 |
|---|---|---|
| 1 | 定位边界：只做"电脑管家"，还是包含居家/IoT？ | 决定 W2 事件源是否扩到 Matter/Home Assistant，是量级差异 |
| 2 | 是否允许常驻后台事件源默认开启？ | 隐私与体验的权衡；建议"安装后首次询问 + 逐源开关" |
| 3 | 邮件集成方式：IMAP 只读轮询/IDLE，还是走既有邮件工具的 API 通道？ | 决定 W2 的实现复杂度与权限提示频率 |
| 4 | 重要度评分是否引入本地小模型？ | 纯规则省资源但笨；小模型更准但增依赖与常驻占用 |
| 5 | W1 效果语义 `dry_run` 是否同时用于工具层（而不只是通知层）？ | 影响与既有五层权限管线的耦合面 |

---

## 9. 与既有路线图的关系

本计划**不是新增愿望单**，而是给既有条目补地基并排序：

| 既有条目 | 在本计划中的落点 |
|---|---|
| P2-1 用户在场感知 | W1.2 away mode（判定复用 `VisionWatcher`） |
| P2-3 主动提醒系统（已完成） | 保留四源；W1 在其 `on_notify` 前加预算闸门，W2 扩源 |
| P2-5 邮件/日历/消息集成 | W2 事件源（只读订阅先行，写操作走 W3 账本） |
| P2-6 文件系统守望 | W2 `file` 源 |
| P2-7 习惯学习 | W1.3 重要度评分中的"历史接受率" |
| P2-8 智能待办 | W3.1 任务脊柱 |
| P3-4 自主任务规划 | W3.1 + W3.2（无条件等待与撤销，不敢自主） |
| P4-4 可信自主执行 | = W1（策略 + 租约）+ W3.2（可撤销），二者齐备才成立 |
| P4-2 预测性服务 | W2 + W1 的长期结果，不单独排期 |

**需要同步修正的既有表述**（本轮实测发现的不实描述）：

- 维度 6 与 P1-9「实时语音 Function Calling（已完成）」中的"ToolRegistry 全部工具接入"，
  与现状不符：默认只注册 2 个内置工具（见 [fixlog](../fixlogs/realtime-talk-tools-mode-default-fix.md)）。
  全量工具不是"已完成"，而是 W4.1 待解决项。

---

## 10. 关联现状文档

- 蓝图（北极星 · 能力模型 · 演进路线）：[BLUEPRINT.md](../BLUEPRINT.md)
- 主动提醒既有实现：[PLAN-p2-proactive-reminder.md](PLAN-p2-proactive-reminder.md)
- 沙箱与回滚：[PLAN-p3-sandbox-execution.md](PLAN-p3-sandbox-execution.md)
- 实时语音待机/唤醒/计费：[PLAN-future-realtime-voice-upgrade.md](PLAN-future-realtime-voice-upgrade.md)
- 实时语音工具模式故障复盘：[realtime-talk-tools-mode-default-fix.md](../fixlogs/realtime-talk-tools-mode-default-fix.md)
- 关键代码：`agent/core/daemon/{proactive,scheduler,vision_watcher}.py`、
  `agent/permissions/`、`agent/core/memory/`、`agent/voice/realtime_tools.py`
