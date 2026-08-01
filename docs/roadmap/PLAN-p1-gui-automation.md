# P1 GUI 自动化增强升级方案

> 优先级：🟠 P1
> 目标：把 Jarvis 的 GUI 操作能力从基础坐标点击升级到「类人交互」——支持拖拽、右键菜单、等待元素出现、多窗口协调、视觉定位（图标/按钮识别点击）以及轻量 RPA 录制回放。
> 参考：ClaudeCode 的 `computer-use`、OpenClaw 的 `gui-tool`、Microsoft Power Automate、pyautogui + opencv 视觉定位方案。

---

## 一、目标与验收标准

### 1.1 目标

1. **拖拽操作**：支持从一个坐标拖拽到另一个坐标，可指定按键、持续时长，覆盖文件移动、滑块、调整窗口大小等场景。
2. **右键菜单**：支持在指定坐标触发右键菜单，并可配合键盘选择菜单项。
3. **等待元素/画面**：在指定区域轮询等待目标画面出现（基于模板匹配或像素变化），超时报错，避免模型盲目重试。
4. **多窗口协调**：支持「在指定窗口内操作」——把窗口坐标系映射到屏幕坐标，AI 可基于窗口内相对坐标点击，不受窗口位置变化影响。
5. **视觉定位**：给定目标图标/按钮的小图，自动在屏幕或指定窗口内找到匹配位置并点击，摆脱对绝对坐标的依赖。
6. **RPA 录制回放（可选）**：新增 `/workflow` 命令，录制一段 GUI 操作序列并保存为 JSON，后续可一键回放。
7. **统一坐标系与坐标转换**：提供窗口相对坐标 → 屏幕绝对坐标的转换工具，降低多窗口/多屏场景下的错误率。
8. **完整文档与测试**：更新 README、架构文档，新增单元测试覆盖核心 GUI 操作。

### 1.2 验收标准

- [ ] `MouseDrag` 工具能从 (x1,y1) 拖拽到 (x2,y2)，支持 duration 和 button。
- [ ] `MouseClick` 支持 `button=right` 已可用，并能在右键后通过 `KeyTap` 选择菜单项。
- [ ] `WaitFor` 工具能基于模板图片在屏幕指定区域等待目标出现，超时返回清晰错误。
- [ ] `WindowFocus` + 窗口相对坐标点击，窗口移动后仍能正确点击内部按钮。
- [ ] `VisualClick` 工具传入小图标后，能自动定位并点击屏幕上的对应按钮。
- [ ] `/workflow record <name>` 和 `/workflow play <name>` 能录制并回放一段操作序列（可选）。
- [ ] 所有新增/修改代码通过 `python -m py_compile` 检查。
- [ ] 新增单元测试覆盖拖拽、右键、等待、视觉定位、窗口坐标转换。
- [ ] 更新 `docs/architecture/*.md` 和 `README.md` 相关章节。

---

## 二、当前状态与差距

### 2.1 已具备能力

| 模块 | 能力 | 文件 |
|---|---|---|
| 鼠标 | 单击、移动、滚动 | `agent/tools/system/mouse.py` |
| 键盘 | 打字、组合键 | `agent/tools/system/keyboard.py` |
| 屏幕 | 全屏/局部截图、分辨率查询、图片回传 LLM | `agent/tools/system/screen.py` |
| 窗口 | 列出、激活、关闭、移动 | `agent/tools/system/window.py` |
| 工具注册 | 可选依赖缺失时静默跳过 | `agent/core/tool.py` |

### 2.2 关键差距

| 差距 | 影响 | 优先级 |
|---|---|---|
| 不支持拖拽 | 无法完成滑块、文件拖拽、调整大小等常见操作 | 高 |
| 右键后无菜单选择辅助 | 右键菜单只能靠坐标点击，脆弱 | 高 |
| 无等待元素出现能力 | 页面/弹窗加载时，模型只能 sleep 后盲猜 | 高 |
| 无视觉定位 | 所有点击依赖绝对坐标，分辨率/布局变化即失效 | 高 |
| 无多窗口相对坐标 | 窗口移动后内部点击容易偏 | 中 |
| 无 RPA 录制回放 | 重复性操作流程无法固化复用 | 低 |

---

## 三、详细设计

### 3.1 新增工具清单

