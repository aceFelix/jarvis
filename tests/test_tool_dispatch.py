"""工具 schema 下发决策模块测试（agent/core/tool_dispatch.py）。

该模块从 query_loop.py 拆出（单文件 800 行上限），承载两条与循环控制无关的策略：
延迟加载分组 + 纯聊天零工具检测。这里直接测纯函数，不再构造整个 QueryLoop；
另有一例校验 QueryLoop 上的薄委托与原实现等价，防止拆分期间行为漂移。

历史教训（决定了关键词表为什么这么长）：误判纯聊天会让本轮 0 工具下发，模型
照样吐文本态工具调用却被全量过滤，表现为「思考完就停止」，见
docs/fixlogs/deepseek-dsml-toolcall-leak-fix.md。

@author aceFelix
"""

from __future__ import annotations

from agent.core.message import Message, TextContent, ToolUseContent
from agent.core.query_loop import QueryLoop
from agent.core.tool import Tool, ToolRegistry
from agent.core.tool_dispatch import (
    ACTION_KEYWORDS,
    CHAT_ONLY_MAX_CHARS,
    _looks_fileish,
    build_tool_defs,
    is_chat_only,
)
from tests._query_loop_fakes import (
    FakeOrchestrator,
    FakeTool,
    ScriptedProvider,
    make_ctx,
    make_loop,
)


class _LazyTool(Tool):
    """延迟加载工具（deferred=True），只能靠 ToolSearch 发现。"""

    name = "lazy_tool"
    description = "延迟工具"
    input_schema = {"type": "object", "properties": {}}
    deferred = True

    async def call(self, args, ctx):
        return None


def _user(text: str) -> Message:
    """构造一条纯文本 user 消息。"""
    return Message(role="user", content=[TextContent(text=text)])


def _ctx_with(*messages: Message):
    """建一个装着指定历史的 ToolContext。"""
    ctx, msgs = make_ctx()
    msgs.extend(messages)
    return ctx


# ---------- 纯聊天检测 ----------

def test_short_greeting_is_chat_only():
    """短问候无动作意图 → 判定纯聊天，本轮无需发任何工具。"""
    assert is_chat_only(_ctx_with(_user("你好"))) is True


def test_action_keyword_blocks_chat_detection():
    """含信息查询关键词（天气）→ 不是纯聊天，必须留工具。"""
    assert is_chat_only(_ctx_with(_user("今天天气怎么样"))) is False


def test_long_message_is_not_chat_only():
    """超过 CHAT_ONLY_MAX_CHARS 的消息视为复杂任务，不做纯聊天判定。"""
    long_text = "请给我写一篇关于人工智能的详细报告，要求不少于五百字，还要举例说明各个技术细节。"
    assert len(long_text) > CHAT_ONLY_MAX_CHARS
    assert is_chat_only(_ctx_with(_user(long_text))) is False


def test_history_tool_use_blocks_chat_detection():
    """历史里已有 tool_use 说明正在办正事 → 一律不是纯聊天。"""
    ctx = _ctx_with(
        _user("你好"),
        Message(role="assistant", content=[ToolUseContent(id="t1", name="lazy_tool", input={})]),
    )
    assert is_chat_only(ctx) is False


def test_workspace_mention_question_is_not_chat_only():
    """回归 2026-09-30 桌面端复现句：工作区/环境指代问句曾被误判为纯聊天。"""
    for text in ("jarvis当前目录是一个什么项目", "这个仓库的结构", "看下配置文件"):
        assert is_chat_only(_ctx_with(_user(text))) is False, text


def test_change_verb_on_filename_is_not_chat_only():
    """回归 2026-10-22：「把 a.txt 改成 alpha2」曾被判纯聊天 → 本轮 0 工具 →
    模型照旧吐 DSML → 被空 valid_names 过滤 → 界面上只剩裸码与一句前言。

    两条互补判据分别验证：口语动词已进词表，文件名/路径走结构判据。
    """
    for text in (
        "把 a.txt 改成 alpha2",   # 用户原话（17 字，旧词表一个都没命中）
        "重命名 a.txt",            # 口语动词 + 文件名
        "把纪要.md 合并",           # 汉字文件名
        "看下 e:/lab/a.txt",       # 路径形态
    ):
        assert is_chat_only(_ctx_with(_user(text))) is False, text


