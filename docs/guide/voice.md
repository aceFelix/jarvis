# 语音系统

> [← 返回 README](../../README.md) · [命令参考](commands.md) · [桌面与多端](desktop.md) · [语音配置详解](../../config-docs/voice-setup.md)

Jarvis 提供两套独立的语音系统：

| 模式 | 技术路线 | 特点 |
|---|---|---|
| **`/voice` 语音对话** | STT → LLM → TTS 管线 | 识别→思考→朗读，逐轮对话 |
| **`/talk` 实时聊天** | 全双工 WebSocket 直连 | 终端半双工轮替；桌面壳全双工（说话打断） |

> 两套系统独立运行，但共用麦克风硬件。同时开启可能导致 PyAudio 设备冲突。

---

## 语音对话 `/voice`

进入语音对话模式后，形成 **听 → 想 → 说** 闭环：

```
🎤 聆听 → STT 识别 → LLM 思考回答 → TTS 朗读 → 🎤 聆听 → ...
```

- **语音输入**：单一 STT 后端 **QwenASR**（`settings.toml` 的 `[stt].model`，默认 `qwen3-asr-flash-realtime`）
  - WebSocket（OmniRealtimeConversation）流式识别，服务端 VAD 断句，中英混合强
  - 对话聆听与待机唤醒共用同一实例；识别语言由 `language` 参数控制（默认 `zh`）

- **语音输出**：两种 TTS 模式
  - **CosyVoiceTTS**：整段合成播放（`cosyvoice-v3-flash` / `v3-plus` / `v3.5-plus`）
  - **StreamTTSPlayer**：WebSocket 流式合成，LLM 逐句输出 → 即时合成播放，首句延迟 ~500ms
  - 默认音色 `longanlang_v3`；内置 7 个音色，`/tts-voice` 可切换或添加自定义音色（音色带「适配模型」字段：系统音色按模型系列隔离、声音复刻绑定 target_model，切换时不兼容自动联动切 `tts_model`）
- **打断机制**：ESC 键打断当前 AI 播报，或说"退下"退出语音模式
- **思考隔离**：思考过程只显示在终端面板，不进入 TTS
- **内容清洗**：自动过滤代码块、表格、链接等不适合朗读的内容

## 实时双工 `/talk`

基于 DashScope 实时语音 WebSocket 服务（`qwen-audio-3.0-realtime-flash`），2026-09-28 重构为
**传输无关引擎（`RealtimeEngine`）+ 终端/桌面双适配器**：

- **server_vad 轮次检测（默认）**：声学 VAD + 静音时长判停，尾音延长当前轮而非误触发新
  轮次；`turn_detection = "smart_turn"` 可切语义判停（支持环境音转写/声纹增强）
- **半双工轮替（终端默认）**：AI 说话期间静音麦克风（发静音帧维持送流），外放稳定多轮；
  戴耳机设 `half_duplex = false` 恢复随口打断
- **桌面端真全双工（jarvis-desktop）**：浏览器 `getUserMedia` 系统级 AEC 消除回声，音频经
  serve 协议（`talk.audio` 上行 / `talk_audio` 下行）桥接，说话即打断，无需配置
- **响应救援（rescue）**：服务端吞轮（取消后不补答/建响应超时）时客户端补发
  `response.create` 兜底，防"说了话永远不回复"
- **AEC 回声消除（终端可选）**：基于 WebRTC AEC3（`aec-audio-processing`），未安装时靠
  半双工静音从源头防回声
- **Function Calling**：默认 `tools_mode = "builtin"`——只注册 get_current_time /
  end_conversation 两个工具，低延迟优先。
  **注意**：`"all"`（装配 ToolRegistry 全部 + MCP，实测 294 个 schema）会让响应创建
  延迟随工具数近似线性增长（探针实测 2 个 0.67s → 294 个 3.45s → 450 个 4.89s），
  该延迟与响应救援、server_vad 尾音重检叠加会造成响应被取消的死循环——用户说完话
  半天无回复（2026-10-09 曾误把默认改为 `"all"` 造成实时语音完全不可用，已回退）。
  **需要调用工具时请走半双工语音 `/voice`**（走标准 LLM API，天然支持全量工具）。
  若确要在实时语音里用工具，精选 10~20 个常用工具子集（勿一次性注册全量）并实机验证。
  高风险操作先语音确认再执行
- **纯终端 UI**：转录文字流实时显示（2026-09 起不再弹出 pywebview 独立窗口）
- **图形化实时聊天**：由三栏工作台（`--gui` 中栏实时模式）与 jarvis-desktop 桌面应用（真全双工）承担
- 退出方式：ESC 键或说"退下"

> **AEC 依赖**：终端回声消除依赖 `aec-audio-processing`（WebRTC AEC3 Python 绑定）和 `numpy`，
> 已包含在 `[voice]` 可选依赖组中；未安装时半双工静音兜底。架构与静音/救援策略详见
> [docs/architecture/06-语音系统.md](../architecture/06-语音系统.md)。

## 实时双工配置

在 `~/.jarvis/settings.toml` 中配置：

```toml
[realtime_talk]
api_key = "sk-xxx"              # DashScope API Key（实时语音必需）
model = "qwen-audio-3.0-realtime-flash"
voice = "longanqian"
event_log = false               # 事件时间线日志（写 ~/.jarvis/logs/diag.log），默认关
echo_suppress_with_aec = true   # AI 说话时压低麦克风抑制回声（仅戴耳机时才建议关）
half_duplex = true      # 半双工：AI 说话时静音麦克风；戴耳机想随口打断设为 false（桌面全双工路径不受影响）
rescue = true           # 响应救援：吞轮时补发 response.create（默认开）
turn_detection = "server_vad"   # 轮次检测：server_vad（默认）/ smart_turn
silence_ms = 500        # server_vad 判停静音时长（毫秒，200~6000）
tools_mode = "builtin"  # 工具面：builtin（默认，2 工具低延迟）/ all（全量，延迟致死循环，勿用）
```

> `api_key` 用于 `/talk` 实时双工语音鉴权（映射到 `dashscope_api_key`）。不配置时回退到 `DASHSCOPE_API_KEY` 环境变量；当前 LLM 厂商就是 dashscope 时也可直接复用主 `api_key`。**不会借用 deepseek/openai 等其它厂商的 key**（2026-09 起防呆 fail-fast：缺配置时直接给出中文配置指引，不发起注定被 1007 Access denied 拒绝的连接）。

## TTS 朗读 `/say`

```bash
/say 你好，我是贾维斯
```

将文字转为语音朗读。使用 DashScope CosyVoice 引擎。

## 录音识别 `/listen`

```bash
/listen      # 录音并输出识别文本
/mic         # 别名
```
