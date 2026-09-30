"""文本态工具调用兜底解析器。

背景（@author aceFelix）:
    jarvis 走的是原生 function-calling（OpenAI `tools` 参数），provider 只认结构化的
    `delta.tool_calls`。但部分模型 / 第三方兼容网关偶发地把工具调用以「文本」形式吐进
    `delta.content`——DeepSeek 用的是其内部序列化格式 DSML（形如
    `<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="X"><｜｜DSML｜｜ parameter ...>`，
    `｜` 为全角竖线 U+FF5C）。这类 token 一旦漏进正文字段，provider 不识别，就会被原样
    显示成乱码，且工具不会触发（表现为「模型抽风，重新说一句才好使」）。

    本模块提供两道防线与一个组合封装：
    1. StreamingLeakFilter —— 流式过滤器，检测到泄漏标记后停止把后续文本当正文回显，
       改为缓存待解析（避免用户看到裸 DSML）。
    2. parse_textual_tool_calls —— 把缓存的泄漏文本（DSML 或纯 XML 两种形态）解析成
       结构化工具调用，交回 provider 转成 ToolCall 事件，让工具照常执行。
    3. TextualToolCallGuard —— 上面两者的组合封装，Provider 侧三行接入即可复用，
       避免每条协议路径各抄一遍「feed/flush/parse」流程。

设计约束:
    - 归一化用正则把 DSML 命名空间前缀去掉，统一成 `<invoke>` / `<parameter>` 的 XML
      形态后复用同一套解析逻辑，兼容直接吐 XML 的网关。
    - 只解析「工具名在已注册集合内」的调用，避免把正文里恰好出现的尖括号误当工具。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# DeepSeek 特殊 token 的分隔竖线。⚠ 真实泄漏样本用的是全角竖线 U+FF5C
# （2026-09-30 从 ~/.jarvis/sessions 存档原文按码点核对确认）；此前实现误用
# U+FF5D（全角右括号），标记匹配不上，兜底对真实样本从未生效。为兼容不同
# 端点的写法，这里把三种竖线全部纳入匹配：U+FF5C / U+FF5D / 半角 U+007C。
# 用变量拼接构造标签，避免源码里出现完整的尖括号标签字面量（与外层工具的参数分隔符冲突）。
_PIPE_CHARS = chr(0xFF5C) + chr(0xFF5D) + "|"
_INVOKE = "inv" + "oke"
_PARAM = "para" + "meter"
# 泄漏标记：DSML 正文里必然出现的「`<` + 两个竖线」（正文散文不会出现全角竖线）
_LEAK_RE = re.compile("<[" + _PIPE_CHARS + "]{2}")
# DSML 命名空间前缀，如 `<｜｜DSML｜｜ ` → 归一化为 `<`
_DSML_PREFIX_RE = re.compile(
    "[" + _PIPE_CHARS + "]{2}DSML[" + _PIPE_CHARS + "]{2}\\s*"
)


@dataclass
class ParsedToolCall:
    """从文本解析出的一个工具调用。"""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


def normalize_leaked_markup(text: str) -> str:
    """把 DSML 形态归一化成 XML 形态（去掉 `<｜｜DSML｜｜ ` 命名空间前缀）。

    `<｜｜DSML｜｜ invoke name="X">` → `<invoke name="X">`，
    `</｜｜DSML｜｜ parameter>` → 对应闭合标签。非 DSML 文本原样返回。
    """
    if not text:
        return text
    return _DSML_PREFIX_RE.sub("", text)


def has_leaked_toolcall(text: str) -> bool:
    """粗判一段文本里是否含泄漏的工具调用标记（DSML 或 XML invoke/parameter）。"""
    if not text:
        return False
    if _LEAK_RE.search(text):
        return True
    open_invoke = "<" + _INVOKE + " "
    open_param = "<" + _PARAM + " "
    return open_invoke in text or open_param in text


def _coerce_value(raw: str, attrs: str) -> Any:
    """按 parameter 标签的 string 属性决定值类型。

    - `string="true"`（或无类型线索）→ 保持字符串；
    - `string="false"` → 尝试按 JSON 解析成数字/布尔/对象；
    - 未显式标注时：纯数字/true/false 也尝试转型，其余保持字符串（命令类参数多为字符串）。
    """
    raw = raw or ""
    explicit_str = 'string="true"' in attrs
    explicit_json = 'string="false"' in attrs
    if explicit_str:
        return raw
    if explicit_json:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
    # 无显式标注：仅对明显的标量做推断，避免把 shell 命令误转
    s = raw.strip()
    if s in ("true", "false", "null"):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return raw
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d+", s):
        return float(s)
    return raw


def parse_textual_tool_calls(
    text: str, valid_names: set[str] | None = None
) -> list[ParsedToolCall]:
    """把泄漏的工具调用文本解析成结构化工具调用列表。

    Args:
        text: 含 DSML 或 XML 工具调用标记的原始文本。
        valid_names: 已注册工具名集合；非空时只保留名字在集合内的调用，
            防止把正文里正常出现的尖括号误判为工具调用。None 表示不过滤。

    Returns:
        ParsedToolCall 列表（可能为空）。解析不到任何合法调用时返回 []，
        调用方据此决定是当普通文本处理。
    """
    if not text or not has_leaked_toolcall(text):
        return []
    norm = normalize_leaked_markup(text)
    calls: list[ParsedToolCall] = []
    # 匹配一个 invoke 块：`<invoke name="X"> ... </invoke>` 或自闭合 `<invoke name="X"/>`
    invoke_re = re.compile(
        "<" + _INVOKE + "\\s+name=\"([^\"]+)\"\\s*(?:/>|>(.*?)</" + _INVOKE + ">)",
        re.DOTALL,
    )
    param_re = re.compile(
        "<" + _PARAM + "\\s+name=\"([^\"]+)\"([^>]*?)(?:/>|>(.*?)</" + _PARAM + ">)",
        re.DOTALL,
    )
    for m in invoke_re.finditer(norm):
        name = m.group(1).strip()
        if valid_names is not None and name not in valid_names:
            continue
        inner = m.group(2) or ""
        args: dict[str, Any] = {}
        for pm in param_re.finditer(inner):
            key = pm.group(1).strip()
            attrs = pm.group(2) or ""
            val_raw = pm.group(3) or ""
            args[key] = _coerce_value(val_raw, attrs)
        calls.append(
            ParsedToolCall(id=f"text_call_{len(calls)}_{name}", name=name, input=args)
        )
    return calls


def _partial_marker_len(buf: str) -> int:
    """返回 buf 结尾处「可能是泄漏标记前缀」的长度，用于流式回退缓冲。

    标记为 `<` + 两个竖线（长 3），故最多回看 2 个尾字符：`<`、`<`+单个竖线。
    """
    if len(buf) >= 2 and buf[-2] == "<" and buf[-1] in _PIPE_CHARS:
        return 2
    if buf.endswith("<"):
        return 1
    return 0


class StreamingLeakFilter:
    """流式文本泄漏过滤器（provider 逐块喂 content 用）。

    正常文本立即放行；一旦检测到 `<｜｜` 泄漏标记，标记前的文字照常输出、标记起的
    所有后续文本转入 call_text 缓存并停止回显（避免裸 DSML 闪现在界面上）。
    """

    def __init__(self) -> None:
        self._pending = ""      # 尚未定论的尾缓冲（可能是标记前缀）
        self._suppressed = False
        self._call_text = ""

    @property
    def suppressed(self) -> bool:
        """是否已进入抑制态（检测到泄漏标记）。"""
        return self._suppressed

    @property
    def call_text(self) -> str:
        """抑制态下缓存的原始泄漏文本，供 parse_textual_tool_calls 解析。"""
        return self._call_text

    def feed(self, chunk: str) -> str:
        """喂入一段 content，返回此刻可安全显示（非泄漏）的文本。"""
        if not chunk:
            return ""
        if self._suppressed:
            self._call_text += chunk
            return ""
        self._pending += chunk
        m = _LEAK_RE.search(self._pending)
        if m:
            emit = self._pending[: m.start()]
            self._call_text = self._pending[m.start():]
            self._pending = ""
            self._suppressed = True
            return emit
        # 未命中完整标记：回退缓冲可能的标记前缀，其余放行
        hold = _partial_marker_len(self._pending)
        emit = self._pending[: len(self._pending) - hold] if hold else self._pending
        self._pending = self._pending[len(self._pending) - hold:] if hold else ""
        return emit

    def flush(self) -> str:
        """流结束时释放尾缓冲里剩余的确定安全文本。"""
        if self._suppressed:
            return ""
        out = self._pending
        self._pending = ""
        return out


class TextualToolCallGuard:
    """文本态工具调用兜底守卫 ——「流式过滤 + 泄漏解析」的组合封装。

    Provider 侧只需三个动作：逐块 `feed()` 拿可回显正文、流末 `flush()` 释放尾缓冲、
    `drain()` 取解析出的真实调用。四步流程（feed / flush / suppressed / parse）收进
    这里，各协议路径接入时不再各自复制同一套代码。

    典型接法（流式循环内）：
        guard = TextualToolCallGuard(t.name for t in tools)
        ...
        safe = guard.feed(delta_text)   # 正常正文即时回显
        ...
        tail = guard.flush()            # 流末释放尾缓冲
        for pc in guard.drain():        # 无结构化调用时转真调用
            yield ToolCall(id=pc.id, name=pc.name, input=pc.input)
        # 一个调用也没解析出来时，必须把吞掉的原文补回显（guard.rescue_text），
        # 否则会出现「半句话静默结束」，比裸码更难排查。

    @author aceFelix
    """

    def __init__(self, tool_names: Iterable[str] = ()) -> None:
        """初始化守卫。

        Args:
            tool_names: 本轮已注册的工具名集合；解析只保留落在集合内的调用，
                防止把正文里恰好出现的尖括号误判成工具调用（空集合 = 全部丢弃）。
        """
        self._filter = StreamingLeakFilter()
        self._valid_names = set(tool_names)
        # 解析结果缓存：drain() 与 rescue_text 都要知道「到底解析出了几个」，
        # 缓存避免同一份泄漏文本重复解析两次
        self._calls: list[ParsedToolCall] | None = None

    def feed(self, chunk: str) -> str:
        """喂入一段正文增量，返回此刻可安全回显的文本。"""
        return self._filter.feed(chunk)

    def flush(self) -> str:
        """流结束时释放尾缓冲里剩余的确定安全文本。"""
        return self._filter.flush()

    @property
    def suppressed(self) -> bool:
        """是否已进入抑制态（正文里出现过泄漏标记）。"""
        return self._filter.suppressed

    def drain(self) -> list[ParsedToolCall]:
        """取出泄漏文本解析出的工具调用；未发生抑制时返回空列表。

        结果会缓存，多次调用返回同一列表（rescue_text 依赖它判断兜底是否成功）。
        """
        if self._calls is None:
            if not self._filter.suppressed:
                self._calls = []
            else:
                self._calls = parse_textual_tool_calls(
                    self._filter.call_text, self._valid_names
                )
        return self._calls

    @property
    def rescue_text(self) -> str:
        """兜底失败时的补救文本：发生过抑制、却一个调用也没解析出来。

        典型场景：本轮根本没发工具 schema（如 chat_detection 判定为纯聊天，
        `valid_names` 为空集），所有解析出的调用被校验过滤掉。此时若不把吞掉的
        原文补回显，用户只会看到半句话就结束，既无工具也无报错——比原来的裸码
        更难排查。返回空串表示无需补救。
        """
        if not self._filter.suppressed or self.drain():
            return ""
        return self._filter.call_text
