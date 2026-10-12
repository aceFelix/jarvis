# 修复复盘：终端 Jarvis 追着不存在的提醒工具空转（「# 日程提醒」段未条件化）

- **日期**：2026-10-12
- **模块**：`agent/prompts/system.py`、`agent/tools/tool_search.py`
- **作者**：aceFelix

## 1. 问题现象

先生对 Jarvis 说：「明天记得提醒我，上午十二点有一场面试啊，发我邮箱。」

- **桌面 jarvis**：邮件确实发出了，但 Jarvis 一直在「思考」——气泡里疯狂输出思维链，
  一分钟才把事办妥；语音消息已答复"办妥了"，气泡框却仍在持续输出思考，停不下来。
- **终端 jarvis（REPL / 语音）**：同样的毛病。先生提供终端日志：
  `SendEmail` 成功（L19-22）后，模型反复调用 `ToolSearch` 搜索 `ScheduleReminder`：
  - L92、L176、L190 均返回「未找到匹配…可用延迟工具（共 188 个）」；
  - 中途还遇到 `websocket closed due to fin=1 opcode=8 data=b'\x03\xefInvalid payload data'`；
  - 搜索无果后**自行绕道**用 VBS + `schtasks` 手搓定时任务，踩到 `/Z` 与 ONCE 组合的坑
    （L328 报错、L368 重试才成功）；
  - L421-467 又反复纠结"要不要实测 VBS 脚本"（引用系统提示里"完成即自查是核心原则"），
  - 最终被先生打断（L469「检测到打断，已停止回复（继续聆听）」）。

复现路径（最小）：在**终端**对 Jarvis 说「XX 时间提醒我做 XX，发我邮箱」。

## 2. 排查过程

1. **先怀疑 UI**：对比桌面前端事件分发（`jarvis-desktop/src/renderer/src/api/dispatcher.ts`
   与 `stores/chatStore.ts`），确认 thinking 增量累进同一 AI 气泡、`finishAssistant()`
   才收尾。但这一层只解释"思考显示停不下来"，**解释不了为什么会一直思考**。
2. **先生一句话点破**：「先停一下，我发现**终端 jarvis 也是这个毛病**」并给出终端日志。
   → 说明问题不在桌面 UI，而在**引擎侧行为**，桌面/终端共享同一套后端。
3. **看终端日志的关键片段**：邮件已成功，模型却执着于 `ToolSearch → ScheduleReminder`。
   → 模型"知道"有提醒能力，但工具列表里没有。
4. **核对注册点**：`agent/core/tool.py` 的 `build_default_registry()`（终端入口）**只注册
   Bash/文件/Web/Email/DevServer 等，不含任何 schedule 工具**；schedule 工具的唯一注册点
   是 `agent/serve/app.py` 的 `_register_proactive_tools`（`registry_hook` → 
   `register_schedule_tools`），仅 serve/桌面宿主走这条路。
5. **定位系统提示**：`agent/prompts/system.py` 的 `_BASE_PROMPT` 里**无条件**写死了
   「# 日程提醒」段（介绍 `ScheduleReminder`/`ListSchedule`/`CancelSchedule`）。
   → 终端下模型被告知有这三个工具，实际 registry 里没有，只能反复 `ToolSearch` 空转。

## 3. 根因分析

**根因：系统提示"广告"了当前环境根本不存在的工具。**

- 「# 日程提醒」段在 `_BASE_PROMPT` 静态常量中，不随 `registry` 变化 → 无论终端还是桌面，
  一律注入。
- 但工具是**按宿主条件注册**的：只有 serve/桌面宿主经 `registry_hook` 挂载
  `ScheduleReminder` 等；终端 `build_default_registry()` 不注册。
- 结果：终端下模型"知道有提醒工具"却在工具列表里找不到 → 触发两条坏路径：
  1. 反复 `ToolSearch` 换关键词重搜同一个不存在的工具（空转）；
  2. 搜不到后**绕道自造实现**（VBS + schtasks），引入新 bug 与长思考。
- **放大器**：`ToolSearch` 未命中时只列出"可用延迟工具名"，没有"该能力可能未注册、
  别再重试、也别绕道"的兜底提示，模型没有台阶下，只能不停试。

