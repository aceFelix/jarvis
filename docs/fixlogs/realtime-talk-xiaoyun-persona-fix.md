# 桌面实时语音自称「小云」人设丢失修复复盘

> 桌面壳（jarvis-desktop）实时语音 `/talk` 全双工会话中，贾维斯自称「小云，不是贾维斯」。
> 根因：桌面全双工路径直接构造 `RealtimeEngine` 父类，丢失了 `RealtimeTalk` 子类的
> 默认贾维斯指令回退，DashScope 实时模型回落自带默认人设。作者 aceFelix。

## 问题现象

- 场景：jarvis-desktop 桌面壳左栏切「[LIV] 实时」模式，语音说「你好，贾维斯在吗？」；
  之前刚在桌面恢复过历史会话「Hello-Jarvis」。
- 表现：AI 回复「你好呀～我在呢～**不过我叫小云，不是贾维斯哦**。有什么我可以帮你的吗？」——
  人设完全变成 DashScope 实时模型的默认角色（阿里云示例同款「小云」），贾维斯人格消失。
- 复现路径：桌面壳 → 实时语音 → 随便问一句「你是谁」。稳定复现。
- 影响范围：**仅桌面全双工路径**（`talk.start` 带 `duplex: true`）；终端 `jarvis` 内 `/talk`
  与工作台 `--gui` 的 PyAudio 路径均正常（自称贾维斯）。

## 排查过程

1. **假设一：恢复会话带入旧人设**（截图里「小云」回复紧随「已恢复会话 Hello-Jarvis」）。
   检查 `~/.jarvis/sessions/Hello-jarvis.json`：只有两条干净消息
   （`Hello jarvis!` + 贾维斯式问候），**无「小云」字样** → 假设排除。
2. **全仓搜「小云」**：仅命中 `dashscope-docs/` 阿里云参考文档（示例 prompt
   「你的名字是小云」），运行时代码无该词 → 指向「模型自带默认人设」，即实时会话的
   `instructions` 为空。
3. **追 instructions 传递链**：`RealtimeEngine.__init__` 默认 `instructions=""` 并原样进
   session config（`realtime_engine.py` L104/L119/L290）；默认贾维斯话术
   （`_INSTRUCTIONS_BUILTIN/_ALL`）定义在 `realtime_talk.py`，仅 `RealtimeTalk.__init__`
   在 instructions 为空时回退。
4. **对比两条启动路径**（`agent/ui/workbench/engine.py`）：
   - PyAudio 路径（L916）构造 `RealtimeTalk` → 有回退 → 人设正常；
   - **桌面全双工路径 `_handle_start_talk_duplex`（L997）直接构造父类 `RealtimeEngine`，
     从不传 `instructions`** → 空 → 模型回落「小云」。根因确认。

## 根因分析

桌面全双工桥接（2026-09-28 接线）为绕开 PyAudio（音频走渲染进程采集/播放）而**跳过
`RealtimeTalk` 子类直接实例化父类 `RealtimeEngine`**；而「默认指令按工具模式回退」的
逻辑实现在子类构造函数里，父类没有。继承链上的默认值没有随构造方式跟进，属典型的
**子类默认值下沉缺失**——两处构造路径行为分叉，只有常被测的终端路径是对的。

## 修复方案

把默认指令回退提为模块级函数，两条路径共用：

- `agent/voice/realtime_talk.py`：新增 `default_instructions(tools_mode)` 模块函数
  （builtin → `_INSTRUCTIONS_BUILTIN`，all → `_INSTRUCTIONS_ALL`）；
  `RealtimeTalk.__init__` 改为调用它（行为不变）。
- `agent/ui/workbench/engine.py` `_handle_start_talk_duplex`：构造 `RealtimeEngine` 时
  传 `instructions=default_instructions(getattr(s, "realtime_tools_mode", "builtin"))`，
  与该路径已有的工具装配（BUILTIN_TOOLS / build_all_tools）同模式同口径。

同轮顺带修复（同一次人工测试发现、桌面端同源问题的 pywebview 侧修复，详见
jarvis-desktop `docs/fixlogs/realtime-talk-user-bubble-order-fix.md`）：工作台前端
`agent/ui/workbench/assets/app.js` 的 `user_transcript` 事件由纯追加改为「本轮流式
AI 气泡已建时插到它前面」，修复用户转写气泡排在 AI 回复之后的时序颠倒。

无临时方案/绕道，属根治。

## 验证结果

- `python -m compileall` 两个改动文件通过；
- `pytest tests/voice/test_realtime_response_lifecycle.py tests/ui/test_workbench_engine_api.py`
  → **72 passed**；
- 人工验证（待用户复测）：桌面壳实时语音问「你是谁」，应自称贾维斯。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/voice/realtime_talk.py` | 新增 `default_instructions()`；`RealtimeTalk.__init__` 改用它 |
| `agent/ui/workbench/engine.py` | `_handle_start_talk_duplex` 构造 `RealtimeEngine` 时传贾维斯指令 |
| `agent/ui/workbench/assets/app.js` | `user_transcript` 插到流式 AI 气泡前（同轮顺带修复用户气泡顺序） |

## 经验总结

- **同一引擎多条构造路径时，默认值必须下沉到共享层**（模块函数/父类构造），否则
  新路径绕过子类即静默丢默认值——本 bug 从接线到发现存活约 12 天，因为测试只覆盖终端路径。
- 「AI 自称怪名字」类问题排查口诀：先查会话历史（人设污染）→ 再全仓搜名字（prompt 泄漏）
  → 最后追 system prompt 构建链（instructions 丢失）。
- 桌面全双工与终端 `/talk` 的行为一致性目前靠「同口径」注释约定，后续如再加
  `RealtimeTalk` 级配置（voice/turbo 等），注意同步检查 `_handle_start_talk_duplex`。
