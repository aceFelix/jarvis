# 语音厂商协议矩阵（解耦调研 · S1 交付物）

> 作者：aceFelix　日期：2026-11-02　状态：**调研完成，待拍板首批接入名单**
>
> 目的：判定各家实时语音 API 与 OpenAI Realtime 事件体系的**同构度**，据此决定「哪些厂商能靠一张
> 注册表接入、哪些必须写事件映射层」。结论直接约束 `agent/voice/` 抽象层的形状。

## 一、调研方法与信息源

按「一手来源优先」取官方文档；抓取日期均为 2026-11-02。**核实状态逐条标注**，不混用。

| 厂商 | 信息源 | 核实状态 |
|---|---|---|
| DashScope（现状） | 本地 `dashscope-docs/` 客户端事件 + 服务端事件全文 | ✅ 完整 |
| StepFun 阶跃星辰 | `platform.stepfun.com/docs/zh/api-reference/realtime/chat` 全文 1096 行 | ✅ 完整（FC 事件名除外） |
| Azure Voice Live | `learn.microsoft.com/.../voice-live-api-reference-2026-06-01-preview` 全文 3810 行 | ✅ 完整 |
| Azure GPT-Live | `learn.microsoft.com/.../gpt-live-reference` | 🟡 部分（事件名已取，全表未展开） |
| 百度千帆 audio-realtime | `cloud.baidu.com/doc/qianfan-api/s/Dmtijam2s` | 🟡 摘要级（session 对象结构已见全貌） |
| MiniMax Realtime | `platform.minimaxi.com/document/Realtime` | 🟡 摘要级（**事件全集未取到**） |
| 豆包 / 火山引擎 | `volcengine.com/docs/DoubaoVoice/End-to-end-real-time...` | 🟡 摘要级（鉴权与生命周期事件已确认） |
| 智谱 GLM-Voice | GitHub `MetaGLM/glm-realtime-sdk` README（官方 SDK，非协议规范页） | 🔴 未核实（`docs.bigmodel.cn` 猜测 URL 返回 404） |
| **OpenAI 本身** | `platform.openai.com/docs/api-reference/realtime-*` | 🔴 **未取到，直连 403** |

> OpenAI 是同构度的**定义源**，却因网络原因只拿到二手印证（51CTO / CSDN 描述的
> `conversation.item.create` + `item.type = message|function_call|function_call_output` 事件体系）。
> 下表所有「同构度」是相对 **DashScope 已核实的 OpenAI 同族事件集** 比对的——因为 DashScope 本身就是
> 照 OpenAI 实现的，可作可用代理基线。真正接 OpenAI 前必须回头核实官方规范。

## 二、基线：jarvis 实际依赖的事件（从代码提取，非文档抄录）

扫描 `realtime_recv.py` / `realtime_events.py` / `realtime_engine.py` / `stt/qwen.py` 得到的
**21 个**事件（17 个处理 + 若干发送）：

```
conversation.item.ambient_audio_transcription.delta      ← DashScope smart_turn 专属
conversation.item.ambient_audio_transcription.completed  ← DashScope smart_turn 专属
conversation.item.create
conversation.item.input_audio_transcription.completed
conversation.item.input_audio_transcription.text         ← DashScope/QwenASR 增量事件名
input_audio_buffer.append
input_audio_buffer.speech_started
input_audio_buffer.speech_stopped
response.audio.delta
response.audio_transcript.delta
response.audio_transcript.done
response.cancel
response.create
response.created
response.done
response.function_call_arguments.done
response.output_item.added
session.created
session.finished                                         ← DashScope 系特有
session.update
session.updated
```

其中 **4 个带厂商色彩**（`ambient_audio_transcription.*`、`input_audio_transcription.text`、
`session.finished`）。也就是说：状态机对协议的真实要求只有 17 个通用事件。

## 三、厂商对照矩阵

