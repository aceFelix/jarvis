# CI 上的 4 个失败：Python 3.14 口径变化、一处假绿、缺依赖未跳过

> **类型**：CI 兼容性修复（含一条测试有效性缺陷）
> **触发**：GitHub Actions 在 Python 3.14.9 上跑 `pytest tests/ -v` 报 4 failed
> **修复日期**：2026-10
> **改动文件**：`agent/core/policy/budget.py`、`tests/tools/test_email_send_now.py`、
> `tests/voice/test_wake_words.py`

---

## 0. 一句话结论

4 个失败有两类：两个是 **Python 3.14 改了标准库口径**（本地 3.13 永远复现不了），
两个是 **CI 没装语音依赖而测试没声明 skip**。其中 email 那条尤其值得记：
它在 3.13 上一直是**假绿**，从没真正验过要验的东西。

---

## 1. 失败清单

```
FAILED tests/policy/test_notify_budget.py::test_parse_clock_accepts_and_rejects
FAILED tests/tools/test_email_send_now.py::test_tool_error_becomes_value_error
FAILED tests/voice/test_wake_words.py::TestStandbyWakeIntegration::test_daily_talk_does_not_wake_from_standby
FAILED tests/voice/test_wake_words.py::TestStandbyWakeIntegration::test_wake_hint_uses_configured_word
```

---

## 2. 失败一：`time.fromisoformat("24:00")` 在 3.14 变成了合法输入

**现象**

```
assert parse_clock("24:00") is None
AssertionError: assert datetime.time(0, 0) is None
```

**根因**

`parse_clock` 原实现是「先试标准 ISO，失败再走容错正则」：

```python
    try:
        return dtime.fromisoformat(s)     # ← 3.14 在这里就把 "24:00" 收下了
    except ValueError:
        pass
    m = _HM_RE.fullmatch(s)               # 走不到了
    ...
    if hour > 23 or minute > 59 or second > 59:   # 这道检查被绕过
        return None
```

ISO 8601 允许 `24:00` 表示「当日结束 / 次日零点」，Python 3.14 的
`time.fromisoformat` 开始接受它并**归一化成 `00:00`**。于是：

| 版本 | `fromisoformat("24:00")` | `parse_clock` 结果 |
|---|---|---|
| 3.13 | 抛 ValueError → 落到正则分支 | `None`（符合契约） |
| 3.14 | 返回 `time(0, 0)` | `00:00`（契约是 None） |

本地是 3.13，所以这个问题在本地永远不会红。

**修复**：把顺序反过来——**先用正则 + 手工范围检查，正则不匹配才退回标准 ISO**。

```python
    m = _HM_RE.fullmatch(s)
    if m:
        hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if hour > 23 or minute > 59 or second > 59:
            return None
        return dtime(hour, minute, second)
    # 非 H:MM[:SS] 形态才交给标准 ISO（如 24:00.500 这类带小数的写法）
    try:
        return dtime.fromisoformat(s)
    except ValueError:
        return None
```

`_HM_RE.fullmatch("24:00")` 命中 → `hour=24 > 23` → `None`，**与标准库版本无关**。

**排查范围**：全仓另外 12 处 `fromisoformat` 都是 `datetime` / `date` 那种
（要求带日期、输入也是自己 `isoformat()` 出来的），不受这条口径变化影响。

---

## 3. 失败二：一条从没真正生效过的测试

**现象**

```
res = asyncio.run(SendEmailTool().call(dict(args), ctx))
TypeError: An asyncio.Future, a coroutine or an awaitable is required
```

**根因**：测试把 `call` 换成了同步 lambda：

```python
monkeypatch.setattr(
    SendEmailTool, "call",
    lambda self, args, ctx: ToolResult.error("邮件功能未启用"),   # 不是协程
)
```

于是 `asyncio.run(ToolResult)`。两个版本对「传了个非协程」的反应不同：

