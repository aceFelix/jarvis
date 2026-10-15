# 三通道 query 锁的「等锁窗口被取消」泄漏 —— 全量高负载下偶发失败用例的修复复盘

> 版本：1.0 | 模块：`agent/utils/query_lock.py` + 桌面引擎 / 手机桥接 / 微信三通道 | 类型：并发缺陷（asyncio 取消 × 线程池阻塞锁）
> 发现方式：本地全量 `pytest` 在第 3 次运行时偶发失败（前 2 次全绿）
> 作者：aceFelix

## 一、问题现象

PR #1 的 CI 转绿后，在开发机（Windows）连续跑全量回归：

```
第 1 次：2560 passed
第 2 次：2560 passed
第 3 次：1 failed, 2559 passed   ← tests/ui/test_serve_stdin_abort.py::test_engine_abort_current_reply
```

失败点在该用例的收尾断言（等了 10s 也没同时集齐 `assistant_done` 与 `info『已停止回复』`）。
特征是**偶发**：单跑该文件 8 次全过，跑全量时约 1/3 概率失败，且失败的不确定性出现在
「已把当前任务登记为 `_send_task`、但还没拿到 query 锁」这个窗口里。

影响面（这是产品缺陷，不只是测试抖动）：

1. 桌面点「停止」若正好落在等锁期间，`threading.Lock` 被线程池事后取走且**永不归还**
   → 桌面 send、手机 bridge、微信三条通道共用同一把锁，后续所有 query 永久卡死；
2. 本轮 `finally` 整段被跳过 → 前端气泡停在「正在回复」永不落地、`_send_task` 句柄残留；
3. 全量测试在高负载下偶发失败，掩盖真实回归。

## 二、排查过程

1. **先怀疑测试写得紧**。单跑 `tests/ui/test_serve_stdin_abort.py` 全绿，全量才挂，
   直觉是机器负载导致 5s/10s 超时不够。**这个方向是错的**——如果只是超时，
   失败信息应该出现在断言超时那一步，而实际是事件流里**根本没有** `assistant_done`。
2. **读代码定位事件来源**。`已停止回复` 由 `engine._command_loop` 的
   `except asyncio.CancelledError` 分支发出（engine.py:352-356），
   `assistant_done` 由 `_handle_send` 的 `finally` 中 `self._ui.assistant_done()` 发出。
   两者缺一即前端闸门不闭合，而全量失败时缺的正是后者。
3. **发现 `await` 在 `try` 之外**。`_handle_send` 原写法：

   ```python
   await asyncio.get_running_loop().run_in_executor(None, self._query_lock.acquire)
   try:
       ...
   finally:
       self._query_lock.release()
   ```

   `reply.abort` → `task.cancel()` 打在等锁的 `await` 上时，`CancelledError` 直接从
   `try` 上方抛出，**`finally` 一行都不执行**：锁不释放、`_send_task` 不清、
   `assistant_done` 不发。而线程池里那次 `acquire()` 取消不掉，稍后照样把锁拿走。
4. **做成确定性复现**，不接受"偶发修好"。新增用例
   `test_abort_during_lock_wait_returns_lock_and_closes_turn`，用 `_SlowLock`
   （`acquire()` 前 `time.sleep(0.3)`）放大窗口。打在未修代码上必挂：

   ```
   AssertionError: 取消落在等锁窗口时本轮未收尾：['user_message', 'info']
   ```

   事件序列里 `info`（已停止回复）有、`assistant_done` 无，与第 2 步的推断完全吻合。
5. **横向扫同类写法**。`run_in_executor(... , lock.acquire)` 全仓共 4 处，
   且**四处都是 acquire 在 try/finally 之外**：
   `agent/ui/workbench/engine.py`（桌面 send）、`agent/ui/workbench/slash_bridge.py`
   （桌面技能轮）、`agent/bridge/server.py`（手机桥接）、`agent/wechat/server.py`（微信）。
6. **顺带澄清**：既有用例 `test_engine_abort_current_reply` 的抖动不是测试写得差，
   而是同一竞态在默认线程池调度下的自然表现；负载只是提高了中奖概率。

## 三、根因分析

**直接根因**：阻塞锁的等待被放进线程池（`run_in_executor`）是正确做法（不能冻结引擎
loop），但**取消语义没有处理**。`asyncio.Task.cancel()` 只能取消 `await`，取消不掉已经
在线程里跑的 `lock.acquire()`。于是存在一个"协程已死、锁稍后被取走"的窗口，
而这段窗口的清理工作原本写在 `try` 之外，`finally` 无法覆盖。