| 工具名 | 所属文件 | 功能 | 依赖 |
|---|---|---|---|
| `MouseDrag` | `mouse.py` | 从一个坐标拖拽到另一个坐标 | pyautogui |
| `WaitFor` | `screen.py` | 等待屏幕/区域出现指定模板或像素变化 | pyautogui + PIL |
| `VisualClick` | `vision_tools.py` 或新增 `gui_vision.py` | 用模板匹配在屏幕上找图标并点击 | pyautogui + PIL |
| `WindowClick` | `window.py` | 在指定窗口的相对坐标内点击 | pyautogui + pygetwindow |
| `WindowRect` | `window.py` | 返回窗口的屏幕绝对坐标与尺寸 | pygetwindow |
| `WorkflowRecord` / `WorkflowPlay` | 新增 `workflow.py` | 录制/回放 GUI 操作序列（可选） | pyautogui + json |

### 3.2 MouseDrag 设计

```python
input_schema = {
    "start_x": int, "start_y": int,
    "end_x": int, "end_y": int,
    "button": {"enum": ["left", "right", "middle"], "default": "left"},
    "duration": {"type": number, "minimum": 0, "maximum": 5, "default": 0.5},
    "easing": {"enum": ["linear", "ease_in_out"], "default": "linear"}
}
```

实现要点：
- 调用 `pyautogui.moveTo(start_x, start_y)` 定位。
- 按下 `pyautogui.mouseDown(button=button)`。
- 移动 `pyautogui.moveTo(end_x, end_y, duration=duration)`。
- 松开 `pyautogui.mouseUp(button=button)`。
- FAILSAFE 保护：全程捕获 `FailSafeException`。
- 权限：ASK，描述拖拽起点/终点。

### 3.3 WaitFor 设计

两种等待模式：
1. **模板匹配**：传入模板图片路径，`WaitFor` 每隔 `interval` 秒在屏幕/区域截图，用 `PIL` 或 `cv2` 模板匹配，找到则返回匹配坐标。
2. **像素变化**：不传模板时，等待指定区域画面发生变化（差异像素超过阈值）。

```python
input_schema = {
    "template_path": {"type": "string", "description": "等待出现的模板图片路径"},
    "region": {"type": "array", "description": "可选：监控区域 [left, top, width, height]"},
    "timeout": {"type": "number", "description": "最长等待秒数（默认 10）", "default": 10},
    "interval": {"type": "number", "description": "轮询间隔秒数（默认 0.5）", "default": 0.5},
    "confidence": {"type": "number", "description": "模板匹配阈值 0-1（默认 0.8）", "default": 0.8},
}
```

实现要点：
- 模板匹配优先用 `pyautogui.locateOnScreen()`（内部已用 Pillow 实现），可选依赖 `opencv-python` 时提升精度。
- 未找到返回明确错误："等待超时，目标未出现"。
- 找到返回中心坐标，供下一步 `MouseClick` 使用。
- 只读操作，自动放行。

### 3.4 VisualClick 设计

基于 `WaitFor` 找到模板位置后自动点击：

```python
input_schema = {
    "template_path": {"type": "string", "description": "要点击的图标/按钮图片路径"},
    "region": {"type": "array", "description": "可选：只在指定区域搜索"},
    "button": {"enum": ["left", "right", "middle"], "default": "left"},
    "clicks": {"type": "integer", "default": 1},
    "confidence": {"type": "number", "default": 0.8},
    "timeout": {"type": "number", "default": 10},
}
```

实现要点：
- 先 `WaitFor` 找位置，再 `MouseClick` 点击中心坐标。
- 找不到时返回错误，提示用户确认图标路径或截图范围。
- 权限：ASK，描述「点击图标 <图片文件名>」。

### 3.5 WindowClick / WindowRect 设计

让 AI 能基于窗口内部相对坐标操作：

```python
# WindowRect
input_schema = {"title": str, "exact": bool}
# 返回 {"left": int, "top": int, "width": int, "height": int}

# WindowClick
input_schema = {
    "title": str, "exact": bool,
    "x": int, "y": int,  # 窗口内相对坐标
    "button": ..., "clicks": ...
}
```

实现要点：
- `WindowRect` 只读，返回窗口绝对坐标。
- `WindowClick` 先把 `(x, y)` 转换为屏幕绝对坐标 `(left+x, top+y)`，再调用 `pyautogui.click`。
- 窗口被最小化时先 `restore()`。

### 3.6 多窗口协调工作流

典型使用顺序：

```text
1. WindowList(filter="Chrome") -> 找到窗口标题
2. WindowFocus(title="Chrome") -> 激活窗口
3. WindowRect(title="Chrome") -> 获取窗口绝对坐标
4. ScreenShot(region=[left, top, width, height]) -> 只看该窗口内容
5. MouseClick / MouseDrag / VisualClick -> 基于绝对/相对坐标操作
```

AI 在 system prompt 中会被提示：操作某应用时，先聚焦窗口，再截图，再基于窗口坐标操作。

