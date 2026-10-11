# 修复：CI 上 3 个 voice 测试因缺 `[voice]` 依赖误败

- **日期**：2026-10-11
- **作者**：aceFelix
- **影响范围**：`tests/voice/test_voice_loop_turn_end.py`、`tests/voice/test_voice_loop_responsiveness.py`（仅测试代码，不涉及生产逻辑）
- **严重级别**：中（CI 红，阻塞合并；不影响线上功能）

## 现象

GitHub Actions 执行 `pytest tests/ -v --tb=short`（矩阵 Python 3.11/3.12/3.13/3.14）报 **3 failed / 2395 passed / 10 skipped**：

```
FAILED tests/voice/test_voice_loop_responsiveness.py::TestVoiceLoopStopDuringListen::test_stop_event_ends_session_and_loop_free
       - IndexError: list index out of range            # states[-1] 越界
FAILED tests/voice/test_voice_loop_turn_end.py::test_on_turn_end_fires_when_round_grows_messages
       - assert [] == [1]                              # 回调从未触发
FAILED tests/voice/test_voice_loop_turn_end.py::test_on_turn_end_exception_swallowed
       - IndexError: list index out of range            # 会话未走到 STATE_EXITED
```

复现步骤：

```bash
# CI 只装 [dev]，不含 [voice]
pip install ".[dev]"
pytest tests/voice/test_voice_loop_turn_end.py tests/voice/test_voice_loop_responsiveness.py -v
# 观察：上述 3 例失败
```

## 排查过程

1. **先怀疑本次改动**：本轮提交只改 email/serve/协议/文档，与 voice 无交集；`git log` 确认失败测试来自更早提交
   （`8188d9f`、`ade33c9`），且 CI 输出平台为 `Python 3.13.16`（Linux）。→ 排除本次改动引入。
2. **看失败语义**：两例 `IndexError` 都是 `assert states[-1] == STATE_EXITED` 越界（`states` 为空），
   一例 `assert [] == [1]` 是回调没被调用。三者共同点：**都在跑完整 `voice_loop()` 的入口测试**，
   而 `voice_loop()` **一个事件、一个状态都没发**。
