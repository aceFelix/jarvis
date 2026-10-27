# W2 感知层实机走查清单（Windows / Git Bash）

> 阶段 9（W2 感知层）交付后的人工验收流程，按顺序执行即可。
> 终端命令以 **Git Bash（MINGW64）** 为准；PowerShell 差异在括号里注明。
> 每次改完 `settings.toml` 都要**重启后端**才生效（只有面板拨开关是热生效）。
>
> @author aceFelix　|　更新：2026-10-18

## 0. 前置（一次性）

### 0.1 让桌面壳找到 Python

本机 PATH 里没有 `python`（只有 `jarvis/.venv`），不设这个变量后端会
`spawn python ENOENT`：

```bash
export JARVIS_PYTHON="E:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/.venv/Scripts/python.exe"
```

永久生效（PowerShell，开新窗口）：

```powershell
setx JARVIS_PYTHON "E:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\.venv\Scripts\python.exe"
```

### 0.2 三个终端窗口的分工

| 窗口 | 干什么 | 命令 |
|---|---|---|
| A | 跑后端（终端测阶段用；桌面测阶段可关） | `./.venv/Scripts/python.exe -m agent.serve` |
| B | 挂着看留痕 | `tail -F ~/.jarvis/percept/events.jsonl` |
| C | 跑桌面壳 / 改配置 | `npm run dev` / `notepad ~/.jarvis/settings.toml` |

**`tail` 必须用大写 `-F`**：小写 `-f` 在文件还不存在时会直接退出
（`no files remaining`），而 `events.jsonl` 是**出现第一条事件时才创建**的。

### 0.3 确认没有残留实例

```bash
tasklist | grep -i electron      # 有输出就在托盘图标上右键「退出」
```

终端测与桌面测**不要同时进行**：两边各起一个 hub，会双份采集、双份写同一个
jsonl、双份打扰，现象不可信。

## 1. 阶段一：桌面首次引导（先做这个！）

⚠️ **顺序硬约束**：引导的出现条件是「没有 `consent.json` + 总开关未开 +
`settings.toml` 里**没有 `[percept]` 表**」。一旦为了阶段二把配置写进去，
引导就再也不会出现。所以本阶段必须在贴配置之前做完。

| # | 操作 | 看到这样算过 |
|---|---|---|
| 1.1 | `notepad ~/.jarvis/settings.toml` 删掉整个 `[percept]` 段；`rm -f ~/.jarvis/percept/consent.json` | `grep -n "\[percept\]" ~/.jarvis/settings.toml` 无输出 |
| 1.2 | 窗口 C：`cd /e/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis-desktop && npm run dev` | 后端起来，无 ENOENT 弹窗 |
| 1.3 | 看聊天区 | 一条 `🔍` 开头的系统气泡，标题「贾维斯的观察能力」，正文含「出厂全部关闭」「events.jsonl」「这条提示只出现一次」 |
| 1.4 | 看标题栏「管家服务」按钮 | 右上角**黄色小圆点脉冲闪动**；悬停 tooltip =「管家服务里新增了「感知源」设置，点这里看看」 |
| 1.5 | `cat ~/.jarvis/percept/consent.json` | 有 `asked_at`、`"choice": ""`（还没表态）、`"master_enabled": false` |
| 1.6 | 点「管家服务」按钮进面板 | 黄点**立刻消失**，tooltip 变回普通文案 |
| 1.7 | 托盘退出 → 再起一次 | **不再出现** 🔍 气泡，**不再亮**黄点 |
| 1.8 | 在面板里什么都不动，退出重进 | 仍不引导（只问一次，不追问） |

隐私底线复核（1.3 那一刻顺手确认）：引导到达时**所有源仍是关的**，
面板没有「一键全部开启」这种按钮，且这条引导不做 TTS 朗读、不过打扰预算。

## 2. 阶段二：采集链路（终端 A + 窗口 B 看 jsonl）

> 终端里**不会弹提醒**——播报要投给 WS 客户端，没客户端就被丢掉。
> 本阶段只看留痕文件，这是设计如此。

### 2.1 写配置（整段替换，别留两个 `[percept]`）