### 3.7 RPA 录制回放（可选）

新增 `/workflow` REPL 命令：

```text
/workflow record chrome-login     # 开始录制，记录后续鼠标/键盘/窗口操作
/workflow stop                    # 停止录制并保存
/workflow list                    # 列出已保存工作流
/workflow play chrome-login       # 回放
/workflow delete chrome-login     # 删除
```

录制格式（JSON）：

```json
{
  "name": "chrome-login",
  "steps": [
    {"type": "focus", "title": "Chrome"},
    {"type": "click", "x": 100, "y": 200, "button": "left"},
    {"type": "type", "text": "user@example.com"},
    {"type": "key", "keys": ["enter"]}
  ]
}
```

实现要点：
- 录制器监听键盘/鼠标事件（`pynput` 或 `pyautogui` 钩子）。
- 保存到 `~/.jarvis/workflows/`。
- 回放时按顺序调用对应工具。
- 因涉及第三方库 `pynput` 和复杂事件监听，本次作为可选增强，如果时间紧张可延后。

### 3.8 坐标系约定

- 所有屏幕坐标统一为**主屏幕绝对坐标**，原点在左上角，x 向右，y 向下。
- 窗口内部坐标为**相对坐标**，原点在窗口客户区左上角。
- 工具返回的坐标信息都带单位说明，减少模型误解。

---

## 四、实现计划

### 4.1 第一阶段：核心增强工具（高优先级）

1. 在 `mouse.py` 中新增 `MouseDragTool`。
2. 在 `screen.py` 中新增 `WaitForTool`（模板匹配 + 像素变化）。
3. 在 `window.py` 中新增 `WindowRectTool` 和 `WindowClickTool`。
4. 新增 `agent/tools/system/gui_vision.py`，实现 `VisualClickTool`（基于 WaitFor + MouseClick）。
5. 更新 `agent/core/tool.py` 的 `_register_gui_tools()` 注册新工具。
6. 更新 `agent/tools/__init__.py` 导出。

### 4.2 第二阶段：AI 使用体验优化

1. 在 system prompt 中增加 GUI 操作最佳实践：先聚焦窗口 → 截图 → 再点击/拖拽。
2. 为 `MouseClick` 增加 `relative_to_window` 支持（可选，更简单的 WindowClick 替代方案）。
3. 为 `ScreenShot` 增加 `window` 参数，一键截取指定窗口。

### 4.3 第三阶段：RPA 与文档（可选/后续）

1. 新增 `/workflow` 命令与 `workflow.py` 工具。
2. 编写测试。
3. 更新 `README.md` 与 `docs/architecture/*.md`。
4. 更新 `jarvis-upgrade-roadmap.md` 中 P1 状态。

---

## 五、风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| pyautogui 在高分屏/多显示器上坐标偏移 | 点击位置错误 | 使用视觉定位作为主要手段，坐标作为备选 |
| 模板匹配对界面缩放/主题敏感 | VisualClick 失败 | 提供 confidence 参数，失败时提示用户 |
| 录制回放依赖窗口位置和焦点 | 回放不稳定 | 录制时记录窗口标题，回放前先聚焦并校验 |
| 右键菜单项无统一定位方式 | 右键后选择菜单困难 | 优先用键盘方向键 + Enter 选择，或等待菜单出现后再视觉定位 |
| 引入 opencv-python 增加安装体积 | 可选依赖变重 | VisualClick 优先用 pyautogui 内置匹配，opencv 作为增强可选依赖 |

---

## 六、涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/tools/system/mouse.py` | 新增 `MouseDragTool` |
| `agent/tools/system/screen.py` | 新增 `WaitForTool` |
| `agent/tools/system/window.py` | 新增 `WindowRectTool`、`WindowClickTool` |
| `agent/tools/system/gui_vision.py` | 新增 `VisualClickTool` |
| `agent/core/tool.py` | 注册新 GUI 工具 |
| `agent/tools/__init__.py` | 导出新工具 |
| `agent/main.py` | 可选：新增 `/workflow` 命令 |
| `docs/architecture/*.md` | 更新 GUI 章节 |
| `README.md` | 更新 GUI 操作说明 |
| `docs/roadmap/jarvis-upgrade-roadmap.md` | 更新 P1 状态 |
| `tests/tools/system/*.py` | 新增单元测试 |

---

## 七、结论

P1 GUI 自动化增强的核心是**让 Jarvis 从"看坐标瞎点"进化到"看图标智能点"**。通过拖拽、等待、窗口相对坐标、视觉定位四层能力叠加，可以覆盖 80% 以上的桌面自动化场景；RPA 录制回放作为远期增强，先把基础能力做扎实再引入。
