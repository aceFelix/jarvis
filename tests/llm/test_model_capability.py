"""模型能力面（S5）的测试。

这个文件守的是**两个相反方向**的错误，缺一个都不算守住：

- 宽松方向：`model_type` 没填 / 填了认不出来的写法，被当成「有视觉」——
  于是图片发给纯文本模型（端点报错），或者图片被 provider 静默抽掉而工具回执
  写着「图片已回传: 是」。2026-11-03 那次贪吃蛇里「假设我能看到 → 汇报我验证过」
  就是这么来的。
- 过度方向：为了修上面那条，把所有默认值一刀切改成 unknown / 按无视觉处理，
  于是 `configs/models.toml` 里发货配好的 5 个 qwen 模型集体看不见图（DashScope
  还会拿 "400 url error"）。这条同样有测钉住（见 TestResolve 的 builtin 两例）。

另有一批契约测盯着「别再散开」：判据只能来自 `model_has_vision`，四个 provider
都得有这一分支，工具措辞只能来自 `image_receipt`。这类断言是文本级的，因为要防
的就是"有人新写一处 `== "text"`"——行为测抓不到还没被执行的那一处。

@author aceFelix
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.core.images import image_receipt
from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.llm.anthropic_provider import (
    AnthropicProvider,
    _block_to_anthropic,
    _messages_to_anthropic,
)
from agent.llm.dashscope_provider import (
    DashScopeProvider,
    _messages_to_dashscope,
    _messages_to_dashscope_multimodal,
)
from agent.llm.model_capability import (
    BUILTIN_MODEL_TYPE,
    IMAGE_OMITTED_NOTE,
    MODEL_TYPE_MULTIMODAL,
    MODEL_TYPE_TEXT,
    MODEL_TYPE_UNKNOWN,
    MODEL_TYPES,
    describe_model_type,
    model_has_vision,
    normalize_model_type,
    resolve_model_type,
    vision_form_default,
    vision_tag,
)
from agent.llm.openai_provider import OpenAIProvider, _messages_to_openai
from agent.llm.provider_registry import PROVIDER_REGISTRY, ProviderMeta
from agent.llm.zai_provider import ZaiProvider

_IMG = ImageContent(data="AAAB", media_type="image/png")

# 工具名 → 源文件。拿模块名做 key，是为了让“某个工具的措辞没改齐”能在参数化
# 里单独报出来，而不是一个循环默默只测到第一个。
_TOOL_SOURCES = {
    "ScreenShot": "agent/tools/system/screen.py",
    "CameraShot": "agent/tools/vision/camera.py",
    "BrowserScreenshot": "agent/tools/web/browser.py",
}


def _settings(**over) -> SimpleNamespace:
    """够用的 settings 替身：只带裁决点真正读的两个字段。"""
    base = {"model": "some-model", "custom_models": {}}
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# 归一化：认不出来就是 unknown，不许猜
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_known_values_pass_through(self) -> None:
        assert normalize_model_type(MODEL_TYPE_TEXT) == MODEL_TYPE_TEXT
        assert normalize_model_type(MODEL_TYPE_MULTIMODAL) == MODEL_TYPE_MULTIMODAL

    def test_case_and_padding_tolerated(self) -> None:
        """大小写与空格是配置里最常见的差异，为它把 provider 构造搞挂不值得。"""
        assert normalize_model_type("  MultiModal ") == MODEL_TYPE_MULTIMODAL
        assert normalize_model_type("TEXT") == MODEL_TYPE_TEXT

    @pytest.mark.parametrize("value", ["", None, "audio", "vision", "multimodal-ish", 3, {}, []])
    def test_unrecognized_becomes_unknown(self, value) -> None:
        assert normalize_model_type(value) == MODEL_TYPE_UNKNOWN

    def test_alias_not_promoted_to_declaration(self) -> None:
        """「vision」这类写法**不**折算成 multimodal。

        折算等于允许配置里用自由措辞声明能力，下一个改代码的人会再加一个别名，
        最后没人说得清哪种写法算声明过。认不出来就按没声明处理，路径只有一条。
        """
        assert model_has_vision("vision") is False
        assert model_has_vision("multimodal+") is False

    def test_model_types_is_the_only_enum(self) -> None:
        """三值枚举本身也是被测对象：少一个值，白名单与标签都会各说各话。"""
        assert MODEL_TYPES == (MODEL_TYPE_TEXT, MODEL_TYPE_MULTIMODAL, MODEL_TYPE_UNKNOWN)


# ---------------------------------------------------------------------------
# 保守方向：unknown 按无视觉处理（仓库既有决定 P4 记录 ②）
# ---------------------------------------------------------------------------


class TestHasVision:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (MODEL_TYPE_MULTIMODAL, True),
            (MODEL_TYPE_TEXT, False),
            (MODEL_TYPE_UNKNOWN, False),
            ("", False),
            (None, False),
        ],
    )
    def test_only_explicit_multimodal_is_vision(self, value, expected) -> None:
        assert model_has_vision(value) is expected

    def test_unknown_is_not_vision__mutation_nail(self) -> None:
        """变异钉子：把判据写成 `!= "text"`（unknown 变有视觉）必须在这里红。"""
        assert model_has_vision(MODEL_TYPE_UNKNOWN) is False

    def test_form_default_maps_unknown_to_the_conservative_choice(self) -> None:
        """表单只有两项，unknown 必须落到 text 而不是 multimodal。

        预填多模态会把"猜的值"变成"用户确认过的值"：他打开表单看一眼直接保存，
        纯文本模型从此带图。
        """
        assert vision_form_default(MODEL_TYPE_UNKNOWN) == MODEL_TYPE_TEXT
        assert vision_form_default(None) == MODEL_TYPE_TEXT
        assert vision_form_default(MODEL_TYPE_MULTIMODAL) == MODEL_TYPE_MULTIMODAL
        assert vision_form_default(MODEL_TYPE_TEXT) == MODEL_TYPE_TEXT


# ---------------------------------------------------------------------------
# 解析：谁的值优先，以及内置模型的刻意例外
# ---------------------------------------------------------------------------


class TestResolve:
    def test_custom_model_without_field_is_unknown(self) -> None:
        """自定义模型没写 model_type → unknown（本次事故的正主）。"""
        s = _settings(model="deepseek-flash", custom_models={"deepseek-flash": {"name": "ds"}})
        assert resolve_model_type(s) == MODEL_TYPE_UNKNOWN
        assert model_has_vision(resolve_model_type(s)) is False

    def test_custom_model_with_garbage_field_is_unknown(self) -> None:
        s = _settings(model="m", custom_models={"m": {"model_type": "多模态"}})
        assert resolve_model_type(s) == MODEL_TYPE_UNKNOWN

    @pytest.mark.parametrize("declared", [MODEL_TYPE_TEXT, MODEL_TYPE_MULTIMODAL])
    def test_explicit_declaration_is_honored(self, declared) -> None:
        s = _settings(model="m", custom_models={"m": {"model_type": declared}})
        assert resolve_model_type(s) == declared

    def test_builtin_without_override_keeps_shipped_declaration(self) -> None:
        """内置清单不能被打成看不见——那是用新错误修旧错误。

        `configs/models.toml` 的 [llm.models] 是维护者手写的发货表；qwen3.x 走
        DashScope 多模态端点，改成 Generation 会 "400 url error"。
        """
        s = _settings(model="qwen3.7-plus", custom_models={})
        assert resolve_model_type(s) == BUILTIN_MODEL_TYPE
        assert model_has_vision(resolve_model_type(s)) is True

    def test_builtin_exception_is_documented_not_accidental(self) -> None:
        """例外值必须是 multimodal 常量本身，别哪天被顺手改成 unknown。"""
        assert BUILTIN_MODEL_TYPE == MODEL_TYPE_MULTIMODAL

    def test_builtin_with_override_uses_the_override(self) -> None:
        """/models 改内置模型写的就是 custom_models[name] —— 声明优先于发货默认。"""
        s = _settings(model="qwen3.7-plus", custom_models={"qwen3.7-plus": {"model_type": MODEL_TYPE_TEXT}})
        assert resolve_model_type(s) == MODEL_TYPE_TEXT

    def test_explicit_name_wins_over_settings_model(self) -> None:
        """热切换查的是目标模型，不是 settings.model 那个启动快照。"""
        s = _settings(
            model="qwen3.7-plus",
            custom_models={"tiny-text": {"model_type": MODEL_TYPE_TEXT}},
        )
        assert resolve_model_type(s, "tiny-text") == MODEL_TYPE_TEXT

    def test_bare_object_does_not_crash(self) -> None:
        """缺字段的 settings 替身要走下去，不能把提示词组装或 provider 构造搞挂。"""
        assert resolve_model_type(object()) == BUILTIN_MODEL_TYPE


# ---------------------------------------------------------------------------
# 措辞：标签与回执都不许替模型下结论
# ---------------------------------------------------------------------------


class TestLabels:
    def test_tag_has_three_states(self) -> None:
        assert vision_tag(MODEL_TYPE_MULTIMODAL) == "多模态"
        assert vision_tag(MODEL_TYPE_TEXT) == "纯文本"
        assert vision_tag(MODEL_TYPE_UNKNOWN) == "视觉未确认"

    def test_unknown_is_never_read_aloud_as_multimodal(self) -> None:
        """两态标签（`"纯文本" if t=="text" else "多模态"`）正是这次的错源之一。"""
        assert vision_tag(None) != "多模态"
        assert describe_model_type("") != "多模态"

    def test_describe_carries_the_consequence(self) -> None:
        """回执里只说"未确认"不够：用户得知道它意味着什么，才知道要不要去改。"""
        assert "无视觉" in describe_model_type(MODEL_TYPE_UNKNOWN)
        assert describe_model_type(MODEL_TYPE_TEXT) == "纯文本"


class TestImageReceipt:
    def test_receipt_says_attached_not_delivered(self) -> None:
        """「已回传」是越权承诺：工具不知道 provider 会不会发出去。"""
        assert "已回传" not in image_receipt(True)
        assert "图片附上: 是" in image_receipt(True)

    def test_receipt_forbids_the_see_claim(self) -> None:
        """每次图片回执都得带上这句：长会话里 system prompt 会被忘掉，回执不会。"""
        assert "不等于看见" in image_receipt(True)
        assert "不得声称亲眼看过" in image_receipt(True)

    def test_no_image_receipt_claims_nothing(self) -> None:
        """编码失败（否）时不该留"看见"条款，也不该省掉体积说明的位置。"""
        line = image_receipt(False, fmt="jpeg", max_size=1280)
        assert line.startswith("图片附上: 否")
        assert "jpeg" in line and "1280" in line

    def test_size_note_kept_from_existing_format(self) -> None:
        """体积参数还得在：模型据此判断清晰度取舍（原措辞里就有这一项）。"""
        assert "最长边<=原图" in image_receipt(True, fmt="png", max_size=0)


# ---------------------------------------------------------------------------
# provider 层：三家已有的分支对齐，第四家（anthropic）补上
# ---------------------------------------------------------------------------


class TestProviderOmission:
    def test_openai_user_image_omitted_with_notice(self) -> None:
        msgs = [Message(role="user", content=[TextContent(text="看图"), _IMG])]
        out = _messages_to_openai(msgs, "sys", skip_images=True)
        assert "图片已省略" in out[1]["content"][0]["text"]

    def test_openai_tool_result_images_omitted_with_count(self) -> None:
        msgs = [
            Message(role="assistant", content=[ToolUseContent(id="c1", name="ScreenShot", input={})]),
            Message(role="user", content=[
                ToolResultContent(tool_use_id="c1", content="截图", images=[_IMG])
            ]),
        ]
        out = _messages_to_openai(msgs, "sys", skip_images=True)
        tool_msg = next(m for m in out if m.get("role") == "tool")
        assert "1 张图片" in tool_msg["content"] and "图片已省略" in tool_msg["content"]

    def test_anthropic_image_block_becomes_text_notice(self) -> None:
        """Anthropic 以前完全没有这一分支：图片对任何模型都照送。"""
        block = _block_to_anthropic(_IMG, skip_images=True)
        assert block["type"] == "text"
        assert "图片已省略" in block["text"]

    def test_anthropic_tool_result_images_flattened_to_string(self) -> None:
        tr = ToolResultContent(tool_use_id="t1", content="截图结果", images=[_IMG, _IMG])
        block = _block_to_anthropic(tr, skip_images=True)
        # 无视觉时 content 必须是串：留 list 里只有文字说明没有意义，还可能被端点拒
        assert isinstance(block["content"], str)
        assert "2 张图片" in block["content"] and "图片已省略" in block["content"]

    def test_anthropic_still_sends_images_when_it_has_vision(self) -> None:
        """反向守门：把省略做成永久省略，等于凭空废掉 Claude 的视觉能力。"""
        msgs = [Message(role="user", content=[_IMG])]
        _sys, api_msgs = _messages_to_anthropic(msgs, skip_images=False)
        assert api_msgs[0]["content"][0]["type"] == "image"

    def test_dashscope_text_path_reports_omission_instead_of_dropping(self) -> None:
        """Generation 端点不收图，但不能装作什么都没有。"""
        tr = Message(role="user", content=[
            ToolResultContent(tool_use_id="t1", content="截图", images=[_IMG])
        ])
        out = _messages_to_dashscope([tr], "SYS")
        tool_msg = next(m for m in out if m.get("role") == "tool")
        assert "图片已省略" in tool_msg["content"]

    def test_dashscope_multimodal_path_still_carries_images(self) -> None:
        """反向守门（同上一条）：unknown 才降级，declared multimodal 必须还能看图。"""
        msgs = [Message(role="user", content=[TextContent(text="看图"), _IMG])]
        out = _messages_to_dashscope_multimodal(msgs, "SYS")
        assert any("image" in item for item in out[1]["content"])

    def test_image_only_message_is_not_dropped_whole(self) -> None:
        """纯图片消息在无视觉模型下也必须留下一条 user 消息（三家同测）。

        这条不是构造的样本，是测 `IMAGE_OMITTED_NOTE` 同源时抓出的真缺陷：原先
        openai 那一支要求“有文字块可挂”才写省略说明，于是用户单发一张图时整条
        turn 直接消失——模型侧连“收到过”的痕迹都没有，比省略图片严重得多。
        """
        msgs = [Message(role="user", content=[_IMG])]

        oai = [m for m in _messages_to_openai(msgs, "s", skip_images=True) if m.get("role") == "user"]
        assert oai, "纯图片消息被整条丢掉了"
        assert "图片已省略" in oai[0]["content"][0]["text"]

        dash = [m for m in _messages_to_dashscope(msgs, "s") if m.get("role") == "user"]
        assert dash and "图片已省略" in dash[0]["content"]

        _sys, anth = _messages_to_anthropic(msgs, skip_images=True)
        assert anth and "图片已省略" in anth[0]["content"][0]["text"]

    def test_vision_path_still_keeps_image_only_message_as_image(self) -> None:
        """反向守门：有视觉时纯图片消息不能被换成文字。"""
        msgs = [Message(role="user", content=[_IMG])]
        out = _messages_to_openai(msgs, "s", skip_images=False)
        assert out[1]["content"][0]["type"] == "image_url"

    def test_omission_note_is_shared_verbatim(self) -> None:
        """四家措辞必须同一句：各写一份迟早有一处写成"纯文本模型"（unknown 时是假话）。"""
        msgs = [Message(role="user", content=[_IMG])]
        assert IMAGE_OMITTED_NOTE in _messages_to_openai(msgs, "s", skip_images=True)[1]["content"][0]["text"]
        assert IMAGE_OMITTED_NOTE in _block_to_anthropic(_IMG, skip_images=True)["text"]
        assert IMAGE_OMITTED_NOTE in _messages_to_dashscope(msgs, "s")[1]["content"]


# ---------------------------------------------------------------------------
# 契约：判据只有一个出处，四家都要有这一层
# ---------------------------------------------------------------------------


_PROVIDER_CLASSES = (OpenAIProvider, ZaiProvider, DashScopeProvider, AnthropicProvider)


class TestProviderSurfaceContract:
    @pytest.mark.parametrize("cls", _PROVIDER_CLASSES, ids=lambda c: c.__name__)
    def test_ctor_takes_model_type_defaulting_to_unknown(self, cls) -> None:
        """默认值必须是 unknown：漏传参数不该变成凭空给模型长一双眼睛。"""
        param = inspect.signature(cls.__init__).parameters.get("model_type")
        assert param is not None, f"{cls.__name__} 没有 model_type 参数"
        assert param.default == MODEL_TYPE_UNKNOWN, f"{cls.__name__} 默认值不是 unknown"

    @pytest.mark.parametrize("cls", _PROVIDER_CLASSES, ids=lambda c: c.__name__)
    def test_exposes_set_model_type_and_has_vision(self, cls) -> None:
        """`set_model_type` 是热切换复用的同步口，`has_vision` 是给上层的只读窗。"""
        assert callable(getattr(cls, "set_model_type", None)), cls.__name__
        assert isinstance(getattr(cls, "has_vision", None), property), cls.__name__

    @pytest.mark.parametrize("cls", _PROVIDER_CLASSES, ids=lambda c: c.__name__)
    def test_stream_uses_the_single_arbiter(self, cls) -> None:
        """图片通路的判据只能来自 `model_has_vision`。

        文本级断言是这里唯一能防住"新写一处 `== "text"`"的手段：那种代码在
        unknown 之下行为错，而行为测跑的是构造好的两值样本，抓不到它。
        """
        src = inspect.getsource(cls.stream)
        assert "model_has_vision" in src, f"{cls.__name__}.stream 没用统一判据"
        assert 'self._model_type == "text"' not in src
        assert 'self._model_type != "text"' not in src

    def test_anthropic_receives_model_type_from_registry(self) -> None:
        """注册表不下发 model_type，AnthropicProvider 的参数就永远是构造默认值。"""
        assert "model_type" in PROVIDER_REGISTRY["anthropic"].init_keys

    def test_vendor_meta_has_no_model_type_default(self) -> None:
        """厂商级默认值是这次的源头之一，不许回来（视觉是厂商×模型两维决定的）。"""
        assert not hasattr(ProviderMeta(), "model_type")


class TestToolReceiptContract:
    """三个图片类工具必须共用同一句措辞（`image_receipt`）。"""

    @pytest.mark.parametrize("name", list(_TOOL_SOURCES))
    def test_no_leftover_delivery_claim(self, name) -> None:
        path = self._repo_root() / _TOOL_SOURCES[name]
        text = path.read_text(encoding="utf-8")
        assert "图片已回传" not in text, f"{name} 还在承诺『已回传』"
        assert "image_receipt(" in text, f"{name} 没走统一措辞函数"

    def test_prompt_stops_claiming_it_can_really_see(self) -> None:
        """`text_blocks` 里那两句"你能真正看到"是同一个谎的提示词版本。"""
        text = (self._repo_root() / "agent/prompts/text_blocks.py").read_text(encoding="utf-8")
        assert "你能真正看到" not in text

    def test_self_check_discipline_has_a_degraded_path(self) -> None:
        """交付自查纪律必须自带降级出口：工具坏着、又只写"必须亲眼看过"，
        模型就只能拿"假设我能看到"填空（贪吃蛇案的具体机制）。"""
        text = (self._repo_root() / "agent/prompts/text_blocks.py").read_text(encoding="utf-8")
        assert "自查手段按能力降级" in text
        assert "BrowserGetText" in text

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 提示词层：无视觉时给降级链，有视觉时一个字节都不加
# ---------------------------------------------------------------------------


class TestVisionPromptSection:
    def _section(self, settings, model_name: str = "") -> str:
        from agent.prompts.system import _vision_section

        return _vision_section(settings, model_name)

    @pytest.mark.parametrize("declared", [MODEL_TYPE_TEXT, MODEL_TYPE_UNKNOWN, "audio"])
    def test_no_vision_models_get_the_block(self, declared) -> None:
        s = _settings(model="m", custom_models={"m": {"model_type": declared}})
        text = self._section(s)
        assert text.startswith("# 当前模型的视觉输入")
        # 三个成分缺一不可：禁止断言、替代手段、以及"验不到就明说"
        assert "不要说你能看见" in text
        assert "BrowserGetText" in text
        assert "没有做视觉验证" in text
        # 还得给出改的办法（P4 记录 ②：宁可少给能力，也要把改的路写明）
        assert "/models" in text

    def test_vision_model_gets_zero_bytes(self) -> None:
        """有视觉时返回空串：不给提示词多一行字，也不制造"我不能看"的假约束。"""
        s = _settings(model="m", custom_models={"m": {"model_type": MODEL_TYPE_MULTIMODAL}})
        assert self._section(s) == ""

    def test_builtin_shipped_model_gets_zero_bytes(self) -> None:
        assert self._section(_settings(model="qwen3.7-plus")) == ""

    def test_none_settings_is_silent(self) -> None:
        """拿不到 settings 时装作没这回事，而不是宣称模型看不见。"""
        assert self._section(None) == ""

    def test_probe_crash_is_fail_open(self) -> None:
        """裁决点自己坏掉不能挡住提示词组装。"""
        class _Boom:
            model = "m"

            @property
            def custom_models(self):
                raise RuntimeError("配置读坏了")

        assert self._section(_Boom()) == ""

    def test_explicit_model_name_overrides_snapshot(self) -> None:
        """切换后的新模型必须拿到自己的段——settings.model 是启动快照。"""
        s = _settings(
            model="qwen3.7-plus",
            custom_models={"tiny": {"model_type": MODEL_TYPE_TEXT}},
        )
        assert self._section(s) == ""
        assert self._section(s, "tiny").startswith("# 当前模型的视觉输入")

    def test_section_lands_before_tool_list(self, monkeypatch, tmp_path) -> None:
        """顺序纪律：先让它知道看不见，再给它工具名单。"""
        from agent.core.tool import build_default_registry
        from agent.prompts import system as system_mod

        monkeypatch.setattr(system_mod, "_CAPABILITY_CACHE", "")  # 跳过真实探测
        reg = build_default_registry()
        s = _settings(model="m", custom_models={"m": {"model_type": MODEL_TYPE_UNKNOWN}})
        s.workdir = str(tmp_path)  # type: ignore[attr-defined]

        prompt = system_mod.build_system_prompt(str(tmp_path), reg, settings=s, model_name="m")

        assert "# 当前模型的视觉输入" in prompt
        assert prompt.find("# 当前模型的视觉输入") < prompt.find("# 可用工具")

    def test_compose_helper_keeps_user_append(self, monkeypatch, tmp_path) -> None:
        """`compose_system_prompt` 存在的意义就是别漏掉末尾那段用户自定义内容。"""
        from agent.core.tool import ToolRegistry
        from agent.prompts import system as system_mod

        monkeypatch.setattr(system_mod, "_CAPABILITY_CACHE", "")
        s = _settings(model="qwen3.7-plus")
        s.workdir = str(tmp_path)  # type: ignore[attr-defined]
        s.enable_thinking = True  # type: ignore[attr-defined]
        s.system_prompt_append = "先生专属的附加要求"  # type: ignore[attr-defined]

        prompt = system_mod.compose_system_prompt(s, ToolRegistry())

        assert prompt.endswith("先生专属的附加要求")


# ---------------------------------------------------------------------------
# 热切换：图片通路翻转时要重拼提示词，不翻转就别丢前缀缓存
# ---------------------------------------------------------------------------


class _Emitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def emit(self, kind: str, payload: object = None) -> None:
        self.events.append((kind, payload))


class _Loop:
    def __init__(self) -> None:
        self.switched: list[tuple[object, str]] = []

    def switch_model(self, provider: object, name: str):
        self.switched.append((provider, name))
        # 不返回旧 provider：本测试不需要验证 close() 路径，省一个 mock


class _Engine:
    """够用的 ChatEngine 替身：只实现 handle_switch_model 会碰到的成员。"""

    def __init__(self, provider: object) -> None:
        self._provider = provider
        self._query_loop = _Loop()
        self._settings = SimpleNamespace(workdir=".", enable_thinking=True)
        self._provider_settings = self._settings
        self._model = "old-model"
        self._model_override = ""
        self._emitter = _Emitter()
        self._ui = SimpleNamespace(info=lambda *a, **k: None, warn=lambda *a, **k: None)
        self._active_registry = object()
        self.rebuilds = 0

    def _rebuild_system_prompt(self) -> None:
        self.rebuilds += 1


class TestSwitchRefresh:
    def test_flipped_detected_in_both_directions(self) -> None:
        from agent.model_manager import _vision_flipped

        vision = SimpleNamespace(has_vision=True)
        plain = SimpleNamespace(has_vision=False)
        assert _vision_flipped(vision, plain) is True
        assert _vision_flipped(plain, vision) is True
        assert _vision_flipped(vision, vision) is False
        assert _vision_flipped(plain, plain) is False

    def test_provider_without_flag_is_treated_as_vision_capable(self) -> None:
        """测试替身 / mock provider 没有 has_vision：按「有视觉」处理。

        代价是多拼一次提示词（丢一段前缀缓存），比漏掉真正需要降级约束的切换便宜。
        """
        from agent.model_manager import _vision_flipped

        assert _vision_flipped(object(), SimpleNamespace(has_vision=False)) is True
        assert _vision_flipped(object(), object()) is False

    @pytest.mark.parametrize(
        ("old_vision", "new_vision", "expected_rebuilds"),
        [(True, False, 1), (False, True, 1), (True, True, 0), (False, False, 0)],
    )
    async def test_serve_switch_rebuilds_only_on_flip(
        self, monkeypatch, old_vision, new_vision, expected_rebuilds
    ) -> None:
        import agent.model_manager as mm
        from agent.serve.engine import model_switch

        new_provider = SimpleNamespace(has_vision=new_vision)
        monkeypatch.setattr(
            mm,
            "_build_switched_provider",
            lambda *a, **k: (new_provider, "纯文本（DeepSeek）", SimpleNamespace(workdir=".")),
        )
        engine = _Engine(SimpleNamespace(has_vision=old_vision))

        await model_switch.handle_switch_model(engine, "new-model")

        assert engine.rebuilds == expected_rebuilds
        # 切换本身照常落地：重拼提示词只是附带动作，不能反过来卡住切换
        assert engine._provider is new_provider
        assert engine._model == "new-model"
