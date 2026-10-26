# 管家形态升级计划（阶段 8–11）：从被动执行器到主动且可信的代理

> 状态：**W1（阶段 8）已开发完成**（分支 `feat/stage8-policy`，落地复盘见
> [16-W1判断层](../architecture/16-W1判断层.md)），已随 v2.2.0 → v2.2.3 发布。
> **W2（阶段 9）已开发完成**（分支链 `feat/stage9-percept` → `fix/notify-budget-speech`
> → `fix/percept-observability`，已全部合入 master），随 **v2.2.5** 发布；落地复盘见
> [17-W2感知层](../architecture/17-W2感知层.md) 与 `docs/fixlogs/` 下三份感知层复盘。
> **W2 的三项前置决策已于 2026-10 拍板**（定位边界 / 逐源默认关 / IMAP IDLE，见第 8 节）。
> **W3（阶段 10）后端已开发完成**（副作用账本 + 不可逆动作延时窗口 + `/undo`，
> 任务脊柱 + 驱动器 + 宿主接线 + `/tasks`（含写入子命令），加上建目标的三个入口：
> `/tasks add` / `TrackGoal` 工具 / serve `tasks.exec`+`actions.exec` RPC，共 374 例单测，
> 并有两条一键实机命令：`scripts/smoke_w3_spine.py`（40 项、不经模型）与
> `scripts/livecheck_w3.py`（12 项、**真 LLM 跑穿到点自动执行 + 副作用入账 + 按目标撤销**）；
> 落地复盘见 [18-W3兑现层](../architecture/18-W3兑现层.md)、
> [w3-journal-spine-delivery](../fixlogs/w3-journal-spine-delivery.md)、
> [w3-entry-rpc-delivery](../fixlogs/w3-entry-rpc-delivery.md) 与
> [w3-driver-approval-blocked-report](../fixlogs/w3-driver-approval-blocked-report.md)；
> **到点真执行已于 2026-10-22 实机跑穿**（当场抓出四条断链并全部修掉）；同日补上「批准并重试」
> 的真通路（`tasks.exec approve`，签限定范围租约），**兄弟仓 jarvis-desktop 也已接完右栏
> 「跨会话目标」与「副作用账本」两个区块**（那边 524 例 vitest 全绿），只剩桌面实机渲染待人工看。
> **W4 尚未开工**（本文档只做设计、排依赖、切片，不代表已开发）。
> 定位：既有的 P1/P2/P3 清单是"能力愿望单"，本文档回答一个更前置的问题——
> **已有的零件（调度器/感知器/记忆/审计/沙箱）之间缺哪几个接口，才能让"主动陪伴"和"无人值守"真的成立。**
>
> @author aceFelix

---

## 0. 问题陈述

横向能力已经不缺：100+ 工具、双模语音、三层记忆、五层权限、沙箱、多端同会话、桌面壳都已落地
（见 [BLUEPRINT.md](../blueprint/BLUEPRINT.md) 的现状自评与能力模型）。但"真正的管家"体验仍然不成立，卡在三处**接口**上：

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
| 感知 | `Scheduler`（`~/.jarvis/schedule.json` 持久化 + 错过补偿）、`VisionWatcher`（mediapipe 手势/人脸事件流）、`SystemMonitor`（阈值告警 + 600s 冷却）、`ProactiveEngine`（简报/截止/日历四源）、**W2 统一事件总线 + 五个现场源**（阶段 9） | 日历/系统/定时三个收编位未装配；总线只在 `--serve` / 桌面壳期间存在（终端 REPL 不装） |
| 判断 | 五层权限管线（fail-closed）、`permissions.yaml`、`max_escalate` 防轰炸、**W1 授权租约 + 打扰预算 + 无人值守**（阶段 8） | 待批重放只有编程入口、未接命令面板；接受率闭环靠审计数据，尚未回流到权重 |
| 兑现 | `ScheduleTask`（once/daily/weekly）、`Subagent`/`Team`/`TaskList`（单次查询内）、沙箱 + shadow-git 文件回滚、操作审计、**W3.1 任务脊柱 + W3.2 副作用账本**（阶段 10 后端） | 脊柱缺模型侧创建入口与桌面视图；token/费用未归集；设置/安装类副作用本期未记账 |
| 记忆 | `store` / `profile_store` / `profile_refiner` / `prune` / `file_state` / `compactor` | 无实体-关系-时间结构化；无时效与出处；无冲突消解 |

---

## 1. 三支柱总览

