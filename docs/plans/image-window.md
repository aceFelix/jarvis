# 图片驾驶舱（P0）工程方案

> 状态：**已定稿，P0 开发中**（2026-11-11 先生批准：§11 四项全按默认，唯一改动是窗口位置——**屏幕左上角、可自由移动**）
> 作者：aceFelix
> 日期：2026-11-11
> 关联：[operation-visibility.md](operation-visibility.md)（P4「失败截图未做」）、[terminal-cockpit.md](terminal-cockpit.md)、[crawler-cockpit.md](crawler-cockpit.md)、[crawl-topology-detach.md](crawl-topology-detach.md)（窗口生命周期范式）

---

## 1. 背景与问题

终端目前**只有图片入站通道，没有出站通道**：图能被模型看见，人看不见。

| 环节 | 现状 | 证据 |
|---|---|---|
| 图片来源 | `ScreenShot` / `CameraShot` / `BrowserScreenshot` / `/paste` / `/image` | `tools/system/screen.py`、`tools/vision/camera.py`、`tools/web/browser.py`、`commands/handlers/media_commands.py` |
| 编码 | `encode_pil_image()` 缩放后 base64，**PIL 对象随即丢弃** | `core/images.py:29-70` |
| 承载 | `ImageContent.data`（纯 base64），**没有路径字段** | `core/message.py:27-41` |
| 送给模型 | `ToolResult.images` → `ToolResultContent.images` | `core/result.py:100-102` |
| 可视化层 | `tool_after` 的 payload **不含 images**，只有 `trace_meta` 与 `window_policy` | `core/orchestrator.py:502-524` |
| 终端渲染 | 只有正文/思考/工具卡/Markdown/代码/diff/表格 | `ui/cli.py`、`ui/markdown_renderer.py` |

后果：模型说「我看了截图，按钮在右下角」时，**用户无法核对它看的是不是同一张图**；`operation-visibility`
计划里写着的「失败截图未做」也卡在同一处——不是不想截，是截完了没地方看。

## 2. 目标与非目标

### 2.1 目标

1. 工具产出的图**多一条落点**：不改变送给模型的图片块，另在 trace 上派生 `image_show` 事件（只带路径与元信息，不带 base64）。
2. 新增**独立图片驾驶舱**（Tk，复用驾驶舱的线程模型与视觉语言），显示最近 N 张图的缩略条 + 当前大图 + 元信息（来源工具 / 尺寸 / 格式 / 路径 / 时间）；默认停靠**屏幕左上角**，整面可拖动且记住位置。
3. 与既有窗口同生命周期：本轮有图才弹、结束保留、点 ✕ 只隐藏、下轮自动重现、主 HUD 隐藏时同步隐藏、终端前台时让位置顶。
4. `[visualization]` 增三项配置（`image_window` / `image_max` / `image_thumb_px`），默认开，可一键关。
5. **不新增运行依赖**：Pillow 已是既存依赖（`encode_pil_image` 在用），`PIL.ImageTk` 只需 tkinter（Windows 自带）。

### 2.2 非目标

- ❌ 终端内真图（sixel / kitty / iTerm2 inline）：Windows Terminal 不支持，不值得为它写多分支适配；
- ❌ 改 `ImageContent` 结构（它是 provider 契约，动它要同步四个 provider 的序列化与测试）；
- ❌ 从 base64 现场解码显示（会引出「送给模型那张」与「给人看这张」的尺寸链混用）；
- ❌ 图片编辑/标注/OCR（P1 之后再议）；
- ❌ 桌面壳 `<img>` 消费（兄弟仓 `jarvis-desktop`，本仓只把 `image_show` 事件契约备好）。

## 3. 关键设计决策

### D1 图片的「人看」路径不进 provider 契约

新增轻量引用对象，放在 `agent/core/images.py`（图片语义的既有归属）：

