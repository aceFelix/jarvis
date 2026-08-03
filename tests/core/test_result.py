"""工具执行结果与权限结果数据模型单元测试。

覆盖 PermissionResult / ValidationResult / ToolResult 的构造、快捷方法
与默认字段行为。

@author aceFelix
"""

from __future__ import annotations

from agent.core.message import ImageContent
from agent.core.result import (
    PermissionBehavior,
    PermissionResult,
    ToolResult,
    ValidationResult,
)


class TestPermissionBehavior:
    """权限三态枚举。"""

    def test_values(self) -> None:
        assert PermissionBehavior.ALLOW.value == "allow"
        assert PermissionBehavior.DENY.value == "deny"
        assert PermissionBehavior.ASK.value == "ask"


class TestPermissionResult:
    """权限判定结果。"""

    def test_allow(self) -> None:
        r = PermissionResult.allow("只读放行")
        assert r.behavior == PermissionBehavior.ALLOW
        assert r.reason == "只读放行"
        assert r.updated_input is None

    def test_allow_no_reason(self) -> None:
        r = PermissionResult.allow()
        assert r.behavior == PermissionBehavior.ALLOW
        assert r.reason is None

    def test_deny(self) -> None:
        r = PermissionResult.deny("危险命令")
        assert r.behavior == PermissionBehavior.DENY
        assert r.reason == "危险命令"

    def test_ask(self) -> None:
        r = PermissionResult.ask("需要确认")
        assert r.behavior == PermissionBehavior.ASK
        assert r.reason == "需要确认"

    def test_ask_defaults_to_none_reason(self) -> None:
        r = PermissionResult.ask()
        assert r.reason is None

    def test_updated_input_field(self) -> None:
        r = PermissionResult(behavior=PermissionBehavior.ALLOW, updated_input={"path": "/a"})
        assert r.updated_input == {"path": "/a"}


class TestValidationResult:
    """输入合法性校验结果。"""

    def test_pass(self) -> None:
        r = ValidationResult.pass_()
        assert r.ok is True
        assert r.message == ""

    def test_fail(self) -> None:
        r = ValidationResult.fail("command 不能为空")
        assert r.ok is False
        assert r.message == "command 不能为空"


class TestToolResult:
    """工具执行结果。"""

    def test_default_fields(self) -> None:
        r = ToolResult()
        assert r.data is None
        assert r.new_messages == []
        assert r.is_error is False
        assert r.images == []

    def test_ok(self) -> None:
        r = ToolResult.ok("done")
        assert r.data == "done"
        assert r.is_error is False
        assert r.images == []

    def test_ok_with_images(self) -> None:
        img = ImageContent(data="abc")
        r = ToolResult.ok("screenshot", images=[img])
        assert r.images == [img]

    def test_ok_with_empty_images_list(self) -> None:
        r = ToolResult.ok("x", images=[])
        assert r.images == []

    def test_error(self) -> None:
        r = ToolResult.error("boom")
        assert r.data == "boom"
        assert r.is_error is True

    def test_new_messages_field(self) -> None:
        """new_messages 用于工具产生的副作用消息。"""
        r = ToolResult(data="ok", new_messages=["extra"])
        assert r.new_messages == ["extra"]
