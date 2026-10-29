# 桌面壳每轮固定卡 10 秒：同步 git 检查点阻塞发送

> 修复日期：2026-10-30
> 影响范围：桌面壳（jarvis-desktop / workbench）每一轮对话
> 严重程度：用户体感「桌面版明显比终端慢」，单轮固定损失 10 秒
> 作者：aceFelix

## 1. 问题现象

用户反馈：**桌面 jarvis 的反应速度远不如终端 jarvis**。每次发完消息，要等一会儿才开始输出思考。

终端一切正常 —— 同样的模型、同样的配置、同样的 `deepseek-flash`。

复现条件：桌面壳连续对话，**每一轮**都有。新开会话、重启进程同样存在。

## 2. 排查过程

### 2.1 三次错误猜测（教训部分）

| 猜测 | 依据 | 结果 |
|---|---|---|
| 上下文超限 | 用户说「聊多了就卡」 | ❌ 用户当场纠正「我才聊两句」 |
| 思考档位过高 | 桌面选「思考: 高」→ `reasoning_effort=max` | ❌ 实测 `max` 反而比默认快，档位不是主因 |
| 感知总线被反复重建 | 日志里 220ms 内装配 20+ 次 | ❌ 那批日志是 pytest 写进用户日志的，作废 |

**前两次是纯猜，第三次是被污染的数据带偏。**

### 2.2 转折点：先让它能测量

停止猜测，在 `openai_provider.stream()` 和 `engine._handle_send()` 各埋分段计时：

```
LLM 计时: 首包思考 0.45s | 首字正文 9.39s | 全程 12.62s | 思考 2029 字 | 正文 733 字
桌面发送: 检查点 10.060s | 等锁 0.003s | 本地前处理共 10.063s
```

**一次测量就定位了。** 关键数据（用户真实四轮）：

```
23:51:59  检查点 10.060s | 等锁 0.003s | 本地前处理共 10.063s
23:53:48  检查点 10.079s | 等锁 0.000s | 本地前处理共 10.079s
23:55:25  检查点 10.058s | 等锁 0.000s | 本地前处理共 10.059s
23:58:53  检查点 10.057s | 等锁 0.000s | 本地前处理共 10.058s
```

四个数字精确到小数点后两位几乎一致（10.057 / 10.058 / 10.060 / 10.079）—— 这不是"慢"，是**每次都撞在同一个超时上限上**。

同时模型侧数据证明模型并不慢：`首包思考 0.45~1.45s`（网络很快）。

所以用户体感 = **10 秒检查点 + 5~9 秒思考**，而终端没有那 10 秒。

## 3. 根因分析

### 3.1 直接原因：同步 git 快照阻塞发送

`agent/ui/workbench/engine.py` 的 `_handle_send()` 在每轮对话开始前创建会话检查点：

```python
ckpt_mgr = checkpoint_ops.make_manager(self)
ckpt_id = ckpt_mgr.create(reason=composed) if ckpt_mgr.available() else None
```

`CheckpointManager.create()`（`agent/core/checkpoint.py:290`）第一条命令就是：

```python
self._git("add", "-A")
```

而 `_run_git` 用 `subprocess.run(..., timeout=self._timeout)`，默认 `_GIT_TIMEOUT = 10` 秒。

**在 24,659 个文件的工作区上（含 `.venv`、`__pycache__`，虽被 ignore 但 git 仍要逐个 stat），Windows 下 `git add -A` 必然吃满 10 秒超时**，然后 `except subprocess.TimeoutExpired` 返回 `None` —— 功能上一无所获，用户却白等了 10 秒。

### 3.2 为什么只有桌面壳中招

终端（`main.py` REPL）**根本没有会话检查点**这一步，所以同样的模型在终端没有任何额外延迟。

这也是为什么"换模型无效、重启才恢复首句正常"这一系列现象看起来像别的问题，实际与模型完全无关。

### 3.3 为什么之前没人发现

- `create()` 的超时是**静默降级**（返回 None 并只记一条 warning），功能层面"看起来正常"
- 检查点是同步调用，日志里只有一条 warning，不体现耗时
- 没有分段计时，用户只说"感觉慢"，无法定位到具体环节

## 4. 修复方案

**不关功能，只改时序** —— 把快照从"同步等待"改成"后台进行"：

