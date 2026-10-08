# 桌面工作台与多端接入

> [← 返回 README](../../README.md) · [语音系统](voice.md) · [命令参考](commands.md) · [跨设备与微信架构](../architecture/14-跨设备与微信接入.md)

---

## 三栏工作台（`--gui`）

```bash
jarvis --gui           # 启动三栏工作台窗口（--talk 与 --gui 等价）
```

双击桌面「JARVIS」图标（或开机自启）打开单窗口三栏工作台：
透明背景透出桌面，方舟反应炉淡蓝动效居中律动（核心呼吸 + 三角线圈轮流点亮，说话时加速）。

- **左栏**：模式切换（💬 文本 / 🎙️ 实时）+ 三面板切换（📜 历史会话 ⇄ 🤖 模型 ⇄ 🎵 音色）
- **中栏**：气泡对话流（流式渲染；思考块与本轮连续工具调用在回复结束后各自折叠成一行，可点开查看）+ 文本输入框，支持历史会话恢复
- **右栏**：CPU / 内存 / 磁盘实时指标
- **窗口行为**：无边框铺满工作区启动（不盖任务栏；自绘标题栏可拖动，带最小化/关闭按钮，不提供全屏）、单实例（二次双击唤起已驻留窗口）
- 日志位于 `~/.jarvis/workbench.log`

> 📌 **架构说明**：原「无窗口 daemon + 托盘遥控」常驻模式（`--daemon`、pystray 托盘菜单、
> 托盘语音/文本终端派生）已于 2026-08 下线；原 `--talk` 独立实时窗口已合并进工作台。
> 完整开发计划见 [docs/plans/workbench-gui.md](../plans/workbench-gui.md)。
>
> 📌 **桌面入口演进**：桌面快捷方式（`autostart desktop`）已于 2026-09 下线，
> 桌面入口由 **jarvis-desktop**（Electron 桌面应用，独立仓库）接管；
> 本仓库终端内仍可用 `jarvis --gui` 打开三栏工作台窗口。
>
> 📌 **桌面安装包后端冻结**：本仓库 `packaging/` 存放把 `agent.serve` 用 PyInstaller 冻结成
> 独立 `jarvis-serve.exe` 的入口（`serve_entry.py`）、打包规格（`jarvis-serve.spec`）与构建
> 脚本（`build_serve.ps1`）；产物 `dist/jarvis-serve/` 由 jarvis-desktop 的 `npm run dist` 经
> `extraResources` 拷入 NSIS 安装包，使终端用户无需安装 Python。

## 开机自启

```bash
python -m agent.daemon.autostart install            # 安装开机自启
python -m agent.daemon.autostart uninstall          # 卸载开机自启
python -m agent.daemon.autostart status             # 查看状态
```

| 平台 | 开机自启 |
|---|---|
| Windows | Startup 文件夹 .lnk（指向静默 VBS，打开三栏工作台） |
| macOS | LaunchAgent plist（`launchctl load`） |
| Linux | 不支持（提示手动 systemd） |

> 📌 桌面快捷方式（`desktop` / `desktop-uninstall` / `desktop-status` 子命令）
> 已于 2026-09 下线：桌面入口由 jarvis-desktop 桌面应用接管。存量桌面 JARVIS.lnk 可直接手动删除。

## 全局热键（保留）

全局热键能力已保留（Windows 默认用原生 `RegisterHotKey`），预留新一代 GUI 工作台的「热键召唤窗口」场景：

```toml
[daemon]
hotkey = "ctrl+shift+j"        # 全局热键
hotkey_native = true            # Windows 优先使用 RegisterHotKey（更快）
hotkey_debounce_ms = 200        # 去抖毫秒，防止一次按下触发多次
```

REPL 仍可用 `--quick` 快速启动，跳过开机动画、MCP、LSP 等可选初始化，首次调用相关命令时再懒加载：

```bash
jarvis --quick                # REPL 快速启动
```

## 主动提醒系统（P2-3，已接线）

