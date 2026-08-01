# J.A.R.V.I.S 实时语音配置指南

> 实时双工语音对话（/talk）的配置、优化与故障排查。

---

## 快速启动

```bash
# 启动后输入
/talk

# 或 CLI 直接启动
jarvis --talk
```

> 需要 DashScope API Key（`DASHSCOPE_API_KEY` 环境变量或 `dashscope_api_key` 配置项）。

---

## 依赖安装

```bash
pip install pyaudio websockets
```

- **PyAudio**：Windows 上可能需要从 [这里](https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio) 下载 whl 安装
- **websockets**：`pip install websockets` 即可

---

## 配置项

以下全部可在 `~/.jarvis/settings.toml` 配置（`[tts]`/`[stt]`/`[voice]` 为表，其余为顶层字段）：

```toml
# ── 实时双工语音 /talk ──
# realtime_* 既可写顶层，也可写在 [realtime_talk] 表内（二选一，表内优先）
realtime_model = "qwen-audio-3.0-realtime-flash"   # 模型
realtime_voice = "longanqian"                        # 音色
realtime_ws_url = ""                                 # 自定义 WebSocket 端点（留空 = 默认 DashScope）

[realtime_talk]
api_key = ""                                        # → dashscope_api_key（实时语音专用 Key）
model = "qwen-audio-3.0-realtime-flash"
voice = "longanqian"
ws_url = ""
auto_start = false                                  # daemon 启动时自动进入实时聊天

# ── 语音打断（注意：barge_in / barge_in_key 必须在 [voice] 表内，写顶层无效）──
[voice]
barge_in = true          # 语音打断：说"闭嘴""等一下"等中断词
barge_in_key = true      # 键盘打断：按 ESC

# /voice 单轮聆听上限（顶层字段，默认 300 秒）
voice_max_seconds = 300.0

# ── TTS 配置（/voice 模式和 AI 回复朗读）──
[tts]
model = "cosyvoice-v3-flash"
voice = "longanlang_v3"
volume = 50
speech_rate = 1.0
pitch_rate = 1.0

# ── STT 配置（/voice 模式的语音识别）──
[stt]
model = "qwen3-asr-flash-realtime"
max_seconds = 15.0       # 单次录音最长秒数
silence_seconds = 1.5    # 连续静音多少秒视为说完
silence_threshold = 500  # RMS 静音阈值（QwenASR 后端走服务端 VAD，此值不生效）
```

---

## AEC 回声消除

### 为什么需要 AEC

外放扬声器场景下，麦克风会拾取扬声器播放的声音，导致 Jarvis "听到自己说话"而误触发。AEC（Acoustic Echo Cancellation）通过算法消除回声。

### 安装 AEC

```bash
pip install aec-audio-processing
```

安装后 `/talk` 启动时会自动显示：

```
AEC 回声消除已启用 · smart_turn · 说话打断 · ESC 退出
```

### 无需 AEC 的场景

- 使用耳机（物理隔离，无回声）
- 使用内置麦克风 + 笔记本扬声器（距离近，回声小）

---

## 音色选择

J.A.R.V.I.S 有两套独立的音色配置，别混用：

### /talk 实时音色（`realtime_voice`）

实时双工语音由 DashScope 实时模型（qwen-audio-3.0-realtime-*）直接输出语音，系统音色可选：

| voice 值 | 说明 |
|---|---|
| `longanqian` | 龙安千（**默认**，温柔知性女声） |
| `longanlingxin` | 龙安聆心 |
| `longanlingxi` | 龙安灵犀 |
| `longanxiaoxin` | 龙安小欣 |
| `longanlufeng` | 龙安路风 |

```toml
# 顶层字段，或写在 [realtime_talk] 表内
realtime_voice = "longanlingxin"
```

> 也支持**声音复刻**：用阿里云声音复刻功能创建音色（创建时 `target_model` 选
> `qwen-audio-3.0-realtime-flash`），把返回的 `voice_id` 填入 `realtime_voice` 即可。

