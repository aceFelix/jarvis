# 爬虫驾驶舱拆分：纯逻辑层独立成包（900 行门禁）

> 作者：aceFelix
> 日期：2026-11-10
> 关联：[crawl-topology-detach.md](../plans/crawl-topology-detach.md)、[crawler-cockpit.md](../plans/crawler-cockpit.md)

## 1. 问题

CI 失败：

```
FAILED tests/test_engine_split_guards.py::TestAgentFileLineLimit::test_no_agent_file_exceeds_limit
AssertionError: agent/ 下超过 900 行的文件: ['agent/ui/cockpit_crawl.py=1070']
```

P3.5（滚动/缩放）与 P3.6（撕出独立窗）两轮改动把 `cockpit_crawl.py` 从
约 700 行推到 **1070 行**，触发 `agent/` 单文件 900 行门禁。

## 2. 根因与修复

拆分层而非压缩代码：`cockpit_crawl.py` 混了**纯逻辑**（URL 截断、滚动窗口、
汇总统计）与 **Tk 视图**（绘制、事件绑定、窗口生命周期），前者天然可单测。

- 新增 `agent/ui/cockpit_crawl_data.py`：迁出 `domain_of`、`clip_url_tail`、
  `visible_window`、`fetch_window`、`scrollbar_geometry`、`unique_fetches`、
  `summarize`、`stats_parts` 及常量 `URL_ROWS` / `EXTRACT_ROWS`，
  依赖仅 `agent.ui.cockpit`（`FETCH_MAX`、`_fmt_bytes`）与 `hud_theme.display_width`；
- `cockpit_crawl.py` 改为从新模块导入并**再导出**（`unique_fetches` 等仍可从原路径
  导入，既有调用与测试零改动）；去掉迁出后不再使用的 `Counter`/`Iterable`/
  `FETCH_MAX`/`display_width` 导入；
- 结果：`cockpit_crawl.py` **1070 → 856 行**，`cockpit_crawl_data.py` 约 240 行。

## 3. 验证

- `tests/test_engine_split_guards.py` 通过（全部 `agent/*.py` ≤ 900 行）；
- 定向：`test_cockpit_crawl.py` + `test_cockpit_topo_view.py` +
  `test_cockpit_crawl_topo.py` + 守卫共 79 条通过；
- 全量 `pytest tests` 以退出码判定通过。

## 4. 过程教训（记录在案）

本轮早前用 `pytest ... | tail -4` 看结果，管道退出码取的是 `tail` 的 0，
**把 pytest 的失败码吞掉了**，导致误报"全量通过"，直到 CI 才暴露。
今后全量回归必须重定向到文件后单独判 `$?`，不得经管道取尾行。
