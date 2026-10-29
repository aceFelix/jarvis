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
  **宁全勿缺**，且补一道**不依赖词表的结构判据**（文件名/路径形态），详见
  docs/fixlogs/deepseek-dsml-toolcall-leak-fix.md。

@author aceFelix
"""

from __future__ import annotations

import re

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
    # 口语化的改动/重命名动词 —— 2026-10-22 复盘：「把 a.txt 改成 alpha2」被判纯
    # 聊天。表里当时只有书面词「修改/删除/编辑」，用户实际说的是「改成/删掉」。
    # 这一类靠补词补不全，故下面另有 _looks_fileish 结构判据兜底。
    "改成", "改为", "改名", "重命名", "换成", "变成", "挪到", "移到",
    "移动到", "复制到", "追加", "清空", "存成", "保存", "生成", "新建",
    "建个", "删掉", "去掉", "贴到", "粘到", "回退", "撤销", "看下", "看看",
    # 视觉/观察类口语（2026-10 第五次复盘）：用户想看屏幕时说的是「看一下」
    # 「看一下吧」「截个屏」「看屏幕」，而表里只有「看下/看看」——差一个字就漏。
    # 这类靠补词仍会漏，故另有 build_tool_defs 的「自救入口」做结构兜底。
    "看一下", "看一眼", "看眼", "看屏幕", "看一下屏幕", "屏幕上",
    "截个图", "截屏", "截个屏", "屏幕截图", "画面", "瞅",
)

# 纯聊天判定的消息长度上限（字符数）。超过即认为任务复杂，必须发工具。
CHAT_ONLY_MAX_CHARS = 20

# 纯聊天轮里**依然必发**的工具。
#
# 2026-10 第五次复盘：`is_chat_only` 命中后原先直接 `return []`，本意是 0 token，
# 但它制造了一个**不可恢复**的故障态——系统提示里写着「以上为延迟加载工具，用
# ToolSearch 搜索关键词加载完整定义后即可调用」并列出 ``available-deferred-tools``，
# 而本轮 tools 里既没有那些延迟工具、也没有 ToolSearch 本身（它虽属核心工具，
# 也被这一刀一起抹了）。模型只能凭训练习惯吐出文本态 DSML：
#
#     <｜｜DSML｜｜ calls>
#     <｜｜DSML｜｜ invoke name="ScreenShot"></｜｜DSML｜｜ invoke>
#     </｜｜DSML｜｜ calls>
#
# 随后被 `parse_textual_tool_calls` 按 valid_names 全量过滤（集合为空），
# 最后由 `rescue_text` 补回显——用户看到的就是一堆乱码、工具不执行。
#
# 所以纯聊天轮改为「只留工具目录（ToolSearch）+ 上一轮已发现的延迟工具」：
# 省 token 的大头（核心工具全量 schema）不变，但误判时模型能自己把工具捞回来。
# @author aceFelix
_SAFETY_TOOLS: frozenset[str] = frozenset({"ToolSearch"})

# 结构判据：出现「文件名形态」或「路径形态」的 token 就不是闲聊。词表永远补不全
# （动词、名词、缩写、方言轮着来），但「要动真实文件」这件事在字面上是有形状的：
# `名字.扩展名` 或 `段/段`、`C:\`。两条正则取向不同是有意区分的：
# - 扩展名判据允许汉字在左侧（「纪要.md」是真文件名，点后接 ASCII 字母本身就是强信号）；
# - 斜杠判据只认 ASCII 邻右，因为「今天/明天」「和/或」这类散文写法在汉字夹斜杠里
#   太常见，放进去就是把每个问句都当任务。
# @author aceFelix
_FILE_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_\u4e00-\u9fff\-]+\.[A-Za-z][A-Za-z0-9]{0,7}"
)
_PATH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_.\-~][/\\][A-Za-z0-9_.\-]"
)


def _looks_fileish(text: str) -> bool:
    """文本里是否含文件名或路径形态的 token（结构判据，不依赖关键词表）。

    命中例：``a.txt``、``纪要.md``、``e:/jarvis-lab``、``C:\\work``、``~/.jarvis``。
    不命中例：``3.14``（点后是数字）、``今天/明天``（汉字夹斜杠不当路径）。
    误判方向是「多发一次工具」，代价是几百 token；漏判的代价是整轮卡死，
    所以扩展名那条取宽松侧。

    Args:
        text: 已 strip 的最后一条 user 消息文本。

    Returns: True 表示本轮极可能要读写真实文件，不可零工具。
    """
    return bool(_FILE_TOKEN_RE.search(text) or _PATH_TOKEN_RE.search(text))


def is_chat_only(ctx: ToolContext) -> bool:
    """纯聊天检测：保守策略，宁可多发不可漏发。

    条件（全部满足才返回 True）：
    - 最后一条 user 消息 <= CHAT_ONLY_MAX_CHARS 字
    - 不含 ACTION_KEYWORDS 里的任一关键词
    - 不含文件名/路径形态的 token（`_looks_fileish` 结构判据）
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

    # 关键词没命中也不等于纯聊天：带文件名/路径的句子必然要动工具（2026-10-22 复盘）
    if _looks_fileish(text):
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

    # 纯聊天检测：短消息 + 无动作意图 → 只留自救入口，不发核心工具全量 schema
    if chat_detection and ctx is not None and is_chat_only(ctx):
        return _safety_defs(registry, ctx)

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


def _safety_defs(registry: ToolRegistry, ctx: ToolContext) -> list[ToolDef]:
    """纯聊天轮的最小工具集：ToolSearch + 已被发现的延迟工具。

    为什么不返回空列表，见 ``_SAFETY_TOOLS`` 上方的复盘。一句话：空列表让
    「提示词说能用 ToolSearch / 清单里没有」这个不一致变成硬故障，而模型侧的
    反应只能是吐文本态 DSML。

    为什么不是把核心工具全带上：那等于取消整个纯聊天优化（省的就是这部分
    schema）。只留 ToolSearch（约 250 token）就足够让模型自救。

    Args:
        registry: 工具注册表。
        ctx: 工具上下文（读 ``extra["discovered_tools"]``）。

    Returns: 纯聊天轮的 ToolDef 列表，至少包含 ToolSearch。

    @author aceFelix
    """
    discovered: set[str] = set(ctx.extra.get("discovered_tools", set()))
    return [
        _to_def(tool)
        for tool in registry.all()
        # 已发现的也要留：否则上一轮 ToolSearch 的成果在下一句闲聊时白丢
        if tool.name in _SAFETY_TOOLS or tool.name in discovered
    ]


def _to_def(tool) -> ToolDef:
    """单个 Tool → ToolDef（只取模型需要的三样）。"""
    return ToolDef(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
    )


__all__ = [
    "ACTION_KEYWORDS",
    "CHAT_ONLY_MAX_CHARS",
    "build_tool_defs",
    "is_chat_only",
]
