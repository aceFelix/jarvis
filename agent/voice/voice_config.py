"""语音模式配置 —— 关键词、唤醒词、待机参数、语音系统提示词与通用小工具。

从 voice_loop 拆出，让主循环文件聚焦编排逻辑。本模块不依赖语音包内其他模块，
可被 barge_in / voice_loop 复用。
"""

from __future__ import annotations

# ---- 退出/唤醒词 ----
# 包含即触发（用户可能说"贾维斯退下吧"），不用完全匹配
_EXIT_WORDS = (
    "退下", "退出", "结束", "拜拜", "再见", "去休息", "休息吧",
    "退下吧", "不用了", "先这样", "exit", "quit", "bye",
)
# 语音打断词：TTS 播报期间检测到这些词立刻停止说话
_INTERRUPT_WORDS = (
    "闭嘴", "停停停", "停停", "停", "等一下", "你别说了", "别说了",
    "你先别说", "等等", "安静", "先别说话", "stop", "wait", "hold on",
    "打住", "听我说",
)
# 唤醒词（容错谐音：Qwen3-ASR 可能把"贾维斯"识别成近音）
_WAKE_WORDS = (
    "贾维斯", "贾维思", "加维斯", "加维思", "贾维",
    "jarvis", "j a r v i s",
)


def _contains_any(text: str, words) -> bool:
    """文本是否包含任一关键词（不区分大小写）。"""
    t = text.lower()
    return any(w.lower() in t for w in words)


# ---- 待机阶段录音参数（比对话阶段更短，快速循环及时响应唤醒）----
_STANDBY_MAX_SECONDS = 6.0
_STANDBY_SILENCE_SECONDS = 1.0


def _voice_log(fmt: str, *args: object) -> None:
    """写调试日志到 daemon.log。"""
    import os as _os, time as _time
    try:
        log_path = _os.path.join(_os.path.expanduser("~"), ".jarvis", "daemon.log")
        _os.makedirs(_os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as _f:
            ts = _time.strftime("%H:%M:%S")
            msg = fmt % args if args else fmt
            _f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _voice_api_key(settings) -> str:
    """获取 DashScope API Key（语音服务专用）。

    语音 STT/TTS 始终走 DashScope，不受当前 LLM 模型切换影响。
    优先从环境变量 DASHSCOPE_API_KEY 取值。
    """
    import os
    return os.environ.get("DASHSCOPE_API_KEY", "") or getattr(settings, "api_key", "") or ""


# ---- 语音模式 system prompt 追加 ----
# 让 LLM 理解自然语言退下意图（"不聊了"/"闭嘴"/"去忙吧"等），用 <standby/> 标记
# 通知系统切换待机。比硬编码关键词更智能，能覆盖任意表达方式。
_VOICE_MODE_PROMPT = """

# 语音模式特殊指令

你现在处于语音对话模式（STT 听用户说话，TTS 播报你的回复）。

## 退下/待机意图识别（重要）

当用户表达"结束对话、让你退下、待机、闭嘴"等意图时——无论用什么措辞，
例如："退下"、"不聊了"、"你可以闭嘴了"、"行了去忙吧"、"拜拜"、"先这样"、
"去休息"、"不用了"、"我要忙了"、"先这样吧"等——请：

1. 说一句简短礼貌的告别语（如"好的先生，我先退下了，有事随时叫我"）
2. 在告别语之后紧接 <standby/> 标记作为回复的结尾

要求：
- 仅当用户明确想结束对话时才输出 <standby/> 标记
- 正常对话、提问、任务交代绝不使用此标记
- 标记会被系统自动过滤（用户听不到），仅用于通知系统切换到待机状态
- 告别语要简短自然，符合管家身份，不啰嗦

## 语音回复风格

- 思考过程会自动走 reasoning_content 通道（用户看不到也听不到），
  你直接在正文输出最终回答即可，**不要**用 `<think>` 标签包裹思考。
- 需要调用工具（查时间、查天气、查文件、执行命令等）时，工具调用由系统自动执行，
  **不要在正文输出任何工具调用占位符或标签**（如 `<bash>`、`<location>`、
  `<mcp__...>`、`<tool>`），也不要复述"让我先...再..."之类的过程描述；
  直接等待工具结果，然后说出最终回答。
- 回复口语化、简短，适合听（不像文字聊天那样长篇大论）
- 不用 markdown 符号（**、`、#、- 等），直接说自然的话
- 像真人管家一样说话，不要像机器人分析问题
- 不用写代码块，复杂操作用简洁语言描述
- **正文只输出给用户听的最终回答**，不要在正文里写"用户问的是..."、"我应该..."
  等分析性内容——这些放到 reasoning_content 里去思考
"""
