# 人设卡构建失败只报“失败”不报原因（且失败后无限重试）

> 状态：**已实施**（2026-11-20；失败带原因 + 失败退避；硬门槛方案已否决）
> 用户感知：`WARN 人设卡构建失败，保留旧卡`，但**没有任何原因**，也无法判断该怎么办。

## 1. 问题现象

执行 `/exit` 时终端与 `~/.jarvis/logs/diag.log` 同时出现：

```
2026-11-19T23:30:01.394 [memory.profile_persona] WARN 人设卡构建失败，保留旧卡
```

- 只有结论，没有原因、没有异常类型、没有可行动信息；
- 用户实际**没有任何旧卡**（`~/.jarvis/memory/persona.json` 不存在），
  "保留旧卡"这句本身与事实不符；
- 该 WARN 会在**每次退出、每次启动补偿**时重复出现（见 §3.4）。

## 2. 排查过程

### 2.1 先看日志属于哪个分支（关键分叉）

`agent/core/memory/profile_persona.py` 的 `_worker()` 有两条失败路径：

```python
new = build_persona(settings, store=store, previous=persona)
if new is None:
    diag_warn(_COMPONENT, "人设卡构建失败，保留旧卡")      # ← 用户看到的这句
    ...
except Exception as e:
    diag_warn(_COMPONENT, f"人设卡重建失败: {type(e).__name__}: {e}")  # ← 带原因的那句
```

用户看到的是**前一句**，说明：

- 没有抛异常（网络错误、鉴权失败、JSON 解析崩溃都会走后一句并带原因）；
- 是 `build_persona()` **正常返回了 `None``。

于是问题收敛到 `build_persona()` 的四个 `return None`。

### 2.2 逐个排除 `build_persona` 的返回 None 分支

| 分支 | 代码位置 | 本次是否成立 | 依据 |
|---|---|---|---|
| 采样后无条目 | `entries = _sample_entries(store)`; `if not entries: return None` | **否** | `profile.json` 有 1 条条目 |
| 精炼 provider 构建失败 | `provider = _build_refine_provider(settings)`; `if provider is None: return None` | **否** | 同一会话内画像提炼**已成功**（见 2.3），说明 provider/Key/模型可用 |
| 模型输出校验失败 | `parse_persona_output(...)` 返回 None | **是（高置信）** | 见 2.4 |
| token 超限 | `if persona.token_count > persona.token_limit: return None` | 否 | 仅 1 条输入，远不到 800 tokens |

### 2.3 画像提炼本身是成功的

`~/.jarvis/memory/refine_ledger.json`：

```json
"贾维斯报时与待命": {
  "refined_until": 8,
  "last_refine_ts": 1795102046.52,
  "last_error": "",
  "last_report": { "extracted": 1, "added": 1 }
}
```

→ 提炼链路（provider/Key/模型/网络）**全部正常**，本次新增 1 条画像。

`~/.jarvis/memory/profile.json`：569 字符、**仅 1 条条目**（`"id"` 出现 1 次）。

### 2.4 真正失败的判定点：缺 `identity` 段

`parse_persona_output()` 的硬门槛：

```python
if not by_key.get("identity"):
    return None  # 没有人设最核心的一段，视为失败
```

在进入这条判定之前，每条 bullet 还要过两道闸：

```python
sources = [str(s) for s in (item.get("sources") or []) if str(s) in valid_ids]
if not sources:
    dropped += 1
    continue                      # 没有有效 sources 的 bullet 一律丢弃
...
if not text or len(text) > MAX_BULLET_CHARS:
    dropped += 1                  # 超 100 字或空文本一律丢弃