TOML 里同名表出现两次是硬错误；Windows 路径**只能用正斜杠**，非要用反斜杠
必须写两遍（`\.` 这类单斜杠会让**整份** settings.toml 解析失败，
所有配置一起作废回退默认值）：

```toml
[percept]
enabled = true
log_events = true
dedup_window_s = 300
min_salience = 0.0
history_max = 200
auto_disable_after_errors = 3

window_enabled = true
window_interval_s = 5
window_title_max_chars = 40
window_title_full_enabled = false
window_linger_minutes = 0
window_exclude_apps = ["1password", "keepass", "bitwarden", "lastpass",
                       "dashlane", "nordpass", "keeweb", "密码", "密钥"]

file_enabled = true
file_watch_dirs = ["E:/J.A.R.V.I.S_Work", "C:/Users/许发明/Downloads"]
file_interval_s = 30
file_grow_threshold = 5
file_max_entries = 2000
file_ignore_suffixes = [".tmp", ".lock"]
file_min_interval_s = 0

vcs_enabled = true
vcs_repos = ["E:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis"]
vcs_interval_s = 120
vcs_min_interval_s = 60

mail_enabled = false
mail_watch_senders = []
mail_min_interval_s = 0

vision_enabled = false
vision_camera_index = 0
vision_fps = 2
vision_gesture_enabled = true
vision_auto_stop_minutes = 0
vision_interval_s = 5

calendar_enabled = false
system_enabled = false
schedule_enabled = false
```

改完**先自验**，别急着起服务：

```bash
cd /e/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis
./.venv/Scripts/python.exe -c "import tomllib,pathlib;d=tomllib.loads((pathlib.Path.home()/'.jarvis/settings.toml').read_text(encoding='utf-8'));print('OK', len(d['percept']), '个键')"
```

看到 `OK 35 个键` 才算过。

### 2.2 起后端

```bash
cd /e/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis
./.venv/Scripts/python.exe -m agent.serve
```

**第一行不许有 ⚠️**（有就是配置还有行没转义好，按行号去修）；应只输出
`{"type": "jarvis-serve-ready", "port": ..., "token": ...}` 然后停住。

### 2.3 逐项验收

| # | 操作 | 判据 |
|---|---|---|
| T1 窗口源 | 点任务栏切 3 个不同应用 | B 里逐条出现 `"source": "window"` / `"kind": "focus_changed"`；`payload.summary` = 「切到 <进程>：<标题>」；`salience_hint` = 0.3。**第一次扫描只建基线不出事件**，必须有切换动作 |
| T2 标题脱敏 | 打开一个标题里带「密码：xxx」或 URL 带 `?token=` 的窗口 | 事件里那部分被剥掉；标题超长按 `window_title_max_chars` 截断 |
| T3 排除名单 | 切到 1Password / KeePass 类应用 | **永不出事件**（命中排除名单即不采） |
| T4 去重 | 在两个窗口间来回切 5 次 | jsonl **不新增行**；桌面面板总览「闸门计数」的 `dedup` **上涨**（`emit` 不变）。⚠️ 只看"没新行"是无效判据，那和"采集坏了"无法区分。2026-10-19 起：这个计数**不再因为改配置而归零**（重建时继承旧账本） |
| T5 文件源 | `for i in 1 2 3 4 5 6 7; do echo x > ~/Downloads/w2_probe_$i.txt; done` | 最多等 30 秒：若干 `file_added`，跨过阈值 5 后**一条** `dir_grew`（而不是刷 7 条） |
| T6 仓库源 | `cd jarvis && echo x >> README.md` | 最多等 2 分钟：jsonl 里一条 `repo_dirty_grew`（带未提交项数与分支名）；**再等 2 分钟不会有第二条**（只在刚跨线那次报）；这条过闸会**上屏**（见 D2）；测完 `git checkout -- README.md` |
| T7 坏源隔离 | `vcs_repos` 加一个不存在的路径，重启后端 | 该源连败 3 次自动停用（面板徽标显末次错误），**其他源照常采** |
| T8 留痕开关 | 面板关掉「本地留痕事件」 | jsonl 停止增长，但面板「最近事件」仍有（内存环形）；「留痕文件」一行显「已关闭」 |
| T9 计数对账 | `wc -l ~/.jarvis/percept/events.jsonl` | 与面板 `emit` 计数一致（`log_events=false` 时不涨但 `emit` 照涨）。注意留痕文件按 `history_max*10` 行裁剪，常驻很久后 jsonl 会比 `emit` 少 |

