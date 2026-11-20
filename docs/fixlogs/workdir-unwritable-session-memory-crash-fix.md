# 只读工作目录下启动崩溃：项目级会话记忆无降级

> 状态：**已实施**（2026-11-20，本地 `jarvis` 仓库；PyPI 版需重新发版）
> 关联：`default-workspace-fallback-fix.md`、`checkpoint-systemdir-block-fix.md`

## 1. 问题现象

从 `C:\Program Files` 启动 jarvis，MCP/画像/LSP 都正常打印后，直接 traceback 退出：

```
File "agent\prompts\system.py", line 264, in build_system_prompt
    session_mem = load_session_memory(workdir)
File "agent\core\memory\compactor.py", line 540, in load_session_memory
    mem_path = _session_memory_path(workdir)
File "agent\core\memory\compactor.py", line 479, in _session_memory_path
    os.makedirs(jarvis_dir, exist_ok=True)
PermissionError: [WinError 5] 拒绝访问。: 'C:\Program Files\.jarvis'
```

用户原话：「这个提示怎么回事，太不友好了」。

## 2. 排查过程

1. 报错点在 `build_system_prompt` → `load_session_memory`，属于**启动时拼系统提示**
   的必经路径；
2. `_session_memory_path()` 实现：

   ```python
   jarvis_dir = os.path.join(workdir, ".jarvis")
   os.makedirs(jarvis_dir, exist_ok=True)
   return os.path.join(jarvis_dir, "SESSION_MEMORY.md")
   ```

   无条件在 **workdir 下**建目录；
3. `load_session_memory()` 的 `try` 在 `_session_memory_path()` **之后**：

   ```python
   mem_path = _session_memory_path(workdir)   # ← 异常从这里抛出，还没进 try
   if not os.path.exists(mem_path): return ""
   try: ...
   ```

4. `update_session_memory()` 的 try 覆盖了路径解析，所以“压缩时写记忆”不会崩，
   只有“启动时读记忆”会崩。

## 3. 根因分析

1. **项目级状态写进 workdir**：会话记忆设计为 `<workdir>/.jarvis/SESSION_MEMORY.md`，
   在只读目录（Program Files、System32）上必然失败；
2. **没有降级路径**：既没有用户级回退，也没有“读不到就当没有”的兜底；
3. **异常位置错**：try 写在 makedirs 之后，等于没保护；
4. **能力边界不清**：会话记忆是旁路增强，却放在了启动关键路径上，失败直接
   等于“不能启动”。

## 4. 修复方案

`agent/core/memory/compactor.py`：

- `_session_memory_path()` 改为「两级候选 + 可写探测」：
  1. `<workdir>/.jarvis/SESSION_MEMORY.md`
  2. `~/.jarvis/memory/projects/<目录名-哈希>/SESSION_MEMORY.md`
- **写探测**：`makedirs(exist_ok=True)` 在已存在的只读目录上不会报错，必须
  实际 `open(probe, "a")` 才算可写；
- 两个位置都不可用 → 返回空串，调用方按“无会话记忆”处理；
- 结果按 workdir 缓存，避免只读目录下每轮重复抛/捕 `PermissionError`；
- 回退到用户级时 `diag_warn` **提示一次**（不是错误，是能力降级说明）；
- `load_session_memory()` 把路径解析移进 try，任何异常返回 `""`；
- `update_session_memory()` 空路径直接返回 `None`。

## 5. 验证结果

**单测**：`tests/core/test_session_memory_fallback.py` 新增 2 例：

- 只读 workdir → `load_session_memory` 返回 `""` 不抛，路径回退到用户级；
- 回退目录可写 → 压缩结果写到回退目录，并能读回。

**回归**：`tests/core` 全量 `1352 passed, 1 skipped`（改动前后一致）。

**仍待实机验证**：

1. `cd C:\Program Files && jarvis` → 进 REPL，首屏一行“会话记忆改存到 …”；
2. 在可写项目目录 → 记忆仍写在 `<workdir>/.jarvis/SESSION_MEMORY.md`（不回退）。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/memory/compactor.py` | 两级路径解析 + 写探测 + 用户级回退 + 缓存 + 全程不抛 |
| `tests/core/test_session_memory_fallback.py` | 新增 2 例回归 |
| `docs/fixlogs/workdir-unwritable-session-memory-crash-fix.md` | 本复盘 |

## 7. 经验总结

- **旁路能力的失败不能升级为主流程失败**。会话记忆读不到就当没有；启动
  不该因它中断。
- **`exist_ok=True` 不等于可写**。已存在的只读目录上 makedirs 会静默成功，
  必须做一次真实写探测。
- **try 的位置决定它有没有用**：`try` 写在可能抛异常的调用之后，等于没写；
  路径解析本身也是 IO，必须一起包进去。
- **能力降级要报告**：回退到用户级目录说明一次即可，既不让用户误以为
  “记忆丢了”，也不刷屏。