| 厂商 | 端点与鉴权 | `turn_detection` | 输入/输出音频 | 工具调用 | 私有扩展 | 同构度 |
|---|---|---|---|---|---|:---:|
| **DashScope** Qwen-Audio-Realtime | `?model=` + `Bearer` | `server_vad` / **`smart_turn`** / `null` | pcm 16k / pcm 24k | `tools(type=function)` + `function_call_output` 回写 | 声纹锁定、联网搜索、情绪增强、`max_history_turns`、语种 | 基准 |
| **百度千帆** audio-realtime | `wss://aip.baidubce.com/ws/2.0/speech/v1/realtime?model=` | `server_vad`（含 `create_response` / `interrupt_response`） | `pcm16` 16k 单声道 | `tools` + `tool_choice` | `temperature` / `speed` / `max_response_output_tokens` | **A**（连 `conversation.created` 都逐字一致） |
| **Azure Voice Live**（preview） | `wss://<res>.services.ai.azure.com/voice-live/realtime` | `server_vad` + **`smart_end_of_turn_detection`**（`threshold_level: low/medium/high/default`） | PCM16 多采样率、G.711、`input_audio_sampling_rate:24000` | `function_call_arguments.delta/done` ✅ | 头像/animation/viseme、降噪与**回声取消内置**、OpenAI+Azure 双语音系 | **A** |
| **StepFun** stepaudio-realtime | `wss://api.stepfun.com/v1/realtime?model=` + `Bearer` | **仅 `server_vad`**；`threshold` 量纲是 **0–5000 能量值**（默认 2500），非 -1~1 | `pcm16` / `pcm16` | `tools` 可为 `function` **或 `retrieval`**（知识库） | `response.thinking.delta/done`（边想边说）、`response.cancelled`；音色有官方清单 + 查询 API | **B** |
| **MiniMax** realtime | `wss://api.minimax.chat/ws/v1/realtime?model=abab6.5s-chat` + `Bearer` | 🟡 未取到 | PCM 16bit **24kHz** 单声道 base64 | 🟡 未取到 | T2A 另走 `wss://api.minimaxi.com/ws/v1/t2a_v2/<voice>` | 🟡 待核实 |
| **智谱** GLM-Realtime | `wss://open.bigmodel.cn/api/paas/v4/realtime?Authorization=<key>`（**鉴权在查询参数**） | `inputMode: manual/localVAD/serverVAD` | `wav`/`pcm`（`pcm16`=16k）、TTS 可 `mp3` | `tools`、`tts_source`、`chatMode` 含视频 | **`sessionConfig` 须每次提交完整配置**（与稀疏更新相反） | **C** |
| **豆包** 端到端实时语音 | `wss://openspeech.bytedance.com/api/v3/realtime/dialogue` + **四个自定义头**（`X-Api-App-ID`/`Access-Key`/`Resource-Id`/`App-Key`） | 自有体系 | 自有体系 | 自有体系 | `StartSession`/`FinishSession`/`FinishConnection` 命名 | **C** |
| **Azure GPT-Live** | 同 Azure 资源域 | 自有 | 24kHz PCM16 base64 | `delegation.responses.tools` | `session.start`/`session.input_audio.append`/`session.output_audio.delta`/`response.item.create` 全自有命名 | **C** |

分级口径：**A** = 事件名与 DashScope/OpenAI 同族，差异只在 URL、鉴权头、参数取值；**B** = 同族但能力是子集
且参数语义有量纲差异，需能力门；**C** = 需要事件映射层。

## 四、按门槛判定

计划的门槛是「同构度 ≥ 90% 才进阶段一」。据此：

- ✅ **进阶段一候选**：百度千帆、Azure Voice Live、StepFun
- ⏸ **留条目不写代码**（只在 meta 预留字段）：豆包、Azure GPT-Live、智谱
- ⚠️ **OpenAI 不能算首批**：作为基线它的同构度自然是 100%，但**官方规范本次未能核实**，
  且国内网络直连 403，实机验证成本高。建议 meta 里留 `openai` 条目 + `verified=false`，
  等能核实官方文档时再启用。

### 建议首批：**StepFun 一家**

理由与取舍：

1. **协议文档最完整可得**——1096 行全文已拿到，客户端事件逐条可读，是三家里唯一「拿到全量一手规范」的；
   百度只有摘要级、Azure 需开资源且是 preview。
2. **鉴权形态与现状完全一致**（`?model=` + `Authorization: Bearer`），jarvis 的建连代码几乎不用改形状。
3. **能力有增量价值**：`tools.type=retrieval`（知识库检索）是 DashScope 这条线没有的；
   `response.thinking.*` 正好对得上 jarvis 已有的 reasoning 通道处理。
4. **代价明确且小**：只有两处真实差异——`threshold` 量纲（0–5000 vs -1~1）和没有 `smart_turn`。
   两者都由 meta 的能力门 + 量纲声明解决，不需要映射层。
5. **风险**：StepFun 文档的 Server Event 章节**未出现** `response.function_call_arguments.done`
   （而 jarvis 依赖它）。接入前必须实机确认其 FC 事件名，否则工具链路会静默失效。列为 S3 前置核实项。

## 五、本地零厂商路线：本轮**不建议**

计划里把「`faster-whisper` 做本地 STT」列为候选（好处是能改善跨平台矩阵中“三平台均需
DashScope Key”那一行，且 CI 可无 key 测试）。核实后判断为**不适合阶段一**：

| 项 | 实测/资料结论 | 对 jarvis 的影响 |
|---|---|---|
| 定位 | 它是**离线批处理库，不是实时流式引擎** | 对 `stt.listen()`（录完一段再出文本）语义**其实契合**，不构成障碍 |
| 断句 | 内置 Silero VAD，但默认只剔除 >2s 静音 | 需**本地做轮次切分**：等于把已下线的 Paraformer 客户端 RMS 断句逻辑复活（`_rms()` 与 `_SILENCE_SECONDS` 仍在 `stt/common.py`） |
| 速度 | i7-12700K CPU、small 模型 int8：13 分钟音频 1m42s（约 6.5× 实时率）、内存 1477MB | 3 秒语音约 0.5s 出结果，可用；但**常驻 1.5GB 内存**对桌面管家是真实代价 |
| 依赖 | `ctranslate2`（C++ 二进制 wheel）+ PyAV 内置解码，无需系统 FFmpeg，Python 3.9+ | 跨平台 OK，但明显加重包体与安装失败面 |
| 覆盖面 | **只能替 STT** | `/talk` 全双工语音对话无本地等价物；`/voice` 的**播报（TTS）**本阶段不动 |