## 3. 阶段三：打扰闸门与口播（桌面，验证「采到 ≠ 一定吵你」）

> 2026-10 修正：本阶段原判据写的是“切应用应该看到气泡”，**错**。
> 普通切换的重要度 = `percept:window` 权重 0.5 × 先验 0.3 = 0.15，低于门槛
> 0.3 ⇒ 只留痕不出声（[复盘](../fixlogs/notify-budget-percept-relay-fix.md)）。
> 2026-10-19 再修正：静默时段**不再是门槛的后门**——低分事件夜里也不入库，
> 因此早上的补播摘要里不会再夹着“切到 记事本”这类碎事（
> [复盘](../fixlogs/percept-panel-history-lost-on-rebuild-fix.md)）。

| # | 操作 | 判据 |
|---|---|---|
| D1 默认静默 | 开着窗口源切 5 次应用 | **一条气泡都没有**，但 jsonl 五条不少——这才是“采到不等于吵你” |
| D2 该出声的出声 | 面板把「停留提醒阈值」改成 30 并在一个应用里待满；或 `echo x >> README.md` 触发仓库源 | 收到**一条**「贾维斯观察」气泡（vcs 0.7×0.5=0.35、linger 0.5×0.65=0.325 都过闸） |
| D3 日额度 | 配置里 `[policy.notify_budget] daily_max = 1`，重启后端，连触 3 条“该播的” | 只播第一条；其余**入库不播**（面板「打扰预算」行的 `待补播` 上涨）；**不再出现“每两条播一条”的乒乓** |
| D4 补播与冷却 | 接着 D3，超过 5 分钟（`aggregate_window_s`）后再触发一条 | 出现一条「📥 补播 N 条提醒，一并呈上」；5 分钟内不会连续出现第二条摘要；单条积压**不套「积压 N 条」壳** |
| D5 静默时段 | `quiet_hours` 设成覆盖当前时刻 | **只有过了门槛的事件**才入库（切屏这类 0.15 分的直接抑制，不进队列）；时段结束后聚合一条，且**不占日额度**（它是补播） |
| D6 口播验收 | 上面任意一条气泡，开着 TTS 听 | 念的是「切到 资源管理器，jarvis 文件夹」而不是「切到 explorer 点 e x e 冒号 C 冒号反斜杠」；无 `.exe`、无反斜杠、无逐字符拼读；`events.jsonl` 里的 `summary` **仍保留原路径**（留痕不被改写） |
| D7 在场静默 | 装好 mediapipe+opencv 后开视觉源，人离开再回来 | jsonl 里有 `face_appear`/`face_disappear`，但**永不上屏、不占日额度**；同时驱动 W1 的 away 推断 |
| D8 反馈条 | 面板拨任一开关 | 子返回头下方出现**绿色反馈条**「✓ 已保存并重装配总线」（不滚动也看得见——错误看不见会被误读成"开关坏了"） |
| D9 预算可见 | 看面板总览「打扰预算」行 | 「今日 3/10 · 待补播 2 条」与 `notify_state.json` 一致；把 `notify_budget_enabled` 改 false 重启 → **整行消失**（不是显示 0/0） |
| D10 改配置不丢证据 | 先切 3 次应用让 jsonl 涨几条，然后在面板上拨一下「守望目录」保存 | 重装配后「最近事件」**仍然列得出那几条**（读磁盘）、闸门计数**不归零**（累加）。旧行为是“已保存并重装配 + 面板显示还没有采到事件”，而磁盘上其实有 24 条 |

## 4. 阶段四：面板功能面

