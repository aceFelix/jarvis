# 桌面壳里 W1 打扰预算配置全部不生效（serve 入口漏调 init_policy）

> 2026-10-22 · serve 入口接线 · @author aceFelix

## 现象

用户按走查清单准备测「打扰预算」（场景 3），发现自己的 `~/.jarvis/settings.toml` 里
**根本没有静默时段相关的配置项**，于是问：「jarvis 的静默时段是写死的吗？」

顺着这句查下去，问题比"没写配置"严重一层：**即使写了也不生效**。

- 配置字段确实存在：`Settings.notify_quiet_hours`（默认 `"23:00-07:00"`），TOML 路径
  `[policy.notify_budget] quiet_hours`；
- 但桌面壳里改这个值没有任何反应，预算始终按 `23:00-07:00 / daily_max=10 /
  aggregate_window_s=300 / min_salience=0.3 / away_idle_minutes=0` 运行；
- 连 `notify_budget_enabled = false`（想把闸门整个关掉）也关不掉。

## 排查过程

### 第一步：确认字段与映射都在

| 环节 | 证据 | 结论 |
|---|---|---|
| Settings 字段 | `agent/config/settings.py` `notify_quiet_hours: str = "23:00-07:00"` | 有 |
| TOML → Settings 映射 | `settings_domains.py` `("policy", "notify_budget", (("quiet_hours", "notify_quiet_hours"), …))` | 有 |
| 解析实现 | `budget.py::_in_quiet()` 支持跨午夜、空串=不启用 | 有 |
| 环境变量覆盖 | `agent/config/env.py` 是**白名单**，无任何 `notify_*` 键 | 环境变量本来就改不了 |

字段与映射都没问题 → 问题在"值有没有被读进去"。

### 第二步：找到读取方式，发现是拉式注入

`agent/core/policy/__init__.py`：

```python
_settings = None            # 模块级快照，由 init_policy(settings) 注入
def _cfg(field, default):
    if _settings is None:
        return default      # ← 没注入就一切回硬编码缺省
    return getattr(_settings, field, default) or default

def get_budget():
    config = BudgetConfig(
        daily_max=int(_cfg("notify_daily_max", 10)),
        quiet_hours=str(_cfg("notify_quiet_hours", "23:00-07:00")), …)
```

于是问：谁调 `init_policy`？全仓搜索只有 **`agent/main.py`**（外加单测桩）。

### 第三步：确认桌面壳走的是另一个入口

`agent/serve/__main__.py` 的文档字符串就写着「Electron 主进程（jarvis-desktop
backend.ts）以 `python -m agent.serve` 拉起本模块」，其代码是：

```python
_settings = load_settings()          # 读了配置
sys.exit(run_serve(_settings))       # 但从未 init_policy(...)
```

`main.py` 里那句注释「CLI 与 serve 都经 main() 进入」只对**用户在终端敲
`jarvis --serve`** 成立；桌面壳直接 spawn 模块，绕过了 `main()`。

### 第四步：实证（不靠推理）

同一份 settings，注入前后各读一次预算状态：

```text
A) 未注入（桌面壳真实路径）: {'day': '2026-10-22', 'count': 5, 'daily_max': 10, 'pending': 0, 'quiet_hours': '23:00-07:00'}
B) 注入后（终端 main() 路径）: {'day': '2026-10-22', 'count': 5, 'daily_max': 3,  'pending': 0, 'quiet_hours': '01:00-02:00'}
```

配置里写 `01:00-02:00 / daily_max=3`，未注入时读回来仍是默认值 → 根因确认。

## 根因

W1 的配置采用「宿主启动时注入快照」的拉式读取，而**注入只挂在了 `main()` 这一个入口上**。
jarvis 实际有三个宿主入口：

| 入口 | 是否注入 | 修复前 |
|---|---|---|
| `jarvis`（REPL） | `main()` 注入 | ✅ 生效 |
| `jarvis --serve` | 同经 `main()` | ✅ 生效 |
| `python -m agent.serve`（桌面壳 spawn） | **无人注入** | ❌ 五个字段全失效 |

`_settings is None` 被当作"安全缺省"设计（模块文档字符串写着"零配置设计：不敲 `/away`、
不签 `/lease` 时完全不介入"），但它掩盖了"配置写了却不生效"这种最难查的失效形态——
租约与 away 确实零配置可用，偏偏预算这五个字段是**必须读配置**的。

## 修复

