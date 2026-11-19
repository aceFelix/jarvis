# 修复：serve 启动期 init 事件广播丢失，桌面壳设置面板永久离线

- 日期：2026-09-25
- 范围：jarvis（bridge/server.py、serve/server.py、serve/app.py、tests/serve）、文档同步
- 作者：aceFelix

## 现象

桌面壳设置面板中全部七个后端联动键（主动播报 TTS / 播报音量 / 播报语速 /
每日简报开关与时间 / 截止日期开关与检查时间）均显示「未连接后端，暂不可改」，
任何一项都改不了；但同一时刻左栏会话列表有数据、状态栏「就绪」、对话正常——
连接明明是通的。右栏 models/voices/schedule/cost/state 等首屏数据同源缺失。

## 排查与根因

设置面板的离线态判定：`settingsStore.backendSettings[key] === null`（单键 null =
未拉取/未连接）。回填唯一入口是 dispatcher 的 `case 'init'` 七路齐刷中的
`refreshSettings()`（settings.get → 全量回填）。即：**init 事件没到渲染层**。

事件时序：

1. `agent/serve/app.py::_serve_main` 装配完成后
   `event_queue.put_nowait({"type": "init", ...})`——**启动期一次性投递**；
2. 事件泵 `_pump_loop` 50ms 内消费并调 `BridgeServer.broadcast`；
3. `broadcast` 首行 `if not self._clients or self._loop is None: return`——
   此刻握手 JSON 还没打印、Electron 还没拿到端口/token、WS 客户端数为 0，
   **init 被静默丢弃**；
4. 桌面壳 WS 连上后永远等不到 init（协议里它只发一次），七路首刷全不触发；
   `backendSettings` 保持全 null → 设置面板整组离线。

会话列表之所以有数据，是靠后续业务事件兜底刷的（`session_new` /
`session_ready` → `refreshSessions`），掩盖了 init 丢失；设置回填没有这类
兜底事件，于是成为最显眼的受害者。protocol.py 事件表写的契约本是
「init：连接建立后首推」，实现却做成了启动期一播，契约与实现分裂。
断线重连场景同病：重连后也没有任何机制重发 init，首屏数据停在断线前。

## 修复

语义归位：**首帧按连接推送**，与协议契约对齐。

| 位置 | 改动 |
|---|---|
| `jarvis/agent/bridge/server.py` | `_handle_ws` 认证通过、`_clients.add(ws)` 后调用新钩子 `await self._on_client_connected(ws)`；基类默认空实现（手机协同模式行为不变） |
| `jarvis/agent/serve/server.py` | `DesktopBridgeServer` 覆写钩子：向该连接直发 `{"event": "init", "data": api.get_state()}` 首帧（payload 同 state.get） |
| `jarvis/agent/serve/app.py` | 删除启动期 `put_nowait(init)`（无客户端时必丢的死代码），注释说明首帧归属 |
| `jarvis/tests/serve/test_serve_routing.py` | 新增 `test_init_pushed_per_client_connect`：钩子直发 init 信封、payload 同 `get_state` |
| `jarvis/tests/serve/test_serve_integration.py` | e2e 断言连接后**首帧**即 init（再走指令/回执链路），把契约钉进测试 |
| `jarvis/docs/architecture/07-UI层.md`、`jarvis-desktop/docs/architecture.md` | 进程契约/装配顺序口径改为「init 按连接首推」 |

顺带拆除一枚既有时间炸弹（与本 bug 无关但红 suite）：`tests/serve/test_hub.py`
的 `TestBriefingCatchup` 用真实时钟 `now - 1h` 推算简报时间，跨午夜时 HH:MM 被
解释成当天未来时间 → 判定不补播而闪挂；改为冻结 hub 模块时钟（`_FrozenDatetime`
注入 `FIXED_NOW` 正午），全类用例按固定时钟推算。

## 验证

- `pytest tests/serve -q`：66 passed（含新增首帧用例与集成首帧断言；
  修复前 TestBriefingCatchup 跨午夜 2 failed）；
- 实机走查口径：重启桌面壳 → 设置面板七键立即显真实值可改；kill 后端等
  主进程拉起新后端 → 重连后 init 重发、面板与右栏首屏数据自动回填。

## 经验

1. **「广播给所有人」不等于「送达给未来的人」**：首帧/快照类事件若只在启动期
   广播一次，而客户端必然晚于启动连接，就等于没发。首帧必须挂在连接建立钩子上
   （per-connection push），顺带覆盖重连。
2. **broadcast 的「无客户端静默丢弃」是隐性契约**：调用方以为投进队列就送达，
   实际无客户端时直接 return。任何依赖广播送达的关键帧都要问一句「此刻有人听吗」。
3. **协议文档写的契约（连接后首推）与实现（启动期一播）可以分裂很久**：因为别的
   事件（session_new 等）兜底掩盖了症状。契约语句要有人对着实现核，测试要钉契约
   （集成测试断言「首帧即 init」）。
4. 设置面板「整组离线」是**数据未回填**的信号，不等于连接断开——排查时先看
   回填链路的事件源，再看连接状态。
