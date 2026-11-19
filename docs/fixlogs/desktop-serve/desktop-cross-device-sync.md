# 设计：桌面端「跨设备协同」接入（手机 PWA / 微信 ClawBot）

- 日期：2026-10
- 范围：jarvis（engine / remote_bridge / bridge(server) / wechat(server) / workbench(api) /
  serve(protocol|server)、tests）、jarvis-desktop（contracts / remoteStore / chatStore /
  backendStore / dispatcher / RemoteConnectMenu / QrcodeCard / ChatArea / glyphs / i18n /
  remote.css / main.tsx、tests）、文档同步
- 作者：aceFelix

## 背景

终端 jarvis 早已能 `/connect-phone`（手机 PWA）与 `/connect-wechat`（微信 ClawBot）远程对话，
共享同一会话历史。桌面壳（Electron+React+TS）此前没有这两条通道的入口。本次把它们接入桌面：
输入栏原 `[LIV]` 实时语音按钮（左栏已有 `[LIV]`，此处冗余）替换为**跨设备协同下拉按钮**，点选
「手机 / 微信」发起连接，**二维码内联显示在中间聊天区**。

三条硬约束（用户明确）：① 手机 + 微信两条通道都做，按钮下拉二选一；② 三通道共享同一会话，
**都能发消息但绝不同时发**（照终端 `_query_lock` 串行化范式）；③ 二维码直接内联聊天区，不做浮层，
微信比手机多一步数字配对码、复用内联输入。

## 核心设计：引擎唯一共享锁

终端 `BridgeServer` / `WeChatBridge` 各自 `threading.Lock()` 自锁，只在本桥接内串行。桌面新增
「三通道互斥」约束，把锁**提升为引擎持有的唯一 query 锁**（`ChatEngine._query_lock`），三通道都
调度到**引擎自己的 asyncio loop**（`engine._loop`）执行 `QueryLoop.run` 并抢同一把锁：

| 通道 | 抢锁处 | 执行 loop |
|------|--------|-----------|
| 桌面文本 | `engine._handle_send`：`run_in_executor(None, lock.acquire)` → `await run` → finally release | `engine._loop` |
| 手机 PWA | `BridgeServer.run_query`（同构） | 注入的 `main_loop` |
| 微信 ClawBot | `WeChatBridge._exec_query`（同构） | 注入的 `main_loop` |

`start_bridge_in_thread` / `start_wechat_in_thread` 及两个类的 `__init__` 新增可选入参
`main_loop`、`query_lock`：桌面 serve 传引擎的 loop 与唯一锁；不传（终端）则各自默认，行为零变化。

## 运行时链路

| 层 | 改动 |
|---|---|
| `engine.py` | `__init__` 加 `self._query_lock` + `self._wechat_pairing_q`；`_handle_send` 包锁（用 `asyncio.get_running_loop().run_in_executor` 抢锁，不依赖 `start()` 才赋值的 `self._loop`，便于单测直接 await）；`_dispatch` 加 `connect_phone/disconnect_phone/connect_wechat/disconnect_wechat/wechat_pairing` 五分支（委托 remote_bridge，配对码 `put` 进队列） |
| `remote_bridge.py`（新建，engine 已超限故按职责拆出） | `connect_phone` / `disconnect_phone` / `connect_wechat` / `disconnect_wechat` 在引擎 loop 上装配桥接；`_wechat_login_worker`（独立线程 + 独立 loop）驱动 login，`_StatusUI` 把微信 info/warn/error 转引擎事件 |
| `bridge/server.py` / `wechat/server.py` | `__init__` / `start_*_in_thread` 加 `query_lock` 注入；`run_query` / `_exec_query` 用注入锁（默认自锁不变） |
| `api.py` | `connect_phone/disconnect_phone/connect_wechat/disconnect_wechat` 入队即返回；`phone_status`/`wechat_status` 读桥接单例；`wechat_pairing(code)` 入队回喂 |
| `protocol.py` / `serve/server.py` | `CMD_PHONE_*` / `CMD_WECHAT_*` 共 7 条入 `DESKTOP_COMMANDS`（计数 36→43）；`EVT_QRCODE` / `EVT_REMOTE_STATE` 事件；`_register_desktop_handlers` 注册 7 条 RPC + `_rpc_wechat_pairing` |

