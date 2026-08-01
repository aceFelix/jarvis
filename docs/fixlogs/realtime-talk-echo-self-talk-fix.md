# 实时双工语音 `/talk` 回声自激与说话不全修复记录

## 问题现象

### 阶段一：升级后音频卡顿、跳说、胡言乱语

用户反馈实时聊天中 Jarvis 语音不流畅，声音跳着说，几个字同时蹦出来，胡言乱语，完全听不清。文字输出正确，但语音断断续续，有时说一半直接停止。

> 用户原话："不是语速的问题懂吗？jarvis说话说不全，跳说，乱说，胡言乱语，我都不知道他在说什么？"

### 阶段二：回退后说话只说一半

回退 JitterBuffer 和 Pacing 后，音频流畅了，但出现新问题：Jarvis 说话只说前半句，后半句不说。例如"在呢，先生，有什么吩咐？"只说"在呢，先生"。用户开口说话后，Jarvis 才继续说后半句。

### 阶段三：自言自语

禁用 barge_in 后，Jarvis 不再说一半，但出现新问题：用户没说话，Jarvis 自己跟自己聊起来了。右侧转录文本把 AI 刚说的话又当成用户输入，形成死循环。

---

## 排查过程

### 第一阶段：定位音频卡顿根因（JitterBuffer + Pacing）

**假设**：升级引入的 JitterBuffer 在网络抖动时丢弃旧音频 chunk，Pacing 逻辑拖慢播放节奏，两者叠加导致音频片段丢失。

**验证**：
- 对比 `jarvis-备份`（升级前）与当前代码，确认升级前是 `response.audio.delta` → 直接 `spk.write(audio)`，无中间缓冲。
- 升级后增加了 `JitterBuffer`（抖动缓冲，丢弃 >400ms 的旧 chunk）和 `_audio_start_time`/`_audio_written_bytes` 的 Pacing 节奏控制。

**操作**：移除 JitterBuffer 导入、初始化、put/get 调用；移除 Pacing 时间计算逻辑；`_play_audio` 协程直接将 chunk 写入扬声器。

**结果**：音频流畅性恢复，但出现"说话只说一半"。

---

### 第二阶段：排查"说话只说一半"（服务端 VAD 误打断）

**日志分析**：
```
[09:17:36] _do_barge_in: 触发打断，清空播放队列
[09:17:43] _do_barge_in: 触发打断，清空播放队列
```

`_do_barge_in` 在 AI 说话期间被触发，清空了扬声器缓冲区，导致后半句被丢弃。

**假设**：服务端 `server_vad` 将扬声器回授声音误判为用户说话，触发 `input_audio_buffer.speech_started`，客户端收到后执行 barge_in 清空播放队列。

**尝试 1**：禁用 barge_in（`barge_in_enabled = False`）
```
[09:53:37] input_audio_buffer.speech_started: AI_speaking=False, barge_in 已禁用，忽略打断
```
**结果**：barge_in 是忽略了，但服务端 VAD 仍然把回声识别成用户语音，触发了新一轮 response，导致 Jarvis 自言自语。

**尝试 2**：切换 `server_vad` → `smart_turn`
**假设**：smart_turn 融合语义理解，回声内容是 AI 刚说过的话，语义上不会被判定为有效打断。
**结果**：还是有回声，smart_turn 单靠语义无法完全过滤物理回声。

---

### 第三阶段：AI 说话时静音屏蔽（临时方案）

**操作**：AI 说话时向服务端发送静音数据，物理切断回声来源。
**结果**：自言自语问题解决，但**完全丧失打断能力**——AI 说话期间用户开口无法打断。

> 用户反馈："照你这么说jarvis不插耳机外放时的实时语音聊天无法做到打断功能了？"

---

### 第四阶段：方案比选与 AEC 落地

**方案对比**：

| 方案 | 原理 | 打断 | 防自语 | 复杂度 |
|---|---|---|---|---|
| A. AI 说话时静音屏蔽 | 发静音数据 | ❌ 完全不能 | ✅ 稳定 | 低 |
| B. 客户端能量门控 + smart_turn | RMS 区分回声和真人 | ⚠️ 大声才能 | ⚠️ 回声大时失效 | 中 |
| C. WebRTC AEC3 回声消除 | 自适应滤波减去回声分量 | ✅ 完美 | ✅ 工业级 | 高 |

