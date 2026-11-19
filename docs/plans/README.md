# 实施计划索引（plans）

> 本目录收录「**已定方向、落到文件与接口**」的实施/工程方案：写清做哪些文件、函数签名、
> 测试矩阵与验收命令。与另外两类文档的分工：
>
> - [roadmap/](../roadmap/)：阶段目标与取舍（**做不做、为什么做**）；
> - 本目录 `plans/`：落地细节（**怎么做、落到哪**）；
> - [fixlogs/](../fixlogs/README.md)：事后复盘（**出了什么问题、怎么修的**）。
>
> **按主题分 7 个子目录**，每个子目录自带索引（本页只做总览）。

## 分类总览

| # | 分类 | 篇数 | 目录 |
|---|---|---:|---|
| 1 | 画像记忆 / 长期记忆 | 5 | [profile-memory/](profile-memory/README.md) |
| 2 | 可视化与驾驶舱 | 8 | [visualization/](visualization/README.md) |
| 3 | 桌面壳与主动播报 | 2 | [desktop/](desktop/README.md) |
| 4 | 操作可见性 | 2 | [visibility/](visibility/README.md) |
| 5 | 配置与能力自检 | 2 | [config/](config/README.md) |
| 6 | 文件操作 | 1 | [fileops/](fileops/README.md) |
| 7 | 语音 | 1 | [voice/](voice/README.md) |
| | **合计** | **21** | |

---

## 新增一篇时

1. **命名**：`<主题>-<切片>.md`（如 `profile-memory-p2-impl.md`）；同一阶段的多刀用后缀区分；
2. **头部状态行**：`> 状态：**…** 作者：… 日期：…`，并链接上位 roadmap 文档；
3. **只写落点**：文件路径、函数签名、数据结构、测试矩阵、验收命令；取舍与成本账留在 roadmap；
4. **归档**：放进对应子目录；在**该子目录的 README** 补一行（链接 + 一句话 + 状态），
   并在本文「分类总览」更新篇数；
5. **跑链接门**：`python scripts/check_md_links.py`（0 死链、0 未入库链接）；
6. **与代码同批提交**，commit message 注明本文档。
