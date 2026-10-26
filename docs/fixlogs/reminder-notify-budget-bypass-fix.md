# 用户提醒不过打扰预算 + 静默时段写法无声失效（2026-10-22）

- **模块**：`agent/core/policy/budget.py`、`agent/serve/hub.py`、`agent/core/daemon/proactive.py`
- **发现方式**：人工实机测试（阶段 10 走查场景 3）
- **严重度**：功能失效（用户显式设置的两个预算参数对提醒完全不起作用）

## 现象

用户按走查清单在自己的 `~/.jarvis/settings.toml` 里配了：

```toml
[policy.notify_budget]
enabled = true
daily_max = 2
quiet_hours = "23:00-7:00"
aggregate_window_s = 0
min_salience = 0.3
```

然后设了三条提醒，23:25 / 23:26 / 23:27 **三条全部语音播报**（截图为三条气泡齐刷刷上屏）。
按配置，这三条应该：命中静默时段 → 一条都不出声；即便不算静默，`daily_max=2` 也该压掉第三条。
两个参数同时"没生效"。

## 排查

顺着"配置到底被谁读过"往下查，查出两个独立缺陷，叠在一起才表现为"三条全响"：

1. **`quiet_hours = "23:00-7:00"` 的写法让整条静默时段无声关闭。**
   `budget._in_quiet()` 用 `time.fromisoformat()` 解析端点，而它只接受两位小时
   （`07:00`），`7:00` 抛 `ValueError`；旧代码捕获异常后 `return False` ——
   **整个静默时段直接失效，且日志一个字都不写**。用户看到的是"改了配置没反应"，
   实际是"配置写法不被接受，系统拒了还不说"。

2. **用户自设提醒这条线根本没接闸门。**
   `serve/hub.py::_on_fire()` 对非引擎任务是这样的：

   ```python
   self._emit(kind="reminder", title="贾维斯提醒", text=task.content, task_id=task.id)
   ```

   ——不经过 `gate_notify`。而简报、截止日期、感知事件、W3 任务脊柱**都**过闸。
   旁证很硬：`budget.py` 的权重表里 `"schedule": 0.9  # 用户自己设的提醒` 早就登记了，
   `tests/policy/test_notify_budget.py` 里也早就断言 `schedule` 在夜间应回 `queue` ——
   **设计意图与实现脱节**：闸门以为自己管着提醒，提醒那边压根没来敲门。

   交叉验证支持这个判断：同一时段内简报确实被压住了（它走 `_dispatch_notify → gate_notify`），
   只有提醒全响 —— 问题在编排线，不在预算算法。

3. **顺手查实："那就让提醒入库待补播"也不能接受。**
   - 冲刷只在**新的 `gate()` 请求**时触发，没有定时器；夜里没动静 = 永不补播。
   - `ScheduleTask.escalate_count` / `max_escalate=3` 这套升级重发**没有任何地方递增**，
     指望它兜底是空的。

   所以把提醒归进 `queue` 只是把"不响"换成"可能永远不响"，仍不合用户意图。

4. **第三处只有在动手实测时才会撞上：“不朗读”其实只做了一半。**
   `hub._emit(speak=False)` 只跳过 TTS 线程，事件 payload 里没任何“静默”信息；
   而桌面壳的 `notify.ts` 是 `new Notification({ title, body })`，Windows 缺省的
   toast **自带提示音**。结果就是：TTS 闭嘴了，系统通知仍会响一声——静默时段白设。
   修它需要两个仓一起动（payload 加 `silent` + 壳侧透传给 `Notification`）。

## 根因

- 解析层：严格 ISO 格式 + 异常静默回落，缺容错也缺警告。
- 编排层：新增播报源时只登记了权重、没接线；"所有打扰都过同一个闸"这条不变量
  在提醒这条线上是破的，而它破得很安静——预算的状态文件、日志、面板都不会提到有一条提醒被漏放。

## 修复

1. `agent/core/policy/budget.py`
   - 新增 `parse_clock()` / `parse_quiet_range()`：个位数小时、首尾空格都收
     （`"23:00-7:00"` 与 `"23:00-07:00"` 等价）；真正不可用的写法仍返回 `None`。
   - `_in_quiet()` 改用新解析；`NotifyBudget.__init__` 对不可用的 `quiet_hours`
     `logger.warning`，**不再无声关闭**。
   - `NotifyAction` 增 `speak: bool = True`；`gate(..., silent_delivery=False)` 新增参数：
     为真时把②静默时段与⑤额度用尽的"入库待补播"改成**降级投递**
     （`kind="emit", speak=False`：气泡与系统通知照投、TTS 不朗读、不入队、
     **不占 `daily_max`**，但仍记 `last_emit`，所以同一条提醒重复不会刷屏）。
   - 抽 `_merge_recent()` / `_mark_emitted()`：降级路径与正常路径共用同一条聚合窗口规则，
     避免"一处合并一处不合并"的分叉。

