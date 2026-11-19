# 实现方案：画像记忆 P0（增量提炼 + 三触发）与 P4（评测集）

> 状态：**步骤 1-3 已落地（其中步骤 3 已于 2026-11-17 下线），步骤 4（文档收尾与提交）已由后续提交覆盖**　作者：aceFelix　日期：2026-11-14
>
> **2026-11-17 修订**：空闲触发（`profile_idle.py`）已整体移除；默认 `profile_refine_min_delta` 6→**7**；
> **成功不再按时间节流**（`profile_refine_interval` 只用于失败退避）；退出路径最多等 15 秒把后台提炼跑完。
> 本文其余内容为历史实现记录，现行口径见 `09-记忆与压缩.md`。
> 上位文档：[roadmap/memory-config/PLAN-profile-memory-upgrade.md](../../roadmap/memory-config/PLAN-profile-memory-upgrade.md) §P0 / §P4
> 本文只写"落到函数签名与文件"的细节；设计取舍、成本账、P1-P5 不在本文范围。

## 一、范围

**做**：

| 编号 | 内容 | 产出 |
|---|---|---|
| P0a | 提炼水位台账：只喂增量、记录 `refined_until` | `profile_ledger.py` + 接线 |
| P0b | 三触发：阈值（delta）/ 终态（`force`）/ 空闲（Scheduler 一次性任务） | `profile_idle.py` + 宿主接线 |
| P4 | 评测集：golden 用例钉住增量、幂等、失败不推进 | 2 个测试文件 |
| 配套 | 3 个配置键接入真源表与两份模板 | settings / domains / specs / toml |

**不做**：P1 四操作与别名、P2 lifecycle/事件轨、P3 向量、P5 RecallProfile。这些依赖本文的台账落地后再开。

## 二、现状事实（改动前坐标）

| 事实 | 位置 |
|---|---|
| 每轮对话后调用提炼 | `agent/main.py`（REPL 每轮 `_auto_save`）、`agent/serve/engine/engine.py:596` |
| 节流与门槛 | `agent/core/memory/profile_refiner.py`：`REFINE_INTERVAL_SECONDS=600`、`profile_refine_min_messages=6`、模块级 `_in_flight`；节流时间戳存台账（`last_attempt_ts`） |
| 全文重喂 | `maybe_refine_async` → `refine_session(list(messages))` → `_collect_dialog_text`（最近 40 条） |
| `_auto_save` 签名 | `agent/session_manager.py:205`，末尾调 `maybe_refine_async(messages, session_name, settings)` |
| 可用 Scheduler | `CliReminderHost.scheduler`（`cli_reminder.py:61`）、`ProactiveHub.scheduler`（`hub.py:194`） |
| 文件锁 | `agent/utils/filelock.py:file_lock(lock_path, max_retries=30)`，拿不到抛 `TimeoutError` |
| 配置真源 | `ConfigSpec(key, section, field, attr, kind, label, hint, tier, apply_mode, …)`（`config_specs.py:95`） |
| `[memory]` 映射 | `settings_domains.py:138` 元组 |
| 现有测试 | `tests/core/test_profile_memory.py`（69 例）、`test_profile_bridge.py` |

## 三、数据结构

### 3.1 水位台账 `~/.jarvis/memory/refine_ledger.json`

```json
{
  "version": 1,
  "sessions": {
    "jarvis-国庆期间会下雨吗": {
      "refined_until": 42,
      "last_msg_ts": 1794550000.0,
      "last_refine_ts": 1794549400.0,
      "last_error": ""
    }
  }
}
```

- `refined_until`：已提炼到的**消息下标**（`messages[refined_until:]` 是待处理增量）。
- `last_msg_ts`：最近一次调用 `maybe_refine_async` 的时间（由每轮 `_auto_save` 刷新），空闲判定用。
- `last_refine_ts`：最近一次**成功或可推进**的提炼时间，替代原模块级 `_last_refine_ts`（跨进程一致）。
- `last_error`：最近一次失败原因（只用于诊断，不参与判定）。
- 上限 **50 个会话**，按 `last_msg_ts` LRU 淘汰；文件损坏 → 当空表（退化为"全量提炼一次"再重建水位，方向安全）。
- 写入走 `file_lock` + 临时文件 + `os.replace`；拿锁超时 → 记 `diag_warn` 并**只更新内存**，不阻塞提炼。

### 3.2 `agent/core/memory/profile_ledger.py`（新，约 150 行）