```

即：**模型必须输出 `key="identity"` 的段，且每条 bullet 都带采样集里真实存在的
`ent_xxx` id、不超 100 字，否则整卡判失败。**

本次只有 **1 条**画像条目，模型极可能：

- 把内容归到 `preferences` / `habits` 等其它段，没产出 `identity`；
- 或产出的 bullet 漏了 `sources` / 编了不存在的 id → 全部被 `dropped` → 段为空。

两种情况都直接命中 `not by_key.get("identity")` → 返回 None →
只留下一句无原因的 WARN。

### 2.5 顺带确认的第二个缺陷：失败后无限重试

```python
def _needs_build(store, persona):
    if not store.entries(): return False
    if persona is None: return True          # ← 没有卡就永远"需要建"
    return _fingerprint(store) != (persona.source_count, persona.source_max_updated_at)
```

失败分支只更新 `last_attempt_at`，**不清理 `pending`**（`persona_state.json`
现在仍是 `"pending": true`）。于是：

- 每次 `/exit` 都会再试一次（`wait_seconds=15`，退出被多等最多 15 秒）；
- 每次启动 `startup_check_async` 也会再试一次；
- 只要画像条目少到建不出 identity 段，就会**每次退出/启动都打一条 WARN**。

## 3. 根因分析

1. **可观测性缺陷（主诉）**：`build_persona` 的四个失败分支共用调用方一句
   `"人设卡构建失败，保留旧卡"`，分支内部没有任何 `diag_*` 记录原因；
   而真正带原因的异常分支只在"抛异常"时才触发。用户拿到的是零信息量告警。
2. **数据不足被当成失败**："只有 1 条画像"属于数据量不足，不是故障；
   当前没有最小条目门槛，也不区分 info（跳过）与 warn（失败）。
3. **校验过严且不可观测**：`identity` 段是硬门槛，`sources` 必须命中采样集，
   任何一条不满足就整卡失败——设计上可以（宁缺毋滥），但**丢弃明细没有记录**，
   无法判断是模型跑偏还是条目不足。
4. **状态机缺陷**：`persona is None` 使 `_needs_build` 恒为 True，失败不清
   pending、无退避，形成"每次都失败、每次都告警"的循环。
5. **文案与事实不符**：`persona.json` 不存在时仍说"保留旧卡"。

## 4. 修复方案（待实施）

### A. 失败必须带原因（最小改动、优先做）

让 `build_persona` 返回 `(persona | None, reason: str)`（或抛
`PersonaBuildError(reason)`），四个分支各自给出可读原因：

| 分支 | 建议文案 |
|---|---|
| entries 为空 | `info: 画像条目为 0，跳过人设卡生成` |
| provider 不可用 | `warn: 精炼模型不可用（model=xxx），无法生成人设卡` |
| parse 失败 | `warn: 模型输出不符要求（缺 identity 段 / 丢弃 N 条：无有效 sources M 条、超长 K 条、重复 J 条）` |
| token 超限 | `warn: 人设卡超限（N > 800 tokens），已放弃本次结果` |

`parse_persona_output` 同步返回诊断结构（`dropped_reasons` 计数），供上面的
文案使用；这些计数**不进对话**，只进 `diag.log`。

### B. 用「失败退避」取代「最小条目门槛」（实施时调整）

原计划新增 `profile_persona_min_entries`（默认 5）硬门槛，实施时否决：

- 1 条画像也可能产出 identity，硬门槛会把 3—4 条的「数据已够」场景一起挡掉；
- 既有 3 个用例（`tests/core/test_profile_persona.py::TestBuildFlow`）明确
  要求「有条目就尝试」，硬门槛会改变既有行为；
- 真正的痛点是**幂等失败被反复重试**，退避正对这个点：
  `RETRY_INTERVAL_S = 600`，失败时记录 `last_fingerprint`，指纹未变且未过
  退避窗口则直接返回 False（不启动线程、不再告警）。

### C. 失败退避，停止打扰

- 失败时记录 `last_attempt_at`，并在 `_needs_build` 中加最小重试间隔
  （例如复用 `profile_refine_interval` 的语义，默认 600s；或 24h）；
- 指纹未变化时（条目数/updated_at 都没变）**直接复用上次失败结论**，
  不清 pending 也不再重试，直到有新条目写入（`mark_pending` 会刷新 `changed_at`）。

### D. 文案与状态如实

- `persona.json` 存在 → `"保留旧卡（built_at=…）"`；
- 不存在 → `"本次未生成人设卡（原因：…）"`；
- 建议给 `/memory` 增加一行人设卡状态（`status()` 已实现，未暴露）：
  `人设卡: 未生成（条目 1/5）`，让用户平时就能看到，而不是退出时才看到告警。

## 5. 验证结果

**已实施并跑通测试（2026-11-20）**：

- 四个失败分支各自 `diag_warn` 原因：无条目 / provider=None / 输出不是 JSON /
  缺 sections / 缺 identity（含有效 id 数与丢弃条数）/ token 超限；
- `_worker` 文案区分有无旧卡，不再对无卡用户说「保留旧卡」；
- 失败写入 `last_fingerprint` + `last_attempt_at`，`maybe_rebuild_async` 按
  `RETRY_INTERVAL_S` 退避（指纹变化则立即重试）；
- `pytest tests/core/test_profile_persona.py tests/memory/test_profile_persona_golden.py`
  → **20 passed**（含 3 个既有状态机用例）；
- 未新增配置键，避免为一次修复铺开「五处同步」。

**仍需实机验证**（有旧卡 / 无卡各一次）：

1. 保持画像 1 条 → 首次 `/exit` 允许尝试一次；失败时 WARN **必须带原因**
   （如「模型输出缺 identity 段（有效 id 1 个，丢弃 2 条）」）；
   紧接着第二次 `/exit`（指纹未变、10 分钟内）应**不再尝试、不再告警**；
2. 画像 ≥ 5 条 → 正常生成 `persona.json`，`/memory` 显示
   `人设卡: 已生成（built_at=…, 6 段）`；
3. 人为构造"模型输出缺 identity"（测试桩）→ WARN 含
   `缺 identity 段` 与丢弃计数；
4. 人为断开精炼 key → WARN 含模型名与 `精炼模型不可用`；
5. 回归：`pytest tests/core tests/commands -q` 全绿；
   `refine_ledger.json` 的既有语义不变。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/memory/profile_persona.py` | 四分支返回原因；`parse_persona_output` 出诊断；失败退避；文案如实 |
