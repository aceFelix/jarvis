# `/models` 改完配置不生效：提示说"已切换"，实际还连旧端点

> **类型**：缺陷修复（配置热生效链路）
> **影响面**：终端 `/models → 修改配置`；桌面壳同一场景早已修好，不受影响
> **修复日期**：2026-10
> **相关文件**：`agent/model_manager.py`、`agent/commands/handlers/model_commands.py`
> **相关用例**：`tests/commands/test_models_edit_applies.py`（新建，5 例）、
> `tests/llm/test_model_manager_extra.py`（+5 例）

---

## 0. 一句话结论

用户改的是**正在运行中的那个模型**，而终端这条链有两处"同名就当没事发生"的短路，
把新配置整个挡在门外。桌面壳早就用 `force=True` 绕过同一个坑，终端没跟上。

---

## 1. 现象

用户把某个模型的配置改掉（厂商/Key/Base URL/模型类型任意一项）之后：

```
> /models
模型「deepseek-flash」配置已更新
模型已切换为: deepseek-flash（多模态（deepseek-flash））
> 贾维斯在吗
⚠️  网络异常，自动重试中...
❌ LLM 调用失败: 原始错误: Connection error.

[ 网络错误 ]
API 服务连接失败或超时。
```

**提示词面是完整的**：「配置已更新」+「已切换为 xxx（多模态）」——
看上去一切正常。但下一句话打出去的请求，用的仍是**改之前的那套端点**。

用户的描述很准确：「新配置的模型他不能马上生效，导致 jarvis 依旧连接的是老的
失效的配置」。

---

## 2. 根因：三处短路叠加

### ① `handle_models`：选中的就是当前模型 → 什么都不做

```python
        elif picked != ctx.model:          # ← 编辑的往往正是当前模型
            result = _switch_model(...)
            if result:
                ctx.provider, ctx.loop, ctx.model = result
        break
```

用户改的是运行中的 `deepseek-flash`，回到列表后再选它，`picked == ctx.model`
成立 → **整条切换链路不执行，也不给任何提示**。

### ② `_switch_model`：同名直接 `return None`

```python
    old_model = getattr(provider, '_model', '')
    if model_name == old_model:
        return None                        # ← 同名即"无需切换"
```

即便调用方真的调了它，这里也会立刻返回，provider 不重建。

### ③ `_build_switched_provider`：靠"差异"决定是否重建，判据偏窄

```python
        if api_fmt.lower() != current_ptype:
            needs_new_provider = True
        elif cfg.get("base_url") and cfg["base_url"] != base.base_url:
            needs_new_provider = True
        elif cfg.get("api_key") and cfg["api_key"] != base.api_key:
            needs_new_provider = True
```

编辑表单**预填**的是该模型当前的 `base_url` / `api_key`。用户如果只改了
`model_type`（纯文本 ↔ 多模态），三个条件全不成立 → 判定"无需重建"。
如果改了 Key 但 `baseline` 恰好也持有同一个新值，同样被吃掉。

---

## 3. 关键事实：这不是新问题，桌面壳早就修过了

`agent/ui/workbench/model_switch.py` 的 `force` 参数，注释写得很清楚：

```python
        force: 目标与当前同名时仍按现配置重建 provider —— models.edit 改了
            运行中模型的端点/模型类型后用（否则「同名即当前」短理会把改动
            挡在门外），提示文案也随之改为「配置已更新」。
```

也就是说：**同一件事，桌面壳走 `force=True` 是对的，终端的 REPL 路径从来没实现**。
两套入口各写一份逻辑，修了一边忘了另一边 —— 和上一个语音 Key 的问题
（`/talk` 对、`/voice` 错）是同一个模式。

---

## 4. 修复

### 4.1 打通 `force` 通道

`_switch_model` 与 `_build_switched_provider` 都新增 `force: bool = False`：

```python
    old_model = getattr(provider, '_model', '')
    if model_name == old_model and not force:      # ← 加 not force
        return None
```

```python
        # force 时一律重建：编辑运行中的模型不能靠差异判断
        needs_new_provider = bool(force)
```

不带 `force` 时行为**完全不变**（普通同名选择不会白建一次连接）。

### 4.2 编辑当前模型后立即重建

`_edit_custom_model` / `_edit_builtin_model` 的返回值从 `None` 改为
**最终保存的模型名**（`str | None`）。返回名字而不是 bool，是因为调用方需要知道
"改的是不是运行中的那个模型"，以及"改名后应该切到哪个新名字"。

`handle_models` 里：

```python
            saved: str | None = None
            if action == "edit":
                saved = _edit_builtin_model(...) if is_builtin else _edit_custom_model(...)
            ...
            # 改的正是运行中那个模型：同名也必须按新配置重建 provider
            if saved and cname == ctx.model:
                result = _switch_model(..., saved, force=True)
                if result:
                    ctx.provider, ctx.loop, ctx.model = result
            continue
```