def test_structural_file_token_works_without_keyword():
    """结构判据独立于词表：`名字.扩展名` 与路径分隔符都算，但不误伤数字与汉字。"""
    assert _looks_fileish("a.txt") is True
    assert _looks_fileish("e:/lab/a.txt") is True
    assert _looks_fileish("C:\\work\\a.md") is True
    assert _looks_fileish("~/.jarvis") is True   # 主目录写法也是路径
    # 反向：点后接数字不算扩展名，汉字夹斜杠不算路径
    assert _looks_fileish("3.14") is False
    assert _looks_fileish("今天/明天") is False


def test_pure_chat_not_overtriggered_by_structural_rule():
    """加了结构判据后，真问候仍得判为纯聊天（零工具省 token 的本意还在）。"""
    for text in ("你好", "谢谢", "嗯嗯好的", "1+1"):
        assert is_chat_only(_ctx_with(_user(text))) is True


def test_look_at_screen_is_not_chat_only():
    """**核心回归**（2026-10 第五次复发）：想看屏幕的短句不能判纯聊天。

    真实事故：语音里用户说「看一下吧」，词表只有「看下 / 看看」——
    差一个字就漏判 → 本轮零工具 → 模型吐 DSML 裸码。
    下面这批是把「差一个字」的各种常见说法钉住。
    """
    for text in (
        "看一下吧", "看一下", "看一眼", "看眼", "看屏幕", "看一下屏幕",
        "屏幕上有什么", "截个屏", "截个图", "截屏", "屏幕截图",
    ):
        assert is_chat_only(_ctx_with(_user(text))) is False, (
            f"{text!r} 被判成纯聊天，本轮将零工具 -> DSML 裸码"
        )


def test_real_greetings_still_chat_only_after_vision_words():
    """补完视觉词后，真问候不能被误伤（误伤只是多花 token，但仍要好）。"""
    for text in ("你好", "谢谢", "嗯嗯", "好啊", "收到"):
        assert is_chat_only(_ctx_with(_user(text))) is True


def test_keyword_match_is_case_insensitive():
    """英文关键词走小写比较，大写同样能识别出工具意图。"""
    assert "open" in ACTION_KEYWORDS
    assert is_chat_only(_ctx_with(_user("OPEN 一下"))) is False


def test_empty_history_is_chat_only():
    """空历史没有 user 文本，长度 0 ≤ 上限 → 判为纯聊天（首帧由调用方决定）。"""
    assert is_chat_only(_ctx_with()) is True


# ---------- 工具定义下发 ----------

def _registry_with_core_and_lazy() -> ToolRegistry:
    """一个核心工具 + 一个延迟工具的注册表。"""
    reg = ToolRegistry()
    reg.register(FakeTool())
    reg.register(_LazyTool())
    return reg


class _StubToolSearch(Tool):
    """ToolSearch 的最小替身（真身要 registry 引用，此处只验名字与 deferred）。"""

    name = "ToolSearch"
    description = "搜索并加载延迟工具"
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    deferred = False

    async def call(self, args, ctx):
        return None


def test_full_mode_sends_everything():
    """deferred_loading=False → 注册表全量下发（排障用的老行为）。"""
    defs = build_tool_defs(_registry_with_core_and_lazy(), None, deferred_loading=False)
    assert {d.name for d in defs} == {"fake_tool", "lazy_tool"}


def test_deferred_tool_hidden_until_discovered():
    """延迟工具未被发现时不下发；写进 discovered_tools 后才带 schema。"""
    reg = _registry_with_core_and_lazy()
    ctx = _ctx_with(_user("打开文件"))  # 有工具意图，避免被纯聊天清零

    assert {d.name for d in build_tool_defs(reg, ctx)} == {"fake_tool"}

    ctx.extra["discovered_tools"] = {"lazy_tool"}
    assert {d.name for d in build_tool_defs(reg, ctx)} == {"fake_tool", "lazy_tool"}


