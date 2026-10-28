"""巨类拆分的结构守卫（workbench engine.py / voice realtime_engine.py）。

两处巨类按职责拆成 mixin 模块后，最容易悄悄坏掉的是三件事：

1. 方法在主类与 mixin 各留一份 → 改一处不生效，拆分等于白做；
2. 阈值常量在新模块里又写一份 → 数值漂移（本项目已在厂商 base_url 上踩过这个坑）；
3. mixin 忘了挂回主类 → 运行时 AttributeError。

所以这里只做**结构断言**，不测行为：行为由 tests/ui、tests/serve、tests/voice 的既有用例
覆盖（拆分后全量 3632 passed，与拆分前逐条对齐）。另加一条 agent/ 目录的行数上限守卫，
防止巨类重新长回来。

@author aceFelix
"""

from __future__ import annotations

import pathlib

from agent.ui.workbench.engine import ChatEngine
from agent.ui.workbench.engine_session import EngineSessionMixin
from agent.ui.workbench.engine_status import EngineStatusMixin
from agent.ui.workbench.engine_talk import EngineTalkMixin
from agent.ui.workbench.engine_voice import EngineVoiceMixin
from agent.voice import realtime_const
from agent.voice.realtime_engine import RealtimeEngine
from agent.voice.realtime_recv import RealtimeRecvMixin
from agent.voice.realtime_rescue import RealtimeRescueMixin

ENGINE_MIXINS = (EngineStatusMixin, EngineSessionMixin, EngineTalkMixin, EngineVoiceMixin)
REALTIME_MIXINS = (RealtimeRecvMixin, RealtimeRescueMixin)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
# 项目规则是单文件 ≤800 行；存量的 main.py(826) / hub.py(814) 还没拆完，
# 先钉住 900 这条硬线防止继续恶化，等存量拆完再收到 800。
LINE_LIMIT = 900


def _own_methods(cls: type) -> set[str]:
    """类自身定义的方法/属性名（不含继承来的），用于判断「是否重复定义」。"""
    return {
        name
        for name, value in vars(cls).items()
        if callable(value) or isinstance(value, property)
    }


class TestMixinWiring:
    """mixin 必须真的挂回主类，且对外方法名一个都不能少。"""

    def test_chat_engine_mixins_attached(self) -> None:
        for mixin in ENGINE_MIXINS:
            assert mixin in ChatEngine.__mro__, mixin.__name__

    def test_realtime_engine_mixins_attached(self) -> None:
        for mixin in REALTIME_MIXINS:
            assert mixin in RealtimeEngine.__mro__, mixin.__name__

    def test_chat_engine_key_methods_reachable(self) -> None:
        # serve / 桌面壳直接点这些名字，拆完必须仍然可达
        for name in (
            "session_usage", "context_usage", "is_busy", "_ensure_session",
            "_handle_switch_model", "_handle_rename", "_handle_delete",
            "_handle_start_talk", "_handle_start_talk_duplex", "feed_talk_audio",
            "_handle_start_voice", "_handle_interrupt_voice",
        ):
            assert hasattr(ChatEngine, name), name

    def test_realtime_engine_key_methods_reachable(self) -> None:
        for name in ("_recv_events", "_end_response", "_response_rescue", "_watchdog"):
            assert hasattr(RealtimeEngine, name), name


class TestNoDuplicateDefinitions:
    """同一个方法只能有一处定义：主类里再抄一份就等于没拆。"""

    def test_chat_engine_does_not_redefine_mixin_methods(self) -> None:
        own = _own_methods(ChatEngine)
        for mixin in ENGINE_MIXINS:
            overlap = own & _own_methods(mixin)
            assert not overlap, f"{mixin.__name__} 与 ChatEngine 重复定义: {sorted(overlap)}"

    def test_realtime_engine_does_not_redefine_mixin_methods(self) -> None:
        own = _own_methods(RealtimeEngine)
        for mixin in REALTIME_MIXINS:
            overlap = own & _own_methods(mixin)
            assert not overlap, f"{mixin.__name__} 与 RealtimeEngine 重复定义: {sorted(overlap)}"

    def test_mixins_do_not_overlap_each_other(self) -> None:
        for group in (ENGINE_MIXINS, REALTIME_MIXINS):
            seen: dict[str, str] = {}
            for mixin in group:
                for name in _own_methods(mixin):
                    assert name not in seen, (
                        f"{name} 同时定义在 {seen.get(name)} 与 {mixin.__name__}"
                    )
                    seen[name] = mixin.__name__


class TestRealtimeConstantsSingleSource:
    """阈值只此一份：各模块拿到的必须是 realtime_const 里的同一个对象。"""

    def test_modules_share_the_same_constant_objects(self) -> None:
        from agent.voice import realtime_engine as engine_mod
        from agent.voice import realtime_recv as recv_mod
        from agent.voice import realtime_rescue as rescue_mod
        from agent.voice import realtime_talk as talk_mod

        names = [n for n in vars(realtime_const) if n.isupper()]
        # 常量表本身也要钉住，防止被误删后本用例变成空转
        assert {"INPUT_RATE", "OUTPUT_RATE", "MAX_RESPONSE_SECONDS", "RESCUE_HOLD_SECONDS"} <= set(names)
        for mod in (engine_mod, recv_mod, rescue_mod, talk_mod):
            for name in names:
                if hasattr(mod, name):
                    assert getattr(mod, name) is getattr(realtime_const, name), (
                        f"{mod.__name__}.{name} 不是 realtime_const 里的那一个（疑似各写了一份）"
                    )


class TestAgentFileLineLimit:
    def test_no_agent_file_exceeds_limit(self) -> None:
        over: list[str] = []
        for path in sorted((REPO_ROOT / "agent").rglob("*.py")):
            # 按 UTF-8 解码后数行：PowerShell Get-Content 对含中文的文件会吞掉行尾，口径不可信
            count = len(path.read_bytes().decode("utf-8", errors="replace").splitlines())
            if count > LINE_LIMIT:
                over.append(f"{path.relative_to(REPO_ROOT).as_posix()}={count}")
        assert not over, f"agent/ 下超过 {LINE_LIMIT} 行的文件: {over}"
