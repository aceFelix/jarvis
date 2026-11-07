# 通用窗口观察器（Window Observer）设计

> 定位：操作可见性的底层机制。取代"按工具名逐个登记是否跟随"的白名单做法。
> 状态：**已实施（2026-11-07）**。代码：`agent/core/window_observer.py`；
> `window_follow.py` 已删除全部工具白名单。
> 作者：aceFelix

---

## 1. 为什么要重做

当前实现有一张 `_FOLLOW_WINDOW_TOOLS` 白名单：只跟随鼠标、键盘、`WindowFocus` 等几个工具。
这带来三个问题：

1. **新增工具必须再改一次代码**：MCP 工具、插件工具、CLI-Anything harness、未来的子代理工具，全都要逐个登记；
2. **间接开窗的工具有缺口**：`Bash` 直接拉起记事本、MCP 在另一个进程里打开软件，白名单根本看不见；
3. **白名单本身在漂移**：`WindowClick`、`WindowMove` 这类明显该跟的工具反而漏了。

结论：**"是否需要跟随"不应该由工具名决定，而应该由"这次执行有没有改变窗口事实"决定。**

---

## 2. 核心原则

> 工具运行前后拍两次窗口快照，谁变了就跟谁；工具自己不需要声明、也不需要被登记。

### 2.1 三条判定依据（按优先级）

| 优先级 | 依据 | 动作 |
|---|---|---|
| 1 | 工具入参里有明确的窗口标题（`title` / `window` / `window_title`） | 跟随该窗口 |
| 2 | 工具入参里有文件路径，且这次是写/改 | 定位所在文件夹（节流） |
| 3 | 本轮新出现、可见、有标题、且**已成为前台**的窗口 | 跟随该窗口 |
| 4 | 以上都不满足 | 不动作 |

**不做的事**：不根据工具名判断；不跟随"已有窗口被用户自己切到前台"（无法与用户操作区分）；不跟随后台弹出但未抢焦点的窗口（更新提示、托盘气泡）。

### 2.2 为什么这样就覆盖了未来工具

- `Bash` 拉起 GUI → 新窗口变前台 → 规则 3 命中；
- MCP / harness / 插件在别的进程开软件 → 新窗口变前台 → 规则 3 命中；
- 未来任何"能开窗"的工具 → 同样命中；
- `WindowList` 只查询 → 无新窗口 → 不动作；
- `WindowClose` 关窗 → 窗口消失 → 不动作；
- `FileRead` → 不做目录定位 → 不动作。

**唯一需要特殊处理的是"在已有窗口里操作、但既不新增窗口、也不改变前台"的场景**（例如后台 UIA 自动化）。这类场景工具入参通常带窗口标题，命中规则 1。

---

## 3. 架构与模块

```
tool_before ──► WindowObserver.before(tool, input, ctx)
                   │  · 拍快照 A
                   │  · 记录 before_foreground
                   │  · 若 input 有 title/path：直接走规则 1/2（同步）
                   ▼
              工具执行（任意工具，不区分）
                   ▼
tool_after  ──► WindowObserver.after(tool_use_id)
                   │  · 延迟 probe_delay_ms 拍快照 B
                   │  · 取 foreground_B
                   │  · 规则 3 判定
                   │  · 未命中且 retry 开启：再延迟 probe_retry_ms 拍 C
                   ▼
              命中 → WindowDirector.show(hwnd)
              未命中 → 什么都不做
```

### 3.1 模块职责

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `agent/core/window_observer.py`（新增） | 快照差分、规则判定、探测调度、去重 | 不直接调 Win32、不写终端 |
| `agent/core/window_follow.py`（改造） | 执行窗口动作（show/focus/目录）、收尾清理 | 不再持有工具白名单 |
| `agent/core/win32_window.py`（复用） | 枚举/快照/前台/关闭 | 不做业务判断 |
| `agent/core/trace.py`（改造） | 把 observer 的判定结果写进事件流 | 不再维护 late-follow 工具表 |

### 3.2 关键数据结构

```python
@dataclass(frozen=True)
class WindowRecord:
    hwnd: int
    title: str
    rect: tuple[int, int, int, int]
    minimized: bool

Snapshot = dict[int, WindowRecord]   # hwnd → record

@dataclass
class ProbeState:
    tool_use_id: str
    tool: str
    before: Snapshot
    before_foreground: int | None
    started_at: float
    probed: bool = False
```