2. `agent/core/daemon/proactive.py`
   - 闸门实现改为 `notify_gate_action(...)`，返回**完整裁决**（含 `kind` 与 `speak`）。
   - `apply_notify_gate()` 降为它的薄包装，旧调用点与"预算未注入时仅 away 静默"的行为一字不变。
   - away 只把**会出声的** `emit` 降为 `queue`：降级投递本就不发声，气泡上屏无害。

3. `agent/serve/hub.py`
   - `_on_fire()` → 新增 `_deliver_reminder()`：提醒交给
     `gate_notify_action(source="schedule", dedup_key="schedule:<task_id>", urgency=0.9)`，
     按裁决决定投不投、朗不朗读。
   - 新增 `_reminder_channels()`：只认已实现通道，未实现的（含 `sms`）**警告一次并忽略**；
     结果按配置原值缓存，避免每条提醒都刷一遍同样的警告。

4. `agent/serve/quiet_delivery.py`（新增，从 hub 拆出）
   - 通道解析 `parse_channels()`、邮件正文 `build_quiet_email()`、发送 + 入账
     `send_quiet_email()`。**不进 `[actions]` 延时窗口**（这封信要的就是此刻送达），
     但记一条 `reversible=False` 的副作用账，`/undo` 据此如实报"已离开本机，撤不回"。
   - 拆出理由：hub 已承担事件队列 + 感知总线 + 任务脊柱接线，加完提醒编排会破 800 行上限。

5. `agent/tools/extensions/email_tool.py` + `agent/ui/workbench/api.py`
   - 抽 `send_email_now(settings, args)`：不经模型、不跑 `check_permissions` 的直发入口。
     桌面「管家服务 → 邮件发送」表单原本自己写了一份 `asyncio.run(SendEmailTool().call(...))`，
     静默改道若再写一份就是两份实现；现在两边同引一处。

6. 配置三处同步：`agent/config/settings.py`（`notify_reminder_channels` /
   `notify_quiet_email_to`）、`agent/config/settings_domains.py`（`[policy.notify_budget]`
   的 `reminder_channels` / `quiet_email_to` 映射）、`configs/settings.example.toml`。

7. 跨仓那一半（jarvis-desktop，同一天的补刀）：
   - `hub._emit` 给 payload 加 `silent`（缺省跟随 `speak`）；首次引导那类“只躲长文
     TTS 朗读、却要 toast 醒目”的调用显式传 `silent=False`，两个意图不共用一个开关。
   - `contracts.ts`：`NotifyRequest` 与 `ProactiveNotifyPayload` 各加 `silent?: boolean`。
   - `dispatcher.ts` 透传、`notify.ts` 仅在 `silent === true` 时给 `Notification` 写该键
     ——**不把 `silent: false` 也写进去**，否则旧后端行为零变化这件事会变成一堆伪回归断言。

**通道取向（用户拍板）**：静默时段**气泡要投**（不播报）；短信"先不做，未来再加" —— 所以
`sms` 只做枚举留位，配了就警告并忽略，**绝不假装已发**。邮件默认不开（`reminder_channels`
默认 `["screen"]`），要信自己加 `"email"`。

## 验证

```
python -m pytest tests/policy -q                        # 61 passed
python -m pytest tests/serve tests/policy -q             # 257 passed
python -m pytest tests/tools/test_email_send_now.py -q             # 2 passed
python -m pytest -q                                      # 3231 passed，无回归
python scripts/check_md_links.py                        # dead links: 0 / untracked: 0
npx vitest run                        # jarvis-desktop：25 files / 527 passed
npm run typecheck                     # jarvis-desktop：node + web 两套均无错误
```

新增 17 例覆盖：静默/超额降级投递（不入队、不占额度、重复仍合并）、不传参数时行为零变化、
提醒确实带正确 source/dedup_key/urgency 过闸、`queue` 裁决不投事件、降级时上屏但不朗读、
email 通道触发发信、`sms` 忽略且警告只刷一次、发信成功入账 / 失败不入账也不抛、
`parse_channels` 大小写与未知值、away 与降级的优先级、`send_email_now` 三条契约。

