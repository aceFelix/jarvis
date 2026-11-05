"""能力探针（静态档）—— 把 registry 的声明换成带证据的实测结论。

三条实现纪律来自 2026-11-03 的实机教训，改动前请先读完：

1. **只认当前解释器**。Python 包与内核 revision 都必须按"这个正在跑的进程"来查
   （`sys.executable` / 进程内 `importlib`）。本次 playwright 需要的 revision 绑在
   `jarvis/.venv` 里那份 1.60.0 上，换父 PATH 上另一个 python 去查就是另一套答案。
2. **revision 号一律问库自己**。绝不把 `1223` 写进代码——库一升级，硬编码立刻变成
   假报警源。这里读 playwright 包内的 `browsers.json`。
3. **"探针挂了" ≠ "东西没有"**。探测超时、无权限、解析失败一律记 `PROBE_FAILED`，
   能力聚合状态随之变成"结论不可信"，**不允许**下游据此生成"缺 X，请安装 X"的文案。
   本次事故里 `tasklist` 在沙箱内返回空输出、被当成"进程被回收"的证据讲给了用户，
   这条纪律就是那一幕的回归护栏。

冒烟档（真的起一次浏览器）在 S3 实现，本模块只声明它的位置。

@author aceFelix
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from importlib import metadata as _metadata
from importlib import util as _util
from pathlib import Path
from typing import Any

from agent.capabilities.registry import (
    CAPABILITY_REGISTRY,
    AssetReq,
    BinaryReq,
    CapabilityMeta,
    CapabilityReport,
    CapState,
    PlatformReq,
    PythonReq,
    ReqState,
    RequirementStatus,
    SecretReq,
    capabilities_blocking_names,
    capability_for_tool,
    get_meta,
)

# 探测结果缓存有效期（秒）。太短会拖慢启动与每次工具调用，太长会错过"用户刚装完"。
# 运行中自愈靠的就是过期后重探，所以这里必须有上限。
CACHE_TTL_S = 300.0

# 单条外部命令的探测超时（秒）。`git --version` 正常是几十毫秒，
# 卡到 5s 一定是有问题（磁盘、杀软拦截），此时应当报探针失败而不是猜结论。
_CMD_TIMEOUT_S = 5.0

_CACHE: dict[str, tuple[float, CapabilityReport]] = {}


# ---------------------------------------------------------------------------
# 版本约束的轻量比对
# ---------------------------------------------------------------------------


def _version_tuple(v: str) -> tuple[int, ...] | None:
    """把 ``1.60.0`` 这类前缀拆成整数元组；拆不动返回 None（不猜）。"""
    parts: list[int] = []
    for chunk in re.findall(r"\d+", v)[:3]:
        parts.append(int(chunk))
    return tuple(parts) if parts else None


def _version_ok(installed: str, spec: str) -> bool | None:
    """已装版本是否满足约束。

    只支持 ``>=x.y.z``（extras 里全是这一种写法）。约束解析不了或版本号异常时返回
    ``None``——语义是"我不知道"，调用方必须保持 OK 并在证据里注明未判定，
    禁止把"解析不了"当成"不满足"。
    """
    if not spec:
        return True
    m = re.match(r"\s*>=\s*([\d.]+)", spec)
    if not m:
        return None
    a = _version_tuple(installed)
    b = _version_tuple(m.group(1))
    if a is None or b is None:
        return None
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) >= b + (0,) * (width - len(b))


# ---------------------------------------------------------------------------
# 各类前置条件的探针
# ---------------------------------------------------------------------------


def probe_python(req: PythonReq, extras_group: str = "") -> RequirementStatus:
    """查 Python 包：能不能 import + 装的是哪个版本。

    注意这里判的是**包**，不是能力：包在但内核缺（本次事故）由 asset 探针负责，
    两者分开才能给出"缺的是哪一件"的可执行答案。
    """
    present = False
    try:
        present = _util.find_spec(req.probe_name) is not None
    except (ImportError, ValueError):
        # find_spec 对半初始化的包会抛异常，属"没装"而非探针故障
        present = False
    if not present:
        return RequirementStatus(
            kind="python",
            label=req.label,
            state=ReqState.MISSING,
            evidence=f"{sys.executable} 的环境里 import 不到 {req.probe_name}",
            fix_hint=_pip_hint(req, extras_group),
            optional=req.optional,
        )
    try:
        version = _metadata.version(req.distribution)
    except _metadata.PackageNotFoundError:
        # import 名与发行名不同源（如 cv2 / opencv-python）：查不到元数据不等于没装
        version = ""
    verdict = _version_ok(version, req.version_spec) if version else None
    evidence = f"{version or '版本未知'} @ {sys.executable}"
    if verdict is False:
        return RequirementStatus(
            kind="python", label=req.label, state=ReqState.MISSING,
            evidence=f"已装 {version}，低于要求的 {req.version_spec}",
            fix_hint=_pip_hint(req, extras_group), optional=req.optional,
        )
    if verdict is None and req.version_spec:
        evidence += "（版本约束未能判定，按已装处理）"
    return RequirementStatus(
        kind="python", label=req.label, state=ReqState.OK,
        evidence=evidence, optional=req.optional,
    )


def _pip_hint(req: PythonReq, extras_group: str = "") -> str:
    """装包提示：有 extras 组就指向整组，避免建议装出一个不在白名单上的单独包。

    修包名只能来自 `pyproject.toml` 的 extras 这一纪律（见计划 4.1），从提示文案就得起。
    """
    if extras_group:
        return f'"{sys.executable}" -m pip install "jarvis-agent[{extras_group}]"'
    return f'"{sys.executable}" -m pip install "{req.distribution}"'


def _sibling_executable(name: str) -> str | None:
    """在 `sys.executable` 同级目录里找 console script。

    为什么必须有这一步：pip 装的命令行入口（`pylsp` 这类）落在 venv 的 Scripts/bin 里，
    而 `shutil.which` 只看 PATH——用 `jarvis.exe` 直接拉起时 venv 不一定被 activate过，
    于是“包装了、命令却找不到”。这仍然是“只认当前解释器”那条纪律的一部分。
    """
    names = [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name] if sys.platform == "win32" else [name]
    base = Path(sys.executable).parent
    for cand in names:
        p = base / cand
        if p.is_file():
            return str(p)
    return None


def probe_binary(req: BinaryReq) -> RequirementStatus:
    """查 PATH 上的外部可执行文件。

    三种结局必须分开：找不到（MISSING）、找到了但跑不动/输出异常（PROBE_FAILED）、
    跑通且特征匹配（OK）。`expect_pattern` 用来确认"真是那个东西"。
    """
    path = shutil.which(req.name) or _sibling_executable(req.name)
    if not path:
        return RequirementStatus(
            kind="binary", label=req.name, state=ReqState.MISSING,
            evidence=f"PATH 与 {Path(sys.executable).parent} 里都找不到 {req.name}",
            fix_hint=f"安装 {req.name} 后重跑 /health caps（系统级依赖不代装）",
            optional=req.optional,
        )
    try:
        proc = subprocess.run(
            [path, *req.args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_CMD_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired:
        return RequirementStatus(
            kind="binary", label=req.name, state=ReqState.PROBE_FAILED,
            evidence=f"{req.name} {' '.join(req.args)} 超过 {_CMD_TIMEOUT_S}s 未返回（探针超时，不足以判它不存在）",
            optional=req.optional,
        )
    except OSError as exc:
        return RequirementStatus(
            kind="binary", label=req.name, state=ReqState.PROBE_FAILED,
            evidence=f"无法执行 {path}: {type(exc).__name__}: {exc}",
            optional=req.optional,
        )
    out = (proc.stdout or proc.stderr or "").strip().splitlines()
    head = out[0][:120] if out else ""
    if proc.returncode != 0:
        return RequirementStatus(
            kind="binary", label=req.name, state=ReqState.PROBE_FAILED,
            evidence=f"{path} 退出码 {proc.returncode}，输出：{head or '(空)'}",
            optional=req.optional,
        )
    if req.expect_pattern and req.expect_pattern not in head:
        # 跑通了但不是"那个东西"：判 MISSING（同名异物），并写清实际看到什么
        return RequirementStatus(
            kind="binary", label=req.name, state=ReqState.MISSING,
            evidence=f"{path} 输出不含 '{req.expect_pattern}'，实得：{head or '(空)'}",
            optional=req.optional,
        )
    return RequirementStatus(
        kind="binary", label=req.name, state=ReqState.OK,
        evidence=f"{path}｜{head}", optional=req.optional,
    )


def _ms_playwright_dir() -> Path:
    """playwright 内核目录（按平台与官方约定推断，允许环境变量覆盖）。

    与 registry 的纪律一致：路径由 playwright 自己在运行时决定，我们只按公开约定
    推断，并用 `PLAYWRIGHT_BROWSERS_PATH` 作为权威覆盖点。取不到就报探针失败。
    """
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _playwright_installed() -> bool:
    """playwright 包在当前解释器里能不能 import（单独拆出来便于测试替换）。"""
    try:
        return _util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def _playwright_revisions() -> dict[str, str]:
    """问 playwright 自己要哪些 revision（{"chromium": "1223", ...}）。

    返回空 dict 表示"问不到"，调用方必须据此报 PROBE_FAILED，不能退回猜数字。
    """
    try:
        import playwright

        candidates = [
            Path(playwright.__file__).parent / "driver" / "package" / "browsers.json",
        ]
        raw = ""
        for c in candidates:
            if c.is_file():
                raw = c.read_text(encoding="utf-8", errors="replace")
                break
        if not raw:
            return {}
        import json

        data = json.loads(raw)
        out: dict[str, str] = {}
        for item in data.get("browsers", []):
            name = str(item.get("name", ""))
            rev = str(item.get("revision", ""))
            if name in ("chromium", "chromium-headless-shell") and rev:
                key = name.replace("-", "_")
                out[key] = rev
        return out
    except Exception:  # noqa: BLE001 - 探针自身异常一律降级为"不知道"，不外泄堆栈
        return {}


def probe_asset(req: AssetReq) -> RequirementStatus:
    """数据资产探针：按 kind 分派到具体实现。"""
    handler: Callable[[AssetReq], RequirementStatus] | None = _ASSET_PROBES.get(req.kind)
    if handler is None:
        return RequirementStatus(
            kind="asset", label=req.label, state=ReqState.SKIPPED,
            evidence=f"未实现 '{req.kind}' 这类资产的探针，本阶段不下结论",
            optional=req.optional,
        )
    return handler(req)


def _probe_playwright_browsers(req: AssetReq) -> RequirementStatus:
    """playwright 内核：包在不在、revision 对不对、目录有没有。

    这是本次事故的当事探针。三层判断各给一条独立结论：
    - playwright 包没装 → SKIPPED（revision 无从对齐，别和 python 那条重复报缺失）
    - 问不到 revision → PROBE_FAILED（宁可不判，不猜数字）
    - 目录缺对应 revision → MISSING，并把**期望路径原文**写进证据（可复核、可反查）
    """
    if not _playwright_installed():
        return RequirementStatus(
            kind="asset", label=req.label, state=ReqState.SKIPPED,
            evidence="playwright 包未安装，revision 无法对齐（缺失由 python 条目报）",
            optional=req.optional,
        )
    revisions = _playwright_revisions()
    if not revisions:
        return RequirementStatus(
            kind="asset", label=req.label, state=ReqState.PROBE_FAILED,
            evidence="读不到 playwright 自带的 browsers.json，无法确定所需 revision（不猜数字）",
            optional=req.optional,
        )
    root = _ms_playwright_dir()
    if not root.parent.exists():
        return RequirementStatus(
            kind="asset", label=req.label, state=ReqState.PROBE_FAILED,
            evidence=f"内核根目录的上级不存在：{root}（路径判定可能有误，不据此判缺失）",
            optional=req.optional,
        )
    # 无头自查只需要 headless shell；完整 chromium 只在 headless=false 时用到，
    # 所以这里"有任一 chromium 形态即算可用"，但把两者状态都写进证据，便于定位。
    have: list[str] = []
    want: list[str] = []
    for key, rev in sorted(revisions.items()):
        folder = root / f"{key}-{rev}"
        want.append(f"{key}-{rev}")
        if folder.exists():
            have.append(f"{key}-{rev}")
    if have:
        return RequirementStatus(
            kind="asset", label=req.label, state=ReqState.OK,
            evidence=f"已装 {'、'.join(have)}（需要 {'、'.join(want)}）@ {root}",
            optional=req.optional,
        )
    return RequirementStatus(
        kind="asset", label=req.label, state=ReqState.MISSING,
        evidence=f"需要 {' 或 '.join(want)}，{root} 下一个都没有",
        fix_hint=f'"{sys.executable}" -m playwright install chromium --only-shell',
        optional=req.optional,
    )


def _probe_path_exists(req: AssetReq, *, want_file: bool = False) -> RequirementStatus:
    """通用存在性探针：展开 `~` 与 `%VAR%` 后查目录/文件是否存在。"""
    if not req.path:
        return RequirementStatus(
            kind="asset", label=req.label, state=ReqState.SKIPPED,
            evidence="声明里没给路径模板，无法探测", optional=req.optional,
        )
    raw = os.path.expandvars(os.path.expanduser(req.path))
    if sys.platform == "win32" and not Path(raw).is_absolute():
        raw = str(Path.home() / raw)
    target = Path(raw)
    if not target.parent.exists():
        return RequirementStatus(
            kind="asset", label=req.label, state=ReqState.MISSING,
            evidence=f"父目录都不存在：{target}",
            fix_hint=req.note or "", optional=req.optional,
        )
    ok = target.is_file() if want_file else target.is_dir()
    return RequirementStatus(
        kind="asset", label=req.label,
        state=ReqState.OK if ok else ReqState.MISSING,
        evidence=f"{'已存在' if ok else '不存在'}：{target}",
        fix_hint="" if ok else (req.note or "首次使用时由库自行下载，本阶段不代下"),
        optional=req.optional,
    )


_ASSET_PROBES: dict[str, Callable[[AssetReq], RequirementStatus]] = {
    "playwright-browsers": _probe_playwright_browsers,
    "dir-exists": lambda r: _probe_path_exists(r),
    "file-exists": lambda r: _probe_path_exists(r, want_file=True),
}


def probe_secret(req: SecretReq) -> RequirementStatus:
    """凭据探针：只看环境变量是否就位。

    keyring 检查留给后续（它需要真实解密，放进启动路径会拖慢且可能弹授权窗）。
    这里宁可 SKIPPED 承认没查，也不要给出"看起来有"的假安心。
    """
    if req.env_var and os.environ.get(req.env_var):
        return RequirementStatus(
            kind="secret", label=req.label, state=ReqState.OK,
            evidence=f"环境变量 {req.env_var} 已设置（值不回显）", optional=req.optional,
        )
    if req.keyring_service:
        return RequirementStatus(
            kind="secret", label=req.label, state=ReqState.SKIPPED,
            evidence=f"环境变量 {req.env_var or '(无)'} 未设置；keyring 条目本次不查",
            optional=True,
        )
    hint = f"设置环境变量 {req.env_var}，或在配置里填该项" if req.env_var else "见配置说明"
    if req.fallback_allowed:
        hint += "（有回退链，报错文案必须点名是哪一家，不能让回退值冒充未配置）"
    return RequirementStatus(
        kind="secret", label=req.label, state=ReqState.MISSING,
        evidence=f"环境变量 {req.env_var} 未设置", fix_hint=hint, optional=req.optional,
    )


def probe_platform(req: PlatformReq) -> RequirementStatus:
    """平台适用性。`unsupported` 与 `missing` 是两件事，不能混。"""
    here = sys.platform
    label = f"平台 {here}"
    supported = "、".join(req.systems)
    if here in req.systems:
        return RequirementStatus(
            kind="platform", label=label, state=ReqState.OK,
            evidence=f"支持 {here}（清单：{supported}）", optional=req.optional,
        )
    return RequirementStatus(
        kind="platform", label=label, state=ReqState.UNSUPPORTED,
        evidence=f"本平台 {here} 不在支持清单 {supported} 里：{req.reason or '未说明原因'}",
        optional=req.optional,
    )


# ---------------------------------------------------------------------------
# 聚合与缓存
# ---------------------------------------------------------------------------


def aggregate_state(reqs: list[RequirementStatus]) -> str:
    """把各条前置的结论合成能力状态。

    优先级是刻意的：
    1. **必需项的探针失败**压过一切——结论不可信时不能宣布缺失，否则下游会拿这条
       去生成“请安装 X”的错误建议（本次 `tasklist` 空输出被当成“进程被回收”就是这个链路）。
    2. 必需项平台不适用 / 必需项缺失。
    3. **可选项的探针失败只算降级**：不然一个可选工具的探测小毛病会把整个能力
       长期标成“不可信”，反而让人忽略真问题。
    4. 可选项缺失只算降级。
    """
    hard = [r for r in reqs if not r.optional]
    soft = [r for r in reqs if r.optional]
    if any(r.state == ReqState.PROBE_FAILED for r in hard):
        return CapState.PROBE_FAILED
    if any(r.state == ReqState.UNSUPPORTED for r in hard):
        return CapState.UNSUPPORTED
    if any(r.state == ReqState.MISSING for r in hard):
        return CapState.MISSING
    if any(
        r.state in (ReqState.MISSING, ReqState.UNSUPPORTED, ReqState.PROBE_FAILED)
        for r in soft
    ):
        return CapState.DEGRADED
    return CapState.OK


def _probe_all_requirements(meta: CapabilityMeta) -> list[RequirementStatus]:
    """按声明逐条探测（静态档）。异常兜底成 PROBE_FAILED，绝不冒泡打断启动。"""
    reqs: list[RequirementStatus] = []
    try:
        for r in meta.python_reqs:
            reqs.append(probe_python(r, meta.extras_group))
        for r in meta.binaries:
            reqs.append(probe_binary(r))
        for r in meta.assets:
            reqs.append(probe_asset(r))
        for r in meta.secrets:
            reqs.append(probe_secret(r))
        for r in meta.platforms:
            reqs.append(probe_platform(r))
    except Exception as exc:  # noqa: BLE001
        reqs.append(RequirementStatus(
            kind="probe", label="探测流程", state=ReqState.PROBE_FAILED,
            evidence=f"{type(exc).__name__}: {exc}",
        ))
    return reqs


def check(name: str, *, force: bool = False, now: float | None = None) -> CapabilityReport:
    """探测一个能力，结果带 TTL 缓存。

    Args:
        name: 能力名（`CAPABILITY_REGISTRY` 的 key）。
        force: 忽略缓存重探。用户刚装完东西时靠它立即生效；正常过期后也会自动重探，
            这是"不重启就能恢复可用"的机制。
        now: 注入当前时间戳，便于测试；默认 `time.time()`。

    表里没有这个能力时返回 `UNKNOWN` 报告——调用方必须按不可靠处理，
    静默当成"可用"就是本次事故的起点。
    """
    meta = get_meta(name)
    if meta is None:
        return CapabilityReport(
            name=name, title=name, state=CapState.UNKNOWN,
            checked_at=now if now is not None else time.time(),
            blocks_selfcheck=False,
        )
    ts = now if now is not None else time.time()
    cached = _CACHE.get(name)
    if cached and not force and (ts - cached[0]) < CACHE_TTL_S:
        # 命中的是上一个时间戳的同一份结论：只改 from_cache，不重探
        return replace(cached[1], from_cache=True)
    reqs = _probe_all_requirements(meta)
    report = CapabilityReport(
        name=meta.name,
        title=meta.title,
        state=aggregate_state(reqs),
        requirements=reqs,
        checked_at=ts,
        extras_group=meta.extras_group,
        repair_actions=meta.repair_actions,
        degradable=meta.degradable,
        blocks_selfcheck=meta.blocks_selfcheck,
    )
    _CACHE[name] = (ts, report)
    return report


def check_all(*, force: bool = False) -> dict[str, CapabilityReport]:
    """全表静态探测（启动时与 `/health caps` 用）。顺序稳定，便于比对与快照测试。"""
    return {name: check(name, force=force) for name in sorted(CAPABILITY_REGISTRY)}


def deep_check(name: str, *, force: bool = True) -> CapabilityReport:
    """静态档 + 冒烟档。只在人显式要（`/health caps --deep`）或修复后复测时跑。

    三个设计决定，每一条都是为了不把“准”换成“全”：

    1. **静态档已判缺件就不跑冒烟**：内核目录都不在，启动一次注定失败的浏览
       器只会多花 90s 并把同一件事报两遍。这里记一条 SKIPPED，让报告能自证
       “没跑”而不是“跑过且通过”。
    2. **冒烟结果写回同一个缓存**：这样“目录在、但内核真的起不来”能反过来
       让调用前门拦下工具——计划里“首次要用该能力时跑冒烟”靠的就是这一步。
       代价是：一次 `--deep` 的结论会在 TTL 内影响热路径，但 PROBE_FAILED 本来
       就不拦，所以只有“实测确实跑不起来”才会改变行为。
    3. **不吞异常**：探针坏掉是 PROBE_FAILED，不是 MISSING，分层已在 smoke.py 保证。
    """
    base = check(name, force=force)
    meta = get_meta(name)
    if meta is None or not meta.smoke_probe:
        return base

    from agent.capabilities.smoke import run_smoke_for

    if base.state in (CapState.MISSING, CapState.UNSUPPORTED):
        smoke_reqs = [RequirementStatus(
            kind="smoke",
            label=f"冒烟探针 {meta.smoke_probe}",
            state=ReqState.SKIPPED,
            evidence="静态档已判定缺件/不适用，跑冒烟没有意义（不是“跑过且通过”）",
        )]
    else:
        smoke_reqs = run_smoke_for(meta)

    merged = [*base.requirements, *smoke_reqs]
    report = replace(base, requirements=merged, state=aggregate_state(merged), from_cache=False)
    _CACHE[name] = (time.time(), report)
    return report


def deep_check_all(*, force: bool = True) -> dict[str, CapabilityReport]:
    """全表深检（静态 + 冒烟）。比 `check_all` 慢一到两个量级，绝不进启动路径。"""
    return {name: deep_check(name, force=force) for name in sorted(CAPABILITY_REGISTRY)}


def smoke_requirements(report: CapabilityReport) -> list[RequirementStatus]:
    """从报告里挑出冒烟那几条（报告渲染要分开写：静态一行、冒烟一行）。"""
    return [r for r in report.requirements if r.kind == "smoke"]


def reset_cache() -> None:
    """清空探测缓存（测试与"用户说刚装完了"时用）。"""
    _CACHE.clear()


def usable(name: str) -> bool:
    """快捷判定：能力当前是否可用（含降级可用）。"""
    return check(name).usable


# ---------------------------------------------------------------------------
# 面对工具的两个出口：调用前拦截（block）与发现时告知（note）
# ---------------------------------------------------------------------------

# 拦哪些状态是刻意的：只拦“确有结论且不可用”的 MISSING / UNSUPPORTED。
# PROBE_FAILED **不拦**——我们只是没看清，据此拒绝执行等于用一个编造的
# 理由阻止一次本来可能成功的调用（本次事故里“沙箱回收进程”就是这么编出来的）。
_BLOCK_STATES = (CapState.MISSING, CapState.UNSUPPORTED)


def report_for_tool(tool_name: str, *, force: bool = False) -> CapabilityReport | None:
    """查一个工具所属能力的当前结论；无声明返回 None。

    `force` 默认 False：工具调用是热路径，靠 TTL 缓存把探测成本拢住（静态档
    实测 <50ms，但一个任务能调上百次工具，乘以次数就不是小数）。需要“现在、
    当场”结论的地方（不可逆动作到点执行、修复后的复测）才传 True。
    """
    cap = capability_for_tool(tool_name)
    if not cap:
        return None
    return check(cap, force=force)


def tool_block_reason(tool_name: str, *, force: bool = False) -> str:
    """该不该在调用前拦下来；返回给模型看的理由（空串 = 放行）。

    文案里必须带四件东西：**哪个能力、缺的是什么、实测证据、可复制的补法**，
    否则模型只会换个工具继续试（本次 Chrome/Firefox 那 8 条 Bash 就是这样）。
    首行带上能力名，是让测得住在拼装层断言“拦下来的就是这一项”，不是只
    剩一句措辞好看的提示。

    `force=True` 给“即将产生外部副作用、且距上次判定已经隔了一段真实时间”的
    地方用（不可逆动作到点执行）：那里拿一缓存结果放行，等于自检没做过。
    """
    rep = report_for_tool(tool_name, force=force)
    if rep is None or rep.state not in _BLOCK_STATES:
        return ""
    lines = [f"能力「{rep.title}」({rep.name}) 当前不可用：{rep.summary_line()}"]
    for item in rep.missing_lines():
        lines.append(f"  - {item}")
    for r in rep.requirements:
        if r.state == ReqState.MISSING and r.fix_hint:
            lines.append(f"  补法：{r.fix_hint}")
    lines.append(
        "重复调用同一个工具不会有不同结果。请先向用户说明缺什么、"
        "建议如何补，或改用降级路径：" + (rep.degradable or "（本能力无降级路径）")
    )
    # 未执行就绝不能报告“看到/验证过”——这是本次事故里直接对用户不实陈述的那一步。
    # 拦在调用前时工具根本没跑，任何“结果”都是凭空写的。
    lines.append(
        "本次调用没有执行。禁止把它当成已验证、已看到、已跑通写进回复；"
        "需要时用上面的缺件事实直接向用户说明。"
    )
    return "\n".join(lines)


def tool_status_note(tool_name: str) -> str:
    """给 ToolSearch 返回文本的一行状态；无声明或完全可用时返回空串。

    目的：让模型在**决定调用之前**就知道这个工具现在跑不通，而不是
    调用后靠重试去发现。本次事故的直接成因就是 ToolSearch 报了一句
    “已找到 5 个工具，已加载到可用列表”却根本没查内核在不在。
    """
    rep = report_for_tool(tool_name)
    if rep is None:
        return ""
    if rep.state in _BLOCK_STATES:
        return f"⚠ 当前不可用：{rep.summary_line()}"
    if rep.state == CapState.PROBE_FAILED:
        return f"⚠ 无法确认：{rep.summary_line()}（别把它当成缺件，先交叉验证）"
    if rep.state == CapState.DEGRADED:
        return f"○ 可用但有缺项：{rep.summary_line()}"
    return ""


def selfcheck_blockers() -> list[CapabilityReport]:
    """列出「影响交付自查成立、且当前不可用或结论不可信」的能力。

    给 system prompt 与交付汇报用：只要这里有东西，模型就不该写"亲眼看过/已验证"。
    """
    out: list[CapabilityReport] = []
    for name in capabilities_blocking_names():
        report = check(name)
        if report.state in (CapState.MISSING, CapState.UNSUPPORTED,
                            CapState.PROBE_FAILED, CapState.UNKNOWN):
            out.append(report)
    return out


def build_capability_prompt_block() -> str:
    """给 system prompt 的「今日实际能力」段。

    只详写影响「交付自查」成立的能力（那些模型会拿它当验收手段的），
    其余能力一行带过——不然这一段会比工具说明还长，反而挤掉真正要看的约束。

    返回空串表示无事可报（全部可用），此时 system prompt 不多出任何字节。
    调用方负责按进程缓存：本函数会跑探测，而 system prompt 每轮都要拼。
    """
    blocking = [check(n) for n in capabilities_blocking_names()]
    others = [r for n, r in sorted(check_all().items()) if n not in set(capabilities_blocking_names())]
    bad_blocking = [r for r in blocking if r.state != CapState.OK]
    degraded_others = [r for r in others if r.state in (CapState.DEGRADED, CapState.MISSING,
                                                          CapState.PROBE_FAILED, CapState.UNSUPPORTED)]
    if not bad_blocking and not degraded_others:
        return ""

    lines = ["# 你今天的实际能力（启动时自检结果，实时状态以工具返回为准）", ""]
    for rep in bad_blocking:
        note = "无法确认" if rep.state == CapState.PROBE_FAILED else rep.summary_line()
        lines.append(f"- ⚠ {rep.title}：{note}")
        for item in rep.missing_lines():
            lines.append(f"    · {item}")
        if rep.degradable:
            lines.append(f"    · 降级路径：{rep.degradable}")
    if degraded_others:
        brief = "、".join(f"{r.name}（{r.summary_line()}）" for r in degraded_others)
        lines.append(f"- ○ 其它受限项：{brief}")
    if bad_blocking:
        lines += [
            "",
            (
                "**硬约束**：上述能力不可用时，不得写“我亲眼看过 / 已验证”——"
                "拿不准就直接说“我还不能确认”，并说明缺什么。"
                "探针报错不等于缺件，不要把“看不清”当成“没有”去给建议。"
            ),
        ]
    return "\n".join(lines) + "\n"


def registry_snapshot() -> dict[str, Any]:
    """表结构快照，供契约测与诊断输出核对声明完整性。"""
    return {
        name: {
            "title": meta.title,
            "extras_group": meta.extras_group,
            "python": [r.distribution for r in meta.python_reqs],
            "binaries": [r.name for r in meta.binaries],
            "assets": [(r.label, r.kind) for r in meta.assets],
            "secrets": [r.label for r in meta.secrets],
            "tools": list(meta.tools),
            "prefixes": list(meta.tool_prefixes),
            "repair": list(meta.repair_actions),
            "blocks_selfcheck": meta.blocks_selfcheck,
        }
        for name, meta in sorted(CAPABILITY_REGISTRY.items())
    }


def platform_name() -> str:
    """当前平台标识（诊断输出用；`platform` 只在报错信息里出现，避免顶部未使用导入）。"""
    return f"{sys.platform}/{platform.system()}"
