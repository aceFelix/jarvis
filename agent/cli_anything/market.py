"""CLI-Anything harness 市场管理。

提供已安装 harness 列表、市场可用列表、安装/卸载能力。

支持多市场源（获取顺序）：
1. jarvis自定义市场远程仓库
2. CLI-Anything官方市场远程仓库
3. jarvis自定义市场本地仓库
4. CLI-Anything官方本地仓库

同 id 的 harness，jarvis市场覆盖CLI-Anything官方。

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


def _fetch_remote_registry(base_url: str | None = None, cache_name: str | None = None) -> dict[str, Any] | None:
    """从 GitHub 远程拉取 registry.json 并缓存。

    Args:
        base_url: 远程 raw 前缀。None 时用官方 CLI-Anything 地址。
        cache_name: 缓存文件名。None 时用默认名。

    Returns:
        解析后的 registry dict，失败返回 None。
    """
    raw_base = base_url or _REMOTE_RAW_BASE
    url = f"{raw_base}/registry.json"
    data = _fetch_url(url)
    if data is None:
        return None
    try:
        registry = json.loads(data.decode("utf-8")) or {}
        # 写入缓存
        fname = cache_name or _REGISTRY_CACHE_FILE
        cache = _cache_path(fname)
        cache.write_bytes(data)
        return registry
    except Exception as e:
        logger.warning("解析远程 registry.json 失败: %s", e)
        return None


def _custom_market_local_path(market_local: str) -> Path | None:
    """解析jarvis自定义市场本地路径（支持相对路径）。"""
    if not market_local:
        return None
    p = Path(market_local)
    if p.is_absolute():
        return p if p.is_dir() else None
    # 相对于 jarvis 项目目录
    project_root = Path(__file__).resolve().parent.parent.parent
    resolved = (project_root / p).resolve()
    return resolved if resolved.is_dir() else None


def _load_registry(
    path: Path | None = None,
    *,
    custom_market_url: str = "",
    custom_market_local: str = "",
) -> dict[str, Any]:
    """加载并合并多市场源的 registry。

    加载顺序（同 id 自定义覆盖官方）：
    1. jarvis自定义市场远程 registry.json
    2. CLI-Anything官方远程 registry.json
    3. jarvis自定义市场本地 registry.json
    4. CLI-Anything官方本地 registry.json（含缓存）

    Args:
        path: 可选的本地 registry.json 路径。
        custom_market_url: jarvis自定义市场 GitHub raw 前缀。
        custom_market_local: jarvis自定义市场本地路径。

    Returns:
        合并后的 registry dict。
    """
    official_registry: dict[str, Any] = {}
    custom_registry: dict[str, Any] = {}

    # --- ① jarvis自定义市场远程 ---
    if custom_market_url:
        remote_custom = _fetch_remote_registry(
            base_url=custom_market_url,
            cache_name="custom_registry.json",
        )
        if remote_custom:
            custom_registry = remote_custom

    # --- ② CLI-Anything官方远程 ---
    remote_official = _fetch_remote_registry()
    if remote_official:
        official_registry = remote_official

    # --- ③ jarvis自定义市场本地 ---
    if not custom_registry and custom_market_local:
        custom_local = _custom_market_local_path(custom_market_local)
        if custom_local:
            custom_reg_file = custom_local / "registry.json"
            if custom_reg_file.is_file():
                try:
                    custom_registry = json.loads(custom_reg_file.read_text(encoding="utf-8")) or {}
                except Exception as e:
                    logger.warning("读取jarvis自定义市场本地 registry.json 失败: %s", e)

    # --- ④ CLI-Anything官方本地（含缓存） ---
    if not official_registry:
        cache = _cache_path(_REGISTRY_CACHE_FILE)
        if _is_cache_valid(cache):
            try:
                official_registry = json.loads(cache.read_text(encoding="utf-8")) or {}
            except Exception as e:
                logger.warning("读取缓存 registry.json 失败: %s", e)
        if not official_registry:
            local_path = path or _market_repo() / "registry.json"
            if local_path.is_file():
                try:
                    official_registry = json.loads(local_path.read_text(encoding="utf-8")) or {}
                except Exception as e:
                    logger.warning("读取本地 registry.json 失败: %s", e)

    # --- 合并（自定义覆盖官方） ---
    if not custom_registry:
        return official_registry
    if not official_registry:
        return custom_registry

    # 合并 harnesses/clis 列表，同 id 自定义优先
    merged = dict(official_registry)
    official_clis = official_registry.get("clis", official_registry.get("harnesses", []))
    custom_clis = custom_registry.get("clis", custom_registry.get("harnesses", []))

    # 用自定义的覆盖同 id 的官方条目
    official_ids = set()
    for cli in official_clis:
        cid = _normalize_id(str(cli.get("name", cli.get("id", ""))))
        if cid:
            official_ids.add(cid)

    merged_clis = list(official_clis)
    for cli in custom_clis:
        cid = _normalize_id(str(cli.get("name", cli.get("id", ""))))
        if cid and cid in official_ids:
            # 替换官方同 id 条目
            merged_clis = [c for c in merged_clis if _normalize_id(str(c.get("name", c.get("id", "")))) != cid]
        merged_clis.append(cli)

    # 自定义市场用 "harnesses" 键，官方用 "clis" 键，统一输出为 "clis"
    merged["clis"] = merged_clis
    merged.pop("harnesses", None)
    # 标记自定义来源 id 集合（供 list_market 使用）
    custom_ids = set()
    for cli in custom_clis:
        cid = _normalize_id(str(cli.get("name", cli.get("id", ""))))
        if cid:
            custom_ids.add(cid)
    merged["_custom_ids"] = custom_ids
    merged["_custom_market_url"] = custom_market_url
    merged["_custom_market_local"] = custom_market_local
    return merged


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


def _remote_skill_url(skill_md: str, base_url: str | None = None) -> str:
    """根据 registry 中的 skill_md 路径构造远程 raw URL。

    支持三种形式：
    1. 相对路径，如 ``skills/cli-anything-xxx/SKILL.md`` 或 ``harnesses/qq/SKILL.md``
    2. GitHub raw 地址，直接返回
    3. GitHub blob 页面地址，转换为 raw 地址

    Args:
        skill_md: registry 中记录的 skill_md 路径或 URL。
        base_url: 自定义市场 raw 前缀。None 时用官方地址。
    """
    raw_base = base_url or _REMOTE_RAW_BASE
    # skill_md 可能已经是完整 URL
    if skill_md.startswith("http://") or skill_md.startswith("https://"):
        # 把 GitHub blob 页面转换为 raw 地址
        if "/blob/" in skill_md and skill_md.startswith("https://github.com/"):
            return skill_md.replace("https://github.com/", "https://raw.githubusercontent.com/", 1).replace("/blob/", "/", 1)
        return skill_md
    # 相对路径拼接
    relative = skill_md.lstrip("/")
    if relative.startswith("skills/") or relative.startswith("harnesses/"):
        return f"{raw_base}/{relative}"
    return f"{raw_base}/skills/{relative}"


def _fetch_remote_skill_md(skill_md: str, base_url: str | None = None) -> str | None:
    """从远程下载单个 SKILL.md 内容。

    Args:
        skill_md: registry 中记录的 skill_md 路径或 URL。
        base_url: 自定义市场 raw 前缀。

    Returns:
        SKILL.md 文本内容，失败返回 None。
    """
    url = _remote_skill_url(skill_md, base_url=base_url)
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


def list_market(
    *,
    custom_market_url: str = "",
    custom_market_local: str = "",
) -> list[dict[str, str]]:
    """列出市场可用 harness（合并jarvis自定义 + CLI_Anything官方）。

    ID 统一从 ``skill_md`` 路径或 ``id`` 字段提取。
    输出增加 ``source`` 字段（"jarvis自定义" / "CLI-Anything官方"）区分来源。
    """
    registry = _load_registry(
        custom_market_url=custom_market_url,
        custom_market_local=custom_market_local,
    )
    clis: list[dict[str, Any]] = registry.get("clis", registry.get("harnesses", []))
    custom_ids: set[str] = registry.get("_custom_ids", set())
    installed_ids = {h.id for h in discover_harnesses()}
    result: list[dict[str, str]] = []
    for cli in clis:
        skill_md = str(cli.get(_CLI_SKILL_MD_KEY, ""))
        # 自定义市场 harness 可能没有 cli-anything- 前缀，用 id 字段
        cid = ""
        if cli.get("id"):
            cid = _normalize_id(str(cli["id"]))
        if not cid and skill_md:
            cid = _id_from_skill_md(skill_md)
        if not cid:
            cid = _normalize_id(str(cli.get(_CLI_NAME_KEY, "")))
        if not cid:
            continue
        source = "jarvis自定义" if cid in custom_ids else "CLI_Anything官方"
        result.append({
            "id": cid,
            "name": str(cli.get(_CLI_DISPLAY_KEY, cli.get("display_name", cid))),
            "description": str(cli.get(_CLI_DESC_KEY, "")),
            "requires": str(cli.get(_CLI_REQUIRES_KEY, "")),
            "installed": "是" if cid in installed_ids else "否",
            "source": source,
        })
    return result


def _cli_info_from_registry(cid: str, registry: dict[str, Any]) -> dict[str, Any] | None:
    """从 registry 中查找指定 harness 的元数据。"""
    for cli in registry.get("clis", registry.get("harnesses", [])):
        # 支持 id 字段和 name 字段两种匹配
        if _normalize_id(str(cli.get("id", ""))) == cid:
            return cli
        if _normalize_id(str(cli.get(_CLI_NAME_KEY, ""))) == cid:
            return cli
    return None


def install_harness(
    harness_id: str,
    registry: "ToolRegistry",
    *,
    workdir: str | None = None,
    run_pip: bool = False,
    custom_market_url: str = "",
    custom_market_local: str = "",
) -> dict[str, Any]:
    """安装指定 harness。

    安装顺序：
    1. 从自定义市场远程下载 ``SKILL.md``（优先）。
    2. 从官方 CLI-Anything 远程下载。
    3. 回退自定义市场本地目录。
    4. 回退官方本地 ``CLI-Anything-main/skills/.../SKILL.md``。
    5. 自定义市场 harness → 整目录复制到 ``~/.jarvis/cli_anything/<id>/``；
       官方 harness → 迁移 SKILL.md。
    6. 刷新 ToolRegistry。

    Args:
        harness_id: harness ID（如 ``blender`` 或 ``qq``）。
        registry: 当前 ToolRegistry，安装成功后刷新。
        workdir: 当前工作目录，用于扫描项目级 harness。
        run_pip: 是否自动执行 registry 中的 ``install_cmd``。
        custom_market_url: 自定义市场 GitHub raw 前缀。
        custom_market_local: 自定义市场本地路径。

    Returns:
        dict，包含 success / message / harness_id。
    """
    cid = _normalize_id(harness_id)
    if not cid:
        return {"success": False, "message": "harness ID 为空", "harness_id": ""}

    # 加载合并后的 registry
    registry_data = _load_registry(
        custom_market_url=custom_market_url,
        custom_market_local=custom_market_local,
    )
    cli_info = _cli_info_from_registry(cid, registry_data)
    custom_ids: set[str] = registry_data.get("_custom_ids", set())
    is_custom = cid in custom_ids

    skill_md_relative = ""
    if cli_info:
        skill_md_relative = str(cli_info.get(_CLI_SKILL_MD_KEY, ""))

    target_dir = _DEFAULT_USER_HARNESS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. 尝试远程下载 SKILL.md
    #    顺序：jarvis自定义市场远程 → CLI-Anything官方远程 → 自定义本地 → 官方本地
    source_skill_text: str | None = None
    source_skill_path: Path | None = None

    if skill_md_relative:
        # ① jarvis自定义市场远程
        if custom_market_url:
            source_skill_text = _fetch_remote_skill_md(skill_md_relative, base_url=custom_market_url)
            if source_skill_text:
                logger.info("已从jarvis自定义市场远程下载 harness SKILL.md: %s", cid)

        # ② CLI-Anything官方远程
        if source_skill_text is None:
            source_skill_text = _fetch_remote_skill_md(skill_md_relative)
            if source_skill_text:
                logger.info("已从CLI-Anything官方远程下载 harness SKILL.md: %s", cid)

    # 2. 远程失败则回退本地
    if source_skill_text is None:
        # ③ jarvis自定义市场本地
        if custom_market_local:
            custom_local = _custom_market_local_path(custom_market_local)
            if custom_local and skill_md_relative:
                local_skill = custom_local / skill_md_relative
                if local_skill.is_file():
                    source_skill_path = local_skill

        # ④ CLI-Anything官方本地
        if source_skill_path is None:
            market = _market_repo()
            source_dir = market / "skills" / f"cli-anything-{cid}"
            source_skill_path = source_dir / "SKILL.md"

        if source_skill_path is None or not source_skill_path.is_file():
            return {
                "success": False,
                "message": f"远程与本地均未找到 harness: {cid}（skill_md={skill_md_relative or '无'}）",
                "harness_id": cid,
            }

    # 3. 安装到 ~/.jarvis/cli_anything/<id>/
    #    pip 型自定义 harness（有 install_cmd）：pip install + 复制 SKILL.md
    #    目录型自定义 harness（无 install_cmd）：整目录复制
    #    官方 harness：仅迁移 SKILL.md
    target_harness_dir = target_dir / cid

    # 提前读取 install_cmd
    install_cmd = ""
    if cli_info:
        install_cmd = str(cli_info.get(_CLI_CLI_INSTALL_KEY, "")).strip()

    if is_custom and install_cmd:
        # ── pip 型自定义 harness：pip install + 复制 SKILL.md ──
        # 先执行 pip install，安装全局命令（如 jarvis-harness-wps）
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
            if proc.returncode != 0:
                err_detail = (proc.stderr or proc.stdout or "").strip()
                return {
                    "success": False,
                    "message": f"pip install 失败 (exit={proc.returncode}): {err_detail}",
                    "harness_id": cid,
                }
        except Exception as e:
            return {
                "success": False,
                "message": f"执行 install_cmd 异常: {e}",
                "harness_id": cid,
            }

        # 再复制 SKILL.md（原样保留，不经过 migrate_one 重写）
        target_harness_dir.mkdir(parents=True, exist_ok=True)
        if source_skill_text is not None:
            (target_harness_dir / "SKILL.md").write_text(source_skill_text, encoding="utf-8")
        elif source_skill_path is not None and source_skill_path.is_file():
            shutil.copy2(source_skill_path, target_harness_dir / "SKILL.md")
        else:
            return {
                "success": False,
                "message": f"pip install 成功，但未找到 SKILL.md: {cid}",
                "harness_id": cid,
            }
        ok = True
        logger.info("pip 型 harness 安装完成: %s (cmd=%s)", cid, install_cmd)

    elif is_custom:
        # ── 自定义市场：整目录复制 ──
        # 优先从本地市场目录复制整个 harness 文件夹
        local_harness_dir: Path | None = None
        if custom_market_local and skill_md_relative:
            custom_local = _custom_market_local_path(custom_market_local)
            if custom_local:
                # skill_md_relative 形如 "harnesses/wps/SKILL.md"，取父目录
                candidate = custom_local / Path(skill_md_relative).parent
                if candidate.is_dir():
                    local_harness_dir = candidate

        if local_harness_dir is not None:
            # 整目录复制（排除 __pycache__）
            if target_harness_dir.exists():
                shutil.rmtree(target_harness_dir)
            shutil.copytree(
                local_harness_dir,
                target_harness_dir,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            ok = (target_harness_dir / "SKILL.md").is_file()
            logger.info("已从本地市场复制 harness 目录: %s → %s", local_harness_dir, target_harness_dir)
        elif source_skill_text is not None:
            # 远程下载：直接写入 SKILL.md（不经过 migrate_one 重写）
            target_harness_dir.mkdir(parents=True, exist_ok=True)
            (target_harness_dir / "SKILL.md").write_text(source_skill_text, encoding="utf-8")
            ok = True
            logger.info("已从远程写入 harness SKILL.md: %s", target_harness_dir)
        else:
            ok = False
    else:
        # ── 官方 harness：原有 migrate_one 逻辑 ──
        from agent.cli_anything.migrate import migrate_one

        if source_skill_text is not None:
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
            "message": f"安装 {cid} 失败（迁移 SKILL.md 或复制目录出错）",
            "harness_id": cid,
        }

    # 4. 可选：执行 pip install（仅对非 pip 型自定义 harness 和官方 harness）
    pip_msg = ""
    if not (is_custom and install_cmd):  # pip 型已在步骤 3 执行过
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
    if is_custom and install_cmd:
        msg += "（pip 安装 + SKILL.md）"
    elif is_custom:
        msg += "（自定义市场，整目录复制）"
    elif source_skill_text is not None:
        msg += "（从远程下载）"
    if install_cmd and not run_pip and not (is_custom and install_cmd):
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


# ---------------------------------------------------------------------------
# harness 启用/禁用/创建/校验（与 Plugin 系统分离，各管各的）
# ---------------------------------------------------------------------------

_HARNESS_STATE_FILE = Path.home() / ".jarvis" / "cli_anything" / "disabled.json"


def _load_harness_disabled_state() -> dict[str, Any]:
    """加载 harness 禁用状态。"""
    try:
        if _HARNESS_STATE_FILE.exists():
            return json.loads(_HARNESS_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"disabled": []}


def _save_harness_disabled_state(state: dict[str, Any]) -> None:
    """持久化 harness 禁用状态。"""
    try:
        _HARNESS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HARNESS_STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("保存 harness 禁用状态失败: %s", e)


def enable_harness(harness_id: str) -> dict[str, Any]:
    """启用被禁用的 harness。

    Returns:
        {"success": bool, "message": str}

    @author aceFelix
    """
    cid = _normalize_id(harness_id)
    if not cid:
        return {"success": False, "message": "harness ID 为空"}

    state = _load_harness_disabled_state()
    if cid not in state.get("disabled", []):
        return {"success": True, "message": f"harness '{cid}' 已是启用状态。"}

    state["disabled"] = [x for x in state["disabled"] if x != cid]
    _save_harness_disabled_state(state)
    return {"success": True, "message": f"harness '{cid}' 已启用。重启或 /reset 后生效。"}


def disable_harness(harness_id: str) -> dict[str, Any]:
    """禁用 harness（不卸载，仅停止加载）。

    Returns:
        {"success": bool, "message": str}

    @author aceFelix
    """
    cid = _normalize_id(harness_id)
    if not cid:
        return {"success": False, "message": "harness ID 为空"}

    state = _load_harness_disabled_state()
    if cid in state.get("disabled", []):
        return {"success": True, "message": f"harness '{cid}' 已是禁用状态。"}

    state.setdefault("disabled", []).append(cid)
    _save_harness_disabled_state(state)
    return {"success": True, "message": f"harness '{cid}' 已禁用。重启或 /reset 后生效。"}


def is_harness_disabled(harness_id: str) -> bool:
    """检查 harness 是否被禁用。"""
    cid = _normalize_id(harness_id)
    if not cid:
        return False
    state = _load_harness_disabled_state()
    return cid in state.get("disabled", [])


def list_disabled_harnesses() -> list[str]:
    """返回被禁用的 harness ID 列表。"""
    state = _load_harness_disabled_state()
    return state.get("disabled", [])


def create_harness(
    harness_id: str,
    *,
    display_name: str = "",
    description: str = "",
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """创建 CLI-Anything harness 脚手架。

    Args:
        harness_id: harness ID（小写，用连字符分隔）。
        display_name: 展示名。默认从 harness_id 生成。
        description: 描述。
        output_dir: 输出目录。默认为 ~/.jarvis/cli_anything/<id>/。

    Returns:
        {"success": bool, "message": str, "harness_id": str}

    @author aceFelix
    """
    cid = _normalize_id(harness_id)
    if not cid or not cid.replace("-", "").isalnum():
        return {
            "success": False,
            "message": f"harness ID '{harness_id}' 不合法，只能包含字母、数字和连字符。",
            "harness_id": "",
        }

    if output_dir:
        target = Path(output_dir) / cid
    else:
        target = _DEFAULT_USER_HARNESS_DIR / cid
    if target.exists():
        return {"success": False, "message": f"目录已存在: {target}", "harness_id": cid}
    target.mkdir(parents=True, exist_ok=True)

    disp = display_name or cid.replace("-", " ").title()
    desc = description or f"{disp} harness for Jarvis"
    skill_md = f"""---
