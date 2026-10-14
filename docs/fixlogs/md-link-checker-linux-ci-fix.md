# 死链扫描工具在 Linux CI 上误报全仓链接 —— 修复复盘

> 版本：1.0 | 模块：`scripts/check_md_links.py` | 类型：工具跨平台缺陷
> 发现方式：PR #1 的 GitHub Actions（Python 3.11/3.12/3.13/3.14 四个矩阵全红）
> 作者：aceFelix

## 一、问题现象

三栏 GUI 窗口壳退役后新增的文档死链门禁工具，在开发机（Windows）上跑全量
`pytest` 是 **2550 passed**，推到 GitHub 开 PR 后 CI 四个 Python 版本**全部失败**：

```
================= 2 failed, 2538 passed, 10 skipped in 28.93s ==================
```

失败输出是一长串"死链"元组，而列出的链接目标（`../plans/workbench-gui.md`、
`../../README.md`、`../architecture/07-UI层.md` …）**肉眼可见全部真实存在**。

- 影响面：只影响门禁工具自身的判定结果，不影响 jarvis 运行时行为；
- 复现条件：非 Windows 环境（Linux CI）跑 `pytest tests/`；
- 失败的两条用例中可确认的一条是 `tests/test_md_link_checker.py::test_repo_docs_have_zero_dead_links`
  （那一长串元组就是它的断言 diff）；另一条按同因定位为依赖相对链接判定的
  `test_relative_dead_link_detected`（CI 日志节选未显示用例名）。

## 二、排查过程

1. **从报错形态找规律**：CI 列出的"死链"在本地逐个可见地存在，且被误报的**全部**是带 `/`
   的相对链接；而 `[自](ok.md)` 这种不含分隔符的链接没被报出来 → 问题在"分隔符处理"这一层，
   与文件是否真的存在无关。
2. **读代码定位到语句**：`resolve_target()` 里有一行
   `raw = target.removeprefix("file:///").replace("/", "\\")`。
   Windows 上 `Path` 同时认 `/` 和 `\`，所以这一步"归一化"在开发机上是**无害的隐式转换**；
   Linux 上 `\` 不是路径分隔符，`docs/target.md` 变成 `docs\target.md` 这个**单个文件名**，
   于是每一条带层级的相对链接都必然不存在 → 全仓误报。
3. **解释"本地为什么全绿"**：所有断言都在 Windows 上跑，而 Windows 对两种分隔符都宽容，
   错误语句在开发机上"看起来是对的"。这是典型的**单向兼容掩盖平台假设**。
4. **勘察修复范围的第二层**：只删 `replace` 还不够——文档里有 **412 条** `file:///e:/...`
   绝对链接，CI 检出路径是 `/home/runner/work/jarvis/jarvis`，这些链接在 Linux 上永远不可能
   命中。用一次性脚本实测：按「仓根目录名」重定位后 **0 条缺失**；另有 **1 条**指向兄弟仓库
   `jarvis-desktop/`，本仓 CI 检不出对方文件，只能跳过。因 CI 只检出被 git 跟踪的文件，
   重定位后的目标逐个与 `git ls-files` 比对，确认没有"本机有、仓库里没有"的情况
   （仅 3 条是目录写法，`git ls-files` 不列目录名，其下文件均已跟踪）。
5. **勘察残留风险**：全仓相对链接中 **0 条**含反斜杠、**0 条**越出仓根，确认去掉归一化后
   不会再有别的坑；700 条链接目标里 **0 条**含 `%` 转义或 `<...>` 写法，正则切分不会截错。

## 三、根因分析

**直接根因**：把 Windows 的路径分隔符兼容当成了通用规则。
`replace("/", "\\")` 的动机是"让 Windows 下 `Path` 解析更稳"，但它把平台私有的分隔符
语义写进了本应平台无关的判定逻辑。

**为什么之前的实现会出错**：

- 工具从"一次性排查脚本"升级为"CI 门禁"时，只补了功能用例，没有补**环境视角**的用例
  （不同检出前缀、POSIX 路径写法）；
- 本地跑通即交付，等于用单一平台给跨平台代码背书。

**次要成因（同一处代码的第二个缺陷）**：文档中的绝对路径链接写死了开发机的
`file:///e:/...`，这类链接在 GitHub 页面上点击即失效，在别的机器上也无法判定 ——
属于既有技术债，本次让工具容忍它，但债还在（见第七节）。

## 四、修复方案

不再"归一化"分隔符，改为**用与检出路径无关的口径**判定：

| 链接写法 | 判定方式 |
|---|---|
| 相对链接 | 按 md 所在目录拼接；先用 `os.path.normpath` 判断是否越出仓根，越出则**不判定**（兄弟仓等） |
| `file:///` 或盘符绝对路径 | 按**仓根目录名**切出仓内相对路径，再拼上当次检出根；同名层级产生多个候选时「任一存在即有效」 |
| http/https/mailto/纯锚点/站点绝对路径/`data:` | 一律跳过（原有口径不变） |