---

## 4. 判定算法（伪代码）

```python
def before(tool, tool_input, ctx):
    if not enabled or mode == "off":
        return
    state = ProbeState(
        tool_use_id=ctx.tool_use_id,
        tool=tool,
        before=backend.snapshot(),
        before_foreground=backend.foreground_hwnd(),
        started_at=now(),
    )
    pending[state.tool_use_id] = state

    # 规则 1/2：入参声明目标（同步执行，不等探测）
    title = first_of(tool_input, "title", "window", "window_title")
    if title:
        director.show_by_title(title)
        return
    path = first_of(tool_input, "file_path", "filePath", "path")
    if path and operation_is_write(tool_input):
        director.show_dir(path, workdir=ctx.workdir)

def after(tool_use_id):
    state = pending.pop(tool_use_id, None)
    if state is None:
        return
    schedule_probe(state, delay=probe_delay_ms)

def probe(state):
    after = backend.snapshot()
    new = set(after) - set(state.before)
    fg = backend.foreground_hwnd()

    # 只认：新出现 + 可见 + 有标题 + 当前前台
    for hwnd in new:
        rec = after[hwnd]
        if rec.minimized or not rec.title:
            continue
        if hwnd == fg:
            director.show(hwnd)
            return
    # 第一次没命中，重试一次
    if not state.probed:
        state.probed = True
        schedule_probe(state, delay=probe_retry_ms)
```

**判定顺序的重要性**：先看入参声明，再看窗口事实。
原因是 `WindowFocus` 这类工具执行很快，快照差分可能只看到"前台变了、但没有新窗口"。

---

## 5. 与清理逻辑的合并

收尾清理不再单独维护 `_involved`，直接复用 observer 的 `followed_hwnds`：

| 集合 | 来源 | 用途 |
|---|---|---|
| `before` | 回合开始时快照 | 用户原有窗口，永不关闭 |
| `followed_hwnds` | observer 每次命中 | 本轮真正操作过的窗口 |
| `last_step_new` | 最后一步的新窗口 | 视为最终结果，保留 |
| `foreground_at_end` | 回合结束时前台 | 用户正在看，保留 |
| `untitled/minimized` | 快照属性 | 不跟随、不关闭 |
| `title contains *` | 未保存标记 | 不关闭，上报 |

清理对象 = `followed_hwnds ∩ (当前快照 - before) - keep`。

---

## 6. 时序与去重

| 参数 | 默认 | 说明 |
|---|---|---|
| `window_probe_delay_ms` | 450 | 工具结束后首次探测延迟；GUI 窗口通常 0.2–0.8s 出现 |
| `window_probe_retry_ms` | 1200 | 首次未命中后的第二次探测 |
| `window_follow_hold_ms` | 1200 | 置顶保持时长（沿用现有） |
| 同 hwnd 去重 | 5s | 避免连续点击反复置顶 |
| 同目录定位 | 60s | 沿用现有 |

> 探测只跑在有窗口跟随的前提下；`window_follow = off` 时**零快照、零线程**。

---

## 7. 边界与降级

| 场景 | 行为 |
|---|---|
| 无头浏览器 | 无窗口可跟；只记录 URL/抓取事件（爬虫可视化另立文档） |
| 虚拟桌面里的窗口 | 无法搬移，终端提示一次，不重复 |
| 前台锁拒绝激活 | `focus` 档失败只提示，不改用强抢 |
| 子代理并发 | v1：子代理的 trace 照记，但**窗口跟随只对主代理生效**（配置 `window_follow_subagents=false`） |
| 用户中途切窗口 | 不跟随已有窗口（规则 3 只认"新出现"），避免夺屏 |
| 后台弹窗 | 不跟随（未成为前台） |
| 工具入参无标题、无窗口变化 | 不动作，静默 |

---

## 8. 配置项（`[ui]`）

| 键 | 默认 | 说明 |
|---|---|---|
| `window_follow` | `show` | 总开关与档位（off/show/focus） |
| `window_probe_delay_ms` | 450 | 首次探测延迟 |
| `window_probe_retry_ms` | 1200 | 二次探测延迟（0 = 不重试） |
| `window_follow_subagents` | `false` | 子代理是否也跟随 |
| `window_cleanup` | `transient` | 收尾清理（沿用） |

---

## 9. 改造清单

