# 计划：让 jarvis 自己管配置（阶段 12 · 自配置与偏好沉淀）

> 立论数据（2026-10-26 实测盘点）：`Settings` 共 **190 个字段**，桌面面板能改的只有
> **22 个**（`[percept]` 15 + desktop 7），其余 **168 个只能手改 `settings.toml`**；
> 终端 `/config` 是**只读自查面板**（8 个分节），**模型侧一个配置工具都没有**。
> 也就是说：装完 jarvis 第一道坎仍然是"打开一个 TOML 文件"，这与"管家"该有的样子相反。

@author aceFelix

## 一、目标与非目标

**目标**（按优先级）

1. 用户只说话，jarvis 能把**体验类**配置改对并当场生效；
2. 每次变更**可解释、可撤销、进账本**，用户永远查得到"这值是谁改的"；
3. 缺口从"168 个字段要人填"压到"**只有 2–3 件必须问**"；
4. （第二阶段）从长期使用里沉淀偏好，让配置越用越贴人。

**非目标（明确不做，写进代码而不是只写在文档里）**

- 不让模型碰**安全类**配置（`sandbox` / `policy.*` 工具许可 / allow-deny / 各类凭据）；
- 不做**静默后台改配置**：所有自动变更必须是一条可见事件；
- 不让模型**每轮嗅探**"这句该不该改配置"（烧 token 且误触发），只走显式触发 + 低频提炼；
- 不让模型替代 `jarvis init`（首次接模型仍要人给 key，那是唯一必须由人做的决定）。

## 二、核心设计

### 2.1 单一真源：`agent/config/config_specs.py`

现在同一件事有四份实现（`percept_settings.PERCEPT_CONFIG_FIELDS`、
`desktop_settings.DESKTOP_SETTING_SPECS`、`configuration.md` 的表格、`settings.py` 的字段），
模型要"解释自己在改什么"就必须有一张**带人话的真源表**：

```python
@dataclass(frozen=True)
class ConfigSpec:
    key: str            # 协议键（模型/面板/终端统一用这个名字）
    section: str        # settings.toml 节名
    field: str          # TOML 字段名
    attr: str           # Settings 属性名
    kind: str           # bool / int / float / time / list
    label: str          # 中文标题（面板与播报共用）
    hint: str           # 一句人话：改它会发生什么
    tier: str           # auto / confirm / forbidden
    apply_mode: str     # live / rebuild_percept / reschedule / restart
    lo / hi / default
```

`percept_settings` / `desktop_settings` 保留现有对外 API（面板与 RPC 不动），
内部改为从这张表派生，避免一次性大改。

### 2.2 三类分级（硬编码，不给模型越界机会）

| tier | 判定标准 | 内容示例 | 模型行为 |
|---|---|---|---|
| `auto` | **纯表达层**，改坏也只是"声音小一点 / 少说一点"，且不涉及"何时能打断用户" | `tts.volume`、`tts.speech_rate`、`daemon.briefing_enabled/time`、`deadline.enabled/check_time`、`percept.*_enabled` 与各类名单 | 直接改 + 播报一句 |
| `confirm` | 影响**打断时机**或**工作范围**，模型单方面改等于给自己放宽护栏 | `policy.notify_budget.*`（`daily_max` / `quiet_hours` / `min_salience`）、`percept.enabled` 总开关、`workdir`、provider/model、`tasks.enabled`、`actions.undo_window_s`、MCP 增删 | 改之前必须**一句话确认**，不回 N 就不改 |
| `forbidden` | 权限与凭据本身 | `sandbox.*`、`policy` 许可与 allow/deny、`api_key*`、`email` 凭据、MCP server 命令行、`realtime` 端口绑定、`cli_anything` | **拒绝**，只告诉用户怎么改（`/init` 或手改） |

> 这里有一个必须纠偏的判断：`policy.notify_budget` **不能算体验类**。
> 它决定"什么时候能打断用户"，模型若能单方面调大 `daily_max` 或挪开 `quiet_hours`，
> 就等于自己给自己放宽了 W1 的护栏——与"模型改 sandbox"是同一类自我提权，
> 只是没那么刺眼。故归 `confirm`。

### 2.3 两个工具（`agent/tools/config_tool.py`）

| 工具 | 作用 | 关键点 |
|---|---|---|
| `ConfigList` | 列出可改项、当前值、`label`/`hint`、tier | 只返回 `auto`/`confirm` 项；`forbidden` 项连名字都不给（避免模型反复试探） |
| `ConfigSet` | 改一个或多个键 | 逐键校验 tier → 先落盘再改运行时 → 记变更 → 回执含**原值**与撤销方式 |

`apply_mode` 由 spec 声明，工具按它分派，**不让模型自己判断**"这个要不要重启"：

- `live`：改运行时 Settings 即可（tts 每次播报现读）
- `rebuild_percept`：调 `ProactiveHub.hot_update_percept()`（逐源开关是装配快照）
- `reschedule`：调 `ProactiveHub.hot_update_schedule()`（调度任务是启动快照）
- `restart`：只落盘并明确告知"下次启动生效"（不假装已生效）

### 2.4 变更账本与撤销

复用 W3 的 `ActionJournal`（`core/actions/journal.py` 的 `EffectRecord`）：