主动感知能力（`agent.core.daemon` 下 Scheduler / ProactiveEngine / DeadlineTracker）
原由常驻 daemon 拉起，2026-08 随托盘下线休眠，**2026-09 已由 serve 宿主的
`ProactiveHub` 重新接线**：每日简报 / 对话内「提醒我」定时任务 / 截止日期检查到期后，
经 `proactive_notify` 事件推给 jarvis-desktop 桌面壳播报（聊天气泡 + Windows 系统通知），
**2026-09 二期起并行用本机 CosyVoice 做待机 TTS 朗读**（复活老 daemon「待机语音」通道：
对话/语音忙时跳过不打断，提醒加「先生，提醒您：」前缀，简报/截止日期只读前 200 字，
`proactive_tts_enabled` 可关，桌面壳设置面板亦可运行时开关——经 `settings.get/set` 写回 settings.toml）。
因 serve 随桌面壳启停，**播报仅在 `--serve` / 桌面壳运行期间生效**（错过依赖
`schedule.json` 补偿 + 简报补播窗口，默认 2 小时、`briefing_catchup_window_min` 可配）。
详见 [docs/plans/proactive-desktop.md](../plans/proactive-desktop.md)。能力清单：

**每日简报**：每天 08:30 自动播报今日概览（待触发提醒、节假日、系统状态、截止日期、日历事件）。

**截止日期追踪**：对贾维斯说"下周五之前交项目报告"，自动注册截止日期，分级提醒（提前 7/3/1/0 天 + 逾期每天）。

**提醒升级**：提醒触发后未确认会自动重复通知（5→10→20 分钟，最多 3 次），说"知道了"即可确认。

**日历集成**（可选）：读取 Outlook/ICS 日历事件，在简报中展示 + 提前 30 分钟提醒。

```toml
[daemon]
briefing_enabled = true
briefing_time = "08:30"    # 每日简报时间
briefing_catchup_window_min = 120  # 简报补播窗口（分钟）：错过 ≤ 此值启动补播一次；≤0 关闭
proactive_tts_enabled = true  # 待机 TTS 朗读（忙时跳过；TTS 参数复用 tts_model/tts_voice 等）

[deadline]
enabled = true
check_time = "09:00"       # 每日检查截止日期的时间

[calendar]
enabled = false            # 日历集成（需配置 Outlook 或 ICS）
backend = "auto"           # auto / outlook / ics
ics_path = ""              # 本地 .ics 文件路径
ics_url = ""               # 远程 .ics 订阅 URL
remind_minutes_before = 30
```

Agent 工具：

| 工具 | 说明 |
|------|------|
| `ScheduleReminder` | 安排定时提醒（"明天 3 点提醒我开会"） |
| `AddDeadline` | 注册截止日期（"下周五之前交报告"） |
| `ListDeadlines` | 查看活跃截止日期 |
| `CompleteDeadline` | 标记截止日期完成 |
| `AcknowledgeReminder` | 确认提醒（停止升级重复通知） |

## 系统资源监控（休眠态）

监控能力代码完整保留（`agent.core.daemon.monitor`），原由常驻 daemon 拉起，
当前处于休眠态，将由新一代 GUI 工作台重新接线。配置项预留：

```toml
[monitor]
enabled = true
cpu_threshold = 85.0       # CPU 超 85% 持续 30s 告警
memory_threshold = 90.0    # 内存超 90% 告警
disk_threshold = 10.0      # 磁盘剩余低于 10% 告警
check_interval = 10        # 检查间隔（秒）
alert_cooldown = 600       # 同类告警冷却（10 分钟）
# P2-3 增强
disk_trend_days = 7        # 磁盘趋势预测：预测几天后将满
high_cpu_duration = 600    # 异常进程：CPU > 50% 持续多少秒通知
work_break_interval = 7200 # 连续工作 2 小时提醒休息
```

## 跨设备协同（P3-1）

在终端输入 `/connect-phone`，电脑端会显示一个二维码，手机扫码即可连接当前 JARVIS 会话，出门在外也能远程操控电脑。

