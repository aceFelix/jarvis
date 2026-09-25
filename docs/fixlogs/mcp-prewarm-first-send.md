# 优化复盘：MCP 连接前移启动后台预热，首条消息秒进

- **日期**：2026-09-25
- **模块**：`agent/ui/workbench/engine.py`（workbench/serve 共用引擎）
- **作者**：aceFelix

## 1. 问题现象

用户反馈：启动桌面壳后「不能马上开始聊天，需要等待一段时间」。
逐段实测（本机 warm 缓存，`_bench_startup.py` 临时脚本，已删）：

| 阶段 | 耗时 | 说明 |
|---|---|---|
| spawn `python -m agent.serve` → stdout 握手 JSON | 1~3s | desktop.log 历次启动样本（12 组）稳定 |
| 渲染层 WS 连接 | <1s | 与握手同秒（`[renderer] ws connect`） |
| settings load / build_default_registry / 系统提示词 | 0.12 / 0.62 / 0.26s | 可忽略 |
| **MCP connect_all（7 server 并发）** | **8.88s** | 首条消息 `_ensure_session` 同步 await |
| MCP 工具注册 / harness 后台注册 | 0.20 / 0.19s | harness 本就在后台，不阻塞 |

结论：输入框约 2~3s 即解锁（启动遮罩消失），但**首条消息（或点历史会话）
触发懒装配 `_ensure_session`，其中 `await _connect_mcp` 同步等 7 个 MCP server
并发连接约 9s**（npx 冷缓存更久，单 server 超时上限 15+5+10+10s）——消息已入队
但轮次不开跑，体感「发出去没反应 / 不能马上聊」。

## 2. 根因分析

- MCP 连接放在首条消息的装配路径里同步 await（2026-09-20 补 MCP 接入时的
  位置选择，见 [serve-mcp-truncation-fix.md](serve-mcp-truncation-fix.md)），
  启动后的空闲窗口（用户读界面、打字）完全浪费。
- 对照先例：harness 动态工具早已后台线程加载、实时语音的 MCP 也是异步加载
  （加载完动态补挂工具），文本路径是唯一同步等 MCP 的入口。

## 3. 优化方案

- `ChatEngine._run` 起跑即创建后台预热任务 `_prewarm()`：
  `build_default_registry()` → `registry_hook`（serve 的提醒/截止日期工具）→
  `_connect_mcp(registry)`；完成后 `_registry_ready` 置位（异常也置位）。
- `_ensure_session()` 改为 `await asyncio.wait_for(self._registry_ready.wait(), 30)`
  后复用 `self._registry`；预热未产出（超时/失败）才走原同步装配兜底
  （现建 registry + hook + MCP），能力不缺失。
- 预热窗口内就发消息：不等 MCP，先少 MCP 工具聊；MCP 连上后工具自动补挂
  进同一 registry（与 harness 后台加载同口径）。
- `_shutdown()` 取消预热任务后**必须 await 落地**再关 loop：否则 loop.close()
  时预热任务仍 pending，报 "Task was destroyed but it is pending" 且 MCP
  子进程回收不全（本次改造引入、同次修复）。

## 4. 验证结果

- `tests/ui/test_workbench_engine_mcp.py` 新增 3 用例：预热建 registry + 钩子
  挂载 + ready 置位（enable_mcp=False 不连）/ 预热失败降级 ready 仍置位 /
  `_ensure_session` 复用预热 registry（重建与同步连 MCP 即 fail）。
- 启动引擎线程的 3 个既有用例（engine_api ×2、serve_stdin_abort ×1）补
  `settings.enable_mcp = False` 保 hermetic：预热不真连用户 MCP server
  （改造前这些用例因懒装配不碰 MCP，改造后会真连并拖慢测试）。
- `pytest tests/ui tests/serve -q`：134 passed，无真 MCP 子进程输出、无
  destroyed task 告警，耗时由 5.84s 降回 3.94s；全量 `pytest tests -q`
  1798 passed（仅存 sandbox 测试既有 proactor 告警，与本次无关）。
- 人工走查：重启桌面壳 → 启动遮罩约 2s 消失 → 右栏日志在启动后数秒内出现
  「MCP: 7/7 server 已连接」→ 立即发消息应直接进 LLM（状态栏不再先转
  「正在连接 7 个 MCP server...」）。

## 5. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/workbench/engine.py` | 新增 `_prewarm` 与 `_registry/_registry_ready/_prewarm_task`；`_ensure_session` 复用预热 registry + 30s 兜底；`_shutdown` 取消后 await 预热任务 |
| `agent/ui/workbench/render.py` | 新增：附件/历史渲染纯函数自 engine 拆出（engine 超 800 行上限，顺拆） |
| `tests/ui/test_workbench_engine_mcp.py` | 新增预热三用例 |
| `tests/ui/test_workbench_engine_api.py`、`tests/ui/test_serve_stdin_abort.py` | 启动引擎用例关 enable_mcp 保 hermetic |
| `docs/architecture/07-UI层.md` | 懒装配章节补预热顺序与降级口径 |

## 6. 经验总结

- **启动空窗是最便宜的并行资源**：用户从窗口可见到打出第一条消息通常有
  5~15s 空闲，把秒级装配（MCP 连接/registry 构建）塞进这段空窗，比在消息
  路径上做任何缓存优化都直接。
- **后台化要留同步兜底**：预热任务任何异常都必须置位 ready 事件，否则
  `_ensure_session` 的 wait 会挂满超时才降级——「后台优化」不能变成
  「新的单点故障」。
- **cancel 必须 await**：loop.close() 前不等待被取消任务落地，Windows 下
  表现为 destroyed-pending 告警 + 子进程管道回收不全，测试 hermetic 问题
  往往就是这类生命周期漏洞的第一个信号。
