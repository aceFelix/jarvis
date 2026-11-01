# 修复复盘：release-contract 规则库用例在 Linux CI 误红（从 skip 到平台分流断言）

> 作者：aceFelix　日期：2026-11-02

## 1. 问题现象

- 提交 `496cf2a`（新增 `tests/test_release_contract.py`）推送后，GitHub Actions
  Python 3.14 / ubuntu 那一格 `Run tests` 变红：`1 failed, 4105 passed, 48 skipped`。
- 唯一失败项：`test_builtin_rulebook_parses_clean`。
- 报错（节选）：
  `AssertionError: 内置规则库存在被拒绝的规则: [('user_temp', "展开后不是绝对路径：'%LOCALAPPDATA%\\Temp' -> ..."), ('recycle_bin', ...), ...]`
  —— 15 条 `paths` 型内置规则全部被拒。
- 本地（Windows）该测试是通过的，只有 CI（Linux）红。

## 2. 排查过程

1. 先怀疑是不是 `cleaners.toml` 被改坏或漏打包 → 读 `RuleBook.rejected` 的拒绝原因，
   全是"展开后不是绝对路径"，且原样保留 `%LOCALAPPDATA%` / `%SYSTEMDRIVE%` / `%SYSTEMROOT%`。
2. 定位到 `agent/core/maintenance/rules.py` 的 `expand_path`：Windows 风格环境变量靠
   `ntpath`/系统环境展开；POSIX 上没有这些变量，占位符原样留着 → 触发"非绝对路径"这道
   fail-closed 闸 → `paths` 型规则被拒。`command` 型规则无路径，不受影响，故只有 15 条被拒。
3. 结论：被测系统行为**正确**（维护层本就"仅 Windows，POSIX 如实拒绝"），是**测试断言**
   把一个 Windows-only 不变量当成了跨平台不变量。

## 3. 根因分析

- `test_builtin_rulebook_parses_clean` 断言"内置规则库零静默拒绝"，隐含前提是
  `%VAR%` 能展开、路径能落绝对——这在 Linux 上不成立。
- 与既有 `docs/fixlogs/maintenance-tests-linux-ci-fix.md` 同一类：**Windows 专属语义的
  维护层用例缺平台门禁**。CI 是 ubuntu、开发机是 Windows，本地永远复现不了。

## 4. 修复方案（两版：第一版写下来就被推翻）

### 4.1 第一版：`@_WINDOWS_ONLY` skip（已推翻）

给用例打平台门，非 Windows 直接 skip。它确实能让 CI 变绿，但本质是**用“关掉测试”换
“关掉红灯”**：`rules.py` 的校验闸有四道，其中**三道与平台无关**，skip 整条用例把它们
一起丢掉了。

| 校验闸 | 受平台影响？ | v1 skip 后 | v2（最终） |
|---|:---:|:---:|:---:|
| 命令白名单 `ALLOWED_COMMAND_BINARIES` | 否（`command` 型在 Linux 照样校验） | ❌ 失去拦截 | ✅ 保住 |
| `risk` / `kind` 取值合法 | 否 | ❌ | ✅ |
| `auto_ok` 与 `command` / `requires_admin` 互斥 | 否 | ❌ | ✅ |
| 规则库被删空 / TOML 结构坏 | 否 | ❌ | ✅ |
| `paths` 展开后必须是绝对路径 | **是** | 仅这条该按平台分流 | ✅ |

所以 v1 不是方向错，而是**动作过大**——只有最后一道闸需要分流。

### 4.2 第二版：POSIX 上正向断言（最终实现）

把原 `test_builtin_rulebook_parses_clean` 改写为 `test_builtin_rulebook_rejects_only_platform_paths`
（`_ON_POSIX`），五层断言：

1. 拒绝集合**恰好等于**全部 `paths` 型（少一条 = 有 Windows 规则被 POSIX 悄悄吃进；多一条 = 混入非法规则）；
2. 每条拒绝原因必须是平台性的（`_PLATFORM_REJECTION_MARKERS`）；
3. `command` 型**必须全部加载成功**（命令白名单、`risk`/`kind`、`auto_ok` 互斥三道闸与平台无关）；
4. loaded 的 `kind` 与 TOML 声明逐条一致，不得出现声明外的 id；
5. 声明里每条必须出现在 loaded 或 rejected 之一——静默丢弃即红。

共享 `_declared_rules()`（读 TOML 原始声明）与 `_split_book()`。效果上 CI 从“少跑一条”变成
“多一条真正在 Linux 上跑的契约”，并把 README 平台矩阵里「仅 Windows，POSIX 如实拒绝」钉成
可执行证据（README 中/英两处已回指本测试）。不动任何运行时代码。

### 4.3 落地时又砍掉的一次自我重复

