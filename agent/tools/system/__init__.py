"""系统与 GUI 操作工具。"""

import platform


def macos_permission_hint(operation: str = "GUI 操作") -> str:
    """macOS 上 GUI 操作失败时返回权限提示。

    macOS 对 GUI 自动化有严格的隐私权限控制，
    未授权时 pyautogui 会静默失败或报错。
    此函数返回用户可读的权限配置指引。
    """
    if platform.system() != "Darwin":
        return ""
    return (
        f"\n\n[macOS 权限提示] {operation}需要授权：\n"
        "- 鼠标/键盘控制：系统设置 → 隐私与安全性 → 辅助功能 → 勾选终端应用\n"
        "- 屏幕截图：系统设置 → 隐私与安全性 → 屏幕录制 → 勾选终端应用\n"
        "授权后需重启终端生效。"
    )
