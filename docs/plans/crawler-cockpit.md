# 爬虫驾驶舱与真爬虫工具（C2 完整版）工程方案

> 状态：**已完成**（2026-11-09 评审通过；P1 抽取、P2 爬虫、P3 拓扑、P4 浏览器降级均已完成并通过全量测试。已确认：选择器 beautifulsoup4、拓扑只做高亮+聚合（P3.5 可选缩放）、默认抓取参数保持、窗口 560×640、阶段顺序 P1→P2→P3→P4）
> 作者：aceFelix
> 日期：2026-11-09
> 关联：[terminal-cockpit.md](terminal-cockpit.md)（C1b HUD）、[jarvis-cinematic-visualization.md](jarvis-cinematic-visualization.md)（C2/C4 总规划）、[cockpit-multicards.md](cockpit-multicards.md)（可视化配置）

---

## 1. 背景与现状

2026-11-09 已落地的 C2-lite：屏幕最左侧独立窗口，标题 **「CRAWL // 爬虫驾驶舱」**（原“抓取网络”），
显示 URL 去重清单、成功/失败/去重/字节/域名汇总，以及两个**预留区**：

| 区块 | 设计用途 | 当前数据源 | 现状 |
|---|---|---|---|
| 抽取 // EXTRACT | 每页抓完后把结构化字段逐行填表 | trace `web_extract`（`url` + `fields`） | **无生产者**，恒空 |
| 拓扑 // TOPOLOGY | URL 图（节点=页面，边=父→子）+ 队列统计 | `crawl_stats` + `web_fetch.parent_url/depth` | **无生产者**，恒空 |
| URL 清单 + 汇总 | 已抓页面、状态、耗时、字节、域名分布 | `web_fetch`（由 WebFetch / BrowserNavigate 派生） | 已可用 |

根因：jarvis 目前**没有爬虫本体**（无 frontier 队列、深度控制、去重库），
`TraceRecorder` 派生 `web_fetch` 时刻意省略 `parent_url` / `depth`（没有数据源就不写假值）。

本方案要补齐三件事：**抽取能力** → **Crawl 工具** → **拓扑与统计可视化**，并保持
“展示层只读事件、不反向影响抓取策略”的既有原则。

---

## 2. 目标与边界

### 2.1 目标

1. 用户说「帮我爬一下某站的前 10 页，抓标题/作者/摘要」时，JARVIS 能真的爬、真的抽、并且
   在左侧「爬虫驾驶舱」里看到：页面清单、深度、抽取表、URL 拓扑、队列/去重统计。
2. 单页场景也能用：只做抽取（不爬），抽取表立刻有数据。
3. 全部沿用现有可视化窗口与显示策略（有抓取才弹、结束保留、终端优先让位、`crawl_window` 可关）。

### 2.2 非目标（本轮明确不做）

- ❌ 分布式爬虫、断点续爬、持久化任务队列；
- ❌ 绕过 Cloudflare 等反爬挑战（遇到就如实标记失败并停止该分支）；
- ❌ 登录态 / 需要交互的页面（后续可复用 Browser 登录态，另立方案）；
- ❌ 力导向/3D 拓扑动画（用稳定分层布局，信息优先）；
- ❌ 让界面参与抓取决策（展示层只消费 trace 事件）。

---

## 3. 数据契约（trace 事件）

所有事件沿用现有 trace JSONL 结构与 `agent_id` 过滤规则（主 HUD/爬虫舱只看主任务）。

### 3.1 `web_fetch`（扩展现有事件，向后兼容）

```json
{
  "type": "web_fetch",
  "url": "https://site/a",
  "final_url": "https://site/a/",
  "status": "ok | error",
  "duration_ms": 320,
  "bytes": 12288,
  "parent_url": "https://site/",   // 新增：根页为 null/省略
  "depth": 1,                       // 新增：根页为 0
  "kind": "http | browser"          // 新增：抓取后端
}
```

- 字段缺失即省略（不写假值）；现有 WebFetch / BrowserNavigate 派生逻辑保持不变（无 parent/depth）。

