# jarvis-desktop 立项 — Electron 桌面壳 + jarvis serve 后端 API

> 状态：✅ 一期已落地（2026-09）｜Part A（jarvis `--serve` 模式）+ Part B（jarvis-desktop Electron 壳全量）已完成，两仓库待用户实机走查通过后分别提交推送。
> 前置：三栏 GUI 工作台（`--gui`，pywebview）已落地并稳定；参照 `deepseek-harness`（dsh，Agent 框架）与 `dsh-desktop`（社区 Electron 桌面壳）的**上下游分离**模式。
> 目标：为 jarvis 增加一个**独立仓库**的桌面应用扩展 jarvis-desktop —— 壳不重写 Agent 运行时，只管桌面宿主能力；UI 用 React 全新实现，但沿用 J.A.R.V.I.S 视觉语言（深蓝玻璃拟态 + 方舟反应炉）。

---

## 一、总体架构

```
jarvis（上游，Python）                     jarvis-desktop（下游，Electron）
┌─────────────────────────┐               ┌──────────────────────────────┐
│ jarvis --serve           │  spawn 子进程   │ Main: backend.ts 管理生命周期  │
│  ChatEngine（复用工作台）  │◄───────────────│ 解析 stdout 握手 JSON         │
│  DesktopBridgeServer     │  127.0.0.1     │ Preload: token/port 经 IPC    │
│  HTTP + WS（token 认证）  │◄───────────────│ Renderer: React 全新 UI       │
└─────────────────────────┘   WS 流式事件    └──────────────────────────────┘
```

- **上游 jarvis** 是唯一 Agent 运行时：`--serve` 装配与 pywebview 工作台**完全相同**的引擎零件（`ChatEngine` + `WorkbenchAPI` + `MetricsCollector`），仅把宿主从 pywebview 换成 WebSocket 服务。
- **下游 jarvis-desktop** 是桌面宿主壳：spawn `python -m agent.serve` 子进程、逐行解析 stdout 握手 JSON 拿到端口/token、经 IPC 下发给渲染进程，渲染进程用 WS 直连后端。
- 对齐 dsh-desktop 原则：**壳不重写 Agent 运行时**，只管桌面宿主能力（窗口/托盘/进程生命周期/单实例）。

## 二、为什么上下游分离

| 维度 | 单仓库内嵌（工作台 `--gui`） | 上下游分离（jarvis-desktop） |
|---|---|---|
| UI 技术栈 | pywebview + 原生 JS/CSS | Electron + React 18 + Zustand + Vite |
| 运行时归属 | UI 与引擎同进程 | 引擎在 Python 子进程，UI 在渲染进程 |
| 分发 | 依赖本机 Python 环境 | 壳可独立打包（二期 PyInstaller 捆绑运行时） |
| 复用 | —— | 同一套 `agent.serve` 可被任意前端消费 |

分离后，jarvis 侧只暴露一个稳定的 WS 协议契约，任何前端（桌面壳、未来的 Web 面板）都能接入，且引擎演进不牵动 UI。

## 三、Part A：jarvis 上游改造（serve 模式）✅

### A1. 入口参数
`agent/main.py` 新增 `--serve`（与 `--gui`/`--talk` 互斥），分发到新包 `agent.serve`；help 注明「headless API 服务，供 jarvis-desktop 等外部前端接入」。

### A2. 新包 `agent/serve/`
| 文件 | 职责 |
|---|---|
| `app.py` | `run_serve()` 入口：装配 Settings + ChatEngine + MetricsCollector + WorkbenchAPI + DesktopBridgeServer → `server.start()` → `engine.start()` → `metrics.start()` → 事件泵 → 投递 `init` → **打印握手 JSON** → stdin EOF 监视 → 优雅停机。退出码：0 正常 / 3 缺 websockets。 |
| `server.py` | `DesktopBridgeServer(BridgeServer)`：复用传输层（HTTP + WS、token 认证、broadcast），WS 指令路由重构为**命令处理器表**；`message` 改道引擎队列，其余 12 条经 `_register_rpc` 注册；事件泵 `_pump_loop` 每 50ms 轮询广播。 |
| `protocol.py` | 指令/事件 schema 常量与协议文档字符串（**前后端唯一契约来源**）。 |
| `__main__.py` | `python -m agent.serve` 入口（Electron spawn 用）。 |

