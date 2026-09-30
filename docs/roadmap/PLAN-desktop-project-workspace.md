# 桌面项目工作区（选择文件夹作为项目）— 设计落库

> 2026-08 · 桌面 jarvis 一次性交付计划。落地范围 = jarvis 后端（serve 协议 + 引擎 + 持久化）
> 与 jarvis-desktop 前端（Electron 主进程/preload/渲染层 + 左栏项目区）。
> 交互口径全程对齐已有的「模型热切换」，同一 serve 进程串行落地、不打断流式、无竞态。

---

## 一、背景与动机

后端 `workdir` 早已是一等公民：
- 工具执行（Bash / FileRead / FileWrite / …）的当前目录
- `SessionMeta.workdir`（会话元数据）
- 项目级 `.jarvis/MEMORY.md` 与 `.jarvis/skills/`
- `get_state` 事件字段

缺的只有三块：

1. **运行时切换 workdir 的 RPC + 引擎内重建**：现在只在 serve 启动时按 cwd 定死，
   没有 `set_workdir`，`settings.set` 白名单不含它也不触发重建；
2. **桌面目录选择器**：主进程只有 `showErrorBox`，没有 `showOpenDialog` 目录选择；
3. **项目 UI 与会话按项目归组**：`list_sessions` 不返回 workdir，左栏无法按项目过滤。

---

## 二、锁定的设计决策

1. **切项目 = 开新会话**：workdir 变了，上下文/项目记忆/技能都换，混聊会串味；
   旧会话仍按 `SessionMeta.workdir` 归在旧项目下可回看。
2. **目录选择器在桌面壳，不在后端**：渲染层经主进程 `dialog.showOpenDialog` 拿路径，
   再经 WS `project.set` 把绝对路径发给后端；后端只接收并校验路径，绝不弹框。
3. **重建走引擎线程内串行**（复刻 `model_switch.handle_switch_model` 范式）：
   正有一轮回复在跑时，切换在队列里排到该轮结束后落地，不打断流式、无竞态。
4. **provider 不重建**（模型不变），只换 `settings.workdir` → 重建 system prompt +
   重挂 harness + 新会话。
5. **持久化**：最近项目 + 最后活跃项目写 `~/.jarvis/projects.toml`（新文件，
   不动 `settings.toml` 语义）。

---

## 三、后端改动清单（jarvis 仓）

### 3.1 协议 `agent/serve/protocol.py`
- 新指令：`CMD_PROJECT_SET="project.set"`、`CMD_PROJECT_GET="project.get"`、
  `CMD_PROJECTS_LIST="projects.list"`、`CMD_PROJECTS_FORGET="projects.forget"`；
  全部加入 `DESKTOP_COMMANDS`（30 → 34，有契约自检）。
- 新事件：`EVT_PROJECT_SWITCHED="project_switched"`，payload `{workdir, name}`。

### 3.2 serve RPC `agent/serve/server.py`
- `_register_desktop_handlers` 注册四条 RPC：
  - `_rpc_project_set`：校验 `path` 非空、`Path(path).is_absolute()` 且 `.is_dir()`，
    否则 `raise ValueError`；通过则调 `api.set_project(path)`，回执 `{ok, workdir, name}`；
  - `_rpc_project_get` → `api.get_project()`；
  - `_rpc_projects_list` → `api.list_projects()`；
  - `_rpc_projects_forget`：校验 path 后转发 `api.forget_project(path)`。

### 3.3 JSBridge `agent/ui/workbench/api.py`
- `set_project(path)`：`_post({"cmd":"set_workdir","path":path})`，入队即返回；
- `get_project()` / `list_projects()` / `forget_project(path)`：读写 `projects.toml`；
  `list_projects` 每项附 `exists=Path(p).is_dir()` 供前端置灰；
- `list_sessions()` 每项补 `workdir` 字段（`SessionMeta.workdir` 现成）。

