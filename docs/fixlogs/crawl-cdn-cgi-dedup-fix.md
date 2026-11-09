# 爬虫驾驶舱：Cloudflare 噪音链接与去重口径修复复盘

> 作者：aceFelix
> 日期：2026-11-09
> 关联：[crawler-cockpit.md](../plans/crawler-cockpit.md)（P2 验收）

## 1. 问题现象

2026-11-09 用 `https://jsonplaceholder.typicode.com/` 做 Crawl 实测（`max_pages=5`、
`max_depth=2`、只抽 `title`），结果出现两处异常：

1. **去重数不一致**：驾驶舱顶部四格显示「去重 0」，下方 Crawl 统计行却是「去重 1」。
2. **无效页被当页面抓取**：URL 清单里出现
   `https://jsonplaceholder.typicode.com/cdn-cgi/l/email-protection`，耗时 2.1s、
   结果 HTTP 404，导致「失败 1」和一条红色失败行。

复现路径：对任意由 Cloudflare 托管、页面含邮箱保护脚本的站点执行 Crawl，
默认参数即可复现（首页渲染后会把 `mailto:` 替换成 `/cdn-cgi/l/email-protection#…`）。

## 2. 排查过程

- 先看 `Crawl` 结果：5 页里 4 成 1 败，失败页正好是 `/cdn-cgi/l/email-protection`，
  路径前缀 `/cdn-cgi/` 是 Cloudflare 的邮箱混淆/挑战专用前缀，不是站点页面。
  抓它既浪费一次请求，又会在驾驶舱里制造红色失败噪音。
- 再看去重：Crawl 的 BFS 在**入队/出队阶段**就去重（`visited` 集合），重复 URL
  不会产生第二条 `web_fetch`。而驾驶舱的 `summarize()` 只拿到 fetch 列表，
  按「同一 URL 出现两次」算 duplicates，于是永远显示 0；真正的去重命中数在
  `crawl_stats.dedup_hits` 里。
- 结论：两个问题都不是 Crawl 抓取逻辑错误，而是「链接过滤」与「展示口径」没对齐
  数据来源。

## 3. 根因分析

1. `extract_links()` 只过滤了 `#` / `javascript:` / `mailto:` / `tel:`，没有过滤
   Cloudflare 注入的 `/cdn-cgi/` 路径；该路径在 HTML 里是普通 `<a href>`，
   被当成正常站内链接入队。
2. `summarize()` 的 `duplicates = total - unique` 只适用于「重复抓取同一 URL」的
   场景；Crawl 的去重发生在抓取之前，两者口径不同。P2 把 `crawl_stats` 接进
   驾驶舱后，没有让四格汇总改用它。

## 4. 修复方案

- `agent/tools/web/crawl.py`：`extract_links()` 在规范化前检查绝对 URL 的 path，
  以 `/cdn-cgi/` 开头直接跳过；注释说明这是 Cloudflare 邮箱保护链接、抓了只会 404。
- `agent/ui/cockpit_crawl.py`：`summarize(fetches, stats=None)` 新增可选 `stats`；
  存在 `dedup_hits` 时以它为准，否则退回按 fetch 列表计算。`_draw_metrics()`
  传入 `state.stats`。
- 没有引入临时开关或 workaround；两处都是最终实现。

## 5. 验证结果

- 新增/更新用例：
  - `tests/tools/test_crawl.py::test_extract_links_skips_cloudflare_email_protection`
  - `tests/ui/test_cockpit_crawl.py::TestSummarize::test_dedup_prefers_crawl_stats`
- 定向测试 34 条通过；全量 `pytest tests -q` 通过（修复时 4789 passed，
  P3 完成后 4799 passed / 2 skipped）。
- 人工复测：用户按同一命令重跑后确认「OK，没毛病」——失败数 1 → 0，
  抓取 4 页，去重格显示 1，清单不再出现 `/cdn-cgi/...`。
- 未引入回归：单页 WebFetch / 无 `crawl_stats` 时仍按原逻辑计算去重。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/tools/web/crawl.py` | `extract_links()` 跳过 Cloudflare `/cdn-cgi/` 链接 |
| `agent/ui/cockpit_crawl.py` | `summarize()` 支持 `crawl_stats.dedup_hits`；四格去重改用它 |
| `tests/tools/test_crawl.py` | 新增 Cloudflare 链接过滤用例 |
| `tests/ui/test_cockpit_crawl.py` | 新增去重优先取 `crawl_stats` 用例 |
| `CHANGELOG.md` | P2 段增加“验收打磨”记录 |

## 7. 经验总结

- **展示口径要跟着数据生产者走**：同一指标有两个来源时，先确认“谁在什么阶段
  去重/过滤”，不要让 UI 自己再猜一遍。
- **第三方注入链接要按前缀拉黑**：Cloudflare 的 `/cdn-cgi/`、部分站点的
  `/wp-json/` 之类不是内容页；爬虫应在链接提取阶段过滤，而不是等 404 再标失败。
- 以后接新的抓取来源（P4 浏览器降级）时，同样先检查它注入的导航链接，
  避免把“浏览器内部路径”当成目标页面。