**方案 B 被否决**：用户指出"如果我扬声器开的声音很大，你AI说话的声音麦克风照样听得到啊"——能量门控无法区分大音量回声与真实语音。

**方案 C 选型**：
- `webrtc-audio-processing`：PyPI 编译失败，Windows 无预编译 wheel。
- `speexdsp`：同样编译失败。
- `aec-audio-processing`：WebRTC AEC3 的 Python 绑定，**Windows 有预编译 wheel**，`pip install` 即可。

**API 探索**：
```python
from aec_audio_processing import AudioProcessor
apm = AudioProcessor(enable_aec=True, enable_ns=True, enable_agc=False, enable_vad=False)
apm.set_stream_format(sample_rate_in=16000, channel_count_in=1)      # 近端（麦克风）
apm.set_reverse_stream_format(sample_rate_in=16000, channel_count_in=1)  # 远端（参考）
# 每帧 160 samples = 320 bytes = 10ms @ 16kHz
apm.process_reverse_stream(reference_frame)  # 喂扬声器参考
clean = apm.process_stream(mic_frame)        # 处理麦克风，返回消回声音频
```

**关键问题**：扬声器输出是 24kHz，麦克风是 16kHz，WebRTC APM 要求两端采样率一致且按 10ms 帧对齐。
**解决**：用 numpy 线性插值将扬声器 24kHz 音频重采样到 16kHz，统一帧大小。

---

## 根因分析

### 根本原因：全双工语音的物理回声闭环

```
扬声器播放 AI 语音 → 声音经空气传播到麦克风 → 麦克风采集到回声
→ 服务端 ASR 将回声识别为"用户说话" → 触发新一轮 response
→ AI 回应"自己的回声" → 形成自言自语死循环
```

### 为什么升级前没问题

升级前的实现是官方快速开始的最简版本，`barge_in` 默认行为下扬声器回声确实会被服务端 VAD 识别，但升级前可能因为：
- 扬声器音量较小 / 麦克风灵敏度较低 / 物理距离较远
- 当时没有深入测试外放场景

升级后显式暴露了这个问题，而非升级引入的 bug。

### 为什么 JitterBuffer/Pacing 导致跳说

- JitterBuffer 在网络抖动时丢弃"过期"的音频 chunk（>400ms），直接丢失音频数据
- Pacing 逻辑按时间戳控制写入节奏，网络延迟时播放进度落后于数据到达，导致缓冲区积压后被丢弃
- 两者叠加：音频片段大面积丢失 → 跳说、胡言乱语

### 为什么"说话只说一半"

服务端 VAD 误触发 `speech_started` → 客户端执行 `_do_barge_in` → `spk.stop_stream()` + `start_stream()` 清空了扬声器缓冲区中尚未播放的后半句音频。

---

## 修复方案

### 1. 回退 JitterBuffer + Pacing（解决跳说）

恢复官方示例的直接播放模式：

```python
# response.audio.delta 处理
audio = base64.b64decode(delta)
await asyncio.to_thread(self._spk.write, audio)  # 直接写入，无中间缓冲
```

### 2. 切换 smart_turn 轮次检测（辅助防回声）

```python
"turn_detection": {
    "type": "smart_turn",  # 融合语义理解，回声内容不会被判定为有效打断
}
```

### 3. WebRTC AEC3 回声消除（核心修复）

新增 `agent/voice/aec.py`，封装 WebRTC AudioProcessor：

```python
class EchoCanceller:
    def __init__(self):
        self._apm = AudioProcessor(enable_aec=True, enable_ns=True, ...)
        self._apm.set_stream_format(sample_rate_in=16000, channel_count_in=1)
        self._apm.set_reverse_stream_format(sample_rate_in=16000, channel_count_in=1)

    def feed_reference(self, playback_audio: bytes):
        """扬声器音频（24kHz）→ 重采样到 16kHz → 喂给 APM 作为参考"""
        ref_16k = _resample_24k_to_16k(playback_audio)
        # 按 10ms 帧送入 APM
        while len(self._ref_buf) >= _FRAME_BYTES:
            self._apm.process_reverse_stream(frame)

    def process_mic(self, mic_data: bytes) -> bytes:
        """麦克风音频 → APM 消回声 → 返回干净音频"""
        # 按 10ms 帧送入 APM
        return self._apm.process_stream(frame)
```