**微信多一步且不占引擎 loop**：终端 `login` 的 `verify_callback` 是阻塞 `input()`。桌面 `connect_wechat`
只装配桥接、把 login 丢独立线程 `_wechat_login_worker`；`qrcode_callback` 把登录二维码经 `qrcode` 事件
回推，`verify_callback` 阻塞在 `engine._wechat_pairing_q`（threading 队列，60s 超时兜底）；桌面二维码卡片
内联配对码输入框，提交走 `wechat.pairing` RPC → 引擎把数字码 `put` 进队列解锁登录。

桌面壳侧：`contracts.ts` 加 7 条 `Cmd` + `RemoteChannel`/`QrcodePayload`/`RemoteStatePayload`/
`PhoneStatusResult`/`WechatStatusResult` 类型；`chatStore` 新增 `qrcode` 消息类型 + `addQrcode`（同通道
未连接卡片就地刷新 url）/`setQrcodeConnected`（含 `findLast` 兼容 es2020 反查辅助）；新建 `remoteStore`
（手机/微信连接态）；`backendStore` 加 6 个动作 + `refreshRemote`（phone.status+wechat.status 回填）；
`dispatcher` 加 `qrcode`/`remote_state` 事件路由、`init` 调 `refreshRemote`；新建 `RemoteConnectMenu`
（下拉按钮，读 remoteStore 决定连接/断开与徽标）、`QrcodeCard`（`qrcode` npm 画 url，微信卡片内联配对码
输入）；`ChatArea` 移除 `[LIV]` 按钮改挂下拉、消息流加 `kind==='qrcode'` 分支；`glyphs` 加 `link` 括号牌；
`i18n` 加文案（zh/en）；新建 `remote.css` 并在 `main.tsx` 皮肤前引入；`qrcode` 入 dependencies。

## 关键设计点与边界

1. **连接是异步事件驱动**：RPC「入队即返回」，二维码/连接态经 `qrcode`/`remote_state` 事件回聊天区，
   不阻塞、不轮询回执。
2. **桌面重开回填连接态但不回放二维码**：`init`→`refreshRemote` 只同步按钮态；二维码每次连接现生成。
3. **共享锁串行回归**：单测 `test_run_query_serializes_two_queries_on_shared_lock` 用活跃 query 峰值
   ==1 证明「两端都能发、绝不同时发」；`test_bridge_server_uses_injected_shared_lock_or_self` 证明注入
   即用 / 终端默认自锁。
4. **微信 login 绝不上引擎 loop**：占 loop 会因 `verify_callback` 阻塞而死锁引擎指令循环，故独立线程 +
   独立 loop + threading 队列解耦。
5. **语音入口不丢**：左栏 `[LIV]` 保留全双工实时语音，仅移除输入栏冗余按钮。

## 验证

- jarvis：`uv run pytest tests/ui tests/serve`（新增 `test_workbench_remote_bridge.py`：手机/微信
  装配用共享锁 + 引擎 loop、qrcode/remote_state 事件、login worker、配对码路由、API 入队、串行化
  回归，及客户端接入/离开回调触发用例）+ 契约 `test_desktop_commands_count`（43）全绿；共 248 passed。
- jarvis-desktop：`npm run typecheck` 通过；`npx vitest run` 全量通过（新增 `remote.test.tsx` 9 例：下拉
  按钮连接/断开路由与徽标、二维码卡片已连接/配对码提交/url 回退；`dispatcher.test.ts` 新增 5 例：
  qrcode/remote_state 上屏与连接态联动）；`npm run build` 成功。

## 交付后修正（2026-10）

首轮联调发现两处问题，已修复：

