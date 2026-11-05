"""能力自检层（声明 + 探测 + 缓存）的测试。

存在的理由就是把两条纪律变成**测得住**的东西，而不是注释里的愿望：

1. `import 成功 ≠ 能力可用`（playwright 包在、内核缺，本次事故的起点）；
2. `探针挂了 ≠ 东西没有`（沙箱里 `tasklist` 空输出被当成"进程被回收"讲给了用户）。

外加一张契约网：表与真实注册的工具必须互相对得上，漏声明直接红——否则能力表
三个月就烂成"看着有、其实没人维护"。

@author aceFelix
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from agent.capabilities import probe as probe_mod
from agent.capabilities.registry import (
    CAPABILITY_REGISTRY,
    DEPENDENCY_FREE_TOOLS,
    AssetReq,
    BinaryReq,
    CapabilityReport,
    CapState,
    PythonReq,
    ReqState,
    RequirementStatus,
    SecretReq,
    all_declared_tools,
    capabilities_blocking_names,
    capability_for_tool,
    extras_for_group,
    get_meta,
)

# 修复动作白名单（计划 4.1）。加一项必须同时加实现与测试，写进表里不算数。
ALLOWED_REPAIR_ACTIONS = {"fetch-browser", "fetch-model", "pip-extras", "config-fix"}

# 运行时才发现的工具（CLI-Anything harness / MCP 服务器）：它们的依赖由
# harness 自己的 plugin.json 或 MCP 服务器声明，不进本表（能力表只盖 jarvis
# 自己能判定缺件的那部分，否则表会变成另一个包管理器）。
#
# 为什么这里必须显式豁免而不是靠“默认 registry 里没有它们”：
# `build_default_registry()` 是 lru_cache 的**共享实例**，任何测试往里注册过
# 72 个 harness 工具，之后同一进程里再 build 一次就会看到它们（2026-11-05 实际
# 踩到：tests/llm 跑完后本契约测变红，单独跑永远绿）。
# @author aceFelix
DYNAMIC_PREFIXES = ("cli_anything__", "mcp__")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _static_tool_names() -> set[str]:
    """从源码里收集 Tool 子类的 `name = "..."` 类属性，不 import 任何模块。

    为什么存在性判据不能取 `build_default_registry()` 的运行时结果：注册是
    **依赖条件**的——`_register_camera_tools()` 缺 cv2 直接 `return`，
    `_register_browser_tools()` 缺 playwright 直接 `return`。Linux CI 上没装
    opencv，`CameraShot` / `ListCameras` 就不在 registry 里，于是「表里登记了
    不存在的工具」这条红其实说的是「这台机器装得不全」。拿运行时注册表当存在性
    判据，恰好犯了本层要治的那个错——**`import 失败 ≠ 东西没有`**。
    （2026-11-05 GitHub Actions 实红：`CameraShot`，本地 Windows 装了 opencv 永绿）

    源码扫描同样保住了这条测原本的用处：名字写错、类被改名或删掉，字面量跟着
    没，依旧红。至于「这台机器上现在能不能用」，那是探测层的职责。

    @author aceFelix
    """
    names: set[str] = set()
    for py in (_repo_root() / "agent").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # 测不拦源码质问题，跳过即可
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # 只认基类名以 Tool 结尾的类（Tool / XxxTool），避开 provider 等其
            # 他带 name 类属性的普通类，防止把“别的名字”当成工具名存在证据。
            bases = {ast.unparse(b).split("[")[0].split(".")[-1] for b in node.bases}
            if not any(b.endswith("Tool") for b in bases):
                continue
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign):
                    continue
                value = stmt.value
                if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                    continue
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == "name":
                        names.add(value.value)
    return names


# ---------------------------------------------------------------------------
# 表自身的一致性
# ---------------------------------------------------------------------------


def test_key_equals_name_field() -> None:
    """表 key 与 meta.name 必须一致，否则反查与缓存键会各说各话。"""
    for key, meta in CAPABILITY_REGISTRY.items():
        assert meta.name == key, f"{key} 的 name 字段与 key 不一致"


def test_every_capability_has_title_and_boundary() -> None:
    """每个能力都得有一句人话名字；不可用时要么能降级、要么承认没法降级。"""
    for meta in CAPABILITY_REGISTRY.values():
        assert meta.title, f"{meta.name} 缺 title"
        assert meta.python_reqs or meta.binaries or meta.assets or meta.secrets or meta.platforms, (
            f"{meta.name} 一条前置条件都没声明"
        )


def test_repair_actions_are_whitelisted() -> None:
    """表里不许出现白名单之外的修复动作 ID（防"声明先于实现"）。"""
    for meta in CAPABILITY_REGISTRY.values():
        unknown = set(meta.repair_actions) - ALLOWED_REPAIR_ACTIONS
        assert not unknown, f"{meta.name} 声明了未知修复动作：{sorted(unknown)}"


def test_extras_group_exists_in_pyproject() -> None:
    """extras_group 必须是 pyproject 里真实存在的组名。

    `pip-extras` 的包名白名单就来自这里，指一个不存在的组等于给出一条会失败的命令。
    """
    text = (_repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    block = text[text.find("[project.optional-dependencies]"):]
    groups = set(re.findall(r"^(\w+) = \[", block, re.MULTILINE))
    assert groups, "pyproject 里没解析出 extras 组，本测试本身失效了"
    for meta in CAPABILITY_REGISTRY.values():
        if meta.extras_group:
            assert meta.extras_group in groups, f"{meta.name} 指向不存在的 extras 组 {meta.extras_group}"


def test_extras_for_group_lists_declared_packages() -> None:
    """按组反查包名：组名写错或包名漏声明都该在这里暴露。"""
    pkgs = extras_for_group("browser")
    assert "playwright" in pkgs


def test_blocking_selfcheck_names_cover_browser_and_capture() -> None:
    """「影响交付自查」的两个当事能力必须在名单里——prompt 与汇报文案都靠它。"""
    names = set(capabilities_blocking_names())
    assert {"browser.selfcheck", "gui.screen_capture"} <= names


# ---------------------------------------------------------------------------
# 表 ↔ 真实工具注册表 的双向契约
# ---------------------------------------------------------------------------


def test_every_deferred_tool_is_declared_or_exempted() -> None:
    """每个延迟加载工具都要能在表里查到归属，否则 ToolSearch 依旧在撒谎。

    这是防"表烂掉"的主闸：新增一个带外部依赖的工具却忘了登记，这里直接红。
    """
    from agent.core.tool import build_default_registry

    registry = build_default_registry()
    unclaimed = [
        t.name for t in registry.all_deferred()
        if not t.name.startswith(DYNAMIC_PREFIXES)   # 运行时发现的另说，见上面
        and not capability_for_tool(t.name) and t.name not in DEPENDENCY_FREE_TOOLS
    ]
    assert not unclaimed, f"这些 deferred 工具没有能力归属：{unclaimed}"


def test_declared_tool_names_actually_exist() -> None:
    """表里写的工具名必须在源码里真有个 Tool 类，不许凭空发明。

    判据取自源码而不是运行时注册表（原因见 `_static_tool_names()`）：名字写错、
    工具改名或被删除都要红；可选依赖没装不算「不存在」。
    """
    static = _static_tool_names()
    # 防本测自身失效：这几个名字都是源码里核实过的，扫不到就是取路径或解析
    # 写崩了，不能静默放行整表。CameraShot 特意在里面——它来自动态导入受限的
    # vision 目录，扫不到说明扫描被依赖条件带偏了。
    assert {"Bash", "ToolSearch", "ScreenShot", "CameraShot"} <= static, (
        f"静态扫描异常，本测试自身失效：{sorted(static)[:5]}"
    )
    missing = [t for t in all_declared_tools() if t not in static]
    assert not missing, f"表里登记了不存在的工具：{missing}"


def test_mcp_dynamic_tools_map_to_mcp_capability() -> None:
    """MCP 工具运行时才注册，按前缀归属；查不到就该退成 unknown 而不是当成可用。"""
    assert capability_for_tool("mcp__amap-maps__maps_weather") == "mcp.connect"
    assert capability_for_tool("NoSuchTool") == ""


# ---------------------------------------------------------------------------
# 报告对象的取值边界
# ---------------------------------------------------------------------------


def _report(state: str, reqs: list[RequirementStatus]) -> CapabilityReport:
    return CapabilityReport(name="x.y", title="演示", state=state, requirements=reqs)


def test_report_usable_excludes_probe_failed_and_unknown() -> None:
    """`usable` 只认 ok / degraded：结论不可信与未登记都不能算可用。"""
    assert _report(CapState.OK, []).usable
    assert _report(CapState.DEGRADED, []).usable
    for bad in (CapState.MISSING, CapState.PROBE_FAILED, CapState.UNKNOWN, CapState.UNSUPPORTED):
        assert not _report(bad, []).usable, bad


def test_report_missing_lines_only_required_and_carry_fix_hint() -> None:
    """必需项才进 missing_lines，且要带着可执行的补法。"""
    reqs = [
        RequirementStatus("python", "playwright", ReqState.MISSING, "import 不到", "pip install", False),
        RequirementStatus("python", "pywinauto", ReqState.MISSING, "没装", "pip install", True),
    ]
    rep = _report(CapState.MISSING, reqs)
    assert rep.missing_lines() == ["playwright：import 不到；补法：pip install"]
    assert rep.degraded_lines() == ["pywinauto：没装"]
    assert "playwright" in rep.summary_line() and "pywinauto" not in rep.summary_line()


def test_report_to_dict_shape_for_ledger() -> None:
    """账本与诊断输出要的结构化字段，字段名漂了就等于事故报告读不出。"""
    rep = _report(CapState.OK, [RequirementStatus("python", "p", ReqState.OK, "1.0", optional=False)])
    d = rep.to_dict()
    assert d["capability"] == "x.y" and d["state"] == CapState.OK
    assert d["requirements"][0]["label"] == "p" and "evidence" in d["requirements"][0]


# ---------------------------------------------------------------------------
# 聚合规则：探针失败与缺失的先后次序
# ---------------------------------------------------------------------------


def _r(state: str, *, optional: bool = False, kind: str = "python") -> RequirementStatus:
    return RequirementStatus(kind, f"{kind}-item", state, "证据", optional=optional)


@pytest.mark.parametrize(
    "reqs,expected",
    [
        ([_r(ReqState.OK)], CapState.OK),
        # 必需项探针失败压过必需项缺失：不能拿"看不清"当"没有"
        ([_r(ReqState.PROBE_FAILED), _r(ReqState.MISSING)], CapState.PROBE_FAILED),
        ([_r(ReqState.MISSING)], CapState.MISSING),
        ([_r(ReqState.UNSUPPORTED)], CapState.UNSUPPORTED),
        ([_r(ReqState.MISSING), _r(ReqState.MISSING, optional=True)], CapState.MISSING),
        ([_r(ReqState.OK), _r(ReqState.MISSING, optional=True)], CapState.DEGRADED),
        # 可选项探针失败只降级，不把整个能力长期标成不可信
        ([_r(ReqState.OK), _r(ReqState.PROBE_FAILED, optional=True)], CapState.DEGRADED),
        ([_r(ReqState.OK), _r(ReqState.SKIPPED, optional=True)], CapState.OK),
    ],
)
def test_aggregate_state_priority(reqs: list[RequirementStatus], expected: str) -> None:
    assert probe_mod.aggregate_state(reqs) == expected


# ---------------------------------------------------------------------------
# Python 包探针
# ---------------------------------------------------------------------------


def test_probe_python_missing_names_current_interpreter() -> None:
    """缺失结论里必须带上"是哪个解释器缺"，否则换环境时无法复核。"""
    st = probe_mod.probe_python(PythonReq("definitely-not-installed-xyz"))
    assert st.state == ReqState.MISSING
    assert probe_mod.sys.executable in st.evidence
    assert "definitely-not-installed-xyz" in st.fix_hint


def test_probe_python_hint_prefers_extras_group() -> None:
    """有 extras 组时补法指向整组，避免建议装出一个不在白名单上的散装包。"""
    st = probe_mod.probe_python(PythonReq("definitely-not-installed-xyz"), extras_group="browser")
    assert "jarvis-agent[browser]" in st.fix_hint


def test_probe_python_present_records_version() -> None:
    """已装时把版本号与解释器都写进证据（版本漂移靠这条才能事后追查）。"""
    st = probe_mod.probe_python(PythonReq("pytest", import_name="pytest"))
    assert st.state == ReqState.OK
    assert probe_mod.sys.executable in st.evidence


def test_probe_python_low_version_is_missing() -> None:
    """满足不了 `>=` 约束按缺失处理，并说明实得版本。"""
    st = probe_mod.probe_python(PythonReq("pytest", version_spec=">=999.0"))
    assert st.state == ReqState.MISSING
    assert "低于要求" in st.evidence


def test_probe_python_unparsable_spec_does_not_fail() -> None:
    """约束解析不了时必须保持 OK 并注明未判定——"判不了"不是"不满足"。"""
    st = probe_mod.probe_python(PythonReq("pytest", version_spec="~=weird"))
    assert st.state == ReqState.OK
    assert "未能判定" in st.evidence


@pytest.mark.parametrize(
    "installed,spec,expected",
    [
        ("1.60.0", ">=1.40.0", True),
        ("1.40.0", ">=1.40.0", True),
        ("1.39.9", ">=1.40.0", False),
        ("1.60.0", "", True),
        ("1.60.0", "~=1.4", None),      # 不支持的写法：承认不知道
        ("nightly", ">=1.40.0", None),  # 版本号异常：承认不知道
    ],
)
def test_version_ok_semantics(installed: str, spec: str, expected: bool | None) -> None:
    assert probe_mod._version_ok(installed, spec) is expected


# ---------------------------------------------------------------------------
# 二进制探针：三种结局必须分开
# ---------------------------------------------------------------------------


def test_probe_binary_not_found_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod.shutil, "which", lambda _n: None)
    monkeypatch.setattr(probe_mod, "_sibling_executable", lambda _n: None)
    st = probe_mod.probe_binary(BinaryReq("nonesuch", expect_pattern="nonesuch"))
    assert st.state == ReqState.MISSING
    assert "PATH" in st.evidence


def test_probe_binary_timeout_is_probe_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """超时只能报"看不清"。这里若判成 MISSING，下游就会建议重装——正是本次的错法。"""
    import subprocess as sp

    monkeypatch.setattr(probe_mod.shutil, "which", lambda _n: r"C:\fake\nonesuch.exe")

    def _raise(*_a, **_k):
        raise sp.TimeoutExpired(cmd="nonesuch", timeout=1)

    monkeypatch.setattr(probe_mod.subprocess, "run", _raise)
    st = probe_mod.probe_binary(BinaryReq("nonesuch"))
    assert st.state == ReqState.PROBE_FAILED
    assert "不足以判它不存在" in st.evidence


def test_probe_binary_pattern_mismatch_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """跑通了但不是那个东西（同名异物）判缺失，并把实得输出写进证据。"""
    monkeypatch.setattr(probe_mod.shutil, "which", lambda _n: r"C:\fake\git.exe")
    monkeypatch.setattr(
        probe_mod.subprocess, "run",
        lambda *_a, **_k: probe_mod.subprocess.CompletedProcess(args=[], returncode=0, stdout="other thing 1.0", stderr=""),
    )
    st = probe_mod.probe_binary(BinaryReq("git", expect_pattern="git version"))
    assert st.state == ReqState.MISSING
    assert "other thing" in st.evidence


def test_probe_binary_nonzero_exit_is_probe_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod.shutil, "which", lambda _n: r"C:\fake\git.exe")
    monkeypatch.setattr(
        probe_mod.subprocess, "run",
        lambda *_a, **_k: probe_mod.subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="boom"),
    )
    st = probe_mod.probe_binary(BinaryReq("git"))
    assert st.state == ReqState.PROBE_FAILED


def test_probe_binary_ok_records_path_and_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_mod.shutil, "which", lambda _n: r"C:\fake\git.exe")
    monkeypatch.setattr(
        probe_mod.subprocess, "run",
        lambda *_a, **_k: probe_mod.subprocess.CompletedProcess(args=[], returncode=0, stdout="git version 2.45.0", stderr=""),
    )
    st = probe_mod.probe_binary(BinaryReq("git", expect_pattern="git version"))
    assert st.state == ReqState.OK
    assert "git version" in st.evidence


def test_sibling_executable_finds_console_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """venv 的 Scripts 没进 PATH 时，命令仍要在解释器同级目录被找到。"""
    exe = tmp_path / "python.exe" if probe_mod.sys.platform == "win32" else tmp_path / "python"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(probe_mod.sys, "executable", str(exe))
    script = tmp_path / ("pylsp.exe" if probe_mod.sys.platform == "win32" else "pylsp")
    script.write_text("", encoding="utf-8")
    assert probe_mod._sibling_executable("pylsp") == str(script)


# ---------------------------------------------------------------------------
# 数据资产探针（playwright 内核：本次事故的当事项）
# ---------------------------------------------------------------------------

_ASSET = AssetReq(label="playwright 浏览器内核", kind="playwright-browsers")


def test_asset_skipped_when_package_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """包都没装时 revision 无从对齐，缺失由 python 条目报——两条不许重复计。"""
    monkeypatch.setattr(probe_mod, "_playwright_installed", lambda: False)
    st = probe_mod.probe_asset(_ASSET)
    assert st.state == ReqState.SKIPPED
    assert "playwright 包未安装" in st.evidence


def test_asset_probe_failed_when_revision_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """问不到 revision 就不许猜数字：结论必须停在"不可信"。"""
    monkeypatch.setattr(probe_mod, "_playwright_installed", lambda: True)
    monkeypatch.setattr(probe_mod, "_playwright_revisions", dict)
    st = probe_mod.probe_asset(_ASSET)
    assert st.state == ReqState.PROBE_FAILED
    assert "不猜数字" in st.evidence


def test_asset_missing_names_revision_and_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """缺失结论要写清"要哪个 revision、在哪个目录没找到"，并给出可复制的补法。"""
    monkeypatch.setattr(probe_mod, "_playwright_installed", lambda: True)
    monkeypatch.setattr(probe_mod, "_playwright_revisions",
                        lambda: {"chromium": "1223", "chromium_headless_shell": "1223"})
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty"))
    st = probe_mod.probe_asset(_ASSET)
    assert st.state == ReqState.MISSING
    assert "chromium-1223" in st.evidence
    assert "playwright install chromium --only-shell" in st.fix_hint
    assert probe_mod.sys.executable in st.fix_hint


def test_asset_ok_when_revision_dir_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "chromium_headless_shell-1223").mkdir(parents=True)
    monkeypatch.setattr(probe_mod, "_playwright_installed", lambda: True)
    monkeypatch.setattr(probe_mod, "_playwright_revisions",
                        lambda: {"chromium_headless_shell": "1223"})
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    st = probe_mod.probe_asset(_ASSET)
    assert st.state == ReqState.OK
    assert "chromium_headless_shell-1223" in st.evidence


def test_asset_unknown_kind_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """没实现 kinds 的资产只能承认没查，不能默认成可用。"""
    st = probe_mod.probe_asset(AssetReq(label="神秘资产", kind="not-implemented"))
    assert st.state == ReqState.SKIPPED


def test_dir_asset_missing_when_parent_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    st = probe_mod.probe_asset(AssetReq(label="权重", kind="dir-exists", path=str(tmp_path / "nope" / "deep")))
    assert st.state == ReqState.MISSING
    assert "父目录都不存在" in st.evidence


# ---------------------------------------------------------------------------
# 凭据与平台探针
# ---------------------------------------------------------------------------


def test_secret_present_never_echoes_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_TEST_SECRET", "super-value")
    st = probe_mod.probe_secret(SecretReq(label="密钥", env_var="JARVIS_TEST_SECRET"))
    assert st.state == ReqState.OK
    assert "super-value" not in st.evidence


def test_secret_missing_mentions_fallback_when_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """允许回退的凭据，补法里必须点名"回退会给谁"——否则回退值会冒充未配置。"""
    monkeypatch.delenv("JARVIS_TEST_SECRET", raising=False)
    st = probe_mod.probe_secret(SecretReq(label="密钥", env_var="JARVIS_TEST_SECRET", fallback_allowed=True))
    assert st.state == ReqState.MISSING
    assert "点名" in st.fix_hint


def test_platform_unsupported_is_not_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """平台不适用与"没装"是两件事，状态词必须区分。"""
    monkeypatch.setattr(probe_mod.sys, "platform", "win32")
    st = probe_mod.probe_platform(probe_mod.PlatformReq(("linux",), reason="只有 Linux 有"))
    assert st.state == ReqState.UNSUPPORTED
    assert "linux" in st.evidence


# ---------------------------------------------------------------------------
# check() 缓存与"不重启即恢复"
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_cache() -> object:
    """每条用例都从空缓存开始，否则用例间会互相看到对方的结论。"""
    probe_mod.reset_cache()
    yield
    probe_mod.reset_cache()


def test_check_unknown_capability_is_not_usable() -> None:
    """表里没有 = 按不可靠处理。静默当"可用"正是本次事故的起点。"""
    rep = probe_mod.check("no.such.capability")
    assert rep.state == CapState.UNKNOWN
    assert not rep.usable


def test_check_uses_cache_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        probe_mod, "_probe_all_requirements",
        lambda meta: calls.append(meta.name) or [RequirementStatus("python", "p", ReqState.OK, "1.0")],
    )
    first = probe_mod.check("camera.capture", now=1000.0)
    second = probe_mod.check("camera.capture", now=1100.0)
    assert calls == ["camera.capture"], "TTL 内不该重探"
    assert not first.from_cache and second.from_cache
    assert second.state == first.state


def test_check_reprobes_after_ttl_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    """过期后自动重探 ⇒ 用户中途装好东西，运行中的实例无需重启就能恢复可用。"""
    state = {"ok": False}

    def _fake(meta):
        ok = state["ok"]
        return [RequirementStatus("python", "p", ReqState.OK if ok else ReqState.MISSING, "演示")]

    monkeypatch.setattr(probe_mod, "_probe_all_requirements", _fake)
    assert probe_mod.check("camera.capture", now=1000.0).state == CapState.MISSING
    state["ok"] = True
    # 仍在 TTL 内：拿到的还是旧结论（缓存的价值就在这里）
    assert probe_mod.check("camera.capture", now=1100.0).state == CapState.MISSING
    # 过期后重探：立即恢复
    assert probe_mod.check("camera.capture", now=1000.0 + probe_mod.CACHE_TTL_S + 1).state == CapState.OK


def test_check_force_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        probe_mod, "_probe_all_requirements",
        lambda meta: calls.append(meta.name) or [RequirementStatus("python", "p", ReqState.OK, "1.0")],
    )
    probe_mod.check("camera.capture", now=1000.0)
    probe_mod.check("camera.capture", now=1000.0, force=True)
    assert calls == ["camera.capture", "camera.capture"]


def test_probe_crash_becomes_probe_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测过程自己炸了不能冒泡打断启动，也不能冒出一句"缺 X"。"""

    def _boom(meta):
        raise RuntimeError("探针内部炸了")

    monkeypatch.setattr(probe_mod, "probe_python", _boom)
    rep = probe_mod.check("camera.capture")
    assert rep.state == CapState.PROBE_FAILED
    assert "探针失败" in rep.summary_line()


def test_selfcheck_blockers_reports_browser_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """内核缺失必须出现在"交付自查受阻"名单里——prompt 与汇报文案都依赖这条。"""
    monkeypatch.setattr(probe_mod, "_playwright_installed", lambda: True)
    monkeypatch.setattr(probe_mod, "_playwright_revisions", lambda: {"chromium_headless_shell": "1223"})
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(Path(probe_mod.sys.executable).parent / "no-such-dir"))
    blockers = probe_mod.selfcheck_blockers()
    assert "browser.selfcheck" in [b.name for b in blockers]
    assert not probe_mod.usable("browser.selfcheck")


def test_registry_snapshot_covers_whole_table() -> None:
    """快照给诊断与契约测用，条目数必须与表一致（防止静默漏项）。"""
    snap = probe_mod.registry_snapshot()
    assert set(snap) == set(CAPABILITY_REGISTRY)
    assert snap["browser.selfcheck"]["assets"], "browser 必须声明数据资产，否则回到 import 层的老错"


def test_get_meta_returns_none_for_unknown() -> None:
    assert get_meta("no.such") is None
    assert get_meta("browser.selfcheck") is not None
