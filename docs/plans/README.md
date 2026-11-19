# 实施计划索引（plans）

> 本目录收录「**已定方向、落到文件与接口**」的实施/工程方案：写清做哪些文件、函数签名、
> 测试矩阵与验收命令。与另外两类文档的分工：
>
> - [roadmap/](../roadmap/)：阶段目标与取舍（**做不做、为什么做**）；
> - 本目录 `plans/`：落地细节（**怎么做、落到哪**）；
> - [fixlogs/](../fixlogs/)：事后复盘（**出了什么问题、怎么修的**）。
>
> **为什么不用子目录物理分组**：全仓大量文档以相对路径引用这里的文件，`aoci.code.txt` 也为
> 每个文件建了索引对象——搬目录会同时打断链接与认知索引（需重写全仓链接 + 重建索引），
> 代价远大于收益。因此用**分类索引**解决“找得到”的问题，文件位置保持不变。
>
> 新增一篇后，请在下方对应分类补一行（文件名 + 一句话 + 状态）。

## 分类总览

| # | 分类 | 篇数 |
|---|---|---:|
| 1 | [画像记忆 / 长期记忆](#1-画像记忆--长期记忆) | 5 |
| 2 | [可视化与驾驶舱](#2-可视化与驾驶舱) | 8 |
| 3 | [桌面壳与主动播报](#3-桌面壳与主动播报) | 2 |
| 4 | [操作可见性](#4-操作可见性) | 2 |
| 5 | [配置与能力自检](#5-配置与能力自检) | 2 |
| 6 | [文件操作](#6-文件操作) | 1 |
| 7 | [语音](#7-语音) | 1 |
| | **合计** | **21** |

---

## 1. 画像记忆 / 长期记忆

| 文档 | 内容 | 状态 |
|---|---|---|
| [memory-profile-phase1a.md](memory-profile-phase1a.md) | Phase 1a 画像记忆初版：提炼 / 存储 / 注入的全链路设计 | 历史基线（现由 P0–P2 取代） |
| [profile-memory-p0-impl.md](profile-memory-p0-impl.md) | P0：提炼水位增量 + 三触发 + P4 评测集（**空闲触发已于 2026-11-17 下线**） | 已落地 |
| [profile-memory-p1-impl.md](profile-memory-p1-impl.md) | P1：两段式裁决（抽取 → 逐条召回 → 四操作）+ 别名 + 注入侧改造 | 已落地（P1a–P1d） |
| [profile-memory-p2-impl.md](profile-memory-p2-impl.md) | P2：lifecycle 分层 + 软删除失效链 + 事件轨；衰减改降权休眠 | 已落地（P2a–P2c） |
| [profile-memory-p6-persona-impl.md](profile-memory-p6-persona-impl.md) | P6：常驻人设卡 + `RecallProfile` list 全量分页；全文归档不变 | 已落地（P6a–P6f） |

## 2. 可视化与驾驶舱

按阶段排列：总纲 → C1 终端 → C2 爬虫 → C3 多任务浮窗 → 子代理任务卡 → 图片驾驶舱；
`workbench-gui` 为已退役的前代 GUI。

| 文档 | 内容 | 状态 |
|---|---|---|
| [jarvis-cinematic-visualization.md](jarvis-cinematic-visualization.md) | 系列总纲：JARVIS 式多弹窗与爬虫可视化构想（C1/C2/C3 的源头） | 构想 |
| [terminal-cockpit.md](terminal-cockpit.md) | C1 终端任务驾驶舱（C1b 独立窗口） | 已实施（2026-11-08） |
| [crawler-cockpit.md](crawler-cockpit.md) | C2 爬虫驾驶舱与真爬虫工具（P1 抽取 / P2 爬虫 / P3 拓扑 / P4 降级） | 已完成（2026-11-09） |
| [crawl-topology-detach.md](crawl-topology-detach.md) | C2 P3.6：拓扑区可撕出独立窗口（拖拽 + 收回动画） | 待评审（2026-11-10） |
| [cockpit-multicards.md](cockpit-multicards.md) | C3-lite 多任务浮窗与配置管理 | 已实施第一版（2026-11-08） |
| [agent-cards.md](agent-cards.md) | C3-lite.2 子代理 / 队友任务卡（多任务浮窗扩展） | 已实现（2026-11-08，待实机验收） |
| [image-window.md](image-window.md) | 图片驾驶舱 P0：终端里真的能看见图 | 已定稿，P0 开发中（2026-11-11） |
| [workbench-gui.md](workbench-gui.md) | 三栏 GUI 工作台（前代方案） | 已退役（2026-10-14） |

## 3. 桌面壳与主动播报

| 文档 | 内容 | 状态 |
|---|---|---|
| [jarvis-desktop.md](jarvis-desktop.md) | jarvis-desktop 立项：Electron 壳 + jarvis serve 后端 API | 一期已落地（2026-09） |
| [proactive-desktop.md](proactive-desktop.md) | 主动播报桌面接线（方案 A） | 已交付（2026-09） |

## 4. 操作可见性

| 文档 | 内容 | 状态 |
|---|---|---|
| [operation-visibility.md](operation-visibility.md) | 操作轨迹 + 窗口跟随（先终端、后桌面） | P1–P3 已实现（2026-11-07） |
| [window-observer.md](window-observer.md) | 通用窗口观察器：取代“按工具名登记是否跟随”的白名单 | 已实施（2026-11-07） |

## 5. 配置与能力自检

| 文档 | 内容 | 状态 |
|---|---|---|
| [config-self-service-batch1.md](config-self-service-batch1.md) | 自配置层第二批扩张（第一批：模型与上下文） | 设计定稿，待开发 |
| [capability-selfcheck.md](capability-selfcheck.md) | 能力自检与自我修复（`/health caps` + `agent/capabilities/`） | 待立项批准（设计定稿） |

## 6. 文件操作

| 文档 | 内容 | 状态 |
|---|---|---|
| [fileops-desktop-upgrade.md](fileops-desktop-upgrade.md) | 桌面文件操作升级（归类 / 整理 / 隔离区） | 待评审（v1.0，2026-08-29） |

## 7. 语音

| 文档 | 内容 | 状态 |
|---|---|---|
| [voice-provider-decoupling.md](voice-provider-decoupling.md) | 语音能力厂商解耦·阶段一（`/talk` 全双工 + `/voice` 半双工） | S1 调研完成，待拍板首批接入名单 |

---

## 新增一篇时

1. **命名**：`<主题>-<切片>.md`（如 `profile-memory-p2-impl.md`）；同一阶段的多刀用后缀区分；
2. **头部状态行**：`> 状态：**…** 作者：… 日期：…`，并链接上位 roadmap 文档；
3. **只写落点**：文件路径、函数签名、数据结构、测试矩阵、验收命令；取舍与成本账留在 roadmap；
4. **在本索引登记**：在对应分类表补一行（链接 + 一句话 + 状态），并更新分类总览的篇数/合计；
5. **跑链接门**：`python scripts/check_md_links.py`（0 死链、0 未入库链接）；
6. **与代码同批提交**，commit message 注明本文档。