### 3.2 `web_extract`（新）

```json
{
  "type": "web_extract",
  "url": "https://site/a",
  "fields": {"title": "React 19 新特性", "author": "张三", "summary": "…"},
  "missing": ["summary"],          // 声明了但没抽到的字段
  "selector": "article"             // 可选：作用范围
}
```

### 3.3 `crawl_stats`（新）

```json
{
  "type": "crawl_stats",
  "queued": 12, "done": 8, "failed": 1, "dedup_hits": 3,
  "bytes": 94208, "elapsed_ms": 8200, "max_pages": 10, "max_depth": 2
}
```

- P2 实现为**结束时随 `trace_meta` 一次性上报**（每页 `web_fetch` 逐条派生），
  驾驶舱在工具结束后刷新；逐页实时上报需要额外的进程内通道，暂不引入。

### 3.4 生产者方案（已落地：trace 派生 + `trace_meta` 旁路）

- `ToolResult` 新增 `trace_meta: dict`：工具把要上驾驶舱的轻量结构化数据放这里，
  **不进入 tool_result 文本、不参与结果截断**；orchestrator 在 `tool_after` 钩子里原样透传；
- `TraceRecorder.on_tool_after` 读取 `payload["trace_meta"]`，逐条派生 `web_fetch` /
  `web_extract` / `crawl_stats`，事件格式单点维护，工具本身不依赖 trace 与 UI；
- 不采用“解析结果文本”的原因：结果超过 `max_result_chars` 时会被头尾截断，JSON 可能不完整；
- 备选 B（ctx 注入 emit 回调）仍不采用：会让每个工具反向依赖 trace。

---

## 4. Crawl 工具

### 4.1 工具定义

```python
class CrawlTool(Tool):
    name = "Crawl"
    description = "广度优先抓取站点：限页数/深度/同域/延迟，可选字段抽取，全程只读"
    input_schema = {
        "url": str,                 # 必填，起点
        "max_pages": 10,            # 1–100
        "max_depth": 2,             # 0–5
        "same_domain": True,        # 只跟同域链接
        "delay_ms": 300,            # 每次请求间隔（礼貌爬取）
        "respect_robots": True,     # 读取 /robots.txt（同域）
        "timeout_s": 20,
        "use_browser": "auto",      # auto | always | never
        "extract": [                # 可选：字段定义，见 §5
            {"name": "title", "selector": "h1", "attr": "text"},
            {"name": "author", "selector": ".author", "attr": "text"}
        ]
    }
```

### 4.2 算法

```
frontier = deque([(start_url, parent=None, depth=0)])
visited = set()            # 规范化 URL 去重
while frontier and done < max_pages:
    url, parent, depth = frontier.popleft()
    norm = normalize(url)
    if norm in visited: dedup_hits += 1; continue
    visited.add(norm)
    html, status, final_url = fetch(url, use_browser 策略)
    emit web_fetch(url, final_url, status, duration, bytes, parent_url, depth, kind)
    if status ok and depth < max_depth:
        for link in extract_links(html):
            if same_domain and domain(link) != domain(start): continue
            if respects_robots(link): frontier.append((link, url, depth+1))
    if extract_defs: fields = extract_fields(html, extract_defs); emit web_extract
    if done % 5 == 0: emit crawl_stats
emit crawl_stats(final=True)
```

- **URL 规范化**：去 fragment、小写 scheme/host、去默认端口、路径尾 `/` 统一、查询串保留但排序
  （避免 `?a=1&b=2` 与 `?b=2&a=1` 被当两页）；**目录默认页归一化**（2026-11-10 补）：
  `index.html` / `index.htm` / `default.html` / `default.htm`（大小写不敏感）从路径尾剥掉，
  `/index.html` ≡ `/`，同页不同 URL 只抓一次。
- **硬上限**：`max_pages` 同时限制 `visited` 与 frontier 长度（frontier 超过 `max_pages*10` 截断），
  防大站内存膨胀。