写完才核对：`tests/core/test_cleaner_rules.py::test_load_builtin_rulebook_has_all_rules`（同为
`_WINDOWS_ONLY`）**已经在守**「内置规则库零拒绝 + 关键 id 名单 + 刻意不收录名单」。我一度拆出的
Windows 分支第一条 `assert not rejected` 与它逐字重合，还把条数硬编码成 `== 19`，恰恰破坏了它
「用名单、不设死值」的设计（新增规则本不必改测试）。所以该分支整条删除，本文件只留 POSIX 那
一半，并在模块注释里写明分工。

> 教训：同一轮里我已因 `tests/serve/test_serve_protocol.py` 砍过一次重叠，仍然又造了第二个——
> 新增断言前“仓库里是否已存在”得当作固定步骤，不能靠事后想起。

## 5. 涉及文件

| 文件 | 改动 |
|---|---|
| `tests/test_release_contract.py` | 规则库用例改为 POSIX 正向断言 `test_builtin_rulebook_rejects_only_platform_paths`，新增 `_declared_rules` / `_split_book` / `_PLATFORM_REJECTION_MARKERS`；删掉 `_WINDOWS_ONLY`；一度新增的 Windows 重复分支已撤销（由 `test_cleaner_rules` 把关） |
| `README.md` / `README.en.md` | 平台支持表注追加“POSIX 如实拒绝”已由 CI 逐条对账的说明，回指本测试 |
| `CHANGELOG.md` | `[未发布]` 测试条目改写为 POSIX 正向断言（含初版 skip 被推翻的说明） |
| `docs/architecture/19-系统维护层.md` | 「已知边界」表补区分：哪类断言该 skip、哪类在 POSIX 上仍有确定答案应正向断言 |
| `docs/fixlogs/release-contract-rulebook-linux-skip-fix.md` | 本复盘 |

## 6. 验证结果

- 本地（Windows / Python 3.13）：`pytest tests/test_release_contract.py tests/core/test_cleaner_rules.py`
  → **33 passed, 1 skipped**（POSIX 那条在 Windows 上 skip 属预期）；`ruff check` 全部通过。
- **POSIX 断言的本地预验（本次关键）**：不需要 Linux 机器。两平台在这条链上的唯一差异是
  `%LOCALAPPDATA%` / `%SYSTEMDRIVE%` / `%SYSTEMROOT%` 查不到值 → `expand_path` 原样保留
  占位符 → `isabs` 判 False（`ntpath.isabs('%LOCALAPPDATA%\\Temp')` 同样为 False，两平台一致）。
  因此在 Windows 进程内用 `mock.patch.dict(os.environ, …, clear=True)` 摘掉这些变量即精确
  复现 CI：实测 `loaded=4 / rejected=15`，拒因文案与你贴的 CI 日志**逐字一致**。
- **变异测试（证明断言不是摆设）**：5 类坏情形全部被红抓住——command 型被拒、paths 型被
  POSIX 意外加载、规则库被删空、kind 错挂、一条被静默丢弃。探针 7/7 符合预期（已删除，不落库）。
- 砍掉 Windows 重复分支后**重跑了一次探针**（正向 + 3 类变异 4/4 符合预期），确认去重没意外削弱拦截力；
  不靠“我没改这条分支”的假设交付。
- 探针自己踩的一个坑：第一版变异用例把 4 条 command 标成 `command`（等于没变异），误报
  “断言漏抓”；改成标成 `paths` 才是有效变异。**变异测试自身也要验证**，否则会把探针的 bug
  当成被测代码的洞。
- Linux 侧最终结论仍以推送后的 CI 复跑为准；本次为推送前预验，不再是“本地绿就交付”。

## 7. 经验总结

- **一次 CI 误红若以 skip 收尾，必须追问“skip 掉的能力有多少本来能保留”**——绿灯不是目标，
  拦截力才是。v1 那一下就把四类 Linux 本来能抓的事故一起关掉了。
- **跨平台断言可以在 Windows 上预验**：只要平台差异落在“环境变量可见性 / 路径语义”上，
  `mock.patch.dict(os.environ, …, clear=True)` 就能复现，比“推上去等 CI”快一个数量级，而且
  能同时做变异测试。遇到仅 PowerShell / `SecurityCenter2` / WMI 的真实 API 差异才只能靠平台门。
- **写跨平台断言前先分清“哪道校验受平台影响”**，别把整条测试按最受限的那道闸一刀切。
- **文档里的平台声明要有测试兜**：README 写了“POSIX 如实拒绝”，就得有一条在 Linux 上跑的断言
  证明它——否则这句话会被自己的测试证伪（本次正是如此）。
- **新增断言前先查“仓库里是否已存在同一条”**，这得是固定步骤：本次先撞了
  `test_serve_protocol.py`（已砍），自以为改了，写完才发现又与
  `test_cleaner_rules.py::test_load_builtin_rulebook_has_all_rules` 撞了一次。
- 与既有 `docs/fixlogs/maintenance-tests-linux-ci-fix.md` 同根因，但修法不同：那次是纯 Windows
  API 调用（只能 skip），这次是“混合校验闸”，该分流而不是一刀切——**同类问题不等于同一种修法**。