| 版本 | 抛什么 | 结果 |
|---|---|---|
| 3.13 | `ValueError("a coroutine was expected, got ToolResult(data='邮件功能未启用', ...)")` | 异常**类型**是 ValueError、**消息**里含 `邮件功能未启用`（在 repr 里）→ `pytest.raises(ValueError, match="邮件功能未启用")` **碰巧通过** |
| 3.14 | `TypeError(...)` | 不匹配 ValueError → 红 |

也就是说：**这条测试从写下那天起就没测过 `send_email_now` 的错误转换**，它测到的
是「`asyncio.run` 收到非协程时抛的错误消息里，恰好包含了工具错误原文」。

**修复**：替身改成 `async def`，让它真的是协程，`asyncio.run` 才会真正执行
`SendEmailTool.call` 并返回 `ToolResult`，再由 `send_email_now` 转成 ValueError。

**这类假绿的识别信号**：`pytest.raises(ValueError, match=...)` 里的 `match`
如果恰好是某个对象的字段值，而这个对象当时正被当作异常消息的一部分打印出来，
那它可能一直在自我印证。

---

## 4. 失败三、四：缺依赖没跳过

**现象**

```
assert '星期五' in '语音功能依赖未安装: dashscope, pyaudio (...)'
ValueError: list.index(x): x not in list
```

**根因**：`TestStandbyWakeIntegration` 真跑 `voice_loop`，而 `voice_loop` 开头就检查
`_VOICE_REQUIRED_MODULES = ("dashscope", "pyaudio")`，CI 没装便直接返回
「语音功能依赖未安装」——状态序列和播报文案都不会产生。

类里 4 个用例只给 2 个挂了 `@requires_pinyin`。CI 恰好也没装 pypinyin，于是那 2 条
「顺带」被跳过；剩下 2 条就红了。**跳过条件挂错了对象**：真正的前提是语音运行时依赖，
pypinyin 只是其中一项能力的额外依赖。

**修复**：补一个类级标记，把判据对准真正的前提：

```python
_VOICE_MODULES = ("dashscope", "pyaudio")
_MISSING_VOICE = tuple(m for m in _VOICE_MODULES if importlib.util.find_spec(m) is None)
requires_voice_runtime = pytest.mark.skipif(
    bool(_MISSING_VOICE),
    reason=f"语音运行时依赖未安装: {', '.join(_MISSING_VOICE)}（pip install \"jarvis-agent[voice]\"）",
)

@requires_voice_runtime
class TestStandbyWakeIntegration:
    ...
```

本地装了这些依赖，所以本地不 skip、照跑（保证用例本身没被改坏）。

---

## 5. 修复后的 CI 预期

原来的分布：`3842 passed + 34 skipped + 4 failed = 3880`
修复后应为：`3844 passed + 36 skipped + 0 failed`

（两条 failure 转 skip，两条转 pass。）

```powershell
python -m pytest tests -q   # 本地 3.13：3880 passed（依赖齐全，不 skip）
```

---

## 6. 教训

- **本地全绿 ≠ CI 全绿，前提是两边版本相同。** 这次两次失败都源于「标准库在
  3.13 → 3.14 之间放宽了输入」，本机根本复现不了。涉及「解析用户手写文本」的地方，
  **不要依赖标准库的宽严口径**，自己卡住契约需要的范围。
- **`pytest.raises(X, match=...)` 的 match 有可能被「无关的 repr」满足。**
  当被误当成异常对象的那个值本身携带目标文本时，测试会假绿得很安静。
  判断测试是否真的生效，看的是「被测函数是否真的执行到了」，不是「断言有没有过」。
- **skip 条件的判据要对准真正的前提。** 拿「pypinyin 没装」去覆盖「pyaudio 没装」
  的用例，只是巧合在 CI 上成立；CI 哪天装了 pypinyin，这两条会原样红回来。
  声明依赖要按**这个测试真跑起来需要什么**来写。
- **CI 用新版本跑是好事。** 这条 Python 3.14 的红，暴露了一个从未生效的测试
  ——它比「CI 一直绿」有价值得多。
