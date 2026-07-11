"""tkinter 模型选择弹窗。

跳出 asyncio/prompt_toolkit 的事件循环冲突，
用一个独立 GUI 窗口实现模型列表的键盘/鼠标选择。
"""

from __future__ import annotations

import tkinter as tk


def pick_model(
    models: dict[str, str],
    current: str,
    *,
    title: str = "J.A.R.V.I.S - 选择模型",
) -> str | None:
    """弹出模型选择窗口。

    - ↑↓ 移动 / Enter 确认 / Esc 取消
    - 双击直接确认
    - 当前模型加粗标注

    返回选中的模型名，取消返回 None。
    """
    if not models:
        return None

    items = list(models.items())  # [(name, desc), ...]

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.iconbitmap(default="")  # 不设图标，走系统默认

    # --- 暗色主题配色 ---
    BG = "#1a1a2e"        # 主背景（比纯黑稍亮，避免和文字粘连）
    FG = "#e0e0e0"        # 主文字（亮白灰）
    SEL_BG = "#1c5a96"    # 选中背景（JARVIS 蓝）
    SEL_FG = "#ffffff"    # 选中文字
    ACCENT = "#5bc8ff"    # 亮蓝强调
    DIM = "#a0a0b0"       # 次要文字（比之前亮，确保可见）
    CURRENT_BG = "#16213e"  # 当前模型背景（深蓝灰）

    root.configure(bg=BG)

    # --- 尺寸计算 ---
    item_height = 30
    header_height = 36
    button_height = 40
    # 最多展示 10 个，超出的 tkinter 自然溢出（暂无滚动，后续加）
    list_height = min(len(items), 10) * item_height
    window_width = 500
    window_height = header_height + list_height + button_height + 24

    # 居中
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - window_width) // 2
    y = (screen_h - window_height) // 2
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")

    result: str | None = None

    # --- 标题栏 ---
    header = tk.Frame(root, bg=BG, height=header_height)
    header.pack(fill=tk.X, padx=12, pady=(8, 0))
    header.pack_propagate(False)

    tk.Label(
        header,
        text="◆  选择模型",
        fg=ACCENT,
        bg=BG,
        font=("Microsoft YaHei", 12, "bold"),
    ).pack(side=tk.LEFT)

    tk.Label(
        header,
        text="↑↓ 移动  Enter 确认  Esc 取消",
        fg=DIM,
        bg=BG,
        font=("Microsoft YaHei", 9),
    ).pack(side=tk.RIGHT)

    # --- 列表框架（不用canvas/scrollbar，5个模型不需要滚动）---
    list_frame = tk.Frame(root, bg=BG)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

    # --- 模型列表项 ---
    selected_idx = [0]
    row_frames: list[tk.Frame] = []

    # 找当前模型索引
    for i, (name, _) in enumerate(items):
        if name == current:
            selected_idx[0] = i
            break

    def _render_item(i: int, name: str, desc: str) -> tk.Frame:
        is_current = name == current
        is_selected = i == selected_idx[0]

        # 背景色
        if is_current and is_selected:
            bg = SEL_BG
        elif is_current:
            bg = CURRENT_BG
        elif is_selected:
            bg = SEL_BG
        else:
            bg = BG

        row = tk.Frame(list_frame, bg=bg, height=item_height, cursor="hand2")
        row.pack_propagate(False)

        # 选中指示
        prefix = "●" if is_current else ("›" if is_selected else " ")
        prefix_fg = ACCENT if is_current else (SEL_FG if is_selected else BG)

        tk.Label(
            row,
            text=prefix,
            fg=prefix_fg,
            bg=bg,
            font=("Consolas", 11),
            width=2,
        ).pack(side=tk.LEFT, padx=(8, 0))

        # 模型名
        name_font = ("Microsoft YaHei", 10,
                     "bold" if is_current else "normal")
        tk.Label(
            row,
            text=name,
            fg=SEL_FG if is_selected else ACCENT if is_current else FG,
            bg=bg,
            font=name_font,
            width=20,
            anchor="w",
        ).pack(side=tk.LEFT)

        # 描述
        tk.Label(
            row,
            text=desc,
            fg=SEL_FG if is_selected else DIM,
            bg=bg,
            font=("Microsoft YaHei", 9),
            anchor="w",
        ).pack(side=tk.LEFT, padx=(4, 8))

        # 当前标记
        if is_current:
            tk.Label(
                row,
                text="当前",
                fg=ACCENT,
                bg=bg,
                font=("Microsoft YaHei", 9, "italic"),
            ).pack(side=tk.RIGHT, padx=(0, 8))

        # 点击事件
        def _on_click(idx=i):
            nonlocal result
            result = items[idx][0]
            root.destroy()

        def _on_hover_enter(_e, r=row, idx=i):
            if idx == selected_idx[0]:
                return
            r.configure(bg="#1a2a3a")

        def _on_hover_leave(_e, r=row, idx=i):
            if idx == selected_idx[0]:
                return
            is_cur = items[idx][0] == current
            r.configure(bg=CURRENT_BG if is_cur else BG)

        for child in row.winfo_children():
            child.bind("<Button-1>", lambda e, idx=i: _on_click(idx))
            child.bind("<Enter>", _on_hover_enter)
            child.bind("<Leave>", _on_hover_leave)

        row.bind("<Button-1>", lambda e, idx=i: _on_click(idx))
        row.bind("<Enter>", _on_hover_enter)
        row.bind("<Leave>", _on_hover_leave)

        return row

    def _refresh() -> None:
        """重建所有行。"""
        for f in row_frames:
            f.destroy()
        row_frames.clear()
        for i, (n, d) in enumerate(items):
            f = _render_item(i, n, d)
            f.pack(fill=tk.X)
            row_frames.append(f)

    _refresh()

    # --- 按键绑定 ---
    def _move_up(_e=None) -> None:
        selected_idx[0] = (selected_idx[0] - 1) % len(items)
        _refresh()

    def _move_down(_e=None) -> None:
        selected_idx[0] = (selected_idx[0] + 1) % len(items)
        _refresh()

    def _confirm(_e=None) -> None:
        nonlocal result
        result = items[selected_idx[0]][0]
        root.destroy()

    def _cancel(_e=None) -> None:
        nonlocal result
        result = None
        root.destroy()

    root.bind("<Up>", _move_up)
    root.bind("<Down>", _move_down)
    root.bind("<Return>", _confirm)
    root.bind("<Escape>", _cancel)
    root.bind("<Double-Button-1>", _confirm)

    # --- 底部按钮 ---
    btn_frame = tk.Frame(root, bg=BG, height=button_height)
    btn_frame.pack(fill=tk.X, padx=12, pady=(4, 8))

    cancel_btn = tk.Button(
        btn_frame,
        text="取消 (Esc)",
        command=_cancel,
        bg="#21262d",
        fg=DIM,
        activebackground="#30363d",
        activeforeground=FG,
        relief=tk.FLAT,
        bd=0,
        padx=16,
        pady=4,
        font=("Microsoft YaHei", 9),
    )
    cancel_btn.pack(side=tk.RIGHT, padx=(6, 0))

    ok_btn = tk.Button(
        btn_frame,
        text="确认 (Enter)",
        command=_confirm,
        bg=ACCENT,
        fg="#0d1117",
        activebackground="#7dd8ff",
        activeforeground="#0d1117",
        relief=tk.FLAT,
        bd=0,
        padx=16,
        pady=4,
        font=("Microsoft YaHei", 9, "bold"),
    )
    ok_btn.pack(side=tk.RIGHT)

    # 聚焦，确保键盘输入生效
    root.focus_force()
    root.lift()
    root.attributes("-topmost", True)

    root.mainloop()
    return result


# 简单测试
if __name__ == "__main__":
    test_models = {
        "qwen3.7-plus": "通义千问 3.7 Plus（默认，带视觉）",
        "qwen3.6-plus": "通义千问 3.6 Plus",
        "qwen3.6-flash": "通义千问 3.6 Flash（快）",
        "qwen3.5-plus": "通义千问 3.5 Plus",
        "qwen3.5-flash": "通义千问 3.5 Flash（快）",
        "qwen3.0-plus": "通义千问 3.0 Plus",
        "qwen3.0-flash": "通义千问 3.0 Flash（快）",
        "deepseek-chat": "DeepSeek V3",
        "deepseek-reasoner": "DeepSeek R1 推理",
    }
    r = pick_model(test_models, "qwen3.7-plus")
    print(f"Selected: {r}")
