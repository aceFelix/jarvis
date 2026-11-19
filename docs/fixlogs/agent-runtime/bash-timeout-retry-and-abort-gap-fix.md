# 命令超时被当成「未知错误」重试，且工具执行期间 Ctrl+C 完全无效

**日期**：2026-10-28
**影响**：语音模式让 jarvis 打开浏览器时卡死两分多钟，Ctrl+C 无法退出语音模式；同一条命令被执行两次（开了两个窗口）
**作者**：aceFelix

---

## 一、现象

`/voice` 下说「帮我打开B站」，模型调用 Bash 拉起火狐，随后终端出现：

```
好的，我用您的火狐浏览器打开 B 站。websocket closed due to fin=1 opcode=8 data=b'\x03\xefInvalid payload data'

Bash 遇到 未知错误，1.0 秒后第 1/1 次重试...
```

之后：

1. 火狐加载慢，jarvis 迟迟没有回复
2. 火狐被**重新加载**一次（用户截图里有两条 B 站标签页）
3. 火狐起来了，jarvis 仍然卡着不动
4. **Ctrl+C 完全无效**，退不出语音模式

## 二、根因

三个独立缺陷叠在一起，症状才这么难看。

### 2.1 命令超时被判成「未知错误」，于是被重试

`agent/core/error_recovery.py` 的超时判据只认英文：

```python
if self._match_any(text_lower, (r"\btimeout\b", r"\btimed out\b")):
```

而 Bash 工具的超时文案是中文——`命令超时（120秒）: ...` 与 `[沙箱] 命令超时（120秒）: ...`。
匹配不上就落进 `UNKNOWN` 分类，日志变成「Bash 遇到 **未知错误**」，
再拿 UNKNOWN 的策略（`max_retries=1`、`backoff 1.0s`）去重试。

**这就是「1.0 秒后第 1/1 次重试」的来历，也是火狐被打开两次的原因。**

### 2.2 命令超时根本不该自动重试

就算分类正确（TIMEOUT，`max_retries=2`），重试也是错的：
命令跑满 120 秒意味着它**真的要等那么久**（前台拉起 GUI 程序、长编译、等网络），
重试只是把同一件事再做一遍——等待翻倍、副作用重复。自愈层只适合「换个参数可能就好」的失败。

### 2.3 工具执行期间没有任何中断机制（Ctrl+C 无效）

`agent/core/context.py` 明确写了契约：

> `abort_event`: 取消信号。**工具应在长操作中检查此事件。**

但全仓 `agent/tools/**/*.py` 里**一次都没有引用过 abort_event**（grep 结果为 0）。

`BashTool._call_normal` 的做法是：

```python
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
```

只等输出、只看超时，**不理会 abort**。而 orchestrator 只在**每个工具调用开始前**检查
`abort_event`，不会中断正在执行的那个。

于是语音模式下按 Ctrl+C 的实际效果是：`_poll_interrupt` 把 `stop_event` 转成 `abort_event`
（这一层是通的），但 Bash 正阻塞在 `communicate()` 上，什么都感知不到，只能等满超时。
`query_loop` 的 `_stream_once` 逐事件检查 abort，所以「思考阶段」能打断、「工具执行阶段」不能——这个差异很反直觉。

## 三、修法

### 3.1 超时判据补中文

`agent/core/error_recovery.py`：

```python
if self._match_any(text_lower, (r"\btimeout\b", r"\btimed out\b", "超时")):
```

### 3.2 让工具能声明「这次失败不要自动重试」

`agent/core/result.py` 的 `ToolResult` 新增 `retryable: bool = True`；
`error()` 支持 `retryable=False`。

`RecoveryExecutor.execute` 在分类之后先看这个标记，为假就直接把错误交回模型、不重跑：

```python
if result is not None and not getattr(result, "retryable", True):
    ...
    return RecoveryResult(original_error=error, final_result=result, attempts=attempts)
```

Bash 的两处超时（普通 / 沙箱）都标 `retryable=False`。

### 3.3 Bash 遵守 abort 契约

`_call_normal` 由「只等 `communicate()`」改为「输出与 abort 竞速」：

```python
comm = asyncio.ensure_future(proc.communicate())
abort_wait = asyncio.ensure_future(ctx.abort_event.wait())
done, _ = await asyncio.wait({comm, abort_wait}, timeout=timeout,
                             return_when=asyncio.FIRST_COMPLETED)
```

- 输出先到 → 照常返回
- abort 先到 → kill 子进程、返回「已取消（用户中断）」、`retryable=False`
- 两者都没到（超时）→ kill、返回「命令超时」、`retryable=False`

`call()` 相应把 `ctx` 传进 `_call_normal`。

## 四、验证

`tests/ui/test_serve_stdin_abort.py` 新增 2 例（连同既有 6 例共 8 例）：

- `test_bash_normal_aborts_on_abort_event`：置位 `ctx.abort_event` 后 2 秒内返回、
  断言 kill 已调用、`retryable is False`
- `test_bash_timeout_marks_not_retryable`：`timeout=1` 跑挂起命令，断言回执含「超时」且 `retryable is False`

全量 `pytest tests -q`：**3649 passed**（原 3647 + 新增 2），无回归。

## 五、遗留与待复现

- **TTS 的 `opcode=8 / 1007 Invalid payload data` 尚未定位。** 这是 CosyVoice 的 WebSocket
  被服务端以 1007（Invalid payload data）关闭，属「说」的方向，与本次「工具执行卡住」无因果关系。
  `StreamTTSPlayer` 已有 `_degraded` 降级路径（整段 `speak()`），但本次日志没有出现降级提示。
  复现方法：`[tts]` 开 `verbose` 看喂进 `feed()` 的原文；若仍报 1007，优先排查
  DashScope 语音额度（同批探针中 `cosyvoice-v3-flash` 曾返回 `Arrearage`）。
- **120 秒等待本身当时没变**（后续已修）：本篇只解决了「能被打断」和「不重试」，
  没改变「后台命令也会跑满 timeout」。真正的原因不在 asyncio 等待逻辑，而在
  MSYS2 bash 自己会等后台 job——详见
  [bash-background-command-wait-fix.md](./bash-background-command-wait-fix.md)，
  那篇把后台命令的耗时从 120 秒降到了 0.3 秒。
- 沙箱路径（`_call_sandboxed` → `SandboxExecutor.run`）尚未接 abort 竞速，
  只标了超时不可重试。

## 六、教训

**声明的契约没人实现，比没有契约更危险。** `abort_event` 的 docstring 写得很清楚，
但 0 个工具引用它——"应该有人检查"变成了"没人检查"，而注释还在替它背书。

**分类器的错误文案与应用文案必须同语言。** 超时判据只写英文，而工具回执是中文，
结果把一次普通的超时升级成了「未知错误 + 重试」，还顺手把副作用做了一遍。
凡是靠文本匹配分类的地方，都要检查两侧的措辞来源是否同一套。
