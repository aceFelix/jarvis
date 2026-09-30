# 未来实时语音升级蓝图（财富自由版）

> 状态：**暂缓（BACKLOG）**。本文档只画蓝图、排优先级、算成本，不代表近期实现。
> 启动条件写得很直白——**什么时候 DashScope 实时语音的账单不再是考虑因素了，什么时候开工**。
> 在此之前，`/talk` 保持"开着即常听、说退下即结束会话"的现状，`/voice` 提供退下/唤醒。
>
> @author aceFelix

## 0. 为什么要留这份文档

现状盘点（2026-09）：

| 能力 | `/voice`（半双工 + 本地逐句 STT） | `/talk`（云端 OmniRealtime 全/半双工） |
|---|---|---|
| 退下进入待机 | ✅ `_detect_standby`（模型回 `<standby/>`） | ❌ `end_conversation` → 直接**关闭会话** |
| 「贾维斯」唤醒 | ✅ `_standby_round` + `_WAKE_WORDS` 本地短录音 | ❌ 无（会话已退出，只能重新点开） |
| 轮次控制方 | 客户端（本地 ASR 逐句转写，自己判断） | **服务端 VAD**（上传即自动应答） |
| 音频链路 | PyAudio 本地采集 | 桌面：渲染进程 `getUserMedia` → `BridgeMic` → WS；终端：PyAudio |

核心矛盾：`/talk` 把"要不要回复"的决策权交给了云端 server_vad，客户端**没法"听着但决定不答"**；
而 `/voice` 的唤醒机制依赖本地逐句 ASR，`/talk` 桌面链路里麦克风被渲染进程独占、再不起第二个 ASR。
所以 `/talk` 的"待机 + 唤醒"不是抄 `/voice` 就行，需要专门设计——本文档给出三级方案，越往上越"费钱"。

---

## L1 · 引擎内"停车/点火"门控待机（最贴桌面，先做这个）

**一句话**：不拆会话、不重连、不起第二个 ASR，把待机做成 `RealtimeEngine` 里的一个门控标志，
复用云端本来就在回传的转写事件当唤醒探测器。

### 设计

状态机加 `_standby: bool`（见 `agent/voice/realtime_engine.py`）：

- **进入待机**：模型识别"退下/不用了/去忙吧"→ 调新内置工具 `enter_standby`
  （或在 `standby_enabled` 时把 `end_conversation` 的回调从 `_request_graceful_stop` 改指向 `enter_standby`）。
  置 `_standby=True`、`ui.on_status("standby")`、提示"💤 待命中，说「贾维斯」唤醒"。**WS 与麦克风上传都不动**。
- **待机期间**（继续上传音频，否则云端转写不到唤醒词）：
  - `conversation.item.input_audio_transcription.completed` 命中 `_WAKE_WORDS`（贾维斯/jarvis/谐音，
    复用 `agent/voice/voice_config.py`）→ `_standby=False`、`on_status("listening")`、
    **放行本轮响应**（模型自然回"在的，先生"，唤醒即有反馈）。
  - 未命中 → 标记 `_suppress_next=True` 吞掉本轮：`response.created` 到达即 `response.cancel`，
    `response.audio.delta` **本地丢弃不 `spk.write`**（用户完全无感），待机期间**跳过 `_response_rescue`**
    （防止救援协程把被吞的轮次又补发出来）。
- **彻底退出**：仍走 `talk.stop`（窗口停止按钮 / ESC），语义不变。

### 为什么这套最贴桌面

渲染进程 `getUserMedia` 流不动 → 无重连授权闪烁、无 1–2s 重连延迟；唤醒近实时（一个已在跑的云端往返）；
Python 侧零新增 ASR、不抢麦克风；桌面 `on_status("standby")` 已映射 `talkStatusLabels.standby='实时待命'`
且反应炉已有 `standby` 态，UI 几乎零改。引擎传输无关 → 终端 `/talk` 一并获得。

### 硬约束（务必遵守，来自既有踩坑）

- **禁止中途补发 `session.update` 切换 `turn_detection`**：会让此后每条响应在 ~100ms 内被服务端 cancelled
  （见 `realtime_engine.py` 模块 docstring 经验①）。所以不能用"切成 manual 模式让客户端控轮"实现待机，
  只能走"放行/吞掉"门控。

### 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/voice/realtime_engine.py` | `_standby` 门控 + `enter_standby()` + 吞轮（cancel + 本地丢音频）+ 待机期抑制 rescue |
| `agent/voice/realtime_tools.py` | 新内置工具 `enter_standby`；`standby_enabled` 时 `end_conversation` 回调改指向待机 |
| `agent/ui/workbench/engine.py` | `_handle_start_talk_duplex` 透传 `standby_enabled` |
| `agent/voice/realtime_talk.py` | 终端适配器同步透传 |
| `agent/config/settings.py` + `configs/settings.example.toml` | `[realtime_talk] standby_enabled` / `standby_wake_words` |
| `jarvis-desktop` 渲染层 | 可选：待机文案细化为"待命中·说贾维斯唤醒" |
| `tests/voice/test_realtime_standby_wake.py` | 新增：命中放行 / 未命中吞掉 / rescue 抑制 回归 |