集成到 `realtime_talk.py`：

```python
# _recv_events：收到 AI 音频时，先喂参考信号再播放
audio = base64.b64decode(delta)
if self._aec is not None:
    self._aec.feed_reference(audio)  # 喂参考
await asyncio.to_thread(self._spk.write, audio)  # 播放

# _send_audio：读麦克风后，过 AEC 再发送
data = self._mic.read(CHUNK_BYTES, False)
if self._aec is not None:
    data = self._aec.process_mic(data)  # 消回声
await ws.send(...)  # 发送干净音频
```

### 4. 优雅降级

AEC 为可选依赖，未安装时自动降级为仅 smart_turn 模式，保证基本可用：

```python
try:
    from .aec import EchoCanceller, is_available as _aec_available
    _HAS_AEC = _aec_available()
except ImportError:
    _HAS_AEC = False
```

---

## 验证结果

### 人工测试

1. **防自言自语**：AI 说话时用户保持安静 → Jarvis 说完整句话后正常待命，不再自言自语 ✅
2. **打断能力**：AI 说话时用户开口 → 成功打断 AI 并响应 ✅
3. **音频流畅性**：不再出现跳说、胡言乱语 ✅
4. **说话完整性**：Jarvis 能完整说完句子，不再只说一半 ✅

### 自动化验证

- 语法编译通过：`py_compile` 验证 `aec.py` 和 `realtime_talk.py`
- AEC 单元行为验证：`AudioProcessor` 帧大小、`process_stream`/`process_reverse_stream` 返回值类型和长度正确

---

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/voice/aec.py` | 新增：WebRTC AEC3 回声消除封装，含 24k→16k 重采样、10ms 帧对齐 |
| `agent/voice/realtime_talk.py` | 集成 AEC：recv 喂参考信号、send 过 AEC；回退 JitterBuffer/Pacing；切换 smart_turn |
| `pyproject.toml` | voice 依赖组新增 `aec-audio-processing` 和 `numpy` |
| `README.md` | 更新实时双工 `/talk` 说明，补充 AEC 回声消除与 smart_turn 特性描述 |

---

## 经验总结

### 1. 全双工语音必须处理回声

外放场景下，扬声器→麦克风的物理回声是全双工语音的固有难题。不能指望服务端 VAD/ASR 自己区分回声与真实语音——必须在客户端发送前就消除回声。官方快速开始省略 AEC 是因为示例假设戴耳机。

### 2. 能量门控治标不治本

回声能量随扬声器音量变化，无法用固定阈值可靠区分回声与真实语音。只有自适应滤波（AEC）才能在各种音量下工作。

### 3. WebRTC AEC3 是工业级方案

Chrome 浏览器同款引擎，效果远优于手写 NLMS。Python 有预编译 wheel（`aec-audio-processing`），Windows 直接 `pip install` 即可，无需编译环境。

### 4. 采样率不匹配需重采样对齐

WebRTC APM 要求近端和远端采样率一致、按 10ms 帧对齐。麦克风 16kHz 与扬声器 24kHz 不匹配时，必须将扬声器音频重采样到 16kHz 再喂给 APM，否则 AEC 失效。

### 5. 升级要敬畏官方实现

官方示例的最简版本看似"不够好"，但经过了工程验证。自行增加 JitterBuffer/Pacing 等优化时，必须充分测试外放场景，否则可能引入比原问题更严重的 bug。

### 6. 可选依赖优雅降级

AEC 作为可选能力，未安装时自动降级，保证基本可用。启动时通过 UI 提示用户是否已启用 AEC，便于排查。

### 固化为规则

- 全双工语音模块**必须**在发送麦克风数据前经过 AEC 处理
- AEC 参考信号**必须**在播放扬声器音频前喂入
- 近端与远端采样率**必须**一致，不一致时在 AEC 层做重采样
- 新增音频处理优化前，**必须**对比官方实现并充分测试外放场景