```
   感知层 W2（事件）            判断层 W1（闸门）             兑现层 W3（行动）
 ┌─────────────────┐        ┌──────────────────┐        ┌──────────────────┐
 │ 时钟（已有）      │        │ 规则放宽          │        │ 任务脊柱          │
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

> ✅ 本节已于阶段 8 落地（`agent/core/policy/`）：授权租约、打扰预算五步
> 闸门、/away 无人值守待批清单全部实现并有单测。
> **2026-10 降级合并修订**：初版实现的策略引擎（engine.py + policy.yaml
> 四态规则）在人工实测复盘后认定为与五层管线平行重复的过度设计，已删除
> ——本节 ① 的声明式能力（allow 放宽 ASK + path_within 路径作用域）改进
> 了管线本身（`permissions/checker.py` + `permissions.yaml`），而不是另套
> 一层；dry_run 随引擎删除退出本期（工具层真拦截归 W3.2 副作用账本）。
> 与设计的其余差异：away 视觉事件源（人脸离开）留给 W2；待批重放执行
> 留给 W3。**2026-10 实测补丁**：/away 会压制沙箱中风险自动放行（升级
> 待批），避免"mv 重命名做了、后续 FileEdit 被拦"的半完成状态。实现
> 口径详见 [16-W1运行时层](../architecture/16-W1判断层.md)。
> **2026-10 实测补丁二（空转防护）**：沙箱中风险自动放行拆掉行为刹车后，
> 实测出现"拿记忆假线索反复全盘检索 + 每轮思考占满 max_tokens 截断续写"
> 导致 15 分钟零产出。新增两道确定性兵闸：同型工具调用循环检测（≥3 次
> 强制 ASK）+ 截断续写熔断（连续 >2 次则停）；记忆同时降级为"仅作线索"
> 口径。详见 [agent-loop-stall-fix](../fixlogs/agent-loop-stall-fix.md)。

### 2.1 现状与问题

- 高风险操作的确认目前由 **instructions 引导模型自行判断**（`realtime_talk.default_instructions()` 与
  主 prompt 都是这个路子）。模型判断不可靠，且不可审计。
- 权限系统（`agent/permissions/`，五层管线）擅长回答"这个工具调用能不能做"，
  但回答不了"**这次调用值不值得打断用户**"以及"**在无人值守时该不该做**"。

### 2.2 设计

**① 策略表（policy-as-code）** — 新增 `agent/core/policy/engine.py`，规则源 `~/.jarvis/policy.yaml`：

> ⚠️ 降级合并修订：本小节按原设计落地后已回退。复盘结论：用户不会为了
> 免确认再学一套平行规则 DSL，且其能力permissions.yaml 可以表达——正确做
> 法是优化五层管线。下列设计意图由 `permissions.yaml` 带 spec 的 allow
> 规则 + 路径归一匹配承接（effect 对应 allow/ask/deny，dry_run 归 W3.2）。

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

- 与既有五层管线的关系（已按降级合并修正）：不再新增平行规则源，而是
  补齐管线缺失的放宽语义：带 spec 的 allow 规则命中可放宽工具默认/特判
  ASK（旧 `_stricter` 只收紧，allow 永远压不过 ASK），默认值仍 fail-closed。

**② 授权租约（lease）** — `agent/core/policy/lease.py`，落盘 `~/.jarvis/leases.json`：

```json
{"id": "…", "scope": {"path_within": ["~/work/reports/**"], "tools": ["FileWrite"]},
 "granted_at": "…", "expires_at": "…", "granted_by": "user", "note": "这批报告今天随便改"}