- **失败继续**：单页 4xx/5xx/超时不终止整次爬取，标记 `status=error` 后继续队列。
- **反爬识别**：复用现有 WebFetch 错误口径；识别到 Cloudflare 挑战（`Just a moment...`）时
  该页失败并附原因，**不尝试绕过**。

### 4.3 抓取后端

| 场景 | 后端 | 说明 |
|---|---|---|
| 静态 HTML | `httpx`（复用 `agent/tools/web/web.py::_fetch_url`） | 默认；支持重定向、超时 |
| 需要执行 JS / `use_browser="always"` | `BrowserNavigate` + `BrowserGetText` | 复用现有工具，每页一次导航+取文 |
| `auto` | 先 httpx；若正文长度 < 阈值（如 500 字符）或出现 CF 挑战特征 → 降级浏览器 | 兼顾速度与成功率 |

- 新增一个纯 Python 依赖 `beautifulsoup4`（MIT，无编译）用于解析与选择器；HTTP 仍复用 httpx。
- Browser 后端同一时刻只开一个页面（`BrowserClose` 收尾），避免多实例抢资源。
- **P2 只实现 HTTP 抓取**：`use_browser`（auto/always/never）与浏览器降级在 P4 接入
  （✅ 2026-11-09 已落地：`fetch_via_browser()` 临时页渲染，降级页面 `kind=browser`）；
  P2 的 `_fetch_url` 不返回重定向终点，`final_url` 先省略（不写假值）。

### 4.4 返回结构（供 trace 派生）

```json
{
  "start_url": "https://site/",
  "pages": [
    {"url": "...", "final_url": "...", "status": "ok", "duration_ms": 320,
     "bytes": 12288, "parent_url": "...", "depth": 1, "kind": "http"}
  ],
  "extracted": [
    {"url": "...", "fields": {...}, "missing": ["summary"]}
  ],
  "stats": {"queued": 0, "done": 8, "failed": 1, "dedup_hits": 3,
            "bytes": 94208, "elapsed_ms": 8200}
}
```

文本摘要（给模型看）另附一段人类可读统计，避免模型解析整段 JSON。

---

## 5. 抽取能力（WebExtract）

### 5.1 独立工具 + Crawl 复用

```python
class WebExtractTool(Tool):
    name = "WebExtract"
    input_schema = {
        "url": str,                     # 必填：先抓取再抽取
        "selector": str,                # 可选：作用范围（CSS 选择器）
        "fields": [{"name": "...", "selector": "...", "attr": "text|html|href|src|任意属性名"}]
    }
```

- Crawl 内部复用同一套 `extract_fields()` 实现；
- 输出同时写回给模型（JSON）与 trace（`web_extract` 事件）。

### 5.2 选择器实现（beautifulsoup4，已确认）

用 `beautifulsoup4`（解析器选内置 `html.parser`，不额外引入 lxml）解析页面，
选择器走 `soup.select()`：

- 支持完整 CSS 选择器（`:nth-child`、`:not()`、属性模糊匹配等），由库负责容错；
- `attr=text` 取 `get_text(" ", strip=True)`；`html` 取内部 HTML；`href/src` 取属性；
- 依赖落点：`pyproject.toml` 核心依赖新增 `beautifulsoup4>=4.12`，
  打包脚本按现有 `[all]` 构建自动带上；不引入 lxml（需要编译，桌面打包成本高）。
- 备选：若后续觉得 bs4 太重，可保留同一 `extract_fields()` 接口换回标准库实现，
  抽取层与 Crawl 解耦，不影响驾驶舱。

### 5.3 失败语义

- 字段抽不到 → 进 `missing`，**不写空字符串**，驾驶舱抽取行标“缺 N”；
- 整页抽取无任何字段 → 仍发 `web_extract`（fields={}, missing 全量），让用户看到“抽了但没抽到”，
  而不是静默消失。

---

## 6. 爬虫驾驶舱（窗口改造）

### 6.1 标题与信息架构