| 文件 | 改动 |
|---|---|
| `agent/core/window_observer.py` | 新增：快照差分、规则、探测调度 |
| `agent/core/window_follow.py` | 删除 `_FOLLOW_WINDOW_TOOLS` / `_IGNORE_TOOLS` / `_FOREGROUND_TOOLS`；新增 `show_hwnd`、`show_by_title`、`show_dir`；保留清理 |
| `agent/core/trace.py` | 删除 `_LATE_FOLLOW_TOOLS` / 延迟 Timer；改为调用 observer；保留事件渲染 |
| `agent/core/win32_window.py` | 快照改为返回 `WindowRecord`（含 rect/minimized），`snapshot()` 签名升级 |
| `agent/config/settings*.py` / `catalog.py` / `settings.example.toml` | 新增 3 个配置项与说明 |
| `tests/core/test_window_observer.py` | 新增：快照脚本化测试（见 §10） |
| `tests/core/test_window_follow.py` | 删除白名单相关用例，保留清理用例 |

---

## 10. 测试矩阵（必须覆盖）

| 用例 | 脚本 | 期望 |
|---|---|---|
| 无窗口变化 | 前后快照相同 | 不调用 show |
| 新窗口 + 变前台 | 快照新增 hwnd，foreground=新 hwnd | show(new) |
| 新窗口但后台 | 快照新增，foreground 仍为终端 | 不动作 |
| 入参带 title | 无新窗口 | show(title) |
| 文件写入 | 入参 file_path | show_dir（节流） |
| 用户切已有窗口 | 前后快照相同，foreground 变了 | 不动作 |
| 首次探测未命中 | 首次无新窗口，二次有 | 二次命中 |
| off 模式 | — | snapshot 调用次数 = 0 |
| 子代理 | ctx 标记 subagent | 不跟随、只记录 |
| 清理 | followed={2}, 最后一步={3}, 前台=4 | 只关 2，保留 3/4 |
| 未保存 | 标题含 `*` | 不关、上报 |
| 定时器竞态 | 工具结束后立刻进入下一工具 | 旧探针取消、不串窗 |

---

## 11. 验收标准

- [ ] 代码中**不存在**"工具名 → 是否跟随"的映射表；
- [ ] `Bash` 直接启动记事本，记事本能被跟随；
- [ ] MCP/harness 工具开窗（若可实测）同样被跟随；
- [ ] `WindowList` / `WindowClose` / `ScreenShot` 不产生窗口动作；
- [ ] 用户自己切窗口不会触发跟随；
- [ ] `window_follow=off` 下快照调用为 0；
- [ ] 收尾清理仍遵守"只关本轮新开、未保存跳过、最终/前台保留"；
- [ ] 全量相关测试通过。

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 判定误跟用户窗口 | 只认"新出现 + 变前台"；已有窗口仅在入参声明时跟 |
| 探测拖慢工具 | 快照只在 follow 开启时拍；EnumWindows 毫秒级；探测在后台线程 |
| 多工具并发串窗 | 按 `tool_use_id` 隔离 ProbeState；超时丢弃 |
| 子代理抢屏 | v1 只允许主代理跟随 |
| 平台差异 | 非 Windows 直接 off，不注册 observer |

---

## 13. 与现有文档的关系

- 本文取代 `operation-visibility.md` §6.1 的工具映射表（已实施并删除白名单）；
- §6.3（show 算法）、§6.5（节流）、§6.6（失败降级）、§6.7（收尾清理）仍然有效；
- 本文只改"什么时候跟"，不改"怎么显示"。

## 14. 实施备注（2026-11-07）

| 设计项 | 实现落点 |
|---|---|
| 快照差分 | `WindowObserver.before/after/_probe`，快照为 `hwnd → WindowInfo` |
| 入参窗口线索 | `_declared_title()` 扫描 `title/window/window_title`，不区分工具名 |
| 文件定位 | 仍在 `TraceRecorder.on_file_changed` → `WindowDirector.on_file_changed` |
| 子代理判定 | 通过 `ctx.ui.activity_id` 识别（终端无该属性，照常跟随） |
| 探测与重试 | `window_probe_delay_ms` / `window_probe_retry_ms`，后台 daemon Timer |
| 清理 | `WindowDirector.close_transients` 共用观察器的 followed hwnd 集合 |
| 测试 | `tests/core/test_window_observer.py` 7 例 + 既有 trace/follow 用例 |
