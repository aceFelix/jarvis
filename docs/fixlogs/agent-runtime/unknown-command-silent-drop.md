# 修复：未注册指令被静默忽略，前端只看到「回执超时」

- 日期：2026-09-26
- 范围：jarvis（bridge/server.py、tests/serve）、文档同步
- 作者：aceFelix

## 现象

桌面壳左栏「添加模型」表单填好全部六个字段（厂商 / 模型名 / API Key / 接口类型 /
Base URL / 模型类型）点「保存」后：表单短暂无反应，约 15 秒后聊天流出现
`✗ 指令 models.add 回执超时`，模型没有添加成功；重试一次仍是同样结果。
同一时刻其他指令（会话列表、模型列表）都正常——连接明明是通的。

## 排查与根因

先按「哪一段慢」排除：

| 候选 | 实测 | 结论 |
|---|---|---|
| keyring 写 API Key 慢 | 系统 Python `WinVaultKeyring`：`set_password` 0.02s / `delete` 0.01s | 排除 |
| 引擎冷 import 慢 | `import agent.model_manager` 0.58s | 排除 |
| 前后端指令名不一致 | `contracts.ts` `ModelsAdd = 'models.add'` ↔ `protocol.py` `CMD_MODELS_ADD = "models.add"` | 排除 |
| `models.toml` 写入慢 | 用户级配置仅 4KB | 排除 |

决定性证据两条：

1. **后端从未执行写盘**：`~/.jarvis/models.toml` 里没有用户填的那个模型名段
   （只有 `last_model` 与既有的 glm/deepseek/minimax 段）——说明 `models.add`
   的 handler 根本没跑，而不是跑了一半慢。
2. **分发层对未知 type 静默忽略**：`agent/bridge/server.py::_handle_ws` 主循环
   `handler = self._ws_handlers.get(t)` 未命中时不回任何回执（既不回 `ok=true`
   也不回 `ok=false`）。而桌面壳 `ws.ts::sendCommand` 默认 **15s** 超时才
   reject「指令 X 回执超时」——超时文案只是客户端的兜底，不是后端说慢。

再对时间轴：当时在跑的 serve 进程启动时间**早于** `agent/serve/server.py`
（含 `_register_rpc(CMD_MODELS_ADD, ...)` 注册）的最后修改时间 → 前端 Vite
热更新已让新表单可用，**后端进程仍是旧代码**（`python -m agent.serve` 不热重载）。
新指令发到旧后端 → 处理器表未命中 → 静默丢弃 → 前端干等 15s。
重启后端后同一操作立即成功，闭环验证。

## 修复

把「静默忽略」改成「明确回执」：错误不再隐形，任何协议层错配都会立刻变成一条
可读的失败回执，而不是 15 秒后的一句超时。

| 位置 | 改动 |
|---|---|
| `jarvis/agent/bridge/server.py` | 新增 `_send_reply_error(ws, cmd_type, error)`：组装 `{"event":"reply","data":{"type","ok":false,"error"}}` 直发该连接（bridge 不反向依赖 serve，故不引 `protocol.build_reply`） |
| `jarvis/agent/bridge/server.py` | `_handle_ws` 分发段：未注册 type → 回 `ok=false`，文案「后端不支持指令 X（后端进程可能未加载最新代码，请重启后端后重试）」；缺 `type` 字段 → 回「指令缺少 type 字段」；两者均 `continue`，连接保持 |
| `jarvis/tests/serve/test_serve_routing.py` | 新增假连接 `_FakeConn` + `test_unregistered_command_replies_error`：直驱 `_handle_ws` 覆盖未注册 / 缺 type 两路回执 |
| `jarvis/tests/serve/test_serve_integration.py` | e2e 补一条：`models.unknown` → 立即收到 `reply` + `ok=false` |
| `jarvis/README.md`、`jarvis/docs/architecture/07-UI层.md`、`jarvis/docs/test/TEST_CHECKLIST.md` | 协议口径同步（T-329 通过标准由「不回执」改为「回 ok=false」） |

手机端兼容性：PWA 只发 `message` / `abort`（`abort` 在 reader 里直接处理，不入表），
且其事件处理器不消费 `reply`，未知事件走 `switch` 默认分支被忽略——行为不受影响。

## 验证

- `pytest tests/serve -q`：69 passed（含两条新增断言）；
- `python -m pytest -q`：1855 passed（全量回归无破坏）；
- 实机走查口径：重启桌面壳 → 前端发任意未注册指令 → 聊天流立即出现
  `✗ 后端不支持指令 X（…请重启后端后重试）`，不再等 15 秒超时。

## 经验

1. **request/response 分发链路的每个出口都必须有回执**：只要有一条分支「什么都不回」，
   客户端就只能靠超时收场，且超时文案会把根因伪装成「慢/超时」。哪怕答案是
   「我不支持这个指令」，也要回——错误可见比错误优雅更重要。
2. **前后端热更新不对称是这类 bug 的温床**：前端 Vite HMR 改完即生效，Python 子进程
   （`python -m agent.serve`）必须重启才加载新代码。新增协议指令时，先假设「前端已新、
   后端仍旧」，让旧后端回一条明确错误，而不是让新指令在旧后端上无声消失。
3. **协议类「超时」先对时间轴**：把「进程启动时间」与「协议源码 mtime」比一比，
   常能一句话定位新旧混用；再配合「数据未落盘」这类终态证据交叉确认。
4. **端到端终态证据 > 日志推断**：`models.toml` 里没有那一段，比任何 traceback 推断都硬。
