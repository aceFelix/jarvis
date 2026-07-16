"""LSP 工具 —— 模型可调用的代码智能工具。

9 种操作：
- goToDefinition: 跳转定义
- findReferences: 查引用
- hover: 悬停信息（类型/文档）
- documentSymbol: 文档符号
- workspaceSymbol: 工作区符号搜索
- goToImplementation: 跳转实现
- prepareCallHierarchy: 准备调用层次
- incomingCalls: 谁调用了我
- outgoingCalls: 我调用了谁
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool


_LSP_OPERATIONS = [
    "goToDefinition",
    "findReferences",
    "hover",
    "documentSymbol",
    "workspaceSymbol",
    "goToImplementation",
    "prepareCallHierarchy",
    "incomingCalls",
    "outgoingCalls",
]

_LSP_DESCRIPTION = """Interact with Language Server Protocol (LSP) servers to get code intelligence.

Supported operations:
- goToDefinition: Find where a symbol is defined
- findReferences: Find all references to a symbol
- hover: Get hover information (documentation, type info)
- documentSymbol: Get all symbols (functions, classes) in a document
- workspaceSymbol: Search for symbols across the workspace
- goToImplementation: Find implementations of an interface
- prepareCallHierarchy: Get call hierarchy item at a position
- incomingCalls: Find all functions that call the function at a position
- outgoingCalls: Find all functions called by the function at a position

All operations require:
- filePath: The file to operate on
- line: Line number (1-based)
- character: Character offset (1-based)

