"""感知事件口播版单测 —— `percept/speech.py` 的"给人耳听"改写。

验收口径（2026-10 人工走查反馈）：播报不能再把 `explorer.exe`、完整路径和
冒号逐字符念出来；同时**留痕必须保持原样**——口播是派生视图，不是新事实。

@author aceFelix
"""

from __future__ import annotations

from agent.core.percept.event import PerceptEvent
from agent.core.percept.speech import friendly_app, spoken_summary


def _window_event(kind: str = "focus_changed", **payload: object) -> PerceptEvent:
    """构造一条窗口事件（payload 形状与真实源保持一致）。"""
    data = {"summary": "切到 explorer.exe：C:\\Users\\me\\.jarvis", **payload}
    return PerceptEvent(source="window", kind=kind, payload=data, salience_hint=0.3)


def test_friendly_app_maps_and_falls_back():
    """命中表用中文名；未命中只剥 `.exe`，不假装认识。"""
    assert friendly_app("explorer.exe") == "资源管理器"
    assert friendly_app("mintty.exe") == "终端"
    assert friendly_app("Qoder CN IDE.exe") == "Qoder"
    assert friendly_app("ShellExperienceHost.exe") == "系统面板"
    assert friendly_app("SomeObscureTool.exe") == "SomeObscureTool"
    assert friendly_app("") == ""


def test_focus_changed_drops_exe_and_path():
    """切屏播报：应用用中文名，路径只念末段，不留 `.exe` 与反斜杠。"""
    spoken = spoken_summary(
        _window_event(process="explorer.exe", title="C:\\Users\\me\\.jarvis")
    )
    assert spoken == "切到 资源管理器，jarvis 文件夹。"


def test_focus_changed_title_keeps_doc_name_only():
    """标题只取第一段文档名（"X - 工作区 - 应用" 的尾巴对耳朵是噪音）。"""
    spoken = spoken_summary(
        _window_event(
            process="Qoder CN IDE.exe",
            title="BLUEPRINT.md - J.A.R.V.I.S - Qoder CN ID…",
        )
    )
    assert spoken == "切到 Qoder，BLUEPRINT。"


def test_terminal_prompt_title_is_skipped():
    """终端提示符样式标题没有播报价值，整段省略。"""
    spoken = spoken_summary(
        _window_event(
            process="mintty.exe",
            title="MINGW64:/e/2.MyProjects/MyAgentChat/J.A.…",
        )
    )
    assert spoken == "切到 终端。"


def test_linger_uses_minutes_and_app():
    """停留超时念成完整一句，带应用名与分钟数。"""
    spoken = spoken_summary(
        _window_event(
            kind="focus_linger", process="chrome.exe", minutes=40, summary="在 chrome 待久了"
        )
    )
    assert spoken == "你在 Chrome 连续待了 40 分钟了。"


def test_dir_grew_omits_file_samples():
    """目录堆积只报数量：耳朵记不住五个文件名，屏幕上照旧有。"""
    spoken = spoken_summary(
        PerceptEvent(
            source="file",
            kind="dir_grew",
            payload={
                "summary": "Work 目录新增 5 个文件（如 a.html、b.html）",
                "dir_name": "Work",
                "added": 5,
                "samples": ["a.html", "b.html"],
            },
            salience_hint=0.55,
        )
    )
    assert spoken == "Work 里堆了 5 个新文件。"


def test_prose_sources_pass_through():
    """VCS 等源的摘要本就是散文，不套模板，只做读法归一。"""
    spoken = spoken_summary(
        PerceptEvent(
            source="vcs",
            kind="repo_dirty_grew",
            payload={"summary": "jarvis 有 8 项未提交变更（分支 main）"},
            salience_hint=0.5,
        )
    )
    assert spoken == "jarvis 有 8 项未提交变更（分支 main）。"


def test_record_is_not_rewritten():
    """口播不改留痕：`summary()` 与 payload 必须保持原样（可查可回放）。"""
    event = _window_event(process="explorer.exe", title="C:\\Users\\me\\.jarvis")
    before = dict(event.payload)
    spoken_summary(event)
    assert event.payload == before
    assert event.summary() == "切到 explorer.exe：C:\\Users\\me\\.jarvis"


def test_missing_payload_fields_degrade_gracefully():
    """字段缺失不抛异常，且**播报不能比留痕更模糊**：没结构化字段就退回摘要。"""
    bare = spoken_summary(PerceptEvent(source="window", kind="focus_changed", payload={}))
    assert "focus_changed" in bare          # 退回 summary()，而不是“切到别的应用”
    # 只有标题没有进程名：语法仍要成立
    only_title = spoken_summary(PerceptEvent(
        source="window", kind="focus_changed",
        payload={"summary": "x", "title": "BLUEPRINT.md - W - Q"},
    ))
    assert only_title == "切到 另一个窗口，BLUEPRINT。"
    # file 源缺 dir_name 同样退回摘要
    assert spoken_summary(PerceptEvent(source="file", kind="dir_grew",
                                       payload={"summary": "下载堆了 9 个"})) \
        == "下载堆了 9 个。"