**为什么之前的实现会出错**：写的时候关注的是"别阻塞 loop"和"拿到锁后一定要释放"，
默认 `await` 失败就等于"没拿到锁"。这个默认在纯 asyncio 锁（`asyncio.Lock`）上成立，
在线程池包装的 `threading.Lock` 上**不成立**——锁的获取是异步于协程生命周期完成的。

**为什么本地平时测不出来**：单文件跑的时候线程池空闲，`acquire()` 几乎立即返回，
窗口宽度接近 0；全量并行时线程池排队，窗口被拉长到足以让 `abort` 落在里面。

涉及模块：`agent/ui/workbench/engine.py`（`_handle_send` / `_command_loop` /
`abort_current_reply`）、`agent/ui/workbench/slash_bridge.py`（技能轮）、
`agent/bridge/server.py`、`agent/wechat/server.py`、共享的 `threading.Lock` 实例。

## 四、修复方案

把"取消安全等锁"收敛成一个公用原语，四个调用点复用，而不是四处各补一段 `try`。

| 层 | 改动 | 为什么这样能解决 |
|---|---|---|
| 新增原语 | `agent/utils/query_lock.py` → `acquire_lock_cancellable(lock) -> bool`：返回 `True` 表示调用方已持有锁（须在 `finally` 里 `release()`）；抛 `CancelledError` 表示等待中被取消，**锁由原语自己负责归还** | 归还逻辑与等待逻辑同源，调用方不可能忘 |
| 返回值约定 | 四处调用点统一改成 `locked = False` → `try: locked = await acquire_lock_cancellable(...)` → `finally: if locked: release()` | 取消路径下不能再 `release()`，否则解未持有的锁抛 `RuntimeError`；正常路径下必须释放 |
| 收尾完整性 | `engine._handle_send` / `slash_bridge` 技能轮把 `assistant_done`、`_send_task = None`、检查点绑定、`_after_turn()` 全部保留在 `finally` 内 | 取消落在等锁窗口时本轮照样收尾，前端闸门闭合 |
| 回归用例 | `tests/test_query_lock.py`（3 例，直接测原语）+ `tests/ui/test_serve_stdin_abort.py` 新增 1 例（`_SlowLock` 确定性复现引擎路径） | 原语层与调用层各钉一颗钉子，避免以后重构时又回到 try 外 |

**中途返工（如实记录）**：原语第一版写成

```python
fut = loop.run_in_executor(None, lock.acquire)
try:
    return bool(await fut)
except BaseException:
    fut.add_done_callback(_give_back)   # ← 无效
    raise
```

看起来合理，实际不成立：外层任务被取消时，asyncio 会**连带**把 `run_in_executor`
返回的那个 asyncio future 标成 `CANCELLED`（底层 `concurrent.futures` 的工作项已经在
RUNNING，取消不掉），于是回调里 `done.result()` 只会抛错，永远读不到"锁其实被拿到了"。
是刚写的 `test_cancel_while_waiting_locks_gives_lock_back` 当场把它测挂（`取消等锁后线程池
没有归还锁`），不是人工 review 发现的。

正确写法是用 `asyncio.shield` 保住**内层** future，取消只影响外层等待，归还回调挂在
内层上：worker 真拿到锁（`result()` 为 `True`）就立刻 `release()`；若工作项在排入线程池
前就被取消，`result()` 抛 `CancelledError`，无锁可还，静默跳过。

**非临时方案**：原语义（协程等锁期间可被取消）是业务要求，不是 workaround；
若将来 query 锁改为 `asyncio.Lock`，本原语可以整体退役，但调用点的 `locked` 标志约定不变。

## 五、验证结果

CI 侧本批只改 jarvis 仓（缺陷与测试都在 `agent/` 与 `tests/` 下）。