### /voice TTS 音色（`tts_voice`）

`/voice` 的播报走 CosyVoice TTS（`tts_model = "cosyvoice-v3-flash"`），默认音色 `longanlang_v3`（龙安朗，沉稳男声）。CosyVoice v3 系列有 **100+ 个系统音色**（女声/男声/童声/方言等），常见如：

| voice 值 | 说明 |
|---|---|
| `longanlang_v3` | 龙安朗（**默认**，沉稳男声） |
| `longxiaochun_v3` | 龙小淳（活泼女声） |
| `longxiaoleng_v3` | 龙小冷（冷艳女声） |
| `longcheng_v3` | 龙城（成熟男声） |
| `loongyuuna_v3` | Yuuna（日语女声） |
| `Ono Anna` | 小野杏（日式漫画音） |
| `loongriko_v3` | Riko（日语甜妹） |

```toml
[tts]
voice = "longxiaochun_v3"
```

> 完整音色列表见阿里云官方文档
> [CosyVoice 音色列表](https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list)
> （不同 `tts_model` 支持的音色集合不同）。也支持**声音复刻**，把复刻的 `voice_id` 填入 `tts_voice`。

### /tts-voice 命令

在 REPL 中可用 `/tts-voice` 交互式选择 / 添加音色，无需手改配置（目前仅支持阿里云 DashScope）：

```text
/tts-voice               # 交互式列表：↑↓ 选择，Enter 确认；Space 管理自定义音色
/tts-voice long          # 前缀匹配切换（如 longxiaochun_v3）
/tts-voice <Tab>         # Tab 自动补全已存在的音色
```

列表含内置音色 + 自定义音色；选择「+ 添加音色」可录入自定义音色（音色名 + DashScope 音色 ID，
声音复刻的 `voice_id` 也可），持久化到 `~/.jarvis/settings.toml` 的 `[tts.custom_voices]`。

---

## 交互方式

### 语音打断

说话过程中说以下词语可打断 Jarvis：

- "闭嘴"、"等一下"、"停"、"别说了"

打断后 Jarvis 立即停止说话，切换回聆听状态。

### ESC 键打断

播报中按 `ESC` 立即停止并切回聆听。不依赖 PyAudio，在 daemon 无窗口模式也能用。

### 退出

说"退下"、"贾维斯退下"、"结束对话"、"再见"、"拜拜"、"没事了"等，或按 `Ctrl+C`。

---

## 故障排查

### 启动报错

| 错误 | 解决 |
|---|---|
| `缺少 websockets 库` | `pip install websockets` |
| `缺少 pyaudio 库` | 下载 whl 安装 |
| `音频设备初始化失败` | 检查麦克风/扬声器是否被其他程序占用 |
| `Authentication failed` | 检查 `dashscope_api_key` 是否正确 |
| `WebSocket 连接超时` | 检查网络，DashScope 实时语音需要稳定的公网连接 |

### 声音断续 / 卡顿

1. 网络波动 → 实时语音对延迟敏感
2. CPU 占用高 → 关闭其他重负载程序

### 外放时自言自语

1. 安装 AEC：`pip install aec-audio-processing`
2. 调低扬声器音量
3. 使用耳机

### 麦克风没声音

```bash
python -c "import pyaudio; p = pyaudio.PyAudio(); print(p.get_default_input_device_info())"
```

检查默认输入设备是否正确。

---

## 架构简述

```
[麦克风] → PyAudio 采集 (16kHz/mono) → WebSocket 发送
                                          ↓
                                    DashScope Realtime API
                                          ↓
[扬声器] ← PyAudio 播放 (24kHz/mono) ← WebSocket 接收

并发协程：
  _send_audio()     → 持续发送麦克风数据
  _recv_events()    → 接收识别结果 + AI 回复音频
  _esc_watcher()    → 监听 ESC 键打断
  _load_mcp_tools() → 后台加载 MCP 工具（不阻塞启动）
  _watchdog()       → 心跳检测，断连自动重连
```
