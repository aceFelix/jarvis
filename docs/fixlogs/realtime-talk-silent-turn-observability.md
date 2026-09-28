# 实时双工语音 `/talk`「我说了它不回复」排查与第 1 层可观测性落地

## 问题现象

用户实机使用 `/talk` 时，屏幕出现以下现象：

- JARVIS 把自己刚说的话又复读了一遍（AI 音色经扬声器回授被服务端识别）
- 用户接着说了「变性了，兄弟。」「咋回事儿？」，这两句与 AI 原话既不重合、也是明确的追问
- JARVIS 全程没有任何回应，屏幕上也一个字都没显示

> 用户原话：「jarvis的 `/talk` 实时语音链路是怎样的，怎么一直我在说话，他不回复我啊」

关键点：**屏幕与扬声器同时静默**——不是"回复了但没声音"，也不是"识别了但没回复"，
而是这一段语音在客户端完全不可见。

---

## 排查过程

### 第一阶段：摸清链路与实机环境

- 通读 `agent/voice/realtime_talk.py`（当时 820 行），确认五路并发协程：
  `_send_audio`（麦克风 → AEC → 回声衰减 → `input_audio_buffer.append`）、
  `_recv_events`（服务端事件分发）、`_esc_watcher`、`_load_mcp_tools_async`、`_watchdog`
- 实测依赖：`aec-audio-processing` **NOT INSTALLED**，`dashscope` / `pyaudio` / `numpy` 正常
  → 当前跑的是 smart_turn 语义防回声降级路径，不是 AEC 路径

### 第二阶段：核对服务端契约（推翻第一版方案）

通读 `dashscope-docs/阿里云实时语音对话实现文档.md` 后确认两件事：

1. `turn_detection.type = "smart_turn"` 下，`silence_duration_ms` / `vad_threshold` 等
   VAD 参数**一律无效**（官方文档原文：这些参数仅在 server_vad 模式下可调）
   → `realtime_talk.py` 里的 `_silence_ms` / `_vad_threshold` 是**死参数**
   （且字段名也不对，server_vad 下发的是 `threshold`）
2. smart_turn 判为非有效轮次时，ASR 结果走
   `conversation.item.ambient_audio_transcription.delta/.completed` 透传，
   **不写入对话上下文**，自然也不会触发 `response`

第一版方案里"把 VAD 参数接进 `session.update`"方向本身就是错的（B 项），
核对文档后当场向用户纠正：A 项（播报期不送音频）只是降级止血，且应送静音帧而非不送。

### 第三阶段：定位根因

对照事件分发表发现：`_recv_events` **只订阅了
`conversation.item.input_audio_transcription.completed`（有效轮次）**，
完全没有处理 `ambient_audio_transcription.*`。

→ 服务端"听到了、但判为非有效轮次"的那段语音，客户端既没订阅、也没打印，
屏幕自然全静默。用户看到的"它不回复"，其中相当一部分其实是
**"话被语义过滤了，而这条通道在客户端根本不存在"**。

---

## 根因

`smart_turn` 的两条转写通道不对称：有效轮次通道已订阅并渲染 `🧑 你:`，
非有效轮次通道（`ambient_audio_transcription`）未订阅。后者一旦触发，
用户侧完全观测不到，既无法判断"到底听没听到"，也无法区分
"被语义过滤"与"进了对话轮却没回复"。

---

## 改动（第 1 层：可观测性，零行为风险）

### 先拆后改（文件已超 800 行上限）

按代码结构规则单文件 ≤800 行的硬约束，`realtime_talk.py` 当时已 820 行，
**必须先拆再改**：

| 新文件 | 行数 | 职责 |
|---|---|---|
| `agent/voice/realtime_tools.py` | 214 | 工具层：`BUILTIN_TOOLS` / `execute_builtin_tool` / `build_all_tools` / `execute_tool` |
| `agent/voice/realtime_events.py` | 195 | 可观测层：`TalkEventLog`（环境音转写 + 事件时间线 + 轮次统计） |
| `agent/voice/realtime_talk.py` | 820 → 711 | 会话轮次编排 + 事件分发（工具块迁出，留注释指向新模块） |

搬迁时保留契约：拆分后用参数化断言锁定旧私有名（`_BUILTIN_TOOLS` /
`_build_all_tools` / `_execute_builtin_tool`）**必须取不到**，防回潮。

### 可观测性三项

1. **环境音转写显式显示**：新分支处理 `AMBIENT_DELTA` / `AMBIENT_COMPLETED`，
   delta 按 `item_id` 累积、completed 定稿后打印一行
   `🔇 未入对话轮（服务端判为非有效语音）: …`，与 `🧑 你:` 形成双通道对照
2. **会话诊断摘要**：`finally` 中输出 `📋 本次有 N 段语音被服务端判为非有效轮次…`
   + 轮次计数（`user_turns` / `ai_turns` / `ambient_turns` / `interrupts`），
   **只在确有语义过滤时输出**，正常对话不受打扰
3. **事件时间线日志**（可选）：`[realtime_talk] event_log`（默认关）开启后，
   每条事件按序写 `~/.jarvis/logs/diag.log`（component=`realtime`）；
   高频的 `response.audio.delta` / `response.audio_transcript.delta` **只计数不落盘**，
   避免刷爆 1 MB 轮转日志

