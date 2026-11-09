"""活动台账多观察者 + TeeActivityUI 测试（C3-lite.2）。

覆盖三件事（aceFelix，2026-11-08）：
1. observer 与桌面 listener 并存，不改变 ``has_listener`` 语义；
2. observer 异常互相隔离，不能反噬台账写入；
3. ``TeeActivityUI`` 先转发终端 UI、再记账（保留原始输出的前提）。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.collaboration.activity import (
    AgentActivityRegistry,
    TeeActivityUI,
    get_activity_registry,
)


def test_observer_receives_snapshot_alongside_listener() -> None:
    """listener 与 observer 都收到同一份快照，且 observer 不改变 has_listener。"""
    reg = AgentActivityRegistry()
    listener_rows: list[list[dict[str, Any]]] = []
    observer_rows: list[list[dict[str, Any]]] = []
    reg.set_listener(listener_rows.append)

    reg.add_observer(observer_rows.append)
    assert reg.has_card_observer() is True
    assert reg.has_listener() is True

    reg.start_agent("sub-1", "explorer-1", role="explorer")
    assert len(listener_rows) == 1
    assert len(observer_rows) == 1
    assert observer_rows[0][0]["agent_id"] == "sub-1"

    reg.remove_observer(observer_rows.append)
    assert reg.has_card_observer() is False


def test_observer_without_listener_keeps_terminal_raw_output() -> None:
    """终端只注册 observer：``has_listener`` 仍为 False，子代理原始输出不被收走。"""
    reg = AgentActivityRegistry()
    reg.add_observer(lambda rows: None)
    assert reg.has_listener() is False


def test_broken_observer_does_not_block_others_or_writes() -> None:
    """一个 observer 抛异常：其它 observer 照收，台账写入不受影响。"""

    def boom(_rows: list[dict[str, Any]]) -> None:
        raise RuntimeError("observer boom")

    reg = AgentActivityRegistry()
    good: list[list[dict[str, Any]]] = []
    reg.add_observer(boom)
    reg.add_observer(good.append)

    reg.start_agent("sub-2", "coder-1", role="coder")  # 不抛
    assert len(good) == 1
    assert reg.snapshot()[0]["agent_id"] == "sub-2"

    reg.remove_observer(boom)


def test_tee_ui_forwards_then_records() -> None:
    """Tee：正文/思考按整段缓冲，工具事件先冲刷再转发，台账实时更新。"""

    class BaseUI:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Any]] = []
            self.texts: list[str] = []

        def tool_use(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None:
            self.calls.append(("tool_use", tool_name))

        def tool_result(
            self, tool_name: str, result: str, is_error: bool = False, tool_use_id: str = ""
        ) -> None:
            self.calls.append(("tool_result", tool_name))

        def assistant_text(self, text: str) -> None:
            self.texts.append(text)

        def assistant_thinking(self, text: str) -> None:  # 不应被 Tee 调用
            self.calls.append(("assistant_thinking", text))

        def info(self, text: str) -> None:
            self.calls.append(("info", text))

        def ask_user(self, prompt: str) -> str:
            self.calls.append(("ask_user", prompt))
            return "ok"

    reg = get_activity_registry()
    reg.clear()
    reg.start_agent("sub-tee", "explorer-1", role="explorer")

    base = BaseUI()
    ui = TeeActivityUI(base, "sub-tee", "explorer-1")

    ui.assistant_thinking("想一想")
    assert base.calls == []  # 思考不喂共享面板，只进台账
    assert reg.snapshot()[0]["status"] == "thinking"

    ui.tool_use("Grep", {"pattern": "TODO"}, "t1")
    # tool_use 前先冲刷思考块（带代理名前缀），再画工具卡
    assert [name for name, _ in base.calls] == ["info", "tool_use"]
    assert "explorer-1" in base.calls[0][1] and "想一想" in base.calls[0][1]

    ui.tool_result("Grep", "找到 12 处", False, "t1")

    # 正文分片只累积，不逐片写终端（防多代理表格逐列交错）
    ui.assistant_text("结论：")
    ui.assistant_text("12 处 TODO")
    assert base.texts == []
    assert "结论" in reg.snapshot()[0]["output"]

    ui.flush()
    assert base.texts == ["结论：12 处 TODO\n"]
    assert reg.snapshot()[0]["current_tool"] == ""
    assert reg.snapshot()[0]["activity"]

    # 未覆盖的方法仍转发宿主（子代理需要用户输入时不能静默）
    assert ui.ask_user("确认吗") == "ok"
    assert base.calls[-1] == ("ask_user", "确认吗")


def test_tee_ui_flushes_text_before_tool_card() -> None:
    """正文未结束就进入工具调用：必须先输出整段正文，再弹工具卡。"""

    class BaseUI:
        def __init__(self) -> None:
            self.events: list[str] = []

        def assistant_text(self, text: str) -> None:
            self.events.append(f"text:{text}")

        def tool_use(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None:
            self.events.append(f"tool:{tool_name}")

        def tool_result(
            self, tool_name: str, result: str, is_error: bool = False, tool_use_id: str = ""
        ) -> None:
            self.events.append(f"result:{tool_name}")

    reg = get_activity_registry()
    reg.clear()
    reg.start_agent("sub-tee-2", "coder-1", role="coder")

    base = BaseUI()
    ui = TeeActivityUI(base, "sub-tee-2", "coder-1")
    ui.assistant_text("先读文件")
    ui.tool_use("FileRead", {"path": "a.py"}, "t1")

    assert base.events == ["text:先读文件\n", "tool:FileRead"]
