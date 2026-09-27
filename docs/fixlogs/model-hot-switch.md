# 修复：选了新模型，模型列表仍显示旧模型为「当前」

- 日期：2026-09-26
- 范围：jarvis（model_manager / query_loop / workbench(engine|api|model_switch)、tests）、
  jarvis-desktop（backendStore / dispatcher / leftStore、docs）、文档同步
- 作者：aceFelix

## 现象

桌面壳左栏「模型」面板点选 `qwen3.8-2.4t-a95b`（底部状态栏与右栏用量卡也立刻变成
`qwen3.8-2.4t-a95b / dashscope`），但再打开模型列表：**「· 当前」标记仍停在
`qwen3.8-flash`**，新点的模型只显示「· 待生效」且一直不落地——看起来像「选了没生效」。

## 排查与根因

按「谁负责改 current」逐段查：

| 环节 | 实际行为 | 结论 |
|---|---|---|
| `WorkbenchAPI.set_model` | 只调用 `save_last_model(name)` 写 `~/.jarvis/models.toml` 顶层 `last_model` | 写盘 OK，但**没通知引擎** |
| `agent/config/settings.py::load_settings` | `last_model` 只在**进程启动时**应用一次（自定义模型覆盖 provider/api_format/base_url/api_key，内置模型只覆盖 model） | 重启才生效 |
| `ChatEngine` | 运行中的 provider / QueryLoop 全程不变 | 切换未发生 |
| `WorkbenchAPI.list_models` | `current = settings.model` —— 启动快照 | **「当前」永不移动** |
| REPL `/model`（`model_manager._switch_model`） | 会重建 provider + 新建 QueryLoop | 只有 REPL 有热切换 |
| 文案 | 后端 docstring 写「重启引擎后生效」，前端提示写「下次对话生效」 | 口径不一致 |

结论：`models.select` 是「只写盘、不切换」的半成品 —— 前端把选择结果乐观显示成
「待生效」，而后端 never 落地；`current` 又取启动快照，于是用户看到的是
「底部/右栏已变新模型，列表里当前还是旧模型」这种自相矛盾的状态。

## 修复

让 `models.select` 在写盘之外**立即热切换运行中的引擎**（复刻 REPL `/model` 链路），
选完立即生效、`current` 立刻移动。

| 位置 | 改动 |
|---|---|
| `jarvis/agent/model_manager.py` | 从 `_switch_model` 抽出 `_build_switched_provider(settings, provider, name) -> (provider, desc, used_settings)`：按目标模型决定「重建 provider 还是复用」，并回报**描述该 provider 端点的 settings 快照**；`_switch_model` 改为复用（返回与文案不变） |
| `jarvis/agent/core/query_loop.py` | 新增 `switch_model(provider, model)`：就地换 `_provider` / `_model`（仿 `_try_failover`），**不重建 loop** —— 会话 token 累计、`_thinking_override`、消息上下文全保留；复位 `_failover_tried`，返回旧 provider 供调用方 `close()`。历史裁剪纯函数迁到 `agent/core/memory/prune.py`（顺带解掉 `layered_context → query_loop` 的反向依赖） |
| `jarvis/agent/ui/workbench/model_switch.py`（新） | `handle_switch_model(engine, name)`：provider 构造 → `loop.switch_model` → 旧 provider `close()` → 更新引擎状态 → 推 `model_switched` + `info`；构造失败推 `warn` 且**不**推事件 |
| `jarvis/agent/ui/workbench/engine.py` | 新增 `current_model` / `current_vendor` 只读属性（装配后取 `_model`，未装配取 `_model_override`，兜底启动配置）、`_provider_settings` 端点快照、`switch_model` 指令分发与 `_handle_switch_model` 薄包装；`_ensure_session` 末尾补落地「装配期间到达的切换」。另按单文件 ≤800 行拆出 `voice_adapter.py` / `mcp_runtime.py` |
| `jarvis/agent/ui/workbench/api.py` | `set_model`：写盘成功后 `_post({"cmd": "switch_model", "name"})`；`list_models` / `get_cost` / `get_state` 的模型与厂商改取 `engine.current_model` / `current_vendor` |
| `jarvis-desktop`（`backendStore.selectModel` / `dispatcher` / `leftStore`） | 点选先乐观记 `pendingModel`、失败撤销；`model_switched` → 清匹配标记 + 刷模型列表/用量/状态；**成功气泡改由引擎 `info` 事件唯一上屏**（去掉本地弹窗，避免双气泡） |
| `jarvis/agent/ui/workbench/assets/app.js` | 同上（工作台前端）：去掉本地「下次对话生效」气泡，`dispatchEvent` 增 `model_switched` → 刷模型列表 |