1. **连接态语义修正（未扫码即显「已连接」）**：初版 `connect_phone` 在桥接 HTTP 一启动就同时
   推 `qrcode` 与 `remote_state connected=True`，而 `QrcodeCard` 在 `connected` 时会收起二维码改显
   「✓ 已连接」——两个事件同批到达，二维码一闪即被替换、用户尚未扫码即「已连接」。修正为
   **`connected` 以手机 WS 客户端真正接入为准**（桥接就绪 ≠ 手机已连）：`BridgeServer` 新增
   `on_client_connected` / `on_client_disconnected` 回调（首个客户端接入 / 最后一个离开时触发，
   断开检测集中在 `_remove_client`，覆盖正常 finally 与 broadcast 发送失败两处），`connect_phone`
   改为挂这两个回调推 `remote_state`、不再启动即抢先推真；仅当复用已运行桥接且已有客户端在线时
   补推一次。终端不设置回调（默认 None），行为零变化。
2. **弹层按钮宽度 + 主题联动**：下拉弹层同在 `.composer-side` 内，被 `composer.css` 的
   `.composer-side .action-btn{width:100%}` 误命中，「连接」按钮撑满整行、把说明文字挤成窄列——
   `remote.css` 的 `.remote-menu-row .action-btn` 补 `width:auto`（晚于 composer.css 引入，同特异度
   覆盖生效）解除拉伸，label 加 `flex:1;min-width:0` 正常换行。配色原硬编码深蓝，在复古绿 / 金属银
   皮肤下露出深蓝底——改为引用 `--qr-*` 变量（`remote.css` `:root` 给电光蓝默认值），retro / light
   皮肤各自 `:root[data-theme=...]` 覆盖同名变量即随主题联动（二维码图 `.qr-img` 固定白底保证扫码对比度）。

## 第三轮微调（2026-10）

3. **去掉微信配对码内联输入**：用户反馈“本质直接通过二维码就能连上微信 ClawBot”。核实
   iLink 登录流程：`wait_login_confirmation` 正常扫码+确认即直接返回 `bot_token`，**仅当服务端
   额外返回 `need_verifycode` 时才会调 `verify_callback` 要配对码**（次要兜底）。因此
   `QrcodeCard` 移除微信卡片的配对码输入行（手机 / 微信一致，扫上即连），不再引用
   `submitWechatPairing`。后端 `wechat.pairing` RPC / `engine._wechat_pairing_q` / `_wechat_login_worker`
   的 `verify_callback` 作为休眠兜底**保留不动**（终端 `input()` 仍用同一机制，且改动会
   波及 `DESKTOP_COMMANDS` 契约计数），风险最低。同步删除 `.qr-pairing*` 死 CSS。
4. **二维码配色随主题联动**：上轮 `.qr-img` 固定白底在复古绿下仍显突兀。新增 `--qr-code-dark` /
   `--qr-code-light` 皮肤变量（电光蓝默认深navy/浅蓝白；retro 深绿/浅绿；light 黑/白），
   `QrcodeCard` 用 `getComputedStyle` 读两值传 `QRCode.toDataURL({color})`，并用 `MutationObserver`
   监听 `<html data-theme>` 变化重绘；`.qr-img` 背景改用 `var(--qr-code-light)` 与图内背景融合。
   （均保证深模块 + 浅底高对比，仍可正常扫描。）
5. **下拉框去圆角**：`.remote-menu-pop` 及其内部 `.action-btn` 的 `border-radius` 改为 `0`，
   与复古 CRT 硬边质感一致（`.qr-img` 圆角也一并取 0）。

## 第四轮：假连接修复（2026-10）

用户反馈“还是没扫就显示已连接、二维码不刷新”。排查确认两层原因：

6. **旧构建未重启**（主因）：截图里微信卡片仍带“配对码”输入框、二维码仍黑白无
   主题色——而这两者已在第三轮删除/上色，证明当时跑的是改动前的旧构建（旧后端仍会
   在桥接启动时就抢跑 `connected=True`）。需重启后端（`uv run jarvis serve`）与桌面
   dev（`npm run dev`）后修复才生效。
