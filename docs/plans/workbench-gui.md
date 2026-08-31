# JARVIS 三栏 GUI 工作台 — 开发计划（已评审定稿）

> 状态：✅ 定稿（2026-08-30，用户评审拍板；删除阶段 `59f746a` 落地后起草）｜**一期已落地（2026-08-30）**
> 前置：无窗口常驻托盘架构已下线，桌面入口暂指向 `jarvis --talk` 独立窗口（一期已改指向 `--gui` 工作台）。
> 目标：用一个**单窗口三栏工作台**取代旧的托盘遥控形态，成为桌面图标的新宿主。

---

## 一、目标形态

双击桌面「JARVIS」图标 → 打开一个常驻工作台窗口：

```
┌──────────────┬───────────────────────────┬──────────────┐
│  左栏 · 模式  │      中栏 · 对话主区        │ 右栏 · 系统   │
│              │                           │              │
│ /voice 语音   │  气泡消息流（用户/贾维斯）    │ CPU / 内存    │
│ /talk 实时    │  流式渲染 + Markdown        │ 磁盘趋势      │
│              │                           │ 网络 / 进程    │
│ 各模式独立选择：│  模型操作区：               │              │
│ - 语音模型     │  换模型 / 换音色 / 打断      │ 告警提示      │
│ - TTS 音色    │  新建会话 / 历史会话         │ （接监控休眠   │
│              │  文本输入框（文本对话也在这）   │   服务后亮灯） │
└──────────────┴───────────────────────────┴──────────────┘
```

**窗口行为（用户已确认口径）**：

| 行为 | 口径 |
|---|---|
| 最小化 | 到**任务栏**（不做托盘） |
| 二次双击桌面图标 | 唤起已驻留窗口到前台（**单实例**，不新建） |
| 全局热键 `Ctrl+Shift+J` | 召唤/聚焦窗口（复用保留的 hotkey 模块） |
| 文本对话 | 进新 UI（中栏文本输入框，取代终端派生） |
| 窗口初始状态 | **最大化**（保留任务栏），透明背景透出桌面 |
| 左栏 | 三面板切换：**历史会话列表** ⇄ **模型** ⇄ **音色**（按钮控制，各自独占全高可滚动） |
| 历史会话 | 支持会话恢复（点击历史项载入到中栏） |

## 二、技术基座（复用家底盘点）

| 零件 | 现状 | 新 UI 中的角色 |
|---|---|---|
| `agent/ui/realtime_window/`（process.py 子进程 + JSBridge + 事件队列） | /talk 独立窗口在用 | 进程模型蓝本：GUI 主进程 + 事件队列通信 |
| `WebviewRealtimeTalkUI` 桥接 | /talk 已验证 | 中栏实时模式直接复用 |
| `RealtimeTalk`（DashScope 全双工，AEC/打断/工具调用） | 稳定 | 左栏 /talk 模式后端 |
| `voice_loop`（STT→LLM→TTS） | 深度耦合 RichCLI（二期解耦） | 左栏 /voice 模式后端 |
| `agent/voice/voice_state.py` 互斥锁 | 心跳+TTL 刚重写 | 模式切换时的麦克风独占仲裁 |
| `agent/daemon/hotkey*.py` | 已保留 | 热键召唤窗口 |
| `agent/core/daemon/`（调度/简报/监控/视觉/日历） | 休眠态 | 右栏指标 + 主动通知渠道重接线 |
| QueryLoop + ToolRegistry | REPL 在用 | 中栏文本对话后端（复用，只换 UI 适配器） |

预估复用率 60-70%；最大工作量在 **/voice 从 RichCLI 解耦**。

## 三、视觉风格（用户拍板）

- **透明科技感**：窗口背景透明（WebView2 `transparent=True`），能看到桌面
- **方舟反应炉**：淡蓝色反应炉波纹动画居中/衬底，待机缓动呼吸；**说话时（TTS 播报/实时音频）波纹加速律动**
- 气泡与面板采用半透明毛玻璃质感，延续暗色科技风配色

## 四、分期计划

### 一期：工作台骨架 + 文本对话 + /talk 整合（不碰 /voice）✅ 已落地（2026-08-30）

1. **窗口进程模型**：`agent/ui/workbench/`（新包）
   - 主进程承载 pywebview（三栏 HTML/CSS/JS），QueryLoop 跑在工作线程
   - 沿用事件队列 + JSBridge 模式（参考 realtime_window）
   - 入口：`jarvis` 参数新增 `--gui`（新窗口），桌面 VBS 改指向它（换名 `start_workbench.vbs`）
2. **单实例与召唤**：
   - 锁文件 `~/.jarvis/workbench.lock`（复用语音锁的心跳+TTL 方案）检测已驻留实例
   - 已存在时：通过本地 socket/命名管道发 `FOCUS` 指令，宿主窗口置前
