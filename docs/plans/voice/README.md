# 语音

> [实施计划索引](../README.md) 的第 7 类，共 4 篇。

| 文档 | 内容 | 状态 |
|---|---|---|
| [voice-provider-decoupling.md](voice-provider-decoupling.md) | 语音能力厂商解耦·阶段一（`/talk` 全双工 + `/voice` 半双工） | 阶段一已落地（2026-11-24），第二厂商待议 |
| [realtime-barge-in.md](realtime-barge-in.md) | `/talk` 全双工真打断：停流再启、音频闸门、本地 VAD 与救援 | 已实施（2026-11-25） |
| [talk-voice-layering.md](talk-voice-layering.md) | `/talk` 与 `/voice` 分层：`agent/audio` ← `agent/voice` + `agent/talk` | 已实施（2026-11-26），待合入主干 |
| [talk-hybrid-tools-delegation.md](talk-hybrid-tools-delegation.md) | `/talk` 工具混合：少量直带 + `delegate_task` 委派后台全量 Agent | **草案，待审核（未实施）** |
