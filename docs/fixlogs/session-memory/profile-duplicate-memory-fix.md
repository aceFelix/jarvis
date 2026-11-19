# 画像记忆重复入库修复（profile-duplicate-memory-fix）

> 日期：2026-11-02 ｜ 作者：aceFelix ｜ 影响范围：`agent/core/memory/profile_store.py`、`agent/core/memory/profile_refiner.py`、`agent/commands/handlers/session_commands.py`

## 一、问题现象

用户查看 `C:\Users\许发明\.jarvis\memory\profile.json`，发现同一条事实被记了很多遍：

- `习惯深夜工作`（category=schedule，source_session=`s`）在 56 条画像里出现了 **6 次**
  （`ent_8572b72b` / `ent_032513c9` / `ent_ba24f47b` / `ent_3a465868` / `ent_8cfa634f` / `ent_da7d92ad`），
  内容、类别、置信度完全一致，只有 id 和 `created_at` 不同——即六次独立入库。
- 相邻的措辞变体也成对出现，例如 `职业是Agent开发者` 与 `职业是Agent应用开发工程师`、
  `姓名是aceFelix` 与 `姓名是aceFelix，沈阳化工大学计算机专业出身`。

影响：画像库被重复条目挤占上限、注入 system prompt 时同一条事实占掉多份 token 配额，
"越用越懂你"退化成"越用越啰嗦"。

## 二、排查过程

1. **先怀疑提炼触发频率**。`maybe_refine_async` 有 600 秒节流 + 进程内 `_in_flight` 锁 +
   `min_messages` 门槛，看起来不会短时间重复触发。但节流是"跨轮对话之间"的，
   一次会话结束后触发一次、下一次会话结束又触发一次——每次都会把相近的对话窗口喂给 LLM。
   节流并不能防止"同一条事实跨会话被反复提取"。方向不完全错，但不是根因。
2. **看存储写入**。`refine_session` 对 `new` 数组直接 `store.upsert(entry)`，而
   `ProfileStore.upsert` **只按 id 去重**——`ProfileEntry.new()` 每次生成随机 `ent_xxxxxxxx` id，
   所以两条内容完全相同的记录在存储层被当成两个不同对象，一律 append。**存储层没有任何按内容的查重**。
3. **看去重设计**。唯一的去重是提炼 prompt 第 6 条软约束："与已有画像矛盾或重复的信息，
   放到 updates 并给出 replace_id"。这是**把去重责任完全交给 LLM 的判断**。
4. **看已有画像展示**：`_build_prompt` 只展示 `existing[:_MAX_EXISTING_ENTRIES]`，且
   `_MAX_EXISTING_ENTRIES = 50`。当画像接近或超过 50 条时，排在前面的高频稳定事实
   （如"习惯深夜工作"）可能不在展示窗口里，模型看不到它，自然不会把它放进 updates，
   于是当成 new 再写一遍——**重复的雪球越滚越大，且越滚越看不见**。

根因链清晰：**存储层零内容查重 + LLM 软约束不可靠 + 展示上限遮挡**，三者叠加。

## 三、根因分析

- **架构层面**：画像写入是"读盘旧条目 → 交 LLM 裁决 → append"，裁决环节没有任何机器兜底，
  正确性完全押在 LLM 是否遵守 prompt 上。LLM 有概率无视、有概率因展示截断而看不见旧条目，
  这两条都会稳定地产出重复。
- **为什么之前没暴露**：低置信、语义各异的条目混在一起时重复不明显；只有"作息/工具"这类
  会被反复提及的稳定事实，才会跨会话攒出一堆一模一样的条目，用户用久了才看出来。

## 四、修复方案

原则：**去重不能只靠软约束，存储层必须有确定性兜底**。三层收口：

### 1. 存储层确定性查重闸（`profile_store.py`，核心）

- 新增 `normalize_profile_content()`：全角转半角、去全部空白、转小写、剥句尾标点，
  仅用于比较、不改写存储原文。
- 新增 `profile_content_similarity()`：字符二元组 Jaccard 相似度（对中文语序不敏感、零分词依赖）。
- 新增 `ProfileStore.merge_or_add(entry) -> (kept, merged)`：写入前扫描已有条目，
  归一化全等 **或** 相似度 ≥ `_SIM_MERGE_THRESHOLD`（0.85）即判为同一事实并合并：
  - 保留先到条目的 id / 原文 / 类别（**保守合并，不覆盖旧表述**）；
  - `confidence` 取两者较大值（同一事实再确认一次只增信、不换写、不被低置信拉低）；
  - `updated_at` 刷新、`created_at` 取最早；`source_session` 为 `manual` 者优先保留。
- 新增 `dedup_all()` / `duplicate_group_report()`：用同款判据对**整库**分组归并 / 只读预演，
  用于清理修复前已积压的重复。

阈值取舍：0.85 是"几乎原样重复"才合并。`职业是Agent开发者` vs `职业是Agent应用开发工程师`
相似度约 0.50，属措辞差异，**不自动合并**——语义级归并交给提炼 LLM 的 updates，存储层只拦死重复。

