# `/init` 配完却连不上：配置只写了盘，没进当前会话

- 发现时间：2026-10-27（阶段 12 P4 真机实测）
- 影响范围：`/init`（三问与 `/init full` 都中）；`jarvis --init` 独立进程不受影响
- 严重度：高——首启正是"配完立刻就能用"的场景，用户会以为配错了

## 现象

隔离 HOME 下跑 `/init`：

```
❓ API Key（回车跳过，稍后用 /init 补）: sk-••••
正在向厂商查询这个账号可用的模型…
厂商返回 2 个可用模型…
✓ 配置已保存到 E:\tmp\jarvis-fresh\.jarvis\models.toml
✓ 连接成功！                        ← 明明通了

> 你好，你是谁
⚠️  网络异常，自动重试中...
❌ LLM 调用失败: 原始错误: Connection error.
```

而启动横幅一直是旧的：

```
provider dashscope   model qwen3.7-plus
```

刚配的是 DeepSeek。**连接测试用新配置跑通了，会话却还在用旧配置。**

## 根因

`/init` 写完 `models.toml` 就结束了。当前 REPL 的 `ctx.provider` / `ctx.loop` /
`ctx.model` 是进程启动时按旧 `settings` 构造的，**从来没有被更新过**：

- `_test_connection()` 直接用本次问答得到的 `vendor` + `api_key` 构造临时客户端 → 所以"连接成功"是真的；
- 下一句对话走的是 `ctx.provider`（旧 dashscope，key 为空）→ 网络层直接失败。

`/model <名字>` 本来就有热切换（`ctx.provider, ctx.loop, ctx.model = result`），
**`/init` 只是没用这条现成的路**。

## 修复

1. `init_command._apply_to_session(ctx, saved_model)`：重读 `settings` →
   把新值**原地**搬进现有 `settings` 对象（其它模块持有同一引用，不能换对象）→
   复用 `model_manager._switch_model` 重建 provider/loop，并回写 `ctx`。
2. `/init`（`handle_init`）把整个 `ctx` 传给 `run_quick_setup`，保存后调用它；
   `jarvis --init` 没有会话上下文，照旧提示"重启后生效"。
3. `/init full` 同样在保存后调用。

回执也改成了描述**真实状态**：

```
✓ 配置已保存到 …/models.toml
已在本会话生效：deepseek / deepseek-v4-pro
```

失败分支不会把"已保存"说成失败：重读失败 → `配置已保存，但重新加载失败（…）；重启后生效`；
切换失败 → `配置已保存，但切到新模型失败（…）；重启后生效`。

## 为什么测试没抓到

首启的单元测试只断到"配置写对了"（`_save_config` 收到什么、`models.toml` 有什么），
`ctx` 在测试里是个 `SimpleNamespace`，**模拟不出"provider 是否真的换了"**。
只有真机把"保存 → 立刻说话"连起来跑一遍才暴露。

补的测试直接钉住这件事：`_apply_to_session` 必须刷新 `ctx.provider/loop/model` 与
`ctx.settings`，且两条失败分支都不能声称"全部成功"。

## 教训

**"✓ 连接成功"和"我现在能用了"是两件事。** 前者只证明凭据有效，后者才证明
配置进了运行中的会话。凡是"改配置"的入口，回执必须回答后者——这也正是阶段 12
自配置层一直在守的口径：`ConfigSet` 有 `apply_mode` 逐档生效，`/init` 却漏了自己。

另一个重复出现的模式：**已经存在的机制（`/model` 的热切换）没有被新入口复用**。
新入口上线前应问一句"这件事现有哪条路已经做对了"。

---

## 残余问题②：热生效了，但复用的还是旧 provider（2026-10-27 第二轮实测）

上一轮修复解决了"不切"，这一轮暴露的是"切了却切错"：日志里明明打印

```
✓ 连接成功！
模型已切换为: deepseek-flash（纯文本（deepseek-flash））
已在本会话生效：deepseek / deepseek-flash

> 你好，你是谁
❌ LLM 调用失败: 原始错误: Connection error.
```

### 根因：比较基准被自己抹掉了

`_apply_to_session` 先把新 `provider/api_format/base_url/api_key` **原地写回
`ctx.settings`**，然后把这份**已被改过**的 settings 交给 `_switch_model`；
而 `_build_switched_provider` 判断"要不要重建 provider"的方式是拿
`[llm.custom_models.<模型>]` 的端点和 settings 比：

```
cfg.base_url == settings.base_url → 无需重建 → 复用入参那个 provider
```

settings 刚被改成和 cfg 一模一样，所以**永远相等、永远复用**，而那个 provider
是启动时用旧配置建的（旧 base_url / 旧 key）。于是：

- `✓ 连接成功` 是 `_test_connection` **新建**的临时 provider 测的 → 真的通；
- 下一句对话走的是**复用的旧 provider** → 打到旧端点 → `Connection error`。

### 修复

1. `_build_switched_provider(..., baseline=None)` / `_switch_model(..., baseline=None)`
   新增可选 `baseline`：**比较用 baseline，构造新 provider 仍用 settings**。
2. `_apply_to_session` 在原地更新**之前**先 `replace(settings)` 留一份快照，
   作为 `baseline` 传下去。REPL 的 `/model`、工作台热切换不传 baseline，
   行为与原来完全一致。

测试钉住两件事：`settings` 已被改成新值时（不传 baseline 会误判复用）
传了 baseline 必须重建；端点在 baseline 上本来就一致时仍然复用（不破坏省重建）。

### 同一批修掉的第三处：`/models` 与 `/init` 的厂商默认地址漂移

"厂商 → 默认 base_url"原先写了两份：`init_command._VENDORS` 与
`model_manager._infer_base_url`。两份已经漂移：

| 厂商 | `/init` 表 | `/models` 推断表 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com/v1` | `https://api.deepseek.com`（**少 `/v1`**） |
| 小米 MiMo | `https://api.xiaomimimo.com/v1` | 无（空串） |

后果：同一个厂商，`/init` 配出来的能连通，`/models` 添加的同厂商模型连不通。
现在抽成单一来源 `agent/config/vendors.py`：`/init` 直接用它，`_infer_base_url`
改为委托 `vendors.default_base_url()`，并加了一条测试断言"表里每个厂商的默认地址
与实际推断结果必须一致"。

顺带修掉桌面壳同款问题：编辑模型时 base_url 留空，旧实现直接走厂商推断，
会把用户手填的 `http://localhost:8000/v1` 默默覆写成厂商默认端点。现在留空
**先沿用原值**，原本也没有才推断（与 `api_key` 留空保持原值同理）。

### 教训补充

**"差异判定"必须拿"正在跑的那一份"当基准。** 先把目标值写进全局对象再去比，
比较就永远相等——比"没生效"更隐蔽，因为回执会理直气壮地说"已生效"。
