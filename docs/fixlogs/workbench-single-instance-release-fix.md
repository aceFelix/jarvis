# 工作台单实例守卫 release 端口释放竞态修复（Linux CI 失败）

- 日期：2026-09
- 作者：aceFelix
- 范围：`agent/ui/workbench/single_instance.py`、GitHub Actions workflow
- 关联：[docs/architecture/07-UI层.md](../architecture/07-UI层.md)（单实例机制章节）

## 1. 问题现象

GitHub Actions Linux CI（Python 3.13）全量 1666 用例中唯一失败：

```text
tests/ui/test_workbench_single_instance.py::test_release_frees_port_and_lock
    assert guard.try_acquire() is True
E   assert False is True
```

即 `SingleInstanceGuard.release()` 之后立即新建守卫 `try_acquire()` 应成功，
却返回 False（被判定「已有驻留实例」）。Windows 本地同用例稳定通过，
仅 Linux CI 复现。

## 2. 排查过程

1. 假设锁文件未清理 → 排除：失败断言在端口重绑路径（`try_acquire` 内
   `bind` 抛 OSError 才返回 False），且锁文件断言在其后；
2. 假设其他用例残留占用 47812 端口 → 排除：同文件前序用例 finally 均
   release，且失败点紧跟本用例首次 acquire；
3. 转折点：对比 Windows/Linux 套接词语义——Linux 的 `close()` **不会唤醒**
   其他线程中阻塞的 `accept()`，监听 socket 要等 accept 自身 0.5s 超时到期
   才从内核端口表释放；Windows `closesocket()` 会强制取消他线程的阻塞调用，
   socket 立即释放；
4. 根因确认：`release()` 只 close 不等待监听线程退出，Linux 下存在最长
   0.5s 的「端口仍被占」窗口，测试（及真实场景关窗后立即重开）在此窗口内
   重绑必然失败。

## 3. 根因分析

- `release()` 语义不完整：关闭 fd ≠ 端口释放。Linux 上阻塞在 accept 的
  监听线程仍持有 socket 引用，端口表条目存活至 accept 超时；
- 该竞态同样是生产 bug：关闭窗口后 0.5s 内再次启动会被误判为驻留实例，
  向已死 socket 发 FOCUS 后退出，表现为「双击没反应」。

## 4. 修复方案

`agent/ui/workbench/single_instance.py`：

1. `release()`：`_stop.set()` → `shutdown()`（尽力唤醒 accept）→ `close()`
   → **join 监听/心跳线程（timeout 2s）** 确认 socket 引用归零、端口真正
   释放后才返回；最后置空 `_server/_listener/_heartbeat` 并清锁；
2. `_accept_loop()`：监听 socket 改局部引用 `srv`，避免 release 置 None 后
   循环读属性 AttributeError，同时保证线程退出前引用持有与 join 对齐。

workflow 清理：`ci.yml` / `publish.yml` 中
`-k "not test_bring_up_reuses_running_proc"` 排除过滤所指用例在当前代码库
已不存在（历史残留、过滤已空转），恢复为全量 `pytest tests/ -v --tb=short`。

## 5. 验证结果

- 本地 Windows：`pytest tests/ui/test_workbench_single_instance.py` 及
  会话/serve/autostart 回归全过；
- Linux 侧由推送后的 CI 全量跑验证（该用例不再需要平台排除）；
- 无行为回退：release 增加的上限为 join 2s（正常路径 accept 0.5s 超时内退出）。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/workbench/single_instance.py` | release 加 shutdown+join 确定性释放端口；accept 循环改局部引用 |
| `.github/workflows/ci.yml` | 移除失效的 -k 排除过滤 |
| `.github/workflows/publish.yml` | 同上 |

## 7. 经验总结

- **close ≠ 释放**：多线程 socket 服务停止时，必须 join 工作线程确认引用
  归零；Linux/Windows 对「close 是否唤醒他线程阻塞调用」语义不同，跨平台
  测试必须覆盖 Linux CI；
- **固定端口单实例**判定失败路径要区分「真驻留」与「自身释放竞态」，
  释放侧保证确定性后，误判窗口才消失；
- CI 的 `-k` 临时排除要登记待办并及时清理，用例删除后过滤会静默空转，
  掩盖「其实已经没人管这条失败」的事实。