行数控制：桌面 handler 全部放 `agent/serve/`，`agent/bridge/server.py` 分发段抽薄，手机 PWA 原有行为（`query`/`abort`）迁移为内置 handler、向后兼容。

### A3. 测试
`tests/serve/` 新增 3 文件共 **19 用例**：`test_serve_protocol.py`（握手/回执 schema，6）、`test_serve_routing.py`（13 指令正常/异常路径 + 事件泵，11）、`test_serve_integration.py`（WS 端到端 + loopback 绑定，2）；另在 `tests/ui/test_workbench_engine_api.py` 补 `--serve` 参数解析用例。全量回归 **1659 passed**（基线 1639 + 20）。

## 四、Part B：jarvis-desktop 新仓库（Electron + React）✅

独立 git 仓库，一期不建远程（远程由用户后续在 GitHub 创建后再关联）。

### B1. 工程骨架
`package.json`（electron + electron-vite + electron-builder + react 18 + zustand + vitest + @testing-library/react + typescript）、`electron.vite.config.ts`、三份 tsconfig、`.gitignore`。

### B2. `src/main/`（主进程，TypeScript）
- `index.ts`：应用生命周期、`requestSingleInstanceLock` 单实例（二次启动聚焦）、无边框主窗口（`contextIsolation:true`/`nodeIntegration:false`/`sandbox:true`）、关闭只隐藏到托盘、`before-quit` 回收后端。
- `backend.ts`：核心——`resolvePythonEnv`（env `JARVIS_PYTHON` 默认 `python`、`JARVIS_REPO` 默认 `../jarvis`）→ spawn `python -m agent.serve`（Windows `windowsHide:true`）→ 逐行 `parseHandshakeLine` 解析握手 JSON（带 30s 超时与失败诊断）→ 状态机 `idle/spawning/ready/error/exited`；退出时 Windows 走 `taskkill /pid <pid> /T /F` 杀进程树。
- `tray.ts`：托盘（反应炉图标、显隐窗口、退出）；`logging.ts`：日志写 `userData/logs/desktop.log`（含 serve stderr 转储）。

### B3. `src/preload/index.ts`
contextBridge 最小暴露 `window.jarvisDesktop`：`getBackendInfo()`（握手 port/token）、`windowControl()`（最小化/关闭）、`onBackendStatus()`（进程状态订阅）。**token 不落盘、不进 localStorage**。

### B4. `src/renderer/`（React 全新 UI）
- `api/ws.ts`：WS 客户端（token 认证连接、指令发送、事件订阅、断线退避重连），框架无关纯 TS。
- `api/dispatcher.ts`：服务端事件 → Zustand store 与反应炉实例的映射（对齐 workbench `app.js::dispatchEvent` 口径）。
- `stores/`：Zustand —— `chatStore`（消息流/流式增量/工具卡）、`leftStore`（会话/模型/音色）、`metricsStore`、`backendStore`（连接状态）。
- `components/`：三栏布局（左：历史/模型/音色三面板；中：自绘标题栏 + 聊天流 + 输入区；右：CPU/内存/磁盘指标）+ 反应炉 canvas。
- `styles/`：CSS 变量集中管理，沿用已验证的 CPU 优化经验（canvas 限帧 30fps、dpr=1、隐藏时暂停）与 grid `min-height:0` 滚动约束链。

### B5. 测试
vitest **75 用例**全通过：`test/main/backend.test.ts`（握手解析/状态机/杀进程树）、`test/renderer/{ws,chatStore,dispatcher}.test.ts`、`test/renderer/components.test.tsx`（@testing-library/react）。`npm run typecheck`（node + web 双程序）干净、`electron-vite build` 三目标成功。人工走查清单见 jarvis-desktop `docs/development.md`。

## 五、Part C：文档同步（两仓库）

- **jarvis-desktop**：`README.md`（定位/运行/开发）、`docs/architecture.md`（运行时拓扑/启动流程/持久化/安全边界/协议契约，章节对齐 dsh-desktop）、`docs/development.md`（本地环境/env 变量/测试/人工走查清单/二期路线图）。
- **jarvis**：`README.md` 功能列表 + 平台支持表加 `--serve` 并新增「外部前端接入（serve 模式）」章节；`USER_GUIDE.md` 补启动用法；`docs/architecture/07-UI层.md` 加「外部前端接入（serve 模式）」小节；`docs/test/TEST_CHECKLIST.md` 新增第 35 组 serve 用例（T-321~T-338）；本立项文档。