Note: LSP servers must be configured. If no server is available, an error is returned."""


class LSPTool(Tool):
    """代码智能工具——通过 LSP 获取定义/引用/类型等信息。"""

    name = "LSP"
    description = _LSP_DESCRIPTION
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": _LSP_OPERATIONS,
                "description": "The LSP operation to perform",
            },
            "filePath": {
                "type": "string",
                "description": "The absolute or relative path to the file",
            },
            "line": {
                "type": "number",
                "description": "The line number (1-based)",
            },
            "character": {
                "type": "number",
                "description": "The character offset (1-based)",
            },
        },
        "required": ["operation", "filePath"],
    }
    max_result_chars = 50_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("LSP 只读操作")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        op = args.get("operation", "")
        if op not in _LSP_OPERATIONS:
            return ValidationResult.fail(f"未知操作: {op}")
        if not args.get("filePath"):
            return ValidationResult.fail("filePath 不能为空")
        # workspaceSymbol 不需要 line/character
        if op != "workspaceSymbol":
            if not args.get("line") or not args.get("character"):
                return ValidationResult.fail(f"{op} 需要 line 和 character 参数")
        return ValidationResult.pass_()

    def get_path(self, args: dict[str, Any]) -> str:
        return args.get("filePath", "")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from agent.lsp.manager import get_lsp_manager
        from agent.lsp.client import path_to_uri
        from agent.tools.base import resolve_path

        manager = get_lsp_manager()
        if not manager:
            return ToolResult(data="LSP 未初始化。请在 settings.toml 中配置 [lsp] 节。")

        operation = args["operation"]
        raw_path = args["filePath"]

        # 解析路径
        file_path = str(resolve_path(ctx, raw_path))

        # 检查文件存在
        if not Path(file_path).exists():
            return ToolResult(data=f"文件不存在: {file_path}")

        # 检查是否有对应的 LSP server
        server_name = manager.get_server_name_for_file(file_path)
        if not server_name:
            ext = Path(file_path).suffix
            return ToolResult(data=f"无 LSP server 可处理 {ext} 文件。请在 settings.toml [lsp.servers] 配置。")

        # 确保文件已打开
        if not manager.is_file_open(file_path):
            try:
                await manager.open_file(file_path)
            except Exception as e:
                return ToolResult(data=f"打开文件失败: {e}")

        # 构造请求参数
        uri = path_to_uri(file_path)
        line = int(args.get("line", 1)) - 1  # 1-based → 0-based
        character = int(args.get("character", 1)) - 1

        method, params = _get_method_and_params(operation, uri, line, character)

        try:
            result = await manager.send_request(file_path, method, params)
        except Exception as e:
            return ToolResult(data=f"LSP 请求失败 ({operation}): {e}")

        if result is None:
            return ToolResult(data=f"无 LSP 结果（server 可能未就绪或操作不支持）")

        # incomingCalls / outgoingCalls 需要两步
        if operation in ("incomingCalls", "outgoingCalls"):
            call_items = result if isinstance(result, list) else []
            if not call_items:
                return ToolResult(data="该位置无调用层次项")

            call_method = (
                "callHierarchy/incomingCalls"
                if operation == "incomingCalls"
                else "callHierarchy/outgoingCalls"
            )
            try:
                result = await manager.send_request(
                    file_path, call_method, {"item": call_items[0]}
                )
            except Exception as e:
                return ToolResult(data=f"调用层次查询失败: {e}")

        # 格式化结果
        formatted = _format_result(operation, result, ctx.workdir)

        return ToolResult(data=formatted)


def _get_method_and_params(operation: str, uri: str, line: int, character: int):
    """映射 operation → LSP method + params。"""
    position = {"line": line, "character": character}
    text_document = {"uri": uri}

    if operation == "goToDefinition":
        return "textDocument/definition", {"textDocument": text_document, "position": position}
    if operation == "findReferences":
        return "textDocument/references", {
            "textDocument": text_document,
            "position": position,
            "context": {"includeDeclaration": True},
        }
    if operation == "hover":
        return "textDocument/hover", {"textDocument": text_document, "position": position}
    if operation == "documentSymbol":
        return "textDocument/documentSymbol", {"textDocument": text_document}
    if operation == "workspaceSymbol":
        return "workspace/symbol", {"query": ""}
    if operation == "goToImplementation":
        return "textDocument/implementation", {"textDocument": text_document, "position": position}
    if operation == "prepareCallHierarchy":
        return "textDocument/prepareCallHierarchy", {"textDocument": text_document, "position": position}
    if operation in ("incomingCalls", "outgoingCalls"):
        # 先 prepareCallHierarchy
        return "textDocument/prepareCallHierarchy", {"textDocument": text_document, "position": position}

    raise ValueError(f"未知操作: {operation}")


def _format_result(operation: str, result: Any, cwd: str) -> str:
    """格式化 LSP 结果为可读文本。"""
    from agent.lsp.client import uri_to_path

    if result is None:
        return "无结果"

    if operation in ("goToDefinition", "goToImplementation"):
        return _format_locations(result, cwd, "定义" if operation == "goToDefinition" else "实现")

    if operation == "findReferences":
        return _format_locations(result, cwd, "引用")

    if operation == "hover":
        return _format_hover(result)

    if operation == "documentSymbol":
        return _format_document_symbols(result)

    if operation == "workspaceSymbol":
        return _format_workspace_symbols(result, cwd)

    if operation == "prepareCallHierarchy":
        return _format_call_items(result, cwd)

    if operation == "incomingCalls":
        return _format_incoming_calls(result, cwd)

    if operation == "outgoingCalls":
        return _format_outgoing_calls(result, cwd)

    return str(result)


def _format_location(loc: dict, cwd: str) -> str:
    """格式化单个 Location。"""
    uri = loc.get("uri", loc.get("targetUri", ""))
    path = uri_to_path(uri) if uri else "<unknown>"

    # 转相对路径
    try:
        rel = os.path.relpath(path, cwd)
        if not rel.startswith(".."):
            path = rel
    except Exception:
        pass

    rng = loc.get("range", loc.get("targetRange", {}))
    start = rng.get("start", {})
    line = start.get("line", 0) + 1
    char = start.get("character", 0) + 1

    return f"  {path}:{line}:{char}"


def _format_locations(result: Any, cwd: str, label: str) -> str:
    """格式化 Location[] / LocationLink[]。"""
    if not result:
        return f"无{label}结果"

    items = result if isinstance(result, list) else [result]
    if not items:
        return f"无{label}结果"

    lines = [f"找到 {len(items)} 个{label}:"]
    for item in items:
        lines.append(_format_location(item, cwd))

    return "\n".join(lines)


def _format_hover(result: Any) -> str:
    """格式化 hover 结果。"""
    if not result:
        return "无 hover 信息"

    contents = result.get("contents", "")

    if isinstance(contents, dict):
        # MarkupContent: {kind, value}
        value = contents.get("value", "")
        return value if value else "无内容"
    elif isinstance(contents, list):
        # MarkedString[]
        parts = []
        for item in contents:
            if isinstance(item, dict):
                parts.append(item.get("value", str(item)))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    else:
        return str(contents)


_SYMBOL_KINDS = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
    20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}


def _format_document_symbols(result: Any) -> str:
    """格式化 documentSymbol 结果。"""
    if not result:
        return "无文档符号"

    symbols = result if isinstance(result, list) else [result]
    if not symbols:
        return "无文档符号"

    lines = [f"文档符号 ({len(symbols)} 个):"]
    for sym in symbols:
        name = sym.get("name", "?")
        kind = _SYMBOL_KINDS.get(sym.get("kind", 0), "Unknown")
        rng = sym.get("range", {}).get("start", {})
        line = rng.get("line", 0) + 1
        detail = sym.get("detail", "")
        detail_str = f" — {detail}" if detail else ""
        lines.append(f"  [{kind}] {name}{detail_str} (line {line})")

        # 递归子符号
        children = sym.get("children", [])
        for child in children:
            cname = child.get("name", "?")
            ckind = _SYMBOL_KINDS.get(child.get("kind", 0), "Unknown")
            crng = child.get("range", {}).get("start", {})
            cline = crng.get("line", 0) + 1
            lines.append(f"    [{ckind}] {cname} (line {cline})")

    return "\n".join(lines)


def _format_workspace_symbols(result: Any, cwd: str) -> str:
    """格式化 workspaceSymbol 结果。"""
    if not result:
        return "无工作区符号"

    symbols = result if isinstance(result, list) else [result]
    if not symbols:
        return "无工作区符号"

    # 限制数量
    max_symbols = 50
    truncated = len(symbols) > max_symbols
    symbols = symbols[:max_symbols]

    lines = [f"找到 {len(symbols)} 个符号{'（已截断）' if truncated else ''}:"]
    for sym in symbols:
        name = sym.get("name", "?")
        kind = _SYMBOL_KINDS.get(sym.get("kind", 0), "Unknown")
        loc = sym.get("location", {})
        container = sym.get("containerName", "")
        container_str = f" in {container}" if container else ""
        lines.append(f"  [{kind}] {name}{container_str}")
        if loc:
            lines.append(f"    {_format_location(loc, cwd).strip()}")

    return "\n".join(lines)


def _format_call_items(result: Any, cwd: str) -> str:
    """格式化 prepareCallHierarchy 结果。"""
    if not result:
        return "无调用层次项"

    items = result if isinstance(result, list) else [result]
    lines = [f"调用层次项 ({len(items)} 个):"]
    for item in items:
        name = item.get("name", "?")
        kind = _SYMBOL_KINDS.get(item.get("kind", 0), "Unknown")
        detail = item.get("detail", "")
        uri = item.get("uri", "")
        path = uri_to_path(uri) if uri else ""
        rng = item.get("range", {}).get("start", {})
        line = rng.get("line", 0) + 1
        detail_str = f" — {detail}" if detail else ""
        lines.append(f"  [{kind}] {name}{detail_str} at {path}:{line}")

    return "\n".join(lines)


def _format_incoming_calls(result: Any, cwd: str) -> str:
    """格式化 incomingCalls 结果。"""
    if not result:
        return "无入站调用"

    calls = result if isinstance(result, list) else [result]
    lines = [f"入站调用 ({len(calls)} 个——谁调用了这个函数):"]
    for call in calls:
        frm = call.get("from", {})
        name = frm.get("name", "?")
        kind = _SYMBOL_KINDS.get(frm.get("kind", 0), "Unknown")
        uri = frm.get("uri", "")
        path = uri_to_path(uri) if uri else ""
        rng = frm.get("range", {}).get("start", {})
        line = rng.get("line", 0) + 1
        # 调用位置
        from_ranges = call.get("fromRanges", [])
        count = len(from_ranges)
        lines.append(f"  [{kind}] {name} at {path}:{line} ({count} 处调用)")

    return "\n".join(lines)


def _format_outgoing_calls(result: Any, cwd: str) -> str:
    """格式化 outgoingCalls 结果。"""
    if not result:
        return "无出站调用"

    calls = result if isinstance(result, list) else [result]
    lines = [f"出站调用 ({len(calls)} 个——这个函数调用了谁):"]
    for call in calls:
        to = call.get("to", {})
        name = to.get("name", "?")
        kind = _SYMBOL_KINDS.get(to.get("kind", 0), "Unknown")
        uri = to.get("uri", "")
        path = uri_to_path(uri) if uri else ""
        rng = to.get("range", {}).get("start", {})
        line = rng.get("line", 0) + 1
        from_ranges = call.get("fromRanges", [])
        count = len(from_ranges)
        lines.append(f"  [{kind}] {name} at {path}:{line} ({count} 处调用)")

    return "\n".join(lines)