### 成本

- 开发：小（单引擎改动 + 一个工具 + 配置透传）。
- 运行：**待机期间云端会话仍开着、仍在收音频 → 持续计实时语音分钟数**。这是它唯一的"贵"点：
  不是零成本空闲，而是"挂着不聊也在烧"。适合"随时待命、唤醒要快"的场景，不适合"一整天开着不管"。

### 验收

- [ ] `/talk` 说"退下"→ 进入待机（状态"实时待命"），不再关闭会话。
- [ ] 待机中说无关话 → 无回复、无声音（被吞），说"贾维斯"→ 秒唤醒并应答。
- [ ] 唤醒后正常多轮；`talk.stop`/ESC 仍能彻底退出。
- [ ] 终端 `/talk` 同口径生效。

---

## L2 · 深待机 + 离线唤醒词（要"零成本常开"才上这个）

**动机**：L1 待机仍在烧云端额度。若要"整天开着、不聊不花钱、喊一声才点火"，需要**本地离线唤醒词检测**
（Keyword Spotting，如 Porcupine / openWakeWord 类），待机时**关闭 WS、麦克风交给本地 KWS**，命中"贾维斯"
再重连 `/talk`。

### 设计要点

- 待机 = 关实时会话（`mic.close`/释放）+ 起一个极轻量本地 KWS 监听（独立线程/进程）。
- 命中唤醒词 → 重连 DashScope WS + `session.update`（新会话，IDLE 态允许）→ 恢复 L1 对话态。
- 与 L1 组合：L1 负责"对话中↔短时待机"，L2 负责"长时间深待机省额度"，用 `standby_deep_after_s`
  （待机静置超过 N 秒升级到深待机）衔接。

### 代价 / 为什么排 L2

- **桌面重连体验**：getUserMedia 重新授权/初始化有可感知延迟与偶发权限弹窗，需专门做"静默重连"缓冲。
- **新增离线依赖**：Porcupine 有商用授权费；openWakeWord 需自己训练"贾维斯"唤醒模型 + 算力。
- **误唤醒/漏唤醒调参**：离线 KWS 的召回/误报平衡要专门调，比 L1 用云端 ASR 转写更费劲。
- 收益：**深待机零云端消耗**，适合长时间挂机。

### 涉及文件（增量）

`agent/voice/wake_word.py`（新增，KWS 适配层）、`realtime_engine.py`（深待机挂起/点火）、
`settings`（`standby_deep_enabled` / `standby_deep_after_s` / `wake_engine`）、桌面侧重连缓冲逻辑、
`tests/voice/test_deep_standby_wake.py`。

---

## L3 · 全时环境感知 / 主动对话（真正烧钱的终极形态）

**动机**：从"你问我答"进化到"它一直在听环境、该插话时主动说"。

- 全时拾取 + 本地 VAD/KWS 前置过滤，只有疑似相关才上行云端（混合 L1/L2 控成本）。
- 结合主动感知系统（`ProactiveHub` / Scheduler / DeadlineTracker）做"到点主动提醒 + 上下文播报"。
- 声纹识别（区分"谁在说话"，只回应主人）、多路音频场景理解。

### 为什么最贵

- 实时语音分钟数从"对话时"扩展到"准全时"，**账单量级跳档**。
- 声纹/场景理解需额外模型与服务。
- 主动打断的"分寸感"是产品难题（打扰 vs 有用），需大量调优。
- 隐私：全时拾取需明确的本地优先、可一键关停、拾取范围可见等安全设计。

---

## 成本对照（决定"什么时候升哪一级"）

| 级别 | 开发量 | 运行成本 | 待机是否烧云端额度 | 唤醒速度 | 启动条件 |
|---|---|---|---|---|---|
| L1 门控待机 | 小 | 中（待机即开始计） | **是** | 近实时 | 想快速拿到桌面退下/唤醒体验 |
| L2 深待机+离线KWS | 中 | 低（深待机零云端） | 否（超时后） | 唤醒后需重连（可缓冲） | 在意长时间挂机的额度 |
| L3 全时环境感知 | 大 | 高（准全时计费 + 额外模型） | 是（大量） | 全时在线 | **财富自由** 🙂 |

## 落地顺序建议

1. 先 L1：一次改动打通终端+桌面，体验提升立竿见影，成本可控（用完记得 `talk.stop` 即止烧）。
2. 有"挂机一整天"诉求再上 L2 的深待机。
3. L3 属于产品形态跃迁，等预算与隐私方案都到位再说。

## 关联现状文档

- 稳定性升级（重连/抖动缓冲/客户端 VAD/淡出打断）见 [PLAN-p1-realtime-stability.md](PLAN-p1-realtime-stability.md)。
- 半双工/回声/唤醒既有实现：`agent/voice/voice_loop.py`、`agent/voice/voice_config.py`。
- 实时引擎与桌面桥接：`agent/voice/realtime_engine.py`、`agent/voice/realtime_bridge_audio.py`、
  `agent/ui/workbench/engine.py::_handle_start_talk_duplex`。
- 退下标记不泄漏上屏复盘：`docs/fixlogs/`（`<standby/>` 显示路径剥标）。