| 文件 | 改动 |
|---|---|
| [`agent/serve/app.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/serve/app.py) | `run_serve()` **首行**调 `init_policy(settings)`，注释写明为何放在 websockets 检查之前 |
| [`tests/policy/test_policy_host_wiring.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/policy/test_policy_host_wiring.py) | 新增 3 例：未注入回缺省（钉住曾出现的症状）、`run_serve` 注入生效、缺 websockets 提前退出时注入也已发生 |
| [`configs/settings.example.toml`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/configs/settings.example.toml) | 补上**原本完全缺失**的 `[policy.notify_budget]` 样例节（含闸门顺序与 `notify_state.json` 说明）——用户看不到这一节就是因为模板里没有 |
| [`agent/config/settings.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/config/settings.py) / [`config-docs/configuration.md`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/config-docs/configuration.md) | 纠正 `min_salience` 的过时口径（写的是"低于此值只入库不播"，2026-10-19 起实际为**直接抑制、不入队也不补播**） |
| [`docs/architecture/16-W1判断层.md`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/docs/architecture/16-W1判断层.md) | 「启动接线」改写为两个入口并列，并注明改配置须重启宿主 |

放在最前面而不是紧挨 `asyncio.run`：注入只是把快照交出去，与 websockets 在不在无关；
且缺依赖提前 `return 3` 时，下游读 `budget.stats()` 也必须是同一份配置。

## 验证结果

```powershell
python -m pytest tests/policy -q    # 54 passed（原 51 + 新增 3）
python -m pytest -q                 # 3211 passed，无回归
```

TOML 侧另跑 `tomllib.load` 确认新增节解析正常（`{'enabled': True, 'daily_max': 10,
'quiet_hours': '23:00-07:00', 'aggregate_window_s': 300, 'min_salience': 0.3}`），
且不打架：`[percept]` 仍在原位。

**待人工复验**（本文件写时未做）：桌面壳 `npm run dev` 重启后，把
`quiet_hours` 改成盖住当前时刻的窗口：

- **简报类**（`[daemon] briefing_time` 设两分钟后）→ 应“只入库不出声”，
  `~/.jarvis/notify_state.json` 的 `pending` 多一条、`reason` 为
  `静默时段（…）暂缓入库`；
- **用户自设提醒**（`/tasks add … --wait time:+90s` 那类不准；直接让贾维斯“10 分钟后…”
  设的提醒才算）→ **不写 pending**，而是气泡照弹、**不发音**（写本文件时还没这个行为，
  同日补实现的“降级投递”把提醒从入库改成了上屏不出声，见
  [16-W1 判断层](../architecture/16-W1判断层.md) 第四节）；
- `daily_max` 改回 2 → 第三条**简报**被暂缓入库（提醒仍照上屏，因为降级投递不占额度）。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/serve/app.py` | `run_serve()` 补 `init_policy(settings)` 接线 |
| `tests/policy/test_policy_host_wiring.py` | 新增宿主接线回归 3 例 |
| `configs/settings.example.toml` | 补 `[policy.notify_budget]` 样例节 |
| `agent/config/settings.py` | `notify_min_salience` 注释口径纠正 |
| `config-docs/configuration.md` | 同上，配置表口径纠正 |
| `docs/architecture/16-W1判断层.md` | 启动接线双入口 + 重启才生效 |

## 经验总结

- **"配置写了不生效"优先查注入点，不要先怀疑解析代码。** 拉式 + 模块级快照的配置
  （`getattr(_settings, …)`）一旦某个宿主入口漏注入，就会静默退回缺省，症状是
  "改了没反应"而不是"报错"，比崩溃更难查。
- **多入口项目要把"每个宿主都必须做的启动动作"收进最里层的那个函数**。`main()` 里
  的一句注释（"CLI 与 serve 都经 main() 进入"）在桌面壳 spawn 子进程的事实面前不成立；
  接线该放在 `run_serve()` 这种两个入口都会经过的地方，而不是各自的调用方。
- **"零配置安全缺省"与"必须读配置"要分开表述**。同一个 `_settings is None` 兜底，
  对租约/away 是真零配置，对预算就是摆设——兜底策略掩盖了字段级的差异。
- **配置模板缺节等于功能不存在**。用户翻自己的 `settings.toml` 找不到 `quiet_hours`
  才引出这一串；`example.toml` 从没给过 `[policy.notify_budget]`，用户自然以为不可配。
- 验证此类问题用**同一份 settings 注入前后各读一次**最直接，两行命令就能定论，
  不用起服务、不用真发播报。