启动横幅加一行图例：`识别结果：✅ 进入对话轮 ｜ 🔇 未入对话轮（服务端判为非有效语音）`。

### 配置面四处接线

| 文件 | 改动 |
|---|---|
| `agent/config/settings.py` | 新增 `realtime_event_log: bool = False` + 键列表 + `[realtime_talk] event_log` 映射 |
| `configs/settings.example.toml` / `agent/configs/settings.example.toml` | 新增 `[realtime_talk]` 段 |
| `agent/commands/handlers/voice_commands.py` | `/talk` 装配点透传 `event_log` |
| `agent/ui/workbench/engine.py` | 工作台装配点透传 `event_log` |

### 实现中修正的一处瑕疵

`_brief` 原本对只有 `type` 的事件回落整段 JSON，把 `{"type": "..."}` 拼进日志行，
时间线变成 `#1 input_audio_buffer.speech_stopped {"type": "..."}` 的冗余形态。
改为回落路径排除 `type` 键、剩空则返回空串，时间线保持 `#序号 事件类型` 干净形态，
并补测试 `test_type_only_event_returns_empty` 锁定。

---

## 验证

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis; python -m pytest tests/voice -q
```

- `tests/voice/` **147 passed**
- 新增 `tests/voice/test_realtime_observability.py`（9 个测试类），
  其中 `TestRecvEventsWiring` 用 `_FakeWS` + `_FakeTalkUI` 直接喂事件给 `_recv_events`，
  断言 `🔇` 行的出现/不出现、四项计数与时间线内容
- `tests/voice/test_talk_terminal.py` 补 `event_log` 透传断言

---

## 经验总结

### 1. 客户端不订阅的事件等于不存在

服务端"听到了但语义过滤"的通道若不订阅，用户侧表现为"完全没反应"，
与"没听到"不可区分。可观测性改造必须覆盖**所有**可能承载用户语音的事件类型，
而不是只处理"正常路径"那一条。

### 2. 先核对服务端契约，再决定改什么参数

`smart_turn` 下 VAD 参数无效是官方文档明写的事实，但代码里仍留着两个死参数。
改行为类参数前必须回查文档，否则改动无效还难排查。

### 3. 零行为风险的可观测性应是排查第一步

不改 VAD、不改增益、不改 `session.update`，只增加可见性——付出极小，
却能直接把"话去了哪条路"从猜测变成事实。

### 4. 高频事件不能逐条落盘

音频 / 转写 delta 每几十毫秒一条，逐条写 1 MB 轮转日志会瞬间刷爆、
把有价值的时序信息挤掉。只计数、不落盘是必要条件。

### 5. 触达 800 行上限时先拆再改

本次 `realtime_talk.py` 已 820 行，若不先拆，可观测性改动会把它推到更不可维护的规模。
拆分按职责切（工具层 / 可观测层 / 编排层），边界干净、外部只依赖 `RealtimeTalk`
与 `DEFAULT_WS_URL`，改动面可控。

### 固化为规则

- 实时语音新增能力时，新增事件类型**必须**在 `_recv_events` 有明确分支或计数
- 高频音频 / 转写 delta 事件**禁止**逐条写轮转日志，只允许计数
- 排查"无响应"类问题**必须**先落地零行为风险可观测性，再改行为
- 单文件触达 800 行上限时**必须**先按职责拆分再叠加新功能

---

## 后续层（尚未实施，按证据推进）

1. **说话人增强**：`turn_detection.voiceprint_audio_urls` 声纹锁定，从源头拒掉 AI 音色回声
   （需公网可访问的声纹音频）
2. **AEC 实测**：装 `aec-audio-processing` 后复测，确认回声是否被物理消除
3. **半双工兜底**：播报期送静音帧（而非不送），牺牲打断换取稳定性

---

## 变更文件

| 文件 | 说明 |
|---|---|
| `agent/voice/realtime_tools.py` | 新建，工具层（214 行） |
| `agent/voice/realtime_events.py` | 新建，可观测层（195 行） |
| `agent/voice/realtime_talk.py` | 拆出工具层 + 接入可观测性（820 → 711 行） |
| `agent/config/settings.py` | `realtime_event_log` 字段与 TOML 映射 |
| `configs/settings.example.toml` / `agent/configs/settings.example.toml` | `[realtime_talk]` 段 |
| `agent/commands/handlers/voice_commands.py` | `/talk` 装配点透传 |
| `agent/ui/workbench/engine.py` | 工作台装配点透传 |
| `tests/voice/test_realtime_observability.py` | 新建，9 个测试类 |
| `tests/voice/test_talk_terminal.py` | 补 `event_log` 透传断言 |
| `docs/architecture/06-语音系统.md` | 新增"环境音转写与事件时间线"整节 |
| `docs/architecture/12-配置系统.md` | `event_log` 映射说明 |
| `docs/architecture/01-总体架构.md` | 目录树 |
| `config-docs/voice-setup.md` / `README.md` / `README.en.md` | 配置示例与目录树 |
