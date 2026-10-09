# 半双工语音对话不进会话历史修复

> 修复日期：2026-10-09 ｜ 模块：半双工语音 × 会话持久化 ｜ 作者：aceFelix

## 1. 问题现象

- 桌面壳半双工语音（[VOX] 语音按钮）正常对话多轮（含工具调用 ×3），退出语音或
  重启应用后恢复会话，**语音轮的气泡全部丢失**，会话仍停留在进入语音前的状态；
- 与前序修复的实时语音落库问题同表现，但发生在另一条语音路径上。

## 2. 排查过程

1. 读工作台 `_handle_start_voice`：`voice_loop` 接收 `self._query_loop` 与
   `self._ctx`，而 `_ctx = _build_context(s, ui, self._messages)`——**消息确实
   写进了 `self._messages`**（`loop.run(user_text, ctx)` 内部 append）；
2. 但搜遍 voice_loop 全文：无任何 `_after_turn` / `_auto_save` 调用。工作台的
   `_auto_save` 只挂在 `_handle_send`（文本轮）结尾的 `_after_turn` 上；
3. 结论：语音轮只进内存列表，**磁盘会话文件从不更新**——不重启看不到问题，
   重启后恢复的会话文件里根本没有语音轮。

## 3. 根因分析

与实时语音落库缺陷（见 `realtime-talk-voice-history-persist-fix.md`）同构：
**显示通路 ≠ 持久化通路**。半双工语音复用 QueryLoop 写消息（这点比实时语音好），
但宿主（工作台引擎）没有在语音轮结束时触发与文本轮相同的持久化钩子。

## 4. 修复方案

在共享的 `voice_loop` 上加可选的每轮结束回调（与 REPL 宿主零耦合）：

| 文件 | 改动说明 |
|---|---|
| `agent/voice/voice_loop.py` | `voice_loop()` 新增关键字参数 `on_turn_end`：对话循环里每轮 `_voice_loop_round` 返回后，若本轮 `ctx.messages` 有增长（正常回复或被打断的半轮）即调用；回调异常静默。未传时行为零变化（REPL） |
| `agent/ui/workbench/engine.py` | `_voice_main` 传 `on_turn_end=self._after_turn`——语音轮与文本轮走同一套自动保存 + 标题生成 |
| `tests/voice/test_voice_loop_turn_end.py` | 新增 3 用例：消息增长触发回调 / 无增长不触发 / 回调异常静默且循环干净退出 |
| `jarvis-desktop/docs/guide/features-voice.md` | 「语音对话进会话历史」条目补半双工路径说明 |

设计取舍：回调放在 `voice_loop` 对话循环而非 `_voice_loop_round` 内部，保持
轮次函数的纯粹性；以「消息数增长」为触发条件，纯打断/空轮不产生空存盘。

## 5. 验证结果

- `python -m compileall -q agent/` 通过；
- `pytest tests/voice tests/ui -q`：**457 passed**（含新增 3 例）；
- 待人工复测：桌面壳 [VOX] 半双工语音对话几轮 → 退出语音 → 切换/恢复会话，
  语音气泡应完整保留；重启应用再恢复同样不丢。

## 6. 经验总结

- **旁路宿主必须接持久化钩子**：任何绕开 `_handle_send` 的对话入口（实时语音、
  半双工语音、跨设备通道）都要显式回答「哪一步触发 `_after_turn`」——跨设备
  通道早接了（`finish` 回调），语音两条路径此前都漏了。
- 复用同一套消息列表（ctx.messages）只保证了运行期一致性，**不等于落盘**；
  持久化是宿主职责，不是数据结构的自然属性。
