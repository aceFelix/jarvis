"""语音（DashScope）API Key —— 解析、来源判定与「有没有配」的统一口径。

**为什么值得单独一个模块**：jarvis 的 LLM 与语音是**两套凭据**。说话/听话（TTS/STT）
固定走阿里云百炼 DashScope，而 LLM 可以是 DeepSeek / OpenAI / Claude / 本地兼容服务。
这两套 key 的读取原先散在三处，并且已经漂移：

===========  ==========================================  ============================
路径          旧口径                                        问题
===========  ==========================================  ============================
``/talk``      ``settings.dashscope_api_key`` → 环境变量        ✓ 正确
``/voice``     **只认环境变量** → 主 LLM 的 ``api_key``        ✗ 两个坑，见下
``/init``      没有这一项                                      ✗ 首启配不出一条能出声的机器
===========  ==========================================  ============================

``/voice`` 旧实现 ``os.environ["DASHSCOPE_API_KEY"] or settings.api_key`` 带来两个坑：

1. **配置文件里写的 key 不生效**。用户把 ``dashscope_api_key`` 写进 ``models.toml``
   或 ``[realtime_talk] api_key``，``_voice_api_key`` 却只读环境变量——「配了没用」。
2. **拿别家的 key 连阿里云**。LLM 换成 DeepSeek 后 ``settings.api_key`` 是 DeepSeek
   的凭据，直接回退给 DashScope 必然 401；而报错只说「TTS 连接建立失败」，用户根本
   想不到是 key 混用，只会以为"语音功能坏了"。

**优先级（高 → 低）**：

1. ``settings.dashscope_api_key`` —— 用户显式配置的意图最强。注意 ``env.py`` 也会把
   环境变量 ``DASHSCOPE_API_KEY`` 灌进这个字段（仅当配置里没值时），所以手动配的
   永远压过环境变量：引导里填的 key 必须立刻生效，不能被机器上残留的旧变量吃掉。
2. 环境变量 ``DASHSCOPE_API_KEY`` —— 给「改了配置文件但当前进程没重读」兜底。
3. ``settings.api_key`` —— **仅当主 LLM 本身就走 DashScope 时才复用**。这是修复坑 2
   的关键：不加厂商判断，DeepSeek 的 key 会被当成语音 key 用。
4. ``settings.default_api_key`` —— **仅当配置顶层（``last_model`` 覆盖前）就是
   DashScope 时才复用**。这层是 2026-10 真机实测后补的：用户顶层写的是百炼
   （``provider = "dashscope"`` + 百炼 key），但 ``last_model`` 切到了 DeepSeek
   模型，于是运行时 ``provider`` 是 deepseek、``api_key`` 是 DeepSeek 的 key。
   若没有这一层，他明明在配置里配了百炼 key，语音却说“未配置”——只能去设环境
   变量或再写一遍 ``dashscope_api_key``。加上这一层就是**零配置可用**。

@author aceFelix
"""

from __future__ import annotations

import os
from typing import Any

#: 走 DashScope 的厂商标识（``provider`` 字段的合法值）。
#: ``vendors.py`` 里 DashScope 的 ``api_format`` 是 ``openai``（走兼容端点），
#: 所以 ``api_format == "dashscope"`` 只出现在旧配置里，一并认。
_DASHSCOPE_VENDORS = frozenset({"dashscope"})

#: DashScope 的域名后缀（判断「这个 base_url 指向阿里云」）。
_DASHSCOPE_HOST = "dashscope.aliyuncs.com"

#: 环境变量名（用户最常直接用这个，而不是写配置）。
ENV_VAR = "DASHSCOPE_API_KEY"

#: 拿 key 的地方（阿里云百炼控制台）。提示语里会带上。
CONSOLE_HINT = "https://bailian.console.aliyun.com/"

#: key 来源标识 → 给人看的说明。``source()`` 返回的键必须在这张表里。
#: ``configured`` 的文案不提“配置文件”：``env.py`` 也会把环境变量灌进这个字段，
#: 用户看到的结果一样（有值），没必要为此做一次额外的文件读取。
SOURCE_LABELS: dict[str, str] = {
    "configured": "dashscope_api_key 已有值（配置文件写入或环境变量灌入）",
    "env": f"环境变量 {ENV_VAR}",
    "llm": "复用主 LLM 的 api_key（当前 LLM 就是 DashScope）",
    "default": "复用配置顶层的 api_key（顶层 provider 就是 DashScope）",
    "": "未配置",
}


def _text(value: Any) -> str:
    """把配置值取成去空白的字符串（None / 非字符串一律当空串）。"""
    return value.strip() if isinstance(value, str) else ""


def _is_dashscope(provider: Any, api_format: Any, base_url: Any) -> bool:
    """三个信号任一命中即认为是 DashScope（信号选择理由见 ``llm_is_dashscope``）。

    @author aceFelix
    """
    if _text(provider).lower() in _DASHSCOPE_VENDORS:
        return True
    if _text(api_format).lower() in _DASHSCOPE_VENDORS:
        return True
    return _DASHSCOPE_HOST in _text(base_url).lower()


def llm_is_dashscope(settings: Any) -> bool:
    """当前**运行中**的 LLM 是否就走 DashScope。

    三个信号任一成立即认为是：
    - ``provider == "dashscope"`` —— 正常情况；
    - ``api_format == "dashscope"`` —— 旧配置/SDK 自管端点写法；
    - ``base_url`` 含 ``dashscope.aliyuncs.com`` —— 用户配的是百炼的 OpenAI 兼容
      端点，但 provider 手填成了 ``openai``（很容易发生）。

    宁可多判一个（最坏是拿着 DashScope 的 key 去连 DashScope，本来也对），
    也不要漏判（漏判的后果就是拿别家的 key 去连，报一个看不出原因的 401）。

    @author aceFelix
    """
    if settings is None:
        return False
    return _is_dashscope(
        getattr(settings, "provider", ""),
        getattr(settings, "api_format", ""),
        getattr(settings, "base_url", ""),
    )


