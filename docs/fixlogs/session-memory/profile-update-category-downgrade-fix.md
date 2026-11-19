# 画像 update 把旧条目类别降级（profile-update-category-downgrade-fix）

> 日期：2026-11-15 ｜ 作者：aceFelix ｜ 影响范围：`profile_resolve.py`（裁决 prompt/解析/落盘）

## 一、问题现象

用户按验证清单实机跑完后，`profile.json` 里出现两处“换值后类别被降级”：

| 被取代的旧条目 | 取代它的新条目 | 被破坏的不变量 |
|---|---|---|
| `ent_bec014ae` **identity** / permanent「从事编程开发工作」 | `ent_40f0e288` **other** / stateful「从事人工智能相关工作」 | 身份事实从 permanent 降成 stateful，将来会被降权休眠 |
| `ent_28ddbe0f` **schedule**「习惯熬夜，常一两点睡」 | `ent_a1bdc1fc` **other**「习惯熬夜，常到凌晨两点」 | 作息被记成 other，类别提示与召回加权都偏掉 |

同时观察到一处分类偏松：`ent_45449aa1`「**近期**常工作到凌晨三点」被判成 `episodic` 并给了 14 天到期。

## 二、排查过程

1. 先确认不是存储层：`merge_or_add` / `invalidate` 的字段都正确（`invalidated_at`、`superseded_by` 均落在旧条目上）；
2. 对比新旧条目字段，发现新条目 `category="other"`、`lifecycle="stateful"`；
3. 回看裁决 prompt：`update` 的输出示例里**只有 content / confidence / aliases，没有 category**——模型自然不写；
4. 再看解析：`category = item.get("category", "other")`——模型不写就落 `other`，且**无法区分“没给”与“显式给了 other”**；
5. 最后看落盘：`apply_decisions` 直接用 `d.category` 建新条目，**没有拿目标旧条目做兜底**；
6. 顺带核对 episodic：prompt 只写“带明确时间边界”，没排除“近期/最近”，且解析层允许 `episodic + expire_days=0`（等于一个永不到期的临时事项）。

## 三、根因分析

三处叠加，缺一不可：

- **示例即契约**：prompt 的 update 示例没有 category，模型按示例输出 → 必然漏字段；
- **缺省值语义丢失**：解析层用 `"other"` 兜底，把“没给”和“给了 other”混为一谈，后续无法继承；
- **落盘不继承**：update 语义是“替换同一事实”，类别与生命周期应从目标条目继承，而不是从新条目重新猜。

## 四、修复方案

1. `Decision.category`：`add` 缺省 `other`；`update` 缺省留空 `""`（表示“沿用目标条目类别”），显式合法值优先。
2. `apply_decisions` 的 update 分支：先取 `target = store.get(target_id)`，建新条目时
   `category=d.category or target.category`、`lifecycle=d.lifecycle or (permanent if target.lifecycle=="permanent" else "")`；
   target 不存在（幻觉/已失效）时仍按 add 走。
3. prompt：update 示例补 `category` 字段 + 一条“update 不写 category 时沿用旧条目类别”的规则。
4. episodic 收紧：
   - 解析层要求 `expire_days > 0`，否则把 `lifecycle` 作废（回退按类别派生）；
   - prompt 明确“只有明确时间点/期限（明天/下周三/这周/N 天后）才算，近期/最近这类模糊说法不算”。
5. 测试：新增 `TestUpdateCategoryInheritance` 6 例（解析继承标记 / add 缺省 / episodic 无到期作废 / 落盘继承 identity+permanent / 显式类别 / prompt 示例含 category）。

## 五、验证结果

```
python -m pytest tests/core/test_profile_resolve.py tests/core/test_profile_memory.py tests/core/test_profile_refine_golden.py -q
→ 133 passed
```

全量回归：见提交说明（5331+ 通过）。

## 六、涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/memory/profile_resolve.py` | update 类别/lifecycle 继承；episodic 必须有 expire_days；prompt 示例与规则收紧 |
| `tests/core/test_profile_resolve.py` | 新增 `TestUpdateCategoryInheritance` 6 例 |

## 七、经验总结

- **模型的输出示例就是契约**：示例里少一个字段，线上就少一个字段——要么示例带全，要么解析层必须能继承；
- **“没给”与“给了空/默认值”必须分开**：用 `""` 表达“缺省继承”，比在解析层猜 `other` 安全；
- **替换语义要延续旧条目的稳定属性**：类别和 lifecycle 属于“这条事实是什么”，不该由一次表述更新重新决定；
- **软删除让问题可见**：旧条目还躺在 JSON 里，才让这次降级一眼可查——这正是 P2b 保留历史的价值。
