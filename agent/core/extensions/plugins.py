"""插件市场 —— 插件发现、安装、卸载、列表。

v0.1 实现：
- 从 marketplace.json（GitHub 托管）拉取可用插件列表
- git clone 插件仓库，复制 skills 到 ~/.jarvis/skills/
- 合并插件 MCP 配置到 ~/.jarvis/mcp.json
- installed.json 跟踪已安装插件

@author aceFelix
"""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def _default_marketplace_url() -> str:
    return "https://raw.githubusercontent.com/aceFelix/jarvis-plugins/master/marketplace.json"


def _find_git() -> str:
    """查找 git 可执行文件路径。Windows 上会扫描常见安装位置。"""
    import platform

    exe_name = "git.exe" if platform.system() == "Windows" else "git"
    # 先试 PATH
    import shutil as _shutil
    found = _shutil.which(exe_name) or _shutil.which("git")
    if found:
        return found
    if platform.system() == "Windows":
        candidates = [
            r"D:\SoftwareDevelopmentKit\Git\bin\git.exe",
            r"C:\Program Files\Git\bin\git.exe",
            r"C:\Program Files (x86)\Git\bin\git.exe",
        ]
        for c in candidates:
            if Path(c).is_file():
                return c
    return ""


class PluginManager:
    """插件管理器。负责插件的发现、安装、卸载和状态跟踪。"""

    def __init__(
        self,
        marketplace_url: str = "",
        install_base: Path | None = None,
    ) -> None:
        self._marketplace_url = marketplace_url or _default_marketplace_url()
        self._install_base = install_base or (Path.home() / ".jarvis" / "plugins")
        self._skills_base = Path.home() / ".jarvis" / "skills"
        self._installed_path = self._install_base / "installed.json"

    # ---- Marketplace ----

    def fetch_marketplace(self) -> list[dict[str, Any]]:
        """拉取 marketplace.json，返回 plugins 列表。网络错误返回空列表。"""
        try:
            from urllib.request import Request, urlopen

            ctx = ssl._create_unverified_context()
            req = Request(self._marketplace_url, headers={"User-Agent": "jarvis-agent/1.0"})
            with urlopen(req, timeout=15.0, context=ctx) as resp:
                raw = resp.read()
                decoded = raw.decode("utf-8", errors="replace")
                data = json.loads(decoded)
            return data.get("plugins", [])
        except Exception:
            return []

    def search(self, keyword: str = "") -> list[dict[str, Any]]:
        """搜索插件市场。keyword 为空时返回全部。"""
        plugins = self.fetch_marketplace()
        if not keyword:
            return plugins
        kw = keyword.lower()
        return [
            p for p in plugins
            if kw in p.get("name", "").lower()
            or kw in p.get("description", "").lower()
        ]

    # ---- CRUD ----

    def install(self, name: str) -> tuple[bool, str]:
        """安装插件。

        1. 搜索 marketplace 找到插件条目
        2. git clone 插件仓库到临时目录
        3. 定位 plugin.json（支持 github-subdir 子目录）
        4. 复制 skills 到 ~/.jarvis/skills/<name>/
        5. 合并 .mcp.json 到 ~/.jarvis/mcp.json
        6. 更新 installed.json

        Returns:
            (True, "") 成功，(False, "错误信息") 失败。
        """
        # 1. 查市场
        plugins = self.fetch_marketplace()
        entry = None
        for p in plugins:
            if p.get("name") == name:
                entry = p
                break
        if entry is None:
            return False, f"插件 '{name}' 不在市场中。用 /plugin search 查看可用插件。"

        source = entry.get("source", {})
        src_type = source.get("type", "")
        if src_type not in ("github", "github-subdir"):
            return False, f"不支持的插件源类型: {src_type}"

        repo = source.get("repo", "")
        ref = source.get("ref", "master")
        subdir = source.get("subdir", "") if src_type == "github-subdir" else ""
        if not repo:
            return False, "插件条目缺少 repo 信息"

        # 2. git clone
        git_exe = _find_git()
        if not git_exe:
            return False, "未找到 git 命令，请安装 Git 后再试。"
        repo_url = f"https://github.com/{repo}.git"
        tmpdir = tempfile.mkdtemp(prefix="jarvis_plugin_")
        try:
            result = subprocess.run(
                [git_exe, "clone", "--depth", "1", "-b", ref, repo_url, tmpdir],
                capture_output=True, text=True, timeout=60,
                env={**__import__("os").environ, "GIT_SSL_NO_VERIFY": "1"},
            )
            if result.returncode != 0:
                err = result.stderr.strip() or "git clone 失败"
                return False, f"git clone 失败: {err}"
        except FileNotFoundError:
            return False, "未找到 git 命令，请安装 Git 后再试。"
        except subprocess.TimeoutExpired:
            return False, "git clone 超时（>60s），请检查网络。"
        except Exception as e:
            return False, f"git clone 异常: {e}"

        # 3. 定位插件目录
        if subdir:
            plugin_root = Path(tmpdir) / subdir
            if not plugin_root.is_dir():
                return False, f"插件子目录不存在: {subdir}"
        else:
            plugin_root = Path(tmpdir)

        # 4. 解析 plugin.json
        try:
            manifest_path = plugin_root / "plugin.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                return False, "插件缺少 plugin.json 清单文件"
        except json.JSONDecodeError as e:
            return False, f"plugin.json 格式错误: {e}"

        manifest_name = manifest.get("name", name)
        manifest_version = manifest.get("version", "0.0.0")
        manifest_skills: list[str] = manifest.get("skills", [])
        manifest_mcp: list[str] = manifest.get("mcp_servers", [])

        # 5. 复制 skills
        installed_skills: list[str] = []
        src_skills = plugin_root / "skills"
        if src_skills.is_dir():
            self._skills_base.mkdir(parents=True, exist_ok=True)
            for skill_dir_name in manifest_skills:
                src = src_skills / skill_dir_name
                dst = self._skills_base / skill_dir_name
                if src.is_dir():
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    installed_skills.append(skill_dir_name)

        # 6. 合并 MCP 配置
        added_mcp: list[str] = []
        mcp_file = plugin_root / ".mcp.json"
        if mcp_file.exists():
            try:
                mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
                new_servers = mcp_data.get("mcpServers", {})
                from agent.core.extensions.mcp_client import merge_mcp_config
                added_mcp = merge_mcp_config(new_servers)
            except Exception as e:
                return False, f"MCP 配置合并失败: {e}"

        # 7. 写 installed.json
        installed = self._load()
        installed["plugins"][manifest_name] = {
            "name": manifest_name,
            "version": manifest_version,
            "source": repo,
            "installed_at": datetime.now().isoformat(),
            "skills": installed_skills,
            "mcp_servers": added_mcp,
        }
        self._save(installed)

        # 清理临时目录
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

        msg_parts: list[str] = []
        if installed_skills:
            msg_parts.append(f"技能: {', '.join(installed_skills)}")
        if added_mcp:
            msg_parts.append(f"MCP: {', '.join(added_mcp)}")
        msg = "; ".join(msg_parts) if msg_parts else ""
        return True, msg

    def uninstall(self, name: str) -> tuple[bool, str]:
        """卸载插件。删除 skills 目录、移除 MCP 条目、更新 installed.json。"""
        installed = self._load()
        entry = installed.get("plugins", {}).get(name)
        if not entry:
            return False, f"插件 '{name}' 未安装。"

        errors: list[str] = []

        # 删除 skills
        for skill_name in entry.get("skills", []):
            skill_dir = self._skills_base / skill_name
            if skill_dir.exists():
                try:
                    shutil.rmtree(skill_dir)
                except Exception as e:
                    errors.append(f"删除技能 {skill_name} 失败: {e}")

        # 移除 MCP
        mcp_servers = entry.get("mcp_servers", [])
        if mcp_servers:
            try:
                from agent.core.extensions.mcp_client import remove_mcp_config
                remove_mcp_config(mcp_servers)
            except Exception as e:
                errors.append(f"移除 MCP 配置失败: {e}")

        # 更新 installed.json
        del installed["plugins"][name]
        self._save(installed)

        if errors:
            return False, "; ".join(errors)
        return True, ""

    def list_installed(self) -> dict[str, Any]:
        """返回已安装插件字典（installed.json 内容）。"""
        return self._load()

    # ---- 内部 ----

    def _load(self) -> dict[str, Any]:
        try:
            if self._installed_path.exists():
                return json.loads(self._installed_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"plugins": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self._installed_path.parent.mkdir(parents=True, exist_ok=True)
        self._installed_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