def default_is_dashscope(settings: Any) -> bool:
    """配置**顶层**（``last_model`` 覆盖前）是否就走 DashScope。

    与 ``llm_is_dashscope`` 的区别是场景：用户的 ``settings.toml``/``models.toml``
    顶层写着百炼 + 百炼 key，但 ``last_model`` 切到了 DeepSeek 模型，于是运行时的
    ``provider``/``api_key`` 都指向 DeepSeek。这一层让语音仍能从顶层找到百炼 key。

    @author aceFelix
    """
    if settings is None:
        return False
    return _is_dashscope(
        getattr(settings, "default_provider", ""),
        getattr(settings, "default_api_format", ""),
        getattr(settings, "default_base_url", ""),
    )


def source(settings: Any) -> str:
    """返回 key 的**来源**：``configured`` / ``env`` / ``llm`` / 空串（未配置）。

    单独暴露来源是为了让报错能说清"这个 key 是哪来的"——排查语音不出声时，
    用户最需要知道的恰恰是"它到底用的哪个 key"，而不是笼统的"连接失败"。

    @author aceFelix
    """
    configured = _text(getattr(settings, "dashscope_api_key", "")) if settings else ""
    if configured:
        return "configured"
    if os.environ.get(ENV_VAR):
        return "env"
    if llm_is_dashscope(settings) and _text(getattr(settings, "api_key", "")):
        return "llm"
    if default_is_dashscope(settings) and _text(getattr(settings, "default_api_key", "")):
        return "default"
    return ""


def resolve(settings: Any) -> str:
    """取出本次语音会话该用的 DashScope API Key（取不到返回空串）。

    @author aceFelix
    """
    kind = source(settings)
    if kind == "configured":
        return _text(settings.dashscope_api_key)
    if kind == "env":
        return os.environ.get(ENV_VAR, "") or ""
    if kind == "llm":
        return _text(settings.api_key)
    if kind == "default":
        return _text(settings.default_api_key)
    return ""


def is_configured(settings: Any) -> bool:
    """语音能力是否已有可用 key（引导据此决定要不要问）。

    @author aceFelix
    """
    return bool(resolve(settings))


def describe(settings: Any) -> str:
    """一行说明当前 key 状态（给 ``/config`` 面板与启动提示用）。

    **绝不回显 key 本身**，只说来源；``/config`` 那边另有脱敏展示。

    @author aceFelix
    """
    return SOURCE_LABELS.get(source(settings), SOURCE_LABELS[""])


def describe_detail(settings: Any) -> str:
    """带**脱敏值**与尽量精确来源的一行说明（引导、`/init voice` 用）。

    与 ``describe()`` 的分工：``describe()`` 只说“哪一类来源”，适合表格里的窄列；
    这里给出“具体是哪把 key”。

    触发原因（2026-10 实测）：首启引导原先用 ``describe()`` 输出
    「dashscope_api_key 已有值（配置文件写入或环境变量灌入）」，用户看了等于没看：
    既不知是哪把 key，也不知是自己写的还是机器上残留的环境变量。

    注：``configured`` 分支靠“环境变量值是否等于配置值”来推测来源，是**启发式**。
    用户把同一个值手写进两份会误报为环境变量——但两种写法本来就等价、修法也一样
    （改任一处），误报无实质危害，不值得为此读一次配置文件。

    @author aceFelix
    """
    kind = source(settings)
    if not kind:
        return "未配置"

    from agent.utils.mask import mask_key

    if kind == "configured":
        key = _text(settings.dashscope_api_key)
        env = (os.environ.get(ENV_VAR) or "").strip()
        where = (
            f"来自环境变量 {ENV_VAR}" if env and env == key
            else "来自配置文件 dashscope_api_key"
        )
        return f"已配置 {mask_key(key)}，{where}"
    if kind == "env":
        return f"已配置 {mask_key(os.environ.get(ENV_VAR, ''))}，来自环境变量 {ENV_VAR}"
    if kind == "llm":
        return (
            f"复用主 LLM 的 key {mask_key(_text(settings.api_key))}，"
            "因为当前 LLM 就是 DashScope（没有单独配语音 key）"
        )
    return (
        f"复用配置顶层的 key {mask_key(_text(settings.default_api_key))}，"
        "因为顶层 provider 就是 DashScope"
    )


def missing_hint(settings: Any) -> str:
    """没有语音 key 时给用户的排查提示（带获取地址与三种配法）。

    专门区分「LLM 不是 DashScope」的情形：那时用户**必须**单独配一个 key，
    直接说清楚，别让他以为复用主 key 就行。

    @author aceFelix
    """
    if default_is_dashscope(settings):
        who = "配置顶层是 DashScope，但顶层没填 api_key"
    elif llm_is_dashscope(settings):
        who = "未取到 key"
    else:
        who = "当前 LLM 不是 DashScope"
    return (
        f"语音能力需要阿里云百炼（DashScope）API Key（{who}）。三选一：\n"
        f"  1. 交互式配置：运行 /init（首启引导会问）\n"
        f"  2. 配置文件：models.toml 顶层写 dashscope_api_key = \"sk-...\"\n"
        f"  3. 环境变量：set {ENV_VAR}=sk-...\n"
        f"  Key 获取：{CONSOLE_HINT}"
    )
