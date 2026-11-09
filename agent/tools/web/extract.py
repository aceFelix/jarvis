"""WebExtract —— 单页结构化字段抽取（爬虫能力 P1）。

为什么单独一个工具：
- ``WebFetch`` 只把整页转 Markdown 交给模型，想看“标题/作者/摘要”只能自己读全文；
- ``WebExtract`` 用 CSS 选择器把字段一次抽成表，并通过 ``ToolResult.trace_meta``
  给左侧「爬虫驾驶舱」的“抽取 // EXTRACT”区提供数据（工具本身不依赖 UI）。

选择器实现：``beautifulsoup4`` + 内置 ``html.parser``（用户 2026-11-09 决策，
不引入 lxml，避免桌面打包带编译依赖）。方案见 ``docs/plans/crawler-cockpit.md``。

@author aceFelix
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool
from agent.tools.web.web import _fetch_url_async, _is_safe_host

# 单字段值上限：正文/HTML 可能很大，抽取表只需要摘要，超出截断避免撑爆回执
_FIELD_VALUE_MAX = 800
# 一次最多抽多少个字段（驾驶舱只展示前 3 个，模型仍能拿到完整表）
_MAX_FIELDS = 12
_MAX_NAME_CHARS = 40


def crawl_enabled(settings: Any) -> bool:
    """``[crawl] enabled`` 总开关；Settings 缺失时默认开启。

    WebExtract 与 Crawl 共用同一闸：抽取能力属于爬虫域，用户关掉爬虫时
    两个工具都应拒绝执行，而不是只关多页抓取。

    @author aceFelix
    """
    return bool(getattr(settings, "crawl_enabled", True))


def _extract_one(scope: Tag | BeautifulSoup, spec: dict[str, Any]) -> str:
    """按单个字段定义从作用域内取值。

    Args:
        scope: 已限定范围的 bs4 节点（没有 ``selector`` 时是整页 soup）。
        spec: ``{"name": ..., "selector": ..., "attr": ...}``。

    Returns:
        抽到的文本/属性值；未命中或为空返回空字符串（由调用方记入缺失）。
        ``attr="html"`` 返回内部 HTML，保留原始空白。
    """
    selector = str(spec.get("selector") or "").strip()
    if not selector:
        return ""
    try:
        target = scope.select_one(selector)
    except Exception:
        # 选择器语法错误按“没抽到”处理：不中断整页抽取，交给 missing 暴露
        return ""
    if target is None:
        return ""

    attr = str(spec.get("attr") or "text").strip().lower()
    if attr in ("", "text"):
        text = target.get_text(" ", strip=True)
        # 文本压成单行，便于表格展示
        return " ".join(text.split())[:_FIELD_VALUE_MAX]
    if attr == "html":
        return target.decode_contents().strip()[:_FIELD_VALUE_MAX]

    raw = target.get(attr)
    if isinstance(raw, list):
        # class 等属性是列表，拼成空格分隔
        raw = " ".join(str(item) for item in raw)
    value = " ".join(str(raw or "").split())
    return value[:_FIELD_VALUE_MAX]


def extract_fields(
    html: str,
    fields: list[dict[str, Any]],
    *,
    selector: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """用 CSS 选择器从一段 HTML 中抽取字段（纯函数，可单测）。

    失败语义（见方案 §5.3）：
    - 字段未命中 / 值为空 → 记入 ``missing``，**不写空字符串**；
    - 作用范围 ``selector`` 没命中 → 全部字段记缺失，不退回整页抽取，
      避免用户以为限定在 ``article`` 内、实际却抽到页面其它位置。

    Args:
        html: 页面 HTML 原文。
        fields: 字段定义列表，每项至少含 ``name`` 与 ``selector``。
        selector: 可选的作用范围（如 ``article``）。

    Returns:
        ``(values, missing)``：抽到的 ``{字段名: 值}`` 与缺失字段名列表。
    """
    soup = BeautifulSoup(html or "", "html.parser")
    scope: Tag | BeautifulSoup = soup
    if selector:
        try:
            scoped = soup.select_one(selector)
        except Exception:
            scoped = None
        if scoped is None:
            return {}, [str(item.get("name") or "?") for item in fields]
        scope = scoped

    values: dict[str, str] = {}
    missing: list[str] = []
    for spec in fields:
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        value = _extract_one(scope, spec)
        if value:
            values[name] = value
        else:
            missing.append(name)
    return values, missing


def _format_summary(
    url: str,
    status: int,
    values: dict[str, str],
    missing: list[str],
    total: int,
    selector: str | None,
) -> str:
    """把抽取结果整理成给模型看的文本（结构化数据另走 ``trace_meta``）。"""
    lines = [f"URL: {url}", f"HTTP {status} | 抽取 {len(values)}/{total} 个字段"]
    if selector:
        lines[1] += f" | 作用范围: {selector}"
    lines.append("")
    if values:
        for name, value in values.items():
            lines.append(f"- {name}: {value}")
    else:
        lines.append("（未抽到任何字段，请检查选择器是否匹配当前页面结构）")
    if missing:
        lines.append("")
        lines.append(f"缺失字段: {', '.join(missing)}")
    return "\n".join(lines)


class WebExtractTool(Tool):
    """抓取单页并按 CSS 选择器抽取结构化字段。

    读取一次页面、抽一次字段；结果同时返回给模型（文本表）与爬虫驾驶舱
    （``tool_result.trace_meta["web_extract"]``，由 trace 派生事件）。

    @author aceFelix
    """

    name = "WebExtract"
    description = (
        "抓取单个网页并按 CSS 选择器抽取结构化字段（标题/作者/摘要/链接等）。"
        "fields 每项为 {name, selector, attr}，attr 常用 text（默认）/html/href/src/content。"
        "抽不到或为空的字段记为缺失，不返回空字符串。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要抽取的页面 URL（http/https）"},
            "selector": {
                "type": "string",
                "description": "可选：抽取作用范围（CSS 选择器），如 article",
            },
            "fields": {
                "type": "array",
                "description": f"要抽取的字段定义，最多 {_MAX_FIELDS} 个",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "字段名（报表列名）"},
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "attr": {
                            "type": "string",
                            "description": "text（默认）/html/href/src，或任意 HTML 属性名",
                        },
                    },
                    "required": ["name", "selector"],
                },
            },
        },
        "required": ["url", "fields"],
    }
    max_result_chars = 12_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """``[crawl] enabled=false`` 时拒绝：抽取随爬虫总开关一起关。"""
        if not crawl_enabled(getattr(ctx, "settings", None)):
            return PermissionResult.deny("爬虫已关闭（[crawl] enabled = false）")
        return PermissionResult.allow("只读网络访问")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        """校验 URL 与字段定义；错误直接回给模型便于自我修正。"""
        url = str(args.get("url") or "").strip()
        if not url:
            return ValidationResult.fail("url 不能为空")
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return ValidationResult.fail(f"仅支持 http/https，收到: {parsed.scheme}")
        if not parsed.netloc:
            return ValidationResult.fail("URL 缺少主机名")
        # 与 WebFetch 同一 SSRF 口径
        if not _is_safe_host(parsed.hostname or ""):
            return ValidationResult.fail(f"安全策略: 禁止访问内网/本地地址: {parsed.hostname}")

        fields = args.get("fields")
        if not isinstance(fields, list) or not fields:
            return ValidationResult.fail("fields 不能为空，至少提供一个字段定义")
        if len(fields) > _MAX_FIELDS:
            return ValidationResult.fail(f"fields 最多 {_MAX_FIELDS} 个，收到 {len(fields)} 个")
        for index, spec in enumerate(fields, 1):
            if not isinstance(spec, dict):
                return ValidationResult.fail(f"fields[{index}] 必须是对象")
            name = str(spec.get("name") or "").strip()
            selector = str(spec.get("selector") or "").strip()
            if not name or not selector:
                return ValidationResult.fail(f"fields[{index}] 缺少 name 或 selector")
            if len(name) > _MAX_NAME_CHARS:
                return ValidationResult.fail(f"fields[{index}].name 过长（≤{_MAX_NAME_CHARS} 字）")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """抓取 → 抽取 → 返回文本表；结构化字段写入 ``trace_meta``。"""
        url = str(args["url"]).strip()
        selector = str(args.get("selector") or "").strip() or None
        fields = [dict(item) for item in args["fields"] if isinstance(item, dict)]
        if ctx.ui:
            ctx.ui.info(f"抽取中: {url}")

        try:
            content, _content_type, status = await _fetch_url_async(url)
        except Exception as exc:  # noqa: BLE001 - 网络异常统一转成失败回执
            return ToolResult(data=f"抽取失败: {type(exc).__name__}: {exc}", is_error=True)
        if status >= 400:
            return ToolResult(
                data=f"HTTP {status}: 页面抓取失败（URL 可能无效或服务器拒绝访问）",
                is_error=True,
            )

        values, missing = extract_fields(content, fields, selector=selector)
        data = _format_summary(
            url, status, values, missing, len(fields), selector
        )
        # trace 派生通道：只放轻量结构化数据，不参与 tool_result 截断
        trace_meta = {
            "web_extract": {
                "url": url,
                "fields": values,
                "missing": missing,
            }
        }
        if selector:
            trace_meta["web_extract"]["selector"] = selector
        return ToolResult(data=data, trace_meta=trace_meta)


__all__ = ["WebExtractTool", "crawl_enabled", "extract_fields"]
