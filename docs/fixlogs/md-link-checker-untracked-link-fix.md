# 文档链接指向被 .gitignore 排除的本地文件 —— 死链门禁第二类漏报的修复复盘

> 版本：1.0 | 模块：`scripts/check_md_links.py` + `docs/guide/development.md` | 类型：门禁能力缺口 + 文档链接错误
> 发现方式：PR #1 的 GitHub Actions（跨平台修复推送后仍剩 1 条失败）
> 前置复盘：[md-link-checker-linux-ci-fix.md](md-link-checker-linux-ci-fix.md)（第一篇解决的是"误报"，本篇是"漏报"）
> 作者：aceFelix

## 一、问题现象

第一篇复盘的跨平台修复（提交 `0fe99c7`）推送后，PR #1 的四个 Python 矩阵**仍全部失败**，
但失败面已从"一长串误报"收敛为**唯一 1 条**：

```
collected 2557 items
FAILED tests/test_md_link_checker.py::test_repo_docs_have_zero_dead_links
  AssertionError: 存在失效链接：[('docs/guide/development.md', '../publish/PUBLISH.md')]
================= 1 failed, 2546 passed, 10 skipped in 31.10s ==================
```

同一条命令在开发机（Windows）上是 **0 死链**。也就是说：文件在本地真实存在，
CI 检出后不存在——这不是误报，是一条**真死链**，而工具在本地根本抓不到它。

- 影响面：只影响门禁判定与一篇文档的链接，不影响 jarvis 运行时行为；
- 复现条件：链接目标位于 `.gitignore` 排除的目录，且只在全新检出的环境（CI）里暴露。

## 二、排查过程

1. **先确认轮次**：查 `commits/0fe99c7/check-runs`，`head_sha` 就是本次修复的提交，
   四个矩阵约 45s 后 exit 1 → 排除"看到旧 run"的错觉，确认修复有效但问题未清空。
2. **比对两轮失败面**：第一篇是"全仓相对链接被误报"（几十条），本轮只剩 1 条真目标，
   说明平台假设已修完，剩下的是**另一类问题**，不能沿用上一篇的结论收尾。
3. **按失败目标查它的入库状态**：
   - `Test-Path docs\publish\PUBLISH.md` → `True`（本机存在，所以本地扫不出问题）；
   - `git ls-files docs/publish` → 空（仓库里没有）；
   - `git check-ignore -v` → 命中 `.gitignore:50:docs/publish`。
   三步即锁定根因：`docs/publish/` 是被**有意**排除的本地目录（发布手册含账号/令牌类信息，
   不该入库），而 `development.md` 用相对链接指向了它。
4. **回头看自己的勘察为什么漏掉它**：第一篇复盘的排查过程里，确实逐个比对过
   `git ls-files`，但**只比对了 412 条绝对链接**——当时判断"绝对链接重定位风险最大"，
   就把勘察范围缩小到了它，相对链接没做入库校验。风险判断对了，范围收敛错了。
5. **把能力做进工具而不是只改这一处**：只修 `development.md` 的话，同类链接下次照样漏。
   于是给工具加"未入库链接"这一维判定（`tracked_paths()` / `find_untracked_links()`），
   再用它一次性列全两仓的同类实例，结果各命中 1 条：
   - `jarvis/docs/guide/development.md` → `../publish/PUBLISH.md`（即 CI 报的那条）；
   - `jarvis-desktop/docs/guide/installation.md` → `../..`。
6. **desktop 那条经核对是"语义写错"而非"未入库"**：`../..` 从 `docs/guide/` 出发落在
   jarvis-desktop **自己的仓根**，而它想指的是兄弟仓 `jarvis`。该仓其他文档引用 jarvis
   一律用 GitHub 地址，故按既有写法统一改为 `https://github.com/aceFelix/jarvis`。
   同时把仓根自身（`.`）计入"已跟踪目录集"，避免真正合法的"链回仓根"写法被误报。