```python
@dataclass(frozen=True)
class ImageRef:
    """一张「给人看」的图片引用：只有路径与元信息，不含字节。

    与 ImageContent 的分工：ImageContent 是送给 provider 的 base64 块，
    由 model_type 决定看不看得见；ImageRef 只服务终端窗口与 trace，
    永不参与 provider 序列化。
    """
    path: str
    source: str = ""          # ScreenShot / CameraShot / BrowserScreenshot
    width: int = 0
    height: int = 0
    media_type: str = ""
    bytes: int = 0
    ts: str = ""              # ISO 时间，展示用

    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, raw: Any) -> "ImageRef | None": ...
```

`from_dict` 必须容忍脏数据返回 `None`（与 `trace._as_event_list` 同口径：错了就不写事件，不写假值）。

### D2 事件走 `trace_meta` 旁路，工具侧零结构改动

`orchestrator` 的 `tool_after` payload 已带 `trace_meta`（`core/orchestrator.py:523`），
`trace.on_tool_after` 已有把 `trace_meta` 展开成派生事件的成熟范式（`core/trace.py:362-388`，
WebExtract / Crawl 就是这么上驾驶舱的）。图片沿用同一条通道：

```python
# core/trace.py，紧跟 crawl_stats 之后
for image in _as_event_list(trace_meta.get("image")):
    ref = str(image.get("path") or "")
    if not ref:
        continue                      # 没有路径就没有展示位，不写假路径
    self._emit({"type": "image_show", **image, **(agent_meta or {})})
```

工具侧各加 1–3 行，把已有变量塞进 `trace_meta`：

```python
return ToolResult.ok(summary, images=images,
                     trace_meta={"image": ref.to_dict()} if ref else {})
```

事件 schema（**只记路径与元信息，绝不带 base64**，与 trace「只记路径不记内容」的隐私口径一致）：

```json
{
  "type": "image_show",
  "path": "C:\\Users\\...\\Temp\\jarvis-shots\\screenshot_20261111_123456.png",
  "source": "ScreenShot",
  "width": 2560, "height": 1440,
  "media_type": "image/jpeg", "bytes": 184320,
  "ts": "2026-11-11T12:34:56.789+08:00",
  "tool_use_id": "call_abc", "agent_id": "researcher@team"
}
```

### D3 三个来源的落点策略不同，按现状分别处理

| 工具 | 现状 | 方案 | 理由 |
|---|---|---|---|
| `ScreenShot` | 落**系统临时目录** `jarvis-shots/`（`screen.py:39-43`），用户可传 `path` 覆盖 | **不改默认落点**，`ImageRef.path` 指该临时文件 | 改落点会牵动既有测试与用户可见行为；图片驾驶舱按「过期占位」优雅降级 |
| `CameraShot` | 落 `~/.jarvis/cam/`（`camera.py:58,178`），已经持久 | 直接用该路径 | 零改动 |
| `BrowserScreenshot` | **完全不落盘**，`page.screenshot()` bytes 直进 `encode_pil_image`（`browser.py:288-299`） | **新增落盘**到 `~/.jarvis/media/browser_<ts>.png`，再编码 | 网页截图是用户最想留存的图；且没有路径就没法展示 |

`BrowserScreenshot` 的 `path` 参数可覆盖；落盘失败**只降级不报错**（沿用它现在的「编码失败降级纯路径」思路）。

### D4 窗口形态：独立 Tk 窗，照抄 CrawlManager 的生命周期

不复用抓取窗（数据模型不同、位置会打架），新建 `CrawlManager` 的兄弟类，
生命周期四件套完全对齐（`ui/cockpit_crawl.py:624-737`）：

| 方法 | 语义 |
|---|---|
| `sync(state, run_id)` | 有图数据才建窗；本轮用户关过则不再自动弹；换轮重置缩略条滚动与选中 |
| `hide_all()` | 主 HUD 关闭时隐藏（不销毁、不影响下轮重现） |
| `show_all(state)` | 主 HUD 重现时同步显示 |
| `detach()` | 退出时先置空 Tk 引用再 `destroy`（防 `Tcl_AsyncDelete`，`cockpit_crawl.py:185-199`） |
| `set_topmost(bool)` | 终端前台时让位（接 `TerminalAwareTopmost`） |

