# 修复复盘：桌面壳 MCP 工具缺失 + anthropic 协议截断轮静默结束

- **日期**：2026-09-20
- **模块**：`agent/ui/workbench/engine.py`（workbench/serve 共用装配路径）、`agent/core/query_loop.py`
- **作者**：aceFelix

## 1. 问题现象

人工测试（桌面壳 jarvis-desktop 文本模式）发现两个叠加症状：

1. 问「明天天气怎么样？」，jarvis 回复一大段灰斜体"内心独白"：念叨要用
   `mcp__amap-maps__*` 高德天气工具、又说"延迟加载列表里没有 mcp__amap-maps__*、
   可用延迟工具只有 MouseClick 等"，然后转去计划 Bash curl wttr.in——
   **全程没有任何真实工具调用**，回合以独白戛然而止（停在"让我改用 ./w.json。"），
   没有正文答案。同一问题在 REPL 终端不会出现（REPL 有 MCP 工具）。
2. 会话存档里查不到该轮（回合未正常走完存档路径的表象之一），
   而相邻的「jarvis现在几点了」会话存档中 Bash tool_use + exit=0 记录完整——
   证明 serve 模式核心工具本身可用，缺的是 MCP 工具与"被截断的收尾"。

## 2. 排查过程

1. **假设纯聊天零工具检测误判**（"明天天气怎么样？"短消息 → 0 工具）：
   读 `_is_chat_only`，"天气/明天"均在动作关键词表 → 返回 False，排除。
2. **假设 serve 不注册核心工具**：会话存档证明 Bash tool_use 成功执行，排除。
3. **比对 REPL 与 workbench/serve 装配路径**：`main.repl()` 在
   `build_default_registry()` 后有完整 MCP 接入段（MCPClient → connect_all →
   `register_dynamic_tools(registry, mcp_client)`）；而
   `ChatEngine._ensure_session()` 只有 registry_hook + harness 后台线程，
   **MCP 连接步骤整体缺失**——engine.py 头部注释还写着"MCP 保持接入"，
   注释与代码不符。模型独白中"列表里没有 mcp__amap-maps__*"与此完全吻合。
4. **独白为何戛然而止**：独白为 thinking 块渲染，回合以"仅 thinking、无正文、
   无 tool_use"结束 = 模型输出在即将发 tool_use 前被截断。查截断恢复：
   QueryLoop 只认 `stop_reason == "length"`（openai 口径），而
   `anthropic_provider` 透传原生 stop_reason——anthropic 协议截断值是
   `"max_tokens"`（推理模型推理预算耗尽同样返回它）→ 恢复分支永不触发 →
   截断轮被静默当作最终答案。用户配置 api_format=anthropic（deepseek-v4-flash
   经 dashscope 兼容端），两缺陷叠加复现截图现象。

## 3. 根因分析

- **缺陷一（MCP 缺失）**：workbench/serve 装配路径漏写 MCP 接入步骤（历史迁移
  遗漏），MCP 工具（amap 天气/天眼查/航班等）在桌面壳永远不注册；系统提示词
  又要求"天气必须优先用 mcp__amap-maps__*"，模型找不到工具只能退而计划 Bash。
- **缺陷二（截断静默）**：截断恢复的 reason 判定只覆盖 openai 口径 `"length"`，
  anthropic 原生 `"max_tokens"` 漏判；推理模型推理预算耗尽即返回该值，
  回合以残缺 thinking 静默收尾，用户看到"念叨一半没了"。

## 4. 修复方案

- `engine.py`：
  - 新增 `_connect_mcp(registry)`：对齐 `main.repl()` 的 MCP 接入——
    MCPClient → `load_mcp_config()` → `connect_all`（内部并发 + 每 server 超时，
    不会拖死装配）→ `register_dynamic_tools(registry, client)`；
    client 引用保留在 `self._mcp_client` 防 GC 断连；
    全部失败/SDK 缺失/异常均静默降级为 info 事件，不阻断文本对话。
  - `_ensure_session()` 在 registry_hook 之后、系统提示词生成之前
    `await self._connect_mcp(registry)`（`enable_mcp` 开关尊重 settings），
    保证 MCP 工具进入系统提示词的延迟工具摘要。
- `query_loop.py`：截断恢复判定改为
  `stop_event.reason in ("length", "max_tokens")`，双协议口径统一触发自动续写。

## 5. 验证结果

- 新增 `tests/ui/test_workbench_engine_mcp.py`（4 用例）：连接成功注册 + client
  保留 / 全失败不注册 / SDK 缺失与无配置静默 / 异常降级 info。
- `tests/test_query_loop_run.py` 新增
  `test_stop_reason_max_tokens_auto_continue`：reason="max_tokens" 触发续写。
-  targeted 运行 56 passed；jarvis 全量 `pytest tests -q` 全绿（见提交记录）。
- 人工走查清单：桌面壳重启后问天气 → 状态栏应出现"正在连接 N 个 MCP server..."
  与"MCP: x/y server 已连接，注册 N 个工具"；再问天气应真实调用
  `mcp__amap-maps__maps_weather`（或降级 Bash 时有完整工具气泡与正文答案）。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/workbench/engine.py` | 新增 `_connect_mcp`；`_ensure_session` 补 MCP 接入；`__init__` 增 `_mcp_client` 引用 |
| `agent/core/query_loop.py` | 截断恢复兼容 anthropic 原生 `max_tokens` |
| `tests/ui/test_workbench_engine_mcp.py` | 新增 MCP 接入四路径回归 |
| `tests/test_query_loop_run.py` | 新增 max_tokens 续写回归 |
| `docs/architecture/07-UI层.md` | 引擎装配顺序与截断口径同步 |
| `README.md` | venv 安装后需激活环境才能找到 `jarvis` 命令的提示 |

## 7. 经验总结

- **装配路径对齐要逐段比对**：workbench/serve 引擎注释声称"对齐 repl 装配"，
  但 MCP 段在迁移时遗漏且注释未同步——"注释与代码不符"本身就是 bug 信号，
  迁移类改动应把源路径步骤清单化逐项核对。
- **stop_reason 是协议方言**：跨协议复用恢复/重试逻辑时，必须在 provider 边界
  或判定处统一口径（openai `length` vs anthropic `max_tokens`），
  新增 provider 时同步检查。
- **推理模型的截断不一定是 max_tokens 设小了**：推理预算耗尽同样返回截断
  stop_reason，表现为"thinking 说到一半停住、无正文无工具"，排查时优先看
  存档/事件流里回合的收尾结构而非只看模型配置。
