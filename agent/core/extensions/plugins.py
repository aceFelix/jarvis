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
import logging
import shutil
import ssl
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


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
        marketplace_local: str = "",
        install_base: Path | None = None,
    ) -> None:
        self._marketplace_url = marketplace_url or _default_marketplace_url()
        self._marketplace_local = marketplace_local
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
        """搜索插件市场。keyword 为空时返回全部。

        合并远程 marketplace.json 和本地插件市场目录。
        本地市场优先：如果本地插件与远程同名，以本地为准。
        """
        plugins = self.fetch_marketplace()
        local_plugins = self._load_local_marketplace()

        # 远程 + 本地合并，本地覆盖远程同名插件
        seen: set[str] = set()
        merged: list[dict[str, Any]] = []
        for p in plugins + local_plugins:
            name = p.get("name", "")
            if not name or name in seen:
                if name in seen:
                    # 本地覆盖远程：移除旧的，追加新的
                    merged = [x for x in merged if x.get("name") != name]
                else:
                    continue
            merged.append(p)
            seen.add(name)

        if not keyword:
            return merged
        kw = keyword.lower()
        return [
            p for p in merged
            if kw in p.get("name", "").lower()
            or kw in p.get("description", "").lower()
        ]

    def _load_local_marketplace(self) -> list[dict[str, Any]]:
        """扫描本地插件市场目录。

        支持两种目录布局：
            1) 扁平布局：<marketplace_local>/<plugin>/plugin.json
            2) 仓库布局：<marketplace_local>/plugins/<plugin>/plugin.json
               （与 aceFelix/jarvis-plugins 仓库结构一致）

        同时会读取本地 <marketplace_local>/marketplace.json，
        把其中 subdir 指向本地 plugins/ 下的 github-subdir 条目映射为本地源，
        方便直接测试远程 marketplace.json 而无需修改 source。

        如果同一个插件既有 plugin.json 又出现在 marketplace.json 中，
        以 plugin.json 为准（可本地直接安装）。

        @author aceFelix
        """
        if not self._marketplace_local:
            return []
        root = Path(self._marketplace_local)
        if not root.is_dir():
            return []

        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 1) 扫描本地 plugin.json（优先）
        # 同时扫描根目录和 plugins/ 子目录
        search_roots = [root]
        plugins_dir = root / "plugins"
        if plugins_dir.is_dir():
            search_roots.append(plugins_dir)

        for search_root in search_roots:
            for subdir in sorted(search_root.iterdir()):
                if not subdir.is_dir():
                    continue
                manifest_path = subdir / "plugin.json"
                if not manifest_path.is_file():
                    continue
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    name = manifest.get("name", subdir.name)
                    if not name:
                        continue
                    manifest["_is_local"] = True
                    manifest["_local_path"] = str(subdir)
                    # 如果本地插件没有 source，默认标记为 local
                    if not manifest.get("source"):
                        manifest["source"] = {"type": "local", "path": str(subdir)}
                    results.append(manifest)
                    seen.add(name)
                except Exception as e:
                    logger.warning("读取本地插件 %s 失败: %s", manifest_path, e)

        # 2) 读取本地 marketplace.json（补充）
        market_file = root / "marketplace.json"
        if market_file.is_file():
            try:
                market_data = json.loads(market_file.read_text(encoding="utf-8"))
                for entry in market_data.get("plugins", []):
                    name = entry.get("name", "")
                    if not name or name in seen:
                        continue
                    source = entry.get("source", {})
                    # 把 github-subdir 指向本地 plugins/ 下的条目映射为本地源
                    if (
                        source.get("type") == "github-subdir"
                        and source.get("subdir", "").startswith("plugins/")
                    ):
                        local_subdir = root / source["subdir"]
                        if local_subdir.is_dir():
                            entry = dict(entry)
                            entry["_is_local"] = True
                            entry["_local_path"] = str(local_subdir)
                            entry["source"] = {"type": "local", "path": str(local_subdir)}
                    results.append(entry)
                    seen.add(name)
            except Exception as e:
                logger.warning("读取本地 marketplace.json %s 失败: %s", market_file, e)

        return results

    # ---- CRUD ----

    def install(self, name: str) -> tuple[bool, str]:
        """安装插件。

        1. 搜索 marketplace（远程 + 本地）找到插件条目
        2. 如果是本地插件，直接复制；否则 git clone 插件仓库到临时目录
        3. 定位 plugin.json（支持 github-subdir 子目录）
        4. 复制 skills 到 ~/.jarvis/skills/<name>/
        5. 合并 .mcp.json 到 ~/.jarvis/mcp.json
        6. 更新 installed.json

        Returns:
            (True, "") 成功，(False, "错误信息") 失败。
        """
        # 1. 查市场（远程 + 本地）
        plugins = self.search(name)
        entry = None
        for p in plugins:
            if p.get("name") == name:
                entry = p
                break
        if entry is None:
            return False, f"插件 '{name}' 不在市场中。用 /plugin search 查看可用插件。"

        source = entry.get("source", {})
        src_type = source.get("type", "")

        # 本地插件直接安装
        if src_type == "local" or entry.get("_is_local"):
            local_path = Path(entry.get("_local_path") or source.get("path", ""))
            if not local_path.is_dir():
                return False, f"本地插件目录不存在: {local_path}"
            return self._install_from_dir(local_path, source_label=str(local_path))

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

        ok, msg = self._install_from_dir(plugin_root, source_label=repo)

        # 清理临时目录
        try:
            shutil.rmtree(tmpdir)
        except Exception:
            pass

        return ok, msg

    def _install_from_dir(self, plugin_root: Path, source_label: str = "") -> tuple[bool, str]:
        """从本地目录安装插件。

        复制 skills、合并 MCP、更新 installed.json。

        @author aceFelix
        """
        # 1. 解析 plugin.json
        try:
            manifest_path = plugin_root / "plugin.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                return False, "插件缺少 plugin.json 清单文件"
        except json.JSONDecodeError as e:
            return False, f"plugin.json 格式错误: {e}"

        manifest_name = manifest.get("name", plugin_root.name)
        manifest_version = manifest.get("version", "0.0.0")
        manifest_skills: list[str] = manifest.get("skills", [])
        manifest_mcp: list[str] = manifest.get("mcp_servers", [])

        # 2. 复制 skills
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

        # 3. 合并 MCP 配置
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

        # 4. 写 installed.json
        installed = self._load()
        installed["plugins"][manifest_name] = {
            "name": manifest_name,
            "version": manifest_version,
            "source": source_label,
            "installed_at": datetime.now().isoformat(),
            "skills": installed_skills,
            "mcp_servers": added_mcp,
        }
        self._save(installed)

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

    # ---- 启用/禁用 ----

    def enable(self, name: str) -> tuple[bool, str]:
        """启用被禁用的插件。

        把该插件的 skills 从 ~/.jarvis/plugins/disabled/<name>/ 移回
        ~/.jarvis/skills/，并从禁用列表移除。

        @author aceFelix
        """
        disabled_dir = self._install_base / "disabled" / name
        if not disabled_dir.is_dir():
            return True, f"插件 '{name}' 已是启用状态。"

        errors: list[str] = []
        src_skills = disabled_dir / "skills"
        if src_skills.is_dir():
            self._skills_base.mkdir(parents=True, exist_ok=True)
            for skill_dir in src_skills.iterdir():
                if not skill_dir.is_dir():
                    continue
                dst = self._skills_base / skill_dir.name
                try:
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.move(str(skill_dir), str(dst))
                except Exception as e:
                    errors.append(f"移动技能 {skill_dir.name} 失败: {e}")

        try:
            shutil.rmtree(disabled_dir)
        except Exception as e:
            errors.append(f"清理禁用目录失败: {e}")

        state = self._load_disabled_state()
        if name in state.get("disabled", []):
            state["disabled"] = [x for x in state["disabled"] if x != name]
            self._save_disabled_state(state)

        if errors:
            return False, "; ".join(errors)
        return True, f"插件 '{name}' 已启用。重启或 /reset 后生效。"

    def disable(self, name: str) -> tuple[bool, str]:
        """禁用插件（不卸载，仅停止加载 skills）。

        把该插件的 skills 从 ~/.jarvis/skills/ 移动到
        ~/.jarvis/plugins/disabled/<name>/skills/，并记录到禁用列表。

        @author aceFelix
        """
        installed = self._load().get("plugins", {})
        entry = installed.get(name)
        if not entry:
            return False, f"插件 '{name}' 未安装。"

        state = self._load_disabled_state()
        if name in state.get("disabled", []):
            return True, f"插件 '{name}' 已是禁用状态。"

        errors: list[str] = []
        disabled_dir = self._install_base / "disabled" / name
        disabled_skills_dir = disabled_dir / "skills"
        disabled_skills_dir.mkdir(parents=True, exist_ok=True)

        for skill_name in entry.get("skills", []):
            src = self._skills_base / skill_name
            if not src.exists():
                continue
            dst = disabled_skills_dir / skill_name
            try:
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.move(str(src), str(dst))
            except Exception as e:
                errors.append(f"禁用技能 {skill_name} 失败: {e}")

        state.setdefault("disabled", []).append(name)
        self._save_disabled_state(state)

        if errors:
            return False, "; ".join(errors)
        return True, f"插件 '{name}' 已禁用。重启或 /reset 后生效。"

    def is_disabled(self, name: str) -> bool:
        """检查插件是否被禁用。"""
        state = self._load_disabled_state()
        return name in state.get("disabled", [])

    def list_disabled(self) -> list[str]:
        """返回被禁用的插件名列表。"""
        state = self._load_disabled_state()
        return state.get("disabled", [])

    # ---- 插件创建/校验 ----

    def create_plugin(
        self,
        name: str,
        *,
        display_name: str = "",
        description: str = "",
        output_dir: Path | str | None = None,
    ) -> tuple[bool, str]:
        """创建 Plugin 系统插件脚手架。

        生成 plugin.json + skills/ 目录 + README.md。

        Args:
            name: 插件 ID（小写，用连字符分隔）。
            display_name: 展示名。默认从 name 生成。
            description: 描述。
            output_dir: 输出目录。默认放临时目录。

        Returns:
            (True, 路径) 成功，(False, 错误信息) 失败。

        @author aceFelix
        """
        safe_name = name.strip().lower().replace(" ", "-")
        if not safe_name or not safe_name.replace("-", "").isalnum():
            return False, f"插件名 '{name}' 不合法，只能包含字母、数字和连字符。"

        import tempfile

        if output_dir:
            target = Path(output_dir) / f"jarvis-plugin-{safe_name}"
        else:
            target = Path(tempfile.gettempdir()) / f"jarvis-plugin-{safe_name}"
        if target.exists():
            return False, f"目录已存在: {target}"
        target.mkdir(parents=True, exist_ok=True)

        disp = display_name or safe_name.replace("-", " ").title()
        desc = description or f"{disp} plugin for Jarvis"

        manifest = {
            "name": safe_name,
            "display_name": disp,
            "version": "0.1.0",
            "description": desc,
            "author": "",
            "category": "general",
            "tags": [],
            "source": {
                "type": "github",
                "repo": f"your-username/jarvis-plugin-{safe_name}",
                "ref": "main",
            },
            "skills": [],
            "mcp_servers": [],
        }
        (target / "plugin.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (target / "skills").mkdir(exist_ok=True)
        readme = f"""# {disp} Plugin

{desc}

## 结构

```
plugin.json    # 插件清单
skills/        # SKILL.md 技能目录
.mcp.json      # MCP 服务器配置（可选）
```

## 发布

1. 将此目录推送到 GitHub 仓库
2. 在 marketplace.json 中添加条目
3. 用户通过 `/plugin install {safe_name}` 安装
"""
        (target / "README.md").write_text(readme, encoding="utf-8")

        return True, str(target)

    def validate_plugin(self, path: str | Path) -> tuple[bool, list[str]]:
        """校验 plugin.json 是否合法。

        Args:
            path: 插件目录或 plugin.json 文件路径。

        Returns:
            (True, []) 合法，(False, ["错误1", ...]) 不合法。

        @author aceFelix
        """
        p = Path(path)
        if p.is_dir():
            manifest = p / "plugin.json"
            if not manifest.exists():
                return False, [f"目录中未找到 plugin.json: {p}"]
            p = manifest
        elif not p.is_file():
            return False, [f"路径不存在: {path}"]
        elif p.name != "plugin.json":
            return False, [f"只支持 plugin.json 文件: {p.name}"]

        errors: list[str] = []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return False, [f"JSON 格式错误: {e}"]

        for field_name in ("name", "version", "description"):
            if not data.get(field_name):
                errors.append(f"缺少必填字段: {field_name}")

        source = data.get("source", {})
        if source:
            src_type = source.get("type", "")
            if src_type not in ("github", "github-subdir", "local"):
                errors.append(f"不支持的 source.type: {src_type}")
            if src_type in ("github", "github-subdir") and not source.get("repo"):
                errors.append("source.type 为 github 时必须指定 repo")

        name = data.get("name", "")
        if name and not name.replace("-", "").replace("_", "").isalnum():
            errors.append(f"name '{name}' 包含非法字符（只允许字母、数字、连字符、下划线）")

        return (len(errors) == 0), errors

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

    def _load_disabled_state(self) -> dict[str, Any]:
        """加载插件禁用状态。"""
        path = self._install_base / "disabled.json"
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"disabled": []}

    def _save_disabled_state(self, state: dict[str, Any]) -> None:
        """持久化插件禁用状态。"""
        path = self._install_base / "disabled.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