窗口规格：

| 项 | 规格 |
|---|---|
| 尺寸 | `480×600`，无边框、深色 HUD 语言、标题 `IMAGE // 图片` |
| 位置 | 默认**屏幕左上角** `(24, 24)`；整面可拖动（超 4px 才算拖），松手写入 `~/.jarvis/ui_state.json` 的 `image` 段（与 HUD 的 `cockpit` 段并列），下次启动恢复并钳制在屏幕内 |
| 主区 | 当前大图，等比适配窗宽（`fit`）；双击切 `fit / 1:1` |
| 缩略条 | 底部一行，最多 `image_max` 张（默认 12），点击切换当前图，滚轮横向滚动 |
| 元信息 | 来源、`宽×高`、格式与体积、时间、路径（点击复制到剪贴板） |
| 控件 | 右上 `✕` 隐藏；`Esc` 立即隐藏（隐私快退） |
| 占位 | 文件已被清理 → 大图区显示 `‹图已过期›` + 保留全部元信息（不弹错、不崩） |

## 4. 数据流

```
ScreenShot / CameraShot / BrowserScreenshot
        │  ① 产出 PIL.Image 与落盘路径
        ├──► encode_pil_image()  ──► ImageContent（base64，送模型，尺寸链 A：≤1280 JPEG85）
        └──► ImageRef(path, w, h) ──► ToolResult.trace_meta["image"]
                                          │
orchestrator tool_after ──► trace.on_tool_after ──► ② _emit("image_show")
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    ▼                                           ▼
          CockpitState.images（有界容器）            traces/<run>.jsonl（只落路径）
                    ▼
          CockpitWindow._pump ──► ③ ImageManager.sync()
                    ▼
          ImageWindow（Tk 线程）──► 按 path 读盘 ──► 缩略图（缓存）/ 大图
```

**两条尺寸链必须分开**：`ImageContent` 的 1280px / JPEG85 是省 token 的策略；
窗口显示走原图 + 屏幕适配（`image_thumb_px` 默认 88，只作用于缩略条）。
把给模型那张拿去给人看，等于让用户误判模型看到的分辨率。

## 5. 文件规划与行数（**已按实测回填**，2026-11-11 落地后）