关键函数 `repo_relative_candidates()` 是纯字符串处理，输出正斜杠形式（与 git 路径口径一致），
不含任何平台分隔符，因此在 Windows 与 Linux 上结果必然相同。

顺手修掉同一函数里的另一类误报：行内代码与围栏代码块里的 `[x](y)` 只是**写法示例**，
GitHub 不会把它渲染成链接，提取前先行剔除（`link_targets()`）。这一条不是预防性设计，
而是本次写的这篇复盘自己踩到的：文中举例写了行内的 `[自](ok.md)`，当场被门禁拦下
（dead links: 1），证明“拿不准时相信工具报的第一条”。

配套把"跳过"显式化：CLI 输出新增 `local links judged: N | skipped (...): M`，
让门禁的覆盖面可交代，避免"0 死链"被误读成"0 链接"。

## 五、验证结果

| 验证项 | 命令 | 结果 |
|---|---|---|
| 工具用例（7 → 14 例） | `uv run pytest tests/test_md_link_checker.py -q` | 14 passed |
| 全量回归 | `uv run pytest -q` | 2557 passed, 3 warnings |
| 全仓扫描（jarvis） | `uv run python scripts/check_md_links.py` | 123 md / 判定 513 / 跳过 29 / **死链 0** |
| 全仓扫描（jarvis-desktop） | `uv run python scripts/check_md_links.py ..\jarvis-desktop` | 13 md / 判定 67 / 跳过 27 / **死链 0** |
| CI 复验 | PR #1 checks | 推送本修复后由四个 Python 矩阵复验 |

新增用例中直接针对本 bug 的四条：

- `test_repo_relative_candidates_posix_checkout`：以 `/home/runner/work/jarvis/jarvis/...`
  这种 CI 检出路径为输入，断言切出的仓内相对路径不含任何分隔符残留；
- `test_absolute_file_url_relocated_by_repo_root`：开发机绝对路径在假定的"另一台机器"根目录下
  仍能正确判定存在/不存在；
- `test_foreign_absolute_link_not_judged` / `test_relative_link_escaping_repo_not_judged`：
  仓外路径不误报；
- `test_links_in_code_samples_not_judged`：代码块/行内代码里的示例链接不参与判定。

> 说明：本 bug 无法在 Windows 上用例复现（Windows 对两种分隔符都宽容），
> 因此防护手段是"把 CI 视角的路径形态直接写进断言输入"，而不是依赖开发机复现。

## 六、涉及文件

| 文件 | 改动说明 |
|---|---|
| `scripts/check_md_links.py` | 删除 `replace("/", "\\")`；新增 `repo_relative_candidates()` / `link_candidates()` / `link_targets()` / `_exists()` / `count_links()`；绝对链接按仓根重定位、越仓链接不判定、代码示例不提取；输出补判定/跳过计数 |
| `tests/test_md_link_checker.py` | 7 → 14 例；`local_link_targets()` 改签名带 `root`；原 `test_file_url_treated_as_absolute` 由"按绝对路径解析"改写为"按仓根重定位 + 仓外不判定"两条 |
| `docs/fixlogs/workbench-gui-retired.md` | 第八节核查工具口径同步（用例数、跨平台判定、CI 复验指引） |
| `docs/test/TEST_CHECKLIST.md` | 尾部工具用例数 7 → 14，并指向本篇复盘 |

## 七、经验总结

**可复用的判定规则**：

1. 本地全绿 + CI 全红时，优先怀疑**环境假设**而不是代码逻辑，按固定清单过一遍：
   路径分隔符、文件名大小写、文本编码（GBK/UTF-8）、换行符、时区、
   文件是否存在于 git 索引（CI 只检出被跟踪的文件）。
2. **单向兼容的归一化是陷阱**。`replace("/", "\\")` 在 Windows 上永远不报错，
   因此永远不会被本地测试抓到。凡是"把 A 平台的写法转成 B 平台的写法"的语句，
   要么双向都成立，要么就不该存在。
3. **门禁工具必须带"CI 视角"的用例**。它自己的平台假设会被固化成对所有提交的阻塞。
   做法：把目标环境的真实路径形态（这里是 `/home/runner/work/<repo>/<repo>`）
   直接写成断言输入，不依赖开发机能否复现。
4. 绝对路径链接按「仓根目录名」重定位，是"文档里已经写死绝对路径"这类历史债的
   低成本兜底；但**根治办法仍是把文档链接写成相对路径**——`file:///e:/...` 在
   GitHub 页面上点击即失效，对其他贡献者等于没有链接。

**遗留待办（不在本次范围）**：

- 仓内 412 条 `file:///e:/...` 绝对链接宜改写为相对链接（涉及约 20 篇文档，
  属独立机械改动，应单独一次提交，避免与功能改动混提）。
