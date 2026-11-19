# 设计：桌面输入区「工作模式 + 思考强度」两个选择器

- 日期：2026-09
- 范围：jarvis（thinking / providers / base / query_loop / settings / workbench(engine|api) /
  serve(protocol|server) / core_commands、tests）、jarvis-desktop（contracts / runtimeStore /
  backendStore / dispatcher / ChatArea / ThemedSelect / i18n / CSS）、文档同步
- 作者：aceFelix

## 背景

终端 jarvis 早已具备两项能力，桌面壳（Electron+React+TS）此前只能在 REPL 里用：

- **工作模式** = `/mode` 权限模式：`default`（写需确认、危险拒绝）/ `plan`（只读规划）/
  `accept_edits`（文件编辑自动放行）/ `yolo`（全自动，危险除外）；
- **思考模式** = `/think on|off` + `thinking_budget` / `reasoning_effort`，厂商差异由
  `agent/llm/thinking.py::THINKING_CONFIGS` 配置表驱动。

本次把两者做进桌面输入区，并顺带加高输入框（纯 CSS）。核心设计目标：桌面呈现**统一口径**，
后端按厂商**翻译**；热切换**复用**模型热切换已验证的「入队即返回、引擎线程内串行落地」链路。

## 统一档位与厂商映射

桌面思考选择器只做四档：**关闭 / 低 / 中 / 高**（内部另有 `on` 供终端 `/think on` 等价语义）。
`ThinkingConfig` 新增两张映射表把统一档位翻译成厂商原生参数：

- `budget_map: dict[str, int]` → 档位映射 `thinking_budget`（Qwen/DashScope：low=512 / medium=2000 / high=8000）；
- `effort_map: dict[str, str]` → 档位映射 `reasoning_effort`（DeepSeek/Moonshot：low=`low` / medium=`high` / high=`max`，
  文档无 medium 就近取档；Zhipu/zai_sdk：原生 low/medium/high）。

新增两条 OpenAI 兼容配置：**xiaomimimo**（仅 `thinking.type` 开关，无强度）、**moonshot**（Kimi，
开关 + `effort_map`）。MiniMax 无干净开关（仅 `reasoning_split`），**不接入**，选择器对其置灰。

`apply_thinking(..., effort=None)`：开启时**优先按 `effort` 档位**查两张 map 注入；`effort` 为空
回退旧 `thinking_budget` 入参与 `config.reasoning_effort` 默认（向后兼容）。纯函数
`supported_efforts(vendor_key)` 返回 `[]`（不支持）/ `["off","on"]`（仅开关）/
`["off","low","medium","high"]`（支持强度），供 `get_state.thinking_supported` 告知前端可选档位。

## 运行时链路

| 层 | 改动 |
|---|---|
| Provider + `base.py` | 各加 `_thinking_effort` 与 `set_thinking_effort(level)`（`off`→关思考，其余置 `_enable_thinking=True` 并记录档位）；`stream()` 调 `apply_thinking` 时传入档位；基类给默认空实现 |
| `query_loop.py` | 仿 `_thinking_override` 增 `_thinking_effort_override` + `set_thinking_effort/is_thinking_effort`；`switch_model`/`_try_failover` 重建 provider 后同步档位（与思考开关注入点一致，跨重建保留）；新增 `set_orchestrator(new)` 仅换编排器、保留消息/usage/思考覆盖（供权限模式热切换） |
| `settings.py` | 新增 `thinking_effort: str = "high"` + 环境变量 `JARVIS_THINKING_EFFORT` |
| `engine.py` | `_dispatch` 加 `set_mode`/`set_thinking` 分支；`_handle_set_mode` 写 settings + 重建 checker/orchestrator + `set_orchestrator`；`_handle_set_thinking` 同步 loop/settings、开关变化时 `_rebuild_system_prompt`；只读属性 `current_permission_mode`/`current_thinking_effort`；未装配时只写 settings，装配时自然生效 |
| `api.py` | `get_state` 加 `permission_mode`/`thinking_effort`/`thinking_supported`；`set_mode`/`set_thinking` 校验枚举后 `_post` 入队即返回 `{ok,...}` |
| `protocol.py` / `server.py` | `CMD_MODE_SET="mode.set"` / `CMD_THINK_SET="think.set"` 入 `DESKTOP_COMMANDS`（计数 34→36）；`_rpc_mode_set`/`_rpc_think_set` request/response 型转 api |
| `core_commands` `/think` | 增加 `low\|medium\|high` 参数分支 → `set_thinking_effort`，终端与桌面同口径 |

桌面壳侧：`contracts.ts` 加 `Cmd.ModeSet/ThinkSet`、`PermissionMode`/`ThinkingEffort` 类型、
`parseRuntimeState` 宽容解析（非法值回退 `default`/`off`，supported 过滤为合法子集）；
新增 `runtimeStore`（职责单一：permissionMode/thinkingEffort/thinkingSupported）；
`backendStore.setMode/setThinking` 发指令后按 `result.ok` 判定成功才写 store、失败弹错（与
`selectModel` 同口径）；`refreshState`（`state.get`）双写 rightStore.mcp 与 runtimeStore，
dispatcher 的 `init` 事件触发完成首屏/重连初始化；`ChatArea` footer 加 `composer-toolbar`
两个 `ThemedSelect`（思考 `thinking_supported` 为空则 `disabled`）；输入框 `min-height`≈44px、
自增高上限 120→200px。

## 关键设计点与边界

1. **RPC 回执双层**：传输层 `reply.data.ok`（指令是否被处理）vs 业务结果 `result`（api 返回的
   dict，如 `{ok:True,mode:"plan"}`）。前端业务判定看 `result.ok`，与 `voices.select` 同口径。
2. **统一档位是 best-effort 映射**：厂商只有两档时按文档就近取档；无思考控制的厂商选择器置灰、
   无副作用。
3. **权限模式热切换只换 orchestrator**，不重建 QueryLoop，保留当前会话上下文与累计用量。
4. **切换在下一轮消息生效**（引擎队列串行，正回复时于该轮结束后落地），符合终端 `/mode`/`/think`
   语义；busy/未连接时不禁用切换。
5. **行为变化提示**：qwen 默认 `thinking_effort="high"` 使默认 budget 由 2000→8000。
6. MiniMax / OpenAI / Google / SiliconFlow 等本次不新增思考接入（保持现状）。

## 验证

- jarvis：`pytest`（新增 thinking 档位、serve 路由、engine/api、query_loop 用例 +
  回归契约 test_config_table_keys / test_desktop_commands_count 同步）全绿；
- jarvis-desktop：`npm run typecheck` 通过、`npx vitest run` 全量通过（新增 runtimeStore /
  backendStore / components 工具条用例）、`npm run build` 成功。