| 文件 | 变更 | 职责 | 实测 |
|---|---|---|---|
| `agent/ui/cockpit_image.py` | **新增** | `ImageWindow`（Tk 绘制/缩略条/大图/元信息/命中/拖动）+ `ImageManager`（生命周期四件套）+ `PhotoImage` 缓存 | 584 行 |
| `agent/ui/image_cache.py` | **新增** | 纯逻辑：缩放尺寸、缓存键、存活判定、布局坐标、元信息文案（可单测，无 Tk） | 194 行 |
| `agent/ui/image_demo.py` | **新增** | 开发期预览：`python -m agent.ui.image_demo` 生成三张测试图并只弹图片驾驶舱（不跑对话、不需要 pyautogui） | 78 行 |
| `agent/ui/cockpit_window.py` | 修改 | 瘦身：`_draw_feed`/`_draw_fetches`/`_section` 迁入 `cockpit_detail`；新增 `image_show` 分发与 `ImageManager` 装配 | 900 → **894** |
| `agent/ui/cockpit.py` | 修改 | `IMAGE_MAX`、`CockpitState.images` 有界容器、`normalize_image_window()`、`VisualOptions` 三项、`image_on()`、`any_enabled()` | 575 → 621 |
| `agent/core/images.py` | 修改 | `ImageRef` + `to_dict`/`from_dict` + `make_image_ref()` | 178 → 297 |
| `agent/core/trace.py` | 修改 | `on_tool_after` 派生 `image_show`（`ImageRef` 白名单重建） | 781 → 790 |
| `agent/ui/cockpit_position.py` | 修改 | `ui_state.json` 分段读-改-写 + `image` 段 | 51 → 88 |
| `agent/tools/web/browser.py` | 修改 | 网页截图落 `~/.jarvis/media/` + `trace_meta` | 729 → 754 |
| `agent/tools/system/screen.py` | 修改 | `trace_meta` + 引用构造 | 397 → 407 |
| `agent/tools/vision/camera.py` | 修改 | `trace_meta` + 引用构造 | 309 → 319 |
| `agent/config/settings.py` | 修改 | `visual_image_*` 三个字段 + 注释 | 832 → 842 |
| `agent/config/settings_domains.py` | 修改 | `[visualization]` 三个映射项 | 423 → 427 |
| `agent/config/catalog.py` | 修改 | 三条问答文案 | 762 → 766 |
| `agent/configs/settings.example.toml` | 修改 | `[visualization]` 补三项与注释 | 482 → 491 |
| `tests/ui/test_cockpit_image.py` | **新增** | 缩略尺寸、缓存键含 mtime、存活判定、左上默认位置、缩略条布局、元信息文案 | 145 行 |
| `tests/ui/test_image_state.py` | **新增** | `images` 有界/换轮清空/无路径丢弃/子代理图保留、`VisualOptions` 归一化、`ImageManager` 生命周期（假窗口） | 233 行 |
| `tests/core/test_trace_images.py` | **新增** | `trace_meta["image"]` 派生、dict/list/脏值、**无 base64**、JSONL 落盘、agent 标注 | 126 行 |
| `tests/ui/test_cli_cockpit.py` | 修改 | 「全 off」补上图片驾驶舱 + 新增「只开图片驾驶舱仍建根窗口」 | 219 → 239 |

合计 **+1311 行（含测试）**，无新依赖（Pillow 既存，`ImageTk` 只需 tkinter）。

### 5.1 已执行的一步：给 `cockpit_window.py` 腾行

`agent/ui/cockpit_window.py` 原本**恰好 900 行**，而 `tests/test_engine_split_guards.py`
就是拦这个数的门禁。实际做法：

1. `_draw_feed` / `_draw_fetches` 两个纯绘制段迁为 `cockpit_detail.draw_feed` /
   `draw_fetches`，并把 `_section` 的 108px 缩进线收成 `draw_section`（坐标只留一处）；
2. 900 → 869 之后再接线，落 **894 行**。

⚠️ **894 只剩 6 行余量**：P1（失败截图联动 + `VisualClick` 矩形叠加）必须先继续瘦身
（`_draw_metrics` / `_draw_current` 是下一个候选），否则下一次改动就撞门禁。

## 6. 配置项（四处同步，漏一处就是"写了不生效"）

```toml
[visualization]
# 图片驾驶舱：auto = 本轮出现工具产出的图就弹、结束保留、点 ✕ 只隐藏；off = 完全不创建。
image_window = "auto"
# 缩略条最多保留几张（1–24）。超出后最旧的滑出，只留最近 N 张。
image_max = 12
# 缩略条单张缩略图的最长边像素（48–160）。大图区恒按窗宽适配，不受此值影响。
image_thumb_px = 88
```

| 落点 | 具体位置 |
|---|---|
| 字段定义 | `agent/config/settings.py`（`visual_crawl_*` 之后，`:207-215` 同段） |
| TOML 映射 | `agent/config/settings_domains.py` 的 `[visualization]` 表（`:91-111`） |
| `/config explain` | `agent/config/catalog.py`（`:205-224` 同段，全字段必须覆盖） |
| 模板 | `agent/configs/settings.example.toml`（`:148` 段） |