标题：`CRAWL // 爬虫驾驶舱`（已改）。窗口从 520×520 调整为 **560×640**，仍贴屏幕最左侧，
仍整面可拖、无边框、终端优先让位。

```
┌ J.A.R.V.I.S   CRAWL // 爬虫驾驶舱        RUN/IDLE  ✕ ┐
│ 抓取 12 | 成功 10 | 失败 1 | 去重 3 | 深度 2 | 128KB │
│ 域名 2 个 · site.com×11 · cdn.site.com×1             │
├ URL 清单 // URLS ───────────────────────────────────┤
│ ✓ L0 https://site/                      320ms 12KB  │
│ ✓ L1 https://site/a                     280ms  8KB  │
│ ✗ L1 https://site/b  403                —      —    │
│ …（最多 8 行，按最后抓取时间倒序）                    │
├ 抽取 // EXTRACT ────────────────────────────────────┤
│ title=React 19 新特性 · author=张三 · 缺 1  [site/a]│
│ …（最多 4 行，点击行高亮拓扑节点）                    │
├ 拓扑 // TOPOLOGY ───────────────────────────────────┤
│  (root)                                             │
│   ├─ /a ── /a1                                      │
│   └─ /b（失败，红）                                  │
│ 队列 2 · 完成 8 · 失败 1 · 去重 3 · 8.2s             │
└──────────────────────────────────────────────────────┘
```

### 6.2 拓扑绘制（分层布局，不做力导向）

- 节点 = 去重后的 URL；边 = `parent_url → url`；
- **层号 = depth**，同层按发现顺序横向排列；根页居中，最多显示 `crawl_max_nodes`（默认 60）个节点，
  超出时把最深一层聚合为 `+N` 节点（点击展开清单，不做交互式缩放）；
- 颜色：成功绿 / 失败红 / 进行中青 / 未抓灰（从 `web_fetch.status` 与队列推导）；
- 节点标签：`/path` 截断（显示宽度 ≤ 12），悬停不实现（Tk Canvas 无原生 tooltip），
  点击节点 → 高亮对应抽取行；点击抽取行 → 高亮节点；
- **交互范围（P3.5 已落地，2026-11-10）**：高亮联动 + 节点上限聚合之外，新增
  ——URL 清单与抽取表支持**鼠标滚轮滚动**（根页固定第一行、`n/总数` 提示与迷你滚动条）；
  拓扑支持 **Ctrl+滚轮缩放（0.55–3.0）、右键/中键拖动平移、双击右键复位**；
  `+N` 聚合节点**点击可按层展开**下一批节点（再点收起）；
  左键单击仍保留“节点↔抽取行高亮”，整窗左键拖动不冲突（先命中节点、空白处才拖窗）；
  **不做节点拖拽**（与整窗拖动/点击高亮冲突，收益低）；
  实现采用“布局纯函数重算 + Tk 视图重绘”而非 `canvas.scale()`（后者不缩放字体，需逐元素补偿）。
- 空态：未接入/无数据时保留现有提示，并统一改成
  「— 等待 Crawl / WebExtract 事件（当前仅 WebFetch/Browser 单页抓取）」。
- **P3 实现补充**：布局纯函数在 `agent/ui/cockpit_crawl_topo.py`；单层超过 8 个节点
  也会折叠为 `+N`（否则标签必然重叠），总数仍受 `crawl_max_nodes` 约束。

### 6.3 统计与状态

- 顶部四格保留（抓取/成功/失败/去重），新增一行小字：`深度 d · 累计字节 · 域名 N · 队列 M`；
- `crawl_stats` 到达时刷新；`run_end` 后保留最后一次统计（与现有“结束保留”一致）。

### 6.4 兼容

- 单页 WebFetch / BrowserNavigate 场景：无 parent/depth → 清单 `L-`、拓扑区显示
  「单页抓取，无拓扑」而不是空白错误态；
- `crawl_window="off"`：不创建窗口（不变）。

---

## 7. 配置

### 7.1 `[visualization]`（界面）

