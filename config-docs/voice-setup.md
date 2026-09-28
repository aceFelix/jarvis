# J.A.R.V.I.S 实时语音配置指南

> 实时双工语音对话（/talk）的配置、优化与故障排查。

---

## 快速启动

```bash
# 启动后输入（REPL 内纯终端全双工对话，2026-09 起不再弹独立窗口）
/talk

# 或 CLI 直接启动（打开三栏工作台，窗口内左栏切换实时模式；--gui 等价）
jarvis --talk
```

> 需要 DashScope API Key（`DASHSCOPE_API_KEY` 环境变量或 `dashscope_api_key` 配置项）。

---

## 依赖安装

```bash
pip install pyaudio
```

- **PyAudio**：Windows 上可能需要从 [这里](https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio) 下载 whl 安装
- **websockets**：已为核心依赖，随 `pip install` 自动安装（2026-09 起，无需手动安装）

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
event_log = false                                   # 事件时间线日志（写 ~/.jarvis/logs/diag.log），默认关
echo_suppress_with_aec = true                       # AI 说话时压低麦克风抑制残余回声（仅戴耳机时才建议关）
half_duplex = true                                  # 半双工：AI 说话期静音麦克风（外放稳定多轮）；耳机全双工设 false
rescue = true                                       # 响应救援：吞轮时补发 response.create（默认开）
turn_detection = "server_vad"                       # 轮次检测：server_vad（默认）/ smart_turn
silence_ms = 500                                    # server_vad 判停静音时长 ms（200~6000）
tools_mode = "builtin"                              # 工具面：builtin（默认 2 工具低延迟）/ all（Registry+MCP）

