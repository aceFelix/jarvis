# 修复：缺语音依赖时 `/voice` 无限刷屏（降级缺失）

- **日期**：2026-10-11
- **作者**：aceFelix
- **影响范围**：`agent/voice/voice_loop.py`（终端 `/voice`、serve / 桌面壳语音路径共用）
- **严重级别**：中（无数据损坏，但持续刷屏、用户无法正常退出到文本模式，体验很差）

## 现象

用户在**未安装 `[voice]` 可选依赖**的 jarvis 安装态（PyPI 2.1.1，仅核心包）里执行 `/voice`：

```
🎙️  语音对话模式已开启
🎤 聆听中...（说话即可，停顿后自动结束）
❌ 识别失败: dashscope 未安装: No module named 'dashscope'，再说一次
🎤 聆听中...（说话即可，停顿后自动结束）
❌ 识别失败: dashscope 未安装: No module named 'dashscope'，再说一次
...（无限重复，刷满整个屏幕）
```

复现步骤：

```bash
# 只装核心包（不含 [voice]）
pip install jarvis-agent
jarvis
> /voice
# 观察：进入语音模式后进入聆听→识别失败的死循环，持续刷屏
```

影响范围：所有缺 `dashscope`/`pyaudio` 的环境（核心包装机、extras 未选全）；终端 `/voice` 与
serve / 桌面壳语音路径都受影响。

## 排查过程

1. **定位报错来源**：日志里错误文案是 `dashscope 未安装: No module named 'dashscope'`。
   grep 发现它来自 [the STT 回调](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/stt/qwen.py#L395)
   与 [TTS 回调](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/tts.py#L423) 的
   `except ModuleNotFoundError` 分支，被包成 `result["error"]` 返回。
2. **确认「为什么没被 import 拦下」**：`voice_loop()` 进入前有
   `try: from agent.voice import create_stt` 的 ImportError 兜底，但 `dashscope` 是**方法内懒导入**
   （[qwen.py:48](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/stt/qwen.py#L48)、
   [tts.py:35](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/tts.py#L35)），
   包 import 阶段**不会报错**，`create_stt()` 也照常返回对象——所以兜底没生效，直接进了循环。
3. **确认「为什么会无限循环」**：[`_voice_loop_round`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/voice_loop.py#L213-L220)
   把 `result["error"]` 一律当作「本轮偶发识别失败」`return True`，外层 `while cont` 立即再来一轮，
   **无任何退避/重试上限**。依赖缺失是**永久性错误**（每轮必失败），于是形成死循环刷屏。
4. **确认干扰项**：日志首行的 `⚠ keyboard 库未安装，ESC 打断不可用` 是正常提示（缺 `keyboard`
   只影响 ESC 打断，不影响流程），与本次 bug 无关，不改。

## 根因

两层问题叠加：

1. **缺少前置依赖检查**：`dashscope`/`pyaudio` 属 `[voice]` 可选依赖，缺失时未在进入语音模式前拦截。
2. **错误处理把「永久性错误」当「瞬时错误」**：识别失败一律 `return True` 重试，且无退避，
   依赖缺失这种必然失败的错误导致无限循环刷屏。

## 修复方案

两处最小改动（[voice_loop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/voice_loop.py)）：

1. **前置依赖检查（fail-fast，主修复）**：`voice_loop()` 里用
   `importlib.util.find_spec` 探测 `dashscope` + `pyaudio`（不实际 import，避免副作用），
   缺失则打印「依赖未安装 + `pip install "jarvis-agent[voice]"`」并 **直接 return**，
   不进入循环、不占语音锁。
2. **永久性错误升级为致命错误**：新增 `_VoiceDependencyError`；`_voice_loop_round()` 中若
   `result["error"]` 含「未安装」或 `No module named`，`raise` 该异常；`voice_loop()` 捕获后
   干净退出并给安装指引（**不**当作「退下」进待机——待机同样会反复失败）。普通瞬时错误
   （网络超时等）行为不变，仍继续下一轮。

选择「前置检查 + 永久错误上抛」而非「加退避重试」的原因：依赖缺失**无法自愈**，重试再多次
也不会成功，只会拖慢刷屏节奏；应快速失败给用户明确指引，这才是正确的降级。

## 验证结果

- `python -m py_compile agent/voice/voice_loop.py`：exit 0。
- 新增回归测试 `tests/voice/test_voice_dependency_preflight.py`（3 例）：
  - 缺依赖时 `voice_loop` 前置拒绝、输出安装指引、**不进入状态机**（`states() == []`）；
  - `_voice_loop_round` 遇缺失错误上抛 `_VoiceDependencyError`；
  - 瞬时错误仍 `return True` 继续聆听。
- `pytest tests/voice -q`：**256 passed**，无回归。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/voice/voice_loop.py` | 新增 `_VoiceDependencyError` 与 `_VOICE_REQUIRED_MODULES`；`voice_loop()` 增加 dashscope/pyaudio 前置依赖检查；`_voice_loop_round()` 将依赖缺失错误升级为致命异常；`voice_loop()` 捕获并干净退出 |
| `tests/voice/test_voice_dependency_preflight.py` | 新增：缺依赖降级的 3 条回归测试 |
| `docs/guide/voice.md` | `/voice` 章节补充「依赖兜底」说明 |
| `docs/fixlogs/voice-missing-deps-degrade-fix.md` | 本复盘文档 |

## 经验总结

- **懒导入会绕过 import 兜底**：把第三方依赖推迟到方法内 `import`，虽然能加快启动、避免包级
  副作用，但也让「包能不能 import」不再等价于「功能能不能用」。凡是依赖懒导入的可选能力，
  都应在**功能入口处**用 `importlib.util.find_spec` 主动做前置检查。
- **重试只对「可能自愈」的错误有意义**：网络抖动、临时限流可重试；依赖缺失、配置错误、
  鉴权失败是永久性的，重试只会刷屏。错误处理应先分类「瞬时 / 永久」，永久性错误快速失败。
- **降级 ≠ 沉默吞错**：正确的降级是「明确告诉用户缺什么、怎么修，然后干净退出」，
  而不是把错误吞成一次普通失败继续空转。
- **本地装包自测要覆盖「缺 extras」场景**：核心包 + 未装 extras 是最容易被忽视的路径，
  本次即为该场景暴露的缺陷，后续自测安装包时应专门走一遍。