7. **微信 `already_connected` 假连接**（新发现的真 bug）：`login()` 的 `already_connected`
   （`binded_redirect`）分支不写 `bot_token`，但 `_wechat_login_worker` 原先只要 `login()` 返回
   True 就推 `connected=True` 并起消息轮询——而 `bridge.connected` 恰为 `bool(bot_token)`，即此
   时“显示已连接”但无可用凭证、根本收不到消息。修复：`_wechat_login_worker` 改为 `if ok and
   bridge.connected` 才报已连接，`already_connected` 无凭证时只 warn 提示重新扫码、保留二维码。
   新增回归用例 `test_wechat_login_worker_already_connected_without_token_not_reported`。

## 第五轮：微信气泡归属与来源标记（2026-10）

用户反馈两个微信对话展示问题：

8. **第二次提问续写同一气泡**：`assistant_done` 事件以前仅由引擎本地输入路径
   （`engine._handle_send` 的 finally）发出；微信 query 走 `WeChatBridge._exec_query → _query_loop.run`，
   不经此路径 → 桌面 AI 气泡永不定稿（`streaming` 一直为真），下一条微信消息的回复因此
   续写进上一气泡。修复：新增 `WeChatUI.end_turn()`，在 `_exec_query` 跑完 query 的 finally 里调
   `desktop_ui.assistant_done()` 收尾本轮（终端 RichCLI 无此方法则 getattr 判空 no-op）。
9. **微信/手机消息居中显示**：入站消息早期走 `desktop_ui.info("[微信] …")`，被桌面当居中系统
   气泡。新增 `WorkbenchUI.remote_user_message(channel, text)` 推 `remote_user_message` 事件，
   `WeChatUI` / `BridgeUI`（手机）均优先调此方法（终端回退 info 前缀）；前端按普通用户气泡
   （右对齐）渲染，仅标签按 `source` 显「微信 / 手机」；`remote_user_message` 处理还先 `finishAssistant`
   兼做防御，即使漏收 `assistant_done` 也不会续写旧气泡。
   新增后端用例（`test_wechat_ui_*` / `test_wechat_exec_query_ends_turn_after_run` /
   `test_workbench_ui_remote_user_message`）与前端用例（dispatcher / chatStore / ChatArea 来源标记）。

## 第六轮：远端对话落盘 + 重连二维码定位（2026-10）

用户反馈三个问题：

10. **手机 / 微信对话是否存到电脑**：手机 / 微信与桌面共享同一 `engine._messages`，但落盘
    （`_after_turn → _auto_save`）此前只在桌面本地输入路径触发，纯远端对话要到下一次桌面操
    作才存盘。修复：`start_bridge_in_thread` / `start_wechat_in_thread` 与 `WeChatBridge.__init__` 新增
    可选入参 `on_turn_end`（桌面注入 `engine._after_turn`），`BridgeServer.run_query` 与
    `WeChatBridge._exec_query` 在 query 结束（释放锁后、引擎 loop 上）各调一次，使每一轮远端
    对话即时增量存盘 + 参与标题生成；终端不注入（`None`）行为不变。新增用例
    `test_wechat_exec_query_calls_on_turn_end` / `test_phone_run_query_calls_on_turn_end`，并在
    `connect_phone` / `connect_wechat` 用例断言注入的 `on_turn_end`。
11. **重连二维码不刷新到当前界面、要往上翻找**：`addQrcode` 对同通道未连接卡片一律就地
    更新 url，断开时旧卡被 `setQrcodeConnected(false)` 还原为未连接态，重连的新二维码因此刷新
    到历史旧位置，不在底部（自动滚底也看不到）。修复：`qrcode` 事件加 `fresh` 字段，一次新连接
    首帧 `fresh:true`（`connect_phone` 直推，微信 worker 首帧推、后续过期重生成帧推 `false`），
    前端 `addQrcode(channel, url, fresh)` 对 `fresh` 先清理该通道旧未连接卡片、再在列表底部新建
    一张（配合既有自动滚底，当前视口即见）；`fresh:false` 仍为同一连接内过期刷新的就地更新。
    已连接历史卡片保留。新增用例 `test_wechat_login_worker_qrcode_cb_fresh_first_then_stale` 与
    前端 dispatcher `fresh` 两条（底部新建 / 保留已连接卡）。

