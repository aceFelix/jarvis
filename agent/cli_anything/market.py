"""CLI-Anything harness 市场管理。

提供已安装 harness 列表、市场可用列表、安装/卸载能力。

市场数据优先从 CLI-Anything 官方 GitHub 仓库远程读取，
网络不可用或本地仓库更新时，会回退到本地 ``CLI-Anything-main/`` 目录。
安装 harness 时同样优先从远程下载 ``SKILL.md``，失败再尝试本地仓库。

@author aceFelix
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agent.cli_anything.loader import _DEFAULT_USER_HARNESS_DIR, discover_harnesses
from agent.cli_anything.registry import discover_and_register

logger = logging.getLogger(__name__)

# CLI-Anything 官方 GitHub 仓库 raw 地址前缀
_REMOTE_RAW_BASE = "https://raw.githubusercontent.com/HKUDS/CLI-Anything/main"

# CLI-Anything 官方仓库默认路径（与 jarvis 项目同级）
_DEFAULT_MARKET_REPO = Path(__file__).resolve().parent.parent.parent.parent / "CLI-Anything-main"

# 缓存目录（~/.jarvis/cache/cli_anything/）
_CACHE_DIR = Path.home() / ".jarvis" / "cache" / "cli_anything"

# registry.json 缓存文件名
_REGISTRY_CACHE_FILE = "registry.json"

# 缓存有效期（秒），默认 1 小时
_CACHE_TTL_SECONDS = 3600

# 远程请求超时（秒）
_REMOTE_TIMEOUT_SECONDS = 15

# registry.json 中 cli 对象的关键字段
_CLI_NAME_KEY = "name"
_CLI_DISPLAY_KEY = "display_name"
_CLI_DESC_KEY = "description"
_CLI_REQUIRES_KEY = "requires"
_CLI_CLI_INSTALL_KEY = "install_cmd"
_CLI_SKILL_MD_KEY = "skill_md"


def _market_repo() -> Path:
    """返回本地 CLI-Anything 市场仓库路径。"""
    return _DEFAULT_MARKET_REPO


def _cache_path(filename: str) -> Path:
    """返回缓存文件路径。"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / filename


def _is_cache_valid(path: Path, ttl: float = _CACHE_TTL_SECONDS) -> bool:
    """检查缓存文件是否存在且未过期。"""
    if not path.is_file():
        return False
    try:
        return time.time() - path.stat().st_mtime < ttl
    except Exception:
        return False


