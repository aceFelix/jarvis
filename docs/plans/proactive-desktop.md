# 主动播报桌面接线（方案 A）

> 状态：已交付（2026-09）
> 关联仓库：jarvis（serve 后端）+ jarvis-desktop（Electron 桌面壳）
> 关联提交背景：`59f746a`（下线常驻托盘架构，主动感知套件休眠）

## 一、背景

2026-08 下线「无窗口后台常驻 + 系统托盘」架构（提交 `59f746a`）时，主动感知
套件（`agent/core/daemon/` 下的 `Scheduler` / `ProactiveEngine` /
`DeadlineTracker`，含各自单测）并未删除，而是整体进入**休眠态**——模块与配置
字段都在，但生产代码里零装配点。提交信息明确写着「主动感知全套保留，等待
GUI 接线」。

休眠的直接后果：曾经在早上 08:30 主动播报的每日简报、对话里「提醒我」创建的
定时任务、截止日期分级提醒，在任何入口都不再触发。

本计划（方案 A）即完成当年预留的接线工作：**把休眠套件挂到 serve 后端，经
WebSocket 事件推给 jarvis-desktop 桌面壳播报**。

## 二、方案选型

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A（采纳）** | serve 宿主装配 ProactiveHub，事件流经桌面壳渲染 | 复用现有 serve/desktop 链路，不新增常驻进程；播报形态与桌面 UI 统一 |
| B | 恢复独立常驻 daemon 进程 + 托盘 | 与「托盘已下线、桌面壳接管」的既定方向冲突，重新引入孤儿进程/端口治理负担 |
| C | 仅在 pywebview 工作台接线 | 工作台非默认入口（桌面图标已指向 jarvis-desktop），覆盖面窄 |

方案 A 的关键优势：serve 进程已装配与工作台完全相同的引擎零件，只需额外挂一个
ProactiveHub，并把老 `NotificationMixin` 的三通道播报（终端日志 + 托盘通知 +
待机 TTS）收敛为单一 `proactive_notify` 事件。

## 三、交付范围

### jarvis 后端

- **`agent/serve/hub.py`（新建）** `ProactiveHub`：装配 `Scheduler` +
  `ProactiveEngine` + `DeadlineTracker`，配置沿用 settings 的 `briefing_*` /
  `deadline_*` 字段；到期/通知投 `proactive_notify` 事件进 serve 事件队列。
  含简报补播窗口（启动时今天 `briefing_time` 已过且 ≤ `briefing_catchup_window_min`
  分钟、默认 120 → 延迟 5 秒补播一次，等桌面壳 WS 连上）。
- **`agent/ui/workbench/engine.py`** `ChatEngine` 新增可选 `registry_hook`：
  在 `build_default_registry()` 之后、系统提示词生成之前调用，宿主据此挂载
  额外工具；workbench 宿主不传，行为零变化。
- **`agent/serve/app.py`** `_serve_main` 装配 hub，`registry_hook` 内注册
  `register_schedule_tools` / `register_deadline_tools`（共享 hub 内部单例）；
  生命周期：事件泵启动后 `hub.start()`，停机时先 `hub.stop()`。
- **`agent/serve/protocol.py` + `server.py`** 新事件 `proactive_notify`
  （payload `{kind, title, text, task_id}`）、新指令 `proactive.ack`
  （透传 hub.acknowledge，hub 缺失时回执 ok=false）。
- **测试** `tests/serve/test_hub.py`（装配/幂等/到期路由/通知路由/补播窗口/
  确认）+ `test_serve_protocol.py` 扩展（ack RPC 三路）。

### jarvis-desktop 桌面壳

- **`src/shared/contracts.ts`** `ProactiveNotifyPayload` / `NotifyRequest`
  类型 + `Cmd.ProactiveAck` + `IpcChannels.SystemNotify`。
- **`src/renderer/src/api/dispatcher.ts`** 新 case `proactive_notify`：
  聊天气泡上屏（reminder 带 ⏰ 前缀，briefing/deadline 全文多行）+ 调
  `window.jarvisDesktop.notify` 弹系统通知 + reminder 带 task_id 时回 ack。
- **`src/renderer/src/stores/backendStore.ts`** `JarvisConnection` 加
  `ackProactive`（fire-and-forget）。
- **`src/main/notify.ts`（新建）** `showSystemNotification`：Electron
  `Notification` 弹窗 + 窗口未聚焦时 `flashFrame`；走主进程而非渲染端 HTML5
  Notification，窗口隐藏/最小化到托盘时依然可靠。
- **`src/preload/index.ts` + `index.d.ts`** 暴露 `jarvisDesktop.notify`
  （单向 ipcRenderer.send）。
- **`src/main/index.ts`** `ipcMain.on(SystemNotify)` → `showSystemNotification`。
- **测试** dispatcher +7 用例、notify.test.ts（9）、preload/index.test.ts（5）；
  全量 81 → 102 passed。

## 四、明确排除（二期）

- TTS 待机语音播报（`proactive_notify` 已带全文，届时加通道不改协议）；
- `SystemMonitor` 系统告警接线；
- 日历源（`CalendarSource`）接线；
- pywebview 工作台宿主的主动感知接线。

## 五、与老 daemon 常驻语义的差异

老 daemon 是常驻进程，播报全天候生效；serve 随桌面壳启停，播报仅在运行期间
生效。补救链：

1. `Scheduler` 自带错过补偿（一次性任务错过 ≤1 小时启动即补触发）；
2. `ProactiveHub` 简报补播窗口（错过 ≤ `briefing_catchup_window_min` 分钟、默认 120 启动补播一次）；
3. 错过超窗口不轰炸（避免开机一堆提醒）。

该差异已在 README / USER_GUIDE / 架构文档明示。

## 六、验收走查清单

1. `briefing_time` 临时改为 2 分钟后 → 重启 `npm run dev` → 到点聊天区出现
   简报气泡 + Windows 系统通知；
2. 对话输入「1 分钟后提醒我喝水」→ 工具卡显示 ScheduleReminder → 到点 ⏰
   气泡 + 系统通知；
3. 补播：`briefing_time` 设为 1 小时前 → 启动桌面壳 → 5 秒后简报补播；
4. 关闭窗口只剩托盘时通知仍弹（主进程 Notification 不依赖窗口）。