```

- 命中租约 → 免打断直接执行；`expires_at` 到期自动失效。
- 用户可一屏查看/一键撤销所有生效租约（桌面设置面板 + `/lease list|revoke`）。
- 这是**减少打断的主要手段**：先说"接下来 2 小时允许动 `~/reports`"，而不是每次都弹确认。

**③ 打扰预算（notify budget）** — `agent/core/policy/budget.py`：

- `daily_max`（每日主动播报次数上限）、`quiet_hours`（静默时段只入库不打扰；
  **用户自设提醒例外**，2026-10-22 改为降级上屏不发声，见 §3.1 注）、
  `aggregate_window_s`（同源事件 5 分钟内合并成一条）、`min_salience`（低于门槛不播报）、
  `channels`（桌面通知 / TTS / 手机推送 的优先级与降级顺序）——设计里的 `channels`
  到 2026-10-22 只落地了提醒那一段（`reminder_channels = ["screen", "email"]`，
  短信通道留位 `sms` 未接入），全量渠道优先级仍是待办。
- **重要度评分** = 来源权重 × 时效紧迫度 × 与当前活跃任务的相关性 × 该来源历史接受率
  （接受率来自审计日志，形成"越用越懂你"的闭环）。
- 落点：`ProactiveEngine` 的 `on_notify` 之前插入 budget 过滤，而不是在每个提醒源里各写一遍。

**④ 无人值守模式（away mode）**：

- 判定：复用 `VisionWatcher`（人脸离开）+ 键鼠活动空闲时长，或手动 `/away`。
  ✅ 阶段 9 已接：视觉不在场由 `source_vision.py` → `set_presence_away` 驱动（带 30 分钟
  有效期，手动 `/away` 不被摄像头取消也不过期）；空闲推断仍由 `away_idle_minutes` 控制。
- 行为：away 期间只执行 `allow` 级策略 + 记入待办队列；不可逆动作一律排队等回来批。
- 回归：一条事后简报——"先生，您不在期间我处理了 3 件事，其中 1 件待您确认"，附可撤销入口。

### 2.3 涉及文件

| 文件 | 改动 |
|---|---|
| ~~`agent/core/policy/engine.py`~~（已删除） | 降级合并：声明式规则改进管线 `agent/permissions/checker.py`（`_relax_by_rule` + `path_match.py`） |
| `agent/core/policy/lease.py`（新增） | 租约签发/校验/过期/撤销 |
| `agent/core/policy/budget.py`（新增） | 打扰预算 + 重要度评分 + 聚合/静默 |
| `agent/core/daemon/proactive.py` | `on_notify` 前插入 budget 过滤；away mode 分流 |
| `agent/permissions/*` | 规则放宽语义升级（带 spec allow 可放宽 ASK）+ 路径归一匹配 |
| `agent/ui/workbench/engine.py` | 待批清单面板 + 租约管理 |
| `agent/config/settings.py` + `configs/permissions.yaml` | `[policy]` 配置项（运行时层字段）与放宽规则示例注释 |
| `tests/test_permissions_relax.py`（新增） | allow 放宽/保守红线/路径 glob 回归 |

### 2.4 验收

- [ ] 同一操作在"有租约/无租约/away"三种情境下得到确定的三种结果，且可解释（命中哪条规则）。
- [x] 静默时段内的**简报/感知事件**只入库不播报、解除后聚合为一条；
  **用户自设提醒**则降级上屏（气泡在、不发声）——2026-10-22 实测后拆成的两半，
  原验收项只写了前者、把后者当成“不该纳闸”的理由，已在 §3.1 纠正。
- [ ] 租约过期后行为立刻回到 `ask`，无需重启。
- [ ] 既有权限行为零回归（五层管线测试全绿）。

### 2.5 风险

规则漏配导致误放行 → 管线默认 fail-closed（未命中任何 allow 就问用户），
且保守红线不放开：DENY 永不可放宽、裸工具名 allow 不能放宽工具特判 ASK。

---

## 3. W2 · 感知层：统一事件总线

> 🔄 **进度（2026-10-16，后端分支 `feat/stage9-percept` / 桌面分支
> `feat/stage9-percept-panel`）**：总线 + 五个源 + 宿主接线 + **`percept.*` RPC**
> + **桌面「感知源」面板 UI** + **首次启动询问** 已落地
> （`agent/core/percept/{event,bus,factory,source_window,source_file,source_vcs,source_mail,
> source_vision,status,consent,__init__}.py`，单测 38 + 30 + 30 + 10 + 17 + 22 + 8 + 16 + 17 + 13 例
> + W1 在场推断 5 例 + `toml_writer` 9 例 + 桌面侧 36 例；`[percept]` 与 `[email].imap_*`
> 配置已落 Settings、`settings_domains.py` 声明式映射与 example.toml），架构说明见
> [17-W2感知层](../architecture/17-W2感知层.md)。
> ✅ **第 4 步已接上**：serve `ProactiveHub` 装配总线并订阅，感知事件经
> `ProactiveEngine.gate_notify`（从 `_dispatch_notify` 拆出的同一个闸门）投
> `proactive_notify(kind=percept)`；人脸离开/回来同时驱动 W1 的 away 推断。
> ✅ **第 5 步已接上**：`percept.status.get`（在采什么 / 采到了什么 + `config` 段）与
> `percept.config.set`（逐源开关 + 名单）两条 RPC 已上协议；面板改完开关由宿主
> **重装配总线即时生效**，不再需要重启桌面壳。
> ✅ **第 6 步已接上**：jarvis-desktop 「管家服务 → 感知源」面板已接入（逐源开关/
> 采集名单/最近事件三区），双仓契约（`contracts.ts` 两条 `Cmd` +
> `percept-contracts.ts`）已同步。
> ✅ **第 7 步已接上（首次启动询问）**：`core/percept/consent.py` 记录“问过没/怎么
> 表态”（`~/.jarvis/percept/consent.json`），只在从未做过选择时把引导装进 hub 的
> pending 槽，等桌面壳连上 WS 的首帧才投递并记账；壳侧给「管家服务」按钮挂一次性
> 提示点作着陆点。引导到达时仍全关，不擅自开始采集。
> ✅ **两轮人工实机走查修复（2026-10-18 / 10-19）**：打扰预算的“入队-冲刷”
> 乒乓与播报改走口播版（[复盘](../fixlogs/notify-budget-percept-relay-fix.md)）；
> 面板观测口易失（改一次配置就重建总线、历史与计数归零）与静默时段绕过
> 重要度门槛（[复盘](../fixlogs/percept-panel-history-lost-on-rebuild-fix.md)）。
> 上行是交付当时的快照，**当前计数以架构文档为准**：`test_percept_bus` 47、
> `test_percept_init` 12、`test_percept_status` 13、`test_hub_percept` 24、
> `test_notify_budget` 18、`test_percept_speech` 9、`test_percept_sources_catchup` 19，
> 桌面 `percept.test.tsx` 35。
> 实机走查清单见 [W2 感知层实机走查清单](../test/percept-w2-testing.md)。
> ✅ **阶段 11 收编**：`calendar` / `system` 两个休眠件已接进总线
> （`source_calendar.py` / `source_system.py`，面板同步开开关）；
> 仍未收编的只剩 `schedule`——它的提醒已经过 W1 闸门播报，再进总线会重复播报
> （理由见架构文档第七节）。

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
| 文件守望 `file` | **自建目录快照**（白名单目录 walk + mtime/size 差分落 `~/.jarvis/percept/file_state.json`） | 只订阅白名单目录；纯轮询差分（不依赖 inotify） |
| 仓库 `vcs` | `git status`/`git stash list` 只读差分（✅ 已实现） | 最小间隔 60s；仅订阅登记过的仓库；只在“刚跨过提醒线”那一档发一次 |
| 邮件 `mail` | IMAP 只读 IDLE 长连接，断线自动降级为定时轮询（✅ 已实现，决策 3） | 只取信封头（发件人/主题/时间/Message-ID），不拉正文、不置已读；已报键落盘去重 |
| 日历 `calendar` | 已有 `CalendarSource` 升级为事件源（✅ 阶段 11 已接总线：仍不起线程，由总线按间隔拉取） | 每 30 分钟 |
| 系统 `system` | 已有 `SystemMonitor`（✅ 阶段 11 已接总线：只调 `check_now()` 拿快照） | 沿用 600s 冷却 |
| 视觉 `vision` | 已有 `VisionWatcher`（mediapipe 手势/人脸）——✅ 本期已接总线 | 默认 2fps；在场类事件不播报、只驱动 away 推断 |

> ⚠️ **与代码事实的修正**（2026-10 实现总线时核实）：`file` 行原文为"复用
> `core/memory/file_state.py` 的目录快照"，但 `file_state.py` 实际是**单文件 mtime
> 冲突缓存**（挂在 `ToolContext.extra` 上、进程内、随会话结束消失，用于拒绝基于
> 过时内容的编辑），**没有目录扫描能力**。file 源必须自带快照实现，不能复用。

> ⚠️ **第 3 步范围修正**（2026-10-16 实现 vcs 源时核实）：`vcs` 行原列“构建/CI 结果
> 尾行”**本期未做**，且已写进代码与架构文档：它要么依赖外部 `gh` CLI 与 GitHub
> 凭据（违背决策 1“只本机采集、不引入外部账号授权”），要么由 jarvis 自己跑构建
> （属 W3 兑现层的主动执行）。本期 vcs 只做本机 git 可得到的四种事实，不假装已接 CI。

- **Scheduler 本期不并入总线**（原设计“`schedule.fired` 也进总线”已推翻）：
  提醒仍是“到点就送到”，不进感知事件流、不与观察类事件拼成一条摘要。
  但下面这半个结论已于 **2026-10-22 推翻**：原写“现有路径（`_on_fire` 直投事件）
  保持不变”，实测发现它把“提醒不该延后”连“提醒不该受任何额度约束”一起定了——
  静默时段形同虚设（设三条就响三条）。现在的口径：**提醒纳闸但不延后**——
  命中静默/超额时走**降级投递**（气泡与系统通知照投、TTS 不朗读、不占日额度），
  即“准点”由上屏保证，“不打扰”由不出声保证；通道见
  `[policy.notify_budget] reminder_channels`（机制详见
  [16-W1 判断层](../architecture/16-W1判断层.md) 第四节）。
- 成本控制硬约束：**本地规则/阈值先判，不每次调 LLM**；只有通过 salience 门槛的事件才进
  一次轻量 LLM 生成话术（可离线降级为模板文案）。
- 隐私：每个源独立开关 + 状态可见（"正在观察：前台窗口 / 构建状态"）+ 数据仅本地落盘、
  不随对话上传；采集范围可一键导出查看。

### 3.2 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/percept/event.py`（新增） | `PerceptEvent` 数据契约 + 源名常量 + payload 指纹/截断 |
| `agent/core/percept/bus.py`（新增） | 事件模型 + 订阅/分发 + 去重 + 节流 + 两层故障隔离 + 留痕/导出 |
| `agent/core/percept/source_window.py` 等（新增，一源一文件） | 各采集器：`source_window.py`、`source_file.py`、`source_vcs.py`、`source_mail.py`、`source_vision.py` 均已落地 |
| `agent/core/daemon/proactive.py` | ✅ 已从 `_dispatch_notify` 拆出 `gate_notify`（W1 闸门唯一实现），感知事件与简报走同一个闸；既有四源行为不变 |
| `agent/core/percept/status.py`（新增） | ✅ `build_status`：配置 + 总线统计 + 源自报 + 最近事件的一份面板快照（中文标签与源说明的真源） |
| `agent/config/percept_settings.py`（新增） | ✅ 面板可改的 `[percept]` 白名单（开关 bool / 名单 list / 分钟 int）+ 校验 + 落盘 + 运行时生效；极客参数与未装配开关一律不进 |
| `agent/config/toml_writer.py`（新增） | ✅ “单节多字段外科式写入 + 字面量转义”从 `desktop_settings` / `email_settings` 两份同构实现抽成唯一实现（第三个域落地时不再抄第三遍） |
| `agent/serve/rpc_email.py` / `rpc_extensions.py`（新增） | ✅ 结构治理：`server.py` 已过 800 行上限（808），按「一族指令一个混入」拆出 email.* 与 extensions.exec，行为与回执口径不变 |
| `agent/serve/rpc_percept.py`（新增） | ✅ `PerceptRpcMixin`：两条感知指令的处理器（先落盘 → 运行时 → 重装配 → 回读） |
| `agent/serve/protocol.py` + `serve/server.py` | ✅ `percept.status.get` / `percept.config.set` 上协议（`DESKTOP_COMMANDS` 51→53），安全范式同 `email.config.*` |
| `agent/serve/hub.py` | ✅ W2 接线点：`_start_percept`/`_stop_percept`/`_on_percept_event`/`_apply_presence`；+ `hot_update_percept()`（停旧总线→按新配置重装配） |
| `agent/core/daemon/vision_watcher.py` | ✅ 事件接入总线（新增 `set_event_handler` 供感知源改接回调），不再休眠 |
| `agent/config/settings.py` + `configs/settings.example.toml` | `[percept.*]` 开关与白名单（**逐源默认关**，决策 2）；`[email].imap_*` 收信字段（决策 3，已落） |
| `agent/config/settings_domains.py`（新增） | 拆分副作用：`_apply_toml` 里 20+ 个同构“表→字段”循环改为声明式映射（settings.py 因加 `[percept]` 突破 800 行上限，拆回 644 行） |
| `agent/config/email_settings.py` | `EMAIL_FIELDS` 新增五个 `imap_*` 协议键（收件配置可与发件同一张面板管理；`imap_password` 与 `smtp_password` 同为 secret：读不回明文、写留空=保持原值）；落盘改为委派 `toml_writer.save_section` |
| `agent/core/policy/state.py` | ✅ `set_presence_away`（手动优先、幂等）+ `is_away` 采信视觉推断并带 30 分钟有效期（`VISION_AWAY_TTL_MIN`） |
| `tests/core/test_percept_bus.py`、`test_percept_sources.py`、`test_percept_sources_vcs_mail.py`、`test_percept_sources_vision.py`、`test_percept_init.py`、`test_percept_status.py`、`tests/config/test_percept_settings.py`、`test_toml_writer.py`、`tests/serve/test_hub_percept.py`、`test_serve_percept_rpc.py`（新增） | 总线裁决 / 窗口与文件 / 仓库与邮件 / 视觉在场 / 配置注入与单例装配 / 面板状态快照 / 感知配置白名单 / 落盘工具 / 宿主接线与热重装配 / 感知 RPC |
| 兄弟仓 `jarvis-desktop/src/shared/percept-contracts.ts`、`stores/perceptActions.ts`、`components/PerceptSource{Panel,List}.tsx`、`PerceptScopeForm.tsx`、`PerceptEventList.tsx`（均新增） | ✅ 第 6 步桌面面板：契约镜像 + 动作工厂（与 `extensionActions` 同范式注入 `backendStore`）+ 逐源开关/采集名单/最近事件三区；`butlerStore` 加 `'percept'`、`ButlerPanel` 改用 `SERVICE_VIEWS` 映射表；文案拆到 `i18n-butler.ts`（`i18n.ts` 因越 800 行上限按业务域先拆后接新）；测试 `test/renderer/percept.test.tsx`（31 例） |

### 3.3 验收

- [ ] 构造四类事件（构建失败 / 目录堆积 / 前台切到某应用超时 / 收到特定来件），
      经 W1 预算后各自产出**恰好一条**聚合提醒。
- [ ] 常驻 CPU 增量 < 1%（空载 10 分钟均值），无源开启时为 0。
- [ ] 任一源异常不影响其他源（沿用 `ProactiveEngine` 的优雅降级惯例）。

---

## 4. W3 · 兑现层：持久任务脊柱 + 可撤销行动

> ✅ **本节已于阶段 10 落地（后端切片，2026-10-21）**：`agent/core/actions/journal.py`、
> `agent/core/tasks/{spine,spine_ops,spawn,driver,cli_host}.py`、`utils/filelock.py`（从 `task_list.py` 提升）、
> `Tool.is_irreversible` 声明位、`orchestrator` 统一拦截、`/undo`、`/tasks`（两节 + 写入子命令）、
> `TrackGoal` 工具、serve `tasks.exec`/`actions.exec`（`agent/serve/rpc_tasks.py`）、
> `[actions]`/`[tasks]` 配置，以及 `budget.py` 登记 `task` 源权重（不登记就落 default 被误裁）。
> 单测 374 例（账本 30 / 编排接入 31 / 绕道硬拦 18 / 台账 35 / 入口层 45 / 驱动器 39 /
> `/undo` 21 / `/tasks` 34 / `TrackGoal` 31 / RPC 32 / 一键走查脚本 4 / 实机修复回归 54）。
> “不经模型”的那一半已用 `scripts/smoke_w3_spine.py` 实机跑绿（40/40，2026-10-21）；
> 到点真执行与驱动器推进步骤已于 **2026-10-22** 用 `scripts/livecheck_w3.py` 实机跑穿
> （12/12，真 LLM 写文件→入账→按目标撤销→文件消失），那一次跑出四条断链（子代理不继承
> 宿主规则、受阻谎报已完成、后台改动不入账、`[actions]` 对后台无效）已全部修掉并各带回归，见
> [w3-driver-approval-blocked-report](../fixlogs/w3-driver-approval-blocked-report.md)；
> 同日接桌面面板时又补了一刀（「批准」得真能放行 → `approve_step` 签限定范围租约），
> 带 12 例回归；剩下桌面实机渲染那一段需到 jarvis-desktop 那边人工看。
> 实机走查后补的两处（延时窗口被等价路径绕开、`/undo --task` 归属链路断）各带回归用例，见
> [w3-delay-window-bypass-fix](../fixlogs/w3-delay-window-bypass-fix.md)。
>
> **四处与本节原文的设计偏离**（实施时核实后改的，逐条理由见交付复盘）：
> 1. **新增 `is_irreversible` 声明位，不复用 `is_destructive`** —— 后者含"删除/覆盖"，
>    `FileWrite` 也为 True 但可回滚；挂在它上面 = 每次存文件干等 30 秒。
> 2. **`/tasks` 扩成两节，不新建命令** —— slash 表只有一份，同义入口让人先猜哪个是活的。
> 3. **到点不回调完整管线，而是执行账本里冻结的那一份 args** —— 无 UI 时重过管线会被
>    fail-closed 拒成 DENY，把用户刚批准的动作自己拦掉。
> 4. **所有 wait 必带 `deadline_at`** —— 感知逐源默认全关，事件永不到来会让任务永久 blocked；
>    超时转 `blocked_timeout` 并经 W1 闸门问用户，绝不静默挂起。
>
> 另有三处口径修正：台账落 `~/.jarvis/goals/`（避开 `~/.jarvis/tasks/<team>/` 串目录）；
> 重启后遗留窗口**作废而非补发**；`SendMessageTool` **不声明不可逆**（本机文件信箱，
> 没"出了本机收不回"的属性）。架构口径与实施细节以
> [18-W3兑现层](../architecture/18-W3兑现层.md) 为准。

### 4.1 任务脊柱（让"替我盯着"成立）

现状：`ScheduleTask` 只有 `trigger_at + content`，是闹钟不是任务。
新增 `agent/core/tasks/spine.py`，落盘 `~/.jarvis/goals/<id>.json`
（**落地修正**：原文写 `~/.jarvis/tasks/<id>.json`，但 `~/.jarvis/tasks/<team>/` 已被协作
`TaskList` 当团队目录用，同层放目标文件会在团队恰好叫 `goals` 时串目录）：

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
  ✅ 已实现，并一律带 `deadline_at`；判定只走时间比较与本地 `stat`（`percept_event`
  由驱动器订阅回调写回 `spec["hit_at"]`），**不调 LLM、不起进程**。
- **断点续跑**：daemon 重启后恢复；失败按退避重试；阻塞超过 N 天主动来问用户（走 W1 预算）。
  ✅ 已实现：`resume()` 复位遗留 `running` 并计一次失败，退避时刻经 `TaskStep.next_run_at`
  **落盘**（只记内存里会被重启绕过），本期退避为**固定间隔**不翻倍。
- 与既有零件的关系：`TaskList`（多 Agent 协作）= 一次查询内的并行子任务；
  spine = **跨会话、跨天**的目标台账，两者用 `parent_task_id` 关联，不重复造轮子。
  ✅ 已实现：两套存储互不写入，文件锁复用 `agent/utils/filelock.py`（同一套不造第二套）。
- 用户面：桌面右栏"任务中心"已存在，将其数据源从"本次会话任务"扩展为 spine 视图。
  ✅ **入口三件套已交付**（2026-10-21 收尾切片；2026-10-26 补 `/tasks approve`）：
    终端 `/tasks add|show|approve|pause|resume|cancel|advance|delete`、
  模型侧 `TrackGoal` 工具、serve `tasks.exec` / `actions.exec` RPC，三者只调同一层
  `agent/core/tasks/spine_ops.py`（语义拆不走两个口径，见
  [18-W3兑现层 §5.4](../architecture/18-W3兑现层.md)）。
  ✅ **桌面面板数据源已切换（2026-10-22，兄弟仓 jarvis-desktop）**：右栏「跨会话目标」与
  「副作用账本」两个区块直接消费 `tasks.exec` / `actions.exec` 快照，含两拍确认、
  `cancel_hold` 一拍撤回，以及「批准并重试」按钮（走 `tasks.exec approve`）；
  那一侧 524 例 vitest 全绿，**只剩桌面实机渲染待人工看**。本仓不重复维护那边的实现细节，
  口径以 jarvis-desktop 仓文档为准。

### 4.2 可撤销行动（让"敢放手"成立）

新增 `agent/core/actions/journal.py`，追加写 `~/.jarvis/actions.jsonl`：

| 类别 | 例子 | 处理（✅ 已落地的口径） |
|---|---|---|
| 可回滚 | 文件写入/删除、移动重命名（系统设置变更、安装软件本期**未入账**） | 执行后引用**宿主本轮已打的那个** shadow-git 检查点（`undo_spec.kind=checkpoint` + cid）；**不新建快照**（会吃掉每会话 20 条配额、反噬 `/rewind`）；没检查点则宁可不记，也不记一条撤不动的假可撤销 |
| 不可逆 | 发邮件（`SendEmailTool` 已声明）、对外 API 写；`is_irreversible` 默认 False（fail-closed），应急可用 `[actions].irreversible_tools` 按名补 | 权限管线正常判定（DENY 永不放宽）→ 本轮不执行，**预览 + 入 30s 待发窗口** → 到点按冻结 args 真执行；窗口内 `/undo cancel` 可拦；重启后遗留窗口一律作废 |

- 新增 `/undo`（撤上一步）/`<id 前缀>`/`list`/`cancel [id]`/`--task <id>`（按目标批量补偿）。
  四条路径共同守住两条如实性：**撤不动的照样列出来并给原因**；文件类回退是**整轮**粒度，
  所以展示影响面、问过用户才落手。
- 与审计的关系：既有操作审计是**事后记录**，账本是**可执行的补偿计划**，两者共用同一次调用、
  各写自己的文件（互不代替）。

### 4.3 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/tasks/spine.py`（新增） | 任务模型 + 持久化（临时文件 + `os.replace` + 跨进程锁）+ 三种条件等待 + deadline |
| `agent/core/tasks/spine_ops.py`（新增） | 建/管目标的**共用业务层**：`split_steps` / `parse_wait` / `wait_from_spec` / `create_goal` / `control_goal` / `find_goal` / `snapshot`，三个入口只调这一层 |
| `agent/core/tasks/driver.py`（新增） | 后台一条线程：到期唤醒 + 总线订阅 + `run_subagent` 推进 + 退避/询问 + 崩溃复位 |
| `agent/core/tasks/cli_host.py`（新增） | `CliSpineHost`：REPL 侧 W3 装配体（遗留作废 + 驱动器起停），与 `CliReminderHost` 同构 |
| `agent/core/actions/journal.py`（新增） | 副作用账本 + `undo_spec`（只有 `checkpoint`/`none` 两种形态）+ 延时窗口 + `resume_pending` |
| `agent/utils/filelock.py`（新增） | `ensure_dir` + `file_lock`：从 `collaboration/task_list.py` 提升，脊柱与协作任务共用一套锁 |
| `agent/core/tool.py` + `agent/core/orchestrator.py` | `is_irreversible`/`describe_irreversible` 声明位；`_run_one` 在权限判定与钩子之后 hold，必回**非 error** 的 `tool_result` |
| `agent/tools/extensions/email_tool.py` | `SendEmailTool` 声明不可逆并给含收件人的预览文案；`SendMessageTool` 经核实**不声明** |
| `agent/commands/handlers/undo_commands.py`（新增）+ `router.py` + `ui/slash_completer.py` | `/undo` 四条路径上命令表与补全面 |
| `agent/commands/handlers/collab_commands.py` | `/tasks` 扩成两节（协作 `TaskList` + 跨会话 spine），并把只读命令重写为子命令分发（`add`/`show`/`pause`/`resume`/`cancel`/`advance`/`delete`/`help`） |
| `agent/tools/track_goal.py`（新增） | `TrackGoal` 工具：模型侧一句话建/管目标，8 个 action，**只有 `delete` 要确认** |
| `agent/serve/rpc_tasks.py`（新增） + `protocol.py` + `server.py` | serve `tasks.exec` / `actions.exec`（协议指令 53 → **55 条**），写操作未带 `confirm=true` 只回预览；`tasks.exec` 后补 `approve` 动词（指令条数不变，一拍生效），签限定范围租约放行被 ASK 拦住的步骤 |
| `agent/prompts/system.py` + `agent/prompts/text_blocks.py` | 根基础提示补 `TrackGoal` 用法行；新增按工具注册表条件注入的目标脊柱口径段；`system.py` 因超 800 行拆出逐字静态正文 `text_blocks.py`（行为不变，4 例护栏锁） |
| `agent/serve/hub.py` + `agent/main.py` | 两个宿主的装配与起停；遗留窗口作废逐条播报 |
| `agent/core/daemon/proactive.py` + `agent/core/policy/budget.py` | 闸门实现上提为 `apply_notify_gate`（`gate_notify` 变薄包装）；登记 `task` 源权重 0.9 |
| `agent/core/checkpoint.py` | 新增 `manager_from_settings` 工厂（三处宿主各写一份构造 → 收拢）；实机修复切片再加 `kind` 分族配额（`create(reason, *, kind="turn")` + `task_max_checkpoints`，任务级不挤占 `/rewind`） |
| `agent/core/tasks/approval_ops.py`（新增，167 行） | 阻塞与授权从 `driver.py` 拆出：`_block`/`_ask`/`request_approval`/`APPROVAL_HOLD_S`/`blocked_hold()`；`TaskDriver(ApprovalOpsMixin)` 继承后调用点一字不改（守 800 行上限） |
| `agent/collaboration/subagent.py` + `agent/core/tasks/spawn.py` | 子代理继承宿主 `permissions_file` 规则与 `settings`（不再写死空 `RuleSet()`）；步骤跑前打 `kind="task"` 检查点并把 cid / 拒绝清单交给编排器与驱动器 |
| `tests/core/test_task_spine.py`、`test_task_driver.py`、`test_action_journal.py`、`test_orchestrator_irreversible.py`、`tests/commands/test_undo_commands.py`、`test_tasks_command.py`、`tests/core/test_spine_ops.py`、`tests/tools/test_track_goal.py`、`tests/serve/test_serve_tasks_rpc.py`、`tests/test_w3_smoke_script.py`、`tests/core/test_orchestrator_denials.py`、`test_driver_approval_blocked.py`、`test_checkpoint_task_quota.py`、`test_w3_step_side_effects.py`、`tests/collaboration/test_subagent_permission_rules.py`（均新增） | 共 **374 例**（含走查后补的绕道硬拦与归属接线用例、收尾切片的入口层 45 + 工具 31 + RPC 32、实机修复回归 54）：条件等待命中、重启续跑、等待/退避期 LLM 零调用、hold 必回非 error 结果、撤销/不可逆拒撤销、受阻不落 done、有产出却夹着被拒→落 done 并附说明 |
| 兄弟仓 `jarvis-desktop/src/shared/tasks-contracts.ts`、`stores/taskActions.ts`、`stores/tasksStore.ts`、`components/SpineTaskPanel.tsx`、`EffectJournalPanel.tsx`、`styles/task-center.css`、`i18n-tasks.ts`（均新增） | ✅ 右栏「任务中心」两个区块：契约镜像（宽容解析，字段缺失不炸渲染）+ 动作工厂（与 `perceptActions` 同范式注入 `backendStore`，`refreshTasks` 一次并发拉两份快照）+ 目标台账面板 + 副作用账本面板；文案按业务域拆出 `i18n-tasks.ts`；结构治理同批——`backendStore.ts` 越 800 行上限拆出 `stores/butlerActions.ts`（719 行），`test/renderer/dispatcher.test.ts` 越限按事件域拆成两个文件 + 共用夹具 `dispatcherFixtures.ts`；测试 `test/renderer/tasks.test.tsx`（30 例） |
| `scripts/smoke_w3_spine.py`（新增，619 行） | 一键实机走查：三个入口 + 延时窗口 + 绕道硬拦 + 两条 RPC 共 40 项，不经 LLM、台账/账本只写临时目录（`--no-serve` 可跳过 WS 段） |
| `scripts/livecheck_w3.py`（新增，398 行） | 一键实机自检：真 LLM 跑穿“到点自动执行 + 副作用入账 + 按目标撤销”共 12 项；只改内存 Settings 不落盘用户配置，跑完自清理（`--wait` 控等待时长） |

### 4.4 验收

- [ ] 关掉终端再打开，任务仍在跑；到点/条件满足自动推进下一步。
  ——单测已锁（`test_resume_resets_stale_running` + 重启后继续推进）；后半句
  “到点自动推进”已于 2026-10-22 实机跑穿（blocked→running→done 全程无人干预），
  前半句“换个进程再打开”仍只有单测（实机自检不重启 serve）。
- [x] 条件等待命中前不重复调 LLM（无轮询烧钱）。
  ——单测直接断言等待期/退避期 spawn 计数为 0（`TestNoPollingBurn`）；实机已证：
  等待那 40 余秒只读到一次状态查询，驱动器靠到期时刻唤醒，不烧轮询。
- [x] 任意任务的副作用可一条命令回滚（不可逆项明确报告"此项无法撤销，已发往 X"）。
  ——`/undo --task` + checkpoint `undo_spec`，拒撤项带原因（有单测；回退粒度是**整轮**）；
  实机已证：后台步骤写的文件进了账本、`journal_ids` 回填、`actions.exec undo_task`
  两拍把工作区真撤回去（文件消失）。
- [x] 不可逆动作在延时窗口内确实未发出，可撤回。
  ——`test_orchestrator_irreversible` 断言“被 hold 时工具一次都没被调用”+ `/undo cancel`；
  2026-10-21 已实机跑过发件窗口一段。

> ⚠️ **四条验收中已有三条拿到实机证据**（2026-10-22，`scripts/livecheck_w3.py` 12/12），
> 只有“换个进程仍继续跑”仍只有单测；桌面壳那一段**程序已接完并通过自动化测试**（兄弟仓
> 524 例 vitest 全绿），但渲染效果还没在真机上看过，所以本节不全打钩；
> 逐项跑法与判据见 [W3 实机走查清单](../test/w3-testing.md)。

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
| **W1** 判断层 | 管线规则放宽（降级合并）+ 租约 + 打扰预算 + away mode | M | 无 | — |
| **W2** 感知层 | 事件总线 + 4 个新事件源 | M | 无（IMAP 用标准库 `imaplib`） | W1 |
| **W3** 兑现层 | 任务脊柱 + 副作用账本 + `/undo` | L | 无 | W1、W2（条件等待需要事件） |
| **W4** 体验补齐 | tool-RAG + 手口分离 + 结构化记忆 | M | 无 | W3（异步回报复用任务脊柱） |

每阶段的共同要求：单元测试 + 真实场景走查 + 修复复盘（依 `.trae/rules/`）。

**明确不做（本期）**：

- IoT / 智能家居接入（定位已定为"只做电脑管家"，见第 8 节决策 1；若要扩展需另立计划）。
- 云端全时环境感知（计费量级跳档，沿用 voice 蓝图的 L3 判断）。
- 更换/引入第三方调度或工作流引擎（既有 `Scheduler` + 自研 spine 足够，避免重依赖）。

---

## 7. 里程碑验收总表

- [x] W1：放宽判定确定可解释 + 租约过期即回 `ask` + 静默时段聚合 + 权限零回归（阶段 8 已交付，随 v2.2.3 发布）
- [ ] W2：四类事件各产出恰好一条聚合提醒 + 常驻 CPU < 1% + 单源故障隔离
  ——**代码已交付（随 v2.2.5）**：三条中“单源故障隔离”有单测锁定（连败自动停用
  + 异常不外溢）；“四类事件各产出恰好一条聚合提醒”与“常驻 CPU < 1%”**尚未实测**，
  不在此打钩；逐项跑法与判据见 [W2 实机走查清单](../test/percept-w2-testing.md)。
- [ ] **W3**：重启续跑 + 条件等待不烧轮询 + 副作用可回滚 + 不可逆有延时窗口
  ——**后端代码已交付（未随版本号发布）**：四条均有单测锁定（共 374 例，见
  [18-W3兑现层](../architecture/18-W3兑现层.md) 第九节），“不经模型”那一半另有
  `scripts/smoke_w3_spine.py` 实机跑绿（40/40）；**到点真执行与驱动推进已于
  2026-10-22 用 `scripts/livecheck_w3.py` 实机跑穿**（12/12，当场抓出并修掉四条断链）；
  同日接完兄弟仓 jarvis-desktop 右栏两个区块（那边 524 例 vitest 全绿）；
  只剩重启续跑待实机验证、桌面渲染待人工看，
  故此处不打钩，逐项跑法与判据见 [W3 实机走查清单](../test/w3-testing.md)。
  交付当时的四处设计偏离与被测试打出的四个真实缺陷见
  [w3-journal-spine-delivery](../fixlogs/w3-journal-spine-delivery.md)；
  补齐建目标三个入口与 serve RPC 的收尾切片见
  [w3-entry-rpc-delivery](../fixlogs/w3-entry-rpc-delivery.md)。
- [ ] W4：按轮挂 ≤12 工具时建响应 < 1s + 语音说"帮我查一下 XX"能异步拿到结果 + 记忆带时效与出处

---

## 8. 待决策项（需你拍板）

| # | 问题 | 影响 | 结论 |
|---|---|---|---|
| 1 | 定位边界：只做"电脑管家"，还是包含居家/IoT？ | 决定 W2 事件源是否扩到 Matter/Home Assistant，是量级差异 | ✅ **已定（2026-10）：只做电脑管家**。事件源全部来自本机与网络协议（窗口/文件/仓库/邮件/系统），数据不出本机、不引入外部账号授权；IoT 留作独立计划（对应第 6 节"明确不做"） |
| 2 | 是否允许常驻后台事件源默认开启？ | 隐私与体验的权衡；建议"安装后首次询问 + 逐源开关" | ✅ **已定（2026-10）：逐源默认关 + 首次启动询问**，且两项均**已落地**（W2 第 6 步：桌面「感知源」开关分区；第 7 步：从未表态时投递一次引导）。与 3.1 的"默认全关"口径一致；`window`（前台窗口）是唯一有明显隐私属性的源，必须独立开关且状态可见 |
| 3 | 邮件集成方式：IMAP 只读轮询/IDLE，还是走既有邮件工具的 API 通道？ | 决定 W2 的实现复杂度与权限提示频率 | ✅ **已定（2026-10）：IMAP IDLE 长连接 + 断线自动降级轮询**（标准库 `imaplib`，零新依赖）。**修正**：原选项"走既有邮件工具的 API 通道"不成立——现仓只有 `SendEmailTool` 走 SMTP **能发不能收**，`[email]` 表无任何 IMAP 字段；`collaboration/mailbox.py` 的"未读邮件"是 multi-agent 文件信箱，与真实邮箱无关。故需新增 `[email].imap_*` 字段，并沿用"读不回传明文授权码、写留空=保持原值"的既有安全范式 |
| 4 | 重要度评分是否引入本地小模型？ | 纯规则省资源但笨；小模型更准但增依赖与常驻占用 | ⏳ **暂不定，不阻塞 W2**：总线与 W1 先跑纯规则/阈值，实测出现"规则判不准"的漏报误报后再评估 |
| 5 | ~~W1 效果语义 `dry_run` 是否同时用于工具层（而不只是通知层）？~~ 已定：随策略引擎降级合并退出 W1，工具层真拦截与副作用账本归 W3.2 | 影响与既有五层权限管线的耦合面 | ✅ 已定（见上，随 W1 降级合并退出）。**W3.2 落地口径**：工具层**不引入 `dry_run` 语义**——权限管线本身已经在做真拦截，再加一层只会重复；不可逆动作走“预览 + 30s 延时窗口 + 冻结参数重放”，可回滚的本地写走“引用本轮检查点 + `/undo`”（见 4.2） |

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

- 蓝图（北极星 · 能力模型 · 演进路线）：[BLUEPRINT.md](../blueprint/BLUEPRINT.md)
- 主动提醒既有实现：[PLAN-p2-proactive-reminder.md](PLAN-p2-proactive-reminder.md)
- 沙箱与回滚：[PLAN-p3-sandbox-execution.md](PLAN-p3-sandbox-execution.md)
- 实时语音待机/唤醒/计费：[PLAN-future-realtime-voice-upgrade.md](PLAN-future-realtime-voice-upgrade.md)
- 实时语音工具模式故障复盘：[realtime-talk-tools-mode-default-fix.md](../fixlogs/realtime-talk-tools-mode-default-fix.md)
- 关键代码：`agent/core/daemon/{proactive,scheduler,vision_watcher}.py`、
  `agent/permissions/`、`agent/core/memory/`、`agent/voice/realtime_tools.py`
