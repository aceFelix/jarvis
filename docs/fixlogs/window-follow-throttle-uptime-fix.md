# 窗口跟随首次动作被节流吞掉（Linux CI uptime 回归）修复复盘

> 发现方式：GitHub Actions CI（推 `6a22097` 后红掉），非人工测试。
> 修复版本：master（2.5.1 之后的工作区，下一次发版为 2.5.2）。
> 作者：aceFelix

---

## 1. 问题现象

推 `6a22097`（仅补交 AOCI 基线，**没有任何业务代码改动**）后，CI 的 `Run tests`
在 Linux 上失败，`tests/core/test_window_follow.py` 三条用例报错：

```text
FAILED tests/core/test_window_follow.py::test_file_write_opens_folder_but_read_does_not
  TypeError: 'NoneType' object is not subscriptable
FAILED tests/core/test_window_follow.py::test_folder_open_is_throttled_per_directory
  AssertionError: assert None is not None
  E  where None = on_file_changed('C:/proj/a.txt', 'write')
FAILED tests/core/test_window_follow.py::test_relative_file_path_uses_workdir
  AssertionError: assert ('open', '/home/runner/work/jarvis/jarvis/C:/proj/a.txt') in []
================= 3 failed, 4508 passed, 55 skipped in 38.02s =================
```

关键特征：

- 同一份代码上一次 CI（`e63686c`）是绿的，这次只多了一笔 `.aoci/baseline.json`；
- 失败点全部落在 `WindowDirector.on_file_changed(...)` 返回 `None`，即"目录定位被节流"；
- 本地 Windows 全量测试一直通过，无法复现。

## 2. 排查过程

### 2.1 第一阶段：先排除"代码真的坏了"

`on_file_changed` 有三个早退分支（`window_follow=off`、非 `write/edit`、空路径）。
测试注入的 `FakeBackend.supported()` 返回 `True`，`window_follow="show"`，
前两个分支都不会命中，所以怀疑落在 `_show_dir` 的节流判断上。

### 2.2 第二阶段：对比两次 CI 差异

`e63686c` 绿、`6a22097` 红，而两次的 `agent/core/window_follow.py` 字节完全一致，
说明不是回归代码本身，而是**运行环境的时间基准**在变。

### 2.3 第三阶段：定位到 `time.monotonic()` 的语义差异

节流实现是：

```python
now = time.monotonic()
if now - self._last_dir.get(folder, 0.0) < self.dir_throttle_s:
    return None
```

`time.monotonic()` 在 Linux 上是**开机以来的秒数**；GitHub runner 每跑一个 job 都是
一台刚启动的虚拟机。测试跑到窗口跟随用例时，机器 uptime 很可能只有几十秒，
于是 `now - 0.0` 直接小于 `dir_throttle_s`（默认 60 秒），首次定位目录被误判成
"60 秒内重复"而返回 `None`。Windows 上 `monotonic()` 是自开机起算且本机已运行很久，
所以本地永远复现不了。

### 2.4 第四阶段：本地复现

把 `time.monotonic` 打桩成固定的小值，直接调 `on_file_changed`：

```text
uptime=   0.5s -> result=None, calls=[]
uptime=   3.0s -> result=None, calls=[]
uptime=  30.0s -> result=None, calls=[]
```

`uptime >= 60s` 时恢复正常。根因确认：**拿 `0.0` 当"从未执行"的哨兵值**，
在时钟起点小于节流阈值时把"第一次"误算成"刚刚做过"。

同类问题也存在于 `_show_record` 的 5 秒窗口节流，只是阈值更小、更难撞上。

## 3. 根因分析

- **错误假设**：`time.monotonic()` 的起点总是远大于节流阈值。
  Windows 上成立（开机已久），Linux CI 新虚拟机上不成立。
- **反模式**：用 `dict.get(key, 0.0)` + 时间差比较来表示"是否执行过"。
  `0.0` 与真实时钟值处在同一个值域里，无法区分"没做过"和"在时刻 0 做过"。
- **影响面**：
  - CI：Linux 上 `test_window_follow.py` 随机红/绿，发版前的 `Test before publish`
    也会偶发失败（该 job 跑在 ubuntu 上），属于会阻塞发布的稳定性问题；
  - 产品：只影响"机器刚开机不到 60 秒就被 jarvis 写文件"这种极端场景，
    用户日常几乎碰不到；但 `_show_record` 的 5 秒节流在开机前 5 秒内也可能吞掉首次显示。
- **为什么之前没发现**：Windows 开发机 uptime 远大于阈值；CI 上是否命中取决于
  runner 启动耗时，属于概率性失败。

## 4. 修复方案

`agent/core/window_follow.py` 两处节流改为"**只对已记录过的键做时间差比较**"：
用 `dict.get(key)` 返回 `None` 表示从未执行，而不是 `0.0`。

```python
# 修复前
if now - self._last_dir.get(folder, 0.0) < self.dir_throttle_s:
    return None

# 修复后
last = self._last_dir.get(folder)
if last is not None and now - last < self.dir_throttle_s:
    return None
```

`_show_record` 的 `_last_show` 同样处理。语义保持：**首次一定放行，之后的重复仍节流**。

未引入 workaround，也不需要后续重构——这是该判断本身的正确写法。

## 5. 验证结果

- 本地复现脚本：`uptime=0.5s` 时修复前返回 `None`、修复后返回
  `{"action": "show_dir", "ok": True}`，`open_folder` 被调用一次。
- 新增两条回归用例：

  | 用例 | 覆盖点 |
  |---|---|
  | `test_first_show_is_not_throttled_on_small_clock` | 5 秒窗口节流首次放行 |
  | `test_first_folder_open_is_not_throttled_on_small_clock` | 60 秒目录节流首次放行，且后续仍节流 |

- `pytest tests/core/test_window_follow.py tests/core/test_window_observer.py`
  → **22 passed**。
- 未改动窗口跟随的对外行为，Windows 实机表现与 2.5.1 一致。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/window_follow.py` | 窗口显示 / 目录定位的节流判断改用 `None` 哨兵，首次动作永不节流 |
| `tests/core/test_window_follow.py` | 新增两条小 uptime 回归用例 |
| `docs/fixlogs/window-follow-throttle-uptime-fix.md` | 本复盘 |
| `CHANGELOG.md` | `[Unreleased]` 记录本次修复 |

## 7. 经验总结

- **时间节流不要用数值哨兵**：凡"没做过"用 `0.0` / `-1` 之类与真实时钟同值域的默认值，
  在 Linux（`monotonic` 自开机起算）和容器/CI 新机器上都会翻车。要么用 `None`，
  要么记录"首次执行"布尔标记。
- **"本地全绿、CI 偶发红"优先怀疑环境差异**：时区、时钟起点、平台路径语义、文件系统大小写。
  本案例就是时钟起点，不是业务逻辑。
- **只用时间差验证异步/节流行为容易抖**：此前已把 `WindowObserver` 的重试测试改成同步调用，
  这次同类问题再次出现——**凡是和真实时间比大小的逻辑，测试里一律打桩时钟**。
- 这类问题不写复盘就容易在下次发版前被当成"随机红"忽略，因此纳入 `docs/fixlogs/`。