| 键 | 默认 | 说明 |
|---|---|---|
| `crawl_window` | `"auto"` | 已有：auto/off |
| `crawl_topology` | `"auto"` | 新增：auto 画拓扑；off 只保留清单/抽取 |
| `crawl_max_nodes` | `60` | 新增：拓扑节点上限（20–200），超出聚合 |

### 7.2 `[crawl]`（工具）

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 关闭时 Crawl/WebExtract 不注册 |
| `max_pages` | `10` | 1–100，硬上限 |
| `max_depth` | `2` | 0–5 |
| `same_domain` | `true` | 只跟同域 |
| `delay_ms` | `300` | 请求间隔（礼貌爬取） |
| `respect_robots` | `true` | 读同域 robots.txt |
| `timeout_s` | `20` | 单页超时 |
| `use_browser` | `"auto"` | auto/always/never |
| `max_bytes_per_page` | `2000000` | 单页正文上限，超出截断 |

配置落点四处同步：`settings.py`、`settings_domains.py`、`catalog.py`、`settings.example.toml`；
`/config explain crawl` 可查。

---

## 8. 模块与文件规划

| 文件 | 变更 | 职责 |
|---|---|---|
| `agent/tools/web/crawl.py` | 新增 | `CrawlTool`：BFS、限页/深度/同域、robots、统计 |
| `agent/tools/web/extract.py` | 新增 | `WebExtractTool` + `extract_fields()`（beautifulsoup4） |
| `pyproject.toml` | 修改 | 核心依赖新增 `beautifulsoup4>=4.12`（纯 Python、MIT） |
| `agent/core/trace.py` | 修改 | 派生 `web_fetch.parent_url/depth/kind`、`web_extract`、`crawl_stats` |
| `agent/core/tool.py` / 注册装配 | 修改 | 注册 Crawl / WebExtract（按 `[crawl] enabled`） |
| `agent/ui/cockpit.py` | 修改 | `fetches` 保留 parent/depth；`FETCH_MAX` 20→200；`topology()` 纯函数 |
| `agent/ui/cockpit_crawl.py` | 修改 | 窗口 560×640、拓扑 Canvas、抽取行/节点联动、L 层级标签 |
| 配置四处 | 修改 | 见 §7 |

每个文件继续遵守 900 行上限；`cockpit_crawl.py` 预计接近上限，拓扑绘制抽到
`cockpit_crawl_topo.py`（纯函数 + Canvas 绘制）。

---

## 9. 分阶段交付（每阶段可独立验收）

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P1 抽取**（✅ 2026-11-09） | 新增 `WebExtract`；trace 派生 `web_extract`；驾驶舱抽取表点亮 | 对单页抽 3 个字段 → 表出现 1 行且字段正确；缺字段显示“缺 N” |
| **P2 爬虫**（✅ 2026-11-09） | `Crawl`（httpx BFS）；`web_fetch` 补 parent/depth；`crawl_stats`；清单显示 L 层 | 爬本地静态站 10 页：清单/统计/去重/深度正确；同域外链不入队 |
| **P3 拓扑**（✅ 2026-11-09） | Canvas 分层拓扑、节点上限聚合、节点↔抽取联动；窗口 560×640 | 拓扑有边、失败红、超过 60 节点聚合为 +N |
| **P3.5（✅ 2026-11-10）** | URL/抽取列表滚轮滚动（根页固定置顶、数量提示、迷你滚动条）、URL 保留尾部智能截断；拓扑 `Ctrl+滚轮` 缩放、右键拖动平移、双击右键复位、`+N` 聚合点击展开；统计文案改「剩余/深度上限」 | 12 页实测：清单可滚、标签无重叠、+N 可展开、缩放平移复位正常；全量测试通过 |
| **P4 浏览器降级**（✅ 2026-11-09） | `use_browser` auto/always/never；`fetch_via_browser()` 临时页渲染；CF 识别与失败标记 | JS 页面能爬到正文；CF 页面标红且不绕过 |
| **P3.6 拓扑外弹（✅ 2026-11-10）** | 拓扑区左键拖动撕出独立窗（960×720 可拉伸、适应画布、Esc 取消）；点 ✕ 220ms 收回动画飞回原位；视图状态共享零迁移、跨窗高亮联动。细节见 [crawl-topology-detach.md](crawl-topology-detach.md) | 人工走查通过；定向 70 条 + 全量测试通过 |

