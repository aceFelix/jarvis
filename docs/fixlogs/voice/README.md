# 语音系统

> [修复与复盘日志索引](../README.md) 的第 1 类，共 26 篇。
> 新增一篇时在本表补一行；文件名与路径保持 `<主题>-<简短描述>-fix.md`。

| 文件 | 主题 |
|---|---|
| [realtime-talk-auth-fix.md](realtime-talk-auth-fix.md) | `/talk` 鉴权失败（语音只认 DashScope key） |
| [realtime-talk-echo-guard-window.md](realtime-talk-echo-guard-window.md) | 外放回声自打断的"智能折中"方案 |
| [realtime-talk-echo-self-cancel-fix.md](realtime-talk-echo-self-cancel-fix.md) | 回声自打断导致 AI 完全不回复 |
| [realtime-talk-echo-self-talk-fix.md](realtime-talk-echo-self-talk-fix.md) | 回声自激与说话不全 |
| [realtime-talk-half-duplex.md](realtime-talk-half-duplex.md) | 外放"AI 说完不接话"的半双工方案 |
| [realtime-talk-response-done-fix.md](realtime-talk-response-done-fix.md) | `response.done` 缺失导致只能回答一句 |
| [realtime-talk-silent-turn-observability.md](realtime-talk-silent-turn-observability.md) | 静默轮排查与第一层可观测性 |
| [realtime-talk-tools-mode-default-fix.md](realtime-talk-tools-mode-default-fix.md) | `tools_mode=all` 导致语音不可用（回退 builtin） |
| [realtime-talk-voice-history-persist-fix.md](realtime-talk-voice-history-persist-fix.md) | 实时语音对话不进会话历史 |
| [realtime-talk-xiaoyun-persona-fix.md](realtime-talk-xiaoyun-persona-fix.md) | 桌面实时语音自称"小云"（人设丢失） |
| [talk-session-persist-fix.md](talk-session-persist-fix.md) | 终端 `/talk` 会话落库缺失 |
| [voice-api-key-onboarding-fix.md](voice-api-key-onboarding-fix.md) | 首启引导缺语音 Key（两套凭据） |
| [voice-exit-and-tts-drain-fix.md](voice-exit-and-tts-drain-fix.md) | Ctrl+C 退进程、TTS 尾音被误录、MCP 退出刷屏 |
| [voice-false-wake-fix.md](voice-false-wake-fix.md) | 无声音自动唤醒 / 对话幻听 |
| [voice-half-duplex-persist-fix.md](voice-half-duplex-persist-fix.md) | 半双工语音对话不进会话历史 |
| [voice-idle-standby-after-reply-fix.md](voice-idle-standby-after-reply-fix.md) | 长回答结束后立刻待机 |
| [voice-loop-tests-missing-deps-fix.md](voice-loop-tests-missing-deps-fix.md) | CI 上 voice 测试因缺 `[voice]` 依赖误败 |
| [voice-missing-deps-degrade-fix.md](voice-missing-deps-degrade-fix.md) | 缺依赖时 `/voice` 无限刷屏 |
| [voice-mode-fix.md](voice-mode-fix.md) | Voice Mode 三段式修复（听 / 想 / 说） |
| [voice-session-title-fix.md](voice-session-title-fix.md) | `/voice` 会话只有时间戳、不生成标题 |
| [voice-stop-unresponsive-fix.md](voice-stop-unresponsive-fix.md) | 桌面壳语音控制按钮无响应 |
| [voice-turn-done-fix.md](voice-turn-done-fix.md) | 语音轮缺收尾信号，气泡停不下来 |
| [voice-wake-word-homophone-fix.md](voice-wake-word-homophone-fix.md) | 唤醒词配置化 + 拼音同音容错 |
| [stt-session-updated-never-arrives-fix.md](stt-session-updated-never-arrives-fix.md) | 死等 `session.updated`，每轮录音前失败 |
| [tts-stream-lifecycle-fix.md](tts-stream-lifecycle-fix.md) | 流式 TTS：语音会断、尾音被截、新旧混播 |
| [standby-display-and-mcp-health-fix.md](standby-display-and-mcp-health-fix.md) | `<standby/>` 泄漏上屏 + MCP 就绪后健康不刷新 |
