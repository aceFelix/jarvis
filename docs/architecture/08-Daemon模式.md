# 08 - 桌面入口与主动感知（原 Daemon 常驻模式）

> ⚠️ **架构变更记录（2026-08）**：原「无窗口 daemon + 托盘遥控」常驻模式已下线——
> `daemon.py`（守护进程）、`tray.py`（pystray 托盘）、`terminal_spawner.py`（文本终端派生）、
> `sessions.py`、`realtime.py`、`notifications.py`、`daemon/voice_state.py` 均已删除，
> `--daemon` / `--with-tray` / `--detached` 启动参数一并移除。
> 当时由新一代**三栏 GUI 工作台**取代（2026-08 一期落地，见 `docs/plans/workbench-gui.md`）。
>
> ❌ **退役（2026-10-14）**：三栏 pywebview 工作台已整体退役——`jarvis --gui` / `--talk`
> 下线，窗口壳（`agent/ui/workbench/{app,single_instance,win32_icon,window_geometry}.py` +
> `assets/`）与 `agent/daemon/autostart.py` 均已删除（开机自启的本质就是开机拉起
> `jarvis --gui`）。图形化桌面唯一入口为 **jarvis-desktop**（自带开机自启）；
> `agent/ui/workbench/` 引擎层仍保留，为 serve 与桌面壳共用。判断依据与影响面见
> [`docs/fixlogs/workbench-gui-retired.md`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/fixlogs/workbench-gui-retired.md)。
>
> ✅ **接线更新（2026-09）**：主动感知三件套（Scheduler / ProactiveEngine / DeadlineTracker）已由 serve 宿主 [`ProactiveHub`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/serve/hub.py) 重新装配，经 jarvis-desktop 桌面壳播报每日简报 / 提醒 / 截止日期（`monitor` / `calendar_source` / `vision_watcher` / `holidays` 仍休眠）。详见 [`docs/plans/proactive-desktop.md`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/plans/proactive-desktop.md)。
>
> ✅ **终端接线（2026-10-14）**：新增 [`core/daemon/cli_reminder.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/cli_reminder.py) 的 `CliReminderHost`，`main.py` 装配 REPL（含 `/voice` 语音）时启动 Scheduler 后台线程并挂载 schedule 四件套，到期经 `RichCLI.notify_reminder()` 在终端上屏 `⏰` 提醒。只接**对话内定时提醒**，不接简报/截止日期/TTS，详见 [`docs/fixlogs/agent-loop-stall-fix.md`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/fixlogs/agent-loop-stall-fix.md)。

## 一、现存核心文件

| 文件 | 职责 | 状态 |
|---|---|---|
| [hotkey.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/hotkey.py) | 跳平台全局热键（keyboard / pynput） | ✅ 保留（待接线） |
| [hotkey_native.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/hotkey_native.py) | Windows 原生 RegisterHotKey 热键监听 | ✅ 保留（待接线） |
| [platform_utils.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/platform_utils.py) | 平台/依赖探测辅助 | ✅ 在用 |
| [voice/voice_state.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/voice_state.py) | 跨进程语音互斥锁（心跳+TTL） | ✅ 在用（由语音包持有） |
| [core/daemon/scheduler.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/scheduler.py) | 任务调度器 | ✅ 在用（2026-09 serve 宿主 ProactiveHub 装配；2026-10 终端 CliReminderHost 装配） |
| [core/daemon/cli_reminder.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/cli_reminder.py) | 终端提醒宿主（启 Scheduler + 挂 schedule 工具 + 到期回调） | ✅ 在用（2026-10-14，`main.py` REPL/语音装配） |
| [core/daemon/proactive.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/proactive.py) | 主动引擎（简报/期限/日历提醒） | ✅ 在用（同上；简报/截止日期已接，日历源传 None 未接） |
| [core/daemon/deadline.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/deadline.py) | 截止日期追踪（分级提醒） | ✅ 在用（同上） |
| [core/daemon/calendar_source.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/calendar_source.py) | 日历源解析 | 💤 休眠 |
| [core/daemon/monitor.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/monitor.py) | 系统资源监控 | 💤 休眠 |
| [core/daemon/vision_watcher.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/vision_watcher.py) | 视觉守望者 | ✅ 在用（2026-10 由 W2 感知源 `percept/source_vision.py` 接入总线，默认关） |
| [core/daemon/holidays.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/holidays.py) | 节假日识别 | 💤 休眠 |

> 💤 休眠 = 代码完整保留且测试覆盖，但启动接线随 `daemon.py` 移除，尚无生产装配点。
> ✅ 在用（主动感知三件套）= 2026-09 由 serve 宿主 `ProactiveHub` 重新装配，到期经 `proactive_notify` 事件推给 jarvis-desktop 桌面壳播报，并行用本机 CosyVoice 做待机 TTS 朗读（二期，`proactive_tts_enabled`，忙时跳过）；**仅 `--serve` / 桌面壳运行期间生效**（终端侧 2026-10 另接 `CliReminderHost`，仅文本上屏不播 TTS）。
> 注：右栏 CPU/内存/磁盘三件套由 [`workbench/metrics.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/ui/workbench/metrics.py) 轻量采集落地（现经 serve 事件供桌面壳右栏）；休眠的 `monitor.py`（阈值告警/趋势预测）、`calendar_source.py`（日历源）、`holidays.py` 待二期接线；`vision_watcher.py` 已于 2026-10（阶段 9 W2 第 4 步）由感知源 `agent/core/percept/source_vision.py` 接入统一事件总线。

