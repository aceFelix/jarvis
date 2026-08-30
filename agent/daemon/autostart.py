"""开机自启 + 桌面快捷方式辅助脚本。

生成 Windows 快捷方式，双击/开机自动启动 JARVIS 实时语音窗口
（``jarvis --talk`` 独立 pywebview 窗口；三栏 GUI 工作台上线后
此处只需再换启动目标。作者：aceFelix）。

用法:
    python -m agent.daemon.autostart install            # 安装开机自启
    python -m agent.daemon.autostart uninstall          # 卸载开机自启
    python -m agent.daemon.autostart status             # 查看开机自启状态
    python -m agent.daemon.autostart desktop            # 创建桌面快捷方式
    python -m agent.daemon.autostart desktop-uninstall  # 删除桌面快捷方式
    python -m agent.daemon.autostart desktop-status     # 查看桌面快捷方式状态
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _shell_folder(name: str) -> Path | None:
    """从注册表读 Windows Shell Folders 的真实路径。

    优先级（逐层降级）:
    1. 注册表 ``HKCU\\...\\Shell Folders\\<name>``（已展开真实路径，最可靠，
       不受 USERPROFILE/APPDATA 环境变量被沙箱重定向影响）
    2. ``[Environment]::GetFolderPath`` 等价的环境变量推断（USERPROFILE 等）
    3. Path.home() 推断（兜底）

    Args:
        name: Shell Folders 键名，如 "Desktop" / "Startup"

    Returns: 路径，失败返回 None。
    """
    # 1. 注册表（仅 Windows）
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            )
            try:
                value, _ = winreg.QueryValueEx(key, name)
                if value:
                    p = Path(value)
                    if p.exists():
                        return p
            finally:
                winreg.CloseKey(key)
        except Exception:
            pass

    # 2. 环境变量推断
    if name == "Desktop":
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            p = Path(userprofile) / "Desktop"
            if p.exists():
                return p
    elif name == "Startup":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            p = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            if p.exists():
                return p

    # 3. 兜底（不验证存在，调用方自检）
    if name == "Desktop":
        return Path.home() / "Desktop"
    if name == "Startup":
        return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return None


def startup_dir() -> Path:
    """Windows 启动文件夹: %APPDATA%/Microsoft/Windows/Start Menu/Programs/Startup。

    用注册表读真实路径，避免沙箱环境变量重定向。
    """
    return _shell_folder("Startup") or (
        Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    )


def desktop_dir() -> Path:
    """Windows 桌面文件夹。

    用注册表读真实路径（含中文用户名目录、OneDrive 重定向都能正确识别），
    避免 USERPROFILE 环境变量被沙箱重定向导致快捷方式建到假目录。
    """
    return _shell_folder("Desktop") or (Path.home() / "Desktop")


def shortcut_path() -> Path:
    """开机自启快捷方式路径。"""
    return startup_dir() / "JARVIS.lnk"


def desktop_shortcut_path() -> Path:
    """桌面快捷方式路径。"""
    return desktop_dir() / "JARVIS.lnk"


def real_home() -> Path:
    """获取真实用户 home 目录。

    从 desktop_dir() 的父目录推导（桌面通常在 <home>\\Desktop），
    避免沙箱环境 USERPROFILE 被重定向导致 ~/.jarvis/ 建到假目录。
    """
    desk = desktop_dir()
    # 桌面父目录 = 用户 home（覆盖默认/OneDrive 重定向/自定义文档盘等场景）
    parent = desk.parent
    if parent.name and parent.exists():
        return parent
    return Path.home()


def jarvis_home() -> Path:
    """~/.jarvis 目录（存放图标、VBS 启动脚本等资源）。

    用 real_home() 确保在真实用户目录，而非沙箱重定向目录。
    """
    return real_home() / ".jarvis"


def icon_path() -> Path:
    """JARVIS 图标路径（~/.jarvis/jarvis.ico）。"""
    return jarvis_home() / "jarvis.ico"


def vbs_path() -> Path:
    """VBS 启动脚本路径（~/.jarvis/start_jarvis_window.vbs）。

    VBS 用 wscript 静默运行（无控制台），通过 py launcher 自适应找
    Python 3.13，拉起 ``jarvis --talk`` 实时语音窗口。
    （文件名从旧版 start_daemon.vbs 变更，确保存量用户的旧 VBS
    不会被复用检查命中而继续启动已下线的 --daemon。作者：aceFelix）
    """
    return jarvis_home() / "start_jarvis_window.vbs"


def python_exe() -> str:
    """获取当前 Python 解释器路径。"""
    return sys.executable


def pythonw_exe() -> str | None:
    """获取配对的 pythonw.exe 路径（无窗口 Python）。

    Windows 上 pythonw.exe 不弹控制台窗口，适合开机自启/桌面快捷方式场景。
    非 Windows 或找不到时返回 None。
    """
    if sys.platform != "win32":
        return None
    exe = sys.executable
    dirname = os.path.dirname(exe)
    basename = os.path.basename(exe).lower()
    if basename == "pythonw.exe":
        return exe
    if basename == "python.exe":
        pythonw = os.path.join(dirname, "pythonw.exe")
        if os.path.exists(pythonw):
            return pythonw
    return None


def jarvis_script() -> str:
    """获取 jarvis 主模块路径。"""
    # agent/main.py 的父目录
    here = Path(__file__).resolve().parent.parent  # agent/
    return str(here / "main.py")


def ensure_icon() -> Path | None:
    """确保 JARVIS 图标存在，不存在则用 PIL 生成。

    图标设计: 蓝色同心圆反应炉（致敬启动动画），
    256×256 多尺寸 ico（含 256/128/64/48/32/16，Windows 各场景适配）。

    返回图标路径，失败返回 None（调用方降级为无图标快捷方式）。
    """
    ico = icon_path()
    if ico.exists():
        return ico
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        return None

    try:
        jarvis_home().mkdir(parents=True, exist_ok=True)
    except Exception:
        return None

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 外层氛围光晕（径向渐变模糊圆）
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    halo_draw.ellipse([20, 20, size - 20, size - 20], fill=(43, 143, 224, 60))
    halo = halo.filter(ImageFilter.GaussianBlur(radius=18))
    img = Image.alpha_composite(img, halo)
    draw = ImageDraw.Draw(img)

    # 外环（深蓝）
    draw.ellipse([40, 40, size - 40, size - 40], outline=(43, 143, 224), width=6)
    # 中环（亮蓝）
    draw.ellipse([72, 72, size - 72, size - 72], outline=(91, 200, 255), width=4)
    # 内环（淡蓝）
    draw.ellipse([100, 100, size - 100, size - 100], outline=(191, 232, 255), width=3)
    # 核心实心圆（带径向亮度感）
    core = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    core_draw = ImageDraw.Draw(core)
    core_draw.ellipse([108, 108, size - 108, size - 108], fill=(191, 232, 255))
    core = core.filter(ImageFilter.GaussianBlur(radius=4))
    img = Image.alpha_composite(img, core)
    draw = ImageDraw.Draw(img)
    # 核心高亮点
    draw.ellipse([116, 116, size - 116, size - 116], fill=(240, 248, 255))

    # 8 段旋转线圈装饰（外环上的 8 个亮点）
    import math
    cx, cy = size // 2, size // 2
    r_outer = (size - 80) // 2  # 外环半径
    for i in range(8):
        angle = i * math.pi / 4
        px = cx + int(r_outer * math.cos(angle))
        py = cy + int(r_outer * math.sin(angle))
        draw.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(91, 200, 255))

    # 保存为多尺寸 ico
    try:
        img.save(ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception:
        # 某些 PIL 版本 sizes 参数格式不同，降级单尺寸
        try:
            img.save(ico, format="ICO")
        except Exception:
            return None
    return ico


def ensure_vbs() -> Path | None:
    """生成 VBS 启动脚本（无控制台拉起实时语音窗口）。

    VBS 用 ``wscript`` 静默运行（无控制台闪屏），支持两种安装方式:

    1. **模块模式**（优先）: ``pyw -m agent.main --talk``
       适用于 ``pip install jarvis``（PyPI 安装）和 ``pip install -e .``（开发模式）。
       不依赖 main.py 文件路径，agent 包已安装到 site-packages，
       配置从 ``~/.jarvis/settings.toml`` 加载。

    2. **脚本模式**（回退）: ``pyw main.py --talk``
       适用于源码 clone 后直接运行。需要 cwd = 项目根目录，
       否则 ``configs/settings.toml`` 找不到会退化成 mock provider。

    两种模式都先尝试 ``pyw -3.13``，回退 ``pyw`` 和 ``pythonw``。
    项目锁定 Python 3.13.x（pyaudio 无 cp314 wheel）。
    ``--talk`` 以独立 pywebview 窗口运行，窗口关闭后进程退出、
    VBS 随之返回，无残留。

    Returns: VBS 路径，失败返回 None。

    @author aceFelix
    """
    vbs = vbs_path()
    main_py = jarvis_script()
    proj_dir = str(Path(main_py).resolve().parent.parent)
    if vbs.exists():
        existing = vbs.read_text(encoding="ascii", errors="ignore")
        if main_py in existing:
            return vbs

    try:
        jarvis_home().mkdir(parents=True, exist_ok=True)
    except Exception:
        return None

    # VBS 用双引号转义: 路径里的 \ 不需转义，" 用 ""
    # 注释用英文，确保纯 ASCII（VBS 默认 ANSI 编码，中文会乱码）
    #
    # 启动逻辑: 拉起 --talk 实时语音窗口（pywebview GUI，pythonw 无控制台）。
    # 窗口关闭后进程退出，VBS 的 sh.Run(..., True) 等待的是整个语音窗口
    # 生命周期，退出码 0 即正常结束。
    vbs_content = (
        "Option Explicit\n"
        "Dim sh, cmd, mainPy, projDir, rc\n"
        f'mainPy = "{main_py}"\n'
        f'projDir = "{proj_dir}"\n'
        "Set sh = CreateObject(\"WScript.Shell\")\n"
        "\n"
        "' cd to project dir so configs/settings.toml loads (dev mode)\n"
        "sh.CurrentDirectory = projDir\n"
        "\n"
        "' Phase 1: Module mode (pip install) - no main.py path needed\n"
        "' agent package is in site-packages, config from ~/.jarvis/\n"
        "On Error Resume Next\n"
        "rc = sh.Run(\"pyw -3.13 -m agent.main --talk\", 0, True)\n"
        "If Err.Number = 0 And rc = 0 Then WScript.Quit 0\n"
        "Err.Clear\n"
        "rc = sh.Run(\"pyw -m agent.main --talk\", 0, True)\n"
        "If Err.Number = 0 And rc = 0 Then WScript.Quit 0\n"
        "Err.Clear\n"
        "rc = sh.Run(\"pythonw -m agent.main --talk\", 0, True)\n"
        "If Err.Number = 0 And rc = 0 Then WScript.Quit 0\n"
        "Err.Clear\n"
        "\n"
        "' Phase 2: Script mode (dev/clone) - needs main.py path + cwd\n"
        "rc = sh.Run(\"pyw -3.13 \"\"\" & mainPy & \"\"\" --talk\", 0, True)\n"
        "If Err.Number = 0 And rc = 0 Then WScript.Quit 0\n"
        "Err.Clear\n"
        "rc = sh.Run(\"pyw \"\"\" & mainPy & \"\"\" --talk\", 0, True)\n"
        "If Err.Number = 0 And rc = 0 Then WScript.Quit 0\n"
        "Err.Clear\n"
        "rc = sh.Run(\"pythonw \"\"\" & mainPy & \"\"\" --talk\", 0, True)\n"
        "On Error GoTo 0\n"
        "Set sh = Nothing\n"
    )
    try:
        vbs.write_text(vbs_content, encoding="ascii")
        return vbs
    except Exception as e:
        print(f"⚠ VBS 写入失败: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _create_shortcut(
    spath: Path,
    description: str,
    use_vbs: bool = False,
) -> int:
    """创建快捷方式的通用实现。

    Args:
        spath: 快捷方式 .lnk 路径
        description: 快捷方式描述
        use_vbs: True=用 VBS 启动脚本（自适应找 Python，适合桌面双击）；
            False=直接指向 pythonw.exe（适合开机自启，路径在用户真实
            终端执行时自然正确）

    Returns: 0 成功，非 0 失败。
    """
    # 确保图标存在
    ico = ensure_icon()
    icon_str = str(ico).replace("\\", "\\\\") if ico else ""
    icon_line = f'$Shortcut.IconLocation = "{icon_str}"' if icon_str else ""

    if use_vbs:
        # VBS 方案：快捷方式指向 wscript.exe 运行 VBS 脚本（无控制台拉起语音窗口）
        vbs = ensure_vbs()
        if not vbs:
            print("⚠ VBS 启动脚本生成失败，回退到直接指向 pythonw.exe", file=sys.stderr)
            use_vbs = False
        else:
            target = r"C:\Windows\System32\wscript.exe"
            # VBS 路径可能含中文（用户名），PowerShell 双引号字符串能正确处理
            arguments = f'"{vbs}"'
            working_dir = str(jarvis_home())
            # 语音窗口由 pywebview 自管，窗口样式取 7（最小化运行）
            ps_script = f'''
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{spath}")
$Shortcut.TargetPath = "{target}"
$Shortcut.Arguments = '{arguments}'
$Shortcut.WorkingDirectory = "{working_dir}"
$Shortcut.Description = "{description}"
{icon_line}
$Shortcut.WindowStyle = 7
$Shortcut.Save()
'''
            import subprocess
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_script],
                    check=True,
                    capture_output=True,
                )
            except Exception as e:
                print(f"创建快捷方式失败: {e}", file=sys.stderr)
                return 1

            print(f"✓ 已创建快捷方式: {spath}")
            print(f"  目标: {target} {arguments}")
            print(f"  工作目录: {working_dir}")
            if ico:
                print(f"  图标: {ico}")
            return 0

    if not use_vbs:
        # 直接指向 pythonw.exe 方案（适合在用户真实终端执行）
        py = pythonw_exe() or python_exe()
        script = jarvis_script()

        target = py
        arguments = f'"{script}" --talk'
        working_dir = str(Path(script).parent)

        ps_script = f'''
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{spath}")
$Shortcut.TargetPath = "{target}"
$Shortcut.Arguments = '{arguments}'
$Shortcut.WorkingDirectory = "{working_dir}"
$Shortcut.Description = "{description}"
{icon_line}
$Shortcut.Save()
'''
        import subprocess
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                check=True,
                capture_output=True,
            )
        except Exception as e:
            print(f"创建快捷方式失败: {e}", file=sys.stderr)
            print(f"可手动创建: 在 {spath.parent} 放一个指向", file=sys.stderr)
            print(f'  "{py}" "{script}" --talk', file=sys.stderr)
            print("的快捷方式", file=sys.stderr)
            return 1

        print(f"✓ 已创建快捷方式: {spath}")
        print(f"  目标: {target} {arguments}")
        print(f"  工作目录: {working_dir}")
        if ico:
            print(f"  图标: {ico}")
        return 0

    return 1


def install() -> int:
    """安装开机自启（跨平台）。

    Windows: 在 Startup 文件夹创建 .lnk 快捷方式。
    macOS: 创建 LaunchAgent plist 并用 launchctl load 加载。
    Linux: 不支持，提示用户手动配置 systemd user unit。
    """
    if sys.platform == "darwin":
        return _install_macos()
    if sys.platform != "win32":
        print("✗ Linux 暂不支持自动安装开机自启")
        print("  可手动创建 systemd user unit:")
        print("    ~/.config/systemd/user/jarvis-daemon.service")
        print("  然后运行: systemctl --user enable jarvis-daemon.service")
        return 1
    # Windows: 用 VBS 方案启动（无控制台拉起语音窗口），不再直接指向 python.exe
    spath = shortcut_path()
    spath.parent.mkdir(parents=True, exist_ok=True)
    return _create_shortcut(spath, "JARVIS 实时语音工作台（开机自启）", use_vbs=True)


def uninstall() -> int:
    """卸载开机自启（跨平台）。"""
    if sys.platform == "darwin":
        return _uninstall_macos()
    if sys.platform != "win32":
        print("✗ Linux 暂不支持自动卸载开机自启")
        return 0
    # Windows
    spath = shortcut_path()
    if spath.exists():
        spath.unlink()
        print(f"✓ 已卸载开机自启: {spath}")
    else:
        print(f"开机自启快捷方式不存在: {spath}")
    return 0


def status() -> int:
    """查看开机自启状态（跨平台）。"""
    if sys.platform == "darwin":
        return _status_macos()
    if sys.platform != "win32":
        print("✗ Linux 暂不支持查看开机自启状态")
        return 0
    # Windows
    spath = shortcut_path()
    if spath.exists():
        print(f"✓ 已安装: {spath}")
        print(f"  Python: {python_exe()}")
        print(f"  脚本: {jarvis_script()}")
    else:
        print(f"✗ 未安装（{spath} 不存在）")
        print(f"  运行 `python -m agent.daemon.autostart install` 安装")
    return 0


def install_desktop() -> int:
    """创建桌面快捷方式（跨平台）。

    Windows: .lnk 快捷方式，指向 VBS 脚本拉起实时语音窗口（--talk）。
    macOS: 创建 .command 文件，双击在 Terminal.app 中启动语音窗口。
    Linux: 创建 .desktop 文件。
    """
    if sys.platform == "darwin":
        return _install_desktop_macos()
    if sys.platform != "win32":
        return _install_desktop_linux()

    # Windows 原有逻辑
    spath = desktop_shortcut_path()
    # 桌面目录可能不存在（极端环境），mkdir 兜底
    try:
        spath.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    rc = _create_shortcut(spath, "JARVIS 个人 AI 管家", use_vbs=True)
    if rc == 0:
        print()
        print("💡 双击桌面「JARVIS」图标 → 打开实时语音对话窗口")
        # 提示文案与实际行为对齐：--talk 独立窗口，直接说话即可对话；
        # 三栏 GUI 工作台上线后此处入口不变，只换窗口内容（作者：aceFelix）。
        print("   窗口内直接说话即可对话，可打断")
        print("   点「结束」/ESC/说「退下」结束会话，点 X 关闭窗口")
        print("   日志: ~/.jarvis/realtime_window.log")
    return rc


def uninstall_desktop() -> int:
    """删除桌面快捷方式（跨平台）。"""
    if sys.platform == "darwin":
        spath = Path.home() / "Desktop" / "JARVIS.command"
    elif sys.platform != "win32":
        spath = Path.home() / "Desktop" / "JARVIS.desktop"
    else:
        spath = desktop_shortcut_path()
    if spath.exists():
        spath.unlink()
        print(f"✓ 已删除桌面快捷方式: {spath}")
    else:
        print(f"桌面快捷方式不存在: {spath}")
    return 0


def status_desktop() -> int:
    """查看桌面快捷方式状态（跨平台）。"""
    if sys.platform == "darwin":
        spath = Path.home() / "Desktop" / "JARVIS.command"
    elif sys.platform != "win32":
        spath = Path.home() / "Desktop" / "JARVIS.desktop"
    else:
        spath = desktop_shortcut_path()
    if spath.exists():
        print(f"✓ 桌面快捷方式已创建: {spath}")
        print(f"  图标: {icon_path()}")
        print(f"  后台 VBS: {vbs_path()}")
    else:
        print(f"✗ 桌面快捷方式不存在（{spath}）")
        print(f"  运行 `python -m agent.daemon.autostart desktop` 创建")
    return 0


# ---------------------------------------------------------------------------
# macOS 适配：LaunchAgent + .command 桌面文件
# ---------------------------------------------------------------------------

def _macos_plist_path() -> Path:
    """macOS LaunchAgent plist 路径。"""
    return Path.home() / "Library" / "LaunchAgents" / "com.jarvis.daemon.plist"


def _install_macos() -> int:
    """macOS: 创建 LaunchAgent plist 并加载。

    LaunchAgent 在用户登录时自动启动，类似 Windows 的 Startup 快捷方式。
    用 ``launchctl load`` 立即生效，无需重启。
    """
    plist_path = _macos_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    script = jarvis_script()
    py = sys.executable
    log_file = str(Path.home() / ".jarvis" / "daemon.log")
    workdir = str(Path(script).parent)

    # LaunchAgent plist 模板
    # RunAtLoad: 登录后自动启动
    # KeepAlive: 进程退出后自动重启（看门狗效果）
    # StandardOutPath / StandardErrorPath: 日志重定向
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.jarvis.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py}</string>
        <string>{script}</string>
        <string>--talk</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{log_file}</string>
    <key>StandardErrorPath</key>
    <string>{log_file}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONIOENCODING</key>
        <string>utf-8</string>
    </dict>
</dict>
</plist>
"""
    try:
        plist_path.write_text(plist_content, encoding="utf-8")
        # 用 launchctl load 立即加载
        import subprocess
        subprocess.run(
            ["launchctl", "load", str(plist_path)],
            check=False,
            capture_output=True,
        )
        print(f"✓ macOS LaunchAgent 已安装: {plist_path}")
        print(f"  Python: {py}")
        print(f"  脚本: {script}")
        print(f"  日志: {log_file}")
        print("  下次登录系统时自动启动 JARVIS 实时语音窗口")
        return 0
    except Exception as e:
        print(f"✗ 安装 LaunchAgent 失败: {e}", file=sys.stderr)
        print(f"  可手动创建 plist 文件: {plist_path}", file=sys.stderr)
        return 1