# ── 语音打断（注意：barge_in / barge_in_key 必须在 [voice] 表内，写顶层无效）──
[voice]
barge_in = false         # 麦克风打断：播报中检测到用户开口自动打断（默认关）
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
model = "qwen3-asr-flash-realtime"   # 唯一后端，原样透传给 DashScope
max_seconds = 15.0       # 单次录音最长秒数（客户端兜底超时）
silence_seconds = 1.5    # 服务端 VAD 连续静音多少秒视为说完
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
轮次检测: server_vad · 半双工轮替·AI 说完你再说 · ESC 退出
```

### 无需 AEC 的场景

- 使用耳机（物理隔离，无回声）
- 使用内置麦克风 + 笔记本扬声器（距离近，回声小）

### 装了 AEC 为什么还要压低麦克风

WebRTC AEC3 在免提外放 + 非理想延迟对齐下**只能消除部分回声**，残留会被
服务端重新识别为用户语音，触发打断并取消 AI 本轮回复——表现为“AI 永远不开口”。
因此 `/talk` 在 AI 说话期间会把麦克风音频乘 `ECHO_SUPPRESS_FACTOR`（0.1）作为兜底，
**AEC 已启用时同样生效**；并配合“回声保护窗口”：AI 说话期间若衰减后麦克风电平仍
很低，`speech_started` 会被判为回声误触发而忽略，不取消回复。

该压低只在 AI 真正说话期间生效，不会永久压小您的语音。佩戴耳机时可关掉它以恢复
正常增益：

```toml
[realtime_talk]
echo_suppress_with_aec = false   # 默认 true；仅耳机场景建议关闭
```

> 若 `/talk` 出现“一直在说、AI 完全不回复”，退出时的诊断摘要会提示“回复被取消”
> 及其回声成因；打开 `event_log = true` 可在 `~/.jarvis/logs/diag.log` 看到完整事件时间线。
> 完整复盘见
> [realtime-talk-echo-self-cancel-fix.md](../docs/fixlogs/realtime-talk-echo-self-cancel-fix.md)。

### 外放时 AI 说完就不接话？半双工轮替（默认开启）

即使有 AEC + 压低 + 回声保护，**免提外放**下仍可能出问题：只要 AI 说话期间麦克风
持续上传，服务端就可能把回声/混响当成“您还在说话”，于是 AI 说完后迟迟等不到
安静信号、**永远不开启下一轮**（典型表现：第一轮能答，之后您说话它一直不接）。

`/talk` 默认用**半双工**从源头解决：AI 说话期间完全不上传麦克风，说完后再留 0.6 秒
回声尾迹静默期才恢复拾取。您只需“等它说完再接话”，多轮就稳定了。代价是失去随口打断
——但外放场景本就打断不了，等于无损。

戴耳机想恢复“随时打断”的全双工时：

```toml
[realtime_talk]
half_duplex = false   # 默认 true（半双工）；设为 false 需配合耳机使用
```

复盘见 [realtime-talk-half-duplex.md](../docs/fixlogs/realtime-talk-half-duplex.md)。

### 轮次检测：为什么默认 server_vad（2026-09-28 根因翻案）

旧版默认 `smart_turn`（语义判停）实测存在“尾音重检”灾难：语义判停偏早，判停后
客户端仍在直播真实麦克风，尾音/换气被服务端 VAD 重新检出为“新轮次”，刚创建的响应
被 `response.done[cancelled, reason=turn_detected]` 掐死——表现为“每轮必取消”。
现默认改为官方免提推荐的 **server_vad**（声学 VAD + 静音时长判停，尾音只会延长当前轮）。

```toml
[realtime_talk]
turn_detection = "server_vad"   # 默认；需要环境音转写/声纹能力时才设 "smart_turn"
silence_ms = 500                # 判停静音时长：说话节奏慢/多人环境可调大到 800~1500
```

设 `smart_turn` 时引擎会自动叠加“轮末静音窗”（判停后 3s 发静音帧堵尾音，您真在
继续说话则立即恢复，不吞话）作为缓解手段，但仍建议安静环境使用。

### 响应救援（rescue）：防“说了话永远不回复”

服务端存在两类吞轮异常：响应被 turn_detected 取消后不再补答；轮次提交后迟迟不
自动建响应。`rescue = true`（默认）时，客户端在 1.2~1.8s 后补发一次
`response.create` 兜底，并有防“重复回答”三闸门（任何 response.done 撤销待发救援、
单字/回声轮不武装、两次救援最小间隔 2s）。遇到“它确实没答”时屏幕会提示
`🛟 响应救援`。排查阶段可用 `rescue = false` 关闭验证。

### 工具面瘦身（tools_mode）

实测 299 个工具 schema 的全量会话会让服务端“轮次提交后迟迟不创建响应”。默认
`builtin` 只给语音会话 2 个内置工具（时间查询/结束对话）；需要文件/命令/MCP 等
全量能力时设 `tools_mode = "all"`（会话启动稍慢，需自行验证稳定性）。MCP 工具
会在首个 `session.update` 之前一次性加载完毕——中途补发会毒化会话（历史故障根因）。

### 桌面端真全双工（jarvis-desktop）

桌面壳的语音模式是**说话即打断**的真全双工：浏览器 `getUserMedia` 的系统级回声
消除（AEC）在声音进网络前就消除 AI 外放回声，服务端只听到您的真实语音；音频经
serve 协议（`talk.audio` 上行 / `talk_audio` 下行）桥接给同一个 `RealtimeEngine`。
桌面路径自动关闭半双工/软件压低/AEC 三重消除，**无需任何配置**；终端 `/talk`
仍是半双工轮替（软件 AEC 不彻底下的稳定解）。架构见
[docs/architecture/06-语音系统.md](../docs/architecture/06-语音系统.md) “桌面全双工桥接”一节。

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

> ⚠ **音色与模型是硬约束**：DashScope 系统音色按模型系列隔离（表中 `_v3`
> 音色只能配 `cosyvoice-v3-*` 系列，配 `cosyvoice-v2` 会报 `voice is not exists`）；
> 声音复刻的 `voice_id` 更绑定创建时指定的 `target_model`，同一声音跨模型使用
> 需对每个目标模型各复刻一次。`/tts-voice` 已据此做切换联动（见下）。

### /tts-voice 命令

在 REPL 中可用 `/tts-voice` 交互式选择 / 添加音色，无需手改配置（目前仅支持阿里云 DashScope）：

```text
/tts-voice               # 交互式列表：↑↓ 选择，Enter 确认；Space 管理自定义音色
/tts-voice long          # 前缀匹配切换（如 longxiaochun_v3）
/tts-voice <Tab>         # Tab 自动补全已存在的音色
```

列表含内置音色 + 自定义音色（描述行透出适配模型，不兼容时标注联动去向）；
选择「+ 添加音色」进入四字段表单，持久化到 `~/.jarvis/settings.toml` 的
`[tts.custom_voices]`：

| 字段 | TOML 键 | 说明 |
|---|---|---|
| 音色名 | section 键 + `name` | 列表展示名 |
| DashScope 音色 ID | `voice_id` | 请求参数（系统音色 / 声音复刻 ID 均可） |
| 适配模型 | `model` | 下拉选：`cosyvoice-v3`（家族前缀，v3-flash/plus/v3.5-plus 均可）/
|  |  | 具体模型（复刻音色选创建时的 target_model）/ 空串 = 不限 |
| 供应商 | `vendor` | 目前仅 `dashscope` |
| 描述 | `description` | 可选 |

```toml
# 自动生成的自定义音色示例（也可手写）
[tts.custom_voices."我的声音"]
name = "我的声音"
voice_id = "my-clone-xxxx"
model = "cosyvoice-v3-flash"   # 复刻时指定的 target_model
description = "声音复刻 - 我的声音"
vendor = "dashscope"
```

**切换联动**：音色带 `model` 且与当前 `tts_model` 不兼容时，自动联动切换
`tts_model` 并落盘（重启保持），保证切换后立即可合成；家族前缀（如
`cosyvoice-v3`）匹配家族内任意模型（v3-flash / v3.5-plus 互切不联动），
联动目标取家族默认具体模型（`cosyvoice-v3-flash`）。

### 桌面壳音色管理（jarvis-desktop）

桌面壳左栏「音色」面板与终端 `/tts-voice` 同口径共用切换公共层（2026-09-28 接入），
无需打开终端也能管理音色：

- **全量目录**：面板列出内置 + 自定义音色（当前音色置顶），副行透出适配模型
  （「适配 X」）与不兼容预告（「联动 X」= 现在点选会自动联动成的 tts_model）；
- **点选即切**：发 `voices.select`，立即写盘并在不兼容时自动联动 `tts_model`，
  聊天流提示「音色已切换为 X（联动 TTS 模型 Y，下次语音生效）」；运行中的语音
  会话不热切换，仍为下次语音生效；
- **添加 / 编辑**：末项「＋ 添加音色」进表单（音色名 / DashScope 音色 ID / 适配模型
  下拉 / 描述，与终端四字段同口径）；双击自定义项进表单预填编辑（音色名锁定，
  同名 upsert 覆盖）；
- **删除**：右键自定义项显删除按钮、再点才真删（二次确认；内置音色不可删，
  后端也会拒绝）。

自定义音色同样落 `~/.jarvis/settings.toml` 的 `[tts.custom_voices]`，终端与桌面壳
看到同一份目录、行为完全一致。

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
| `code=1007 Access denied / account in good standing` | DashScope 鉴权被拒：key 无效，或百炼账号欠费/未开通实时语音模型；确认 `dashscope_api_key`（或 `DASHSCOPE_API_KEY`）与控制台账号状态 |
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
【终端】                          【桌面 jarvis-desktop】
麦克风(PyAudio 16k)                getUserMedia+浏览器AEC → AudioWorklet 重采样 16k
   ↓ attach_audio                  ↓ serve 指令 talk.audio(base64 帧)
RealtimeEngine（协议状态机/静音策略/响应救援/Function Calling，传输无关）
   ↑ attach_audio                  ↑ BridgeMic / BridgeSpk
扬声器(PyAudio 24k)                Web Audio 顺序排播 24k ← serve 事件 talk_audio
   ↕ WebSocket
DashScope Realtime API（server_vad 判停 / 理解 / 生成 / TTS）

引擎并发协程：
  _send_audio()       → 持续发送麦克风数据（含静音门控）
  _recv_events()      → 接收识别结果 + AI 回复音频
  _response_rescue()  → 响应救援：吞轮时补发 response.create
  _watchdog()         → 停止时主动关闭 WS，解除 recv 阻塞
终端附加：_esc_watcher() → 监听 ESC 键退出
```