```python
@dataclass
class RefineState:
    refined_until: int = 0
    last_msg_ts: float = 0.0
    last_refine_ts: float = 0.0
    last_error: str = ""

def ledger_path() -> Path
def load_state(session_id: str) -> RefineState
def save_state(session_id: str, state: RefineState) -> None
def touch(session_id: str, msg_count: int, *, now: float | None = None) -> RefineState
    """每轮调用：刷新 last_msg_ts；若 msg_count < refined_until（/rewind、/compact 截断）→ 水位重置为 msg_count。"""
def pending_delta(session_id: str, msg_count: int) -> int
    """max(0, msg_count - refined_until)；负数已在 touch 里处理。"""
def advance(session_id: str, refined_until: int, *, now: float | None = None) -> None
    """成功推进：写 refined_until / last_refine_ts，清 last_error。"""
def mark_error(session_id: str, error: str) -> None
def idle_sessions(*, now: float, idle_minutes: int, min_delta: int, counts: dict[str, int]) -> list[str]
    """纯函数：返回满足 `now-last_msg_ts ≥ idle` 且 `pending_delta ≥ min_delta` 的会话。"""
def prune_sessions(max_sessions: int = 50) -> int
def reset_for_tests(path: Path | None = None) -> None   # 测试注入路径用
```

**关键不变式**：只有 `advance` 能增大 `refined_until`；`touch` 只在消息数变少时下调它。

### 3.3 `agent/core/memory/profile_idle.py`（新，约 110 行）

```python
_scheduler: Scheduler | None
_settings: object | None

def bind_scheduler(scheduler, settings) -> None
    """宿主启动时调一次；重复绑定以最后一次为准（记 diag_warn）。不启动任何线程。"""
def schedule_idle_check(session_id: str, *, last_msg_ts: float) -> None
    """注册/替换一次性任务 `profile-idle:<sid>`，触发时刻 = last_msg_ts + idle_minutes。"""
def cancel_idle_check(session_id: str) -> None
def _fire(session_id: str) -> None
    """到点：重读台账；条件不满足（delta 不足 / 未真空闲）就重新排下一次；满足则
    `maybe_refine_async(messages=None, ...)` 不可行 → 改为读缓存消息快照后触发（见 §4.3）"""
```

> **消息从哪来**：Scheduler 回调里没有对话列表。方案：`profile_idle` 维护
> `bind_messages_provider(callable) -> list[Message]`，由宿主注入"取当前消息"的只读回调
> （REPL 传 `lambda: messages`，engine 传 `lambda: self._messages`）。回调抛异常 → 放弃本次空闲触发
> 并记 diag，不重试。

### 3.4 `maybe_refine_async` 新签名（`profile_refiner.py`）

```python
def maybe_refine_async(
    messages: list[Message],
    session_id: str,
    settings: Any,
    *,
    force: bool = False,
) -> bool:
```

流程：

```
sid = session_id or "default"（空值记 diag_warn 一次）
total = len(messages)
state = touch(sid, total)                     # 刷新 last_msg_ts + 处理截断
delta = max(0, total - state.refined_until)
gate_min = int(profile_refine_min_delta or 6)
if delta < gate_min:
    if force and delta >= 1: 继续        # 终态触发只要求有新消息
    else: 返回 False
if not force and now - state.last_refine_ts < profile_refine_interval: 返回 False
if not _in_flight.acquire(blocking=False): 返回 False
slice_ = list(messages[state.refined_until:])
worker: report = refine_session(slice_, sid, settings)
        if report.error == "": advance(sid, total)
        else: mark_error(sid, report.error)
```

- `_last_refine_ts` 模块级变量**删除**，改读台账（跨进程一致）。
- `_in_flight` 保留（进程内并发保护）。
- `force=True` 时仍要求 `delta ≥ 1`（没有新消息就不提炼）。

### 3.5 `RefineReport` 增加 `advanceable`

```python
@dataclass
class RefineReport:
    added: int = 0
    updated: int = 0
    merged: int = 0
    skipped: bool = False
    advanceable: bool = False   # 新增：本轮是否可推进水位
    reason: str = ""
    error: str = ""
```

取值规则（`refine_session` 内设定）：

| 情况 | advanceable |
|---|---|
| 正常提炼（含 added/updated/merged=0） | ✅ True |
| `skipped` 且 reason 是"无可提炼对话内容" | ✅ True（没内容也算处理完） |
| `skipped` 且 reason 是"无可用 LLM（mock）" | ❌ False（等配置好再试） |
| 任意 `error != ""` | ❌ False |

## 四、逐文件改动