> P1 → P2 → P3 → P4 已全部完成；**P3.5 于 2026-11-10 实测触发**（books.toscrape.com 12 页单层 11 节点标签重叠、清单/抽取被截断不可回看），同日完成落地。
> 触发记录见 `docs/fixlogs/crawl-cockpit-p35.md`。

---

## 10. 测试计划

### 10.1 纯逻辑（无网络、无 Tk）

- URL 规范化与去重（fragment/尾斜杠/query 排序）；
- 同域判定（子域是否算同域，默认算）；
- BFS 深度/页数上限、frontier 截断；
- `beautifulsoup4` 选择器与字段抽取（text/html/href/src、缺字段、脏 HTML 容错）；
- 拓扑布局坐标：层号、同层排序、节点上限聚合；
- `CockpitState` 对 `web_extract` / `crawl_stats` / 带 parent/depth 的 `web_fetch` 的消费。

### 10.2 集成

- 本地 `http.server` 静态站（3 层、10 页、含 404/重复链接）：
  - 断言 `web_fetch` 条数 = 抓取页数、parent/depth 正确；
  - 断言 `dedup_hits`、`failed`、`crawl_stats` 次数（每 5 页 + 收尾）；
- `TraceRecorder` 派生：给定 Crawl 返回 JSON → 事件序列与字段一一对应；
- robots.txt 禁止路径不入队。

### 10.3 UI

- 空态文案、单页无拓扑态、节点上限聚合、点击联动（纯函数层验证，不依赖真实 Tk）；
- 真实 Tk 冒烟：构造带 parent/depth + 抽取 + 统计的 `CockpitState`，渲染后 `find_all()` > 0 且销毁正常。

### 10.4 回归

- 现有 WebFetch / BrowserNavigate 场景：清单/汇总不变，拓扑显示“单页抓取，无拓扑”；
- `crawl_window="off"`、`crawl_topology="off"` 行为；
- 全量 `python -m pytest tests -q` 必须全绿。

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| 大站 frontier 膨胀 | `max_pages` 硬上限 + frontier 截断 + `visited` 去重 |
| 被封/CF | 不绕过；该页标红并继续；报告中说明 |
| 不当抓取（隐私/条款） | 默认同域、限页、延迟 300ms、遵守 robots；不做登录态 |
| 选择器能力不足 | 已换用 bs4（完整 CSS、容错好），风险降为“依赖体积”；不引入 lxml |
| 窗口信息过载 | 节点上限 60 + 聚合；清单 8 行、抽取 4 行；拓扑可关 |
| 新增依赖 | 已确认新增 `beautifulsoup4`（纯 Python、MIT、无编译）；不引入 lxml，打包按 `[all]` 自动带上 |

---

## 12. 待评审确认项

已确认（2026-11-09）：

- ✅ **选择器**：使用 `beautifulsoup4`（`html.parser` 后端），不引入 lxml；
- ✅ **拓扑交互**：只做“节点↔抽取行高亮 + 节点上限聚合”，不做缩放/平移/拖拽；实测节点重叠再加 P3.5 滚轮缩放；
- ✅ **默认抓取参数**：同域、深度 2、10 页、延迟 300ms、遵守 robots，保持不变。

- ✅ **窗口**：命名「CRAWL // 爬虫驾驶舱」，尺寸 **560×640**，仍贴屏幕最左侧；
- ✅ **阶段顺序**：P1 抽取 → P2 爬虫 → P3 拓扑 → P4 浏览器降级，每阶段独立验收并跑全量测试。

无剩余待确认项，可进入 P1 开发。
