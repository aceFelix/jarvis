"""模型能力面：「这个模型能不能看见图片」的唯一裁决点。

为什么单独要这一个文件：``model_type`` 曾散落在十几个文件里各自
``cfg.get("model_type", "multimodal")``，而「没人填」被当成了「有视觉」。后果是
两头错：纯文本模型照样收到图片块（不少端点直接 400），或者图片被 provider
静默丢掉、工具回执却写着「图片已回传: 是」。2026-11-03 那次「写个简易贪吃蛇」
里"假设我能看到 → 汇报我验证过"，成因正是这条。

口径沿用仓库自己定过的决定（``docs/roadmap/PLAN-self-config.md``「P4 实施记录 ②
model_type 保守给 text，不猜多模态」）：**拿不准就按看不见处理**，同时把怎么改
写明，不静默剥夺能力。

为什么是三值而不是两值：「不知道」是一种真实存在的状态（历史 models.toml 里
根本没写过这个字段）。把它压成两值必然选错一边，而选错的代价不对称——
误判"有"会让模型声称看见了根本没收到的图，误判"无"只是少一项能力且当场可见。

一处刻意的例外：``configs/models.toml`` 的 ``[llm.models]`` 是维护者手写的发货
清单，条目能力由写表的人负责（qwen3.x 走 DashScope 多模态端点，改用
``Generation.call()`` 会拿 "400 url error"）。所以内置模型在没有覆盖配置时取
``BUILTIN_MODEL_TYPE``，不是 ``unknown``——把发货就配好的 5 个模型集体打成
看不见，是用一个新错误去修另一个错误。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

# 三值枚举（写在 TOML / 传给 provider / 给 UI 显示，同一批字符串）
MODEL_TYPE_TEXT = "text"
MODEL_TYPE_MULTIMODAL = "multimodal"
MODEL_TYPE_UNKNOWN = "unknown"

#: 合法取值。serve 层的入参白名单与表单选项都以它为准（别在别处再抄一份）。
MODEL_TYPES: tuple[str, ...] = (MODEL_TYPE_TEXT, MODEL_TYPE_MULTIMODAL, MODEL_TYPE_UNKNOWN)

#: 内置（发货）模型未声明类型时的取值。改动它等于改动所有内置模型的图片通路，
#: 理由见模块 docstring「一处刻意的例外」，不要为了让某一行测试变绿而调它。
BUILTIN_MODEL_TYPE = MODEL_TYPE_MULTIMODAL

#: 图片被省略时写给模型看的那一句。四个 provider 共用，所以它住在这里而不是
#: 某个 provider 里。
#:
#: 措辞刻意不说「纯文本模型」：``unknown`` 也走这一支，而把未确认说成确定的
#: 纯文本，与把未送达说成「已回传: 是」是同一种错——替模型替用户下结论。
IMAGE_OMITTED_NOTE = "（当前模型无视觉输入，图片已省略）"


def normalize_model_type(value: Any) -> str:
    """把任意来源的写法收敛到 ``MODEL_TYPES`` 之一；认不出来一律 ``unknown``。

    容错而不是报错：这个值来自用户手写的 TOML、桌面壳的 RPC 入参、以及历史遗留
    配置。为一个小写字母差异把整个 provider 构造搞挂，代价远大于收益。
    """
    if not isinstance(value, str):
        return MODEL_TYPE_UNKNOWN
    text = value.strip().lower()
    if text in (MODEL_TYPE_TEXT, MODEL_TYPE_MULTIMODAL):
        return text
    return MODEL_TYPE_UNKNOWN


def model_has_vision(model_type: Any) -> bool:
    """该模型是否能接收图片。**只有明确声明 multimodal 才返回 True**。

    这是整套判据里唯一需要"保守"的地方：``unknown`` 按无视觉处理。写成
    ``!= "text"`` 会让 unknown 变成有视觉，那正是本次要修的错。
    """
    return normalize_model_type(model_type) == MODEL_TYPE_MULTIMODAL


def resolve_model_type(settings: Any, model_name: str | None = None) -> str:
    """取某模型生效的 model_type（默认针对当前模型 ``settings.model``）。

    查找顺序只有两级，且刻意不引入更多来源：

    1. ``settings.custom_models[name]`` 里显式写的值 —— 含「内置模型的覆盖配置」
       （``/models`` 改内置模型写的就是这里，见 ``model_manager._edit_builtin_model``）；
       写了但认不出来 → ``unknown``，**不猜**。
    2. 内置清单且没覆盖 → ``BUILTIN_MODEL_TYPE``。

    Args:
        settings: 任何带 ``model`` / ``custom_models`` 属性的对象（缺属性按未配置处理）。
        model_name: 目标模型名；省略则用 ``settings.model``。

    @author aceFelix
    """
    name = model_name or getattr(settings, "model", "") or ""
    cfg = (getattr(settings, "custom_models", None) or {}).get(name)
    if isinstance(cfg, dict):
        return normalize_model_type(cfg.get("model_type"))
    return BUILTIN_MODEL_TYPE


def vision_tag(model_type: Any) -> str:
    """短标签（列表前缀、补全提示、表格列）：多模态 / 纯文本 / 视觉未确认。

    两态写法（``"纯文本" if t == "text" else "多模态"``）会把 unknown 念成
    「多模态」——那是同一个错误换了个地方出现，所以单独给一态。
    """
    normalized = normalize_model_type(model_type)
    if normalized == MODEL_TYPE_MULTIMODAL:
        return "多模态"
    if normalized == MODEL_TYPE_TEXT:
        return "纯文本"
    return "视觉未确认"


def describe_model_type(model_type: Any) -> str:
    """带后果说明的标签，给「切模型 / 添加模型」这类回执用。

    比 :func:`vision_tag` 多说一句「按无视觉处理」：回执里只写“未确认”，用户不
    知道它意味着什么；而这一句直接决定他要不要去 /models 改。
    """
    tag = vision_tag(model_type)
    if tag == "视觉未确认":
        return "视觉未确认（按无视觉处理）"
    return tag


def vision_form_default(model_type: Any) -> str:
    """模型类型选择框的默认项：确认过的走确认值，其余一律落在 ``text``。

    表单只有「多模态/纯文本」两个选项（没有"我不知道"这一项给用户选），所以
    unknown 必须映射到一个选项上；按仓库既有决定映射到保守的那一边。
    """
    return MODEL_TYPE_MULTIMODAL if model_has_vision(model_type) else MODEL_TYPE_TEXT