## 二、桌面入口（当前形态：jarvis-desktop）

本仓库不再提供任何本地 GUI 窗口。图形化桌面唯一入口是独立仓库 **jarvis-desktop**
（Electron 桌面应用）：主进程拉起并监管 `jarvis --serve` 子进程，凭就绪 JSON 里的
`port` + `token` 建 WebSocket，渲染三栏布局（左：模式 / 会话 / 模型 / 音色 + 项目区；
中：对话气泡流；右：任务 / 用量 / 系统状态 / 运行健康），自带开机自启与系统通知。

```bash
jarvis              # 终端 REPL
jarvis --serve      # headless 后端（供 jarvis-desktop 等外部前端接入）
```

> 已退役形态（仅供理解历史演进）：2026-08 一期起由 pywebview 三栏工作台承担桌面入口
> （透明窗口最大化、单实例端口 47812 聚焦、左栏切 /talk 实时双工），2026-10 窗口壳整体删除。

终端 REPL 文本对话（`jarvis`）与 `/voice`、`/talk` 均独立于桌面入口：REPL `/talk`
自 2026-09 起为**纯终端全双工**（不再弹独立窗口），桌面侧全双工由 jarvis-desktop
的音频桥承担。

## 三、跨进程语音互斥锁

防止 REPL `/voice` 与 serve 侧语音会话（桌面壳发起的半双工 /voice 或全双工 /talk）同时抢占麦克风：

- 锁文件：`~/.jarvis/voice.lock`，内容 `PID,时间戳`
- 持锁进程后台线程每 30 秒心跳续约；时间戳超过 60 秒视为持锁进程崩溃，锁可被抢占
- 不依赖 `os.kill(pid, 0)` 检活：Windows 上该调用对已死进程同样成功，不可靠

## 四、全局热键（保留，待接线）

> 原为窗口壳的「热键召唤窗口」场景保留，壳退役后暂无装配点；能力与测试完整保留，
> 待桌面壳或后续方案自行调用。

```toml
[daemon]
hotkey = "ctrl+shift+j"   # 全局热键
hotkey_native = true      # Windows 优先 RegisterHotKey
hotkey_debounce_ms = 200  # 去抖毫秒
```

| 平台 | 优先后端 | 回退 | 说明 |
|---|---|---|---|
| Windows | 原生 RegisterHotKey（`NativeHotkeyListener`） | keyboard 库 | 无需额外依赖 |
| macOS | pynput | keyboard 库（需 root） | pynput 只需辅助功能权限 |
| Linux | keyboard 库 | — | 需 root 或 input 组权限 |

## 五、开机自启（已退役）

开机自启原由 `agent/daemon/autostart.py` 维护（`python -m agent.daemon.autostart
install / uninstall / status`），本质是让系统开机静默拉起 `jarvis --gui` 三栏窗口；
随窗口壳退役，该模块及其 CLI 已于 2026-10 整体删除，本仓库不再提供跨平台自启注册能力。

