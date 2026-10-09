# 实时语音对话不进会话历史修复

> 修复日期：2026-10-09 ｜ 模块：桌面工作台实时语音 × 会话持久化 ｜ 作者：aceFelix

## 1. 问题现象

- 用户在桌面 jarvis（Electron 壳）里进行实时语音对话，聊了多轮；
- 退出实时语音、恢复会话（或查看左栏会话条数）后，**语音问答气泡全部丢失**，
  会话里只剩文本对话的消息（实测「Hello-jarvis」仅存 2 条文字消息）；
- 复现路径：进入实时语音 → 任意对话几轮 → 退出/切换会话 → 恢复原会话 → 语音内容消失。

## 2. 排查过程

1. **假设：渲染层没把语音气泡回放** —— 读 `session_manager._auto_save` 与
   `ChatEngine._handle_load`：恢复逻辑本身会回放 `self._messages` 全部内容，
   渲染层无问题。排除。
2. **确认：语音内容从未进 `_messages`** —— 桌面实时语音（全双工与 PyAudio 两条路径）
   在 DashScope 实时通道内闭环（`RealtimeEngine.run_session` 直连 WS），**不经
   QueryLoop**，全程没有任何代码把转写追加到 `engine._messages`；`_auto_save` 只
   保存 `self._messages` → 语音内容天然不落盘。根因确认。
3. **对照：/voice 半双工语音无此问题** —— voice_loop 走 QueryLoop/ctx.messages，
   轮次天然入会话。仅实时语音（/talk 与桌面全双工）受影响。

## 3. 根因分析

实时语音是独立 WS 管道，转写只经 `WorkbenchRealtimeUI` 回调**透传给前端显示**，
从不写回引擎会话列表；会话持久化（`_after_turn` → `_auto_save`）只认
`self._messages`。显示与持久化两条数据通路完全脱节。

落库必须解决的三个时序问题（来自上一单修复的已知特性）：

| 问题 | 说明 | 对策 |
|---|---|---|
| 转写滞后 | 输入转写（用户说了啥）常晚于回复转写（AI 答了啥）到达 | 配对缓冲：回答先到且用户开过口 → 暂存，用户转写一到按 [问, 答] 落库 |
| 开场白 | 进入语音时 AI 先打招呼，无对应用户问句 | 用户从未开口（`speech_started` 未触发）→ AI 转写单独落库 |
| 回声伪轮 | AI 外放被麦克风重新拾取、被转写成"用户语音" | 与引擎同口径过滤：转写与 AI 刚播内容重合 → 丢弃 |

## 4. 修复方案

落库挂点选在**两条桌面语音路径共用的适配器** `WorkbenchRealtimeUI`
（全双工 `RealtimeEngine` 与 PyAudio `RealtimeTalk` 都经它回调），配对完整后
回调引擎新公开方法 `commit_voice_turn` 落入 `self._messages` 并复用
`_after_turn` 持久化（自动保存 + 标题生成与文本对话同规则）。

| 文件 | 改动说明 |
|---|---|
| `agent/ui/workbench/bridge.py` | `WorkbenchRealtimeUI` 增加转写配对缓冲（`_staged_user/_staged_ai/_speech_since_flush/_last_ai_text`）：正常顺序配对、滞后纠序、开场白单独落库、回声过滤、打断轮旧问句先落库、`flush_pending_transcripts()` 收尾兜底 |
| `agent/ui/workbench/engine.py` | 构造 `WorkbenchRealtimeUI` 传入 `owner=self`；新增 `commit_voice_turn(user, ai)`：按 [问, 答] 追加 Message 并触发 `_after_turn`；两条语音路径 `_talk_main` 的 finally 增加 flush（停止瞬间残留半轮补存） |
| `tests/ui/test_workbench_voice_persist.py` | 新增 12 用例：配对/滞后纠序/开场白/回声过滤/打断轮/flush 幂等/无 owner 透传/commit 集成（消息追加+存盘触发）/noop 守卫 |
| `jarvis-desktop/docs/guide/features-voice.md` | 全双工语音通路节补「语音对话进会话历史」说明 |

打断轮处理：用户问句未获回复（被 VAD 打断）又开新口时，旧问句先单独落库
（保留「说过这话」的事实），新轮正常配对。

## 5. 验证结果

- `python -m pytest tests/ui tests/voice -q`：**454 passed**（含新增 12 例）；
- 配对逻辑用桩 owner 纯同步验证；`commit_voice_turn` 集成用例全程 monkeypatch
  `_auto_save`，不触真实用户目录；
- 待人工复测：实时语音对话几轮 → 退出 → 恢复会话，语音问答气泡应完整回放且
  顺序正确（问在上、答在下）。

## 6. 经验总结

- **显示通路 ≠ 持久化通路**：旁路能力（实时语音、跨设备）接入 GUI 时，事件只透传
  给前端不等于进了会话历史；新通道接入时必须显式回答「数据从哪进 `_messages`」。
- **异步双通道的顺序只能靠语义信号**：输入/回复转写到达顺序不可靠，落库需以
  服务端 `speech_started`（经 `on_user_speaking(True)` 透出）为「用户开过口」的
  权威信号做配对，而不是按到达序。
- **收尾兜底**：任何「攒一批再写」的缓冲都必须在会话结束（finally）flush，
  否则停止瞬间的半轮数据必丢。
