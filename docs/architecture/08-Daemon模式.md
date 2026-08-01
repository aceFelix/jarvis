# 08 - Daemon 常驻模式

Daemon 模式让 J.A.R.V.I.S 像贾维斯一样常驻后台，随叫随到。

## 一、核心文件

| 文件 | 职责 |
|---|---|
| [daemon.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/daemon.py) | 守护进程 + 托盘 + 热键 |
| [hotkey_native.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/hotkey_native.py) | Windows 原生 RegisterHotKey 热键监听 |
| [terminal_spawner.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/terminal_spawner.py) | 跨平台终端弹出（文本对话窗口） |
| [autostart.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/autostart.py) | 开机自启 + 桌面快捷方式 |
| [voice_state.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/voice_state.py) | 语音状态管理 |
| [core/daemon/scheduler.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/scheduler.py) | 任务调度器 |
| [core/daemon/proactive.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/proactive.py) | 主动引擎（简报/期限/日历提醒） |
| [core/daemon/deadline.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/deadline.py) | 截止日期追踪（分级提醒） |
| [core/daemon/calendar_source.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/calendar_source.py) | 日历源解析 |
| [core/daemon/monitor.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/monitor.py) | 系统资源监控 |
| [core/daemon/vision_watcher.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/vision_watcher.py) | 视觉守望者 |
| [core/daemon/holidays.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/holidays.py) | 节假日识别 |

## 二、后台常驻

### 跨平台后台分离

| 平台 | 方式 | 说明 |
|---|---|---|
| Windows | `pythonw.exe` + `DETACHED_PROCESS` | 无窗口进程，关闭终端不影响 |
| macOS | `start_new_session=True` | 新会话脱离终端 |
| Linux | 不支持后台分离 | 以前台模式运行 |

**启动命令**：
```bash
jarvis --daemon          # 后台启动
jarvis --with-tray       # 前台 REPL + 托盘
```

## 三、系统托盘

### 托盘图标

蓝色同心圆图标（致敬钢铁侠方舟反应炉），使用 `pystray` + `pillow`。

### 托盘菜单

| 菜单项 | 行为 |
|---|---|
| **语音对话** | 唤起 `/voice` 语音对话模式 |
| **文本对话** | 弹出终端运行完整 REPL（自动恢复上次会话） |
| **实时聊天** | 开关实时双工语音（勾选=开启），弹出方舟反应炉窗口 |
| **退出贾维斯** | 立即终止守护进程 |

### 实时聊天单例管理

- daemon 生命周期内只维护一个窗口
- 重复点击"实时聊天"唤起已有窗口
- `realtime_enabled_getter` / `realtime_toggle` 回调管理状态
- 状态持久化到 `~/.jarvis/settings.toml` 的 `[realtime_talk].auto_start`

### 文本对话弹窗

跨平台弹出终端：
- Windows：Git Bash / CMD
- macOS：Terminal.app
- Linux：gnome-terminal / xterm

## 四、全局热键（跨平台）

```toml
[daemon]
hotkey = "ctrl+shift+j"   # 全局热键
```

### 平台策略

[HotkeyListener](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/daemon.py) 跨平台重构，根据平台自动选择后端：

| 平台 | 优先后端 | 回退 | 说明 |
|---|---|---|---|
| Windows | 原生 RegisterHotKey（`NativeHotkeyListener`） | keyboard 库 | 无需额外依赖 |
| macOS | pynput | keyboard 库（需 root） | pynput 不需 root，只需辅助功能权限 |
| Linux | keyboard 库 | — | 需 root 或 input 组权限 |

### pynput 后端（macOS）

```python
def _start_pynput(self) -> bool:
    from pynput import keyboard as pkb
    # 解析热键字符串 "ctrl+shift+j" → pynput HotKey 格式
    hotkey_combo = pkb.HotKey(
        pkb.HotKey.parse(self._hotkey),
        self._on_trigger
    )
    self._listener = pkb.Listener(
        on_press=lambda key: hotkey_combo.press(self._normalize(key)),
        on_release=lambda key: hotkey_combo.release(self._normalize(key)),
    )
    self._listener.start()
```

**为什么 macOS 用 pynput 而不是 keyboard**：
- `keyboard` 库在 macOS 上需要 root 权限（`sudo`），用户体验差
- `pynput` 只需辅助功能权限（系统设置 → 隐私与安全性 → 辅助功能），无需 root
- pynput 是 macOS 上最成熟的全局热键方案

### 停止与清理

`stop()` 方法根据 `_backend` 分别处理：
- `"native"`：调用 `NativeHotkeyListener.stop()`
- `"pynput"`：调用 `listener.stop()`
- `"keyboard"`：调用 `keyboard.unhook_all()`