```toml
[bridge]
http_port = 8765               # PWA 页面端口
ws_port = 8766                 # WebSocket 通信端口
token = ""                     # 认证 token，留空自动生成
```

**使用方式**：
1. 在 JARVIS 终端输入 `/connect-phone`
2. 终端显示二维码和访问地址
3. 手机和电脑连同一局域网 Wi-Fi
4. 手机扫码或手动访问 URL 开始对话

终端效果示例：

```
🌐 跨设备协同已启动
   手机访问: http://192.168.1.100:8765/?token=a1b2c3d4e5f6g7h8
   手机和电脑需在同一局域网（Wi-Fi）

████  ████  █  ████  ████
█  █  █  █  █  █     █  █
...

提示: 手机扫码或手动访问上方 URL 即可开始对话
      输入 /connect-phone 可重新生成二维码
```

**核心特性**：
- **共享会话**：手机端与电脑端共享同一对话历史，手机上发的消息会同步到电脑终端
- **权限隔离**：手机端默认 PLAN 模式（只读），写操作需手机端确认
- **流式输出**：JARVIS 回复实时推送到手机端，支持 Markdown 渲染
- **工具调用可视化**：手机端可查看工具调用过程和结果
- **Token 认证**：每次 `/connect-phone` 自动生成 token，防止未授权访问
- **中断支持**：手机端可随时中断 JARVIS 的回复
- **终端会话生命周期**：随当前 JARVIS 终端退出而关闭

> 外网访问需配合内网穿透（如 frp、Cloudflare Tunnel）。

> **桌面端**：jarvis-desktop 输入栏的「跨设备协同」下拉按钮（`[LNK]`）可直接发起手机连接，
> 二维码内联显示在中间聊天区（重连时总在底部刷新，无需往上翻找）；手机发来的消息以带「手机」
> 标记的用户气泡上屏、每轮对话即时存到电脑会话历史；桌面文本 / 手机 /
> 微信三端共享同一会话并抢引擎唯一 query 锁串行发送（详见 jarvis-desktop README 与
> [docs/architecture/14-跨设备与微信接入.md](../architecture/14-跨设备与微信接入.md) 第六节）。

## 微信 ClawBot 接入

在终端输入 `/connect-wechat`，扫码连接微信 ClawBot，之后在微信中发消息即可与 JARVIS 对话（含完整工具调用能力）。

**使用方式**：
1. 在 JARVIS 终端输入 `/connect-wechat`
2. 终端显示二维码（或扫码链接）
3. 手机微信扫码并确认连接
4. 在微信中找到 ClawBot 发消息即可对话

**核心特性**：
- **官方接口**：基于腾讯 iLink Bot API，安全合规不封号
- **完整能力**：微信端可使用 JARVIS 全部工具（文件、命令、搜索等）
- **共享会话**：微信对话与电脑终端共享同一对话历史
- **24h 续期**：连接有效期 24 小时，到期前终端提醒重新扫码
- **长消息分段**：超过 2000 字自动分段发送

**依赖**：`pip install "jarvis-agent[wechat]"`（aiohttp + qrcode）

> 需微信版本 ≥ 8.0.70，设置 → 插件中可看到 ClawBot。

> **桌面端**：jarvis-desktop 输入栏的「跨设备协同」下拉按钮可直接发起微信连接，二维码内联聊天区（重连
> 时总在底部刷新），**扫上即连**（微信配对码为服务端偶发兜底，桌面不再内联输入）；微信发来的消息以带「微信」标记的
> 用户气泡上屏、每条回复各自成独立气泡、每轮对话即时存到电脑会话历史；与手机、桌面文本共享同一会话并串行发送。

## 外部前端接入（serve 模式）

```bash
jarvis --serve         # 启动 headless API 服务（不渲染本地 UI，供外部前端接入）
```