> `/config explain` 与 `/config list` 读的是 `catalog.py` + `config_specs.py`。
> `[visualization]` **不在** 63 项真源表里（`config_specs.py` 无 `visual_*`），
> 所以本次**不改** `config_specs.py`，但**必须**改 `catalog.py`，否则问答面查不到。

归一化函数放 `ui/cockpit.py`（与 `normalize_crawl_window` 并列）：

```python
def normalize_image_window(value: Any) -> str:
    """图片驾驶舱模式：``auto`` / ``off``；未知值降级为 auto。"""
    return "off" if str(value or "").strip().lower() == "off" else "auto"
```

`VisualOptions` 增 `image_window: str = "auto"`、`image_max: int = 12`、`image_thumb_px: int = 88`，
并提供 `image_on()`（与 `crawl_on()` 同构，受总开关 `enabled` 约束），
并计入 `any_enabled()`——**否则只开图片驾驶舱时 `feed()` 会在 `:136` 直接返回**。

## 7. 缩略图缓存与性能（**按实测回填**）

- 键 = `path | mtime_ns | 目标边像素`（大图另加 `main|` 前缀）；文件被改写 → 键变 → 自动失效
  （固定路径反复截图的场景必需）。
- 上限 `THUMB_CACHE_MAX = 24`（略大于 `image_max`）；超限时**只丢不在当前画面上的最旧条目**
  （`_current_keys` 保护），否则正在显示的那张被丢会立刻变白。
- **必须在 Tk 线程内创建 `PhotoImage`**；主线程只传路径。
- **必须持有引用**：全部 `PhotoImage` 存在实例的 `_photos` 字典里；`_main_photo` 式的局部变量
  会被 GC 回收 → 全白窗（最难查的一类）。
- 解码开销：`Image.draft("RGB", ...)` 让 JPEG 直接在解码期降采样（2560×1440 → 88px 差一个
  数量级），再加 `thumbnail()`。首帧会一次性解「可见的 N 张」（N ≤ `image_max`，实测常见 1–3 张），
  之后按需命中缓存；换图只解当前大图一张。
- 极端情况下 `Image.open` 失败（文件被占用/半截写入）：只把这一张画成占位并记 `diag`，
  窗口其余部分照常。

## 8. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| `PhotoImage` 未持引用被 GC | 窗口全白，最难查 | 实例属性持有 + 回归测试断言引用存在 |
| Tk 非线程安全 | 崩解释器 | 所有 Tk 调用只在 Tk 线程；主线程只 `queue.put` |
| `cockpit_window.py` 撞 900 行门禁 | CI 红 | §5.1 先瘦身再接线的固定顺序 |
| 收尾清理误关图片驾驶舱 | 图窗在任务结束时被关掉 | 默认 `window_cleanup_unknown="keep"` 下安全（`trace.py:509-585`）；若用户配 `transient` 会误关，P1 给 `TraceRecorder` 加"自家窗口 hwnd 忽略集" |
| 系统清理临时目录 | ScreenShot 的图过期 | 过期占位 + 保留元信息，绝不弹错 |
| 隐私（截图含敏感内容） | 泄露到屏幕 | 不落 trace 正文（只有路径）；`Esc` 立即隐藏；`image_window="off"` 一键关；图片驾驶舱不进 `tool_result` 文案 |
| 文案越权 | 模型以为用户已核对 | **禁止**在回执里写"已为你显示/已确认"；`image_receipt` 的「附上不等于看见」原样保留（`images.py:137-157`） |
| 打包缺 `ImageTk` | 冻结版窗口不可用 | `packaging/build_serve.ps1` 冒烟补一条：起 `ImageTk.PhotoImage` 自检；失败只禁用图片驾驶舱并记 diag，不影响主流程 |

## 9. 验收清单

### 9.1 自动化

