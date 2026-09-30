# 修复复盘：桌面壳有 MCP 工具却不用（ToolSearch 钥匙工具漏注册）

- **日期**：2026-09-29
- **模块**：`agent/ui/workbench/engine.py`（workbench/serve 共用装配路径）、`agent/prompts/system.py`
- **作者**：aceFelix

## 1. 问题现象

同一台机器、同一份 `~/.jarvis`（7 个 MCP server、150 个工具已连上）：

- **终端 jarvis（REPL）** 问「明天天气如何」：先调 `ToolSearch`（query="amap maps weather"）
  加载高德天气工具 → 再调 `mcp__amap-maps__maps_weather` → 拿到结构化预报，秒回且准确。
- **桌面 jarvis（serve）** 问同样的话：**完全跳过 MCP**，直接 `Bash` 起一串
  `curl https://www.weather.com.cn/...` + `python -c` 正则抓 HTML，中间还触发
  「Bash 遇到 资源不存在，0.5 秒后第 1/1 次重试...」并长期卡在"执行中:Bash"，
  且用户无法停止。右栏明明显示"MCP 7 连 / 注册 150 个工具"。

即：桌面壳"MCP 工具连上了却不用"，表现明显比终端笨。

## 2. 排查过程

1. **确认 MCP 工具是否进了 registry**：`mcp_runtime.connect_mcp` 与
   `engine._ensure_session` 都调 `register_dynamic_tools(registry, client)`，
   工具确已注册（右栏 150 个即证）。→ 不是"没加载"。
2. **看 `deferred_loading=True` 下的工具下发逻辑**：`query_loop._build_tool_defs`
   对 `deferred=True` 的工具（MCP/harness/GUI）**初始不发给模型**，只有当工具名出现在
   `ctx.extra["discovered_tools"]` 里才带完整 schema 下发。
3. **`discovered_tools` 由谁填**：只有 `ToolSearch` 工具命中后才把工具名加入
   `discovered_tools`（见 `tools/tool_search.py`）。`ToolSearch` 自身 `deferred=False`
   是核心工具，本应始终随请求下发。
4. **终端 vs 桌面装配差异**：`main.py` 有
   `if settings.tools_deferred_loading: registry.register(ToolSearchTool(registry))`；
   而 `engine._ensure_session` 与 `mcp_runtime.prewarm` **都没有注册 `ToolSearch`**。
   → 桌面模型在 system prompt 里被告知"用 ToolSearch 加载 MCP 工具"，实际工具列表里
   根本没有 `ToolSearch`，无从加载 → 退回 `Bash + curl`。

## 3. 根因分析

- **缺陷**：workbench/serve 装配路径漏注册延迟加载机制的"钥匙工具" `ToolSearch`。
  这是 [serve-mcp-truncation-fix.md](serve-mcp-truncation-fix.md) 那次"对齐 main.py 装配"
  只补了 `_connect_mcp`（把 MCP 工具注册进 registry）、却没同步补 `ToolSearch` 注册留下的
  后半段缺口：工具"连得上、但延迟态加载不了"。
- **放大链**：模型退而 `Bash + curl` 抓网页 → 命令在 Git Bash 下偶发"资源不存在" →
  命中 `error_recovery` 的 NOT_FOUND 重试（`max_retries=1`）→ 重试再走同一失败命令 →
  重试耗尽后 `ask_user_on_fail` 在 serve 引擎循环里等待回答 → 用户感知为"卡死 + 无法停止"。
  这些下游症状的**总开关就是选错工具**：修好 ToolSearch 后模型走 MCP，不再进这条链。
- **提示词口径**：`system.py` 的「MCP 外部服务工具」段此前只说"优先用 MCP 工具"，
  未点明"MCP 是延迟加载、要先 ToolSearch 加载"，也未明令禁止用 Bash/curl 代替，
  对弱模型引导不足。

## 4. 修复方案

- `engine.py::_ensure_session`：在 prewarm 复用与同步兜底两条分支汇聚后、
  `build_system_prompt` 之前，补
  `if s.tools_deferred_loading: registry.register(ToolSearchTool(registry))`
  （带 `"ToolSearch" not in registry` 去重），与终端 `main.py` 完全对齐。放在此处
  可同时覆盖"预热成功复用 registry"和"预热失败同步重建"两种装配路径，且在提示词
  生成前完成，确保 `ToolSearch` 进入核心工具清单发给模型。
- `system.py`「MCP 外部服务工具」段：明确"MCP 工具是延迟加载的，初始列表里没有，
  必须先用 ToolSearch 搜关键词加载"，逐场景补 `ToolSearch「关键词」→ mcp__...` 的
  加载路径，并加原则"禁止用 Bash/curl/wget 抓网页数据代替对应 MCP 工具"。

## 5. 验证结果

- `tests/ui/test_workbench_engine_mcp.py` 新增 2 用例：
  `test_ensure_session_registers_tool_search_when_deferred`（开启延迟加载 →
  装配后 `"ToolSearch" in registry`）、
  `test_ensure_session_skips_tool_search_when_not_deferred`（关闭 → 不注册，与终端同口径）。
- `pytest tests/ui tests/serve -q` → 213 passed；`py_compile` 通过。
- 人工走查（预期）：桌面壳重启后问天气 → 应先出现 `ToolSearch` 工具卡、再出现
  `mcp__amap-maps__maps_weather` 结果卡与正文答案，行为与终端一致；不再走 Bash+curl，
  也就不再触发其重试卡死。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/workbench/engine.py` | `_ensure_session` 补注册 `ToolSearch`（延迟加载钥匙工具，对齐 main.py） |
| `agent/prompts/system.py` | MCP 段点明延迟加载 + ToolSearch 加载路径 + 禁止 Bash/curl 代替 |
| `tests/ui/test_workbench_engine_mcp.py` | 新增 ToolSearch 注册开/关两条回归 |
| `docs/architecture/07-UI层.md` | 引擎装配步骤补 ToolSearch 注册环节 |

## 7. 经验总结

- **延迟加载是"注册 + 钥匙"两件事，缺一不可**：把可选工具注册进 registry 只完成一半；
  `deferred=True` 意味着它们默认不发给模型，必须同时提供 `ToolSearch` 这把钥匙，
  否则模型只会在提示词里"看到名字却调不动"。对齐 `main.py` 装配要按"能力清单"逐项核，
  不能只补最显眼的那一步。
- **下游卡死常是上游选错工具的放大**：重试/退避/询问本身没错，但把"本不该走的 Bash 抓网页"
  引出来的失败链当独立 bug 去修是治标；先根治"为什么不用 MCP"，重试与停止症状自然消失。
- **终端与桌面双宿主的行为差异优先比对装配路径**：同一 settings、同一 MCP 配置下表现不同，
  几乎必然是两条装配路径（`main.repl` vs `engine._ensure_session`）某一步不一致。