name: {disp}
id: {cid}
description: {desc}
when_to_use: {desc}
trigger_words:
  - {cid}
command: cli-anything-{cid}
args:
  - name: subcommand
    type: string
    required: true
    description: 要执行的子命令。参考 CLI-Anything 官方文档。
  - name: json
    type: boolean
    required: false
    default: true
    description: 是否添加 --json 标志输出结构化 JSON。
examples:
  - cli-anything-{cid} --help
---

# {disp} Harness

{desc}

## 使用方式

Jarvis 会把 ``subcommand`` 参数拼接在 ``cli-anything-{cid}`` 后面执行。

例如：

```bash
cli-anything-{cid} --json <subcommand>
```

## 安装

使用前需要先安装对应 harness 包：

```bash
pip install cli-anything-{cid}
```
"""
    (target / "SKILL.md").write_text(skill_md, encoding="utf-8")

    readme = f"""# {disp} Harness

{desc}

## 使用方式

在 Jarvis 中直接使用 `{cid}` 工具，或通过命令行执行：

```bash
cli-anything-{cid} --help
```

## 开发

编辑 `SKILL.md` 修改 harness 配置。重启 Jarvis 或执行 `/reset` 后生效。
"""
    (target / "README.md").write_text(readme, encoding="utf-8")

    return {"success": True, "message": str(target), "harness_id": cid}


def validate_harness(path: str | Path) -> tuple[bool, list[str]]:
    """校验 SKILL.md 是否合法。

    Args:
        path: harness 目录或 SKILL.md 文件路径。

    Returns:
        (True, []) 合法，(False, ["错误1", ...]) 不合法。

    @author aceFelix
    """
    p = Path(path)
    if p.is_dir():
        skill_md = p / "SKILL.md"
        if not skill_md.exists():
            return False, [f"目录中未找到 SKILL.md: {p}"]
        p = skill_md
    elif not p.is_file():
        return False, [f"路径不存在: {path}"]
    elif p.name != "SKILL.md":
        return False, [f"只支持 SKILL.md 文件: {p.name}"]

    errors: list[str] = []
    try:
        from agent.cli_anything.loader import parse_skill_md
        parse_skill_md(p)
    except ValueError as e:
        errors.append(str(e))
    except Exception as e:
        errors.append(f"解析失败: {e}")

    return (len(errors) == 0), errors
