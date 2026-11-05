"""冒烟探针与能力报告（S3）的测试。

S1 证明「表说得对」，S2 证明「结论走到了模型眼前」，S3 要证明两件事：

1. **装了但跑不起来**这件事看得见（静态档的盲区），而且看不见时**不许**伪装成看见；
2. `/health caps` 给出的修复命令**可复制、可执行、且只在确有缺件时出现**。

计划 7.2 的故障注入验收（改名内核目录 → ①静态报缺 ②ToolSearch 带说明 ③不重试
④报告给正确命令 ⑤改回后不重启即恢复）在这里覆盖 ④⑤ 两步，①②③ 见
`test_capability_consumers.py` 与 `test_capability_probe.py`。

@author aceFelix
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent.capabilities import probe as probe_mod
from agent.capabilities import smoke as smoke_mod
from agent.capabilities.registry import (
    CAPABILITY_REGISTRY,
    CapabilityMeta,
    CapabilityReport,
    CapState,
    PythonReq,
    ReqState,
    RequirementStatus,
)

_BROWSER_CAP = "browser.selfcheck"


def _req(state: ReqState, *, kind: str = "python", optional: bool = False,
         label: str = "playwright", evidence: str = "实测证据",
         fix_hint: str = "") -> RequirementStatus:
    return RequirementStatus(
        kind=kind, label=label, state=state, evidence=evidence,
        fix_hint=fix_hint, optional=optional,
    )


def _report(name: str, state: CapState, reqs: list[RequirementStatus], *,
            title: str = "标题", degradable: str = "") -> CapabilityReport:
    return CapabilityReport(
        name=name, title=title, state=state, requirements=reqs,
        checked_at=0.0, degradable=degradable,
    )


class _Completed:
    """假的 subprocess.CompletedProcess 结果。"""

    def __init__(self, rc: int = 0, out: str = "", err: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = err


@pytest.fixture(autouse=True)
def _clean_cache() -> object:
    """冒烟结论会写进同一个缓存，用例之间必须隔离。"""
    probe_mod.reset_cache()
    yield
    probe_mod.reset_cache()


# ---------------------------------------------------------------------------
# A. 探针本身：失败分类是唯一要紧的事
# ---------------------------------------------------------------------------


class TestSmokeProbeClassification:
    """同一句「启动失败」，分成 missing 还是 probe_failed，后果完全不同。"""

    def test_launch_uses_current_interpreter_in_a_subprocess(self, monkeypatch):
        """只认 `sys.executable`：用别的 python 验的就不是用户手上这个环境。"""
        seen: dict[str, object] = {}

        def _fake_run(cmd, **kw):
            seen["cmd"] = cmd
            seen["timeout"] = kw.get("timeout")
            return _Completed(0, "SMOKE_OK \n")

        monkeypatch.setattr(smoke_mod.subprocess, "run", _fake_run)
        st = smoke_mod.smoke_browser_launch()

        assert st.state == ReqState.OK
        assert seen["cmd"][0] == sys.executable  # type: ignore[index]
        assert seen["timeout"], "没有超时就没有硬边界，探针能把命令层挂死"

    def test_explicit_missing_marker_becomes_missing_with_copyable_hint(self, monkeypatch):
        """"内核真的没装"要判 missing，且补法必须带当前解释器的绝对路径。"""
        err = (
            "playwright._impl._api_types.Error: Executable doesn't exist at "
            "C:\\Users\\x\\AppData\\Local\\ms-playwright\\chromium_headless_shell-1223\\..."
            "\n╚ Run the following command to download new browsers: ╝\n"
            "╚ playwright install                                ╝\n"
        )
        monkeypatch.setattr(
            smoke_mod.subprocess, "run",
            lambda *a, **k: _Completed(1, "", err),
        )
        st = smoke_mod.smoke_browser_launch()

        assert st.state == ReqState.MISSING
        assert sys.executable in st.fix_hint
        assert "playwright install" in st.fix_hint

    def test_unrecognized_failure_is_probe_failed_not_missing(self, monkeypatch):
        """本次 tasklist 骗局的回归护栏：认不出来的失败**不许**变成安装建议。"""
        monkeypatch.setattr(
            smoke_mod.subprocess, "run",
            lambda *a, **k: _Completed(1, "", "browserType.launch: Target page, context or browser has been closed"),
        )
        st = smoke_mod.smoke_browser_launch()

        assert st.state == ReqState.PROBE_FAILED
        assert st.fix_hint == "", "不知道缺什么就不给补法，给了就是编造"

    def test_timeout_is_probe_failed(self, monkeypatch):
        def _boom(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="python", timeout=1)

        monkeypatch.setattr(smoke_mod.subprocess, "run", _boom)
        st = smoke_mod.smoke_browser_launch(timeout_s=1.0)

        assert st.state == ReqState.PROBE_FAILED
        assert "超时" in st.evidence

    def test_cannot_spawn_child_is_probe_failed(self, monkeypatch):
        """连子进程都起不来（解释器被删）也不能判成「能力缺失」。"""
        def _boom(*_a, **_k):
            raise OSError("拒绝访问")

        monkeypatch.setattr(smoke_mod.subprocess, "run", _boom)
        st = smoke_mod.smoke_screen_capture()

        assert st.state == ReqState.PROBE_FAILED
        assert "无法启动探针子进程" in st.evidence

    def test_screen_capture_success_records_resolution(self, monkeypatch):
        monkeypatch.setattr(
            smoke_mod.subprocess, "run", lambda *a, **k: _Completed(0, "SMOKE_OK 1920 1080\n")
        )
        st = smoke_mod.smoke_screen_capture()

        assert st.state == ReqState.OK
        assert "1920 1080" in st.evidence

    def test_screen_capture_never_claims_missing(self, monkeypatch):
        """截不到屏可能是锁屏/远程会话/无桌面，绝不是「没装 pyautogui」。"""
        monkeypatch.setattr(
            smoke_mod.subprocess, "run", lambda *a, **k: _Completed(1, "", "no display")
        )
        st = smoke_mod.smoke_screen_capture()

        assert st.state == ReqState.PROBE_FAILED

    def test_probe_crash_inside_wrapper_is_contained(self, monkeypatch):
        meta = CapabilityMeta(name="x", title="X", smoke_probe="browser-launch")

        def _boom(*_a, **_k):
            raise RuntimeError("探针内部炸了")

        # setitem 不是 setattr：SMOKE_PROBES 是个 dict，而 run_smoke_for 在调用时才
        # 从这里取函数——替模块属性不会生效。
        monkeypatch.setitem(smoke_mod.SMOKE_PROBES, "browser-launch", _boom)
        reqs = smoke_mod.run_smoke_for(meta)

        assert len(reqs) == 1 and reqs[0].state == ReqState.PROBE_FAILED

    def test_unimplemented_probe_name_is_reported_not_skipped(self):
        """表里写了个不存在的探针名：必须显式失败，不能静默当成「无需冒烟」。"""
        meta = CapabilityMeta(name="x", title="X", smoke_probe="no-such-probe")
        reqs = smoke_mod.run_smoke_for(meta)

        assert reqs[0].state == ReqState.PROBE_FAILED
        assert "SMOKE_PROBES" in reqs[0].evidence

    def test_capability_without_probe_yields_nothing(self):
        meta = CapabilityMeta(name="x", title="X")
        assert smoke_mod.run_smoke_for(meta) == []


# ---------------------------------------------------------------------------
# B. 深档合并：静态缺件不白跑，装了跑不起来能拦住
# ---------------------------------------------------------------------------


class TestDeepCheck:
    """`deep_check` 的三个语义各是一条防线，缺一不可。"""

    def _meta(self, *, smoke: str) -> CapabilityMeta:
        return CapabilityMeta(
            name=_BROWSER_CAP, title="浏览器自动化",
            python_reqs=(PythonReq("playwright"),), smoke_probe=smoke,
            blocks_selfcheck=True,
        )

    def test_static_missing_skips_smoke_and_says_so(self, monkeypatch):
        monkeypatch.setattr(
            probe_mod, "get_meta", lambda _n: self._meta(smoke="browser-launch"),
        )
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            lambda _m: [_req(ReqState.MISSING, evidence="目录里没有 revision 1223")],
        )
        called: list[str] = []
        monkeypatch.setitem(
            smoke_mod.SMOKE_PROBES, "browser-launch",
            lambda **_k: called.append("跑") or _req(ReqState.OK, kind="smoke"),
        )

        rep = probe_mod.deep_check(_BROWSER_CAP)

        assert called == [], "静态档已判缺件，不该再花 90s 撞一次注定失败的启动"
        smoke_reqs = probe_mod.smoke_requirements(rep)
        assert smoke_reqs[0].state == ReqState.SKIPPED
        assert "不是“跑过且通过”" in smoke_reqs[0].evidence

    def test_smoke_missing_flips_capability_to_blocking(self, monkeypatch):
        """静态全过、冒烟起不来 → 能力变 missing → 调用前门拦得下（计划第 3.2 那一步）。"""
        monkeypatch.setattr(
            probe_mod, "get_meta", lambda _n: self._meta(smoke="browser-launch"),
        )
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            lambda _m: [_req(ReqState.OK, evidence="1.60.0")],
        )
        monkeypatch.setitem(
            smoke_mod.SMOKE_PROBES, "browser-launch",
            lambda **_k: _req(
                ReqState.MISSING, kind="smoke", label="冒烟",
                evidence="启动时报缺件", fix_hint="python -m playwright install chromium --only-shell",
            ),
        )

        rep = probe_mod.deep_check(_BROWSER_CAP)

        assert rep.state == CapState.MISSING
        assert probe_mod.tool_block_reason("BrowserNavigate"), "深档结论要能被热路径用上"

    def test_smoke_probe_failed_does_not_block(self, monkeypatch):
        monkeypatch.setattr(
            probe_mod, "get_meta", lambda _n: self._meta(smoke="browser-launch"),
        )
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            lambda _m: [_req(ReqState.OK, evidence="1.60.0")],
        )
        monkeypatch.setitem(
            smoke_mod.SMOKE_PROBES, "browser-launch",
            lambda **_k: _req(ReqState.PROBE_FAILED, kind="smoke", label="冒烟", evidence="超时"),
        )

        rep = probe_mod.deep_check(_BROWSER_CAP)

        assert rep.state == CapState.PROBE_FAILED
        assert probe_mod.tool_block_reason("BrowserNavigate") == ""

    def test_deep_result_is_cached_for_the_hot_path(self, monkeypatch):
        """/health caps --deep 之后立刻调工具，结论要沿用（否则深档白跑一次）。"""
        monkeypatch.setattr(
            probe_mod, "get_meta", lambda _n: self._meta(smoke="browser-launch"),
        )
        runs: list[int] = []
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            lambda _m: runs.append(1) or [_req(ReqState.OK, evidence="1.60.0")],
        )
        monkeypatch.setitem(
            smoke_mod.SMOKE_PROBES, "browser-launch",
            lambda **_k: _req(ReqState.OK, kind="smoke", label="冒烟"),
        )

        probe_mod.deep_check(_BROWSER_CAP)
        again = probe_mod.check(_BROWSER_CAP)

        assert len(runs) == 1, "第二次应命中缓存"
        assert again.from_cache

    def test_capability_without_smoke_returns_static_report(self, monkeypatch):
        monkeypatch.setattr(probe_mod, "get_meta", lambda _n: self._meta(smoke=""))
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            lambda _m: [_req(ReqState.OK, evidence="1.60.0")],
        )
        rep = probe_mod.deep_check(_BROWSER_CAP)

        assert rep.state == CapState.OK
        assert probe_mod.smoke_requirements(rep) == []


# ---------------------------------------------------------------------------
# C. 表与探针的契约：漂移立刻红
# ---------------------------------------------------------------------------


class TestSmokeContract:
    """能力表声明的探针名与 SMOKE_PROBES 必须互相对得上。"""

    def test_every_declared_smoke_probe_is_implemented(self) -> None:
        missing = [
            f"{meta.name} -> {meta.smoke_probe}"
            for meta in CAPABILITY_REGISTRY.values()
            if meta.smoke_probe and meta.smoke_probe not in smoke_mod.SMOKE_PROBES
        ]
        assert missing == [], "表里声明了探针但没实现：深档会把它报成无法确认，白跑一场"

    def test_no_orphan_probes(self) -> None:
        declared = {m.smoke_probe for m in CAPABILITY_REGISTRY.values() if m.smoke_probe}
        orphan = sorted(set(smoke_mod.SMOKE_PROBES) - declared)
        assert orphan == [], "实现了没人声明的探针等于死代码，要么挂到能力上要么删"

    def test_selfcheck_blocking_capabilities_have_a_way_out(self) -> None:
        """影响交付自查的能力必须有降级路径或修复动作，否则就是逼模型造假。"""
        bad = [
            meta.name for meta in CAPABILITY_REGISTRY.values()
            if meta.blocks_selfcheck and not meta.degradable and not meta.repair_actions
        ]
        assert bad == []


# ---------------------------------------------------------------------------
# D. 报告渲染
# ---------------------------------------------------------------------------


class TestReportRendering:
    """措辞即纪律：「无法确认」绝不能印成「未安装」，补法绝不能凭空给。"""

    def _collect(self, monkeypatch, reports: dict[str, CapabilityReport]):
        from agent.capabilities import report as report_mod

        monkeypatch.setattr(report_mod, "check_all", lambda **_k: reports)
        monkeypatch.setattr(report_mod, "deep_check_all", lambda **_k: reports)
        return report_mod

    def test_missing_capability_prints_copyable_command(self, monkeypatch):
        rep_mod = self._collect(monkeypatch, {
            _BROWSER_CAP: _report(
                _BROWSER_CAP, CapState.MISSING,
                [_req(ReqState.MISSING, kind="asset", label="playwright 浏览器内核",
                      evidence="目录里一个都没有",
                      fix_hint='"C:\\venv\\Scripts\\python.exe" -m playwright install chromium --only-shell')],
                title="浏览器自动化",
            ),
        })
        text = rep_mod.render_caps(rep_mod.collect_reports())

        assert "不可用" in text
        assert "playwright install chromium --only-shell" in text
        assert "可复制的修复命令" in text

    def test_probe_failed_wording_is_unconfirmed_and_warns_against_installing(self, monkeypatch):
        rep_mod = self._collect(monkeypatch, {
            "vision.detect": _report(
                "vision.detect", CapState.PROBE_FAILED,
                [_req(ReqState.PROBE_FAILED, kind="binary", label="某个命令",
                      evidence="探针超时", fix_hint="pip install 某个包")],
                title="实时视觉检测",
            ),
        })
        text = rep_mod.render_caps(rep_mod.collect_reports())

        assert "无法确认" in text
        assert "那不是缺件，别照着装东西" in text
        # 关键：PROBE_FAILED 的 fix_hint 不进「修复命令」清单
        assert "pip install 某个包" not in text

    def test_optional_missing_contributes_no_fix_command(self, monkeypatch):
        rep_mod = self._collect(monkeypatch, {
            "lsp.python": _report(
                "lsp.python", CapState.DEGRADED,
                [_req(ReqState.MISSING, label="python-lsp-server", optional=True,
                      fix_hint="pip install python-lsp-server")],
                title="Python 语言服务",
            ),
        })
        text = rep_mod.render_caps(rep_mod.collect_reports())

        assert "可用（有缺项）" in text
        rep = rep_mod.collect_reports()["lsp.python"]
        assert rep_mod.iter_fix_commands({"lsp.python": rep}) == []
        assert "可复制的修复命令" not in text, "可选项缺失不该催用户装东西"

    def test_deep_header_states_which_probes_actually_ran(self, monkeypatch):
        """深档自证跑了什么：让「没跑」和「跑过且通过」在报告里看得见差别。"""
        rep_mod = self._collect(monkeypatch, {
            _BROWSER_CAP: _report(
                _BROWSER_CAP, CapState.MISSING,
                [
                    _req(ReqState.MISSING, kind="asset", label="内核", evidence="没有"),
                    _req(ReqState.SKIPPED, kind="smoke", label="冒烟探针 browser-launch",
                         evidence="静态档已判定缺件"),
                ],
                title="浏览器自动化",
            ),
            "gui.screen_capture": _report(
                "gui.screen_capture", CapState.OK,
                [
                    _req(ReqState.OK, label="pyautogui", evidence="有"),
                    _req(ReqState.OK, kind="smoke", label="冒烟探针 screen-capture",
                         evidence="截帧成功 1920 1080"),
                ],
                title="屏幕截图",
            ),
        })
        text = rep_mod.render_caps(rep_mod.collect_reports(deep=True), deep=True)

        assert "冒烟实跑：gui.screen_capture" in text
        assert "因缺件跳过：browser.selfcheck" in text

    def test_all_ok_without_probe_suggests_deep(self, monkeypatch):
        rep_mod = self._collect(monkeypatch, {
            "camera.capture": _report(
                "camera.capture", CapState.OK, [_req(ReqState.OK, label="cv2", evidence="有")],
                title="摄像头",
            ),
        })
        text = rep_mod.render_caps(rep_mod.collect_reports())

        assert "其余 1 项可用" in text
        assert "/health caps --deep" in text

    def test_detail_view_lists_every_requirement_and_hint(self, monkeypatch):
        from agent.capabilities import report as report_mod

        # 用真表里的 browser.selfcheck：详情视图的 extras 组/修复动作/降级路径全部
        # 来自声明本体，替掉 get_meta 就等于只测了自己的排版，测不到声明与渲染的一致性。
        monkeypatch.setattr(
            probe_mod, "_probe_all_requirements",
            lambda _m: [
                _req(ReqState.OK, label="playwright>=1.40.0", evidence="1.60.0"),
                _req(ReqState.MISSING, kind="asset", label="playwright 浏览器内核",
                     evidence="需要 chromium-1223", fix_hint="playwright install"),
            ],
        )
        text = report_mod.render_one(_BROWSER_CAP)

        assert "前置条件实测" in text
        assert "playwright 浏览器内核" in text and "补法：playwright install" in text
        assert "降级路径：文本级验证" in text
        assert "影响交付自查：是" in text
        assert "fetch-browser" in text, "允许的修复动作要透明，用户才能判断这一项能不能自动修"

    def test_detail_view_for_unknown_name_lists_known(self):
        from agent.capabilities.report import render_one

        text = render_one("no.such.capability")

        assert "没有登记过能力" in text
        assert _BROWSER_CAP in text

    def test_json_shape_is_stable_and_sorted(self, monkeypatch):
        rep_mod = self._collect(monkeypatch, {
            "b.cap": _report("b.cap", CapState.OK, [_req(ReqState.OK, label="x", evidence="y")]),
            "a.cap": _report(
                "a.cap", CapState.MISSING,
                [_req(ReqState.MISSING, label="x", evidence="y", fix_hint="装一下")],
            ),
        })
        payload = rep_mod.to_dict(rep_mod.collect_reports())

        assert list(payload) == ["generated_at", "capabilities", "fix_commands"]
        assert list(payload["capabilities"]) == ["a.cap", "b.cap"], "键序稳定，便于两次 diff"
        assert payload["fix_commands"] == [{"capability": "a.cap", "command": "装一下"}]

    def test_markdown_covers_every_capability(self, monkeypatch):
        rep_mod = self._collect(monkeypatch, {
            _BROWSER_CAP: _report(
                _BROWSER_CAP, CapState.MISSING,
                [_req(ReqState.MISSING, kind="asset", label="内核", evidence="目录里没有",
                      fix_hint="playwright install")],
                title="浏览器自动化",
            ),
        })
        md = rep_mod.render_caps_markdown(rep_mod.collect_reports())

        assert md.startswith("# 能力自检报告")
        assert f"| `{_BROWSER_CAP}` | 不可用 |" in md
        assert "playwright install" in md

    def test_markdown_also_refuses_to_publish_unconfirmed_hints(self, monkeypatch):
        """上一轮实跑暴露的真 bug：Markdown 那条路也能把“看不清”的补法发出去。"""
        rep_mod = self._collect(monkeypatch, {
            "vision.detect": _report(
                "vision.detect", CapState.PROBE_FAILED,
                [_req(ReqState.PROBE_FAILED, kind="binary", label="某个命令",
                      evidence="探针超时", fix_hint="pip install 某个包")],
                title="实时视觉检测",
            ),
        })
        md = rep_mod.render_caps_markdown(rep_mod.collect_reports())

        assert "无法确认" in md
        assert "pip install 某个包" not in md


# ---------------------------------------------------------------------------
# E. 命令接线：/health caps 的分支与预告
# ---------------------------------------------------------------------------


class _CapUI:
    """记录 info/warn/error 的最小 UI。"""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warn(self, text: str) -> None:
        self.warns.append(text)

    def error(self, text: str) -> None:
        self.warns.append(f"ERROR:{text}")


def _cmd_ctx():
    return SimpleNamespace(ui=_CapUI(), settings=None, workdir=".")


class TestHealthCapsCommand:
    """命令层只该做分派与预告，排版归 report 层——这里测分派是否正确。"""

    async def test_plain_caps_runs_static_only(self, monkeypatch):
        from agent.commands.handlers import system_commands as sc

        seen: dict[str, object] = {}

        def _collect(*, deep: bool = False):
            seen["deep"] = deep
            return {}

        monkeypatch.setattr("agent.capabilities.report.collect_reports", _collect)
        monkeypatch.setattr("agent.capabilities.report.render_caps", lambda *_a, **_k: "静态报告")

        ctx = _cmd_ctx()
        assert await sc.handle_health(ctx, "/health caps") is True

        assert seen["deep"] is False
        assert "静态报告" in ctx.ui.infos[-1]

    async def test_deep_announces_the_wait_before_running(self, monkeypatch):
        from agent.commands.handlers import system_commands as sc

        order: list[str] = []

        def _collect(*, deep: bool = False):
            order.append(f"collect(deep={deep})")
            return {}

        monkeypatch.setattr("agent.capabilities.report.collect_reports", _collect)
        monkeypatch.setattr("agent.capabilities.report.render_caps", lambda *_a, **_k: "深档报告")

        ctx = _cmd_ctx()
        await sc.handle_health(ctx, "/health caps --deep")

        assert order == ["collect(deep=True)"]
        assert len(ctx.ui.infos) == 2, "必须先预告再跑：长静默会被当成卡死并中断"
        assert "深档自检" in ctx.ui.infos[0] and "一两分钟" in ctx.ui.infos[0]
        assert ctx.ui.infos[1] == "深档报告"

    async def test_target_name_routes_to_detail_view(self, monkeypatch):
        from agent.commands.handlers import system_commands as sc

        seen: dict[str, object] = {}

        def _render_one(name: str, *, deep: bool = False) -> str:
            seen["name"] = name
            seen["deep"] = deep
            return "详情"

        monkeypatch.setattr("agent.capabilities.report.collect_reports", lambda **_k: {})
        monkeypatch.setattr("agent.capabilities.report.render_one", _render_one)

        ctx = _cmd_ctx()
        await sc.handle_health(ctx, f"/health caps {_BROWSER_CAP}")

        assert seen == {"name": _BROWSER_CAP, "deep": False}
        assert ctx.ui.infos[-1] == "详情"

    async def test_json_flag_emits_machine_readable_payload(self, monkeypatch):
        from agent.commands.handlers import system_commands as sc

        rep = {_BROWSER_CAP: _report(
            _BROWSER_CAP, CapState.OK, [_req(ReqState.OK, label="x", evidence="y")],
        )}
        monkeypatch.setattr("agent.capabilities.report.collect_reports", lambda **_k: rep)

        ctx = _cmd_ctx()
        await sc.handle_health(ctx, "/health caps --json")

        assert '"capabilities"' in ctx.ui.infos[-1]
        assert _BROWSER_CAP in ctx.ui.infos[-1]
