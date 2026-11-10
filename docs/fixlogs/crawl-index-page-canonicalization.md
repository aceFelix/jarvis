# Crawl：目录默认页归一化（/index.html ≡ /）

> 作者：aceFelix
> 日期：2026-11-10
> 关联：[crawler-cockpit.md](../plans/crawler-cockpit.md)（§4.2 URL 规范化）

## 1. 问题现象

books.toscrape.com 实测（30 页）时，URL 清单同时出现：

- `https://books.toscrape.com/`（L0）
- `https://books.toscrape.com/index.html`（L1，同一页面）

后果：浪费 1 页抓取额度、拓扑多一个假节点、去重统计显示 0（实际 1 次）。

## 2. 根因

`normalize_url()` 只做 fragment/scheme/host/端口/尾斜杠/query 排序归一化，
没有处理"目录默认页"——`/index.html` 与 `/` 指向同一文档但被视为两个 URL。
BFS 的 `visited` 集合按归一化后的 URL 去重，因此挡不住。

## 3. 修复

`agent/tools/web/crawl.py`：

- 新增 `_INDEX_FILES = {"index.html", "index.htm", "default.html", "default.htm"}`；
- `normalize_url()` 在尾斜杠统一之前，把路径尾的默认页文件名剥掉
  （大小写不敏感）：`/a/index.html` → `/a`、`/index.html` → `/`；
- 带 query 的默认页（`/a/index.html?p=1` → `/a?p=1`）保留 query，不与裸目录归并
  （不同 query 可能是不同文档，保守处理）。

## 4. 验证

- `tests/tools/test_crawl.py::TestUrlHelpers::test_normalize_merges_directory_index_pages`
  覆盖：根/子目录归并、大小写、非默认页不受影响、query 保留；
- 定向 25 条通过；全量 `pytest tests -q` 通过；
- 行为变更面：抓取去重口径——同站索引页重复链接不再消耗页数额度。

## 5. 遗留

无。此项为 2026-11-09 起多轮登记的"URL canonicalization 边界"遗留项，至此关闭。
