"""厂商默认表 —— 首启引导（`/init`）与 `/models` 添加模型共用的**唯一**默认值来源。

为什么要单独一个模块（2026-10 实测后改的）：

原先同一份「厂商 → 默认 base_url」在两个地方各写一遍——`init_command._VENDORS`
和 `model_manager._infer_base_url`。两处立刻漂移了：首启表里 DeepSeek 是
`https://api.deepseek.com/v1`，推断表里是 `https://api.deepseek.com`（少了 `/v1`），
小米 MiMo 更是只在首启表里有。结果是「首启配出来的模型能连通，用 `/models`
添加的同厂商模型连不通」——同一个厂商，两条入口给出两个地址。

现在只保留这张表：`/init` 直接读它，`/models` 的 base_url 留空时也从这里推断。

字段说明：

- ``key``           写入配置的 vendor 值（`/init` 与 `/models` 表单共用）
- ``name``/``desc`` 仅供界面展示，**不写具体型号**——写上去就是下一张会过期的表
- ``default_model`` 断网/无 key 时的兜底款，不是"推荐款"
- ``default_base_url`` 该厂商的接入地址；空串表示由 SDK 自己管（或必须问用户）
- ``api_format``    协议类型：``openai`` / ``anthropic``（决定用哪个 Provider 类）

@author aceFelix
"""

from __future__ import annotations

#: 内置厂商表（顺序即选择器里的顺序）。
#:
#: `default_model` 只是**断网/无 key 时的兜底**。首启拿到 key 后会实时拉取在线
#: 模型列表（`agent/llm/model_catalog.py`），**不依赖这张表是否最新**。
#: 2026-10-27 用真实 key 实测 `GET /models` 修正过三处兜底：DeepSeek 账号里只有
#: `deepseek-flash` / `deepseek-v4-pro`（`deepseek-v4-flash` 已不在列表）；MiniMax
#: 返回的是 `MiniMax-M3`（模型 id 大小写敏感）；小米返回里没有 `mimo-v2.5-pro`，
#: 且前四个是 asr/tts 语音模型（所以选模型时必须提醒用户选"对话模型"）。
VENDORS: list[dict] = [
    {
        "key": "dashscope", "name": "阿里云 DashScope（通义千问）",
        "desc": "国内首选，千问系列多模态视觉",
        "default_model": "qwen3.7-plus",
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容（推荐）",
    },
    {
        "key": "deepseek", "name": "DeepSeek",
        "desc": "高性价比，推理能力强",
        "default_model": "deepseek-flash",
        "default_base_url": "https://api.deepseek.com/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "openai", "name": "OpenAI（GPT 系列）",
        "desc": "综合能力最强，生态最成熟",
        "default_model": "gpt-5.5",
        "default_base_url": "https://api.openai.com/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 原生",
    },
    {
        "key": "zhipu", "name": "智谱 AI（GLM 系列）",
        "desc": "国产大模型，GLM 系列",
        "default_model": "glm-5.2",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容（推荐，支持 thinking）",
    },
    {
        "key": "anthropic", "name": "Anthropic（Claude 系列）",
        "desc": "擅长长文本、复杂推理、代码生成",
        "default_model": "claude-sonnet-4.8",
        # 显式写出来（与 SDK 默认值相同）：留空会让「留空=按厂商推断」这条
        # 兜底路径返回空串，`/models` 添加时就得让用户手填。
        "default_base_url": "https://api.anthropic.com",
        "api_format": "anthropic",
        "api_format_desc": "Anthropic 原生协议",
    },
    {
        "key": "moonshot", "name": "Moonshot（Kimi）",
        "desc": "Kimi 系列，长上下文与多模态",
        "default_model": "kimi-k3",
        "default_base_url": "https://api.moonshot.cn/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "minimax", "name": "MiniMax",
        "desc": "国内大模型，长上下文支持",
        "default_model": "MiniMax-M3",
        "default_base_url": "https://api.minimax.chat/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "siliconflow", "name": "SiliconFlow（硅基流动）",
        "desc": "开源模型托管平台，DeepSeek/Qwen/Llama 等",
        "default_model": "deepseek-ai/deepseek-v4-flash",
        "default_base_url": "https://api.siliconflow.cn/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "xiaomimimo", "name": "小米 MiMo",
        "desc": "小米自研大模型",
        "default_model": "mimo-v2.6-flash",
        "default_base_url": "https://api.xiaomimimo.com/v1",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "google", "name": "Google Gemini",
        "desc": "Google 多模态大模型",
        "default_model": "gemini-3.5",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容",
    },
    {
        "key": "custom", "name": "其他 OpenAI 兼容服务",
        "desc": "手动输入 base_url、model 名和 api_format",
        "default_model": "",
        "default_base_url": "",
        "api_format": "openai",
        "api_format_desc": "OpenAI 兼容（可修改）",
    },
]

#: `/models` 表单里自建服务叫 ``other``，首启表里叫 ``custom``——同一件事的两个历史叫法。
_ALIASES: dict[str, str] = {"other": "custom"}

#: API 格式 → 由 SDK 自己管端点、不需要 base_url 的协议。
_SDK_MANAGED_FORMATS = frozenset({"dashscope", "zai"})


def find(vendor_key: str) -> dict | None:
    """按 vendor key 查表；未知厂商返回 ``None``（别名 ``other`` 会归一到 ``custom``）。

    @author aceFelix
    """
    key = _ALIASES.get(vendor_key, vendor_key)
    for v in VENDORS:
        if v["key"] == key:
            return v
    return None


def default_base_url(vendor_key: str, api_format: str = "") -> str:
    """按厂商 + 接口类型给出默认接入地址。

    Args:
        vendor_key: 厂商 key（如 ``deepseek``；``other`` 视同 ``custom``）。
        api_format: 协议类型；``dashscope`` / ``zai`` 由 SDK 接管端点，返回空串。

    Returns:
        接入地址；未知厂商或 SDK 自管端点时为空串（由调用方决定要不要问用户）。

    @author aceFelix
    """
    if (api_format or "").strip().lower() in _SDK_MANAGED_FORMATS:
        return ""
    vendor = find(vendor_key)
    if vendor is None:
        return ""
    return str(vendor.get("default_base_url", "") or "")


def default_model(vendor_key: str) -> str:
    """按厂商 key 给出兜底模型名（断网/无 key 时用）。

    @author aceFelix
    """
    vendor = find(vendor_key)
    return str(vendor.get("default_model", "") or "") if vendor else ""


def default_api_format(vendor_key: str) -> str:
    """按厂商 key 给出协议类型（未知厂商按 OpenAI 兼容处理）。

    @author aceFelix
    """
    vendor = find(vendor_key)
    return str(vendor.get("api_format", "openai") or "openai") if vendor else "openai"
