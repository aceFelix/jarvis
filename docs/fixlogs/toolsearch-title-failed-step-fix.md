# 一次网页抓取暴露的三处体验问题修复复盘

> 模块：ToolSearch（工具发现）、会话标题生成、任务驾驶舱执行序列
> 发现时间：2026-11-09（用户实机走查：`jarvsi帮我抓取一下 https://ailhz.top/`，8 步 / 1 失败 / 34.9s）
> 修复时间：2026-11-09
> 作者：aceFelix

---

## 1. 问题现象

这次抓取任务**最终成功**：WebFetch 403 → curl 仍 403 → 识别出 Cloudflare 挑战页 →
切无头浏览器 `BrowserNavigate` → `BrowserGetText` 拿到 1394 字正文 → `BrowserClose`。
但从用户视角看有三处明显不顺手：

1. **ToolSearch 白跑一步，返回体失控**：查询「浏览器 打开网页 获取文本 browser」，
   返回的 6 个工具全是 `cli_anything__browser` 插件，内置 `BrowserNavigate` /
   `BrowserGetText` 没进结果；返回体 **56,423 字符**被宿主截断落盘，模型在思考里
   说「搜索返回太多东西……不确定它们的 schema」，随后直接猜调 `BrowserNavigate` 却成功。
2. **会话标题被链接污染**：`📝 会话标题已生成: jarvsi帮我抓取一下htt`——15 字截断
   正好切在 `https` 上。
3. **驾驶舱看不到失败**：8 步任务里唯一的失败是第 1 步 WebFetch，执行序列只显示
   最近 4 步，于是界面出现「失败 1」但列表全是 ✓ 的矛盾。

---

## 2. 排查过程

### 阶段 1：ToolSearch

先看 `ToolSearchTool.call()`：它只遍历 `registry.all_deferred()`（延迟池）。
而 `BrowserNavigate` 属于**已常驻的内置工具**（`deferred=False`），根本不在搜索池里——
但模型已经可以调用它（schema 由注册表提供），于是出现「搜不到、却能调」的错位。

再看打分 `_score()`：查询按空格切成整词，`打开网页` 不会拆成 `打开` / `网页`，
中文描述几乎命中不了；而 `cli_anything__browser` 名字里带 `browser` 又带 `__` 分段，
按现有权重反而排到前面，`max_results=6` 把真正的浏览器工具挤了出去。

最后看返回体：每个工具都拼完整 `json.dumps(schema)`，没有任何长度上限，
6 个 MCP 工具直接 5.6 万字符。

### 阶段 2：标题

`_generate_title_from_first_user()` 直接 `first_user_text[:15]`。用户首句
`jarvsi帮我抓取一下https://ailhz.top/...` 的前 15 字正是 `jarvsi帮我抓取一下htt`。

### 阶段 3：执行序列

`_draw_steps()` 用 `list(state.step_history)[-4:]`，`step_history` 是 `deque(maxlen=4)`
——失败步一旦滑出窗口就再也画不出来，而顶部的失败计数来自 `state.failed`，两者口径不一致。

---

## 3. 根因分析

| 问题 | 根因 |
|---|---|
| ToolSearch 搜不到内置工具 | 搜索池只有 deferred 池；已常驻工具被排除 |
| 中文查询不命中 | 关键词按空格整词切分，缺少中文分词/别名映射 |
| 返回 56KB | schema 与返回体都没有长度上限 |
| 标题切在链接上 | 取标题前没有清洗 URL/代码/路径 |
| 失败步消失 | 展示窗口只看最近 4 条，失败步没有置顶策略 |

---

## 4. 修复方案

### 4.1 ToolSearch（`agent/tools/tool_search.py`）

- 搜索池 = `all_deferred()` + `all_core()`（排除 ToolSearch 自身）；
- 已常驻工具在结果里标注「状态: 已可用（常驻工具，直接调用）」，且不写入
  `discovered_tools`（它们本来就不需要“发现”）；
- 新增 `_expand_keywords()`：中文长词拆二元组（`打开网页` → `打开`/`开网`/`网页`），
  并通过 `_ALIASES` 补英文片段（浏览器→browser/navigate/page 等）；
- 新增长度上限：单条 schema 1200 字符、整次返回 6000 字符，超长时明确告知
  「另有 N 个结果未展开，可缩小关键词」。

### 4.2 会话标题（`agent/session_manager.py`）

- 新增 `_clean_title_source()`：剥代码块、行内代码、`http(s)/ftp` URL、Windows 绝对路径，
  再压缩空白；
- `_generate_title_from_first_user()` 先清洗再截 15 字；若清洗后为空（用户只发了链接），
  回退原文，避免空标题。

### 4.3 执行序列（`agent/ui/cockpit.py` + `cockpit_window.py`）

- 新增纯函数 `step_rows(history, limit=4)`：最近 4 步里已有失败就原样返回；否则把
  窗口外最近一次失败补到第一行，并压缩一条最近步，总行数保持 4（窗口高度固定）；
- `_draw_steps()` 改用 `step_rows()`。

---

## 5. 验证结果

新增/更新用例：

- `tests/test_core_tools.py`：
  - 中文长词命中内置浏览器工具；
  - 常驻工具标注「已可用」且不进入 `discovered_tools`；
  - 超大 schema 返回被截断且总长 ≤ 7000；
  - `_expand_keywords` 拆词与别名；
- `tests/test_session_manager.py`：标题剥离 URL、只发链接时回退原文；
- `tests/ui/test_cockpit.py::TestStepRows`：无失败取最近 4、失败在窗口外时置顶、
  窗口内失败不重复；
- `tests/capabilities/test_capability_consumers.py`：适配新的返回头文案。

结果：全量 `python -m pytest tests -q` → **4745 passed, 2 skipped**。

人工走查（建议复测）：再说一次「帮我抓取 https://ailhz.top/」，观察
ToolSearch 一次就能命中 `BrowserGetText`、返回体明显变短；`/sessions` 标题不再
出现 `htt` 残片；若中途有失败，执行序列第一行能看到 ✗。

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/tools/tool_search.py` | 搜索池含常驻工具、中文拆词/别名、schema 与返回体限长 |
| `agent/session_manager.py` | 新增 `_clean_title_source`，标题先清洗再截断 |
| `agent/ui/cockpit.py` | 新增 `step_rows`（失败步置顶） |
| `agent/ui/cockpit_window.py` | 执行序列改用 `step_rows` |
| `tests/test_core_tools.py` / `tests/test_session_manager.py` / `tests/ui/test_cockpit.py` / `tests/capabilities/test_capability_consumers.py` | 用例与断言同步 |
| `README.md` / `README.en.md` / `docs/plans/terminal-cockpit.md` / `CHANGELOG.md` | 行为说明同步 |

---

## 7. 经验总结

- **“已可用”和“搜得到”是两件事**：只搜延迟池会让模型对已常驻工具失去 schema，
  进而反复搜索或盲猜。搜索应覆盖全部工具，并明确标注是否需要“发现”。
- **中文检索不能只按空格切词**：至少要做二元组 + 领域别名，否则中文描述形同虚设。
- **工具返回值必须有预算**：56KB 的 schema 拼装对模型是负资产（被宿主截断后只剩噪音）。
- **统计口径与展示口径必须一致**：失败数来自状态机，列表却只取最近 4 条，就必然出现
  「有失败但看不到失败」。展示层要有“关键条目置顶”的兜底策略。
