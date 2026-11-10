# 拓扑撕出独立窗（P3.6）落地复盘

> 作者：aceFelix
> 日期：2026-11-10
> 关联：[crawl-topology-detach.md](../plans/crawl-topology-detach.md)（本方案）、[crawler-cockpit.md](../plans/crawler-cockpit.md)（总规划）

## 1. 问题现象

P3.5 的 30 页实测把全部交互验证通过（滚动、缩放、平移、+N 展开），但也暴露了
本性质问题：L1 展开到约 29 个节点后，标签在 524×160 的拓扑区内完全不可读。
先生决策：拓扑区支持**左键按住直接拖出为独立窗口**，看完点 ✕ 带动画收回。

## 2. 手势契约（先生最终纠正版，以此为准）

- **拓扑区内**：左键按住拖动 = 撕出（无长按、无按钮）；单击不拖动 = 节点/聚合高亮联动；
- **拓扑区外**：左键拖动 = 移动整个驾驶舱（现状不变）；
- 阈值：8px 武装（区分点击与拖动）、80px/拖出原区域（区分真撕出与取消）、Esc 取消；
- 撕出拖动中显示半透明幽灵预览（不抢焦点），松手在光标处生成 960×720 可拉伸独立窗。

## 3. 实现要点

- 新增 `agent/ui/cockpit_topo_view.py`：
  - `TopoViewState` 共享视图状态（scale/pan/expanded/selected），驾驶舱与独立窗
    持有同一实例，外弹/收回零迁移；
  - `TopoView` 复用绘制与视图变换组件，双方窗口共用；
  - `TearOffGesture` 纯状态机、动画插值（ease-out 220ms/14 帧）、`fit_view`；
- 新增 `agent/ui/cockpit_topo_detach.py`：`TopoDetachedWindow`（✕ 收回 + 适应画布 +
  收回动画）与 `TearOffGhost`（半透明跟随预览）；
- `cockpit_crawl.py`：左键按下按拓扑矩形分流；CrawlManager 持有独立窗/幽灵生命周期，
  提供 `rerender_all / select_url / dock_rect / undock` 接口；外弹后驾驶舱拓扑区
  画占位（点击收回）；
- 收回动画以驾驶舱拓扑区**实时屏幕矩形**为终点（`winfo_rootx/y`），
  alpha 渐隐 1.0→0.2，末帧销毁并原位重绘；
- 不做：拖回驾驶舱来 dock、多份同时外弹、位置持久化、节点拖拽。

## 4. 验证结果与实测后修正

- 先生首次实测撕出成功，但独立窗首帧拓扑挤在左上角一小块：创建瞬间窗口未
  映射，`winfo_width()` 返回 1，布局按 200×160 兜底尺寸绘制，且首帧
  `<Configure>` 与预设默认尺寸相同、未触发重绘。修复：`_size_now()` 在
  未映射时（`winfo_ismapped()` 为假）回退设计尺寸 960×720，渲染同步记录
  本次使用的尺寸。
- 先生反馈"拖出后还要手动点适应画布"：**落点即自动 fit**，撕出第一帧就是
  完整大图（`CrawlManager.detach_topology` 创建后调用 `fit()`）。
- 自动化：`tests/ui/test_cockpit_topo_view.py`（手势状态机、落点判定、动画插值、
  fit 计算、共享状态）；`test_cockpit_crawl.py` 增加撕出接线（区内外分流、Esc 取消、
  占位绘制）与 `TestTopologyInteraction` 迁移到新 API；定向 70 条全绿。
- 全量 `pytest tests -q` 通过。
- 人工走查：先生实测后确认效果。

## 5. 遗留

- `/` 与 `/index.html` canonicalization 合并（Crawl 工具层，独立于本方案）。