def test_chat_detection_returns_no_tools():
    """纯聊天 + 开启检测 → 返回空列表（本轮 0 token 下发工具）。

    注意：真实注册表里总有 ToolSearch，那种情况见
    `test_chat_turn_still_sends_tool_search`。
    """
    reg = _registry_with_core_and_lazy()
    ctx = _ctx_with(_user("你好"))
    assert build_tool_defs(reg, ctx) == []


def test_chat_turn_still_sends_tool_search():
    """**核心回归**（2026-10 第五次复发）：纯聊天轮也必须下发 ToolSearch。

    用户原话「看一下吧」（语音模式，4 字）曾被判为纯聊天 → 本轮 tools=[]，
    既没有 ScreenShot 也没有 ToolSearch（它虽属核心工具，也被「零工具」
    这一刀一起抹了）。而系统提示里写着「用 ToolSearch 加载延迟工具」并列出
    ``available-deferred-tools`` ——模型只能凭习惯吐文本态 DSML，
    随后被 valid_names 全量过滤，用户看到的就是一堆 ``<｜｜DSML｜｜ calls>``。

    ToolSearch 是拿回其它一切工具的唯一入口，它的开销（约 250 token）
    远小于「误判即整轮卡死」。
    """
    reg = _registry_with_core_and_lazy()
    reg.register(_StubToolSearch())
    ctx = _ctx_with(_user("你好"))

    defs = build_tool_defs(reg, ctx)

    assert {d.name for d in defs} == {"ToolSearch"}, (
        "纯聊天轮只能省掉核心工具体，不能把自救入口也省掉"
    )
    # 普通核心工具仍被省下（省 token 的本意还在）
    assert "fake_tool" not in {d.name for d in defs}


def test_chat_turn_keeps_already_discovered_tools():
    """已发现的延迟工具在下一句闲聊里不能丢：否则上一轮 ToolSearch 白搜。"""
    reg = _registry_with_core_and_lazy()
    reg.register(_StubToolSearch())
    ctx = _ctx_with(_user("你好"))
    ctx.extra["discovered_tools"] = {"lazy_tool"}

    assert {d.name for d in build_tool_defs(reg, ctx)} == {"ToolSearch", "lazy_tool"}


def test_chat_detection_can_be_disabled():
    """关掉检测后，同样的问候句仍照常下发核心工具。"""
    reg = _registry_with_core_and_lazy()
    ctx = _ctx_with(_user("你好"))
    defs = build_tool_defs(reg, ctx, chat_detection=False)
    assert {d.name for d in defs} == {"fake_tool"}


def test_none_ctx_never_suppresses():
    """ctx 为 None 时无从判断聊天意图，按「宁多发不可漏发」下发核心工具。"""
    defs = build_tool_defs(_registry_with_core_and_lazy(), None)
    assert {d.name for d in defs} == {"fake_tool"}


def test_def_carries_name_description_schema():
    """下发的 ToolDef 必须带全 name / description / input_schema，模型才能正确调用。"""
    defs = build_tool_defs(_registry_with_core_and_lazy(), None, deferred_loading=False)
    tool_def = next(d for d in defs if d.name == "fake_tool")
    assert tool_def.description == "测试工具"
    assert tool_def.input_schema == {"type": "object", "properties": {}}


# ---------- QueryLoop 薄委托与模块实现等价 ----------

def test_query_loop_delegates_match_module_functions():
    """拆分后 QueryLoop._build_tool_defs / _is_chat_only 必须与模块函数同结果。"""
    reg = _registry_with_core_and_lazy()
    loop: QueryLoop = make_loop(
        ScriptedProvider([]), FakeOrchestrator(), reg,
        deferred_loading=True, chat_detection=True,
    )
    ctx = _ctx_with(_user("你好"))

    assert loop._is_chat_only(ctx) == is_chat_only(ctx)
    assert {d.name for d in loop._build_tool_defs(ctx)} == {
        d.name for d in build_tool_defs(
            reg, ctx, deferred_loading=True, chat_detection=True,
        )
    }
