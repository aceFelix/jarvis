# 流式 TTS 会话生命周期缺陷：语音会断、尾音被截断、新旧音频混播

> 日期：2026-10
> 触发：「帮我看看 jarvis 的语音系统有啥问题没有，会不会出现有时候文字都出了，
> 但是语音会断，然后继续之后的说话过程中会夹杂上次断的语音继续说」
> 改动：`agent/voice/tts.py`、`agent/voice/stream_tts.py`、`agent/voice/voice_loop.py`
> 测试：`tests/voice/test_tts_stream_lifecycle.py`（新建，20 例）、
> `tests/voice/test_tts_playback_drain.py`（更新口径 + 新增 1 例回归）

---

## 0. 结论先说

用户描述的现象**确实存在**，而且是三个独立缺陷叠加：

| # | 缺陷 | 直接后果 |
|---|---|---|
| ① | 打断后 pyaudio 输出流 / DashScope WS **只置标志、从不释放** | 流泄漏 → 两轮输出流并存 → **新旧语音混播**；连接攒够后建连被拒 → **用久了彻底没声音** |
| ② | 收尾时长用「首块到现在」估算，把 **LLM 停顿**算成已播 | 长停顿后估算归零 → `stop_stream()` **掐断最后一句** |
| ③ | WS 断了之后 `on_data` **静默丢弃**，`streaming_call` 也不抛 | **文字照常上屏、语音没了**，且永不降级、用户无从判断 |

---

## 1. 三个缺陷怎么查出来的

### 1.1 缺陷 ①　打断路径根本没有关流

全仓搜 `stop_stream` / `close`，结果只有两处（都在 `tts.py` 的 `on_close` 里）：

```
tts.py:155-156   self._stream.stop_stream(); self._stream.close()   ← 只在 on_close
```

而打断走的是完全另一条路：

```
voice_loop._on_barge()
  → StreamTTSPlayer.stop()
    → CosyVoiceTTS.stop()
      → _PlaybackCallback.stop()     # 就一句 self._stop_flag.set()
```

没有 `close()`，也没有 `synth.close()`。接着 `finish()` 里：

```python
def _watch_stop():                     # 每 50ms 看一次 stop_flag
    ...
    if cb._stop_flag.is_set(): done.set()
...
self._synthesizer = None               # ← 唯一能关 WS 的引用被清掉了
```

`on_close` 只有 SDK 自己回调才触发，而我们从不主动 close 合成器，WS 又还连着
——**它可能永远不触发**。实测确认：

```
=== A. 打断后输出流是否关闭 ===
  打断前 stream.open = True
  stop() 之后 stream.open = True | stop_stream 调过 = False
  → 流仍然开着 = True （True 即泄漏）
```

下一轮 `on_open()` 在**同一个全局 PyAudio**（`audio.py` 单例）上再 `open()` 一个
输出流，两个流同时向声卡写 → 就是「夹着上次断的语音继续」。

### 1.2 缺陷 ②　`remaining_play_seconds()` 的数学错误

旧实现：

```python
total = self._total_bytes / (_PCM_RATE * _PCM_CHANNELS * _PCM_WIDTH)
rem = total - (time.time() - self._play_start)      # _play_start = 首块音频到达时刻
```

它默认「从首块音频到现在这段时间，声卡一直在播」。可真实情况是：

- LLM 吐字有间隙（深度思考、工具调用动辄数秒）；
- 服务端合成有抖动；
- 这期间**声卡是静音的**，却被算进了「已播」。

于是停顿越长，估算越低。实测：

```
=== B. LLM 吐字有停顿时 remaining_play_seconds 是否失真 ===
  累计音频 = 3.00s
  elapsed(自首块) = 6.05s
  估算 remaining = 0.00s   |  真实还没播的 ≈ 1.00s
```

`on_close` 与 `_wait_for_playback` 都拿这个值决定排空多久 → 估成 0 就直接
`stop_stream()`，**最后一句被掐断**。这就是「语音会断」。

### 1.3 缺陷 ③　音频被丢了，但没人知道

```python
def on_data(self, data: bytes) -> None:
    if self._stop_flag.is_set() or self._stream is None:
        return          # ← 静默
```