### 3.4 引擎 `agent/ui/workbench/engine.py`
- `_dispatch` 加 `set_workdir` 分支 → `project_switch.handle_set_workdir`；
- 新字段 `_workdir_override`（镜像 `_model_override`）：会话未装配时记账，
  `_ensure_session` 装配末尾自然生效。

### 3.5 新模块 `agent/ui/workbench/project_switch.py`
`async handle_set_workdir(engine, path)` 主链：

1. `expanduser` + `is_absolute` + `is_dir` 二次校验；非法只 emit `warn`，不推事件；
2. 同路径 → 仅 emit `project_switched` 让前端收敛；
3. 会话未装配（`_query_loop is None`）→ 写 `settings.workdir` + `_workdir_override`
   + `touch_project` + emit + info；
4. 已装配：**先**用 `build_system_prompt(path, registry, ...)` 计算新提示词（不 mutate
   `s`），成功才 `s.workdir=path` → `loop.update_system_prompt(new)` →
   `threading.Thread(register_dynamic_tools, workdir=path)`（按 `tool.name` 去重安全）→
   `_handle_new_session()` → `touch_project` → emit `project_switched` + info；
   prompt 生成失败 emit `warn` 且**不 mutate settings**，避免「目录新、提示词旧」分裂。

### 3.6 QueryLoop `agent/core/query_loop.py`
- `update_system_prompt(text)` 仿 `switch_model`：就地换 `_system`，
  不重建 loop，消息上下文与 `session_usage` 累计保留。

### 3.7 持久化 `~/.jarvis/projects.toml`（`agent/config/projects_registry.py`）
- 结构：`last_active="..."` + `[[project]] {path,name,last_opened}`；
- `list_projects()` / `get_last_active()` / `get_last_active_existing()` /
  `touch_project(path)`（去重 + 置顶 + 截断 `_MAX_RECENT=30` + 设 last_active）/
  `forget(path)`（若正 last_active 则清空）；
- Windows 路径反斜杠与引号在写侧转义（`\\` / `\"`），tomllib 读侧自动解。

### 3.8 serve 启动默认 workdir
- `agent/serve/__main__.py`（桌面壳 `python -m agent.serve` 真实入口）：
  `load_settings()` → `get_last_active_existing()` → 命中则 `with_overrides(workdir=...)`；
- `agent/main.py`（`jarvis --serve` 命令行入口）：`args.serve` 且未显式 `--workdir` 时
  同样优先 `get_last_active_existing()`；
- 效果：重开桌面壳自动回到上次项目，目录已不存在则回退命令行 cwd。

---

## 四、前端改动清单（jarvis-desktop 仓）

### 4.1 主进程 `src/main/index.ts` + 契约 `src/shared/contracts.ts`
- `IpcChannels.SelectDirectory = 'jarvis:select-directory'`；
- `Cmd.ProjectSet/ProjectGet/ProjectsList/ProjectsForget`（字符串与 protocol.py 对齐）；
- `ProjectItem`、`CurrentProject` TypeScript 接口（`exists` / `persisted` 等字段）；
- `registerIpc` 加 `ipcMain.handle(SelectDirectory, ...)` →
  `dialog.showOpenDialog(mainWindow, {properties:['openDirectory']})`
  返回 `canceled ? null : filePaths[0]`；
- **properties 不加 `createDirectory`**：避免默认多一个「新建文件夹」的 UI 干扰项目选择。

### 4.2 preload `src/preload/index.ts` + `index.d.ts`
- `selectDirectory: (): Promise<string | null> => ipcRenderer.invoke(IpcChannels.SelectDirectory)`
  走白名单通道，不暴露 `ipcRenderer` 本体。

### 4.3 渲染层
- `api/ws.ts`：无需改（通用 `sendCommand`）；
- `api/dispatcher.ts`：
  - `case 'init':` 追加 `void conn.refreshProjects()`（七路 → 八路刷新）；
  - `case 'project_switched':` 写入 `currentProject`、只清匹配项的 `pendingProjectPath`、
    刷 `sessions` 与 `projects`；
