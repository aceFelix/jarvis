# 爬虫驾驶舱 P3.5：列表滚动与拓扑缩放/平移/聚合展开落地复盘

> 作者：aceFelix
> 日期：2026-11-10
> 关联：[crawler-cockpit.md](../plans/crawler-cockpit.md)（P3.5）

## 1. 问题现象

2026-11-10 用 `https://books.toscrape.com/` 做 Crawl 实测（`max_pages=12`、
`max_depth=2`、抽 `h1`），P3 功能全部正常，但暴露三类体验问题：

1. **清单不可回看**：URL 清单只显示最近 7 条、抽取表只显示最近 4 条，
   12 页数据里根页与早期记录被挤出视野，且窗口不支持任何滚动。
2. **拓扑标签重叠**：11 个 L1 节点单层折叠为 8 可见 + `+3`，可见节点标签
   连续挤在一起影响阅读（P3.5 预留的“实测重叠再排期”条件触发）。
3. **URL 辨识度不足**：长 URL 截断为 `books.toscrape.com/catalogue/category…`，
   多行同前缀，看不出具体分类；且 `队列 120` 文案易被误解为“又排了 120 页”
   （实为达到 max_pages 后 frontier 剩余数）。

## 2. 方案（与用户确认范围）

- **P0 可读性**：URL 保留尾部智能截断（`域名/…/尾段`）；根页固定在清单第一行；
  「队列」改「剩余」、「深度」改「深度上限」。
- **P1 列表滚动**：URL 清单与抽取表支持鼠标滚轮；`n/总数` 提示 + 迷你滚动条；
  窗口尺寸 560×640 不变。
- **P2 拓扑交互**：Ctrl+滚轮缩放（0.55–3.0）、右键/中键拖动平移、双击右键复位、
  `+N` 聚合点击按层展开；左键仍为节点↔抽取行高亮；不做节点拖拽。
- 本轮**不改** Crawl 的 URL 规范化（`/` 与 `/index.html` 未合并去重的问题
  留待下一轮单独处理，避免抓取行为与驾驶舱交互混在同一变更）。

## 3. 实现要点

- `agent/ui/cockpit_crawl_topo.py`
  - `short_label()` 改为**取最后语义段**（如 `fiction_10`），同类页面标签不再同前缀；
  - `build_topology()` 新增 `layer_limits` 参数：用户展开某层时按层提高可见上限，
    布局仍为纯函数，可单测。
- `agent/ui/cockpit_crawl.py`
  - 新增 `clip_url_tail()`：保域名 + 尾段，中间 `…`；
  - URL 清单/抽取表滚动状态（`_url_scroll` / `_extract_scroll`）+ 滚轮绑定
    + 钳制 + `n/总数` + 迷你滚动条；根页（depth==0）固定第一行不参与滚动；
  - 拓扑视图状态（`_topo_scale/_topo_pan_x/_topo_pan_y/_aggregate_depths/
    _expanded_layers`）：Ctrl+滚轮缩放、右键/中键拖动平移、双击右键复位；
    实现采用**布局纯函数重算 + 重绘**而非 `canvas.scale()`（后者不缩放字体）；
  - 点击命中优先级：节点/聚合/抽取行 > 空白整窗拖动，避免交互冲突；
  - `+N` 聚合节点带 tag 可点击，展开该层下一批（再点收起）；
  - 统计文案：`队列`→`剩余`、`深度`→`深度上限`。
- `agent/ui/cockpit.py`：`EXTRACT_MAX` 10→200（与 `FETCH_MAX` 对齐，滚动由视图层控制）。

## 4. 验证结果

- 新增/更新用例：
  - `tests/ui/test_cockpit_crawl_topo.py`：尾部语义段标签、`layer_limits` 展开、
    展开后聚合消失/收起恢复；
  - `tests/ui/test_cockpit_crawl.py`：`clip_url_tail`、根页固定滚动窗口钳制、
    剩余/深度上限文案、缩放钳制（0.55–3.0）、平移复位、聚合点击展开/收起。
- 定向测试（cockpit 三文件）全绿；全量 `pytest tests -q` 通过。
- 人工复测：待用户按原场景重跑确认（重点：滚轮滚动、Ctrl+缩放、右键平移、
  +N 展开、点击联动未回归）。

## 5. 遗留

- `/` 与 `/index.html` canonicalization 合并（Crawl 工具层，下一轮）；
- 拓扑节点拖拽不做（与整窗拖动/高亮冲突，收益低）。