`_stream is None` 意味着流已释放（WS 关得比合成早，或收到上一轮的尾巴）。
上层 `StreamTTSPlayer._send_sentence()` 只在 **抛异常** 时才降级：

```python
try:
    self._tts.feed(cleaned)     # WS 已断时往往不抛，音频静默进黑洞
except Exception:
    self._degraded = True
```

文字走 `events.on_ai_text_delta`（与 TTS 无关）照常上屏，语音却没了。
实测：

```
=== C. on_close 之后 on_data 的行为 ===
  close 后 stream = None （None 表示已释放）
  on_close 后再来音频：写入量 0 → 0（相等 = 静默丢弃）
  → 此时错误不会上报，StreamTTSPlayer 也不会降级，只表现为「没声音」
```

而且 `voice_loop` 里 `stream_ok = False` 时也**什么都不说**——用户只知道没声音。

---

## 2. 修法

### 2.1 把「释放流」变成一个幂等的单一出口

```python
def _release_stream(self) -> None:
    """幂等释放 pyaudio 输出流（stop / on_close 都可能先到）。"""
    with self._stream_lock:
        stream = self._stream
        self._stream = None
    if stream is None:
        return
    for method in ("stop_stream", "close"):
        try:
            getattr(stream, method)()
        except Exception:
            pass
```

`stop()` 与 `on_close()` 都改调它。**打断 = 立刻闭嘴**：

```python
def stop(self) -> None:
    self._stop_flag.set()
    self._release_stream()
```

> 只置标志只能拦住**后续**写入，已经进环形缓冲的那段仍会继续出声。
> 把流关掉才是真正的「立即安静」。

### 2.2 合成器也要主动收尾（否则 WS 照样泄漏）

```python
def _close_synthesizer(self, *, cancel: bool = False) -> None:
    synth = self._synthesizer
    self._synthesizer = None
    if synth is None:
        return
    if cancel:
        try: synth.streaming_cancel()      # 告诉服务端本次作废，别再推音频
        except Exception: pass
    try: synth.close()                     # 断 WS
    except Exception: pass
```

- `CosyVoiceTTS.stop()` → 回调 `stop()` + `_close_synthesizer(cancel=True)`
- `finish()` / `speak()` 正常收尾 → `_close_synthesizer()`（不再指望 SDK 回 `on_close`）

一个细节：`finish()` 原写法是 `finally: self._close_synthesizer()` 之后又用
`self._safe_get_delay(self._synthesizer)`——但 close 已经把引用置 None，
`first_package_delay_ms` 会永远变 0。改为 **先取 delay 再 close**。

### 2.3 收尾时长改成「只看最后一次写入之后」

```python
def remaining_play_seconds(self) -> float:
    if self._last_data_time is None:
        return 0.0
    tail = self._last_chunk_bytes / _BYTES_PER_SEC + _OUTPUT_BUFFER_SECONDS
    rem = (self._last_data_time + tail) - time.time()
    return rem if rem > 0 else 0.0
```

物理依据：`stream.write()` 阻塞写环形缓冲，缓冲深度只有 `frames_per_buffer`
帧，所以最后一块写完后最多再播「最后一块时长 + 缓冲深度」。**与之前停了多久
完全无关**。

同时把缓冲深度**显式钉住**，否则 PortAudio 各平台默认值不同，`_OUTPUT_BUFFER_SECONDS`
无从估算：

```python
_PCM_FRAMES_PER_BUFFER = 1024                 # ≈46ms
_OUTPUT_BUFFER_SECONDS = _PCM_FRAMES_PER_BUFFER / _PCM_RATE

stream = pa.open(..., frames_per_buffer=_PCM_FRAMES_PER_BUFFER)
```

### 2.4 丢包要能区分「主动」与「故障」

```python
def on_data(self, data: bytes) -> None:
    if self._stop_flag.is_set():
        return                       # 打断：主动闭嘴，正常，不记 error
    stream = self._stream            # 先取本地引用，防与 stop() 竞态
    if stream is None:
        if self._error is None:
            self._error = "TTS 播放流已关闭，音频被丢弃"
        return
    try:
        stream.write(data)
    except Exception as e:
        self._error = f"TTS 写入扬声器失败: {type(e).__name__}: {e}"
        return
    ...
    self._last_data_time = time.time()
    self._last_chunk_bytes = len(data)
```

