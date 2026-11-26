# /voice 聊完一轮后进程静默退出（barge-in 关流竞态）

> 修复日期：2026-11-26　分支：`refactor/talk-voice-layering`　作者：aceFelix

## 1. 问题现象

用户在 `E:\J.A.R.V.I.S_Work\jarvis-work` 下运行 `jarvis` → `/voice`：

```
🎤 聆听中...（说话即可，停顿后自动结束）
🧑 你说: 在吗？
在的，先生，随时听候吩咐。
✓ 完成 · 共 0 步 · 文件工具 0 次 · 0 失败 · 3.0s
📝 会话标题已生成: 在吗
🎤 聆听中...（说话即可，停顿后自动结束）

许发明@DESKTOP-KN2CB2R MINGW64 /e/J.A.R.V.I.S_Work/jarvis-work (master)
$
```

- 第一轮正常，进入下一轮聆听后**直接回到 shell**；
- **没有任何 traceback / 错误提示**；
- 用户描述“聊着聊着自动退出程序”。

## 2. 排查过程

### 2.1 先确认运行的是哪份代码（避免误判）

```powershell
where.exe jarvis
# E:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\.venv\Scripts\jarvis.exe

python -c "import agent, agent.voice.voice_barge as vb; print(agent.__file__, agent.__version__, vb.__file__)"
# ...\jarvis\agent\__init__.py 2.6.2 ...\jarvis\agent\voice\voice_barge.py
```

结论：入口是工作区 `.venv` 的 2.6.2，`voice_barge` 确实存在——**不是旧 checkout**，
问题在当前代码。

### 2.2 排除 Python 层主动退出

`agent/voice/` 下无 `os._exit` / `sys.exit`；`voice_loop` 的 Ctrl+C 处理只置标志、
在协程内抛 `KeyboardInterrupt` 并打印“退出语音模式”。而本次连这一行都没有，
更像是 **native 崩溃**（Python 异常一定会有 traceback）。

### 2.3 锁定可疑点：关闭 PyAudio 流的线程顺序

`VoiceBargeWatcher.stop()` 原实现：

```python
self._stop.set()
if self._stream is not None:
    self._stream.close()      # ← 主线程
self._thread.join(timeout=1.0)  # ← 监听线程此时可能还阻塞在 read()
```

而 `_run()` 在另一线程里循环 `stream.read(...)`。Windows 上 PortAudio 对
「一个线程 `Pa_ReadStream`，另一线程 `Pa_CloseStream`」是未定义行为。

### 2.4 用最小复现脚本对照（关键证据）

脚本按两种顺序反复开/读/停 40~60 轮（`.venv` 的 pyaudio，真实麦克风）：

| 顺序 | 结果 |
|---|---|
| 先 close 再 join（原实现） | 第 4 轮进程退出，`exit=-1073741819` = **0xC0000005 访问违例**，无 traceback |
| 先 join 再 close | 60/60 轮正常，`exit=0` |

与“第一轮结束→watcher.stop()→下一轮聆听时进程消失”的时间线完全吻合。

## 3. 根因

`/voice` 每轮结束都会 `stop()` 掉播报期的 barge-in 监听器。原实现**在主线程先
`close()` 输入流、再 `join` 监听线程**；监听线程可能仍阻塞在 `stream.read()`，
Windows PortAudio 在 read/close 并发时直接访问违例，进程无 Python 异常、静默退出。

`barge_in._BargeInWatcher.stop()` 存在同样的顺序问题（旧打断监听器，当前主链路
已改用 `voice_barge`，但一并修正）。

## 4. 修复方案

- `VoiceBargeWatcher.stop()`：只置停止标志 + `join(timeout=2.0)`，**绝不在外部 close**；
- `_run()`：用局部变量持有流，`finally` 中由监听线程自己 `close()` 并清空 `self._stream`；
- `_BargeInWatcher` 同样处理（原实现只在 stop 里关流，改为线程内关闭）；
- 读单帧最长阻塞 100ms（`frames_per_buffer=1600 @16kHz`），2s join 足够；
- 新增回归用例：断言 `close` 发生在监听线程内、且不在主线程。

## 5. 验证结果

| 项 | 结果 |
|---|---|
| 最小复现（join_first 60 轮） | 60/60，exit 0 |
| `tests/voice/test_voice_barge.py` | 新增 1 例，全部通过 |
| 全量 `pytest tests/` | 见提交信息（0 failed） |
| 真机 `/voice` 多轮 | **待用户复测** |

## 6. 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/voice/voice_barge.py` | stop 只 join；流由 `_run` finally 关闭；docstring 记录 0xC0000005 约束 |
| `agent/voice/barge_in.py` | 同样的 stop/_run 顺序修正 |
| `tests/voice/test_voice_barge.py` | 新增关闭线程归属回归用例 |
| `docs/log/CHANGELOG.md` | Unreleased 修复条目 |

## 7. 经验总结

- **阻塞式 C 扩展资源（PortAudio stream、socket、文件锁）的关闭必须与使用者同线程**；
  “先停标志、再 join、最后由使用者自己关”是可复用范式。
- 进程**无 traceback 直接退回 shell** = native 崩溃（0xC0000005 / 段错误），
  优先查 C 扩展的并发调用，而不是 Python 异常路径。
- 用**子进程 + 退出码**做最小对照实验（close_first vs join_first），能在几分钟内
  把“偶发”变成“第 4 轮必现”，比读代码猜快得多。
- 排查“用户环境是不是旧代码”时先跑 `where.exe` + `import agent` 打印路径与版本，
  拿证据再下结论。
