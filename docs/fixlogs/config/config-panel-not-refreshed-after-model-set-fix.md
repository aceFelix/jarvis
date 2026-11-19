# 模型改完配置，界面右边的滑杆还是旧值（阶段 12 自配置层）

> 实测发现：aceFelix，2026-10-26　|　修复：同批　|　影响面：桌面设置面板 + 感知源面板

## 现象

对话里说「jarvis 把播报的音量改成 50」：

- 贾维斯回执「播报音量：100 → 50」；
- `~/.jarvis/settings.toml` 里确实是 `[tts] volume = 50`；
- **右栏设置面板的「播报音量」滑杆仍停在 100**。

用户的第一反应是"改了但没生效"。而真正的危险在后面：这时候顺手拖一下滑杆，面板会
把 `100` 通过 `settings.set` 又写回去，**静默覆盖掉模型的改动**——文件、运行时、
面板三者会重新"一致"，只是都变回了用户以为已经改掉的那个值。

## 根因

设置面板的数据流是**单向一次性的**：

```
后端 init 事件 ──► dispatcher: conn.refreshSettings() ──► settings.get
                                                          │
                                       settingsStore.backendSettings（面板镜像）
                                                          │
面板滑杆 onChange ──► setBackendSetting（乐观更新 + settings.set 写回）
```

`refreshSettings()` 全仓只在 `dispatcher.ts` 的 `init` 分支被调用一次（连上后端时）。
之后无论谁改了配置——面板自己（乐观更新覆盖得到）还是**模型**（面板完全不知情）——
镜像都不会再拉。

`ConfigSet` 走的是「外科式落盘 + 改运行时 Settings」，这两处都对了，但它运行在
**后端进程**里，没有向 WebSocket 客户端说过一个字。面板于是维持着连上那一刻的快照。

同样的缺口在感知源面板上也存在：`PerceptSourcePanel` 的状态是组件自己的 `useState`，
只在挂载时 `fetchPerceptStatus()` 一次。模型关掉某个源时，卡片照旧显示"已开启"。

## 修法：加一条 `settings_changed` 广播

### ① 配置层：把「谁改了、改了什么」独立广播出去

`agent/config/config_specs.py` 新增变更监听器（与既有的生效钩子**分开**）：

```python
register_change_listener(fn)   # 宿主登记
notify_changed({"tts_volume": 50})   # 工具改完调；监听器抛异常一律吞掉
```

为什么不复用生效钩子：钩子按 `apply_mode` 登记，而 `live` 类项（音量、语速）本来就
不需要"生效"动作，也就没有钩子可挂——**可界面恰恰在这时候最容易说错话**。
"怎么生效"与"谁该知道"是两件事。

`agent/tools/config_tool.py` 在「改运行时 + 跑生效钩子」之后调一次
`notify_changed({协议键: 新值})`。三个不该广播的路径都单独验证过：

| 情形 | 广播？ | 理由 |
|---|---|---|
| 真的改了 | ✅ | |
| 值没变（无变更被剔掉） | ❌ | 没变就没有重拉的必要 |
| 校验失败 / 禁改项被拒 | ❌ | 值没落地 |
| 监听器自己炸了 | 照样算成功 | 界面刷新失败不该让配置写入变失败 |

### ② 宿主：推进事件队列

`agent/serve/config_hooks.py` 登记一个监听器，往 serve 事件队列投：

```json
{"type": "settings_changed", "payload": {"keys": ["tts_volume"], "source": "model"}}
```

事件泵把 `{"type","payload"}` 原样映射成线缆上的 `{"event","data"}`，桌面壳收得到。
没有事件队列（终端 REPL）就当无事发生，不报错也不假装发了。

### ③ 桌面壳：收到就重拉，不自己拼值

```ts
case 'settings_changed': {
  void conn.refreshSettings()                       // settings.get 是权威口径
  if (keys.some(isPerceptConfigKey)) conn.refreshPercept()
  break
}
```

- **不用 payload 里的值回填**：那要再写一份宽容解析，而 `settings.get` 已经是现成的
  权威口径（还能顺手拿到模型没改的键）。一次 set 只带一组键，重拉开销可忽略。
- 感知键判定放进 `shared/percept-contracts.ts` 的 `isPerceptConfigKey()`，不是就地
  写个 `endsWith('_enabled')`：协议层的总开关叫 `percept_enabled`，而面板内部的
  `PerceptConfig` 叫 `enabled`——只判后缀会漏掉"模型把感知整个关了"这件最该看见的事。
  判错的代价不对称：多认一个只是多拉一次，漏认则面板一直显示旧开关。
- 感知面板的状态是组件局部 `useState`，所以 `refreshPercept()` 只递增 store 里的
  `perceptTick` 计数器，面板把它加进 effect 依赖后自动重拉——不新开第二条数据通路。

## 验证

| 层 | 用例 | 钉住什么 |
|---|---|---|
| `tests/config/test_config_specs.py` | `test_change_listener_receives_new_values_once` | 监听器拿到新值，且同一回调不重复登记 |
| 同上 | `test_change_listener_is_not_called_for_empty_changes` | 空变更不广播 |
| 同上 | `test_broken_change_listener_does_not_break_the_write` | 监听器抛异常被吞，后续监听器照收 |
| `tests/tools/test_config_tool.py` | `test_change_is_broadcast_with_new_values` | 只广播**真变了**的键与新值 |
| 同上 | `test_noop_change_is_not_broadcast` / `test_failed_write_is_not_broadcast` | 未变 / 失败都不广播 |
| 同上 | `test_broken_listener_does_not_fail_the_tool` | 回执仍是"已改成功" |
| `tests/serve/test_config_hooks.py` | `test_config_set_broadcasts_settings_changed_event` | 事件真的进了队列，且信封是 `{keys, source}` |
| 同上 | `test_no_event_when_write_is_a_noop` | 无变更不入队 |
| `test/renderer/dispatcherSettings.test.ts` | 7 例 | 重拉设置 / 感知键才刷感知面板 / 总开关也算感知键 / 坏 payload 不炸 |

实机判据：说「把音量改成 50」→ 右栏滑杆**立刻**跳到 50；再拖回 80 → 文件里也变 80
（两条路互相看得见，不会再互相覆盖）。

## 复现路径（修好前）

1. 开桌面壳，右栏点 `[SET]` 进设置面板，记下滑杆位置（如 100）；
2. 对话里说「把播报音量改成 50」；
3. 回执显示已改，`settings.toml` 也是 50，**但滑杆还在 100**；
4. 把滑杆拖一下（哪怕拖回原位），`settings.toml` 会变回 100 —— 模型的改动被覆盖。

## 附带教训

**"改成功"与"用户看得见"是两件事。** 这一层的单元测试全绿（落盘对、运行时对、
回执对、`/undo` 对），因为被测的是后端进程内的行为；而面板显示的是另一条数据通路上的
快照。凡是"跨进程/跨层展示"的值，改完必须显式推一次通知，否则测试再绿用户也看不到。
同类前车：`tasks.*` / `actions.*` 侧栏区块、`percept.status` 面板，都靠面板自己拉；
这次把它们共同的缺口补成了协议级事件，后续新增可改项不必逐个加刷新逻辑。

@author aceFelix