- `stores/backendStore.ts`：
  - `JarvisConnection` 加 `refreshProjects()`；
  - `BackendStoreState` 加 `setProject(path)` / `refreshProjects()` / `forgetProject(path)`；
  - `setProject` 遵循 `selectModel` 的乐观标记 + 失败撤销范式；
- `stores/leftStore.ts`：
  - `SessionItem` 加 `workdir?: string`；
  - 新增 `currentProject: CurrentProject | null`、`recentProjects: ProjectItem[]`、
    `pendingProjectPath: string`；
  - 对应 setter `setCurrentProject` / `setRecentProjects` / `setPendingProjectPath`；
- `components/LeftSidebar.tsx`：`col-header` 下方插入 `<ProjectSection />`；
- `components/ProjectSection.tsx`（新增）：
  - 顶部「项目」标题 + 「＋ 打开文件夹」按钮（调 `window.jarvisDesktop.selectDirectory`
    → 成功 `setProject(pick)`）；
  - 当前项目行：名字 + 路径 tooltip + `pending` 徽标；
  - 最近项目列表：点击切换 / 右键显示「从列表移除」/ `exists=false` 显「目录不存在」徽标；
- `styles/project-section.css`（新增）：按职责从 `main.css` 拆分（`main.css` 已 1163 行）；
- `i18n.ts`：中英文案（项目、打开文件夹、最近项目、待生效、从列表移除、目录不存在等）。

---

## 五、测试覆盖

### 5.1 后端 pytest
- `tests/config/test_projects_registry.py`（新增 9 用例）：
  空 / touch 置顶 / 去重 / 截断 `_MAX_RECENT` / forget 清 active /
  保留其他 active / Windows 反斜杠往返 / 损坏降级 / `get_last_active_existing`；
- `tests/ui/test_project_switch.py`（新增 5 用例）：
  非法 warn / 同路径收敛 / 未装配 override / 已装配 rebuild + new_session /
  prompt 失败中止；
- `tests/serve/test_serve_protocol.py`：`test_desktop_commands_count` 30 → 34；
  新增四条 RPC 用例（注册存在 / 合法 path 入队 `set_workdir` /
  非法 path raises / forget 缺参 raises）；
- `tests/ui/test_workbench_engine_api.py`：`test_api_list_sessions_carries_workdir`；
- `tests/test_query_loop_session.py`：`TestUpdateSystemPrompt::test_replaces_system_and_keeps_state`。

### 5.2 桌面 vitest
- `test/preload/index.test.ts`：`selectDirectory` 走 `SelectDirectory` 通道 +
  通道名稳定契约（改名会断链）；
- `test/renderer/backendStore.test.ts`：项目工作区段落 6 用例
  （setProject 上送 + 乐观标记 / 同路径去重 / 回执失败撤销 / refreshProjects 双指令 /
  forgetProject 后刷列表 / forget 空 path 拒绝）；
- `test/renderer/dispatcher.test.ts`：init 八路刷新 + `project_switched`
  写入 currentProject + 清匹配 pending + 保留不匹配 pending + payload 缺 workdir 保守降级；
- `test/renderer/projectSection.test.tsx`（新增 8 用例）：空态 / 当前项目 + pending /
  打开文件夹成功 / 取消 / 最近项目点击 / 当前项点击 noop / 右键 forget / `exists=false` 徽标。

### 5.3 交付验收
- `pytest tests -q`：全绿；
- `npx vitest run`：284 用例全绿；
- `npx electron-vite build`：main + preload + renderer 三段均构建成功。

---

## 六、文档同步范围

- `README.md`：serve 协议契约从 30 条 → 34 条桌面指令 + 项目热切换事件；
  新增「项目工作区（桌面壳，2026-08）」bullet；