| # | 检查 | 判据 |
|---|---|---|
| P1 可改范围 | 总览 2 个开关 + 「感知源」7 个源开关 + 「采集名单」4 个文本域与 2 个分钟阈值 | 共 15 个键；其余 22 个键面板上**没有**（成本与隐私旋钮刻意不暴露） |
| P2 反斜杠安全 | 在「守望目录」里填 `E:\J.A.R.V.I.S_Work` 保存 | 不写坏 `settings.toml`（后端会正确转义），且 `2.1` 那条自验命令仍解析通过 |
| P3 假可用防线 | 开某源但不填目录/仓库 | 面板显已启用，不发事件、不报错（"开了没配齐"是配置未完成，不是运行时故障） |
| P4 收编位 | 看逐源列表 | 阶段 11 起日历/系统**已有开关**；只剩「定时提醒」**只有说明没有开关**（它已由提醒通道播报，再接会重复） |
| P5 错误内联 | 杀掉后端进程，再在面板拨开关 | 错误显示在**顶部反馈条**，不进聊天流、不弹系统框 |
| P6 不做乐观更新 | 后端断开时拨开关 | 开关保持原状（面板显示的是后端此刻的真实状态） |
| P7 隐私自查 | 「最近事件」子标签 | 事件原样回显，不做前端二次脱敏；**留痕开着时这份列表读的是磁盘尾部**，所以刚改过配置也不会空（D10） |
| P8 关掉即释放 | 开着 mail/vision 源后关掉 | IMAP 真登出、摄像头真释放（指示灯灭）——重装配路径兑现的承诺 |

## 4.1 阶段五：日历与系统资源收编（2026-10-26 新增）

这两个源都是“底层能力早就写好、只是从没被宿主编好线”的休眠件
（`core/daemon/calendar_source.py` / `core/daemon/monitor.py`），所以这里既验采集，
也验**没配齐时不谎报**。

| # | 操作 | 看到这样算过 |
|---|---|---|
| S1 面板有开关 | 打开「管家服务 → 感知源」 | 「日历事件」「系统资源」两行**都有开关**；「定时提醒」仍是灰卡（只有说明 + 理由） |
| S2 系统资源能采 | `[percept]` 开总开关 + `system_enabled = true`，把 `[monitor] memory_threshold` 临时改成 `1.0`（内存必超，**瞬时判定**），重启后端 | 后端起来后第一轮采集（**约 1 分钟内**，本源节拍 60s）「最近事件」出现 `内存使用率 …` 一条；`alert_cooldown` 内**不重复** |
| S2b 为什么别用 CPU 阈值验首发 | 把 `cpu_threshold` 调低来代替 `memory_threshold` | 会**等两个批次**（约 2 分钟）才出第一条：`monitor._do_check` 要求 CPU **持续 30 秒**超阈才报（防瞬时尖峰），第一次采样只建基准。这不是源坏了 |
| S3 不留第二条采样线 | 同上，看后端日志 | 没 `SystemMonitor started` 那类自转线程日志（本源只调 `check_now()`）；`感知总线已启动，轮询周期 60.0s` 只一条 |
| S4 没配齐不谎报 | 开 `calendar_enabled = true`，但 `[calendar] backend` 保持 `auto`、本机无 Outlook、`ics_path` 为空 | 该源显示“已启用”但 `detail` 里自报不可用原因；**不发事件、不报错**，也不会出现「连续失败已自动停用」 |
| S5 日程只报一次 | 按下方造一份 ICS 并配 `[calendar] ics_path`，日程设在 10 分钟后 | 出现**一条** `source=calendar`，`payload.minutes_until` 落在提前窗口内；本源节拍 **300s**，再等两轮（约 10 分钟）**不重复播报**同一条 |
| S6 全天/已开始的不报 | ICS 里再加一条全天日程（`DTSTART;VALUE=DATE:`）、一条已经开始 5 分钟的日程 | 两条都不出现在 `events.jsonl` |
| S7 恢复通知不占额度 | S2 之后把 `[monitor] memory_threshold` 改回 `90.0` 重启（让内存“回落”） | 不弹「内存已恢复」气泡、`events.jsonl` 里**不出现** `recovery` 行；面板该源「观察范围」里的 `last_alert` 仍是上一条告警 |
| S8 改配置不重报 | 在面板上把任意一个开关**连拨 4 次**（每次保存都会热重装配、换一台新总线） | `events.jsonl` 尾部同一 `source + kind` 在 `dedup_window_s`（300s）内**只应出现一条**。旧行为是连点几次就重复入库几次（白扣打扰额度），复盘见 [热重装配丢去重账本](../fixlogs/percept-dedup-lost-on-hot-rebuild-fix.md) |

