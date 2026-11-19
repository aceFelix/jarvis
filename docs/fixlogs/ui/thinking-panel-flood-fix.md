# 思考面板刷屏修复（thinking-panel-flood-fix）

> 日期：2026-08-27 ｜ 作者：aceFelix ｜ 影响范围：`agent/ui/cli.py`（RichCLI 思考面板）

## 现象

深度思考模型（如 deepseek-v4-flash、qwen3 系列）流式回复时，终端被几十个
`╭─ 💭 思考过程 ─╮` 面板刷满：内容相同、逐个递增重复出现，严重时完全看不到
最终回答。

## 根因

思考面板用 Rich `Live` 原地刷新显示，但原地擦除依赖光标移动转义序列，
只在**真实交互终端**可用。两种情况会让 `Live` 退化为"每次刷新追加一整帧"：

1. **`console.is_terminal == False`**：stdout 被重定向、部分 IDE 终端、
   mintty、管道等。每次 `Live.update()` 都把整帧面板重新输出。
2. **面板高度超过终端可视高度**：原代码设了
   `vertical_overflow="visible"`，显式关闭了 Rich 的高度保护；
   超出屏幕的内容无法被光标回移擦除，同样变成追加输出。

思考流 delta 密度高（`refresh_per_second=12`），几秒内就刷出上百帧重复面板。

## 修复

`agent/ui/cli.py` 三处改动：

1. **非交互终端降级静态模式**：`assistant_thinking` 创建 `Live` 前先检查
   `self._console.is_terminal`；非交互终端只累积缓冲，
   `_end_thinking` 时才一次性打印完整面板（全程只输出一个面板）。
2. **高度保护**：`vertical_overflow` 从 `"visible"` 改为 `"ellipsis"`，
   面板超过终端可视高度时截断为省略号，保证原地擦除始终可行。
3. **顺带修一个潜伏 bug**：`_end_assistant_line` 现在先调用
   `_end_thinking()`。原时序下"思考→直接工具调用"（思考后不出正文直接
   tool_call，deepseek 思考模式常见）不会触发思考收口，导致面板残留、
   缓冲跨轮累积。

## 测试

新增 `tests/ui/test_thinking_panel.py`（6 用例）：

| 用例 | 覆盖点 |
|---|---|
| `test_many_deltas_single_panel` | 50 个增量只输出一个面板（修复前会刷几十个） |
| `test_no_live_created` | 非交互终端不创建 Live |
| `test_end_by_assistant_text` | 正文到达时收口思考、缓冲清空 |
| `test_live_created_and_stopped` | 交互终端保持 Live 原地刷新 |
| `test_end_assistant_line_closes_thinking` | 思考→工具调用路径能收口 |
| `test_end_assistant_line_without_thinking` | 无思考时只补换行 |

回归：`tests/test_session_manager.py` + `tests/test_query_loop_run.py`
（95 用例）全部通过。

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
python -m pytest tests/ui/test_thinking_panel.py -v
```

## 验证方式

重启 jarvis，向开启思考的模型提问（如 `你对我了解多少`）：

- 交互终端（Windows Terminal / cmd）：思考面板原地刷新，正文开始后面板定格
- 重定向输出（如 `jarvis > out.txt` 或 IDE 终端）：思考结束时只出现一个面板
