# 画像衰减依据是死字段（profile-decay-reference-wiring-fix）

> 日期：2026-11-15 ｜ 作者：aceFelix ｜ 影响范围：`profile_store.py`、`profile_resolve.py`、`/memory` 列表

## 一、问题现象

用户追问“半衰期是什么依据”时核对代码，发现 `ProfileStore.decay()` 的实现与它的注释/文档不一致：

```python
basis = max(e.updated_at, e.last_referenced_at, e.created_at)
```

注释声称“近期更新/引用过的不衰”，但全仓检索显示：

- `last_referenced_at` **从来没有被写入过**（只在合并时于若干 0 之间取 max）；
- `ref_count` 同样从未自增（只有合并时把 0 相加）。

即“被引用/被再次提及就续命”只是纸面机制，实际依据只有 `updated_at`。
后果：任何 `stateful` 事实只要 90 天内没被再次合并，conf=0.9 会衰到 0.11 < floor 0.15 被删除——
而用户心里“我上周才提过这事”并不能保护它。

## 二、排查过程

1. 用户问“半衰期依据”——先看 `decay()` 默认值来自哪里：Phase 1a 的 M3 维护管线，**经验值，无实测推导**；
2. 检查 `basis` 三个输入是否都有人写：
   - `updated_at`：写入/合并时刷新（有）；
   - `created_at`：创建时写（有）；
   - `last_referenced_at`：grep `last_referenced_at =` 只命中合并时取 max（两侧都是 0）→ **死字段**；
3. 检查 `ref_count`：同样只做 `e.ref_count + entry.ref_count`，没有任何自增点 → **死字段**；
4. 确认语义缺口：`recall_similar`（裁决前召回）和注入渲染都不该算“用户又提到了”，
   真正可用的信号只有两个——**提炼裁决为 noop**（新消息里再次说到同一事实）
   与**合并写入**（同一事实被重新抽取）。

## 三、根因分析

- 字段是 Phase 1a 为“衰减依据”预留的，但当时没接任何写入点，属于典型的“先建字段后忘接线”；
- 因为默认值 0 不会让衰减崩，问题一直没暴露，只是让机制**名不副实**；
- 文档照抄了设计意图（“被引用过的不衰”），进一步掩盖了死字段。

## 四、修复方案

1. `ProfileStore.touch_reference(entry_id, *, now=None)`（新）：刷新 `last_referenced_at`、`ref_count += 1`；
   失效/不存在返回 False，不动内容与置信度。
2. 接线点选 **noop**：`profile_resolve.apply_decisions` 遇到 `op=noop` 且 `target_id` 非空时调用它。
   理由：P0 之后提炼只喂增量，noop 意味着“这段**新消息**里又提到了这条事实”，是可靠的再确认信号。
3. **合并也算一次再确认**：`merge_or_add` 命中重复时 `ref_count = e + entry + 1`，
   `last_referenced_at = now`（原先取 max，两个 0 还是 0）。
4. `/memory` 列表在 `ref_count > 0` 时展示“提及 N 次”，让续命依据对用户可见。
5. 不改半衰期与 floor（30 天 / 0.15 仍为经验值，等评测集校准），本次只让依据真正成立。

## 五、验证结果

```
python -m pytest tests/core/test_profile_memory.py tests/core/test_profile_resolve.py -q
→ 103 passed
```

新增用例：
- `TestReferenceWiring`（4 例）：字段刷新/失效与不存在不续命/合并计一次/老条目按最近提及算年龄；
- `TestNoopRenewal`（2 例）：noop 续命、幻觉 target 静默；
- `/memory` 展示“提及 1 次”（1 例）。

全量回归：5269+ 通过（见提交说明）。

## 六、涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/memory/profile_store.py` | 新增 `touch_reference`；`merge_or_add` 刷新 `last_referenced_at` 且 `ref_count+1` |
| `agent/core/memory/profile_resolve.py` | `noop` 分支调用 `touch_reference` |
| `agent/commands/handlers/session_commands.py` | 列表展示“提及 N 次” |
| `tests/core/test_profile_memory.py`、`tests/core/test_profile_resolve.py` | 7 例新测试 |
| `docs/architecture/09-记忆与压缩.md`、`docs/log/CHANGELOG.md` | 口径同步 |

## 七、经验总结

- **“预留字段”必须在同一次改动里接上写入点**，否则默认值会把它伪装成正常工作；
- 注释与文档里的机制描述要能被 grep 验证：写“被引用不衰”时，就该有 `last_referenced_at` 的写入点；
- 召回/注入**不能**当作“再确认”信号：它们每次都会发生，接了等于所有条目永不衰减；
- 半衰期 30 天仍是经验值，真正的依据要靠真实“提及间隔”分布 + 评测集来校准（P4）。

## 八、后续变更（2026-11-15，同日）

本篇修复完成后，用户进一步拍板“衰减不能是删除，改为降权”，因此 `decay()` 已拆为
`expire_episodic()`（只出清到期 episodic）与 `audit_freshness()`（只统计休眠，不改数据）：
- `confidence` 不再被时间乘；权重改为读时派生 `weight = confidence × 新鲜度`；
- 低于 0.10 进休眠（不注入、保留、可被再次提及复活）；
- 本篇“90 天必被删”的描述已成为历史，当前口径见 `09-记忆与压缩.md`「权重与休眠」。