- `ImageRef.from_dict`：缺 `path` / 非法类型 → `None`，不抛；
- `trace`：`trace_meta["image"]` 为 dict / list / 脏值三种形态的事件条数与字段；
- `CockpitState`：`IMAGE_MAX` 裁剪、换轮清空、`agent_id` 事件不污染主任务；
- `VisualOptions`：三项归一化与钳制、`image_on()` 受总开关约束、`any_enabled()` 计入；
- 纯逻辑：缩略图尺寸计算、LRU 键含 mtime、路径存活判定、布局坐标越界回退；
- `cockpit_window.py` 行数 ≤ 900（既有守卫）；
- 全量 `pytest tests -q` 全绿。

### 9.2 人工走查（Windows + Git Bash）

1. 让模型调 `ScreenShot` → 图片驾驶舱自动出现在 HUD 左侧，大图为刚截的屏，元信息与工具回执里的路径一致；
2. 连截 3 张 → 缩略条 3 张，点击切换，大图与缩略图对应；
3. 双击大图 → `fit / 1:1` 切换；滚轮 → 缩略条横向滚动；
4. 点 `✕` → 只隐藏；下一轮再截图 → 自动重现；
5. Git Bash 切到前台 → 图片驾驶舱与 HUD 一并取消置顶，切走恢复；
6. 手工删掉临时目录里的图 → 大图区显示「图已过期」，元信息仍在，不崩不弹错；
7. `[visualization] image_window = "off"` 重启 → 全程无图片驾驶舱，其余可视化照旧；
8. `CameraShot`、`BrowserScreenshot` 各跑一次，确认落点分别为 `~/.jarvis/cam`、`~/.jarvis/media`。

## 10. 分期

| 期 | 内容 |
|---|---|
| **P0**（本文） | 三来源 `ImageRef` → `image_show` → 图片驾驶舱显示/隐藏/置顶/过期降级 + 三项配置 + 测试 |
| P1 | 失败步联动（补 `operation-visibility` 的「失败截图未做」）、`VisualClick` 匹配矩形叠加在人看的那张图上 |
| P2 | 桌面壳 `<img>` 消费 `image_show`（兄弟仓）；图片驾驶舱"另存到 media" |
| P3 | 终端 half-block 文本降级（无 Tk / 非 TTY 环境） |

## 11. 已确认项（2026-11-11 先生定稿）

| # | 项 | 决定 |
|---|---|---|
| 1 | 图片来源范围 | **只对工具产出的图**自动弹窗；`/paste`、`/image` 属于用户主动入站，不弹 |
| 2 | 窗口位置 | **屏幕左上角 `(24,24)`，可自由移动**（不是左右侧贴边）；位置记忆写 `ui_state.json` 的 `image` 段 |
| 3 | `ScreenShot` 落点 | **不改**，继续落系统临时目录，接受"可能过期"，窗口以「图已过期」占位降级 |
| 4 | P0 是否含 `VisualClick` 矩形叠加 | **不做**，放 P1 |

P0 交付后先看效果，再决定 P1（失败步联动 + 匹配矩形叠加）与 P2（桌面壳消费）。

## 12. 落地记录与偏差（2026-11-11 实做后回填）

| 计划 | 实际 | 原因 |
|---|---|---|
| `image_thumb_px` 默认 220、范围 96–480 | **88**、48–160 | 220px 的缩略图在 480 宽窗口里一行只放得下 2 张，而缩略条高度只有 96；改成「缩略条单张最长边」后数字才成立 |
| 事件派生直接 `**image` 展开 | 改为 `ImageRef.from_dict` 白名单重建 | 测试当场暴露：上游误塞的 base64 会一路落进 JSONL 与事件流 |
| `CockpitState` 只按事件类型收集 | 增加「无 path 不入库」 | 否则窗口会画一张空图（显示成「图已过期」占位） |
| `save_position` 整文件覆盖 | 改**分段读-改-写**（`_write_section`） | 新增 `image` 段后，两个窗口会互相冲掉对方的位置 |
| `TerminalAwareTopmost(root, crawl)` | 新增 `peers=()` | 图片驾驶舱也要跟 Git Bash / cmd 一起让出置顶 |
| 计划估 +1150 行 | 实测 **+1311 行** | `cockpit_image.py` 584 行（拖动/滚轮/命中/缓存/占位/元信息比预估多） |