def _uninstall_macos() -> int:
    """macOS: 卸载 LaunchAgent。"""
    plist_path = _macos_plist_path()
    if not plist_path.exists():
        print(f"LaunchAgent 未安装: {plist_path}")
        return 0
    try:
        import subprocess
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            check=False,
            capture_output=True,
        )
        plist_path.unlink()
        print(f"✓ 已卸载 LaunchAgent: {plist_path}")
    except Exception as e:
        print(f"卸载失败: {e}", file=sys.stderr)
        # 即使 unload 失败也尝试删除文件
        try:
            plist_path.unlink()
        except Exception:
            pass
    return 0


def _status_macos() -> int:
    """macOS: 查看 LaunchAgent 状态。"""
    plist_path = _macos_plist_path()
    if plist_path.exists():
        print(f"✓ 已安装: {plist_path}")
        print(f"  Python: {sys.executable}")
        print(f"  脚本: {jarvis_script()}")
        # 检查是否已加载
        import subprocess
        result = subprocess.run(
            ["launchctl", "list", "com.jarvis.daemon"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("  状态: 已加载（launchctl）")
        else:
            print("  状态: 未加载（运行 `launchctl load {}` 加载）".format(plist_path))
    else:
        print(f"✗ 未安装（{plist_path} 不存在）")
        print(f"  运行 `python -m agent.daemon.autostart install` 安装")
    return 0


def _install_desktop_macos() -> int:
    """macOS: 创建 .command 桌面文件，双击在 Terminal.app 中启动语音窗口。

    .command 文件是 macOS 特有的可执行脚本文件，双击会用 Terminal.app 打开。
    与 Windows .lnk 不同，它会保留一个终端窗口（语音窗口由 pywebview 弹出）。
    """
    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    spath = desktop / "JARVIS.command"

    script = jarvis_script()
    py = sys.executable
    workdir = str(Path(script).parent)

    # .command 文件内容：cd 到项目目录 → 运行实时语音窗口（--talk）
    content = f"""#!/bin/bash
# JARVIS realtime voice window launcher (macOS .command)
cd "{workdir}" || exit 1
exec "{py}" "{script}" --talk
"""
    try:
        spath.write_text(content, encoding="utf-8")
        # 赋予可执行权限
        spath.chmod(0o755)
        print(f"✓ 已创建桌面快捷方式: {spath}")
        print("💡 双击「JARVIS.command」→ 打开实时语音对话窗口")
        print("   窗口内直接说话即可对话，可打断")
        print("   日志: ~/.jarvis/realtime_window.log")
        return 0
    except Exception as e:
        print(f"✗ 创建桌面快捷方式失败: {e}", file=sys.stderr)
        return 1


def _install_desktop_linux() -> int:
    """Linux: 创建 .desktop 桌面文件。

    双击后在终端中以前台 REPL 模式启动 jarvis（等同 Windows 的 cmd 窗口
    运行 `jarvis`），可打字对话；关窗口即退出。
    """
    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    spath = desktop / "JARVIS.desktop"

    script = jarvis_script()
    py = sys.executable
    workdir = str(Path(script).parent)

    content = f"""[Desktop Entry]
Type=Application
Name=JARVIS
Comment=Just A Rather Very Intelligent System
Exec={py} {script}
Path={workdir}
Terminal=true
Categories=Utility;
"""
    try:
        spath.write_text(content, encoding="utf-8")
        spath.chmod(0o755)
        print(f"✓ 已创建桌面快捷方式: {spath}")
        print("💡 双击「JARVIS.desktop」→ 终端中进入 REPL 对话界面（关窗口即退出）")
        return 0
    except Exception as e:
        print(f"✗ 创建桌面快捷方式失败: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("用法: python -m agent.daemon.autostart <command>")
        print()
        print("命令:")
        print("  install            安装开机自启（Windows: Startup .lnk / macOS: LaunchAgent）")
        print("  uninstall          卸载开机自启")
        print("  status             查看开机自启状态")
        print("  desktop            创建桌面快捷方式（Windows: .lnk / macOS: .command / Linux: .desktop）")
        print("  desktop-uninstall  删除桌面快捷方式")
        print("  desktop-status     查看桌面快捷方式状态")
        print()
        print("注意: Linux 暂不支持自动安装开机自启，可手动创建 systemd user unit。")
        return 0
    cmd = args[0].lower()
    if cmd == "install":
        return install()
    if cmd == "uninstall":
        return uninstall()
    if cmd == "status":
        return status()
    if cmd == "desktop":
        return install_desktop()
    if cmd in ("desktop-uninstall", "uninstall-desktop", "remove-desktop"):
        return uninstall_desktop()
    if cmd in ("desktop-status", "status-desktop"):
        return status_desktop()
    print(f"未知命令: {cmd}", file=sys.stderr)
    print("用法: python -m agent.daemon.autostart <command>", file=sys.stderr)
    print("运行 `python -m agent.daemon.autostart --help` 查看完整命令列表", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
