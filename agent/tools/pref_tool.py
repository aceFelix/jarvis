"""`Preference` 工具 —— 让模型记下"带条件的以后"（阶段 12 P3）。

与 `ConfigSet` 的分工是**一次性 vs 带条件**：

| 用户说的话 | 该用哪个 |
|---|---|
| 「太吵了，音量小一点」 | `ConfigSet`（现在就是 30，永远 30） |
| 「以后我写代码时别念出来」 | `Preference`（只有前台是编辑器时不出声）|

为什么必须分开而不是给 `ConfigSet` 加个 `when` 参数：两者的失败方式完全不同。
改错配置是"值不对"，删一条就好；记错偏好是"以后某个场合行为不对"，而那一刻
用户往往已经忘了自己说过什么——所以偏好必须有原话存档、可列、可删，且
**只能落在 auto 域**（见 `agent/config/prefs.py` 的边界说明）。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.config import prefs as prefs_mod
from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


def _why_not_hit(pref: Any) -> str:
    """说清"当时为什么没命中"——把当前上下文摊开，用户才能看出条件写错了。

    只看结果说"不命中"是没用的信息：用户写的是 `Code.exe`、而他实际在用
    `Qoder.exe`，光看"不命中"永远想不通。把两边都写出来，他一眼就能发现。

    @author aceFelix
    """
    try:
        ctx = prefs_mod.current_context()
    except Exception:  # noqa: BLE001 - 取不到上下文只说结果
        return "当前不命中"
    app = str(ctx.get("app") or "")
    hour = ctx.get("hour")
    where = f"当前前台是 {app}" if app else "当前探测不到前台程序（无 GUI 会话？）"
    return f"当前不命中：{where}，{hour} 点；条件写的是「{pref.when.describe()}」"


def _render_list(items: list[Any], suggested: list[Any] | None = None) -> str:
    """把偏好列成人话（模型与 `/prefs` 共用同一份口径）。"""
    parts: list[str] = []
    if suggested:
        lines = [f"待确认候选 {len(suggested)} 条（**还没生效**）："]
        for pref in suggested:
            lines.append(f"- {pref.id}｜{pref.describe()}（依据：{pref.said or '无'}）")
        lines.append("用户点头后用 action=accept 接受；不要就用 action=forget 拒掉。")
        parts.append("\n".join(lines))
    if items:
        lines = [f"已生效的偏好 {len(items)} 条（新→旧）："]
        for pref in sorted(items, key=lambda p: p.created_at, reverse=True):
            said = f"  原话：「{pref.said}」" if pref.said else ""
            lines.append(f"- {pref.id}｜{pref.describe()}（命中 {pref.hits} 次）{said}")
        parts.append("\n".join(lines))
    if parts:
        return "\n".join(parts)
    return (
        "还没有记过任何偏好。用户说「以后…」「每次…」「当我…时」这类话才记；"
        "一次性的调整请直接用 ConfigSet。"
    )


class PreferenceTool(Tool):
    """三条动作：list / remember / forget。"""

    name = "Preference"
    description = (
        "记住或忘掉**带条件的**偏好（如『以后我写代码时别念出来』）。"
        "只有当用户明确说了『以后』『每次』『当我…时』才用；一次性的调整请用 ConfigSet。"
        "action=list 可查看已记住的偏好与待确认候选；用户说『好，记下来』时用 action=accept。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "remember", "forget", "accept"],
                "description": "list=列出现有偏好与候选；remember=记一条；accept=接受一条候选；forget=忘掉/拒掉一条",
            },
            "key": {
                "type": "string",
                "description": "配置键（与 ConfigSet 同名）；remember 必填，且只能记『可直接改』的项",
            },
            "value": {
                "type": "string",
                "description": "偏好值（remember 必填）：布尔写 true/false，数字写数值，名单用逗号分隔",
            },
            "when_app": {
                "type": "string",
                "description": (
                    "条件一：前台程序名（不分大小写，含即算）。多个用逗号分隔，"
                    "如 Code.exe,Qoder.exe,idea64.exe——用户说『写代码时』通常不止一个编辑器"
                ),
            },
            "when_workdir": {
                "type": "string",
                "description": "条件二：工作目录（前缀匹配，如 E:/proj）",
            },
            "when_hours": {
                "type": "string",
                "description": "条件三：时间段，如『22-7』（含端点，支持跨午夜）",
            },
            "said": {
                "type": "string",
                "description": "用户原话（存档，便于日后追溯『为什么这样』）",
            },
            "id": {
                "type": "string",
                "description": "forget / accept 用：偏好 id 或前缀（list 里能看到）",
            },
        },
        "required": ["action"],
    }
    max_result_chars = 4_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return str(args.get("action") or "").strip().lower() == "list"

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return self.is_read_only(args)

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        action = str(args.get("action") or "").strip().lower()
        if action not in ("list", "remember", "forget", "accept"):
            return ValidationResult.fail("action 只能是 list / remember / accept / forget")
        if action == "remember":
            if not str(args.get("key") or "").strip():
                return ValidationResult.fail("remember 需要 key")
            if "value" not in args:
                return ValidationResult.fail("remember 需要 value")
            if not any(args.get(k) for k in ("when_app", "when_workdir", "when_hours")):
                # 无条件偏好就是改配置，不该走这条路——否则"记一条偏好"会变成
                # 偷偷永久改配置，而且不入账本、不可 /undo。
                return ValidationResult.fail(
                    "带条件的偏好至少要给一个条件（when_app / when_workdir / when_hours）；"
                    "无条件地长期改，请直接用 ConfigSet（那会入账本、可 /undo）"
                )
        if action in ("forget", "accept") and not str(args.get("id") or "").strip():
            return ValidationResult.fail(f"{action} 需要 id")
        return ValidationResult.pass_()

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """只用 auto 域键（由 prefs.remember 兜底），不涉及护栏，故放行。"""
        if self.is_read_only(args):
            return PermissionResult.allow("查看偏好")
        return PermissionResult.allow("偏好只能落表达层配置，且可随时忘掉")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        action = str(args.get("action") or "").strip().lower()
        try:
            if action == "list":
                return ToolResult.ok(
                    _render_list(prefs_mod.load_active(), prefs_mod.load_suggested())
                )

            if action == "accept":
                accepted = prefs_mod.accept(str(args.get("id") or ""))
                if accepted is None:
                    return ToolResult.error("没找到这条候选（用 action=list 看现有 id）")
                return ToolResult.ok(f"已接受并生效：{accepted.describe()}")

            if action == "forget":
                target = prefs_mod.forget(str(args.get("id") or ""))
                if target is None:
                    return ToolResult.error("没找到这条偏好（用 action=list 看现有 id）")
                return ToolResult.ok(f"已忘掉：{target.describe()}")

            when = self._parse_when(args)
            pref = prefs_mod.remember(
                str(args.get("key") or ""),
                self._parse_value(str(args.get("key") or ""), args.get("value")),
                when=when,
                said=str(args.get("said") or ""),
            )
        except ValueError as e:
            return ToolResult.error(str(e))

        hit_now = "现在正好命中这条条件，已立即生效" if pref.when.matches(
            prefs_mod.current_context()
        ) else _why_not_hit(pref)
        return ToolResult.ok(
            f"已记住：{pref.describe()}（{hit_now}；id {pref.id}）\n"
            f"以后想取消就说「忘掉这条」或让我用 Preference forget {pref.id}。"
        )

    @staticmethod
    def _parse_value(key: str, raw: Any) -> Any:
        """模型给的值按协议键的种类收敛（随后 prefs.remember 还会校验一遍）。"""
        from agent.config.config_specs import get_spec

        spec = get_spec(key)
        if spec is None:
            return raw
        if spec.kind == "bool":
            if isinstance(raw, bool):
                return raw
            text = str(raw).strip().lower()
            if text in ("true", "1", "on", "开", "是", "yes"):
                return True
            if text in ("false", "0", "off", "关", "否", "no"):
                return False
            return raw  # 交给 validate_value 报统一错误
        if spec.kind in ("int", "float"):
            try:
                return int(raw) if spec.kind == "int" else float(raw)
            except (TypeError, ValueError):
                return raw
        if spec.kind == "list" and isinstance(raw, str):
            return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        return raw

    @staticmethod
    def _parse_when(args: dict[str, Any]) -> prefs_mod.When:
        hour_from = hour_to = -1
        raw_hours = str(args.get("when_hours") or "").strip()
        if raw_hours:
            lo, _, hi = raw_hours.partition("-")
            try:
                hour_from = int(lo.strip())
                hour_to = int(hi.strip()) if hi.strip() else hour_from
            except ValueError as e:
                raise ValueError(f"时间段看不懂：{raw_hours}（应写成 22-7 这种）") from e
        return prefs_mod.When(
            app=str(args.get("when_app") or "").strip(),
            workdir=str(args.get("when_workdir") or "").strip(),
            hour_from=hour_from,
            hour_to=hour_to,
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        action = str((args or {}).get("action") or "").strip().lower()
        if action == "remember":
            key = str((args or {}).get("key") or "")
            return f"记住偏好 {key}" if key else "记住偏好"
        if action == "forget":
            return "忘掉一条偏好"
        return "查看偏好"
