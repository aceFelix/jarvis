# P1-3 实时语音稳定性 — 升级方案

## 目标

提升 `/talk` 实时双工语音在网络抖动、弱网环境下的稳定性，优化打断检测和静音检测体验，让实时语音对话达到生产可用水平。

## 当前痛点

| 问题 | 根因 | 影响 |
|---|---|---|
| WebSocket 断开即结束 | `run()` 中 `websockets.connect` 无重连逻辑 | 网络瞬断 → 会话终止 |
| 网络抖动导致音频卡顿 | `response.audio.delta` 直接写入扬声器，无缓冲 | AI 语音断断续续 |
| 打断有爆音 | `spk.stop_stream() + start_stream()` 硬切 | 用户体验差 |
| 死连接无法检测 | 无 ping/pong 心跳 | 网络静默断开 → 会话假死 |
| 完全依赖服务端 VAD | 无客户端 VAD 兜底 | 服务端 VAD 漏检时无法补位 |

## 方案设计

### 1. 自动重连（指数退避）

```
连接断开
  │
  ├─ 非致命错误（401/403）→ 直接退出，提示用户
  │
  └─ 网络/超时错误
       │
       ├─ 重连次数 < max_retries（默认 5）
       │    ├─ 等待 backoff = min(base * 2^n, cap) 秒
       │    ├─ 通知 UI：正在重连（第 n 次）
       │    └─ 重新 connect + session.update
       │
       └─ 超过重连次数 → 退出，提示用户
```

**参数**：
- `reconnect_max_retries`: 5
- `reconnect_base_delay`: 1.0s
- `reconnect_max_delay`: 30.0s

### 2. 抖动缓冲（Jitter Buffer）

在 AI 语音播放前加入一个有界缓冲队列：

```python
class JitterBuffer:
    """有界 FIFO 缓冲，平滑网络抖动导致的音频到达不均匀。

    - 目标延迟：120ms（~3 个 40ms chunk）
    - 最大延迟：400ms（超过则丢弃旧数据，防止延迟累积）
    - 最小延迟：40ms（不足则等待，防止underrun）
    """
```

**策略**：
- 收到 `response.audio.delta` → 放入 jitter buffer
- 独立播放协程从 buffer 读取 → 写入扬声器
- buffer 超过 max_delay → 丢弃最旧 chunk（保持低延迟）
- buffer 不足 min_delay → 等待（避免 underrun 爆音）

### 3. 打断优化（淡出 + 快速清空）

替换 `stop_stream/start_stream` 硬切为：

1. **快速淡出**：对 speaker buffer 中剩余的音频做 20ms 线性衰减到 0，消除爆音
2. **清空 jitter buffer**：丢弃所有待播放的 AI 音频
3. **不打断 stop_stream**：保持流打开，避免重新打开设备的延迟

```python
def _fade_out_and_clear(self):
    """20ms 线性淡出 + 清空缓冲区，消除打断爆音。"""
    # 1. 取出 jitter buffer 中的剩余音频
    # 2. 应用线性淡出 envelope
    # 3. 写入扬声器（让正在播放的音频平滑结束）
    # 4. 清空 jitter buffer
```

### 4. 心跳保活

利用 websockets 库内置的 ping/pong：

```python
await websockets.connect(
    url,
    additional_headers=headers,
    ping_interval=20,      # 每 20s 发 ping
    ping_timeout=10,       # 10s 内无 pong 则判定断开
)
```

### 5. 客户端 VAD 兜底

轻量级 RMS 能量检测，作为服务端 VAD 的补充：

```python
class ClientVAD:
    """客户端 RMS 能量 VAD，作为服务端 VAD 的兜底。

    - 当服务端 VAD 未触发 speech_started 但客户端检测到持续能量
    - 持续 N 帧能量超阈值 → 疑似用户说话 → 主动清空 AI 播放
    """
```

**参数**：
- `client_vad`: bool = True（是否启用）
- `client_vad_threshold`: 0.02（RMS 阈值，比服务端低，作为兜底）
- `client_vad_frames`: 3（连续 N 帧超阈值才触发）

### 6. 配置项

新增 `[realtime_talk]` 配置：

```toml
[realtime_talk]
# ... 已有配置 ...
reconnect_max_retries = 5        # 最大重连次数
reconnect_base_delay = 1.0       # 重连基础延迟（秒）
reconnect_max_delay = 30.0       # 重连最大延迟（秒）
jitter_buffer_ms = 120           # 抖动缓冲目标延迟（毫秒）
jitter_max_ms = 400              # 抖动缓冲最大延迟（毫秒）
client_vad = true                # 启用客户端 VAD 兜底
client_vad_threshold = 0.02      # 客户端 VAD RMS 阈值
client_vad_frames = 3            # 客户端 VAD 连续触发帧数
fade_out_ms = 20                 # 打断时淡出毫秒数
ping_interval = 20               # 心跳间隔（秒）
ping_timeout = 10                # 心跳超时（秒）
```

## 实现文件

| 文件 | 改动 |
|---|---|
| `agent/voice/realtime_talk.py` | 重构：自动重连、抖动缓冲、打断优化、心跳保活、客户端 VAD |
| `agent/voice/jitter_buffer.py` | 新增：JitterBuffer 实现 |
| `agent/voice/client_vad.py` | 新增：客户端 VAD 实现 |
| `agent/config/settings.py` | 新增 realtime 重连/抖动/VAD 相关配置字段 |
| `configs/settings.example.toml` | 新增配置示例 |
| `tests/voice/test_realtime_stability.py` | 新增：单元测试 |

## 验收标准

- [ ] WebSocket 意外断开后自动重连，重连后可正常对话
- [ ] 网络抖动时 AI 语音不卡顿（jitter buffer 生效）
- [ ] 打断时无爆音（淡出平滑）
- [ ] 死连接在 ping_timeout 内被检测并触发重连
- [ ] 服务端 VAD 漏检时，客户端 VAD 可补位打断 AI 播放
- [ ] 鉴权失败不重连，正确提示用户

## 风险与降级

| 风险 | 降级策略 |
|---|---|
| Jitter buffer 增加延迟 | 目标 120ms，用户感知不明显；可配置关闭 |
| 客户端 VAD 误触发 | 阈值设低 + 连续帧数要求，仅作为兜底 |
| 重连期间丢失对话上下文 | 重连后重新发送 session.update，保持 instructions |
| 淡出音频仍有残留 | 淡出后立即清空 buffer，最坏 20ms 残留可接受 |

## 涉及文件

- `jarvis/agent/voice/realtime_talk.py`（重构）
- `jarvis/agent/voice/jitter_buffer.py`（新增）
- `jarvis/agent/voice/client_vad.py`（新增）
- `jarvis/agent/config/settings.py`（修改）
- `jarvis/configs/settings.example.toml`（修改）
- `jarvis/tests/voice/test_realtime_stability.py`（新增）

@author aceFelix
