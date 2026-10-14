"""本轮发给模型的工具 schema 决策（纯函数）。

从 `query_loop.py` 抽出，原因有二：

1. **文件行数上限**：QueryLoop 主循环本体已接近 800 行上限，工具下发策略
   与循环控制是两件事，分家后各自单一职责（见 code-structure 规则）。
2. **可独立测试**：这段逻辑的回归全靠「哪些词该发工具、哪些工具该延迟」，
   与 LLM 流式、上下文压缩无关，独立成模块后测试不必再构造整个 QueryLoop。

两条策略（2026-09-30 桌面端复盘后确立的口径）：

- **延迟加载分组**：核心工具（``deferred=False``）始终携带完整 schema；
  延迟工具（MCP / harness / GUI 等，``deferred=True``）只有在被 ToolSearch
  发现、写进 ``ctx.extra["discovered_tools"]`` 之后才携带 schema，否则只出现在
  系统提示的名字摘要里 —— 省 token 的关键。
- **纯聊天零工具**：短问句 + 无动作意图 + 历史无工具调用 → 本轮一个工具都不发
  （0 token）。误判的代价是「模型想调工具却拿不到 schema」，所以关键词表
  **宁全勿缺**，详见 docs/fixlogs/deepseek-dsml-toolcall-leak-fix.md。

@author aceFelix
"""

from __future__ import annotations

from agent.core.context import ToolContext
from agent.core.tool import ToolRegistry
from agent.llm.base import ToolDef

# 动作关键词（中英文）：含任一则认为有工具意图。
# 分四类，逐类都是「漏判即 0 工具、会话当场卡死」换来的教训堆出来的，
# 删词前务必先跑 tests/test_query_loop_stream.py。
ACTION_KEYWORDS: tuple[str, ...] = (
    # 动作词 — 明确要求执行操作
    "帮我", "查", "搜", "打开", "写", "创建", "运行", "执行", "安装",
    "删除", "修改", "编辑", "发送", "截图", "点击", "输入", "下载",
    "上传", "连接", "启动", "停止", "关闭", "设置", "配置",
    "help", "search", "open", "write", "create", "run", "exec",
    "install", "delete", "edit", "send", "screenshot", "click",
    "download", "upload", "connect", "start", "stop", "close",
    "文件", "命令", "终端", "浏览器", "网页", "邮件", "日程", "提醒",
    # 信息查询词 — 短问句可能需工具获取实时数据
    "几点", "几号", "多少", "天气", "温度", "湿度", "时间", "日期",
    "星期", "今天", "明天", "昨天", "今年", "版本", "日历",
    "how", "what", "when", "who", "where", "which", "why",
    "time", "date", "today", "weather", "version", "calendar",
    # 工作区/代码库词 —— 「当前目录是一个什么项目」这类问句必须能调工具。
    # 2026-09-30 桌面端复盘：这类句子被误判为纯聊天 → 本轮 0 工具 → 模型仍凭习惯
    # 吐文本态 DSML 调用 → 被兜底按 valid_names 全量过滤 → 表现为「思考完就停止」。
    # 注意：关键词统一小写，比较走 text_lower。
    "目录", "项目", "仓库", "工程", "代码", "结构", "路径", "分支",
    "提交", "依赖", "日志", "报错", "文件夹", "数据库", "接口",
    "git", "repo", "project", "directory", "folder", "codebase",
    "commit", "branch", "readme", "build", "deploy",
    # 环境指代词 —— 短问句指向当前环境，往往需要读现实数据而非背书
    "当前", "现在", "这个", "这里", "这是", "本地", "机器", "我的",
)

# 纯聊天判定的消息长度上限（字符数）。超过即认为任务复杂，必须发工具。
CHAT_ONLY_MAX_CHARS = 20


def is_chat_only(ctx: ToolContext) -> bool:
    """纯聊天检测：保守策略，宁可多发不可漏发。

    条件（全部满足才返回 True）：
    - 最后一条 user 消息 <= CHAT_ONLY_MAX_CHARS 字
    - 不含 ACTION_KEYWORDS 里的任一关键词
    - 对话历史中无工具调用记录（有则说明已在办正事）

    Args:
        ctx: 工具上下文，从中读取 messages。

    Returns: True 表示本轮可以一个工具都不发。
    """
    # 取最后一条 user 消息；顺手探测历史里是否已出现 tool_use
    last_user_text = ""
    has_tool_use = False
    for msg in reversed(ctx.messages):
        if msg.role == "user" and not last_user_text:
            for block in msg.content:
                if hasattr(block, "text"):
                    last_user_text = block.text
                    break
        if msg.role == "assistant":
            if msg.get_tool_uses():
                has_tool_use = True
                break

    if has_tool_use:
        return False

    text = last_user_text.strip()
    if len(text) > CHAT_ONLY_MAX_CHARS:
        return False

    text_lower = text.lower()
    for kw in ACTION_KEYWORDS:
        if kw in text_lower:
            return False

    return True


def build_tool_defs(
    registry: ToolRegistry,
    ctx: ToolContext | None = None,
    *,
    deferred_loading: bool = True,
    chat_detection: bool = True,
) -> list[ToolDef]:
    """把注册表里的工具转成给 LLM 的 ToolDef 列表。

    Args:
        registry: 工具注册表。
        ctx: 工具上下文（读 ``extra["discovered_tools"]`` 与最新消息）；
            None 时按「无已发现的延迟工具」处理。
        deferred_loading: False → 全量下发（老行为，用于排障）。
        chat_detection: False → 永不启用纯聊天零工具。

    Returns: 本轮要发给模型的工具定义，可能为空列表。
    """
    # 延迟加载关闭 → 回退到全量发送
    if not deferred_loading:
        return [_to_def(tool) for tool in registry.all()]

    # 纯聊天检测：短消息 + 无动作意图 → 0 工具
    if chat_detection and ctx is not None and is_chat_only(ctx):
        return []

    # 分组发送：核心工具 + 已发现的延迟工具
    discovered: set[str] = set()
    if ctx is not None:
        discovered = ctx.extra.get("discovered_tools", set())

    defs: list[ToolDef] = []
    for tool in registry.all():
        # 核心工具始终携带；延迟工具只在被发现后携带
        if not tool.deferred or tool.name in discovered:
            defs.append(_to_def(tool))
    return defs


def _to_def(tool) -> ToolDef:
    """单个 Tool → ToolDef（只取模型需要的三样）。"""
    return ToolDef(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
    )


__all__ = ["ACTION_KEYWORDS", "CHAT_ONLY_MAX_CHARS", "build_tool_defs", "is_chat_only"]
