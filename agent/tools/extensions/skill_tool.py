"""LoadSkill 工具 —— 按需加载技能包完整指令。

system prompt 里只注入 skill 摘要（名字+描述），省 60k+ token。
LLM 看到摘要后，需要某个 skill 的详细指令时调用 LoadSkill 加载完整正文。

配合 skills.py 的 match_skills_for_message()（触发词自动匹配）形成双保险：
- 有 trigger_words 的 skill → 自动匹配加载
- 没有 trigger_words 的 skill → LLM 主动调 LoadSkill 加载

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import ToolResult
from agent.core.tool import Tool


class LoadSkillTool(Tool):
    """加载技能包的完整指令。

    当你需要某个技能的详细操作规范时，传入技能名加载完整正文。
    技能名在 system prompt 的"可用技能"摘要列表里列出。
    """

    name = "LoadSkill"
    description = (
        "加载技能包的完整指令。当你需要某个技能的详细操作规范时，"
        "传入技能名（在可用技能摘要列表里）加载完整正文。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "要加载的技能名（system prompt 技能摘要列表里的名字）",
            },
        },
        "required": ["skill_name"],
    }

    # 核心工具，始终携带（不被 ToolSearch 延迟加载）
    deferred = False

    def __init__(self, workdir: str) -> None:
        self._workdir = workdir

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        skill_name = (args.get("skill_name") or "").strip()
        if not skill_name:
            return ToolResult.error("请提供技能名（skill_name 参数）")

        from agent.core.extensions.skills import load_skills

        skills = load_skills(self._workdir)
        for s in skills:
            if s.name == skill_name or s.name.lower() == skill_name.lower():
                content = f"# 技能: {s.name}\n"
                if s.description:
                    content += f"**描述**: {s.description}\n"
                if s.when_to_use:
                    content += f"**使用时机**: {s.when_to_use}\n"
                content += f"\n{s.content}"
                return ToolResult.ok(content)

        # 未找到，列出可用技能名供参考
        available = ", ".join(s.name for s in skills) if skills else "（无已安装技能）"
        return ToolResult.error(f"未找到技能「{skill_name}」。可用技能: {available}")

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True