## 三、根因分析

**直接根因**：`docs/guide/development.md` 的两处链接指向 `.gitignore` 排除的本地目录
`docs/publish/`。这个目录只存在于维护者本机，任何全新检出（CI、其他贡献者）里都没有。

**为什么工具永远抓不到**：门禁的判定基准是**开发机的文件系统存在性**（`_exists()`），
而 CI 的判定基准是 **git 索引**。两个集合并不相等，其差集正是"本机有、仓库无"的文件——
写 `.gitignore` 的那一刻就制造了差集，而工具从未看过索引这一侧。

**为什么之前的实现会出错**：

- 第一篇复盘的教训清单里，「文件是否存在于 git 索引（CI 只检出被跟踪的文件）」
  这一条**已经写进了文档**，但只作为"人工排查时该想到的清单项"存在，没有变成可执行的判定；
  结果同一个坑仍然掉了第二次——**写在文档里的清单不等于被防护的清单**。
- 勘察阶段用"风险大小"来裁剪范围，而门禁类问题该用"覆盖完整性"来裁剪范围。

## 四、修复方案

分三层，缺一层就只是把 CI 哄绿：

| 层 | 改动 | 为什么必须 |
|---|---|---|
| 文档 | `development.md` 顶部导航 `[发布流程](../publish/PUBLISH.md)` → 站内锚点 `#自动发布流程`；正文改为纯文本提示「见本地 `docs/publish/PUBLISH.md`（该目录被 `.gitignore` 排除，不随仓库分发，故此处不给链接）」 | 被忽略的文件**不能**作为链接目标，只能作为文字说明；锚点指回上文同一节，导航不断 |
| 工具 | 新增 `tracked_paths()`（`git ls-files -z`，无 git 环境返回 `None`）、`_repo_relative()`、`find_untracked_links()`；`main()` 把未入库链接与死链同样置退出码 1 并逐条打印 | 把"入库状态"变成门禁的第二维，本地就能预演 CI 的判定 |
| 测试 | 新增 `test_untracked_link_detected`（monkeypatch 伪造索引）、`test_untracked_check_skipped_without_git`、`test_repo_docs_links_are_all_git_tracked`（全仓门禁） | CI 只跑 `pytest tests/`，所以防护必须挂成测试，光有 CLI 脚本等于没接上门禁 |

关键细节：`find_untracked_links()` 只报"存在但不在索引里"的目标，真不存在的仍由
`find_dead_links()` 负责，两者分工不重叠；目录链接看其下是否有已跟踪文件，仓根 `.` 恒视为存在。

## 五、验证结果

CI 侧本轮只改动 jarvis 仓（门禁工具与文档），jarvis-desktop 侧为纯文档修正且不跑链接门禁。

| 验证项 | 命令 | 结果 |
|---|---|---|
| 工具用例（14 → 17 例） | `uv run pytest tests/test_md_link_checker.py -q` | 17 passed |
| 全量回归 | `uv run pytest -q --tb=line` | 2560 passed, 3 warnings in 67.47s |
| 全仓扫描（jarvis） | `uv run python scripts/check_md_links.py` | 124 md / 判定 516 / 跳过 30 / **死链 0 / 未入库 0** |
| 全仓扫描（jarvis-desktop） | `uv run python scripts/check_md_links.py ..\jarvis-desktop` | 14 md（含 desktop 侧本篇同类复盘）/ 判定 66 / 跳过 28 / **死链 0 / 未入库 0** |
| CI 复验（实测） | PR #1 五个 check（`head_sha` = `8e4e01e`） | **全部 success**：Python 3.11 / 3.12 / 3.13 / 3.14 四个矩阵跑完 `pytest tests/`（约 44–51s）+ Scan for secrets，失败面由 `1 failed` 归零 |