### 2. 提炼管线接线（`profile_refiner.py`）

- `refine_session` 的 `new`/`updates` 写入由 `store.upsert` 改为 `store.merge_or_add`，
  命中合并计入新增的 `RefineReport.merged` 计数（不再谎报 added）。
- 展示上限 `_MAX_EXISTING_ENTRIES` 由 50 提到 80，减少"旧条目被截断看不见"。
- prompt 第 6 条收紧为"重复（含措辞不同但语义相同）一律放 updates、禁止再进 new"，
  并新增第 7 条"拿不准就选最接近的一条放 updates，不要两条都写"。
- 每轮提炼写盘后追加一次 `dedup_all()`：修复前积压的重复随任意一次提炼顺带清理，无需额外维护任务。

### 3. 命令入口（`session_commands.py`）

- `/memory add` 改走 `merge_or_add`，重复口述同一件事返回"已并入相似记忆"而非默默新增。
- 新增 `/memory dedup [yes]`：两段式（预览重复组与可归并条数 → `yes` 执行 `dedup_all`），
  与 `/memory clear`、`/memory sync` 同确认体例，供用户主动清掉历史积压的重复。

是否引入临时方案：无。`merge_or_add` 是长期写入正路，`upsert` 保留给"显式按 id 覆盖"的场景。

## 五、验证结果

- **单元测试**：`tests/core/test_profile_memory.py` 新增 14 例，覆盖：归一化与相似度判据、
  `merge_or_add` 完全重复合并（置信取大不被低置信拉低）、manual 来源优先保留、措辞差异不合并、
  `dedup_all` 幂等归并、`duplicate_group_report` 只读预演不写盘、提炼管线"LLM 把旧事实当新事实
  再报"被合并（`added==0 / merged>=1`）、`/memory dedup` 预览与 yes 执行、`/memory add` 重复合并。
- **回归**：`test_profile_memory.py` + `test_profile_bridge.py` 合计 69 例全部通过（原有 55 例无回归）。
- **人工走查（待用户实机确认）**：重启 jarvis 后敲 `/memory dedup` 应列出 `习惯深夜工作 × 6` 等
  重复组与"可归并 N 条"；`/memory dedup yes` 后 `/memory` 中该条只剩一条；后续跨会话再提到
  "熬夜"不应再产生新的重复条目。

## 六、涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/memory/profile_store.py` | 新增归一化/相似度函数与 `merge_or_add`/`dedup_all`/`duplicate_group_report` |
| `agent/core/memory/profile_refiner.py` | 写入改走查重闸、展示上限 50→80、prompt 收紧去重、每轮末尾 `dedup_all` |
| `agent/commands/handlers/session_commands.py` | `/memory add` 走查重、新增 `/memory dedup [yes]` |
| `agent/ui/slash_completer.py` | `/memory` 补全描述补 `dedup/refine/sync` |
| `tests/core/test_profile_memory.py` | 新增 14 例去重相关单测 |
| `docs/architecture/09-记忆与压缩.md` | 设计取舍表、提炼流程图、命令表补去重 |
| `docs/guide/concepts.md` / `docs/guide/commands.md` | 画像记忆与命令参考补 `/memory dedup` 与查重说明 |
| `CHANGELOG.md` | 未发布段新增修复与 `/memory dedup` 条目 |

## 七、经验总结

1. **凡是"AI 判断"的环节都要有机器兜底**：把去重、裁决、校验完全交给 LLM 遵守 prompt 是脆弱的。
   LLM 无视软约束、或因输入被截断而看不见依据，都会稳定地产出错误。存储/执行层必须有一道
   确定性的、不依赖模型自觉的闸（本例的 `merge_or_add`）。这是全项目反复出现的模式（权限 fail-closed、
   tool_pairing 修复、配置写入 `apply_config_patch` 单一路径都是同一条律）。
2. **展示窗口是隐形数据丢失**：`[:50]` 截断看似只是"少展示"，实则是把"是否重复"的判据从模型眼前
   搬走，造成"越重复越看不见、越看不见越重复"的正反馈雪球。任何"取前 N 条喂模型"的设计都要问：
   被截掉的那部分会不会正好是判定所需的关键上下文。
3. **保守合并优于换写**：查重命中时保留先到条目的原文与类别、只把置信取大，避免用一句更差的
   表述覆盖掉更早更准的一句。`manual`（用户亲写）永远优先。
4. **历史脏数据要有自愈路径**：修复只挡新增还不够，已积压的重复靠"每轮提炼末尾 `dedup_all` +
   `/memory dedup yes` 主动整理"两条路清干净，不需要专门的迁移脚本或维护任务。
5. **新命令的三个登记点别漏**：router（本例 `/memory` 前缀已路由，无需改）、`slash_completer`、
   `/help` 文案与 `docs/guide/commands.md`——漏一个用户在补全或帮助里就找不到。