def _fetch_url(url: str, timeout: float = _REMOTE_TIMEOUT_SECONDS) -> bytes | None:
    """从远程 URL 获取内容。

    Args:
        url: 远程地址。
        timeout: 超时时间（秒）。

    Returns:
        响应内容（bytes），失败返回 None。
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if resp.status != 200:
                logger.warning("远程请求失败 %s: status=%s", url, resp.status)
                return None
            return resp.read()
    except urllib.error.HTTPError as e:
        logger.warning("远程请求 HTTP 错误 %s: %s", url, e.code)
        return None
    except urllib.error.URLError as e:
        logger.warning("远程请求 URL 错误 %s: %s", url, e.reason)
        return None
    except TimeoutError:
        logger.warning("远程请求超时 %s", url)
        return None
    except Exception as e:
        logger.warning("远程请求异常 %s: %s", url, e)
        return None


def _fetch_remote_registry() -> dict[str, Any] | None:
    """从 GitHub 远程拉取 registry.json 并缓存。

    Returns:
        解析后的 registry dict，失败返回 None。
    """
    url = f"{_REMOTE_RAW_BASE}/registry.json"
    data = _fetch_url(url)
    if data is None:
        return None
    try:
        registry = json.loads(data.decode("utf-8")) or {}
        # 写入缓存
        cache = _cache_path(_REGISTRY_CACHE_FILE)
        cache.write_bytes(data)
        return registry
    except Exception as e:
        logger.warning("解析远程 registry.json 失败: %s", e)
        return None


def _load_registry(path: Path | None = None) -> dict[str, Any]:
    """加载 CLI-Anything registry.json。

    加载顺序：
    1. 远程 GitHub registry.json（优先，成功后缓存）
    2. 本地有效缓存
    3. 本地 CLI-Anything-main/registry.json

    Args:
        path: 可选的本地 registry.json 路径。

    Returns:
        registry dict，全部失败返回空 dict。
    """
    # 1. 优先远程
    remote_registry = _fetch_remote_registry()
    if remote_registry:
        return remote_registry

    # 2. 回退缓存
    cache = _cache_path(_REGISTRY_CACHE_FILE)
    if _is_cache_valid(cache):
        try:
            return json.loads(cache.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("读取缓存 registry.json 失败: %s", e)

    # 3. 回退本地仓库
    local_path = path or _market_repo() / "registry.json"
    if local_path.is_file():
        try:
            return json.loads(local_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("读取本地 registry.json 失败: %s", e)
    return {}


def _normalize_id(value: str) -> str:
    """把用户输入的 id 归一化（去除 cli-anything- 前缀，小写）。"""
    v = value.strip().lower()
    if v.startswith("cli-anything-"):
        return v[len("cli-anything-"):]
    return v


def _id_from_skill_md(skill_md: str) -> str:
    """从 ``skills/cli-anything-<id>/SKILL.md`` 或 URL 中提取 harness id。"""
    import re

    try:
        # skill_md 通常是 "skills/cli-anything-ccswitch/SKILL.md"
        # 也可能是远程 URL，如 "https://.../cli-anything-zotero/.../SKILL.md"
        parts = Path(skill_md).parts
        dir_name = parts[-2] if len(parts) >= 2 else ""
        cid = _normalize_id(dir_name)
        if cid and cid != "skills":
            return cid
        # fallback：从路径/URL 中匹配 cli-anything-<id>
        match = re.search(r"cli-anything-([^/\\]+)", skill_md)
        if match:
            return _normalize_id(match.group(1))
        return ""
    except Exception:
        return ""


def _remote_skill_url(skill_md: str) -> str:
    """根据 registry 中的 skill_md 路径构造远程 raw URL。

    支持三种形式：
    1. 相对路径，如 ``skills/cli-anything-xxx/SKILL.md``
    2. GitHub raw 地址，直接返回
    3. GitHub blob 页面地址，转换为 raw 地址
    """
    # skill_md 可能已经是完整 URL
    if skill_md.startswith("http://") or skill_md.startswith("https://"):
        # 把 GitHub blob 页面转换为 raw 地址
        # https://github.com/<user>/<repo>/blob/<branch>/<path>
        # -> https://raw.githubusercontent.com/<user>/<repo>/<branch>/<path>
        if "/blob/" in skill_md and skill_md.startswith("https://github.com/"):
            return skill_md.replace("https://github.com/", "https://raw.githubusercontent.com/", 1).replace("/blob/", "/", 1)
        return skill_md
    # 去掉可能的前导 skills/，统一拼接
    relative = skill_md.lstrip("/")
    if relative.startswith("skills/"):
        return f"{_REMOTE_RAW_BASE}/{relative}"
    return f"{_REMOTE_RAW_BASE}/skills/{relative}"


def _fetch_remote_skill_md(skill_md: str) -> str | None:
    """从远程下载单个 SKILL.md 内容。

    Args:
        skill_md: registry 中记录的 skill_md 路径或 URL。

    Returns:
        SKILL.md 文本内容，失败返回 None。
    """
    url = _remote_skill_url(skill_md)
    data = _fetch_url(url)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except Exception as e:
        logger.warning("解码远程 SKILL.md 失败 %s: %s", url, e)
        return None


def list_installed(workdir: str | None = None) -> list[dict[str, str]]:
    """列出已安装的 harness。"""
    return [
        {"id": h.id, "name": h.name, "description": h.description}
        for h in discover_harnesses(workdir=workdir)
    ]


def list_market() -> list[dict[str, str]]:
    """列出市场可用 harness（优先远程 registry.json）。

    ID 统一从 ``skill_md`` 路径中提取，确保与已安装 harness 的 id 一致。
    """
    registry = _load_registry()
    clis: list[dict[str, Any]] = registry.get("clis", [])
    installed_ids = {h.id for h in discover_harnesses()}
    result: list[dict[str, str]] = []
    for cli in clis:
        skill_md = str(cli.get(_CLI_SKILL_MD_KEY, ""))
        if not skill_md or "cli-anything-" not in skill_md:
            # 只列出标准 CLI-Anything harness（skill_md 路径含 cli-anything-）
            continue
        cid = _id_from_skill_md(skill_md)
        if not cid:
            cid = _normalize_id(str(cli.get(_CLI_NAME_KEY, "")))
        if not cid:
            continue
        result.append({
            "id": cid,
            "name": str(cli.get(_CLI_DISPLAY_KEY, cid)),
            "description": str(cli.get(_CLI_DESC_KEY, "")),
            "requires": str(cli.get(_CLI_REQUIRES_KEY, "")),
            "installed": "是" if cid in installed_ids else "否",
        })
    return result


def _cli_info_from_registry(cid: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    """从 registry 中查找指定 harness 的元数据。"""
    for cli in registry.get("clis", []):
        if _normalize_id(str(cli.get(_CLI_NAME_KEY, ""))) == cid:
            return cli
    return None


def install_harness(
    harness_id: str,
    registry: "ToolRegistry",
    *,
    workdir: str | None = None,
    run_pip: bool = False,
) -> dict[str, Any]:
    """安装指定 harness。

    安装顺序：
    1. 从远程 GitHub raw 下载 ``SKILL.md``。
    2. 远程失败时回退到本地 ``CLI-Anything-main/skills/.../SKILL.md``。
    3. 迁移 SKILL.md 到 ``~/.jarvis/cli_anything/<id>/``。
    4. 刷新 ToolRegistry。

    Args:
        harness_id: harness ID（如 ``blender``）。
        registry: 当前 ToolRegistry，安装成功后刷新。
        workdir: 当前工作目录，用于扫描项目级 harness。
        run_pip: 是否自动执行 registry 中的 ``install_cmd``。

    Returns:
        dict，包含 success / message / harness_id。
    """
    cid = _normalize_id(harness_id)
    if not cid:
        return {"success": False, "message": "harness ID 为空", "harness_id": ""}

    # 加载 registry（会触发远程优先 + 缓存 + 本地回退）
    registry_data = _load_registry()
    cli_info = _cli_info_from_registry(cid, registry_data)

    skill_md_relative = ""
    if cli_info:
        skill_md_relative = str(cli_info.get(_CLI_SKILL_MD_KEY, ""))

    from agent.cli_anything.migrate import migrate_one

    target_dir = _DEFAULT_USER_HARNESS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. 尝试远程下载 SKILL.md
    source_skill_text: str | None = None
    source_skill_path: Path | None = None
    if skill_md_relative:
        source_skill_text = _fetch_remote_skill_md(skill_md_relative)
        if source_skill_text:
            logger.info("已从远程下载 harness SKILL.md: %s", cid)

    # 2. 远程失败则回退本地仓库
    if source_skill_text is None:
        market = _market_repo()
        source_dir = market / "skills" / f"cli-anything-{cid}"
        source_skill_path = source_dir / "SKILL.md"
        if not source_skill_path.is_file():
            return {
                "success": False,
                "message": f"远程与本地均未找到 harness: {cid}（skill_md={skill_md_relative or '无'}）",
                "harness_id": cid,
            }

    # 3. 迁移：远程下载的内容直接写到临时文件再迁移；本地则直接迁移原文件
    if source_skill_text is not None:
        # 创建临时 SKILL.md 供 migrate_one 读取
        tmp_dir = target_dir / f"{cid}.tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_skill = tmp_dir / "SKILL.md"
        tmp_skill.write_text(source_skill_text, encoding="utf-8")
        try:
            _, ok = migrate_one(tmp_skill, target_dir, harness_id=cid)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        _, ok = migrate_one(source_skill_path, target_dir)  # type: ignore[arg-type]

    if not ok:
        return {
            "success": False,
            "message": f"迁移 {cid} SKILL.md 失败",
            "harness_id": cid,
        }

    # 4. 可选：执行 pip install
    pip_msg = ""
    install_cmd = ""
    if cli_info:
        install_cmd = str(cli_info.get(_CLI_CLI_INSTALL_KEY, "")).strip()

    if run_pip and install_cmd:
        try:
            proc = subprocess.run(
                install_cmd,
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            pip_msg = f"\n安装命令退出码: {proc.returncode}"
            if proc.stdout:
                pip_msg += f"\n{proc.stdout.strip()}"
            if proc.stderr:
                pip_msg += f"\n{proc.stderr.strip()}"
            if proc.returncode != 0:
                return {
                    "success": False,
                    "message": f"迁移成功，但 install 命令执行失败{pip_msg}",
                    "harness_id": cid,
                }
        except Exception as e:
            pip_msg = f"\n执行 install 命令异常: {e}"

    # 5. 刷新 registry
    discover_and_register(registry, workdir=workdir)

    msg = f"已安装 harness: {cid}"
    if source_skill_text is not None:
        msg += "（从远程下载）"
    if install_cmd and not run_pip:
        msg += f"\n如需使用，请手动执行安装命令:\n  {install_cmd}"
    msg += pip_msg
    return {"success": True, "message": msg, "harness_id": cid}


def uninstall_harness(
    harness_id: str,
    registry: "ToolRegistry",
    *,
    workdir: str | None = None,
) -> dict[str, Any]:
    """卸载指定 harness（删除 ~/.jarvis/cli_anything/<id> 目录）。"""
    cid = _normalize_id(harness_id)
    if not cid:
        return {"success": False, "message": "harness ID 为空", "harness_id": ""}

    harness_dir = _DEFAULT_USER_HARNESS_DIR / cid
    if not harness_dir.exists():
        return {
            "success": False,
            "message": f"未找到已安装的 harness: {cid}",
            "harness_id": cid,
        }

    try:
        shutil.rmtree(harness_dir)
    except Exception as e:
        return {
            "success": False,
            "message": f"删除 {harness_dir} 失败: {e}",
            "harness_id": cid,
        }

    # 刷新 registry：当前 registry 中仍保留旧工具，但重启后会消失。
    # 这里尝试重新注册以反映删除，但 ToolRegistry 不支持注销，所以仅提示。
    discover_and_register(registry, workdir=workdir)
    return {
        "success": True,
        "message": f"已卸载 harness: {cid}（重启后生效）",
        "harness_id": cid,
    }