| 文件 | 改动 | 预估 |
|---|---|---|
| `agent/core/memory/profile_ledger.py` | 新建，见 §3.2 | +150 |
| `agent/core/memory/profile_idle.py` | 新建，见 §3.3 | +110 |
| `agent/core/memory/profile_refiner.py`（约 490 行） | 删 `_last_refine_ts`；`maybe_refine_async` 换台账+force；`RefineReport.advanceable`；`refine_session` 设 advanceable | +40 / −15 |
| `agent/session_manager.py` | `_auto_save(..., final: bool = False)`；调用改 `maybe_refine_async(..., force=final)` | +3 |
| `agent/main.py` | 退出前 `_auto_save(final=True)`；`reminder_host.start()` 后 `profile_idle.bind_scheduler(...)` + 注入消息回调 | +6 |
| `agent/serve/engine/engine.py` | 切换/载入会话前对旧会话 `final=True`；`_messages` 回调交给 hub 绑定 | +6 |
| `agent/serve/app.py` 或 `agent/serve/hub.py` | `hub.start()` 里 `profile_idle.bind_scheduler(self.scheduler, settings)` | +4 |
| `agent/config/settings.py` | 3 个新字段 | +3 |
| `agent/config/settings_domains.py` | `[memory]` 元组加 3 项 | +3 |
| `agent/config/config_specs.py` | 3 条 `ConfigSpec`（auto / live） | +12 |
| `configs/settings.example.toml`、`agent/configs/settings.example.toml` | 各加 3 条注释键 | +6 |
| `tests/core/test_profile_ledger.py`（新） | 见 §5 | +180 |
| `tests/core/test_profile_refine_golden.py`（新） | 见 §5 | +220 |
| `docs/log/CHANGELOG.md`、`docs/architecture/09-记忆与压缩.md` | 同步行为与配置 | +30 |

## 五、测试矩阵

### 5.1 `test_profile_ledger.py`（纯文件，无 LLM）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 空库 | `load_state` 返回默认值，不抛 |
| 2 | `touch` 刷新 `last_msg_ts` | 单调递增 |
| 3 | `advance` 后 `pending_delta` | 归零 |
| 4 | 消息变少（rewind） | `touch` 把 `refined_until` 下调到新 len |
| 5 | 文件损坏 | 当空表，不抛 |
| 6 | 写入原子性 | 无 `.tmp` 残留 |
| 7 | 50 会话上限 | 第 51 个淘汰最久未活跃 |
| 8 | 锁被占（模拟） | 不抛、记内存、返回成功 |
| 9 | 两线程并发 `advance` | 不丢更新、不写坏 |

### 5.2 `test_profile_refine_golden.py`（`_FakeProvider`，零网络）

| # | 用例 | 断言 |
|---|---|---|
| 1 | delta < min_delta | provider 调用 **0** 次 |
| 2 | delta ≥ min_delta | 调用 **1** 次；prompt **只含增量**（旧消息文本不出现） |
| 3 | `force=True` + delta=1 | 调用 1 次 |
| 4 | `force=True` + delta=0 | 调用 0 次 |
| 5 | provider 抛错 | 水位**不推进**，`last_error` 有值 |
| 6 | skipped="无可用 LLM" | 水位**不推进** |
| 7 | skipped="无可提炼内容" | 水位**推进** |
| 8 | 同一会话连续 3 次 | 总调用次数 = ⌈总消息/delta⌉，不重复喂 |
| 9 | 提炼提示词体积 | 字符数 ≤ 阈值（防后续改 prompt 膨胀） |

## 六、验收命令

```powershell
python -m pytest tests/core/test_profile_ledger.py tests/core/test_profile_refine_golden.py tests/core/test_profile_memory.py -q
python -m compileall -q agent/
python scripts/check_md_links.py
```

**人工走查**（实现完成后）：聊 8 轮 → 等 10 分钟再聊 2 轮 → 看 `refine_ledger.json` 的
`refined_until` 是否等于消息数；退出 → 确认终态提炼把最后增量收干；静置 60 分钟 → 确认空闲触发补提炼一次且只一次。

## 七、风险与回滚