全量测试 **4970 passed / 2 skipped**；`test_engine_split_guards` 行数门禁通过（894 < 900）。

已知限制（P0 明确不修）：

- 缩略条**不滚动**：超出窗口宽度的缩略图不画（把 `image_max` 调大只会多保留、不会多显示）；
- 位置只在拖动结束时落盘，拖动过程中不节流写盘；
- `ScreenShot` 仍落系统临时目录，被清理后只剩元信息；
- 桌面壳（兄弟仓 `jarvis-desktop`）尚未消费 `image_show`，右栏暂无图片位。

## 13. 三舱视觉规范（2026-11-12）

依据 `posters/jarvis-cyberpunk-design-philosophy.md`（Neon Doctrine）落地，先生指定三色分工。

### 13.1 三色纪律

| 舱 | 主色 | 谱系 | 位置 |
|---|---|---|---|
| 任务驾驶舱 TASK | 电光青 | `#00d4ff` | 屏幕右侧 |
| 爬虫驾驶舱 CRAWL | 品红电流 | `#ff2d78` | 屏幕左侧 |
| 图片驾驶舱 IMAGE | 荧光绿 | `#39ff88` | 屏幕左上（可拖） |

**语义色跨舱统一**（成 `#33f0c0` / 败 `#ff4d6a` / 警告 `#ffb347`），只有主色随舱变——
否则品红舱里的「失败红」会和主色糊成一片，成/败看不出来。品红舱的失败色因此刻意偏橙红。

### 13.2 共享材质（`agent/ui/hud_theme.py`）

| 函数 / 数据 | 作用 |
|---|---|
| `HudTheme` | 一舱一套色板（bg / bg_panel / grid / scan / accent / accent_dim / accent_glow / text / dim / ok / fail / warn） |
| `THEME_TASK` / `THEME_CRAWL` / `THEME_IMAGE` | 三个实例；**任务舱是全局默认**，旧常量名（`CYAN`/`GRID`…）继续指向它，既有子组件（工具卡、代理卡、详情浮层）零改动换新色 |
| `draw_background()` | 深渊黑 + 扫描线（每 4px）+ 网格（每 28px）+ 颗粒（固定种子 96 粒）+ 左右侧刻度 |
| `draw_frame()` | 外框（accent_dim）+ 四角 HUD 括号（accent，臂长 16） |
| `glow_text()` | 先铺 accent_glow 偏移一层再叠亮字；支持 `fill`/`glow` 覆盖，让语义色数字也能发光 |

三舱的背景材质**只有一份实现**：材质若各写一份，颗粒密度与网格间距迟早漂。

### 13.3 改名

「图片窗口」→「图片驾驶舱」：UI 标题 `IMAGE COCKPIT // 图片驾驶舱`，类名
`ImageWindow` → `ImageCockpit`，配置说明与 README/文档同步。

**刻意不动的两处**：配置键仍是 `[visualization] image_window`（改名会破坏用户已有配置）、
文件仍是 `docs/plans/image-window.md`（改名会断已提交的链接与历史）。

### 13.4 预览

```bash
python -m agent.ui.hud_preview     # 三舱一起弹：图片(左上) / 爬虫(左) / 任务(右)
python -m agent.ui.image_demo      # 只看图片驾驶舱
```

### 13.5 取舍（不做的事）

- **不引入 Tektur / Silkscreen 字体文件**：打包体积 + 缺失风险，用 Consolas + 微软雅黑近似；
- **不做 GPU 级辉光**：Tk 只有偏移叠色，接受「够用」而非丝滑；
- **颗粒限 96 粒/舱**：单舱全量重绘 344 个 item 约 10–20ms，再加会让事件密集时卡顿。