## 五、开机自启 / 桌面快捷方式

[autostart.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/daemon/autostart.py)：

```bash
python -m agent.daemon.autostart install            # 安装开机自启
python -m agent.daemon.autostart uninstall          # 卸载
python -m agent.daemon.autostart status             # 查看状态
python -m agent.daemon.autostart desktop            # 创建桌面快捷方式
python -m agent.daemon.autostart desktop-uninstall  # 删除桌面快捷方式
```

### 跨平台实现

| 平台 | 开机自启 | 桌面快捷方式 |
|---|---|---|
| Windows | Startup 文件夹 `.lnk` | `.lnk`（指向 VBS 无窗口启动） |
| macOS | LaunchAgent plist（`launchctl load`） | `.command`（Terminal.app 打开） |
| Linux | 不支持（提示手动 systemd） | `.desktop` 文件 |

## 六、调度器 Scheduler

[scheduler.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/scheduler.py) 定时执行任务：

- 支持定时任务（每天某时执行）
- 支持周期任务（每 N 分钟）
- 支持一次性任务
- 与日程工具 `ScheduleTool` 联动

## 七、系统资源监控 Monitor

[monitor.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/monitor.py)：

### 监控项

```toml
[monitor]
enabled = true
cpu_threshold = 85.0       # CPU 超 85% 告警
memory_threshold = 90.0    # 内存超 90% 告警
disk_threshold = 10.0      # 磁盘剩余低于 10% 告警
check_interval = 10        # 检查间隔（秒）
alert_cooldown = 600       # 同类告警冷却（10 分钟）
```

### 告警方式

- 托盘通知
- 语音告警（如开启语音）
- 写入日志

**为什么有冷却**：防止 CPU 持续高位时反复告警打扰。

## 八、视觉守望者 VisionWatcher

[vision_watcher.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/vision_watcher.py)：

### 本地 AI 视觉监控

使用 `mediapipe` 本地模型：
- `gesture_recognizer.task` — 手势识别
- `blaze_face_short_range.tflite` — 人脸检测

### 模型缓存

模型文件缓存到 `~/.jarvis/models/`，避免重复下载。

### 优势

- **低延迟**：本地推理，无网络延迟
- **零成本**：不调 API
- **隐私**：视频不出本机

### 触发命令

- "帮我盯着" / "打开监控" → 启动视觉监控
- 手势触发动作（如挥手唤醒）
- 人脸检测触发提醒

## 九、主动引擎 ProactiveEngine

[proactive.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/proactive.py) 让贾维斯具备"主动意识"，定时推送三类提醒：

| 提醒类型 | 来源 | 触发时机 |
|---|---|---|
| **每日简报** | 天气 + 日程 + 待办汇总 | 每天固定时刻（如 09:00） |
| **截止日期提醒** | [deadline.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/deadline.py) 的 `DeadlineTracker` | 每日检查，分级提醒（提前 7/3/1 天、当天、逾期） |
| **日历检查** | [calendar_source.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/calendar_source.py) | 每日检查日程冲突 |

### 截止日期工作流

```
用户："下周五之前交项目报告"
  → 主 agent 调 AddDeadline 工具
  → DeadlineTracker 创建条目（~/.jarvis/deadlines.json）
  → ProactiveEngine 每天 09:00 调 check_deadlines()
  → 计算距 due_date 天数，匹配 remind_days
  → 命中则生成提醒文本，通过 on_notify 回调播报
```

分级提醒：提前 N 天 → "距离 XX 还有 N 天"；当天 → "今天是 XX 截止日！"；逾期 → 每天提醒直到完成。

## 十、自动启动实时聊天

```toml
[realtime_talk]
auto_start = true   # daemon 启动时自动进入实时聊天
```

daemon 启动时检查 `auto_start`，为 true 则自动唤起实时聊天窗口。

## 十一、会话恢复

异常退出后下次启动：
- 自动提示恢复上次会话（对话历史 + 工作目录）
- 文本对话窗口也会自动恢复

## 十二、设计取舍

| 决策 | 选择 | 理由 |
|---|---|---|
| 后台分离 | 平台特定 | 各平台机制不同 |
| 托盘 | pystray | 跨平台 |
| 热键 | 跨平台多后端 | Windows 原生 / macOS pynput / Linux keyboard |
| 视觉监控 | mediapipe 本地 | 低延迟/零成本/隐私 |
| 主动提醒 | ProactiveEngine 定时 | 简报/期限/日历，贾维斯更"主动" |
| 单例窗口 | 是 | 避免重复麦克风占用 |
| 监控冷却 | 10 分钟 | 防反复打扰 |