| ~~`agent/config/settings.py` 等三个配置文件~~ | 未改：改用 `RETRY_INTERVAL_S` 模块常量，未新增配置键（避免五处同步） |
| `agent/configs/settings.example.toml` | 未改（无新配置键） |
| `agent/commands/handlers/session_commands.py` | 待做：`/memory` 展示人设卡状态 |
| `docs/architecture/09-记忆与压缩.md` | 待补：失败可观测与退避口径 |
| `docs/fixlogs/profile-persona-build-silent-failure-fix.md` | 本复盘 |

## 7. 经验总结

- **"失败"两个字是最差的日志**。用户拿到告警后的第一个问题必然是"为什么"，
  而写日志的人当场就知道为什么——不写下来，等于把排查成本转嫁给用户。
  凡是 `return None` 的失败分支，都必须在返回前留下原因。
- **数据不足不是故障**。样本量不足导致生成不出结果，应走 info 跳过，
  否则每次退出都告警，用户会逐渐无视告警（告警疲劳），真正的故障也就被埋了。
- **状态机不能"必然失败"还无限重试**：`persona is None → 需要重建` 在
  "永远建不出来"时会退化成每次启动+每次退出各试一次，既拖慢退出又刷屏。
  重试必须带退避与"输入是否变化"的判断。
- **文案不能虚构现状**：没有旧卡就说保留旧卡，会让用户误以为系统里有一张
  他找不到的卡，进一步怀疑安装坏了（与 `init-settings-toml-absent-fix.md`
  是同一类"看不见就当没有"的认知问题）。