- 一条记录 = 一次 `ConfigSet` 调用，`undo_spec` 存 `{key: before_value}`；
- 撤销走**独立的配置回滚路径**（不是文件回滚——配置是文本外科式写入，
  `restore(cid)` 那套 git 检查点语义不适用）；
- 播报口径统一成：「`简报时间 08:00 → 07:30`（已生效），`/undo` 可回退」。

### 2.5 提示词守则（`agent/prompts/text_blocks.py` 追加一条）

1. 只在用户**显式要求**改配置时调用 `ConfigSet`，不得"顺手优化"；
2. 改完必须一句话说清"改了什么、原值多少、怎么回退"；
3. `forbidden` 项被要求时：拒绝 + 给出正确的改法，**不得换等价手段绕过**；
4. 拿不准属于哪一类时按 `confirm` 处理（宁多问一句）。

## 三、分期

| 期 | 内容 | 交付判据 |
|---|---|---|
| **P1 ✅ 已交付（2026-10-26）** | `config_specs` 统一表 + `ConfigList` / `ConfigSet` + 三类分级 + 账本 + 回执 + 测试 + 文档 | 说"把 TTS 音量调到 30"能改并回执；说"关掉前台窗口感知"能改且面板同步；`/undo` 能回退；说"关掉 sandbox"被拒 |
| P2 | `confirm` 确认流完善 + `/config set` 终端入口 + 面板复用同一张表 | 三类行为在终端与面板口径一致 |
| P3 | 偏好账本 `~/.jarvis/prefs.jsonl` + 推导层（配置 = 偏好 + 上下文）+ `/prefs` + 每日提炼（走 Scheduler） | 说一次"以后我写代码时别念出来"，次日依然生效且 `/prefs` 看得到原话 |
| P4 | 首启只问三件（模型 / key / 工作目录），其余全默认；用几天后主动提议调整 | 全新机器到能用 ≤ 3 个问题 |

## 三之二、P1 实施记录（计划外的三个修正）

计划是纸上的，写完代码才发现三处得改，记在这里以免下轮又踩：

### ① 确认流不需要新做：直接映射到 W1 权限管线

计划原文说"借 `AskUser` 一句话确认"。实际更好的做法是 `check_permissions` 直接返回
`PermissionResult.ask`——W1 那套管线已经处理了「无人值守 / 沙箱 / 租约 / 待批清单」
各种边界，另起一套必然两处口径不一致。于是 P2 的"confirm 确认流"实际上在 P1 就已经
完成了，P2 只剩终端入口与面板派生。

### ② 新增了第五种 `apply_mode`：`policy`

计划只想到 `live` / `percept` / `reschedule` 三种。写的时候发现
`[policy.notify_budget]` 四项看着像现读，其实 `get_budget()` 是**懒建单例**
（`_budget_singleton is None` 才重建）——不显式重建就只落盘不生效。所以单列一档，
由工具自己调 `init_policy(settings)`（进程内的事，不需要宿主）。

### ③ 覆盖面从"四组"扩到 22 项

计划说"先覆盖 `tts` / `daemon` / `deadline` / `percept` 四组"。实测发现 `[percept]`
一组就有 8 个可改项，再加 `[policy.notify_budget]` 四项（打扰预算是用户最想调的
东西之一），最终 22 项（15 auto / 7 confirm）。`[actions]` 只收了 `undo_window_s`：
`journal_enabled` 关掉等于把已发生的副作用记不住，归 forbidden 更合适。

### ④ 顺手把提示词段做了

计划 §2.5 要求追加一条守则，已落为 `_SELF_CONFIG_SECTION`（条件注入：没注册
`ConfigSet` 就不拼）。内容不是"介绍工具"，而是三条最容易越界的纪律：
只按用户明确要求改 / 沙箱与凭据一律不碰 / confirm 项不代用户答复。

## 四、风险与对策

| 风险 | 对策 |
|---|---|
| 配置漂移（模型反复微调把配置搅成筛子） | 账本里每条带 `source: model/user/init` 与时间；`ConfigList` 标出"模型改过的项" |
| 模型"好心"改坏体验 | 三类分级 + 回执含原值 + 一键回退；`auto` 只放纯表达层 |
| 用户不知道自己被改了什么 | 每次变更都播报（走 W1 闸门，静默时段入库待补播），并进账本 |
| 与已有四份 spec 漂移 | P1 只做"派生"不做"重写"：现有 API 全保留，测试锁住旧行为 |

## 五、验收（人工，可粘贴）

1. `把 TTS 音量调到 30` → 改成功、回执含原值与 `/undo`；`settings.toml` 的注释与其他段一字不动
2. `关掉前台窗口感知` → 走 `rebuild_percept`，桌面面板该源变未开启且不必重启
3. `把每日简报改到 07:30` → 走 `reschedule`，`~/.jarvis/schedule.json` 与 hub 的调度时间同步
4. `把沙箱关掉` → 拒绝，并告知去 `/init` 或手改（**不尝试**用 Bash 改文件绕过）
5. `把打扰预算的每日上限调到 50` → 走 `confirm`，问一句再改
6. `/undo` → 上述任意一条可回退，回执写明"配置已回退到 X"
