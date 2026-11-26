# /talk 与 /voice 分层拆分（talk-voice-layering）

> 状态：**计划已审，实施中**（2026-11-26）
> 分支：`refactor/talk-voice-layering`（基线 `feat/friday-persona` @ `f90c046`）
> 作者：aceFelix

## 1. 为什么要拆

`agent/voice/` 里混装了两条**完全独立**的语音链路：

- `/voice`：STT → LLM → TTS 半双工管线（`voice_loop` 编排，走标准 LLM API 与全量工具）
- `/talk`：DashScope Realtime WebSocket 全双工（`RealtimeEngine` 状态机，自带 Function Calling）

两者只共享 4 个音频基础设施模块，其余 28 个模块各属一条链路。`realtime_*` 前缀本身
已经是事实上的子包边界，继续混在一个目录里会让"改 /talk 会不会影响 /voice"这类判断
只能靠人肉翻 import。本次把边界落成目录。

## 2. 目标分层

```
agent/audio/          音频基础设施（设备单例 / AEC / 麦克风互斥 / 厂商注册表 / DSP）
   ↑          ↑
agent/voice/   agent/talk/       两条链路，互不 import
   ↑          ↑
agent/commands/  agent/serve/    宿主装配层，可同时依赖两者
```

**依赖规则（硬约束）**：

1. `agent/audio` **禁止** import `agent.voice` 或 `agent.talk`（它只依赖第三方与标准库）。
2. `agent/voice` 与 `agent/talk` **互不 import**；需要共用的一律下沉到 `agent/audio`。
3. 宿主层（commands / serve）可以 import 两条链路，负责互斥与生命周期编排。
4. 新增共用能力时先问"是不是音频基础设施"，是就进 `agent/audio`，不要在两条链路里各写一份。

## 3. 文件映射

### 3.1 agent/audio/（新建）

| 新路径 | 来源 | 职责 |
|---|---|---|
| `__init__.py` | 新建 | 聚合导出常用入口 |
| `device.py` | `voice/audio.py` | PyAudio 全局单例 `get_pyaudio` / `release_pyaudio` |
| `aec.py` | `voice/aec.py` | WebRTC AEC3 回声消除包装 |
| `mic_lock.py` | `voice/voice_state.py` | 麦克风互斥锁（/talk 与 /voice 不能同时开麦） |
| `providers.py` | `voice/voice_providers.py` | 语音厂商注册表（STT/TTS/realtime 共用，未知厂商 fail-closed） |
| `dsp.py` | 抽自 `voice/realtime_audio._rms` | 归一化 RMS（16bit mono → 0.0~1.0） |

### 3.2 agent/talk/（新建，11 个模块，保留原文件名）

    realtime_engine.py       协议状态机与轮次编排
    realtime_recv.py         服务端事件接收循环
    realtime_rescue.py       响应收尾 / 救援 / 看门狗
    realtime_talk.py         终端适配器（PyAudio + ESC + 人设指令）
    realtime_tools.py        Function Calling 工具层
    realtime_mcp.py          MCP 工具加载
    realtime_audio.py        衰减 / 静音窗 / 本地 VAD 调参常量
    realtime_const.py        音频与救援阈值常量
    realtime_events.py       事件时间线日志
    realtime_bridge_audio.py 桌面桥接音频对象
    talk_history.py          转写按轮配对落库

包内相对导入保持不变（整组一起搬）；跨包导入改指 `agent.audio.*`。

### 3.3 agent/voice/（保留 /voice 管线）

    stt/  tts.py  stream_tts.py  tts_text.py  tts_voices.py
    voice_loop.py  voice_events.py  repl_adapter.py  voice_config.py
    wake_words.py  barge_in.py  voice_barge.py

### 3.4 删除（死代码）

- `voice/client_vad.py`：生产链路零引用，只有 `tests/voice/test_realtime_stability.py`
  在测它；realtime 的本地 VAD 实际由 `realtime_audio` 的函数承担。连同该测试一并删除。

## 4. 关键设计点

### 4.1 消除唯一的反向依赖

拆分前 `voice/voice_barge.py` 借用 `realtime_audio._rms`，拆后会变成 voice → talk 的
反向依赖。处理方式：把归一化 RMS 抽到 `agent/audio/dsp.py`，两侧都从基础设施取。

### 4.2 RMS 有三份，只统一两份

| 实现 | 契约 | 处理 |
|---|---|---|
| `realtime_audio._rms` | `bytes → float`（0.0~1.0） | 下沉为 `audio/dsp.rms` |
| `client_vad._rms` | 同上（重复实现） | 随死代码删除 |
| `stt/common._rms` | `bytes, width → int` | **保留不动**，契约不同，合并会改行为 |

### 4.3 不留兼容 shim

内部项目，一次改净：旧路径不保留 re-export 模块。`realtime_talk.py` 对外 re-export 的
常量（`CHUNK_BYTES` / `ECHO_GUARD_RMS` 等）跟着搬，测试的 `rt_mod.CHUNK_BYTES` 用法不变。

### 4.4 已知差异（本次不改）

`realtime_talk.run()` 的终端路径自建 `pyaudio.PyAudio()`，而 /voice 侧走 `device.get_pyaudio()`
单例。两者并存是历史现状（Windows 多实例并发有 segfault 风险，见
`docs/fixlogs/voice/` 相关复盘）。本次**只搬不改行为**，该差异另开话题处理。

## 5. 实施顺序（5 个提交）

1. `docs`：本文件落库（不含代码改动）。
2. `refactor(audio)`：下沉 4 模块 + 新建 dsp + 删 client_vad + 改 17 处引用与测试 patch 字符串。
3. `refactor(talk)`：搬 11 模块 + 8 个测试文件与 conftest/_fakes 归位 `tests/talk/` + 宿主引用。
4. `docs`：19 个 md 的路径同步、架构 06 分层图、目录树、CHANGELOG。
5. `chore(aoci)`：索引迁移（旧路径条目删、新路径条目建、R 引用改），三证收尾。

## 6. 验收标准

- [ ] `compileall agent` 与 `ruff --select F821 agent/` 通过
- [ ] 全量 `pytest tests/ -q` 0 failed（删 client_vad 测试后重新记录基线）
- [ ] `agent.voice` 与 `agent.talk` 互不 import；`agent.audio` 不 import 两者
- [ ] 终端 `/talk`：起会话 → 报时 → 说"退下"先听告别再退出
- [ ] `/voice`：起会话 → 开口打断 → 空格打断，行为与拆分前一致
- [ ] 麦克风互斥仍生效（/talk 与 /voice 不能同时开麦）
- [ ] wheel 含 `agent/talk/*` 与 `agent/audio/*`，`agent/voice/` 不再含 `realtime_*`
- [ ] Markdown 死链检查 0
- [ ] AOCI verify / check / guide 三证全绿

## 7. 明确不在本次范围

- 不改 `tools_mode` 默认、不改星期五人设、不给 /talk 加工具子集
- 不合并 `stt/common._rms`
- 不改 `/voice` 唤醒词默认值
- 不做文件改名（`realtime_*` 前缀保留）、不改对外 API 与协议事件名
