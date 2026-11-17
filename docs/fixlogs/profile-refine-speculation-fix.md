# 画像提炼把推断当事实（profile-refine-speculation-fix）

> 日期：2026-11-17 ｜ 作者：aceFelix ｜ 影响范围：`profile_resolve.py`（抽取/裁决 prompt + 解析硬闸）

## 一、问题现象

第二轮实机验证后，`profile.json` 出现一条不该入库的“事实”：

```json
{"id": "ent_566389cc", "category": "other",
 "content": "关注贵阳天气，可能与出差或探亲有关", "confidence": 0.6}
```

同一轮里那条临时事项也带着推测：

```json
{"id": "ent_aa8ec19d", "content": "下周五有出差安排，目的地可能是贵阳"}
```

用户只是查了贵阳天气，并没有说出“出差/探亲”的意图——这是模型把**自己的推断**写成了
画像事实，还带着“可能”两个字。

## 二、排查过程

1. 先确认不是去重/落盘问题：条目是 `add` 进来的，类别 `other`、置信 0.6，符合“低置信推断”；
2. 打开抽取 prompt，发现两处结构性诱因：
   - 规则 3 写着“**从行为推断取低**”——等于明确鼓励“可以推断，只是置信写低点”；
   - “画像不是什么”里写着“一次性事件、临时任务、当天安排一律不记”，与 P2c 的
     episodic（下周五出差）**互相矛盾**，模型只能自行取舍；
3. 两条 prompt 都没有一句“禁止写推测/可能性”，解析层也没有任何兜底闸。

## 三、根因分析

- **“取低”= 允许写推断**：只调置信度、不禁止内容，模型会把因果猜测一起写进 content；
- **规则矛盾**：抽取段说临时事项不记，裁决段却有 episodic 契约，模型行为不稳定
  （上一轮“近期加班”被判 episodic，这一轮又被判回 stateful）；
- **没有确定性兜底**：prompt 是软约束，解析层对“可能/也许/大概”完全没有拦截。

## 四、修复方案

1. 抽取 prompt：
   - 规则 3 改为“明确自述取高；**不要从行为反推性格或意图**”；
   - 新增规则“只写用户明确陈述的内容；禁止推测，也不要替用户补因果——不确定就不记”；
   - “不是什么”列表改为“已过去的一次性事件、当天琐事、闹钟提醒；**带明确期限的未来安排除外**”，
     与 P2c 的 episodic 对齐。
2. 裁决 prompt：新增“content 只写用户明确陈述的部分”，并给出示例
   （“下周五可能去贵阳出差” → content 只写“下周五有出差安排”）。
3. 解析硬闸：非 `episodic` 的 add/update，content 含
   `可能/也许/或许/大概/恐怕/应该是/不确定/猜测` → **直接拒收**并计入 `rejected` 遥测；
   episodic 不拦（用户对行程可以不确定），由 prompt 负责只写确定部分。
4. 测试：新增 `TestSpeculationGate` 4 例（推测事实拒收 / 推测 update 拒收 /
   episodic 放行 / 普通事实不受影响）。

## 五、验证结果

```
python -m pytest tests/core/test_profile_resolve.py tests/core/test_profile_memory.py tests/core/test_profile_refine_golden.py -q
→ 137 passed
```

全量回归见提交说明。

## 六、涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/memory/profile_resolve.py` | 抽取/裁决 prompt 改写；`_UNCERTAIN_MARKERS` 硬闸；episodic 与“一次性事件”口径对齐 |
| `tests/core/test_profile_resolve.py` | 新增 `TestSpeculationGate` 4 例 |

## 七、经验总结

- **“置信度取低”不是禁止推断**：只要允许写，模型就会写；要禁止就得在内容层禁止；
- **同一件事的规则要同批改**：抽取 prompt 与裁决契约（episodic）隔了两轮才对齐，
  中间必然生产脏数据；
- **软约束后面要有硬闸**：prompt 负责“尽量不写”，解析层负责“写了也进不来”；
- 用户实机数据比任何测试都直观——`ent_566389cc` 这一条把问题暴露得干干净净。