## 六、协议契约（前后端唯一来源）

契约在 Python 侧 `agent/serve/protocol.py`，TS 镜像在 jarvis-desktop `src/shared/contracts.ts`，两边改动必须同步。

- 传输：WebSocket，JSON 文本帧；握手（stdout 单行）：`{"type":"jarvis-serve-ready","port","http_port","token","pid"}`。
- 指令（客户端→服务端）：`{"type":"<cmd>",...params}`；事件（服务端→客户端）：`{"event":"<name>","data":<payload>}`；回执：`{"event":"reply","data":{"type","ok","result"|"error"}}`。
- 13 条桌面指令：`message` / `sessions.{list,open,new}` / `models.{list,select}` / `voices.{list,select}` / `metrics.get` / `state.get` / `answer_user` / `talk.{start,stop}`。（后续「主动播报桌面接线」新增第 14 条 `proactive.ack` 与 `proactive_notify` 事件，见 [proactive-desktop.md](proactive-desktop.md)；「/voice 半双工解耦桥接」新增第 15-17 条 `voice.{start,stop,interrupt}` 与 `voice_started` / `voice_stopped` / `voice_state`（listening/thinking/speaking/standby/exited）/ `voice_user_transcript` / `voice_ai_text_delta` / `voice_ai_text` 六事件——音频 I/O 留 serve 本机 pyaudio，桌面壳只做遥控器 + 状态/文字显示；「停止回复与 serve 卡死修复」新增第 18 条 `reply.abort`（线程安全取消引擎 send 任务，发送按钮 busy 时变「■ 停止」，见 docs/fixlogs/serve-bash-hang-fix.md）。）

## 七、风险与对策

| 风险 | 对策 |
|---|---|
| 壳与引擎进程生命周期错配（孤儿 Python 占端口） | 握手带 pid；退出 `taskkill /T` 杀进程树；stdin EOF 双向联动停机 |
| token 泄露 | 仅内存传递、不落盘；WS 仅绑 127.0.0.1 随机端口，不对局域网暴露 |
| 本机无 Python 环境 | 一期 env 可配 + 失败弹窗诊断；二期 PyInstaller 捆绑运行时 |
| 前后端协议漂移 | protocol.py 为唯一来源 + contracts.ts 镜像；测试校验全部桌面指令注册（现 18 条） |
| Electron 二进制下载失败（证书/代理） | README/development.md 记录国内镜像 workaround |

## 八、一期落地复盘（2026-09）

- **Part A**：`agent/serve/` 4 模块落地，`tests/serve/` 19 用例 + 1 参数用例，全量回归 1659 passed。
- **Part B**：jarvis-desktop 24 个源文件（main/preload/renderer/shared）+ 5 个测试文件，vitest 75 passed、typecheck 干净、build 成功。
- **修复的真实 bug**：`backend.ts` 错误路径原用 `this.fail(msg).then(reject)`——`fail()` 总是返回 rejected promise，`.then(reject)` 的 `reject` 是成功处理器永不触发，导致 `start()` 悬挂 + 未处理拒绝（生产中 Python 缺失/serve 早崩/握手超时时启动遮罩会永久转圈、错误弹窗永不出现）。改为 `.catch(reject)`（4 处）并补 `fail()` docblock 说明两种正确用法。
- **npm install 踩坑**：electron 二进制下载遇 TLS 证书错误 + esbuild EBUSY；用 `ELECTRON_SKIP_BINARY_DOWNLOAD=1` 先装齐 `.bin`（typecheck/test/build 不需要 electron 运行时二进制），实机 `npm run dev` 前须用镜像补下载。

## 九、二期展望（本次不做）

NSIS 安装包 + 代码签名、`electron-updater` 自动更新、故障恢复/安全模式（后端崩溃自动重启 + 降级 UI）、PyInstaller 捆绑 Python 运行时（脱离本机环境依赖）、实时语音模式深度接入、手机 Bridge 复用。

## 十、假设与边界

- 一期 dev 模式：jarvis-desktop 依赖本机 jarvis 源码仓库（env 可配），不捆绑 Python；正式分发是二期目标。
- 现有 pywebview 工作台保留不动，与 jarvis-desktop 并存（`--gui` 走工作台，`--serve` 走桌面壳）。
- 前端栈定 React 18 + Zustand + Vite（electron-vite 模板，TypeScript，函数组件 + Hooks）。
- jarvis-desktop 为新 git 仓库，一期不推送远程。
