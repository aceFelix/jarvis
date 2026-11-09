# /voice 无声音自动唤醒 / 对话幻听修复复盘

> 模块：语音模式（`/voice`）待机唤醒 + 对话聆听
> 发现时间：2026-11-09（用户实机：没有出声，JARVIS 自动「🔊 唤醒」；随后对话阶段未出声却连续识别出「贾维斯」）
> 修复时间：2026-11-09
> 作者：aceFelix

---

## 1. 问题现象

第一轮：`/voice` 进入待机后，用户**完全没有说话**，JARVIS 仍会偶发自动唤醒：

```text
💤 待机中，说「贾维斯」唤醒我
🔊 唤醒，回到对话模式
```

第二轮（修复待机后又发现）：用户一直在正常对话，但**最后两次对话压根没出声**，
终端却出现：

```text
🎤 聆听中...（说话即可，停顿后自动结束）
🧑 你说: 贾维斯。
```

LLM 真的把这句幻听当成用户输入并回复“我在，先生”。

---

## 2. 排查过程

### 阶段 1：待机唤醒链路

待机阶段 `_standby_round()` 调 `stt.listen(max_seconds=_STANDBY_MAX_SECONDS)`，
把识别文本交给 `matches(text, wake_words)`。只要文本里**任意位置**包含
「贾维斯」/近音字，就立即唤醒。

### 阶段 2：对话聆听链路

对话阶段 `_voice_loop_round()` 同样调用 `stt.listen`，识别到文本就直接
`events.on_user_transcript(user_text)` 并上交 LLM。也就是说，**幻觉文本既能
触发待机唤醒，也能直接变成一轮用户发言**。

### 阶段 3：三个放大因素

1. **唤醒热词偏置**：`QwenASR` 初始化时把「贾维斯」写进
   `input_audio_transcription.corpus.text`，让 ASR 一开始就倾向于输出该词。
   静音/环境噪音/TTS 尾音时，模型会**凭空输出「贾维斯」**；
2. **匹配过于宽松**：待机唤醒用通用包含匹配，长句里碰巧含「贾维斯」也会唤醒；
3. **缺少 VAD 门禁**：即使服务端没发过 `speech_started/stopped`，只要 ASR 返回
   文本就会被当成用户真的说了话。

---

## 3. 根因

无声音自动唤醒 / 对话幻听 = **ASR 幻觉（热词偏置放大）** + **宽松包含匹配** +
**无 VAD 门禁** 三者叠加。不是唤醒词配置错误，也不是麦克风一直有声音。

---

## 4. 修复方案

### 4.1 整场语音都不再下发唤醒热词

`voice_loop` 创建 STT 时不再传 `hotwords`；对话与待机两次 `stt.listen` 都显式
`hotwords=""`。热词偏置在静音/噪音/TTS 尾音上制造幻觉，比它带来的唤醒准确率
更烦人；近音字鲁棒性交给 `matches_wake` 的拼音层。

### 4.2 待机使用严格唤醒门禁 `matches_wake`

`agent/voice/wake_words.py` 新增：

- 去首尾标点后整句 **≤12 字**；
- 唤醒词必须出现在**前 8 字**内（字面或拼音同音）；
- 拼音层仍只对 ≥3 字词开放（避免「甲维集团」这类短词同音误报）。

普通长句即使包含「贾维斯」也不会唤醒；用户正常喊「贾维斯」「加维思」
「喂贾维斯」「贾维斯在吗」仍能唤醒。

### 4.3 无 VAD 事件的结果直接丢弃（对话 + 待机都生效）

`_QwenASRCallback` 新增 `speech_seen`：收到 `speech_started/stopped` 才置位；
`listen()` 返回 `speech_detected`。

- 待机：`speech_detected=False` 直接返回空文本，不进入唤醒判定；
- 对话：`speech_detected=False` 时把识别文本清空，按“没听清”处理，
  **绝不上交 LLM**（修复“没出声却出现 🧑 你说: 贾维斯”）。

---

## 5. 验证

- 新增 `TestStrictWakeGate`：
  - 13 个近音写法仍全部唤醒；
  - 4 条带上下文的短句仍唤醒；
  - 11 条日常语句零误唤醒；
  - 长句包含「贾维斯」、唤醒词出现在后段 → 不唤醒。
- 新增 `test_dialog_drops_hallucinated_text_without_vad`：
  模拟 `{"text": "贾维斯", "speech_detected": False}`，断言不产生
  `user_transcript`、不调用 QueryLoop、只提示“没听清”。
- `tests/voice/test_wake_words.py`、`test_stt_qwen_session.py`、
  `test_voice_idle_standby.py`：95 passed。
- 全量测试：`4702 passed, 2 skipped`（新增对话幻觉用例后）。
- 待用户复测：待机与对话沉默时都不应再出现“贾维斯”；正常喊仍能唤醒。

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/voice/voice_loop.py` | 创建 STT 不传热词；对话/待机 `listen(hotwords="")`；`matches_wake`；对话+待机 `speech_detected` 门禁 |
| `agent/voice/wake_words.py` | 新增 `matches_wake`（短句 + 前 8 字 + 拼音门禁） |
| `agent/voice/stt/qwen.py` | `listen(hotwords=...)` 覆盖；`speech_seen` / `speech_detected` |
| `tests/voice/test_wake_words.py` | 严格唤醒门禁测试 |
| `tests/voice/test_voice_idle_standby.py` | 对话幻觉 VAD 门禁测试 |
| `docs/guide/voice.md`、`docs/architecture/06-语音系统.md` | 唤醒判定说明 |

---

## 7. 经验总结

- **唤醒热词是双刃剑**：它在静音/噪音上会制造 ASR 幻觉；用户已醒着的对话阶段
  完全不需要它，待机阶段也宁可依赖拼音层，不要用幻觉换准确率。
- **“有没有人声”必须由 VAD 决定**，不能只看 ASR 是否返回文本。
- 幻觉文本不仅会误唤醒，还会直接变成一轮“用户发言”，因此**对话聆听也要做
  VAD 门禁**，不能只守待机。
- 如果未来仍偶发误唤醒，下一层手段是提高 VAD 阈值（当前 0.4，可单独传
  `vad_threshold`），而不是继续放宽/收紧词表。
