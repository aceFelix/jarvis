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
- 重型可选依赖（opencv/mediapipe/paddleocr/playwright）基础包不含，相应工具运行时
  懒加载失败即优雅降级（serve 本就用 try/except 兜底）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH = 本 spec 所在目录（jarvis/packaging）；ROOT = jarvis 仓库根。
HERE = Path(SPECPATH)
ROOT = HERE.parent

# 全量收集 agent 子模块（工具/命令/处理器多为动态 import）。
hiddenimports = collect_submodules("agent")

# 可选增强：keyring 后端经 entry-points 发现，PyInstaller 常漏；装了才补，未装跳过。
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

# 随包数据：agent/configs 示例与默认规则（保持相对路径，供 Path(__file__) 兜底查找）。
datas = [(str(ROOT / "agent" / "configs"), "agent/configs")]

a = Analysis(
    [str(HERE / "serve_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 排除明确不需要的重型/无关模块，缩小体积、规避误打包。
    # playwright/cv2/mediapipe/paddleocr/onnxruntime 均为可选 extras：serve 的工具
    # 注册对它们 try/except 懒加载，缺席即优雅降级（截屏/浏览器/视觉类工具不可用，
    # 文本对话、文件、命令、MCP 等核心能力不受影响）。剔除可省约 200MB。
    excludes=[
        "tkinter",
        "matplotlib",
        "PyQt5",
        "PySide2",
        "IPython",
        "jupyter",
        "notebook",
        "playwright",
        "cv2",
        "mediapipe",
        "paddleocr",
        "paddle",
        "onnxruntime",
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
