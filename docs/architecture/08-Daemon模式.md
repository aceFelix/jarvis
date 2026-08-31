# 08 - 桌面入口与主动感知（原 Daemon 常驻模式）

> ⚠️ **架构变更记录（2026-08）**：原「无窗口 daemon + 托盘遥控」常驻模式已下线——
> `daemon.py`（守护进程）、`tray.py`（pystray 托盘）、`terminal_spawner.py`（文本终端派生）、
> `sessions.py`、`realtime.py`、`notifications.py`、`daemon/voice_state.py` 均已删除，
> `--daemon` / `--with-tray` / `--detached` 启动参数一并移除。
> 由新一代**三栏 GUI 工作台**取代（2026-08 一期落地，见 `docs/plans/workbench-gui.md`）。
> 桌面入口已指向 `jarvis --gui` 三栏工作台。

## 一、现存核心文件

| 文件 | 职责 | 状态 |
|---|---|---|
| [autostart.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/autostart.py) | 开机自启 + 桌面快捷方式（指向 `--gui`） | ✅ 在用 |
| [hotkey.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/hotkey.py) | 跨平台全局热键（keyboard / pynput） | ✅ 保留（待工作台接线） |
| [hotkey_native.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/hotkey_native.py) | Windows 原生 RegisterHotKey 热键监听 | ✅ 保留（待工作台接线） |
| [platform_utils.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/platform_utils.py) | 平台/依赖探测辅助 | ✅ 在用 |
| [voice/voice_state.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/voice_state.py) | 跨进程语音互斥锁（心跳+TTL） | ✅ 在用（由语音包持有） |
| [core/daemon/scheduler.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/scheduler.py) | 任务调度器 | 💤 休眠（待工作台二期接线） |
| [core/daemon/proactive.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/proactive.py) | 主动引擎（简报/期限/日历提醒） | 💤 休眠 |
| [core/daemon/deadline.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/deadline.py) | 截止日期追踪（分级提醒） | 💤 休眠 |
| [core/daemon/calendar_source.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/calendar_source.py) | 日历源解析 | 💤 休眠 |
| [core/daemon/monitor.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/monitor.py) | 系统资源监控 | 💤 休眠 |
| [core/daemon/vision_watcher.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/vision_watcher.py) | 视觉守望者 | 💤 休眠 |
| [core/daemon/holidays.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/holidays.py) | 节假日识别 | 💤 休眠 |

> 💤 休眠 = 代码完整保留且测试覆盖，但启动接线随 `daemon.py` 移除；
> 工作台二期将重新装配这些服务（通知渠道改为窗口内提示 + 语音播报）。
> 注：右栏 CPU/内存/磁盘三件套一期已由工作台 `workbench/metrics.py` 轻量采集落地，休眠的 `monitor.py`（阈值告警/趋势预测）待二期接线。

## 二、桌面入口（当前形态：三栏工作台）

双击桌面「JARVIS」图标（或开机自启）→ 静默启动 `jarvis --gui`
→ 打开透明背景的三栏工作台（最大化，保留任务栏）：
左栏模式/历史/模型/音色三面板切换，中栏文本对话气泡流，右栏 CPU/内存/磁盘指标。
二次双击不新建窗口，而是把已驻留窗口唤到前台（单实例，端口 47812）。
窗口内左栏可切换到 /talk 实时双工模式。

```bash
jarvis --gui           # 与桌面图标等效的命令行入口（--talk 等价，兼容旧快捷方式）
```

REPL 文本对话（`jarvis`）与 `/voice` 仍独立于桌面入口；REPL `/talk` 暂仍弹独立实时窗口，待后续整合。

## 三、跨进程语音互斥锁

防止 REPL `/voice` 与工作台实时模式（或 REPL `/talk` 窗口）同时抢占麦克风：

- 锁文件：`~/.jarvis/voice.lock`，内容 `PID,时间戳`
- 持锁进程后台线程每 30 秒心跳续约；时间戳超过 60 秒视为持锁进程崩溃，锁可被抢占
- 不依赖 `os.kill(pid, 0)` 检活：Windows 上该调用对已死进程同样成功，不可靠

## 四、全局热键（保留，待工作台接线）

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

## 五、开机自启 / 桌面快捷方式

```bash
python -m agent.daemon.autostart install            # 安装开机自启
python -m agent.daemon.autostart uninstall          # 卸载
python -m agent.daemon.autostart status             # 查看状态
python -m agent.daemon.autostart desktop            # 创建桌面快捷方式
python -m agent.daemon.autostart desktop-uninstall  # 删除桌面快捷方式
```

| 平台 | 开机自启 | 桌面快捷方式 |
|---|---|---|
| Windows | Startup 文件夹 `.lnk` | `.lnk`（指向静默 VBS，打开 `--gui` 工作台） |
| macOS | LaunchAgent plist（`launchctl load`） | `.command`（Terminal.app 打开） |
| Linux | 不支持（提示手动 systemd） | `.desktop` 文件（终端内运行） |

> VBS 脚本名 `start_daemon.vbs` → `start_jarvis_window.vbs` → `start_workbench.vbs`（现行）：
> 复用检查按文件名命中旧脚本会跳过重新生成，换名强制刷新启动目标。

## 六、休眠服务清单（待工作台二期重新接线）

| 服务 | 能力 | 配置节 |
|---|---|---|
| Scheduler | 定时/周期/一次性任务，与日程工具联动 | — |
| ProactiveEngine | 每日简报、截止日期分级提醒、日历提醒 | `[daemon] briefing_*` |
| DeadlineTracker | 截止日期条目管理（`~/.jarvis/deadlines.json`） | `[deadline]` |
| CalendarSource | Outlook（win32com）/ ICS 日历解析 | `[calendar]` |
| Monitor | CPU/内存/磁盘阈值告警、磁盘趋势预测、异常进程检测 | `[monitor]` |
| VisionWatcher | mediapipe 本地手势/人脸监控 | — |
| Holidays | 节假日/工作日识别 | — |

## 七、设计取舍（下线复盘）

| 决策 | 结论 | 理由 |
|---|---|---|
| 托盘遥控 | ❌ 移除 | 工作台直接呈现对话，托盘菜单交互被窗口内控件取代 |
| 无窗口后台分离 | ❌ 移除 | 单窗口进程模型更简单：最小化到任务栏即可，无需 DETACHED_PROCESS |
| 文本终端派生 | ❌ 移除 | 文本对话已进入工作台中栏，不再弹独立终端 |
| 全局热键 | ✅ 保留 | 工作台「热键召唤窗口」场景复用（待接线） |
| 主动感知全家桶 | 💤 休眠 | 代码保留，随工作台二期以窗口内通知形式回归 |
| 语音互斥锁 | ✅ 迁移 | 麦克风独占需求与形态无关，迁入 `agent/voice/` |