上层据此降级：

```python
try:
    self._tts.feed(cleaned)
    reason = self._tts.error          # ← 关键：不抛异常也要查
    if reason:
        raise RuntimeError(reason)
except Exception as e:
    self._degraded = True
    self._fallback_buf += cleaned     # 已发未冲刷的收回，改整段 speak()
    self._fallback_buf += self._buf
    self._buf = ""
    return
```

`voice_loop` 建连失败时也不再沉默：

```python
if not stream_ok:
    events.on_info("⚠ TTS 连接建立失败，本轮只显示文字。检查 DASHSCOPE_API_KEY、"
                   "语音额度与网络；若反复出现，重开语音模式重试", LEVEL_WARN)
```

### 2.5 顺手删掉一个死常量

`stream_tts.py` 里的 `_WS_IDLE_TIMEOUT = 10.0` 注释写着「LLM 两句之间间隔太久
就认为 WS 已被 RST、需要重连」——但它**从未被任何代码引用**。真正需要的能力
已由上面的 `tts.error → 降级` 覆盖，直接删除并在原处留一行说明，免得后人以为
空闲重连已经实现了。

---

## 3. 验证

修完后用同一套探针复验：

```
=== A. 打断后输出流是否关闭 ===
  stop() 之后 stream.open = False | stop_stream 调过 = True
  → ✅ 已释放

=== B. 两轮之间是否还会并存两个输出流 ===
  第 2 轮开流后，第 1 轮的流还开着吗 = False
  → ✅ 不并存

=== C. LLM 长停顿后 remaining 是否仍准确 ===
  估算 remaining = 1.05s | 真实还没播的 ≈ 1.00s
  → ✅ 不再被停顿污染

=== D. 音频被丢是否可感知（WS 断了但没抛异常）===
  error = 'TTS 播放流已关闭，音频被丢弃'
  → ✅ 可被上层感知并降级

=== E. 打断后丢包不该被当成错误 ===
  error = None
  → ✅ 属预期，不报错
```

| 项 | 结果 |
|---|---|
| `pytest tests/voice` | **336 passed**（原 322，+14） |
| 新增 `test_tts_stream_lifecycle.py` | 20 例（流释放/并发流/合成器关闭/静默降级/线程安全） |
| 更新 `test_tts_playback_drain.py` | `_primed_callback` 适配新口径 + 新增 `test_long_gap_does_not_underestimate` |

---

## 4. 教训

1. **「置标志」不等于「释放资源」。** 打断只 `stop_flag.set()` 是个很自然的
   偷懒——回调里检查一下就不写了。但底层流仍然活着，还占着声卡通道；引用被
   清空之后连补救的机会都没了。凡是「后台回调持有的资源」，收尾路径必须
   **显式、幂等** 地释放，且不能依赖第三方库的回调（它可能永远不来）。
2. **用时间差算进度，一定先问「这段时间里真的在推进吗」。** `总时长 - (now - 起点)`
   看着天经地义，可起点是「首块音频到达」，中间完全可能几分钟没有数据。
   算进度要用 **最近一次真实变化** 作锚点，不是最早那次。
3. **静默 `return` 会吃掉整个功能。** `if stream is None: return` 一行代码，
   让「文字有、语音没」变成了查无实据的玄学。失败要么记 error、要么抛、
   要么打日志——总得留下一个能被上层观察到的痕迹。
4. **异常不是失败的唯一形态。** `streaming_call` 在 WS 已断时往往安安静静地
   把数据丢进黑洞。只靠 `try/except` 判断成败，会漏掉一整类「假成功」。
   做完动作要**再查一次状态位**。
5. **别让测试替身固化 bug 的语义。** `test_tts_playback_drain.py` 原来的
   `_primed_callback` 照旧公式构造 `_play_start`/`_total_bytes`，等于把错误
   口径写进了测试。改口径时必须同步改替身，并**补一条专门打脸旧口径的用例**
   （`test_long_gap_does_not_underestimate`），否则后人重构会把它改回去。
6. **死常量是谎报进度。** `_WS_IDLE_TIMEOUT` 有名字、有注释、有数值，看上去
   功能已经做了——实际零引用。留着比删掉危险得多。