顺带修掉一个设计隐患：`_switch_model` 原以**全局 settings** 作为「是否需要重建
provider」的比较基准，而 settings 已被启动时的自定义模型覆盖 —— 在
「自定义 → 内置 → 自定义」往返时会误判为「配置一致」而复用内置 provider，
使 provider 指向错误端点。热切换改用引擎留存的 `_provider_settings`（每次装配/切换
更新），并由 `_build_switched_provider` 回报新快照。

## 验证

- `pytest tests/ui/test_workbench_engine_model_switch.py -q`：9 passed（未装配记账 /
  已装配换 provider 与 close / 装配后补落地 / 重复点选 / 构造失败 / 空名 / 指令路由）；
- `pytest -q`（全量）：1876 passed；
- jarvis-desktop：`npm run typecheck` 通过、`vitest run` 208 passed、`npm run build` 成功；
- 实机走查：重启后端 + 壳 → 左栏点选非当前模型 → 该项先「待生效」→ 一句
  「模型已切换为 X（…）」 →「· 当前」立刻移到新项、右栏用量卡模型/厂商同步 →
  继续对话即新模型回答（无需重启）。

## 经验

1. **「写盘成功」不等于「已生效」**：持久化与运行时状态是两件事。凡是「用户以为改完
   立即生效」的开关，都要问一句「运行中的实例怎么感知」——只改配置文件、等下次启动
   加载，就是这类「看起来没生效」的 bug。
2. **状态展示必须与状态来源同源**：`current` 取启动快照 → 界面永远比现实慢一步。
   凡是「当前 X」类标记，都应从运行中的实体（引擎、连接、进程）实时读取。
3. **热切换要保住会话状态**：切模型只换 provider / 模型名，不能重建承载会话状态的
   对象（token 累计、思考模式覆盖、消息上下文）；本项目里 QueryLoop 的
   `switch_model` 与 `_try_failover` 同构，二者应保持同样的字段一致性。
4. **端点比较基准要用「当前实例的快照」**：拿被覆盖过的全局配置做主键判断，会在
   「回切」场景误判「配置相同」。谁描述的 provider，就用谁的快照做基准。
5. **前端乐观提示要与事件回执二选一**：既然引擎会推 `info`「模型已切换为 X」，
   前端再本地弹一次就是双气泡；正确做法是前端只做中间态标记（待生效/撤销/清理），
   最终结果统一由事件上屏。

## 后续扩展：编辑当前模型 → force 重建（2026-09）

桌面壳左栏模型项补齐「配置管理」后，上面这条热切换的短路逻辑挡住了一条新路径：
`models.edit` 改的**就是当前运行模型**时，`handle_switch_model` 的「同名即当前 →
直接 return」会让端点 / 接口类型的改动完全不落地——用户看到「配置已更新」，
实际请求仍走旧 provider。改动如下：

| 位置 | 改动 |
|---|---|
| `jarvis/agent/ui/workbench/model_switch.py` | `handle_switch_model(engine, name, force=False)`：force 时跳过「同名即当前」短路，照常重建 provider，info 文案改「模型配置已更新」；非 force 的行为与文案不变 |
| `jarvis/agent/ui/workbench/engine.py` | 指令分发透传 `force` → `_handle_switch_model` |
| `jarvis/agent/ui/workbench/model_admin.py`（新） | 模型管理聚合（list/add/edit/remove）：`edit_model` 只在目标就是当前模型时入队 `{"cmd": "switch_model", "name", "force": True}` |
| `jarvis/agent/serve/server.py` | `_rpc_models_edit` / `_rpc_models_remove` 注册与字段校验（`api_format` / `model_type` 非空才落白名单） |
| `jarvis/agent/serve/protocol.py` | 新增 `models.edit` / `models.remove` 常量与指令表；顺带修掉 `models.add` 长期漏在 `DESKTOP_COMMANDS` 集合外（指令数 26 → 27，并补「已注册 RPC 必须已声明」的反向防漏测试） |
| `jarvis-desktop`（`LeftSidebar` / `ModelForm` / `backendStore` / `leftStore`） | 模型项双击进编辑表单、右键显删除按钮；`editModel` / `removeModel` 发指令后刷列表并提示（`hot_switched` / `was_current` 两个文案分支） |

验证：`pytest -q` 1907 passed；jarvis-desktop `npm run typecheck` 通过、`vitest run`
220 passed、`npm run build` 成功。

经验：**「同名即当前」的短路是幂等优化的常见陷阱** —— 它对「重复点选同一模型」
是正确的，对「同一模型改配置后要重建」就是错误的。凡是幂等判断，都要问一句
「被跳过的那次操作的**参数**是否可能已变」；参数会变时，幂等判断必须由调用方
显式声明（本项目用 `force` 字段），而不是让被调用方猜。
