# 实现方案：画像记忆 P2（生命周期分层 + 事件轨）

> 状态：**P2a/P2b/P2c 全部落地（2026-11-15）**　作者：aceFelix　日期：2026-11-15
> 上位文档：[roadmap/memory-config/PLAN-profile-memory-upgrade.md](../../roadmap/memory-config/PLAN-profile-memory-upgrade.md) §P2
> 本文只写落点与契约；设计取舍在 PLAN，不在本文重复。

## 一、范围

| 切片 | 内容 | 本次 |
|---|---|---|
| **P2a** | `lifecycle ∈ {permanent, stateful, episodic}` 字段 + decay 分流 + 类别派生 | ✅ |
| **P2b** | `stateful` 新值即失效：软删除（`invalidated_at` / `superseded_by`）+ 注入过滤 | ✅ |
| **P2c** | 事件轨 `events.jsonl`（append-only + 过期清理） | ✅ |

**不做**：P3 向量、P5 RecallProfile。

## 二、现状事实

| 事实 | 位置 |
|---|---|
| 条目字段：id/category/content/aliases/confidence/source_session/时间戳/ref_count | `profile_store.ProfileEntry` |
| 磁盘 version=2（P1b 加 aliases） | `_save_locked` |
| decay 对所有条目一视同仁：30 天半衰期，<0.15 删除 | `ProfileStore.decay` |
| 唯一豁免：`source_session == "manual"` | 同上 |
| 合并（merge_or_add / dedup_all）只并 confidence/aliases/时间戳 | 同上 |
| 每日 03:30 维护任务调用 decay + prune | `agent/core/daemon` 维护管线 |

**问题**：身份/关系这类"5 年后还成立"的事实也被半衰期磨，靠反复提及续命；
而过时状态（在做什么项目）又没有"新值即失效"的语义，只能等衰减慢慢磨。

## 三、数据模型

### 3.1 `ProfileEntry.lifecycle`

```python
lifecycle: str = "stateful"     # permanent / stateful / episodic
expire_at: float = 0.0          # 仅 episodic 用：到期直接出清（0 = 不设）
```

- 白名单 `LIFECYCLES = ("permanent", "stateful", "episodic")`，非法值落 `stateful`。
- `profile.json` version **2 → 3**；缺字段读取时**按类别派生**（见 3.2），不写盘迁移。
- 合并策略：`permanent > stateful > episodic`，取更"稳"的那个——同一条事实被
  以 stateful 再报一次，不能把 permanent 降级。

### 3.2 类别 → 初始 lifecycle（`default_lifecycle_for_category`）

| lifecycle | 类别 | 依据 |
|---|---|---|
| `permanent` | identity / relationship / aspiration | 姓名、学历、家人、长期抱负：5 年后仍成立 |
| `stateful` | personality / preference / work_habit / schedule / mindset / tool_usage / project / other | 会变但不会立刻失效；靠半衰期淡忘 |

`episodic` 不在类别派生里：只有带明确到期时间的事实才走（P2c 由提炼显式给出）。

## 四、decay 分流（P2a 核心）

> **2026-11-15 修订**：衰减已改为**只降权不删**（用户拍板）。本节原稿的“删除”现在
> 只保留给 `episodic` 显式到期；stateful 不再被删，只进休眠。当前口径见
> `09-记忆与压缩.md` 的「权重与休眠」一节与 CHANGELOG。

```
对每条条目：
  manual 来源 → 跳过（现状不变，最高信任）
  lifecycle == permanent → 跳过，confidence 不动
  lifecycle == episodic:
      expire_at > 0 且 now > expire_at → 删除
      否则 → 走半衰期（没给到期时间的临时项按 stateful 处理）
  其它（stateful/未知）→ 走半衰期（现状）
```

返回值仍是"清除条数"，调用方（每日维护）不需要改。

## 五、接口契约

```python
# profile_store.py（2026-11-15 修订后的实际接口）
LIFECYCLES: tuple[str, ...]
def default_lifecycle_for_category(category: str) -> str
def entry_weight(entry, *, now=None, half_life_days=30.0) -> float

class ProfileEntry:
    lifecycle: str = "stateful"
    expire_at: float = 0.0
    @staticmethod
    def new(content, category="other", confidence=0.5, source_session="",
             aliases=None, lifecycle="", expire_at=0.0) -> ProfileEntry

class ProfileStore:
    def weighted_entries(self, *, now=None) -> list[tuple[float, ProfileEntry]]
    def active_entries(self, *, now=None, floor=0.10) -> list[ProfileEntry]
    def dormant_count(self, *, now=None, floor=0.10) -> int
    def expire_episodic(self, *, now=None) -> int   # 唯一仍会删的衰减邻近路径
    def audit_freshness(self, *, now=None, floor=0.10) -> int  # 只统计，不改数据
```