3. **定位「零事件」来源**：读 [voice_loop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/voice_loop.py#L498-L514)，
   进入会话前有一段 fail-fast 前置依赖检查：

   ```python
   _VOICE_REQUIRED_MODULES = ("dashscope", "pyaudio")   # L93
   ...
   missing = [n for n in _VOICE_REQUIRED_MODULES if importlib.util.find_spec(n) is None]
   if missing:
       events.on_error(...); events.on_info(...)
       return                                          # 在 on_state(STATE_DIALOG)（L549）之前
   ```

   该 `return` 发生在任何 `on_state` 之前 → `states` 为空、`on_turn_end` 回调不触发，
   完美对上 3 例失败现象。
4. **确认环境差异**：CI 的 `ci.yml` 只 `pip install ".[dev]"`（[L36-39](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/.github/workflows/ci.yml#L36-L39)），
   **不含 `[voice]`**；本地机器 `dashscope`/`pyaudio` 均已安装，故本地全绿、CI 全红。
   这也解释了为什么是「隐藏耦合」：测试 monkeypatch 掉了 `create_stt`/`CosyVoiceTTS`，
   却漏掉了这段**先于它们执行**的真实依赖探测。
5. **发现第 4 例「假通过」**：`test_on_turn_end_skipped_when_no_message_growth` 断言 `calls == []`，
   早退时恰好也成立 → CI 上属于**假绿**，掩盖了同一根因。

## 根因

测试对「真实环境中是否装了 `[voice]` 可选依赖」存在**隐藏耦合**：

- 前置依赖检查是在更早的「缺依赖无限刷屏」修复中新增的（见
  [voice-missing-deps-degrade-fix.md](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/fixlogs/voice-missing-deps-degrade-fix.md)），
  它用 `importlib.util.find_spec` 探测**真实是否安装** `dashscope`/`pyaudio`；
- 这几例单元测试的意图是验证 `voice_loop()` 的状态机与 `on_turn_end` 回调，
  已把音频层（`create_stt`/`CosyVoiceTTS`）打桩，**唯独没打桩依赖探测**；
- CI 不装 `[voice]`，探测失败 → 提前 return，测试断言的前置条件被破坏。

这不是生产代码缺陷，而是**测试未与可选依赖解耦**。

## 修复方案

在这两个测试文件中，对依赖探测打桩，让测试不依赖真实 `dashscope`/`pyaudio`，从而**真实覆盖
`voice_loop()` 完整流程**（此前因早退其实什么都没覆盖到）：

| 文件 | 改动 |
|---|---|
| [test_voice_loop_turn_end.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/voice/test_voice_loop_turn_end.py#L60-L96) | 在 `_run_voice_loop()` 内追加 `monkeypatch.setattr(vl, "_VOICE_REQUIRED_MODULES", ())`（`vl` 由 `sys.modules["agent.voice.voice_loop"]` 取得） |
| [test_voice_loop_responsiveness.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/voice/test_voice_loop_responsiveness.py#L187-L205) | `TestVoiceLoopStopDuringListen::test_stop_event_ends_session_and_loop_free` 内同样打桩探测清单 |

选择「把探测清单置空」而非「在 CI 里硬装 `[voice]`」的原因：

- `pyaudio` 在新 Python（3.13/3.14）上需本地编译 PortAudio，CI 装机脆弱、拖慢流水线；
- 这些测试**本就不该依赖真实音频栈**（stt/tts 已打桩），探测同属「外部环境」，应一并打桩；
- 打桩后测试语义更纯粹：验证的是 `voice_loop` 逻辑，而非「CI 装没装 extras」。

**踩坑记录**：第一版用 `import agent.voice.voice_loop as vl` 取模块，结果 `vl` 是**函数**
——`agent/voice/__init__.py` 用同名函数 `voice_loop` 重导出，遮蔽了模块属性。改用
`sys.modules["agent.voice.voice_loop"]` 取模块本体才能 patch 到全局变量。

## 验证结果

1. 本地常跑（本机装了 `[voice]`）：`pytest tests/voice/test_voice_loop_turn_end.py tests/voice/test_voice_loop_responsiveness.py -v`
   → **8 passed**。
2. **模拟 CI 无依赖环境**：临时 pytest 插件把 `dashscope`/`pyaudio` 的 `find_spec` 强制返回 `None`
   （`-p ci_dep_blocker`），再跑同两个文件 → **8 passed**，证明修复在无 `[voice]` 的 CI 下同样成立
   （插件为一次性验证物，已删除，未入库）。
3. 全量语音用例：`pytest tests/voice -q` → **256 passed**，无回归。
4. 「假通过」用例 `test_on_turn_end_skipped_when_no_message_growth` 现在**真实执行**完整循环后仍需
   `calls == []`，由假绿转为真绿。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `tests/voice/test_voice_loop_turn_end.py` | `_run_voice_loop()` 中打桩 `_VOICE_REQUIRED_MODULES = ()`，使 3 例在无 `[voice]` 环境正常执行 |
| `tests/voice/test_voice_loop_responsiveness.py` | `TestVoiceLoopStopDuringListen` 用例中同样打桩依赖探测 |
| `docs/fixlogs/voice-loop-tests-missing-deps-fix.md` | 本复盘文档 |

## 经验总结

- **功能入口新增的「环境探测」必须同步在测试里打桩**：给生产代码加了 `find_spec` / 环境变量 /
  文件存在性之类的 preflight 后，凡是通过该入口的既有测试都要检查一遍，否则会引入
  「本地过、CI 挂」的隐藏耦合。
- **「本地绿、CI 红」优先怀疑环境差异**：本机装了 `[voice]`，CI 只装 `[dev]`，正是典型场景。
  排查时先对齐「CI 装了什么」，再决定是补依赖还是打桩——能用打桩就不该硬装重依赖。
- **早退会制造「假通过」**：依赖缺失导致函数提前 return 时，断言「什么都没发生」的用例会意外成立。
  改完后应复核同文件其他用例是否也存在同类假绿。
- **重导出的同名符号会遮蔽模块**：`from pkg import sub` 与 `pkg.sub` 语义不同；`import pkg.sub as x`
  在包用同名函数重导出时会拿到函数。需要 patch 模块级全局时，统一走 `sys.modules[...]` 取模块本体。