`--serve` 把 J.A.R.V.I.S 的对话引擎以 **WebSocket API** 形式对外服务，供 jarvis-desktop（Electron 桌面壳，独立仓库）等外部前端接入。它与 `--gui`/`--talk` 互斥：后者在本进程内渲染 pywebview 工作台，`--serve` 不渲染任何本地 UI，只装配与工作台**完全相同**的引擎零件（`ChatEngine` + `WorkbenchAPI` + `MetricsCollector`），经 `DesktopBridgeServer` 以 WS 事件流对外服务。

- **绑定收敛**：仅监听 `127.0.0.1` + 系统分配的随机端口，不对局域网暴露（这是与手机协同模式 `0.0.0.0` + 固定端口的关键差异）。
- **token 认证**：WS 连接须带 token（`ws://127.0.0.1:<port>/?token=xxx`），token 错误服务端以 `4401` 关闭。
- **就绪握手**：进程就绪后向 stdout 打印**单行** JSON，外部宿主（Electron 主进程）逐行解析：

  ```json
  {"type": "jarvis-serve-ready", "port": 51234, "http_port": 51235, "token": "<hex>", "pid": 999}
  ```

- **停机信号**：stdin EOF（父进程退出 / 杀管道）或 `SIGINT` 触发优雅停机（先关传输层，再停采集与引擎）。
- **依赖**：`websockets` 已为核心依赖（随 `pip install` 自动安装，2026-09 起）；仍保留缺失降级：import 失败时以退出码 `3` 报错退出。
- **主动播报（已接线）**：serve 宿主装配 `ProactiveHub`（复活 2026-08 下线托盘时休眠的主动感知套件），每日简报（默认 08:30）/ 对话内"提醒我"定时任务 / 截止日期检查到期后经 `proactive_notify` 事件推给桌面壳（聊天气泡 + 系统通知），二期起并行待机 TTS 朗读（`proactive_tts_enabled`，忙时跳过）。因 serve 随桌面壳启停，错过依赖 `schedule.json` 错过补偿 + 简报补播窗口（默认 2 小时、`briefing_catchup_window_min` 可配）。
- **半双工语音（已接线）**：`/voice` 已从 RichCLI 解耦（`VoiceSessionEvents` 协议 + 双适配器），照 `/talk` 模式经 serve 桥接进桌面壳：指令 `voice.{start,stop,interrupt}`、事件 `voice_started/stopped/state/user_transcript/ai_text_delta/ai_text`，与 `/talk` 互斥。**音频 I/O（STT 录音 / TTS 播放）留在 serve 子进程本机 pyaudio**（与桌面壳同机出声），不向桌面壳传音频流；桌面壳只做遥控器 + 状态/文字显示（打断为按钮 + 麦克风 barge-in 双通道）。
- **全双工实时语音音频桥（2026-09-28 已接线）**：`talk.start` 带 `duplex: true` 时，音频不再走 serve 本机 pyaudio，而是双向桥接：渲染进程 `getUserMedia`（浏览器 AEC）采集 16kHz PCM16 经指令 `talk.audio`（base64 帧，fire-and-forget）上行喂 `BridgeMic` → `RealtimeEngine`；AI 24kHz 语音帧经事件 `talk_audio` 下行，由 Web Audio 顺序排播；打断时下行空 payload 表示 flush。浏览器系统级回声消除使桌面壳成为**说话即打断的真全双工**（引擎路径内自动关半双工/软件压低/软件 AEC）。采集/播放实现见 jarvis-desktop `src/renderer/src/audio/`。
- **停止回复（已接线）**：指令 `reply.abort` 经 `ChatEngine.abort_current_reply()` 线程安全取消当前 send 任务（不入指令队列，避免串行自死锁）；取消路径仍发 `assistant_done` 收尾 + info「已停止回复」，Bash 子进程被同步回收不留孤儿。桌面壳发送按钮回复中变「■ 停止」，再点即发此指令。**任意来源都能停**（2026-10）：手机 / 微信 / 主动任务发起的轮次经桥接 `on_query_begin`（引擎 `_remote_query_begin`）把当前任务登记为 `_send_task`，故 `reply.abort` 对非桌面本地输入的在跑轮次同样生效；桌面侧 `busy` 改由引擎活动事件（`assistant_text`/`assistant_thinking`/`tool_use`）驱动、`assistant_done` 统一撤销，不再只靠本地发送置位。
- **消息附件（已接线）**：`message` 指令可选 `images`（`[{data: base64, media_type}]`，≤8 张）与 `files`（`[{name, content}]`，≤5 个文本文件）：图片转 `ImageContent` 走 vision 链路（与 REPL `/image` `/paste` 同一底层），文件由引擎拼进消息正文的「附带文件」代码块（超 2 万字符截断）；上限在 `serve/server.py` 入队校验快速失败。桌面壳入口为输入栏 📎 按钮（多选）与粘贴事件，纯图片消息也可发送。
- **项目工作区（桌面壳，2026-08）**：左栏**底部**常驻「项目」区（面板区之后、状态栏之前，配色随主题皮肤）：`＋ 打开文件夹` 由主进程目录选择器取绝对路径后发指令 `project.set`，后端二次校验（存在 + 绝对，不自动建目录）→ 入队引擎线程内串行重建系统提示词 / 重挂 harness / 开新会话，落地推 `project_switched`；最近项目走 `projects.list`（点击即切、右键「从列表移除」= `projects.forget`，只清 `~/.jarvis/projects.toml` 记录、不删磁盘）；serve 启动默认 workdir 取该文件 `last_active`，实现「重开回到上次项目」。另有：`init`（每连接首帧）把左栏状态栏从启动期的「等待后端启动...」切到「就绪」；后端进程未重启（无 `project.*` 注册）时点选会秒级上屏「后端不支持指令 project.set（…请重启后端后重试）」而非干等超时。
- **右栏四区块（桌面壳）**：任务中心（`schedule.list` 待触发提醒 + 活跃截止日期倒计时 + 最近简报）、会话与用量（`cost.get`，口径同 REPL `/cost`：token 四类累计 + 缓存命中率 + 轮数/消息数，命中率统一由 `Usage.cache_hit_rate` 按协议口径算好、前端不重算；附上下文窗口占比 `context_*`，口径同 REPL `/context`，由引擎只读属性 `context_usage` 经 `get_cost` 透传，2026-10）、系统状态三指标卡、运行健康（`state.get` 的 `mcp` 连接快照 + 事件日志流）；快捷操作（🗜 手动压缩上下文—点击经 `slash.exec` 透传 `/compact`、结果走 `slash_result` 命令输出卡片／新会话／停止回复／复制最后回复）已迁入输入栏（曾有的 📸 主屏截屏因实用性低已于 2026-10 删除）。刷新时机：init 十路齐刷（含设置回填、协同回填与 slash.commands 补全目录，2026-10）、assistant_done 刷用量、proactive_notify 刷任务列表、`mcp_ready` 刷运行健康（MCP 为后台预热约 9s，init 时快照常为 null，"未启用"，连接落定后推 `mcp_ready` 事件驱动右栏补刷）。
- **添加模型（桌面壳，2026-09）**：左栏模型面板列表末项「＋ 添加模型」（虚线框）→ 点击后独立组件 `ModelForm` 整体替换列表（同右栏设置面板模式），六个字段（模型厂商/模型名/API Key/接口类型/Base URL/模型类型）与 REPL `/models` → 添加其他模型完全同口径；提交走 `models.add` 指令：serve 二次校验（模型名必填、接口类型/模型类型白名单）→ 复用 `save_custom_model` 写用户级 `~/.jarvis/models.toml` 的 `[llm.custom_models."<name>"]`（API Key 同步系统 keyring）+ `_infer_base_url` 推断空 Base URL → 成功后壳刷 `models.list` 并提示「模型「X」已添加」，失败保持表单打开可修正。表单字段区带 `.form-scroll` 滚动容器（面板高度不足时自身滚动，不溢出压到左栏底部「项目」区；报错与保存/取消常驻滚动区之外，2026-09-30），音色表单 `VoiceForm` 同口径。
- **修改与删除模型配置（桌面壳，2026-09）**：左栏模型项交互对齐会话列表 —— **双击**模型项进 `ModelForm` 编辑该模型（预填 `models.list` 每项 `config` 现值），**右键**模型项则项内出现删除按钮、再点才真删（二次确认）。编辑走 `models.edit` 指令（`name` 必填，`new_name`/`vendor`/`api_format`/`base_url`/`api_key`/`model_type` 留空表示不改）：内置模型（命中项目级 `[llm.models]`）**名字锁定不可改**（改名只会产生「幽灵模型」），自定义模型可改名（写新段删旧段，`api_key` 留空则**保持原 Key** —— 桌面壳不回显密钥，与 REPL「留空即清空」刻意不同）；改的是当前运行模型时 serve 侧入队 `{"cmd": "switch_model", "force": true}` **强制重建 provider**，端点/接口类型改动立即生效（回执带 `hot_switched`，壳提示「当前会话已按新配置重连」）。删除走 `models.remove`：仅自定义模型可删（内置模型与「用户级 models.toml 无该段」均回 ok=false，后者防「删不掉但重启复活」），删的是当前模型时回执 `was_current` 且**不动运行中的 provider**（提示用户另选）。
- **音色管理（桌面壳，2026-09-28）**：左栏「音色」面板与模型面板同范式 —— `voices.list` 返回**全量音色目录**（内置 + 自定义，每项 `{name, voice_id, description, vendor, model, linked, current, custom}`，当前音色置顶；副行透出「适配 X」/「联动 X」预告）；点选音色走 `voices.select`（回执从 bool 升级为 `{ok, name, voice_id, linked_model, old_model}`：与终端 `/tts-voice` 同口径立即写盘并在不兼容时**自动联动 tts_model**，`linked_model` 带回壳提示「联动 TTS 模型 X，下次语音生效」）；末项「＋ 添加音色」表单提交 `voices.add`（name/voice_id 必填、内置名遮蔽拒绝，upsert 即编辑——双击自定义项进表单预填）；右键自定义项显删除按钮、再点发 `voices.delete`（仅 custom 可删，后端经 `remove_custom_voice` 外科式删 `models.toml` 段）。
- **设置面板（桌面壳，2026-09；同年 09 第一批扩键）**：设置独立成面板（标题栏齿轮进入，整体替换右栏信息面板）：外观（主题/语言，纯前端 localStorage 偏好）+ 后端联动三组（经 `settings.get`/`settings.set` 与 serve 联动：校验→先外科式落盘 settings.toml 对应节→再改运行时，失败回滚）：语音播报（待机 TTS 开关 + 音量/语速）、每日简报（开关 + 时间）、截止日期追踪（开关 + 检查时间）；简报/截止日期改动额外触发 `ProactiveHub` 调度热重注册（无需重启）。白名单单一真源在 `agent/config/desktop_settings.py`（密钥/自由路径永不入协议）。
- **未知指令失败回执（2026-09 加固）**：WS 分发对**未注册**的指令 type 立即回 `{"event":"reply","data":{"type","ok":false,"error"}}`（旧行为是静默忽略，既不回 ok 也不回 error），错误文案为「后端不支持指令 X（后端进程可能未加载最新代码，请重启后端后重试）」；缺 `type` 字段同样回失败回执。原因是前端（Vite 热更新）可能先支持新指令、而后端进程仍是旧代码（`python -m agent.serve` 不热重载），静默丢弃只会让桌面壳干等到 15s 超时、用户看不到任何原因（典型症状「指令 models.add 回执超时」）；手机 PWA 不消费 `reply` 事件，行为不受影响。
- **子进程 stdin 隔离（2026-09 修复）**：Bash 工具与沙箱执行器创建子进程时显式 `stdin=DEVNULL`，不再继承宿主 stdin——serve 宿主的 stdin 是 Electron 永不关闭的管道且有 watch 线程阻塞读，MSYS2 bash 继承后会挂死（工具永不返回），见 [docs/fixlogs/serve-bash-hang-fix.md](../fixlogs/serve-bash-hang-fix.md)。另 `ask_user` 新增异步版 `ask_user_async`，权限询问不再阻塞引擎事件循环。
- **斜杠命令透传（`slash.exec`，2026-10）**：桌面壳输入框识别 `/` 前缀，经 `slash.exec` 指令把命令原文转发到引擎侧新模块 [agent/ui/workbench/slash_bridge.py](../../agent/ui/workbench/slash_bridge.py)，复用终端同一个 `dispatch_command` 执行、捕获 stdout 输出以 `slash_result` 事件（`{command, ok, text}`）回推，桌面渲染成命令输出卡片——一次改动把 `/compact` `/context` `/cost` `/diff` `/doctor` `/tools` `/mcp` `/skills` `/memory` `/plugin` 等一大批终端能力带进桌面。三道护栏：白名单只放行非交互命令（桌面已有原生控件的 mode/think/model/sessions/rewind 等不透传，防双入口口径漂移）；交互禁令（执行期临时把 `pick_from_list` / `form_input` / `ask_user` / `terminal_picker` 等换成抛错实现——serve 的 stdin 是协议管道，任何命令试图交互都干净失败而不挂起、不抢管道）；白名单外命中已安装技能（`/<skill-name>`）动态放行，与手机/微信共用 query 锁串行、可被 `reply.abort` 停止、轮后正常落盘。
- **斜杠命令补全目录（`slash.commands`，2026-10）**：新增第 47 条只读指令 `slash.commands`，返回桌面可执行斜杠命令目录 `[{name, description, source}]`（`source`：passthrough=白名单透传 / native=桌面原生控件对应命令 / skill=已安装技能），数据源 [agent/ui/workbench/slash_bridge.py](../../agent/ui/workbench/slash_bridge.py) 的 `build_desktop_commands`——口径与 `run_slash` 执行护栏严格对齐，补出来的每条命令必然可执行。桌面壳据此实现输入框 `/` 前缀弹层补全（输 `/c` 匹配所有 c 开头命令、输 `/` 展示全部），手感对齐终端 REPL。