造一份最小 ICS（存成 `~/cal-test.ics`）：

```ics
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:percept-test-1
SUMMARY:感知源走查
DTSTART:20261026T210000
DTEND:20261026T213000
END:VEVENT
END:VCALENDAR
```

> 时间要按**本机当前时刻 +10 分钟**改。提前窗口取 `[calendar] remind_minutes_before`
> （默认 30），所以提前 10 分钟的日程一定落在窗口内。
> ⚠️ `[calendar] enabled` 是**主动引擎**的日历简报开关，与 `[percept] calendar_enabled`
> （感知总线）不是同一个：本节只拨后一个。

## 5. 自动化门禁（手测前后各跑一次）

```powershell
cd e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis; .venv\Scripts\python.exe -m pytest -q
cd e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis-desktop; npm run test; npm run typecheck; npm run build
```

基线（2026-10-27，含阶段 12 P1–P4 自配置层全量）：jarvis **3632 passed**；
桌面 **535 passed** + typecheck + build 全绿；
死链门禁 `python scripts/check_md_links.py` → dead 0 / untracked 0。

> 阶段 11 收尾时的基线是 3263；阶段 12 P1 新增 105 例（真源表 / 两个工具 / 宿主
> 生效钩子 / 提示词段四份测试）；手测发现的「面板不跟新值」补了 9 例后端
> （变更监听器 3 + 工具广播 4 + 宿主事件 2）与 7 例桌面（`dispatcherSettings.test.ts`）；
> P2 面板派生又补 58 例（派生一致性守卫 49 + 列表类 3 + 改写的分级不变量 6）；
> P2 终端入口再补 37 例（`test_config_set_command.py`）；
> P3 偏好账本补 49 例（条件匹配 / auto 域边界 / 闸门施放 / 工具与命令 47 + 提示词 2）；
> P3 提炼与多决策点再补 28 例（共同上下文 / 候选不生效 / accept 转正 / 拒后不重提）；
> P4 首启三问再补 32 例（判定 / 台账 / 三问 / /init 分流 / 提议门槛）；
> P4 实测评测又补 7 例（多程序 `when_app` / 回执带当前前台 / 幽灵条件拦截）；
> P4 首启改在线模型目录后再补 16 例（`test_model_catalog.py` 12 + 首启流程改造 4）；
> P4 实测追问“怎么只有一个模型”后补 4 例：拉取失败必须带**具体原因**回给用户；
> P4 修 `/init` 配完连不上后补 6 例：保存必须热生效到当前会话；
> P4 第二轮实测后补 18 例：热生效的比较基准必须用旧端点快照（否则复用旧 provider）、
> 厂商默认地址单一来源（`agent/config/vendors.py` + `test_vendors.py`）、
> DeepSeek 推断补 `/v1`、编辑时 base_url 留空不覆盖用户地址。
> 数字只管看"有没有少例"，不必较真——**绿比数字重要**。

## 6. 收尾

```bash
# 恢复走查前的配置（阶段一第 1.1 步与本次改动都有备份）
ls ~/.jarvis/settings.toml*
```

## 7. 任何一步不符，把这三样发我

1. 第几步、预期什么、实际看到什么；
2. `Get-Content "$env:APPDATA\jarvis-desktop\logs\desktop.log" -Tail 40` 的输出（PowerShell）；
3. `settings.toml` 的相关段落（**API key 那几行自行打码**）。

按项目规矩，人工测试发现并修掉的缺陷要补 `docs/fixlogs/` 复盘——
本轮已产出两份：

- [percept-config-set-envelope-fix.md](../fixlogs/percept-config-set-envelope-fix.md)
  （协议信封键 `type` 混进配置白名单，导致面板所有开关点了没反应）；
- [notify-budget-percept-relay-fix.md](../fixlogs/notify-budget-percept-relay-fix.md)
  （感知源权重未登记 + 冲刷无冷却且绕开门槛，导致“每两条必播一条”与
  “静默时段积压 1 条”的谎话文案；顺带修了 TTS 逐字符念路径）。
