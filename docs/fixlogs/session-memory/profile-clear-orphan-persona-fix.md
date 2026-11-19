# 画像清空后旧人设卡永久残留（profile-clear-orphan-persona-fix）

> 日期：2026-11-19 ｜ 作者：aceFelix ｜ 影响范围：`/memory clear`（人设卡与事件轨未清）

## 一、问题现象

P6 落地后做全流程核对，发现 `/memory clear yes` 的语义与实际行为不一致：

1. 执行 `/memory clear yes` 后，`profile.json` 被清空，但 `persona.json` 原封不动；
2. 注入层优先读人设卡，于是**下一次会话仍在注入“已清空”的旧画像**，并显示
   “画像库共 0 条”；
3. `/memory events` 仍能看到已被“清空”的旧行程（`events.jsonl` 未动）；
4. 尝试 `/memory persona rebuild` 补救无效：空库让 `build_persona` 返回 `None`，
   而 `_worker` 的约定是“失败保留旧卡”——**旧卡永久残留**。

复现路径（三步即现）：

```
/memory add 习惯熬夜
/memory persona rebuild   # 生成 persona.json
/memory clear yes
/memory                   # 仍显示人设卡：N tokens · 构建于 ...
```

## 二、排查过程

1. 先看 `/memory clear` 分支：只有 `ProfileStore().clear()` + 重建 system prompt，
   没有任何 persona / events 相关调用；
2. 再看注入侧 `_profile_section`：只要 `load_persona()` 非空就注入卡片，**不校验
   画像库是否为空**；`total = len(store.entries())` 只是文案里的计数；
3. 再确认“能否自愈”：`build_persona` 在 `_sample_entries` 为空时返回 `None`，
   `_worker` 失败分支保留旧卡；`startup_check_async` 对空库直接 `return False`。
   三条路径都不会删除旧卡 → 确认是**永久残留**，不是临时显示问题；
4. 最后看事件轨：`events.jsonl` 是独立 append-only 文件，`clear` 从未触碰。

## 三、根因分析

- **主因**：`/memory clear` 的实现停留在“只有 profile.json 一份数据”的时代。
  P2c 增加事件轨、P6 增加人设卡之后，清空语义没有同步扩展。
- **放大因素**：人设卡的失效链条只有“重建覆盖”，没有“显式丢弃”。而重建在空库时
  必然失败，于是“不删旧卡 + 无法重建”叠加成永久残留。
- **设计教训**：派生数据（人设卡、事件轨）必须跟随主数据一起清；只靠“下次重建会
  纠正”是不够的——重建本身可能因输入为空而无法发生。

## 四、修复方案

| 文件 | 改动 |
|---|---|
| `agent/core/memory/profile_persona.py` | 新增 `discard()`：删除 `persona.json` + `persona_state.json`，并 `reload_profile_cache()`；用 `_in_flight` 非阻塞锁避开正在运行的重建线程（删了也会被 worker 写回），拿不到锁返回 `False` 由调用方如实提示 |
| `agent/core/memory/profile_events.py` | 新增 `clear_events()`：整轨删除并返回删除条数；与 `append_event` 共用进程锁 + 跨进程文件锁；文件不存在 / IO 失败静默返回 0 |
| `agent/commands/handlers/session_commands.py` | `clear` 分支在清空画像后调用上述两者，并分别输出“已丢弃常驻人设卡 / 已清空事件轨（N 条）”；人设卡未能丢弃时明确提示稍后重试 |

取舍说明：

- **删文件而不是写空文件**：空 `events.jsonl` 与“从未写过”语义相同，留着只会让下次
  `append_event` 多一次目录检查；
- **不删除即提示，而不是阻塞等待**：清空操作不能被后台重建线程拖住；重建完成后
  用户重试一次即可。

## 五、验证结果

- 新增单测：
  - `TestDiscard` 3 例（删卡与 pending / 无文件幂等 / 重建中拒绝删除）；
  - `TestClearEvents` 3 例（清空计数与文件消失 / 缺文件幂等 / 清空后可继续追加）；
  - `TestMemoryCommand::test_clear_requires_yes` 扩展为同时断言人设卡文件消失、
    pending 归零、事件轨为空、两条回执出现。
- 相关测试：`tests/core/test_profile_events.py`、`test_profile_persona.py`、
  `test_profile_memory.py` 共 **126 passed**；
- 全量回归：**5383 passed, 2 skipped**；`compileall` 退出码 0；
- 文档同步：`09-记忆与压缩.md`（人设卡“清空”行 + `/memory` 命令表）、
  `guide/commands.md`、`guide/concepts.md`、`CHANGELOG.md`、本复盘与索引。

## 六、涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/memory/profile_persona.py` | 新增 `discard()` |
| `agent/core/memory/profile_events.py` | 新增 `clear_events()` |
| `agent/commands/handlers/session_commands.py` | `clear` 分支一并清人设卡与事件轨 |
| `tests/core/test_profile_persona.py` | `TestDiscard` 3 例 |
| `tests/core/test_profile_events.py` | `TestClearEvents` 3 例 |
| `tests/core/test_profile_memory.py` | 命令级 clear 断言扩展 |
| `docs/architecture/09-记忆与压缩.md` | 清空语义 |
| `docs/guide/commands.md`、`docs/guide/concepts.md` | 命令说明 |

## 七、经验总结

1. **“清空”要按数据族清，不按单文件清**：主数据 + 派生数据（人设卡、事件轨、
   pending 状态）属于同一语义单元，新增派生数据时必须回头检查所有清理入口。
2. **“下次重建会纠正”是危险的假设**：重建可能因输入为空、模型失败、线程冲突而
   根本不发生；删除路径必须显式且幂等，不能寄望于后续重建。
3. **后台线程与删除要互斥**：后台 worker 会在结束时写盘，删除必须与它共享同一把
   锁；拿不到锁时如实返回失败，比“假装成功”更安全。
4. **回归测试要断言副作用，不只断言主对象**：clear 的旧测试只断言 `len(store) == 0`，
   所以漏掉了两个并存的派生文件。以后凡是“清空/删除”类操作，测试至少覆盖该操作
   涉及的所有落盘路径。