3. **中栏文本对话**：QueryLoop 输出事件流 → 气泡渲染（复用 LayeredContext/工具结果折叠）
4. **左栏 /talk 模式**：复用 `WebviewRealtimeTalkUI`，窗口内切换而非弹独立窗；`--talk` 参数保留但直接启动工作台（兼容旧快捷方式），独立窗口下线合并
5. **左栏模型/音色选择 + 历史会话列表**：`/models` 能力以 UI 呈现（读写 model_registry）；左栏按钮在"历史会话列表"与"模型音色面板"间切换；历史项点击恢复会话到中栏
6. **任务栏最小化**：pywebview 原生最小化即可，无需托盘

**一期验收**：双击图标开窗口；文本/实时双工可用；二次双击聚焦；热键召唤；最小化到任务栏。

### 二期：/voice 解耦 + 右栏系统指标 + 主动感知接线

1. **/voice 事件适配器**（核心难点）：
   - 抽 `VoiceSessionEvents` 协议（状态迁移：聆听/思考/播报/待机；文本增量；打断）
   - `voice_loop` 的 `RichCLI` 依赖改为协议注入：`RichCLIVoiceAdapter`（REPL 不回归）+ `WorkbenchVoiceAdapter`（GUI）
   - REPL `/voice` 与新 UI 共用同一引擎，互斥锁保证不双开
2. **左栏 /voice 模式接入**：模型/音色选择直接改 Settings 生效；**待机电源驻留低功耗聆听保留，加开关控制**（配置项）
3. **右栏系统指标**：拉起休眠的 `monitor.py`，通知回调改为窗口内横幅 + 语音播报
4. **主动感知回归**：调度器/简报/截止/日历接到工作台生命周期，通知走窗口内渠道

**二期验收**：三栏全部可用；说"退下"进待机、说"贾维斯"唤醒在 GUI 内生效；监控告警进右栏。

## 五、风险与对策

| 风险 | 对策 |
|---|---|
| /voice 与 RichCLI 耦合深，解耦牵一发动全身 | 二期先抽协议、双适配器并行，REPL 路径测试保底 |
| pywebview 单线程限制（主线程跑窗口） | 所有语音/LLM 循环放工作线程或子进程，走事件队列 |
| 单实例聚焦跨进程通信 | 一期可降级：锁文件 + 轮询；二期再优化为 socket |
| 主动感知服务接线面广 | 分服务逐个接入，每个服务独立验收 |

## 六、评审定稿记录（2026-08-30）

1. ✅ 默认**最大化**窗口（透明背景看桌面；真全屏会盖住任务栏，故用最大化）；方舟反应炉淡蓝波纹，说话时律动。
2. ✅ 右栏先做 **CPU/内存/磁盘** 三件套（复用休眠的 monitor 模块采集）。
3. ✅ `--talk` **合并进 GUI**：参数保留，语义变为启动工作台；独立实时窗口（`agent/ui/realtime_window/`）一期整合后下线。
4. ✅ 中栏文本对话**支持历史会话恢复**。
5. ✅ /voice 待机电源驻留低功耗聆听**保留，加开关**。
6. ✅ 左栏新增**历史语音对话记录列表**，按钮切换"历史列表 / 模型音色选择"两个面板。

## 七、一期落地复盘（2026-08-30）

- `agent/ui/workbench/` 新包 7 个 Python 模块 + 4 个前端资源落地；测试 18 用例，全量回归 1638 passed。
- 实现口径与计划的差异：
  - 单实例聚焦未降级为锁文件轮询，直接用端口 47812 监听（锁文件仅作兜底诊断）；**不可设 SO_REUSEADDR**（Windows 会双绑定）。
  - /talk 整合：工作台左栏实时模式直接内嵌 `RealtimeTalk`（非复用 `WebviewRealtimeTalkUI` 独立窗）；`agent/ui/realtime_window/` 包保留，仅 REPL `/talk` 在用，待后续整合下线。
  - 右栏三件套用轻量 `workbench/metrics.py`（psutil + shutil）落地，未拉起休眠的 `monitor.py`（阈值告警留二期）。
  - 全局热键召唤窗口一期未接线（模块保留，二期接）。
- 待实机验证：`python -m agent.daemon.autostart desktop` 重建快捷方式后双击验证（透明效果依赖 WebView2 版本，不透明时降级深色底）。

## 八、实机反馈修复复盘（2026-08-30 第二轮）

实机测试反馈两个问题：背景完全白色（桌面透不出来）、反应炉动画观感差。修复：