| 平台 | 旧实现（已删除） | 现状 |
|---|---|---|
| Windows | Startup 文件夹 `.lnk` → 静默 VBS → `pyw -m agent.main --gui` | ❌ 不再提供 |
| macOS | LaunchAgent plist（`launchctl load`） | ❌ 不再提供 |
| Linux | 从未支持（提示手动 systemd） | ❌ 不再提供 |

需要开机启动请在 **jarvis-desktop** 的设置内开启。存量用户的手动清理指引（执行
`jarvis --gui` 也会打印同一份说明）：

```powershell
# Windows：删 Startup 快捷方式与自启辅助文件
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\JARVIS.lnk"
Remove-Item "$env:USERPROFILE\.jarvis\start_workbench.vbs", "$env:USERPROFILE\.jarvis\jarvis_window.ico"
```

```bash
# macOS：删 LaunchAgents 下 JARVIS 的 plist
rm ~/Library/LaunchAgents/*JARVIS*.plist
```

> 更早之前的桌面快捷方式（`desktop` / `desktop-uninstall` / `desktop-status` 子命令）
> 已于 2026-09 下线：桌面入口由 jarvis-desktop（Electron 桌面应用）接管，旧桌面图标
> 与桌面应用功能冲突，子命令与跨平台 .command/.desktop 生成逻辑一并移除。
>
> VBS 脚本名曾用过 `start_daemon.vbs` → `start_jarvis_window.vbs` → `start_workbench.vbs`
> 三段更名：复用检查按文件名命中旧脚本会跳过重新生成，故每次换启动目标都靠改名强制刷新。

## 六、主动感知服务清单（三件套已接线，余待二期）

| 服务 | 能力 | 配置节 | 状态 |
|---|---|---|---|
| Scheduler | 定时/周期/一次性任务，与日程工具联动 | — | ✅ serve 宿主 ProactiveHub + 终端 CliReminderHost 装配 |
| ProactiveEngine | 每日简报、截止日期分级提醒、日历提醒 | `[daemon] briefing_*` | ✅ 简报/截止已接；日历源传 None 未接 |
| DeadlineTracker | 截止日期条目管理（`~/.jarvis/deadlines.json`） | `[deadline]` | ✅ serve 宿主 ProactiveHub 装配 |
| CalendarSource | Outlook（win32com）/ ICS 日历解析 | `[calendar]` | 💤 休眠（二期） |
| Monitor | CPU/内存/磁盘阈值告警、磁盘趋势预测、异常进程检测 | `[monitor]` | 💤 休眠（二期） |
| VisionWatcher | mediapipe 本地手势/人脸监控 | `[percept] vision_*` | ✅ 在用（2026-10：W2 感知源接入总线，`vision_enabled` 默认关） |
| Holidays | 节假日/工作日识别 | — | 💤 休眠 |

## 七、设计取舍（下线复盘）

| 决策 | 结论 | 理由 |
|---|---|---|
| 托盘遥控 | ❌ 移除 | 工作台直接呈现对话，托盘菜单交互被窗口内控件取代 |
| 无窗口后台分离 | ❌ 移除 | 单窗口进程模型更简单：最小化到任务栏即可，无需 DETACHED_PROCESS |
| 文本终端派生 | ❌ 移除 | 文本对话已进入工作台中栏，不再弹独立终端 |
| 全局热键 | ✅ 保留 | 原为工作台「热键召唤窗口」场景保留；窗口壳退役后暂无装配点，待桌面壳或后续方案接线 |
| 主动感知全家桶 | ✅ 部分复活 | 调度/简报/截止三件套 2026-09 由 serve 宿主 `ProactiveHub` 装配、经 jarvis-desktop 桌面壳播报（`proactive_notify` 事件 + 系统通知），二期补齐待机 TTS 朗读通道（老 daemon 三通道中的「待机语音」复活，忙时跳过）；监控/日历/视觉/节假日仍休眠待二期 |
| 语音互斥锁 | ✅ 迁移 | 麦克风独占需求与形态无关，迁入 `agent/voice/` |