验证：后端 `tests/ui`+`tests/serve` 257 passed；`npm run typecheck` 0；`npx vitest run` 322 passed；
`npm run build` 0。⚠ 需重启后端 `uv run jarvis serve` + 重跑桌面 `npm run dev` 才生效。

## 第七轮：任意来源的轮次都能停止（发送/停止双态按钮）（2026-10）

用户反馈（之前就提过、仍未生效）：jarvis 正在工作中时，发送按钮应变为“停止”图标，点击
可让 jarvis 停止回复或执行；但截图里 jarvis 已在跑工具（工具调用 ×10/×3），按钮仍停在“发送”。

12. **根因**：`busy`（驱动发送/停止双态）此前**只由桌面本地 `sendMessage` 置位**，而手机 / 微信 /
    主动任务发起的轮次不走 `sendMessage`（走 `BridgeServer.run_query` / `WeChatBridge._exec_query`），
    桌面虽能收到广播的 `tool_use`/`assistant_text` 事件、却从不置 `busy`，按钮因此不切。且
    `reply.abort → abort_current_reply` 只取消 `_handle_send` 登记的 `_send_task`，对远端轮次返回
    False（点停止无效）。另手机 `BridgeUI.finish()` 不向桌面发 `assistant_done`，若仅置 busy 会卡死。

    **修复三处**：
    - 前端 `dispatcher.ts`：`assistant_text` / `assistant_thinking` / `tool_use` 任一活动事件都
      `chat.setBusy(true)`（`assistant_done` 已 `setBusy(false)`），使按钮对任意来源都切为“停止”；
      历史回放走 `session_loaded → replayHistory`（不经这些事件），不会误置忙。
    - 后端引擎新增 `ChatEngine._remote_query_begin()`（`self._send_task = asyncio.current_task()`）；
      `start_bridge_in_thread` / `start_wechat_in_thread` 与 `WeChatBridge.__init__` 新增 `on_query_begin`
      入参（桌面注入该方法），`BridgeServer.run_query` / `WeChatBridge._exec_query` 在拿到 query 锁、
      即将跑 `query_loop.run` 前各调一次，使 `abort_current_reply` 能取消任意来源在跑轮次（三通道
      由 query 锁串行，同一时刻只有一个在跑，登记不互覆）。
    - 手机 `BridgeUI.finish()` 补调 `desktop_ui.assistant_done()`（与微信 `WeChatUI.end_turn` 对称），
      避免手机轮次桌面收不到 done、busy 永久卡住。

    终端不注入（`None`）/无 `assistant_done` 方法均 no-op，行为不变。新增用例：前端 dispatcher
    “远端轮次活动事件自动置 busy”/“tool_use/assistant_thinking 也置 busy”；后端
    `test_phone_run_query_calls_on_query_begin` / `test_wechat_exec_query_calls_on_query_begin` /
    `test_bridge_ui_finish_emits_desktop_assistant_done` / `test_engine_remote_query_begin_registers_current_task`，
    并在 `connect_phone` / `connect_wechat` 断言注入的 `on_query_begin`。

验证：后端 `tests/ui`+`tests/serve` 261 passed（+4）；`npm run typecheck` 0；`npx vitest run` 326 passed（+2）；
`npm run build` 0。⚠ 前端改动 `npm run dev` 热更新即生效；后端 `on_query_begin` / `BridgeUI.finish` 需
**重启后端 `uv run jarvis serve`** 才生效（否则手机/微信轮次虽能显停止、但点下去取消不了）。