```python
ckpt_task = asyncio.ensure_future(
    asyncio.get_running_loop().run_in_executor(None, ckpt_mgr.create, composed)
)
try:
    # shield：超时只放弃等待，不要把快照任务本身取消掉
    ckpt_id = await asyncio.wait_for(asyncio.shield(ckpt_task), timeout=0.4)
except Exception:
    ckpt_id = None
```

轮末（`finally`）再取结果：

```python
if ckpt_id is None and ckpt_task is not None:
    try:
        ckpt_id = await ckpt_task
    except Exception:
        ckpt_id = None
```

**设计要点：**

1. 快照仍在「发送前」启动（线程池立即开始），状态语义不变
2. 0.4 秒宽限：小仓库/无变更时立刻拿到 cid，副作用账本正常工作
3. 大仓库拿不到就留给 `finally` —— 那时模型早已答完，**10 秒被完全藏在思考期里**
4. `asyncio.shield` 保证超时只放弃等待，快照本身照常跑完
5. 任何异常一律兜底 `None`，退回"仅对话回退"，不影响主流程

### 4.1 顺带修复测试隔离

`test_serve_stdin_abort.py` 里 `Settings()` 默认 `checkpoint_enabled=True`，会导致测试**真的去跑 git**（该文件单跑从 4s 变成 9.9s）。且用例用 `time.sleep(0.3)` 放大"等锁窗口"，被新增的 0.4s 快照宽限挪走了取消落点。

修复：在这些用例里显式 `settings.checkpoint_enabled = False`，与快照无关的时序测试不再受干扰。

## 5. 验证结果

### 5.1 分段计时实测

```
[B 改动后] 发送前阻塞 0.42s  （用户感知延迟）
[B 改动后] 快照实际完成于 1.98s（藏在模型思考期里）
[A 改动前] 用户等待 1.78s
=== 用户感知延迟从 1.78s 降到 0.42s，快照能力保留 ===
```

（探针跑的是 index 已热的场景；真实每轮间隔数分钟、index 冷，改动前是 10 秒，改动后仍是 0.42 秒 —— **用户感知延迟与工作区大小彻底解耦**。）

### 5.2 回归测试

- `tests/ui` + `tests/core`：**1100 passed**
- 全量：**3888 passed**
- 新增 `TestStreamTimingLog` 2 例，锁住计时日志契约

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/llm/openai_provider.py` | 新增分段计时：首包思考 / 首字正文 / 全程 + 思考/正文字数 |
| `agent/ui/workbench/engine.py` | 检查点改为线程池异步 + 0.4s 宽限 + 轮末取；新增本地前处理计时 |
| `tests/llm/test_openai_provider.py` | 新增 `TestStreamTimingLog` 2 例 |
| `tests/ui/test_serve_stdin_abort.py` | 引擎用例关闭检查点（避免真 git + 时序污染） |

## 7. 经验总结

### 7.1 该测的别猜

本次排查连续猜错三次（上下文、思考档位、感知重建），每次都基于"看起来合理"的推断，全部落空。**加上分段计时后一次就定位。**

**规则：凡涉及"为什么慢"，先埋点测出分段耗时，再谈方案。** 没有分段数据的优化都是猜测。

### 7.2 别在跑测试的同时分析运行时日志

`~/.jarvis/jarvis.log` 会被 pytest 写入（`get_logger()` 全局、mock provider 也会记 `LLM 请求`）。我曾在跑测试期间分析同一份日志，得出"感知总线每轮重建 20 次"的错误结论。

**判断方法：日志里出现 `key=<MagicMock>`、`base_url=''`、`raw='not json at all'` 这类特征，就是测试产出的。**
**根治方向（未做）：测试应把日志重定向到临时文件。**

### 7.3 静默降级的超时是隐藏的性能陷阱

`except subprocess.TimeoutExpired: return None` 让功能"看起来正常"，却吃掉了 10 秒用户时间。**凡是同步阻塞在用户主链路上的操作，超时上限必须远低于用户耐心阈值（≤0.5s），否则等于每次卡那么久。**

### 7.4 区分"降级"与"优化"

最初给出的建议是降思考档位、关感知、换非推理模型 —— 这些都是**削能力换速度**。真正的优化应该是：定位到具体阻塞点，改时序或消除重复工作，能力一点不少。