### 4.3 选中当前模型不再静默

```python
        else:
            ui.info(
                f"「{picked}」已经是当前模型。想改它的配置："
                "在列表里按【空格】打开管理菜单 → 修改配置（保存后立即生效）"
            )
```

不静默（用户会以为切了），也不默默重建（没改东西，白白断一次连接）。

### 4.4 顺手修：改名保存失败会丢配置

原顺序是**先删后存**：

```python
    if new_name != name:
        _remove_custom_model_from_toml(name)
        del settings.custom_models[name]        # ← 内存里先删掉
    if save_custom_model(new_name, new_config):  # ← 这里失败，旧配置就没了
```

一次写盘失败（磁盘满、权限、文件被占用）就会把用户现有模型从内存里抹掉。
改成先存新名、成功后再删旧名。

---

## 5. 用户侧的行动

改完配置不需要重启：

```
/models
  → 选中目标模型 → 按【空格】→ 修改配置 → 改完保存
  → 提示「配置已更新」后立刻生效，下一句话就用新端点
```

**注意**：`force` 重建是**重新构造 provider**，正在进行的对话上下文不受影响
（QueryLoop 换 provider，会话历史保留）。

---

## 6. 测试

### 6.1 新增 `tests/commands/test_models_edit_applies.py`（5 例）

测的是**链路**——谁该调 `force`：

| 用例 | 锁什么 |
|---|---|
| `test_edit_current_model_forces_rebuild` | **核心回归**：编辑运行中模型 → `_switch_model(force=True)` |
| `test_edit_other_model_does_not_touch_running_provider` | 改别的模型不该断当前连接 |
| `test_cancelled_edit_does_nothing` | 取消编辑 → 不切 |
| `test_rename_follows_new_name` | 改名后切到**新**名字，`ctx.model` 同步 |
| `test_picking_current_model_tells_user_how_to_edit` | 选中当前模型不是静默，且指出怎么改配置 |

### 6.2 `tests/llm/test_model_manager_extra.py` 新增（5 例）

| 用例 | 锁什么 |
|---|---|
| `TestSwitchModel::test_force_rebuilds_even_when_name_matches` | 同名 + `force` 必须重建，且用**新** base_url |
| `TestSwitchModel::test_force_off_still_short_circuits` | 不带 force 保持原行为（不白建 connection） |
| `TestEditCustomModel::test_returns_saved_name_for_caller` | 返回最终模型名（改名场景返回新名） |
| `TestEditCustomModel::test_cancel_returns_none` | 取消返回 `None` |
| `TestEditCustomModel::test_save_failed_returns_none_and_keeps_old` | **写盘失败不能丢旧模型**（先删后存那个坑） |

### 6.3 验证

- `pytest tests`：**3872 passed**，exit=0
- 无回归；`tests/llm` + `tests/commands` + `tests/config` 全绿

---

## 7. 教训

### ① 同一个能力在两套入口各写一份，就一定会漂移

这已经是**连续第两个**同类问题：

| 场景 | 写对的那份 | 写错的那份 |
|---|---|---|
| 语音 Key 解析 | `/talk`（带厂商判断） | `/voice`（无条件回退主 key） |
| 模型配置热生效 | 桌面壳 `model_switch(force=True)` | 终端 `/models`（同名即跳过） |

判断标准很简单：**如果某个能力有 A/B 两个入口，先搜另一个入口是怎么做的**。
`force` 这个参数是桌面壳在上一轮就已经想明白的结论，终端只要抄过来就行。

### ② "提示说成功"和"真的成功了"是两件事

这次的输出特别有迷惑性：

```
模型「deepseek-flash」配置已更新      ← 配置确实写盘了
模型已切换为: deepseek-flash（多模态）  ← 这句话是 _switch_model 打印的
```

但如果切换链路根本没走到 `_switch_model`，第二句话根本不出现。
所以**光看提示文案判断不了**——必须验"下一句话用的端点对不对"。
这也是为什么修复后要把断言写成 `_switch_model` 的**参数**（`force=True`），
而不是断言"有没有打印提示"。

### ③ 静默的 `else` 分支是 bug 温床

`elif picked != ctx.model:` 这一行，把"选中当前模型"这个**最常见的操作**
送进了一个什么都不做的 `else` 分支。用户唯一的感知就是"我操作了但没反应"。

规则：交互式流程里，**用户做了一个明确动作，就必须有明确反馈**——
哪怕反馈是"这已经是当前状态了"。

### ④ 先删后存 = 把失败变成一个更坏的失败

`del` 之后才 `save`，一旦 `save` 失败，用户丢的不是"这一次修改"，而是
"原来那份能用的配置"。写操作的正确顺序永远是：**先在目标位置写成功，再清理旧的**。
