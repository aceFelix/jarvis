# STT 死等一个从不返回的 session.updated，/voice 每轮都在进录音前失败

**日期**：2026-10-28
**影响**：`/voice` 半双工语音对话完全不可用（麦克风一句话都没被识别过）
**作者**：aceFelix

---

## 一、现象

用户戴耳机与否都复现：进入 `/voice` 后每轮都打印

```
🎤 聆听中...（说话即可，停顿后自动结束）
❌ 识别失败: 配置会话超时，再说一次
```

四次连续失败后，第五次停在「聆听中」不再报错（实为 Ctrl+C 打断在 10 秒等待窗口内）。再进一次 `/voice` 依旧。

关键误导：文案「再说一次」把链路级故障描述成了偶发识别失败，用户会反复重试而不会去查配置。

## 二、排查过程

不靠猜，用探针直连 DashScope（脚本放 `.git/` 下、用完即删）逐层剥离。

### 2.1 连接与模型名都是好的

`session.created` 正常到达，且服务端自带完整默认配置：

```json
{"model":"qwen3-asr-flash-realtime","modalities":["text"],"input_audio_format":"pcm",
 "sample_rate":16000,
 "input_audio_transcription":{"model":"qwen3-asr-flash-realtime"},
 "turn_detection":{"type":"server_vad","threshold":0.2,"silence_duration_ms":800}}
```

说明：模型名有效、WebSocket 握手成功、会话层是通的，**且不发 `session.update` 也已具备识别所需配置**。

### 2.2 六组对照：`session.updated` 一次都没来

| 变体 | 载荷 | 收到 `session.updated` |
|---|---|---|
| E1 | 不发 update | 否 |
| E2 | 只 `turn_detection` 单字段 | 否 |
| E3 | +`modalities`/`input_audio_format`/`output_audio_format`/`sample_rate` | 否 |
| E4 | E3 + `voice: null` | 否 |
| E5 | + `input_audio_transcription.model` | 否 |
| E6 | SDK 原生 `update_session()`（复现现场） | 否 |

六组事件序列全部只有 `['session.created']`。**排除了参数错误的可能。**

再查 dashscope SDK（1.25.23）的 `_on_message`：它只分派
`session.created` / `session.finished` / `error` / `response.created` /
`response.audio.delta` / `response.audio_transcript.delta` / `response.done`，
**根本没有 `session.updated` 分支**——服务端与 SDK 两侧都不认这个事件名。

### 2.3 更要紧的：服务端 VAD 根本没跑

送 3.9 秒音频（含两段 1.2 秒满幅正弦，足以触发任何能量阈值）后，事件序列仍是 `['session.created']`：
没有 `input_audio_buffer.speech_started`、没有转录、**连 `error` 都没有**。

### 2.4 同 key 调 TTS 立刻暴露账户侧问题

```
error_code: "Arrearage"
error_message: "Access denied, please make sure your account is in good standing."
```

同一 key 调 `qwen-turbo` 返回的是 `429 Throttling.RateQuota`（限流），不是 Arrearage，
说明 key 有效、账户活跃，**是语音类模型（CosyVoice 合成 + Qwen3-ASR 实时识别）没有可用额度或未开通**。

## 三、根因

两个层次，必须分开说清楚——代码缺陷把服务端问题伪装成了「配置超时」。

**服务端侧（环境，代码改不动）**：该 DashScope 账户对语音类模型无可用额度/未开通。
表现为「接受连接 → 回默认配置 → 后续一律不响应且不报错」，比报错更难查。

**代码侧（本次修复）**：

1. `agent/voice/stt/qwen.py` 的 `listen()` 与 `transcribe_file()` **死等 `session.updated` 10 秒**，
   而这个事件从不到来 → 每一轮都在进入录音循环之前 `return`，麦克风音频从未被送出。
2. 超时后回执为「配置会话超时」，与「偶发识别失败」不可区分 → 用户被文案引导着反复重试。
3. 收尾处 `wait_finished(timeout=10.0)` 在链路已死时又白等 10 秒，失败耗时翻倍
   （`/voice` 下 `max_seconds` 是 300 秒，用户体感就是「卡死」）。

## 四、修法

### 4.1 配置确认降级为可选信号

```python
# 只等一个短窗口；不以超时判失败，只有服务端明确下发 error 才算配置失败
callback.wait_session_updated(timeout=_SESSION_CONFIRM_TIMEOUT_S)  # 2.0s
if callback.error:
    ...
```

依据：`session.created` 已携带完整默认配置，不发 update 也能识别；`session.updated` 本就不是这个模型的契约。

### 4.2 新增「服务端静默」判据

`_QwenASRCallback.event_count` 统计收到的服务端事件总数（非 dict 不计）。录音前后对比：

- 计数增长 → 链路在工作（用户说了话必然先有 VAD 事件），正常走
- 录音期间 `_SERVER_SILENCE_TIMEOUT_S`（10s）内计数不动 → `server_silent = True`，提前收尾

**判据是「零事件」而非「没识别出文字」**：用户说了话必然有 `speech_started`，
所以正常路径不会被这个窗口误伤；这正好把「没人说话」和「服务端没在工作」分开。

### 4.3 回执如实

```python
elif server_silent and not callback.final_text:
    result["error"] = (
        "服务端无响应：录音期间未收到任何识别事件，"
        "请检查 DashScope 该模型的额度与开通状态（cosyvoice / qwen3-asr 分属单独计费）"
    )
```

`callback.error`（服务端明确下发的错误）优先级更高，不被这条覆盖。

### 4.4 链路已静默时不再等 session.finished

```python
if not server_silent:
    callback.wait_finished(timeout=10.0)
```

## 五、验证

- 新增 `tests/voice/test_stt_qwen_session.py`（6 例，1.5 秒跑完）：
  缺 `session.updated` 仍能返回文本 / 零事件报「服务端无响应」/ 零事件提前收尾 /
  `error` 事件优先于「无响应」/ `transcribe_file` 不再要求确认 / 事件计数不被非 dict 污染。
- 全量 `pytest tests -q`：**3647 passed**（原 3641 + 新增 6），无回归。

## 六、遗留

- **账户额度需用户自行处理**：本修复只保证失败可见、不干等，不解决 DashScope 侧额度。
  额度恢复后若仍无响应，应重跑探针比对事件序列。
- `transcribe_file()` 的 `wait_finished(timeout=20.0)` 未加静默判据（非 `/voice` 主路径），
  若后续用于批量识别可一并收紧。
- 本次未覆盖 `/talk`（实时双工）链路：它走另一套引擎与鉴权，需单独验证。

## 七、教训

**「等一个确认事件」这种写法，必须同时问一句：这个事件不来时，我是该失败还是该继续？**
`session.updated` 被当成硬前置，结果一个模型不发它就等于整个功能不可用。
另外，**失败回执的措辞就是排障路径**——「再说一次」把用户推向了错误方向，
改成「服务端无响应 + 查额度」后，同样的故障一秒就能定位。