协议契约（指令 / 事件 schema）唯一来源在 [agent/serve/protocol.py](../../agent/serve/protocol.py)：47 条桌面指令（`message` / `sessions.*`（含 `rename` / `delete`） / `models.*`（含 `add` 添加自定义模型 / `edit` 修改配置 / `remove` 删除模型） / `voices.*`（含 `add` 添加自定义音色 / `delete` 删除音色，`select` 回执带 tts_model 联动） / `metrics.get` / `state.get` / `schedule.list` / `cost.get` / `answer_user` / `reply.abort` / `mode.set` / `think.set`（2026-09 工作模式与思考强度选择器） / `checkpoint.*`（`preview` 撤回前预览 / `rewind` 消息级回溯与文件回滚，2026-10） / `slash.exec`（斜杠命令透传，结果走 `slash_result` 事件，2026-10） / `slash.commands`（斜杠命令补全目录，只读，2026-10） / `talk.*`（含 `talk.audio` 上行音频帧） / `voice.{start,stop,interrupt}` / `proactive.ack` / `settings.{get,set}` / `project.{set,get}` + `projects.{list,forget}`（2026-08 项目工作区） / `phone.*` / `wechat.*`（协同与配对管理））+ 对话流 / 会话 / 提示 / 指标 / 实时语音（含 `talk_audio` 下行音频帧） / 半双工语音（`voice_*`） / 主动播报（`proactive_notify`） / 项目热切换（`project_switched`，2026-08） / 命令输出（`slash_result`，2026-10）事件。架构细节见 [docs/architecture/07-UI层.md](../architecture/07-UI层.md) 的「外部前端接入（serve 模式）」与「项目热切换」小节，立项计划见 [docs/plans/jarvis-desktop.md](../plans/jarvis-desktop.md) 与 [docs/plans/proactive-desktop.md](../plans/proactive-desktop.md)。