`merge_or_add` / `dedup_all`：合并时取更稳的 lifecycle；`expire_at` 取两者较大
（有到期时间的那个不因合并丢失）。

## 六、代码落点

| 文件 | 改动 | 预估 |
|---|---|---|
| `agent/core/memory/profile_store.py` | LIFECYCLES + 派生函数 + 字段 + new() + decay 分流 + 合并策略 + version 3 | 约 80 行 |
| `tests/core/test_profile_memory.py` | 新增 lifecycle / decay 分流 / 兼容读 / 合并降级防护 | +100 |
| `docs/architecture/09-记忆与压缩.md` | 生命周期表 + decay 分流说明 | +15 |
| `docs/log/CHANGELOG.md`、PLAN §P2 状态 | 同步 | +15 |

**不新增配置键**：`lifecycle` 的行为完全由数据决定，没有可调参数（避免死配置）。

## 七、测试矩阵（P2a）

| # | 用例 | 断言 |
|---|---|---|
| 1 | `new()` 不给 lifecycle | 按类别派生（identity→permanent，preference→stateful） |
| 2 | `new()` 给非法 lifecycle | 落 stateful，不抛 |
| 3 | 老库（无 lifecycle 字段） | 读出按类别派生，不报错 |
| 4 | permanent 跑 decay | confidence 不变、不删除 |
| 5 | stateful 跑 decay（越过半衰期） | confidence 衰减，低于 floor 删除 |
| 6 | episodic 带 expire_at | 到期删除 |
| 7 | episodic 无 expire_at | 按 stateful 走衰减（不凭空删除） |
| 8 | manual | 仍豁免（现状回归） |
| 9 | merge_or_add 合并 | permanent 不被 stateful 降级；expire_at 取大 |
| 10 | dedup_all 合并组 | 主条目保留最稳 lifecycle |
| 11 | 磁盘 version | 写盘为 3 |

## 八、验收

```powershell
python -m pytest tests/core/test_profile_memory.py tests/core/test_profile_refine_golden.py -q
python -m compileall -q agent/
python scripts/check_md_links.py
```

## 九、风险与回退

| 风险 | 处置 |
|---|---|
| 老库全部缺字段 | 读取时按类别派生（确定性），无需迁移脚本；下次写盘自动补全 |
| 把易变事实误判 permanent | 派生表刻意保守：只有 identity/relationship/aspiration 三类 permanent |
| decay 删多了 | 只影响 confidence 衰减路径；permanent 反而更安全（不再被磨） |
| 回退 | 删掉 lifecycle 字段读取即可回到旧行为；无配置开关、无外部状态 |

## 十、P2c 契约（已落地）

- **写入信号**：裁决段可选输出 `"lifecycle": "episodic"` + `"expire_days"`（1–365，从今天算）；
  非 episodic 的候选强制清空这两个字段。
- **落盘**：`ProfileEntry.new(..., expire_at=now+days)` + `profile_events.append_event(...)`；
  事件与条目同源、同内容、同到期时间。
- **读取**：`/memory events [n]` 看最近未过期事件；默认**不注入** system prompt。
- **清理**：每日维护 `prune_events(retention_days)`，默认 90 天，`0` = 只删到期项。

## 十一、实施记录（2026-11-15，P2a）

| # | 计划 | 实际 | 原因 |
|---|---|---|---|
| 1 | 一次性迁移脚本 `migrate_profile_lifecycle.py` | **不写脚本**：读取时按类别派生 | 派生是确定性的，读到即可用；下次任意写盘自动补全字段，幂等且零失败路径 |
| 2 | 提炼输出 lifecycle | 本次不输出 | 类别派生已覆盖三类；episodic 需要时间表达式（P2c），现在让 LLM 输出只会徒增解析面 |
| 3 | decay 直接用当前时间 | 新增 `now=` 注入点 | 半衰期断言不能靠 sleep，也不能靠改系统时间 |
| 4 | — | `_save_locked` version 2 → 3 | 新字段上线，版本号递增便于用户/工具识别 |

已落地：`profile_store.py`（LIFECYCLES / `default_lifecycle_for_category` / `_normalize_lifecycle` /
`_most_stable_lifecycle` / 字段 / `new()` / `decay` 分流 / 合并防护 / version 3）、
`tests/core/test_profile_memory.py`（`TestLifecycle` 9 例）、
09-记忆与压缩（生命周期表 + decay 分流 + 设计决策）、CHANGELOG、PLAN。

