"""CLI-Anything harness 数据模型。

定义 harness 及其参数的 schema，用于从 SKILL.md 解析后承载结构化数据。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HarnessArg:
    """harness 命令参数定义。

    Attributes:
        name: 参数名（英文，会作为命令行参数 --<name> 传递）。
        type: JSON Schema 类型，如 string / integer / number / boolean / array。
        description: 参数用途说明，会展示给 LLM。
        required: 是否必填。
        enum: 可选的枚举值列表。
        default: 默认值。
    """

    name: str
    type: str
    description: str
    required: bool = False
    enum: list[str] | None = None
    default: Any | None = None


@dataclass
class Harness:
    """一个 CLI-Anything harness 的内存表示。

    Attributes:
        id: harness 唯一标识，会作为工具名 ``cli_anything__<id>`` 的一部分。
        name: 软件显示名。
        description: 简短能力描述。
        command: 执行入口命令，如 python / node / 可执行文件路径。
        args: 参数定义列表。
        when_to_use: 触发场景说明，注入 system prompt。
        trigger_words: 关键词列表，用于技能匹配。
        examples: 使用示例，注入 system prompt。
        dir_path: harness 所在目录，执行命令时会传给入口作为上下文。
    """

    id: str
    name: str
    description: str
    command: str
    args: list[HarnessArg] = field(default_factory=list)
    when_to_use: str = ""
    trigger_words: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    dir_path: Path | None = None
