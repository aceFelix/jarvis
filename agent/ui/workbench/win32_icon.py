"""Win32 窗口图标设置：绕过 Form.Icon，直接对 hwnd 操作。

背景（用户实测多轮仍白板/发白）：工作台是 WS_EX_LAYERED 分层透明窗口，
pywebview 构造时设置的 Form.Icon 在任务栏/Alt-Tab 不稳定（回退成 pythonw.exe
的白板图标）；WM_SETICON / GCL 均无效——Windows 10 任务栏真正按
AppUserModelID 显示图标，故追加进程 AUMID + 注册表图标映射。

@author aceFelix
"""

from __future__ import annotations

import ctypes
import os

_WM_SETICON = 0x0080
_ICON_BIG = 1      # 任务栏 / Alt-Tab 大图标
_ICON_SMALL = 0    # 标题栏小图标（无边框窗口不可见，但保持惯例一并设置）
_IMAGE_ICON = 1
_LR_LOADFROMFILE = 0x0010

# 窗口类图标索引
_GCL_HICON = -14
_GCL_HICONSM = -34

# 工作台的 AppUserModelID：任务栏按此 ID 分组并显示注册表中指定的图标。
APP_USER_MODEL_ID = "AceFelix.JARVIS.Workbench"


def set_current_process_app_user_model_id(aumid: str = APP_USER_MODEL_ID) -> bool:
    """为当前进程设置 AppUserModelID（Win10 任务栏按 AUMID 分组/显示图标）。

    窗口必须和快捷方式/注册表使用同一 AUMID，任务栏才会把窗口归到同一组
    并使用注册表中映射的图标，而不是 pythonw.exe 的白板图标。
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
        return True
    except Exception:
        return False


def register_app_icon(aumid: str, ico_path: str, display_name: str = "J.A.R.V.I.S 工作台") -> bool:
    """在注册表 HKCU 下注册 AppUserModelID 的图标和显示名。

    路径：`HKCU\Software\Classes\AppUserModelID\{aumid}`
    - Icon 键指向 ico 文件（含图标索引 0），任务栏/Alt-Tab 据此显示图标。
    - DisplayName 键用于任务栏提示/跳转列表标题。

    写入 HKCU 不需要管理员权限；失败只返回 False，不影响窗口启动。
    """
    try:
        import winreg
        key_path = f"Software\\Classes\\AppUserModelID\\{aumid}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, f"{ico_path},0")
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, display_name)
        return True
    except Exception:
        return False


def _load_icon(ico_path: str, cx: int = 0, cy: int = 0):
    """LoadImageW 加载 ico，返回 hIcon（0 表示失败）。"""
    try:
        return int(ctypes.windll.user32.LoadImageW(None, ico_path, _IMAGE_ICON, cx, cy, _LR_LOADFROMFILE))
    except Exception:
        return 0


def set_window_icon(hwnd: int, ico_path: str) -> bool:
    """给窗口句柄设置任务栏/Alt-Tab 图标（大 + 小两档 WM_SETICON）。

    任一档加载失败不影响另一档；成功设置至少一档返回 True。
    旧图标句柄由系统接管，无需手动释放。
    """
    if not hwnd or not ico_path or not os.path.isfile(ico_path):
        return False
    try:
        ok = False
        for wparam, size in ((_ICON_BIG, 32), (_ICON_SMALL, 16)):
            hicon = _load_icon(ico_path, size, size)
            if hicon:
                ctypes.windll.user32.SendMessageW(hwnd, _WM_SETICON, wparam, hicon)
                ok = True
        return ok
    except Exception:
        return False


def set_window_class_icon(hwnd: int, ico_path: str) -> bool:
    """设置窗口类图标（GCL_HICON / GCL_HICONSM）。

    任务栏/Alt-Tab 在 WM_SETICON 无效时会 fallback 到窗口类图标，
    对无边框分层窗口尤其重要。
    """
    if not hwnd or not ico_path or not os.path.isfile(ico_path):
        return False
    try:
        ok = False
        hicons: list[tuple[int, int]] = [
            (_GCL_HICON, _load_icon(ico_path, 0, 0)),  # 默认尺寸（通常 32）
            (_GCL_HICONSM, _load_icon(ico_path, 16, 16)),
        ]
        for gcl, hicon in hicons:
            if not hicon:
                continue
            ctypes.windll.user32.SetClassLongPtrW(hwnd, gcl, hicon)
            ok = True
        return ok
    except Exception:
        return False