链接统计相比第一篇（123 md / 判定 513 / 跳过 29 → 124 md / 判定 516 / 跳过 30）变化的原因
是真实的：`../publish/PUBLISH.md` 改成了纯锚点，从"需判定"移入"跳过"（−1）；本篇及另两文档
新增的 4 条指向本篇的链接参与判定（+4）；md 数 +1 即本篇自身。desktop 侧 `../..` 改成
 GitHub 外链后同理（14 md / 判定 66 / 跳过 28；md 数 +1 是 desktop 侧新增的同类复盘）。

> 附带验证：写完本篇后先跑了一次门禁，它当场报出 3 条"未入库链接"——全部指向本篇自己，
> 因为当时它还没 `git add`。这正是该类检查应有的行为（链接目标必须真的在索引里），
> 暂存后重跑即归 0。

> 关于 `test_repo_docs_links_are_all_git_tracked` 的 `pytest.skip`：仅当运行环境不在 git
> 仓库内（例如从 sdist 分发包里跑测试）才跳过，CI 与开发机都会实际执行，属环境守卫而非"绕过测试"。

## 六、涉及文件

| 文件 | 改动说明 |
|---|---|
| `scripts/check_md_links.py` | 新增 `tracked_paths()` / `_repo_relative()` / `find_untracked_links()`；`main()` 增加未入库链接报告与退出码；已跟踪目录集含仓根 `.` |
| `tests/test_md_link_checker.py` | 14 → 17 例：未入库链接检出、无 git 环境跳过、全仓链接入库门禁 |
| `docs/guide/development.md` | 两处指向 `docs/publish/PUBLISH.md` 的链接改为站内锚点与纯文本说明 |
| `docs/fixlogs/md-link-checker-linux-ci-fix.md` | 第五节「CI 复验」由预期改为实测结论，并指向本篇 |
| `docs/fixlogs/workbench-gui-retired.md` | 第八节核查工具口径同步（新增维度、用例数、实测统计） |
| `docs/test/TEST_CHECKLIST.md` | 尾部工具用例数 14 → 17，补指本篇复盘 |
| jarvis-desktop `docs/guide/installation.md` | `../..` 修正为 `https://github.com/aceFelix/jarvis` |
| jarvis-desktop `docs/fixlogs/cross-repo-link-installation-fix.md` | 该仓同类问题的随次复盘（跨仓链接层数算错） |

## 七、经验总结

**可复用的判定规则**：

1. **"本地全绿 + CI 全红"有两类成因**，必须分开排查：
   - 平台假设（分隔符、大小写、编码、换行、时区）→ 见第一篇；
   - **判定基准集合不同**：本地看文件系统，CI 看 git 索引。凡是"本地存在但没 `git add`"
     的东西（`.gitignore` 里的目录、忘加的产物、本机专属配置），只会在这第二类里出现。
2. **清单不落地成门禁，就等于没写**。第一篇已经把"是否在 git 索引里"列进排查清单，
   却仍然漏掉了本条——因为那只是给人看的提醒。防护手段的强度排序是：
   CI 里的测试 > 可执行的 CLI 门禁 > 文档中的提醒。
3. **修一类问题时，先把这一类的全部实例列出来**。做法是给工具加检测能力，然后拿它
   扫两个仓；只修 CI 报的那一条，等价于把一个已知缺陷留在原地等它复发。
4. **`.gitignore` 排除的目录可以放文档，但文档不能链接过去**；要引用就写成文字路径并说明
   原因，否则每个新检出上都是一个死链。工具现已把这条规则变成硬门禁。
5. 门禁工具的行为变更要同步到"接入点"：CI 只跑 `pytest tests/`，所以新判定必须挂成
   测试用例，而不是只加一条 CLI 输出。

**遗留待办（不在本次范围）**：第一篇记录的 412 条 `file:///e:/...` 绝对链接改写为相对链接，
仍应作为独立的一次机械提交处理。
