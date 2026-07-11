"""tkinter 会话选择弹窗。

/load 无参数时弹出，支持 ↑↓ Enter Esc 选择已保存的会话。
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime


def pick_session(
    sessions: list[dict],
    *,
    title: str = "J.A.R.V.I.S - 加载会话",
) -> int | None:
    """弹出会话选择窗口。

    sessions: list of {name, message_count, model, created_at, updated_at, ...}

    - ↑↓ 移动 / Enter 确认 / Esc 取消
    - 双击直接确认

    返回选中的 sessions 索引，取消返回 None。
    """
    if not sessions:
        return None

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)

    # --- 暗色主题 ---
    BG = "#1a1a2e"
    FG = "#e0e0e0"
    SEL_BG = "#1c5a96"
    SEL_FG = "#ffffff"
    ACCENT = "#5bc8ff"
    DIM = "#a0a0b0"

    root.configure(bg=BG)

    item_height = 32
    header_height = 36
    button_height = 40
    visible_count = min(len(sessions), 10)
    window_width = 560
    window_height = header_height + visible_count * item_height + button_height + 24

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - window_width) // 2
    y = (screen_h - window_height) // 2
    root.geometry(f"{window_width}x{window_height}+{x}+{y}")

    result_idx: int | None = None

    # --- 标题栏 ---
    header = tk.Frame(root, bg=BG, height=header_height)
    header.pack(fill=tk.X, padx=12, pady=(8, 0))
    header.pack_propagate(False)

    tk.Label(
        header, text="◆  加载会话", fg=ACCENT, bg=BG,
        font=("Microsoft YaHei", 12, "bold"),
    ).pack(side=tk.LEFT)

    tk.Label(
        header, text="↑↓ 移动  Enter 确认  Esc 取消", fg=DIM, bg=BG,
        font=("Microsoft YaHei", 9),
    ).pack(side=tk.RIGHT)

    # --- 列表 ---
    list_frame = tk.Frame(root, bg=BG)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

    selected_idx = [0]
    row_frames: list[tk.Frame] = []

    def _format_time(ts: float) -> str:
        try:
            return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        except Exception:
            return "----"

    def _render_item(i: int, s: dict) -> tk.Frame:
        is_selected = i == selected_idx[0]
        bg = SEL_BG if is_selected else BG

        row = tk.Frame(list_frame, bg=bg, height=item_height, cursor="hand2")
        row.pack_propagate(False)

        # 选中指示
        prefix = "›" if is_selected else " "
        tk.Label(
            row, text=prefix,
            fg=SEL_FG if is_selected else BG,
            bg=bg, font=("Consolas", 11), width=2,
        ).pack(side=tk.LEFT, padx=(8, 0))

        # 会话名
        name = s.get("name", "?")
        tk.Label(
            row, text=name,
            fg=SEL_FG if is_selected else FG,
            bg=bg, font=("Microsoft YaHei", 10, "bold"), width=22, anchor="w",
        ).pack(side=tk.LEFT, padx=(6, 0))

        # 消息数
        count = s.get("message_count", 0)
        tk.Label(
            row, text=f"{count}条",
            fg=SEL_FG if is_selected else DIM,
            bg=bg, font=("Microsoft YaHei", 9), width=4, anchor="e",
        ).pack(side=tk.LEFT)

        # 时间
        ts = s.get("updated_at") or s.get("created_at", 0)
        tk.Label(
            row, text=_format_time(ts),
            fg=SEL_FG if is_selected else DIM,
            bg=bg, font=("Consolas", 9), width=11,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        # 点击事件
        def _on_click(idx=i):
            nonlocal result_idx
            result_idx = idx
            root.destroy()

        def _hover_enter(_e, r=row, idx=i):
            if idx == selected_idx[0]:
                return
            r.configure(bg="#1a2a3a")

        def _hover_leave(_e, r=row, idx=i):
            if idx == selected_idx[0]:
                return
            r.configure(bg=BG)

        for child in row.winfo_children():
            child.bind("<Button-1>", lambda e, idx=i: _on_click(idx))
            child.bind("<Enter>", _hover_enter)
            child.bind("<Leave>", _hover_leave)
        row.bind("<Button-1>", lambda e, idx=i: _on_click(idx))
        row.bind("<Enter>", _hover_enter)
        row.bind("<Leave>", _hover_leave)

        return row

    def _refresh():
        for f in row_frames:
            f.destroy()
        row_frames.clear()
        for i, s in enumerate(sessions):
            f = _render_item(i, s)
            f.pack(fill=tk.X)
            row_frames.append(f)

    _refresh()

    # --- 按键 ---
    def _up(_e=None):
        selected_idx[0] = (selected_idx[0] - 1) % len(sessions)
        _refresh()

    def _down(_e=None):
        selected_idx[0] = (selected_idx[0] + 1) % len(sessions)
        _refresh()

    def _confirm(_e=None):
        nonlocal result_idx
        result_idx = selected_idx[0]
        root.destroy()

    def _cancel(_e=None):
        nonlocal result_idx
        result_idx = None
        root.destroy()

    root.bind("<Up>", _up)
    root.bind("<Down>", _down)
    root.bind("<Return>", _confirm)
    root.bind("<Escape>", _cancel)
    root.bind("<Double-Button-1>", _confirm)

    # --- 底部按钮 ---
    btn_frame = tk.Frame(root, bg=BG, height=button_height)
    btn_frame.pack(fill=tk.X, padx=12, pady=(4, 8))

    tk.Button(
        btn_frame, text="取消 (Esc)", command=_cancel,
        bg="#21262d", fg=DIM,
        activebackground="#30363d", activeforeground=FG,
        relief=tk.FLAT, bd=0, padx=16, pady=4,
        font=("Microsoft YaHei", 9),
    ).pack(side=tk.RIGHT, padx=(6, 0))

    tk.Button(
        btn_frame, text="确认 (Enter)", command=_confirm,
        bg=ACCENT, fg="#0d1117",
        activebackground="#7dd8ff", activeforeground="#0d1117",
        relief=tk.FLAT, bd=0, padx=16, pady=4,
        font=("Microsoft YaHei", 9, "bold"),
    ).pack(side=tk.RIGHT)

    root.focus_force()
    root.lift()
    root.attributes("-topmost", True)

    root.mainloop()
    return result_idx