- `docs/architecture/07-UI层.md`：新增「项目热切换」章节 + `project_switch.py` 表格行；
- `docs/test/TEST_CHECKLIST.md`：新增 T-342 ~ T-348（project.* 指令 / serve 启动恢复 /
  `list_sessions` workdir），总数 340 → 348；
- `docs/roadmap/PLAN-desktop-project-workspace.md`：本文件，设计落库与决策记录。

---

## 七、安全与边界

- 后端二次校验目录存在且绝对，拒绝空/相对/不存在；**不自动创建目录**；
- 切项目**不改 `permission_mode`**（沿用现有权限模型），在真实项目目录写文件/跑命令的
  风险由既有权限确认承担；
- 换 workdir 使 system prompt 变（含 `_env_section` workdir），**LLM 前缀缓存失效一次
  属预期**；
- 桌面壳侧目录选择器仅在主进程 `dialog.showOpenDialog` 弹框，渲染层不接触 `fs`/`path`；
  preload 白名单通道不暴露 `ipcRenderer` 本体。

---

## 八、假设与后续演进

- 一个 serve 进程同一时刻只对应一个活跃项目（切项目即换当前，不并行多项目会话）；
  如需多项目并行属后续演进；
- 项目名取目录 basename；重名以完整路径为唯一键；
- pywebview 工作台（`--gui`）暂未接项目区（桌面 Electron 壳独占）；
- 若后续接入 git 集成 / 项目模板 / `.env` 快照，走 `projects_registry` 扩展字段。

---

## 九、后续调整（2026-09-29，实机反馈）

落地后第一轮实机使用暴露三处问题，均已修正：

1. **位置**：项目区原先紧贴 `col-header`（左栏最顶），抢了模式/面板切换的视线；
   改为**底部常驻**——面板区（history / model / voice）之后、`#left-footer` 之前，
   不参与压缩（`flex-shrink: 0`），切面板时始终可见。回归断言见
   `test/renderer/components.test.tsx`「项目区底部常驻」（`compareDocumentPosition`）。
2. **配色**：原先写死蓝色（`#5bc8ff` 系），在荧光绿 CRT / 金属银皮肤下与主题打架；
   改为变量驱动——描边用三皮肤共用的 `--edge-*`，文本/强调用新增的
   `--proj-text/bright/dim/error/panel/hover-bg/hover-fg`（同 `--scroll-thumb` 范式，
   由 `theme-*.css` 的 `:root[data-theme]` 块赋值），悬停取「反白」口径
   （同 `.list-item:not(.noop):hover`）；三皮肤的 `:where(...)` 去圆角/去玻璃列表
   纳入 `.project-section` 等四个类。
3. **左栏状态栏**：连上后端后 `statusLabel` 长期停在启动期的「等待后端启动...」
   （此前只有首轮回复结束/断线才刷新）；现由 `init`（每连接首帧）切到「就绪」。

**现场诊断（报错案例）**：实机截图里聊天流出现 4 条「后端不支持指令 project.get /
projects.list（…请重启后端后重试）」。根因不是代码缺陷：`desktop.log` 显示后端
进程 pid=31964 于 20:16:10 启动，而 `agent/serve/server.py` 与
`agent/ui/workbench/project_switch.py` 的最后修改时间为 21:01:41 / 21:17:48 ——
Python 子进程不热重载（渲染层 Vite HMR 却即时生效），旧进程没有 `project.*`
注册。**处置：从托盘菜单退出桌面壳（关窗只隐藏、不会重启后端）后重新启动**。
该项无需改代码，壳侧「未注册指令立即回 ok=false」的兜底文案已经把原因说清了。

调整后验证：`npx vitest run` 285 用例全绿（16 文件）、`npm run build`
（electron-vite）main / preload / renderer 三段构建成功、`pytest -k project` 25 用例全绿。