## 十二、实施记录（2026-11-15，P2b）

| # | 计划 | 实际 | 原因 |
|---|---|---|---|
| 1 | 软删除另开字段组 | `invalidated_at` + `superseded_by` 追加到 `ProfileEntry` 末尾 | 加在末尾不会影响任何位置参数构造；两字段都缺省为 0/""，向下兼容 |
| 2 | — | `update` 先 `merge_or_add` 再决定是否失效 | 同义重述会合并回目标自身（`kept.id == target_id`），那种情况绝不能把目标标成失效 |
| 3 | 失效条目如何处理其他维护 | `decay` 跳过、`prune` 先淘汰、`dedup` 保留、`merge` 跳过 | 失效条目是历史证据，不应被半衰期磨、也不该把旧值“复活”；囤积时优先淘汰 |
| 4 | `/memory` 是否展示历史 | 不展示条目，末尾只提示条数 | 信任原则：看得到“记得什么”，而完整历史链路（`/memory history`）留到需要时再加 |

已落地：`profile_store.py`（`invalidate` / `entries(include_invalidated=)` / `get(...)` /
`__len__` 只算有效 / 合并与去重跳过失效 / `decay` 跳过 / `prune` 先淘汰 / 磁盘字段）、
`profile_resolve.apply_decisions`（update 改软删除）、`session_commands._show_profile`（历史计数提示）、
`tests/core/test_profile_memory.py`（`TestSoftDelete` 8 例）、`tests/core/test_profile_resolve.py`
（`TestSoftDeleteOnUpdate` 3 例）。

## 十三、实施记录（2026-11-15，衰减改降权）

| # | 决定 | 理由 |
|---|---|---|
| 1 | 权重不落盘，读时派生 | 不写盘 → 调参不改数据、可回滚；`confidence` 保持“可信度”语义不被时间污染 |
| 2 | 休眠阈值 0.10 | weight 已是双因子（置信×新鲜），比原单因子 floor 0.15 略低；不同置信的休眠时点约 48–95 天 |
| 3 | 召回不排除休眠 | 休眠条目仍是 update 的合法目标（被新值替换时自然退出历史），排除反而会让旧值无法被正确替换 |
| 4 | 超容量仍物理淘汰 | 200 条是磁盘/预算的硬上限；按权重最低先淘汰，属于“容量兜底”而非衰减 |
| 5 | `decay()` 不保留为别名 | 语义已变（不再删），保留旧名会让后人误以为还在衰减删除；拆为 `expire_episodic` + `audit_freshness` |

> **2026-11-18 修订**：默认改为 `profile_max_entries = 0`（**不限**）——单用户不应因硬上限丢掉
> “一年没提”的事实；仅用户显式设了上限才按权重淘汰。大批量归并 `dedup_all` 改为仅在
> ≤ 600 条时随提炼自动跑（O(n²) 保护），超出后用 `/memory dedup` 手动整理。

已落地：`profile_store.entry_weight` / `weighted_entries` / `active_entries` / `dormant_count` /
`audit_freshness` / `expire_episodic` / `prune_over_limit(按权重)` / `render_for_prompt(只喂活跃条目)`；
`profile_inject`（权重参与排序）；`proactive` 维护任务改口径；`/memory` 展示权重与休眠；
测试 `TestFreshnessWeight` 6 例，全量 1693（core+daemon+commands）通过。

## 十四、实施记录（2026-11-15，P2c）

| # | 决定 | 理由 |
|---|---|---|
| 1 | `expire_days`（相对天数）而非绝对日期 | 中文日期（“下周三”）解析歧义大；只让模型给天数，换算交给代码 |
| 2 | 非 episodic 强制清空字段 | 防模型给普通事实乱设到期（“喜欢猫，7 天后忘了”）；解析层就吃掉 |
| 3 | 事件轨默认不注入 | 临时事项不该长期占 300 token 预算；需要时 `/memory events` 查 |
| 4 | 事件与条目同写 | 两条腿同一内容：条目靠 `expire_at` 到期出清，事件轨靠保留期清理 |
| 5 | 写入/读取/清理均失败静默 | 事件轨是旁路日志，绝不能让磁盘问题打断提炼或维护任务 |

已落地：`profile_events.py`（append/load/recent/prune）、`Decision.lifecycle/expire_days` 与解析硬闸、
`apply_decisions`（episodic 入库 + 事件追写）、`/memory events [n]`、`profile_event_retention_days`
（Settings 209→210）、每日维护 prune、两份模板；测试 `test_profile_events.py` 16 例 + 裁决 4 例 + 命令 2 例。