**待人工复验**（写本文件时未做）：桌面壳重启后按 `docs/test/w3-testing.md` §6b 逐条走查，
预期"气泡弹了但屋里没出声"，且 `~/.jarvis/notify_state.json` 里这条既不写 `pending`
也不 `count+1`。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/core/policy/budget.py` | `parse_clock`/`parse_quiet_range`、非法写法警告、`NotifyAction.speak`、`gate(silent_delivery)`、`_merge_recent`/`_mark_emitted` |
| `agent/core/daemon/proactive.py` | `notify_gate_action()`（完整裁决）+ `ProactiveEngine.gate_notify_action()`；`apply_notify_gate()` 降为包装 |
| `agent/serve/hub.py` | `_on_fire` → `_deliver_reminder`（过闸 + 按通道投递）、`_reminder_channels`、`_send_quiet_email` |
| `agent/serve/quiet_delivery.py`（新增） | 通道解析 / 邮件正文 / 发送与不可逆副作用入账 |
| `agent/tools/extensions/email_tool.py` | 抽 `send_email_now()` |
| `agent/ui/workbench/api.py` | `send_email` 改调 `send_email_now`（去掉重复实现） |
| `agent/config/settings.py`、`settings_domains.py`、`configs/settings.example.toml` | `reminder_channels` / `quiet_email_to` 两个字段 |
| `tests/policy/test_notify_budget.py` | 降级投递 4 例 |
| `tests/serve/test_hub_reminder_delivery.py`（新增） | 接线与通道 11 例 |
| `tests/tools/test_email_send_now.py`（新增） | 直发入口契约 2 例 |
| `tests/serve/test_hub.py`、`tests/serve/test_hub_percept.py` | 提醒测试补预算桩、闸门桩补 `silent_delivery` 参数 |
| `docs/test/w3-testing.md`、`docs/fixlogs/serve-policy-config-not-injected-fix.md` | §6b 新增人工走查节；旧 fixlog 的“提醒只入库”预期按新行为改写 |
| `docs/architecture/16-W1判断层.md`、`config-docs/configuration.md`、`docs/guide/desktop.md`、`docs/roadmap/PLAN-butler-autonomy-upgrade.md`、`docs/blueprint/BLUEPRINT.md`、`README.md` | 口径同步（含把"提醒不过闸、保持不变"的原设计决策改为"纳闸但不延后"） |
| `jarvis-desktop/src/shared/contracts.ts` | `NotifyRequest` / `ProactiveNotifyPayload` 各加 `silent?: boolean` |
| `jarvis-desktop/src/renderer/src/api/dispatcher.ts`、`src/main/notify.ts` | `silent` 透传到 Electron `Notification`（toast 不出声） |
| `jarvis-desktop/test/main/notify.test.ts`、`test/renderer/dispatcherNotify.test.ts` | 静默透传与“缺省不写该键”共 3 例 |
| `jarvis-desktop/docs/development.md`、`docs/guide/installation.md` | 新增静默降级走查项；单测计数 227 → 527（陈旧值修正） |

## 经验总结

1. **判据不能只看权重表有没有登记这个源。** `schedule` 在 `_DEFAULT_SOURCE_WEIGHTS` 里躺了很久，
   还配了一条"夜间应回 queue"的单测，但提醒那条线压根没调闸门 —— 权重表和单测都在自我安慰。
   查"这个源到底过不过闸"，要看它的投递点有没有 `gate_notify`，不是看权重表。
2. **解析失败静默回落是最坏的失败形态。** 用户写成 `"23:00-7:00"`（很自然的写法），系统拒了、
   还整条关闭静默时段、日志零字 —— 用户只能得出"这功能是写死的"。容错 + 一句警告，
   比严格 + 沉默便宜得多，也好解释得多。
3. **"送达"和"出声"必须能分开决策。** 闸门只有"播/不播"两个出口时，把用户亲口要的提醒归到
   "不播"就等于丢了它；加一个 `speak` 标记，才谈得上"命中静默时段时降级而不是延后"。
   抽象要贴语义：延后不是降级的唯一形式。
4. **不变量要有可执行的看门方式。** "所有打扰都过同一个闸"写在文档里，但破口出现在没被文档
   照到的编排线上。`escalate_count` 有字段无递增也是同一类问题：接口留了位、线没接，
   看起来像已实现。凡是"有字段/有权重/有单测"的地方，都要问一句调用点在哪。
5. **改一条投递线要顺带核它经过的三个入口**（`apply_notify_gate` / `gate_notify` / 直投 `_emit`），
   并把设计文档里那句"保持不变"一起改掉 —— 否则下一次有人照着旧决策把它改回去。
6. **“不出声”这个意图横跨两个仓，不是单仓能说完的。** 后端把 TTS 关干净了，Windows toast
   仍会响；这类“意图”必须随事件走完整链路到渲染端，所以 payload 里需要一个显式
   `silent`，而不是假定“两端都知道 speak=False 的含义”。同时要给两种意图留出区分：
   躲长文朗读（要 toast 醒目）与静默投递（两者都关）不是同一个开关。