1. **白底根因**：pywebview `transparent=True` 只透明 WebView2 控件，宿主 WinForms Form 仍不透明（#F0F0F0）。改为 `frameless=True` + shown 后经 `form.Invoke` 打 `AllowTransparency=True + BackColor=Transparent` 补丁，实现像素级透明；窗口尺寸用 `SPI_GETWORKAREA` 取工作区（maximized 与 AllowTransparency 互斥，且无边框最大化会盖任务栏）。
2. **无边框配套**：页面自绘标题栏（整条 `.pywebview-drag-region` 拖动），最小化/关闭按钮经新增 `window_minimize / window_close` API 转主进程（全屏按钮后续移除，见第 9 条）。
3. **reactor.js 重制**：对标反应炉标志造型——炽白核心呼吸、10 块三角线圈段轮流点亮、分段弧环正反向旋转、刻度环、雷达扫掠（createConicGradient 带降级）、~260 粒子预渲染辉光精灵提速；说话时速度/能量/亮度联动，公共 API 不变。
4. **任务栏图标对齐桌面图标**：任务栏显示 pywebview 默认图标 → 改走官方 `webview.start(icon=~/.jarvis/jarvis.ico)`（与桌面快捷方式同源，不存在时经 `ensure_icon()` 生成）。早期方案在透明补丁里后置 `form.Icon` 会被 pywebview 构造逻辑覆盖，不可靠，已改由 Form 构造时即生效。
5. **板块边界加深（三轮）**：用户多轮反馈线条/字体不够明显后，边框色集中到 `--edge-strong/--edge-mid/--edge-soft` 三个 CSS 变量（最终 0.85/0.68/0.58）；三栏玻璃板与中栏容器 1.5px 近不透明亮蓝边 + 外发光；小框（列表项/指标卡/分段按钮）边框 1.2px；字体整体加大加粗提亮（列表 13px/500、指标 13px/600、次级文字 +1px 且颜色提亮）。后续调边界只需改 `:root` 变量。
6. **模型列表补全**：左栏模型面板只列当前+自定义，漏了内置表 → `list_models()` 对齐 REPL `/models` = 内置（`[llm.models]`）+ 自定义（`[llm.custom_models]`）合并去重，当前模型置顶标 current 并带内置描述；厂商复用 `model_manager._infer_model_vendor` 推断；新增 1 条单测（1639 passed）。
7. **列表划不动（两轮）**：第一轮把「模型音色」拆成「历史会话 / 模型 / 音色」三 tab 各自独占全高 + 滚动条加宽加亮后仍不能滚。真正根因：三栏是 `#workbench` grid 的项，**grid 项默认 `min-height: auto`**，长列表把栏目撑得比窗口还高，内容溢出铺出窗口，内层 `.list-area` 拿到足够高度永远不产生溢出 → 无滚动条、滚轮无处可滚。修复：`.glass-col` / `#center-col` 补 `min-height: 0`（高度受行高约束，溢出落回滚动容器），`.list-area` / `#chat-history` 同样置 0，`.list-item` / `.message` 加 `flex-shrink: 0` 防压扁。
8. **CPU 优化（风扇狂转）**：透明窗口走 WS_EX_LAYERED 软件合成，全屏 60fps canvas + 多处大半径 `backdrop-filter` 是 CPU 大头。修复：reactor.js 限帧 30fps + dpr 固定 1 + 粒子 260→160 + `document.hidden` 时跳过绘制；CSS 移除每条消息气泡的 `backdrop-filter`（随消息条数增长），玻璃面板/输入栏模糊半径 18→10、中栏 12→8。
9. **砍掉全屏按钮**：用户实测反馈真全屏覆盖桌面任务栏，两轮修（改铺满工作区）后用户拍板“不要全屏了”。窗口启动本就铺满工作区（不盖任务栏），▢ 按钮无存在意义 → 移除 `window_toggle_fullscreen` API、`#btn-max` 按钮与 JS 接线，标题栏只留最小化/关闭；工作区获取保留在 `window_geometry.work_area()`（建窗用）。
10. **任务栏图标发白/白板（多轮）**：第一轮换实底 ico（透明底 `jarvis.ico` 在任务栏浅色底上合成发白）——`autostart` 抽 `_draw_reactor_icon(solid_bg)` / `_save_ico` 复用，新增 `ensure_window_icon()` 生成深蓝实底 `~/.jarvis/jarvis_window.ico`；但实测仍是白板（pythonw.exe 回退图标）——**根因：`AllowTransparency=True`（WS_EX_LAYERED）后任务栏不再显示 `Form.Icon`**（pywebview `start(icon=...)` 构造时设置的被分层窗口特性吃掉）。第二/三轮修复：新增 `win32_icon.set_window_icon(hwnd, ico)` 经 `LoadImageW` + `SendMessage(WM_SETICON)` 直接对 hwnd 设大小两档图标；但实测仍白板——日志抓到 `int(form.Handle)` 抛 `TypeError`（pythonnet 的 `System.IntPtr` 不能 `int()` 直转），改 `form.Handle.ToInt64()` 后 WM_SETICON 已生效但任务栏仍白板。第四轮：**WM_SETICON / GCL 无效，Windows 10 任务栏真正按 AppUserModelID 显示图标**——新增 `set_current_process_app_user_model_id()` + `register_app_icon()` 在 `HKCU\Software\Classes\AppUserModelID` 下注册 AUMID 与图标映射；同时给桌面/自启快捷方式设置同一 `AppUserModelID`（`AceFelix.JARVIS.Workbench`），实现任务栏正确显示深蓝反应炉图标。桌面图标保持透明底不变。
11. **左栏悬停/选中字体变蓝**：用户要求选项/按钮鼠标浏览到或选中时字体变蓝。修复：`.seg-btn` 悬停与 `.active`、`.list-item` 悬停均 `color: #5bc8ff`（主题蓝），`.list-item.current` 亮蓝 `#7fe3ff`，`#new-session-btn` 悬停同步；中栏发送按钮不受影响。
