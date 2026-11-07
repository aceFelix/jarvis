# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包规格：冻结 jarvis serve 后端为独立可执行 jarvis-serve。

产物：dist/jarvis-serve/ 目录（onedir），内含 jarvis-serve.exe + _internal/。
桌面壳（jarvis-desktop）经 electron-builder 的 extraResources 把整个目录随
NSIS 安装包分发，打包态 spawn 该 exe 取代 `python -m agent.serve`。

设计要点（@author aceFelix）：
- onedir 而非 onefile：启动更快、数据文件路径可预测、规避 onefile 解包到临时目录
  触发杀软误报；NSIS 打包整个目录对用户仍是「一个安装包」。
- console=True：serve 靠 stdout 打单行握手 JSON，必须是控制台子系统（Electron
  spawn 时 windowsHide 隐藏黑框）。
- collect_submodules('agent')：agent 大量工具/命令为动态 import，需全量收集子模块。
- datas 仅带 agent/configs（示例模板 + permissions.yaml 默认规则）；绝不带 repo 根
  configs/（那是开发者实盘配置，可能含密钥），用户配置首启生成于 ~/.jarvis/。
- **全能力打包（2026-11-07）**：桌面安装包要求“jarvis 有的能力桌面也得有”，
  构建环境用 `pip install -e ".[all]"` 装入 gui/browser/camera/vision/voice/
  mcp/daemon/wechat 八组 extras；本 spec 用 `_collect_optional()` 对其中含数据文件
  或动态导入的包做 collect_all，不再把它们排除。playwright 浏览器内核不在这里
  （Python 包不含内核），由 build_serve.ps1 另下载到 dist/jarvis-serve/ms-playwright，
  运行时 serve_entry 指向该目录。
- 构建机没装某个 extras 时 `_collect_optional()` 静默跳过，保证本地最小构建仍可产出；
  CI 由 `jarvis-serve.exe --caps` 门禁断言八组能力真的在包里。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH = 本 spec 所在目录（jarvis/packaging）；ROOT = jarvis 仓库根。
HERE = Path(SPECPATH)
ROOT = HERE.parent

# 全量收集 agent 子模块（工具/命令/处理器多为动态 import）。
hiddenimports = collect_submodules("agent")
datas = [(str(ROOT / "agent" / "configs"), "agent/configs")]
binaries = []


def _collect_optional(pkg: str) -> None:
    """尽量收集一个可选依赖的数据文件、二进制与隐藏导入。

    构建机没装该 extras 时跳过（本地最小构建仍可用）；装了则把包内 data
    （playwright driver、mediapipe 模型、paddleocr 配置、aec/pyaudio 的原生库）
    一并带上，避免“能 import 但运行时缺文件”。
    """
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    except Exception:
        return
    datas.extend(pkg_datas)
    binaries.extend(pkg_binaries)
    hiddenimports.extend(pkg_hidden)


# 八组 extras 中“含数据/动态导入”的包逐个收集；未安装会自动跳过。
for _pkg in (
    "playwright",          # browser：driver/node + package
    "cv2",                 # camera / vision：OpenCV
    "mediapipe",           # vision：模型资源
    "paddleocr", "paddle", # vision：OCR 与推理库
    "pyautogui", "pyscreeze", "pymsgbox", "pytweening",  # gui：输入/截图链
    "PIL",                 # gui / vision
    "pygetwindow", "pywinauto", "pywin32",               # gui：窗口管理
    "dashscope",           # voice：TTS/ASR SDK
    "pyaudio",             # voice：PortAudio 绑定
    "aec_audio_processing", "numpy",  # voice：AEC 与重采样
    "pypinyin",            # voice：唤醒词
    "mcp",                 # mcp
    "aiohttp", "qrcode",   # wechat：ClawBot + 二维码
    "keyring",             # secrets
    "psutil",              # daemon：系统监控
):
    _collect_optional(_pkg)

# 可选增强：keyring 后端经 entry-points 发现，PyInstaller 常漏；装了才补。
for _extra in (
    ["keyring", "keyring.backends.Windows", "keyring.backends.chainer"],
    ["websockets", "websockets.legacy", "websockets.legacy.server"],
):
    for _mod in _extra:
        try:
            __import__(_mod)
            hiddenimports.append(_mod)
        except Exception:
            pass

a = Analysis(
    [str(HERE / "serve_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 仅排除与本能力集无关的 GUI 框架/科学计算后端，缩小体积；八组 extras 内的
    # 包一律不排除（否则会重演“开发态能跑、打包态没有”）。
    excludes=[
        "tkinter",
        "matplotlib",
        "PyQt5",
        "PySide2",
        "IPython",
        "jupyter",
        "notebook",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jarvis-serve",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 需要 stdout 打握手 JSON
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="jarvis-serve",
)