> 说明：本次按先生选定的范围**只修根因**——提示词条件化 + ToolSearch 兜底提示。
> 语音收尾不完整（语音路径不发 `assistant_done`、"办妥"误报）是另一条独立问题，
> 不在本次改动内，另行处理。

## 4. 修复方案

### 4.1 `agent/prompts/system.py`：日程提醒段条件化注入

- 把原 `_BASE_PROMPT` 中的「# 日程提醒」段整段抽出为模块常量 `_SCHEDULE_SECTION`
  （`_BASE_PROMPT` 内删除，避免静态无条件注入）。
- 新增 `_schedule_section(registry)`：**仅当** `"ScheduleReminder" in registry` 时返回
  `_SCHEDULE_SECTION`，否则返回空串。
- 在 `build_system_prompt()` 的段落拼装列表中，于 `_platform_section()` 之后加入
  `_schedule_section(registry)`。

这样：终端（未注册 schedule 工具）不再被告知有提醒能力；桌面/serve（注册了）保持原样。

### 4.2 `agent/tools/tool_search.py`：未命中兜底提示

在 `ToolSearch` 未命中分支的返回文案末尾追加提示：若换过多个关键词仍搜不到，说明该能力
在当前环境未注册（部分功能仅桌面/服务端可用），应改用已有工具或如实告知，**不要继续反复
搜索同一工具、也不要绕道拼凑实现**。

## 5. 验证结果

- 新增单元测试：
  - `tests/core/test_system_prompt.py::TestScheduleSectionConditional`
    - `test_absent_when_no_schedule_tool`：空 registry → 输出不含「# 日程提醒」/`ScheduleReminder`；
    - `test_present_when_schedule_tool_registered`：注册 `ScheduleReminder` → 输出含该段与三个工具名。
  - `tests/test_core_tools.py::TestToolSearch::test_no_match_hints_against_retry`：
    未命中返回含"未注册"与"不要继续反复搜索"兜底文案。
- 定向验证：`pytest tests/core/test_system_prompt.py tests/test_core_tools.py -q` → **154 passed**。
- 全量回归：`pytest -q` → **2430 passed**（含上述 3 个新增用例）。
- 未引入回归；未改动语音/桌面 UI，行为影响面仅限系统提示与 ToolSearch 文案。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/prompts/system.py` | 抽出 `_SCHEDULE_SECTION` 常量并从 `_BASE_PROMPT` 移除；新增 `_schedule_section(registry)`；`build_system_prompt` 条件注入 |
| `agent/tools/tool_search.py` | 未命中分支追加"该能力可能未注册、勿反复重试/绕道"兜底提示 |
| `tests/core/test_system_prompt.py` | 新增 `TestScheduleSectionConditional`（条件注入 2 例） |
| `tests/test_core_tools.py` | 新增 `TestToolSearch::test_no_match_hints_against_retry` |
| `docs/guide/desktop.md` | 注明提醒类工具仅桌面/serve 宿主可用，终端 CLI 不注册 |

## 7. 经验总结

- **系统提示里的"能力广告"必须与实际注册表一致**：凡是描述某个具体工具/能力的提示段落，
  都应像 `_cli_anything_section` 那样**按 registry 条件注入**，不能写死在静态 `_BASE_PROMPT`
  里。同理适用于任何"按宿主条件注册"的工具集。
- **搜不到的兜底要给台阶**：`ToolSearch` 只列"可用工具名"不够，还要明确告诉模型
  "搜不到 = 该能力可能未注册，别再重试、也别绕道"，否则模型会退化成反复空转或自造实现。
- **桌面/终端共享引擎**：同一后端 bug 会在两端同时出现。用户一句"终端也是这个毛病"把排查
  从"UI 显示问题"拉回"引擎行为问题"，是本次的关键转折——遇到"UI 停不下来"类反馈，
  先问一句"其他入口是否同样"往往能快速定位层次。
- 语音收尾不完整（`voice_ai_text` 不调 `finishAssistant`、语音路径无 `assistant_done`）
  是本次暴露但未修的独立问题，已记录待办，避免与本次根因修复混为一谈。
