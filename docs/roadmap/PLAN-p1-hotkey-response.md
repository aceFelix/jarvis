# P1-2 更快的热键响应 — 升级方案（历史文档）

> ⚠️ 2026-08 更新：常驻托盘模式已下线，本方案中 `text_terminal_warm` 配置与终端派生路径已移除；
> 热键能力（`hotkey` / `hotkey_native` / `hotkey_debounce_ms`）保留，待三栏工作台（一期已落地）接线。
> 以下为历史实现记录。


> 目标：把「全局热键按下 → 文本对话窗口可输入」的耗时控制在 **500ms** 以内。

---

## 一、当前瓶颈分析

| 瓶颈 | 位置 | 影响 |
|---|---|---|
| keyboard 库全局钩子扫描 | `HotkeyListener` | 热键按下后需经过 keyboard 库的事件队列与扫描，延迟约 50~200ms |
| daemon 主循环 0.5s 轮询 | `JarvisDaemon.run()` 中 `_wake_event.wait(timeout=0.5)` | 文本/语音唤起 worst case 要等 500ms |
| 文本终端冷启动 | `_spawn_text_terminal()` spawn 新 Python 进程 | 加载全量模块、初始化 provider、注册工具、MCP/LSP，耗时 2~5s |

实测路径（Windows detached daemon + Git Bash）：

```
Ctrl+Shift+J
  → keyboard 库回调 (~100ms)
  → _trigger_text()
  → _spawn_text_terminal()
  → subprocess.Popen(mintty + python -m agent.main --no-boot)
  → Python 解释器启动 + 模块导入 + RichCLI 初始化 + provider 构建 + 工具注册 + banner
  → 窗口显示且 prompt_toolkit 就绪
```

总耗时通常在 **2~5 秒**，远超 500ms 目标。

---

## 二、优化思路

### 2.1 低延迟热键监听（Windows 原生）

Windows 提供 `RegisterHotKey` + `GetMessage` 原生 API，比 keyboard 库的全局钩子更快、更稳：

- 系统级原子注册，无扫描延迟。
- 不依赖管理员权限（RegisterHotKey 只需要普通权限）。
- 回调线程独立，按下即触发。

实现 `NativeHotkeyListener`（Windows 专用），保留 `HotkeyListener`（基于 keyboard 库）作为跨平台回退。

### 2.2 消除事件轮询

热键回调直接执行唤起动作，不再经过 `_wake_event` 主循环轮询：

- 语音模式：直接托盘通知 + TTS 反馈「在，先生」。
- 文本模式：直接调用 `FastTerminalSpawner.bring_up()`。

### 2.3 文本终端快速唤起

核心策略：**进程预启动 + 窗口复用**。

#### 2.3.1 进程预启动（Warm Terminal）

daemon 启动后在后台预先 fork 一个隐藏文本终端进程，加载好 jarvis REPL 并阻塞在输入就绪状态。热键按下时通过 IPC/信号把它「显示」到前台。

优点：唤起即输入，延迟 < 100ms。  
缺点：常驻一个隐藏进程，占用内存约 80~150MB。

实现为可选配置 `daemon_text_terminal_warm`（默认 `false`），由用户按需开启。

#### 2.3.2 窗口复用

如果上一次文本终端进程仍在运行，直接把该窗口置顶（`SetForegroundWindow`），而不是重新 spawn。避免重复冷启动。

#### 2.3.3 快速启动模式

新增 `--quick` 参数给 `python -m agent.main`：

- 跳过 boot animation。
- 跳过 MCP 连接。
- 跳过 LSP 初始化。
- 延迟加载 harness 工具（首次用到时再加载）。

这样普通 spawn 模式也能从 2~5s 降到 1~2s；配合窗口复用后，第二次唤起可在 500ms 内完成。

---

## 三、具体改动

### 3.1 新增模块

| 文件 | 职责 |
|---|---|
| `agent/daemon/hotkey_native.py` | Windows 原生热键（`RegisterHotKey`）实现 |
| `agent/daemon/terminal_spawner.py` | 快速终端唤起：窗口复用 / warm 进程 / 快速启动参数 |

### 3.2 修改模块

| 文件 | 改动 |
|---|---|
| `agent/daemon/daemon.py` | 用新的热键监听器；热键回调直接触发；主循环去掉 0.5s 轮询依赖；集成 `FastTerminalSpawner` |
| `agent/config/settings.py` | 新增配置：`daemon_hotkey_native`、`daemon_hotkey_debounce_ms`、`daemon_text_terminal_warm` |
| `agent/main.py` | 新增 `--quick` 启动参数；支持跳过 boot animation / MCP / LSP / harness 延迟加载 |
| `configs/settings.example.toml` | 补充新配置示例 |

### 3.3 配置项

```toml
[daemon]
hotkey = "ctrl+shift+j"
hotkey_native = true           # Windows 优先使用 RegisterHotKey（更快）
hotkey_debounce_ms = 200       # 热键去抖毫秒，防双击触发
text_terminal_warm = false     # 是否预启动隐藏文本终端（极速唤起，但常驻内存）
```

---

## 四、验收标准

- [ ] Windows 下热键优先走原生 `RegisterHotKey`。
- [ ] 热键回调直接执行，不再依赖 500ms 轮询。
- [ ] 文本终端窗口可复用：已有窗口时直接置顶，不重新 spawn。
- [ ] `--quick` 参数可显著缩短新终端首次启动时间。
- [ ] `text_terminal_warm=true` 时，热键唤起到 prompt 就绪 < 500ms（实测）。
- [ ] keyboard 库仍可作为跨平台回退（macOS / Linux）。
- [ ] 单元测试覆盖热键解析、去抖、窗口复用逻辑。
- [ ] 编译 / 构建通过。

---

## 五、风险与回退

| 风险 | 回退方案 |
|---|---|
| `RegisterHotKey` 在部分 Windows 版本/权限下注册失败 | 自动回退到 keyboard 库 |
| warm 进程占用内存 | 默认关闭，用户按需开启 |
| warm 进程崩溃或窗口句柄失效 | 检测到后自动重新 spawn |
| `--quick` 跳过 MCP/LSP 导致功能缺失 | 仅影响新终端启动阶段，首次调用相关命令时懒加载 |

---

## 六、涉及文件

- `jarvis/agent/daemon/hotkey_native.py`（新增）
- `jarvis/agent/daemon/terminal_spawner.py`（新增）
- `jarvis/agent/daemon/daemon.py`
- `jarvis/agent/config/settings.py`
- `jarvis/agent/main.py`
- `jarvis/configs/settings.example.toml`
- `jarvis/docs/roadmap/jarvis-upgrade-roadmap.md`
- `jarvis/README.md`
- `jarvis/tests/daemon/test_hotkey.py`（新增测试）