核心矛盾：阶段一的目标是「把全双工 `realtime_*` 与半双工 STT 从单一厂商解开」，而本地路线只能
撑起其中一半（听），说与全双工仍得靠厂商。拿一个只能解决 50% 的方案去承担“零厂商”定位，
会把抽象层引向“兼容一个本地实现”的额外设计约束，收益不匹配。

> 建议归入**阶段二（TTS 解耦）**一并评估：真正的零厂商需要 STT + TTS 两侧都有本地方案
> （如 faster-whisper + Piper/Kokoro），那时才能完整替掉一个分支。
>
> 本节的性能与内存数据来自二手技术博客对官方基准的转述，**未逐条回到 README 核实**；
> 模型文件体积本次未取到可靠数字，不在此声称。

## 六、四个改变抽象层设计的发现

这些比「接哪家」更重要，直接影响 S2 的 meta 字段设计：

1. **STT 不需要独立抽象层。** `qwen3-asr-flash-realtime` 本质是 realtime 会话的
   「仅文本输出 + 启用 `input_audio_transcription`」模式（官方 SDK 示例即
   `output_modalities=['text']` + `enable_input_audio_transcription=True`）。所以 `/voice` 的识别与
   `/talk` 的对话**共用同一份 provider meta**，只差 `model` 与 `modalities`。计划里
   `stt_ws_url` / `realtime_ws_url` 双字段可以合并。
2. **meta 粒度必须是 `(vendor, model 系列)` 而非 `vendor`。** 同为 DashScope：
   Qwen-Audio 用 `smart_turn` + 扁平 `input_audio_format`，而 Qwen-Omni 用 `semantic_vad` +
   嵌套 `audio.input.format.samplerate`。按厂商粒度建表会立刻装不下同厂商的两种形态。
3. **`session.update` 的更新语义本身是厂商差异**（稀疏 vs 完整提交）。jarvis 现在最硬的三条经验之一
   是「配置必须在 IDLE 期一次性完成」；对要求完整提交的厂商（智谱），任何中途更新都得重放全量配置。
   meta 需要一个 `session_update_semantic: sparse|full` 字段，而不是假设大家都稀疏。
4. **TTS 走的是另一套协议，本 meta 覆盖不到。** DashScope 的 CosyVoice/Paraformer 属
   **Inference 协议**（`run-task` / `task-started` / `continue-task` / `finish-task`），
   与 realtime 事件体系无关——这正是 `tts.py` 必须绑 SDK 的原因。阶段二解耦 TTS 时
   不能复用本表，需要独立的 inference 协议抽象。

## 七、S3 前置核实清单（接入前必须逐条实机确认）

| # | 待核实 | 为什么必须核实 |
|---|---|---|
| 1 | StepFun FC 结果事件名（是否为 `response.function_call_arguments.done`） | jarvis 依赖该事件驱动工具回写，名字不同则工具链静默失效 |
| 2 | StepFun `threshold` 的实际量纲边界与 0–5000 的换算关系 | 直接照搬 0.5 会等于把阈值设成近乎 0，环境噪声全触发 |
| 3 | 无 `smart_turn` 时 `server_vad` 的打断表现（jarvis 的 barge-in 依赖 `speech_started`） | 影响 AEC 之外的第二道打断保险 |
| 4 | StepFun 纯识别模型（供 `/voice` 半双工复用同一 meta） | 若无，`/voice` 侧仍只能用对话模型取 `input_audio_transcription.completed` |
| 5 | 各家音色清单与「音色是否可中途更新」 | DashScope 与 StepFun 均限定首次 `session.update` 设置，`/tts-voice` 热切换语义要一致 |
| 6 | OpenAI 官方事件全集与 `turn_detection` 取值 | 基线本身尚未一手核实，`openai` 条目在核实前不得置 `verified=true` |

## 八、下一步

1. 你拍板首批名单（建议 StepFun；若要更省可选百度，要更广可 StepFun + Azure）；
2. 之后按计划顺序：先落 S4 回放测试（帧序护栏），再动 S2 抽象层，最后 S3 接第二家；
3. 本文档的第四节结论段在拍板后更新为最终决定；
4. 已完成（2026-11-03）：`docs/architecture/06-语音系统.md` 已加指针指向本文档，立项计划文档见
   [plans/voice-provider-decoupling.md](../plans/voice-provider-decoupling.md)。