| 风险 | 处置 |
|---|---|
| `/rewind`、`/compact` 让消息列表变短 | `touch` 检测 `len < refined_until` → 下调水位并记 diag；代价是最多重复提炼一次 |
| 会话重命名 → 新 key | 新会话全窗口提炼一次（可接受）；旧 key 由 LRU 淘汰 |
| 台账写失败 | 只更新内存 + diag_warn，绝不阻断提炼；下次成功写入时补上 |
| Scheduler 未绑定（测试/未 start） | 空闲触发静默禁用，阈值与终态两触发照常 |
| 空闲任务与终态触发撞车 | 两者都过 `_in_flight` 与水位闸，重复调用只有一次真正提炼 |
| `advanceable` 判断错 | 最坏是多提炼一轮（安全方向）而不是丢记忆 |
| 回滚 | 3 个配置键全关 + 删 `refine_ledger.json` → 行为回到 2026-11-14 之前 |

## 八、提交切分（四个独立提交）

1. `feat(memory): 提炼水位台账 profile_ledger（含截断回退与并发保护）` —— ledger + 单测
2. `feat(memory): 提炼改为增量喂入，支持终态 force 触发` —— refiner + session_manager + main/engine + golden 测试
3. `feat(memory): 空闲超时补提炼（复用宿主 Scheduler，一次性任务）` —— profile_idle + 宿主接线 + 测试
4. `docs(memory): P0 行为与配置同步` —— settings/domains/specs/example×2/CHANGELOG/架构 09

每步都能独立跑绿、独立回退；第 2 步做完即可消掉"重复喂窗口"，第 3 步只是补最后一个触发。

## 九、开工前待确认（默认值，不反对即按此执行）

| # | 决策 | 默认 |
|---|---|---|
| 1 | `profile_refine_min_delta` 默认 | ~~6~~ → **7**（2026-11-17 定：每 7 条一次） |
| 2 | `profile_refine_idle_minutes` 默认 | ~~60（对标 memobase）~~ → **2026-11-17 下线** |
| 3 | 无会话名时的 key | `"default"` + 一次 diag_warn |
| 4 | 台账文件损坏 | 当空表（重提炼一次），不备份不阻断 |
| 5 | 空闲触发是否朗读/提示 | ~~否，静默后台~~ → **2026-11-17 连同空闲触发一并下线** |

## 十、实施记录（2026-11-14）

步骤 1-2 已落地。与本文原稿的差异（均已写进代码注释）：

| # | 原稿 | 实际 | 原因 |
|---|---|---|---|
| 1 | `RefineState.last_refine_ts` 兼作节流依据 | 新增 `last_attempt_ts`，节流按“尝试”计；`last_refine_ts` 只记成功 | 否则 provider 持续报错时每轮都会重试（既有用例 `test_throttle_second_call_skipped` 抓到） |
| 2 | 3 个配置键进 `config_specs` 真源表 | 只进 `settings.py` + `settings_domains.py` + 两份 example | 与现有 `profile_*` 一致（记忆参数不由模型代改）；“58 项”不变，只有目录总数 202→205 |
| 3 | `touch` 后直接用旧 state | 用 `touch` 的**返回值** | `touch` 会下调水位（rewind/compact），旧值会让增量算错 |
| 4 | 增量在 worker 内取 | 启线程前 `list(messages[...])` 快照 | 切会话/新建会 `clear()` 消息列表，晚取只剩空表 |
| 5 | 空闲触发挂宿主 Scheduler | 改为**每会话一次性 `threading.Timer`** | `Scheduler` 会把任务持久化进 `schedule.json`，且 `list_pending()` 会在 `/schedule` 里列出——内部提炼任务污染用户可见提醒；Timer 非常驻、取消即释放，无需改 Scheduler 语义 |
| 6 | 空闲触发也要求 `delta ≥ min_delta` | 空闲门槛降为 **`delta ≥ 1`** | 实测：聊两三句走开，短尾巴被主路径与空闲两处门槛同时挡死，永远不入库；空闲已按小时限流，不需要再卡条数 |
| 7 | `/memory refine` 直调 `refine_session` | 改走 `maybe_refine_async(force=True)` | 旧路径整窗重喂且不推进水位，来源还被写成 `manual-refine` |

已落地：`profile_ledger.py`、`profile_idle.py`、`test_profile_ledger.py`（15 例）、
`test_profile_refine_golden.py`（12 例）、`test_profile_idle.py`（10 例）、
`profile_refiner.py`（`advanceable` + 三触发入口）、`session_manager.py`（`final` 参数）、
`main.py`（退出终态 + 空闲绑定）、`app.py`（桌面空闲绑定）、
`engine_session.py`（切会话/新建前 flush）、settings/domains/两份模板、
`09-记忆与压缩.md`、`concepts.md`、`CHANGELOG.md`。

剩余：步骤 4——按四个提交切分提交（台账 / 增量+终态 / 空闲 / 文档），推送前过 L3。