| 验证项 | 命令 | 结果 |
|---|---|---|
| 未修代码上的确定性复现 | `uv run pytest tests/ui/test_serve_stdin_abort.py -q`（修复前） | 1 failed：`取消落在等锁窗口时本轮未收尾：['user_message', 'info']` |
| 原语第一版的归还逻辑 | `uv run pytest tests/test_query_lock.py -q`（shield 前） | `test_cancel_while_waiting_locks_gives_lock_back` FAILED |
| 新用例 + 原有用例 | `uv run pytest tests/test_query_lock.py tests/ui/test_serve_stdin_abort.py -q` | 9 passed |
| 语法/编译 | `uv run python -m compileall -q agent/` | exit 0 |
| 全量回归 × 3 轮 | `uv run pytest -q --tb=line` | 2564 passed, 3 warnings（61.60s / 62.59s / 51.29s），**三轮全绿** |
| 文档门禁 | `uv run python scripts/check_md_links.py`；`uv run pytest tests/test_md_link_checker.py -q` | 125 md / 判定 518 / 跳过 30 / **死链 0 / 未入库 0**；17 passed |

对照修复前：同样三轮全量里有 1 轮挂在 `test_engine_abort_current_reply`。修复后既有用例
不再抖，新用例在竞态窗口被人为放大 300ms 的情况下仍然稳定通过。

**回归检查**：三个通道（桌面 send / 手机 bridge / 微信）的等锁与释放语义未变，只新增
"取消时归还"这一条路径；技能轮额外验证了 `_send_task` 句柄在取消后恢复为 `prev_task`。

## 六、涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/utils/query_lock.py` | 新增 `acquire_lock_cancellable()`：`asyncio.shield` 保住内层 future，被取消时给内层挂 done 回调自动归还线程池事后取得的锁 |
| `agent/ui/workbench/engine.py` | `_handle_send` 等锁移入 `try/finally`，改 `locked` 标志决定是否释放；注释说明取消落在等锁窗口的后果 |
| `agent/ui/workbench/slash_bridge.py` | 技能轮等锁同构改造，`finally` 保留句柄恢复与 `_after_turn()` |
| `agent/bridge/server.py` | `run_query` 等锁移入 `try/finally`，移除不再使用的 `loop` 局部变量 |
| `agent/wechat/server.py` | `_exec_query` 同构改造，补方法级文档说明等锁期间可被取消 |
| `tests/test_query_lock.py` | 新增 3 例：正常持有、取消后自动归还（线程占锁构造）、多等待者串行与锁复用 |
| `tests/ui/test_serve_stdin_abort.py` | 新增 `test_abort_during_lock_wait_returns_lock_and_closes_turn`（`_SlowLock` 确定性复现引擎路径） |
| `docs/test/TEST_CHECKLIST.md` | 新增 36.3 节 T-363/T-364 与本批用例口径 |
| `docs/fixlogs/query-lock-cancel-leak-fix.md` | 本篇复盘 |

## 七、经验总结

1. **`await run_in_executor(None, blocking_call)` 的取消不是原子的**。协程被取消只意味着
   "不再等结果"，不意味着"这次调用没发生"。凡是"等待动作本身会产生副作用"（拿锁、占用
   资源、提交任务）都必须把 `await` 放进 `try`，并让清理逻辑能区分"我拿到了"和"我半路跑了"。
2. **偶发失败先问"少了哪个事件"，再问"超时够不够"**。本次误判的起点是把 1/3 概率直接
   归因于负载不足；实际上断言失败信息里的事件序列（有 `info`、缺 `assistant_done`）已经
   指明是代码路径缺失，而不是时间不够。
3. **飘用例的正确处理是把它变成确定性复现**，不是加 `sleep`、加大超时或加 `xfail`。
   `_SlowLock` 只多 10 行代码，却把 1/3 概率变成 100%——先让它在未修代码上必挂，
   修完再让它必过，这才是证据。
4. **同类缺陷要横向扫一遍再收工**。`lock.acquire` 的线程池包装全仓有 4 处，
   只修测试报出的那一处，剩下三处就是三次待爆的同类事故（且分布在三个通道上）。
   可复用做法：抽公用原语 + 全量替换调用点，让"下次还有人这样写"变难。
5. **`asyncio.shield` 不是可选优化**。取消传播会污染 `run_in_executor` 返回的 future 状态，
   使"事后读结果"这条路封死；想要知道"被取消的等待后来成功了没"，必须 shield 内层并把
   回调挂在内层上。
6. **自己写的测试会立刻抓到自己的 bug**。本次两处方向纠错（第 1 步的超时误判、
   原语第一版的无效归还）都是被新写的用例证伪的，而不是靠阅读代码——这也再次说明
   先写复现用例再动手改实现的价值。
