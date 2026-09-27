"""tool_use / tool_result 配对修复 —— 维持对话历史的协议不变量。

背景（aceFelix）：
LLM 协议要求历史里每个 assistant 的 tool_use 必须在**紧随的下一条消息**里
有配对的 tool_result，且每个 tool_result 都要有对应的 tool_use。违反时
Anthropic 兼容端点（如 DeepSeek）会在受理前直接拒收整个请求：

    messages.6: `tool_use` ids were found without `tool_result` blocks immediately after

一旦残缺历史被持久化，该会话每次请求都携带它 → 永久性失败，用户重试、
换措辞、换模型全部无效（请求根本没进模型）。四条会产生悬空调用的路径：

1. 工具执行中被用户中断（query_loop 未补结果就 break）
2. 输出被 max_tokens 截断且已含 tool_use（continue 后续写，工具没执行）
3. 工具执行期抛非取消类异常（assistant 已入历史，结果缺失）
4. 编排器调度异常导致部分调用拿不到结果

本模块提供两道保障：
- make_placeholder_results：为未回收的调用合成占位结果（源头补齐用）
- ensure_tool_pairing：发送前扫描并修复消息序列（出口兜底，可自愈旧脏历史）

关键约束：ensure_tool_pairing 只作用于「发送副本」，**绝不就地修改**传入的
消息对象——分层上下文的冻结前缀被 cache_control 锁定，就地改动会让 prompt
缓存全部失效。仅当某条消息确需修改时才重建它。

@author aceFelix
"""

from __future__ import annotations

from agent.core.message import Message, ToolResultContent, ToolUseContent

# 占位结果文案：让模型明确知道"这次调用没有结果，需要就重新发起"
# is_error=True 保证模型按失败处理，而不是把占位文本当成真实工具输出。
_PLACEHOLDER_TEMPLATE = (
    "[系统补充] 工具 {name} 的调用没有返回结果（{reason}）。"
    "该调用已失效，如需完成此步骤请重新发起调用。"
)

# 常见补齐原因（供各调用点复用，保证文案口径一致）
REASON_ABORTED = "会话被用户中断"
REASON_TRUNCATED = "输出被截断，工具未执行"
REASON_EXEC_ERROR = "工具执行抛出异常"
REASON_UNRECOVERED = "上一条回复未回收结果"
REASON_ORPHAN = "编排器未产出结果"


def make_placeholder_result(use: ToolUseContent, *, reason: str) -> ToolResultContent:
    """为单个未回收的 tool_use 合成占位结果。

    Args:
        use: 未被执行的工具调用块。
        reason: 补齐原因（写入占位文案，建议使用本模块的 REASON_* 常量）。

    Returns:
        is_error=True 的 ToolResultContent，id 与调用配对。
    """
    return ToolResultContent(
        tool_use_id=use.id,
        content=_PLACEHOLDER_TEMPLATE.format(name=use.name, reason=reason),
        is_error=True,
    )


def make_placeholder_results(
    uses: list[ToolUseContent], *, reason: str
) -> list[ToolResultContent]:
    """为一组未回收的 tool_use 批量合成占位结果（保持输入顺序）。"""
    return [make_placeholder_result(u, reason=reason) for u in uses]


def _placeholder_message(uses: list[ToolUseContent], *, reason: str) -> Message:
    """构造只含占位结果的新 user 消息（用于下一条消息不是 user 的场景）。"""
    return Message(
        role="user",
        content=make_placeholder_results(uses, reason=reason),
    )


def _rebuild_user_message(msg: Message, content: list) -> Message:
    """用新 content 重建 user 消息（保留原 id / timestamp，便于日志与持久化比对）。"""
    return Message(role=msg.role, content=content, id=msg.id, timestamp=msg.timestamp)


def ensure_tool_pairing(messages: list[Message]) -> list[Message]:
    """扫描并修复 tool_use / tool_result 配对，返回可安全发送的消息序列。

    修复规则：
    - 悬空 tool_use → 补占位结果：优先并入紧随的下一条 user 消息（占位块放在
      该消息 content 最前面，Anthropic 要求 tool_result 前置），下一条不是
      user 时新建一条只含占位结果的 user 消息（避免出现连续两条 user 消息）
    - 孤儿 tool_result（引用的 tool_use 不存在）→ 丢弃
    - 重复 tool_result（同一 id 出现多次）→ 只保留第一次
    - 修复后 content 为空的消息 → 丢弃（空 content 会被 API 拒绝）

    完全健康的历史会原样返回（同一列表对象，零拷贝）；只有确需改动的消息
    才重建副本，原消息对象始终不被修改。
    """
    if not messages:
        return messages

    out: list[Message] = []
    use_by_id: dict[str, ToolUseContent] = {}   # 已出现的 tool_use（判定孤儿用）
    seen_results: set[str] = set()              # 已采纳的 tool_result id（去重）
    pending: list[ToolUseContent] = []          # 上一条 assistant 中尚未配对的调用
    changed = False

    for msg in messages:
        if msg.role == "assistant":
            # 上一条 assistant 的结果还没等到就撞上新 assistant：
            # 先补一条占位消息，否则那批调用永远悬空。
            if pending:
                out.append(_placeholder_message(pending, reason=REASON_UNRECOVERED))
                pending = []
                changed = True
            uses_here: list[ToolUseContent] = []
            for b in msg.content:
                if isinstance(b, ToolUseContent):
                    # 同一 id 重复出现时以第一次为准（后续不再登记）
                    use_by_id.setdefault(b.id, b)
                    uses_here.append(use_by_id[b.id])
            out.append(msg)
            pending = uses_here
            continue

        if msg.role != "user":
            # system 等角色不参与配对（转换时会被单独提取）
            out.append(msg)
            continue

        original_results = [b for b in msg.content if isinstance(b, ToolResultContent)]
        kept: list = []
        results_here: list[ToolResultContent] = []
        for b in msg.content:
            if isinstance(b, ToolResultContent):
                if b.tool_use_id in use_by_id and b.tool_use_id not in seen_results:
                    seen_results.add(b.tool_use_id)
                    results_here.append(b)
                else:
                    # 孤儿（无对应调用）或重复 → 丢弃
                    changed = True
                continue
            kept.append(b)

        missing = [u for u in pending if u.id not in seen_results]
        pending = []
        for u in missing:
            seen_results.add(u.id)
            results_here.append(make_placeholder_result(u, reason=REASON_UNRECOVERED))

        if missing or results_here != original_results:
            new_content = results_here + kept
            if new_content:
                out.append(_rebuild_user_message(msg, new_content))
            # 修复后为空的消息直接丢弃，避免发送空 content
            changed = True
            continue

        out.append(msg)

    # 历史以 assistant(tool_use) 结尾：补一条占位消息收尾
    if pending:
        out.append(_placeholder_message(pending, reason=REASON_UNRECOVERED))
        changed = True

    return out if changed else messages
