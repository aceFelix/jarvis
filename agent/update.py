"""jarvis 本体版本自检与升级（Phase 1：检查 + 提示 + 一键升级）。

职责：
1. 查询 PyPI 上 ``jarvis-agent`` 的最新发布版本（纯 HTTP，失败静默返回 None）。
2. 用 ``~/.jarvis/update_check.json`` 做 24 小时节流，避免每次启动都打网络。
3. 识别当前安装渠道（pip / uv tool / editable / 未安装），给出对应的升级命令。
4. 提供启动时的后台非阻塞检查，以及 ``/update`` 命令用的一键升级。

设计取舍：
- **只检查不自动改文件**：Windows 上运行中的进程镜像会被锁定，无法在进程内
  直接替换自身；自动套用（退出后由子进程安装）留待 Phase 2。本阶段只做
  "检出 → 提示 → 用户确认后升级（重启生效）"，风险最低。
- 网络异常、离线、超时一律静默降级，绝不阻塞或刷屏。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# PyPI 包名与元数据地址（与 pyproject.toml 的 project.name 保持一致）
_PACKAGE = "jarvis-agent"
_PYPI_JSON_URL = f"https://pypi.org/pypi/{_PACKAGE}/json"
# 网络请求超时（秒）：启动时后台执行，短超时防止长时间挂起
_HTTP_TIMEOUT_SECONDS = 1.5
# 检查节流窗口：24 小时内不重复请求 PyPI
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
# 一键升级的子进程超时（秒）
_UPGRADE_TIMEOUT_SECONDS = 600


def current_version() -> str:
    """获取**当前正在运行**的 jarvis 版本。

    优先 ``agent.__version__``（实际被执行的代码里写的那个），元数据只当回退。

    为什么要把顺序倒过来（2026-11-03 实机）：旧实现先读 `importlib.metadata.version()`，
    而 editable 安装（`pip install -e`）下那份 dist-info 停在装包那天，于是启动横幅出现
    「发现新版本 2.3.4（当前 2.2.2）」——用户跑的代码其实已经是 2.3.4，那个“当前版本”
    是个假数字，还会把用户推向 `/update`（对 editable 环境有害，见 `start_background_check`）。
    正常 pip 安装下两处本来就要求一致（发版时三处双写 + 版本一致测试），所以优先读
    代码内版本对普适安装也没有影响。

    @return 版本字符串，最坏情况返回 "0.0.0"
    """
    try:
        from agent import __version__ as running_version
    except ImportError:
        # 极早期 / 异常打包下 `agent.__version__` 可能取不到，交给下面的元数据回退
        running_version = ""
    if running_version:
        return running_version
    try:
        from importlib.metadata import version

        return version(_PACKAGE)
    except Exception:
        return "0.0.0"


def _dist_available() -> bool:
    """判断 ``jarvis-agent`` 是否以包形式安装（而非直接跑源码）。"""
    try:
        from importlib.metadata import distribution

        distribution(_PACKAGE)
        return True
    except Exception:
        return False


def latest_version(timeout: float = _HTTP_TIMEOUT_SECONDS) -> str | None:
    """查询 PyPI 上 ``jarvis-agent`` 的最新版本。

    任何网络/解析异常（离线、超时、代理、JSON 异常）都返回 None，不抛出。

    @param timeout: 请求超时秒数
    @return 最新版本字符串；不可用时为 None
    """
    try:
        req = urllib.request.Request(
            _PYPI_JSON_URL,
            headers={"User-Agent": f"jarvis/{current_version()}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = data.get("info", {}).get("version")
        return str(latest) if latest else None
    except Exception:
        return None


def _version_key(ver: str) -> tuple:
    """把版本号解析成可比较的元组（宽松，非严格 PEP 440）。

    数字段按整数比较，预发布后缀（如 rc1）保留，保证 ``2.1.10 > 2.1.9``。

    @param ver: 版本字符串
    @return 可比较元组
    """
    parts: list[tuple[int, int, str]] = []
    for seg in re.split(r"[.\-+]", ver):
        if not seg:
            continue
        m = re.match(r"^(\d+)(.*)$", seg)
        if m:
            parts.append((0, int(m.group(1)), m.group(2)))
        else:
            parts.append((1, 0, seg))
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """判断 latest 是否比 current 更新。

    优先使用 packaging 实现严格 PEP 440 比较；不可用时回退到宽松比较。

    @param latest: 最新版本
    @param current: 当前版本
    @return True 表示存在更新
    """
    try:
        from packaging.version import parse as _parse

        return _parse(latest) > _parse(current)
    except Exception:
        try:
            return _version_key(latest) > _version_key(current)
        except Exception:
            return False


def _cache_path() -> Path:
    """节流缓存文件路径：``~/.jarvis/update_check.json``。"""
    return Path.home() / ".jarvis" / "update_check.json"


def _read_cache() -> dict:
    """读取节流缓存；不存在或损坏时返回空字典。"""
    try:
        with _cache_path().open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_cache(latest: str) -> None:
    """写入节流缓存（记录检查时间戳与最新版本）。失败静默。"""
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "latest": latest}, f)
    except Exception:
        pass


def check_for_update(force: bool = False) -> tuple[bool, str, str]:
    """综合节流缓存判断是否有新版本。

    @param force: True 时忽略节流，强制联网查询
    @return (是否有新版本, 当前版本, 最新版本)
    """
    cur = current_version()
    cache = _read_cache()
    latest: str | None = None

    fresh = cache.get("latest") and (time.time() - float(cache.get("ts", 0)) < _CHECK_INTERVAL_SECONDS)
    if fresh and not force:
        latest = str(cache["latest"])
    else:
        latest = latest_version()
        if latest:
            _write_cache(latest)
        else:
            # 离线：回退用上次缓存的最新版本，仍可提示（不写入新时间戳）
            cached_latest = cache.get("latest")
            latest = str(cached_latest) if cached_latest else None

    if not latest:
        return False, cur, cur
    return is_newer(latest, cur), cur, latest


def _is_editable_install() -> bool:
    """判断是否为 editable 安装（``pip install -e .`` 开发模式）。

    通过包元数据的 direct_url.json 中的 dir_info.editable 标记识别，
    开发模式绝不能被自动升级覆盖。

    @return True 表示 editable 安装
    """
    try:
        from importlib.metadata import distribution

        raw = distribution(_PACKAGE).read_text("direct_url.json")
        if raw:
            return bool(json.loads(raw).get("dir_info", {}).get("editable"))
    except Exception:
        pass
    return False


def detect_installer() -> str:
    """识别当前安装渠道。

    @return "pip" / "uv" / "editable" / "unknown"
    """
    if not _dist_available():
        return "unknown"  # 直接跑源码，未作为包安装
    if _is_editable_install():
        return "editable"
    # uv tool 的隔离 venv 位于 .../uv/tools/<pkg>
    prefix = str(Path(sys.prefix)).replace("\\", "/").lower()
    if "/uv/tools/" in prefix:
        return "uv"
    return "pip"


def upgrade_command() -> list[str] | None:
    """返回当前渠道下的一键升级命令。

    注意：``pip install -U jarvis-agent`` 只升级主包本身，已安装的 extras
    依赖不会被卸载（功能保留）；新版本为某个 extras 新增的依赖不会被自动拉取。

    @return 升级命令列表；不支持自动升级（editable/unknown）时为 None
    """
    installer = detect_installer()
    if installer == "uv":
        return ["uv", "tool", "upgrade", _PACKAGE]
    if installer == "pip":
        return [sys.executable, "-m", "pip", "install", "-U", _PACKAGE]
    return None


def run_upgrade() -> tuple[bool, str]:
    """执行一键升级（阻塞，供 /update 命令调用）。

    @return (是否成功, 子进程输出)
    """
    cmd = upgrade_command()
    if cmd is None:
        installer = detect_installer()
        if installer == "editable":
            return False, "当前为开发模式安装（pip install -e .），已跳过自动升级，请手动 git pull。"
        return False, "无法识别安装方式（可能未作为包安装），请手动升级。"
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_UPGRADE_TIMEOUT_SECONDS,
        )
    except Exception as e:  # 超时 / 找不到可执行文件等
        return False, f"升级命令执行失败：{e}"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, output


def format_command(cmd: list[str]) -> str:
    """把命令列表拼成可直接粘贴执行的命令行（含空格的路径加引号）。

    Windows venv 下 `sys.executable` 常带空格（如 `Program Files`），直接 join 拼出来的
    命令用户粘回去执行不了。

    @author aceFelix
    """
    return " ".join(f'"{part}"' if " " in part else part for part in cmd)


def start_background_check(ui) -> None:
    """启动时后台检查新版本并提示（非阻塞）。

    在守护线程里完成网络查询，检出更新时通过 ``ui.info`` 打印一行提示。
    任何异常都吞掉，绝不影响启动。

    @param ui: 具备 ``info(text)`` 方法的 UI 对象（通常是 RichCLI）
    """
    def _run() -> None:
        try:
            has_update, cur, latest = check_for_update()
            if not has_update:
                return
            # 文案必须按安装渠道给：以前这里硬编码「运行 /update，或手动执行
            # pip install -U」，可 editable（开发模式）下 /update 会被 run_upgrade 拒绝，
            # 而用户真去手动执行 `pip install -U` 更糟——它把 editable 安装换成 PyPI
            # 正式版，**直接覆盖掉源码树里尚未发布的修改**。渠道判断系统里本来就有
            # （`upgrade_command` 对 editable/unknown 返回 None），只是这条提示路径
            # 没去用它。（2026-11-03）@author aceFelix
            cmd = upgrade_command()
            if cmd is None:
                ui.info(
                    f"发现新版本 {latest}（当前 {cur}）。当前是开发模式（源码）环境，"
                    f"自动升级会覆盖本地代码，请用 git pull 更新。"
                )
                return
            ui.info(
                f"发现新版本 {latest}（当前 {cur}）。"
                f"运行 /update 升级，或手动执行：{format_command(cmd)}。"
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="jarvis-update-check").start()
