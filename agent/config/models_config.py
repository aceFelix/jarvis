"""模型配置（models.toml）——模型域的路径、外科式回写与拆分迁移。

背景（aceFelix）：
模型域配置（provider / api_format / model / last_model / api_key / base_url /
max_tokens / temperature / enable_thinking / thinking_budget / vendor_fallback
+ [llm.models] + [llm.custom_models.*]）原与其余配置同住 settings.toml，带来两类
真实伤害：
1) 程序回写（/models、jarvis init、切换模型）与手写配置混在一处：顶层键一旦落到
   [llm.models] 之后就会被 TOML 静默解析进该子表（「配置写了不生效」），重复
   section 更会让整份配置解析失败；
2) 密钥（api_key）散落在近 300 行的通用配置里，暴露面大。
故拆出 models.toml：与 settings.toml 同层放置（项目级 configs/、用户级 ~/.jarvis/，
兼容回退 ~/.my-agent/），密钥跟随模型配置同处一文件（aceFelix 要求）。

加载语义（见 settings.load_settings）：
    项目 settings.toml → 项目 models.toml → 用户 settings.toml → 用户 models.toml
    → 环境变量 → CLI 参数
即「同层 models.toml 覆盖同层 settings.toml，用户级整体覆盖项目级」，向后兼容：
老配置里的模型键留在 settings.toml 也照常生效，迁移只是整理。

模块职责：
- models_path_for / user_models_path：路径推导（示例模板对应 models.example.toml）
- read_toml_text / write_toml_text：文本读写（保持原换行风格、剥离 BOM）
- upsert_top_scalar / upsert_table_block：保留注释的外科式写入
- upsert_custom_model / upsert_last_model / save_init_model：模型域写入封装
- split_model_config / auto_split_user_config：幂等拆分迁移

@author aceFelix
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

# 模型域顶层键：必须写在任何 [section] 之前（TOML 顺序陷阱）
MODEL_TOP_KEYS: frozenset[str] = frozenset(
    {
        "provider",
        "api_format",
        "model",
        "last_model",
        "api_key",
        "base_url",
        "dashscope_api_key",
        "max_tokens",
        "temperature",
        "enable_thinking",
        "thinking_budget",
        "vendor_fallback",
    }
)

# 模型域 section 名前缀：llm / llm.models / llm.custom_models."xxx"
MODEL_SECTION_PREFIX = "llm"

MODELS_FILE_NAME = "models.toml"
MODELS_EXAMPLE_FILE_NAME = "models.example.toml"
SETTINGS_FILE_NAME = "settings.toml"
_SETTINGS_EXAMPLE_NAME = "settings.example.toml"

# 行级识别：section 头（容忍行内空白与行尾注释）与键值行
# 行尾注释必须容忍：[memory.embedding]  # 说明 这种写法合法，识别不到会把段内的
# provider = "dashscope" 误判为顶层模型键而搬错位置。
_SECTION_RE = re.compile(r"^\[(.+?)\][ \t]*(?:#.*)?$")
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=")

# 新建 models.toml 时写入的头部说明
HEADER_LINES: tuple[str, ...] = (
    "# jarvis 模型配置（models.toml）",
    "#",
    "# 与 settings.toml 同层：项目级 configs/models.toml、用户级 ~/.jarvis/models.toml。",
    "# 加载顺序（后者覆盖前者）：",
    "#   configs/settings.toml → configs/models.toml",
    "#   → ~/.jarvis/settings.toml → ~/.jarvis/models.toml",
    "#   → 环境变量（JARVIS_* / DASHSCOPE_API_KEY 等）→ CLI 参数",
    "#",
    "# 本文件承载模型域配置：当前模型、密钥、可选模型列表与自定义模型。",
    "# provider / api_format / model / last_model / api_key / base_url / max_tokens /",
    "# temperature / enable_thinking / thinking_budget / vendor_fallback 等顶层键",
    "# 必须写在任何 [section] 之前——[llm.models] 之后的顶层键会被 TOML 静默解析进",
    "# 该子表，导致「配置写了不生效」。",
    "#",
    "# [llm.custom_models.\"*\"] 与 last_model 由程序回写（/models、jarvis init、",
    "# 切换模型），手动编辑保留其结构即可；api_key 建议走环境变量或系统 keyring。",
)


# ---------------------------------------------------------------------------
# 路径推导
# ---------------------------------------------------------------------------


def models_path_for(settings_path: Path) -> Path:
    """由 settings.toml 路径推导同层 models.toml 路径。

    示例模板（settings.example.toml）对应 models.example.toml，
    避免分发模板被当成用户实际配置加载。

    @author aceFelix
    """
    if settings_path.name == _SETTINGS_EXAMPLE_NAME:
        return settings_path.with_name(MODELS_EXAMPLE_FILE_NAME)
    return settings_path.with_name(MODELS_FILE_NAME)


def user_config_paths() -> tuple[Path, Path]:
    """解析用户级配置的成对路径，返回 ``(settings.toml, models.toml)``。

    目录规则：优先 ``~/.jarvis``；仅当 ``~/.jarvis/settings.toml`` 不存在而
    ``~/.my-agent/settings.toml`` 存在时（老版本目录布局）使用 ``~/.my-agent``。
    两个文件必须同目录成对返回，否则读写会落到不同目录，出现「写了不生效」。

    @author aceFelix
    """
    home = Path.home()
    jarvis_dir = home / ".jarvis"
    legacy_dir = home / ".my-agent"
    if not (jarvis_dir / SETTINGS_FILE_NAME).exists() and (
        legacy_dir / SETTINGS_FILE_NAME
    ).exists():
        base = legacy_dir
    else:
        base = jarvis_dir
    return base / SETTINGS_FILE_NAME, base / MODELS_FILE_NAME


def user_models_path() -> Path:
    """用户级模型配置路径（默认 ~/.jarvis/models.toml，兼容 ~/.my-agent/）。

    与 ``settings.load_settings`` 的用户级目录规则共用 ``user_config_paths``，
    保证写入目标与加载来源始终是同一个文件。

    @author aceFelix
    """
    return user_config_paths()[1]


# ---------------------------------------------------------------------------
# 文本读写（保持换行风格，避免 TOML 换行不一致故障）
# ---------------------------------------------------------------------------


def read_toml_text(path: Path) -> tuple[str, str]:
    """读取 TOML 文本，返回 ``(内容, 换行符)``。

    内容统一规范成 ``\\n`` 便于行级处理，换行符单独返回、写回时还原——用户的
    Windows 手写配置是 CRLF，直接按文本模式改写可能整篇变 LF（TOML 换行不一致
    是已知故障源）。同时剥离编辑器写入的 UTF-8 BOM（tomllib 不接受 BOM）。

    @author aceFelix
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8")
    eol = "\r\n" if "\r\n" in text else "\n"
    if eol != "\n":
        text = text.replace("\r\n", "\n")
    return text, eol


def write_toml_text(path: Path, text: str, eol: str = "\n") -> None:
    """写回 TOML 文本（按 eol 还原换行），不写 BOM。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\n", eol) if eol != "\n" else text
    path.write_bytes(data.encode("utf-8"))


# ---------------------------------------------------------------------------
# 外科式写入助手（保留注释与其他内容）
# ---------------------------------------------------------------------------


def _top_end(content: str) -> int:
    """返回顶层区结束下标（首个 [section] 行首，无 section 则为全文长度）。"""
    first = re.search(r"^\[", content, re.MULTILINE)
    return first.start() if first else len(content)


def upsert_top_scalar(content: str, key: str, literal: str) -> str:
    """写入顶层标量键（替换已有值，或插入到顶层区），其余内容原样保留。

    TOML 顶层键必须位于任何 [section] 之前，因此插入窗固定在首个节头前：
    优先跟随 model = 行（与 key 自身的行），否则追加到顶层区末尾。

    @author aceFelix
    """
    end = _top_end(content)
    top, rest = content[:end], content[end:]
    line = f"{key} = {literal}"
    pat = re.compile(rf"^{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    if pat.search(top):
        return pat.sub(lambda _m: line, top, count=1) + rest
    anchor = re.search(r"^model[ \t]*=.*$", top, re.MULTILINE) if key != "model" else None
    if anchor:
        return top[: anchor.end()] + "\n" + line + top[anchor.end() :] + rest
    return top.rstrip("\n") + ("\n" if top.strip() else "") + line + "\n" + rest


def _normalize_section(name: str) -> str:
    """规范化 section 名：去掉方括号、引号与空白，便于同名匹配。

    入参可能是节头原文（``[llm.custom_models."a"]``）或正则捕获到的节名
    （``llm.custom_models."a"``），两种形态都要能匹配上：``[llm.custom_models.a]``
    与 ``[llm.custom_models."a"]`` 在 TOML 中等价，不认无引号写法会重复写段。
    """
    cleaned = name.replace('"', "").replace("'", "").strip()
    return cleaned.strip("[]").strip()


def _is_model_section(name: str) -> bool:
    """判断 section 是否属于模型域（llm / llm.models / llm.custom_models.*）。"""
    n = _normalize_section(name)
    return n == MODEL_SECTION_PREFIX or n.startswith(MODEL_SECTION_PREFIX + ".")


def _find_section(content: str, name: str) -> tuple[int, int] | None:
    """定位同名 section 的行范围 ``(header 行首, 下一节头前)``，未找到返回 None。"""
    target = _normalize_section(name)
    for m in re.finditer(r"^\[(.+?)\][ \t]*(?:#.*)?$", content, re.MULTILINE):
        if _normalize_section(m.group(1)) != target:
            continue
        tail = content[m.end() :]
        nxt = re.search(r"^\[", tail, re.MULTILINE)
        end = m.end() + (nxt.start() if nxt else len(tail))
        return m.start(), end
    return None


def upsert_table_block(content: str, header: str, body_lines: list[str]) -> str:
    """替换或追加一个 ``[section]`` 段（含节头行），其余内容原样保留。

    已存在 → 整段替换（段 = 节头到下一个节头前）；不存在 → 追加到文件末尾。

    @author aceFelix
    """
    block = "\n".join([header, *body_lines])
    found = _find_section(content, header)
    if found is not None:
        start, end = found
        head = content[:start].rstrip("\n")
        tail = content[end:].lstrip("\n")
        parts = [p for p in (head, block, tail) if p]
        return "\n\n".join(parts) + "\n"
    return content.rstrip("\n") + "\n\n" + block + "\n"


# ---------------------------------------------------------------------------
# 模型域写入封装（/models、jarvis init、切换模型）
# ---------------------------------------------------------------------------


def _read_or_seed(path: Path) -> tuple[str, str]:
    """读目标文件；不存在或为空时返回带头部说明的种子内容与默认换行。"""
    if path.is_file():
        text, eol = read_toml_text(path)
        if text.strip():
            return text, eol
    return "\n".join(HEADER_LINES) + "\n", "\n"


def upsert_custom_model(name: str, config: dict[str, str]) -> bool:
    """写入自定义模型到用户级 models.toml 的 ``[llm.custom_models."<name>"]`` 节。

    已存在则整段替换，否则追加；注释与其他内容原样保留。api_key 非空时同步
    写入系统 keyring（S-01，TOML 明文作降级兜底）。返回 True 表示写入成功。

    @author aceFelix
    """
    path = user_models_path()
    try:
        text, eol = _read_or_seed(path)
        body = [f'name = "{name}"']
        if config.get("api_key"):
            body.append(f'api_key = "{config["api_key"]}"')
        if config.get("base_url"):
            body.append(f'base_url = "{config["base_url"]}"')
        body += [
            f'provider_type = "{config.get("provider_type", "openai")}"',
            f'model_type = "{config.get("model_type", "multimodal")}"',
            f'vendor = "{config.get("vendor", "dashscope")}"',
        ]
        text = upsert_table_block(text, f'[llm.custom_models."{name}"]', body)
        write_toml_text(path, text, eol)
    except OSError:
        return False

    api_key = config.get("api_key", "")
    if api_key:
        try:
            from agent.config.keyring_store import store_api_key

            store_api_key(config.get("vendor", config.get("provider_type", "openai")), api_key)
        except Exception:
            pass
    return True


def upsert_last_model(model_name: str) -> bool:
    """写入 ``last_model`` 到用户级 models.toml 顶层（下次启动恢复该模型）。

    @author aceFelix
    """
    path = user_models_path()
    try:
        text, eol = _read_or_seed(path)
        text = upsert_top_scalar(text, "last_model", f'"{model_name}"')
        write_toml_text(path, text, eol)
        return True
    except OSError:
        return False


def remove_custom_model(name: str) -> bool:
    """从用户级 models.toml 删除 ``[llm.custom_models."<name>"]`` 段（/models 删除）。

    段不存在或文件不存在返回 False；其余内容与注释原样保留，顺带清掉旧版
    追加的「# 自定义模型」孤立注释。

    @author aceFelix
    """
    path = user_models_path()
    if not path.is_file():
        return False
    try:
        text, eol = read_toml_text(path)
        found = _find_section(text, f'[llm.custom_models."{name}"]')
        if found is None:
            return False
        start, end = found
        head = text[:start].rstrip("\n")
        tail = text[end:].lstrip("\n")
        new_text = "\n\n".join(p for p in (head, tail) if p) + "\n"
        # 用行首正则判断是否还剩自定义模型段：文件头部说明里也出现了
        # [llm.custom_models."*"] 字样，substring 判断会永远为真。
        if not re.search(r"^\[llm\.custom_models\.", new_text, re.MULTILINE):
            new_text = new_text.replace("# 自定义模型（通过 /models 添加）\n", "")
        write_toml_text(path, new_text, eol)
        return True
    except OSError:
        return False


def save_init_model(
    *,
    vendor_key: str,
    model_name: str,
    api_key: str,
    base_url: str,
    api_format: str,
    model_type: str,
) -> Path:
    """``jarvis init`` 落盘：用户级 models.toml 写自定义模型 + last_model + 顶层兜底。

    与 /models 添加自定义模型同口径：写 [llm.custom_models."<model>"] 让模型可被
    选中，写 last_model 让下次启动默认用它，并兜底 provider / api_format 顶层键
    （仅缺失时补写，不覆盖用户已有选择）。api_key 非空时同步系统 keyring。
    返回写入的文件路径（供 CLI 提示展示）。

    @author aceFelix
    """
    path = user_models_path()
    text, eol = _read_or_seed(path)
    body = [f'name = "{model_name}"']
    if base_url:
        body.append(f'base_url = "{base_url}"')
    if api_key:
        body.append(f'api_key = "{api_key}"')
    body += [
        f'provider_type = "{api_format}"',
        f'model_type = "{model_type}"',
        f'vendor = "{vendor_key}"',
    ]
    text = upsert_table_block(text, f'[llm.custom_models."{model_name}"]', body)
    text = upsert_top_scalar(text, "last_model", f'"{model_name}"')
    text = _ensure_top_scalar(text, "provider", f'"{vendor_key}"')
    text = _ensure_top_scalar(text, "api_format", f'"{api_format}"')
    write_toml_text(path, text, eol)

    if api_key:
        try:
            from agent.config.keyring_store import store_api_key

            store_api_key(vendor_key, api_key)
        except Exception:
            pass
    return path


def _ensure_top_scalar(content: str, key: str, literal: str) -> str:
    """仅当顶层键缺失时补写（已有则原样保留，不覆盖用户选择）。"""
    top = content[: _top_end(content)]
    if re.search(rf"^{re.escape(key)}[ \t]*=", top, re.MULTILINE):
        return content
    return upsert_top_scalar(content, key, literal)


# ---------------------------------------------------------------------------
# 拆分迁移：settings.toml 的模型域内容 → models.toml
# ---------------------------------------------------------------------------


def has_model_content(content: str) -> bool:
    """轻量判断文本顶层/节头是否含模型域内容（用于避免启动期无谓改写）。

    @author aceFelix
    """
    in_other_section = False
    for line in content.split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _SECTION_RE.match(s)
        if m:
            in_other_section = not _is_model_section(m.group(1))
            if not in_other_section:
                return True
            continue
        if in_other_section:
            continue
        m = _KV_RE.match(s)
        if m and m.group(1) in MODEL_TOP_KEYS:
            return True
    return False


def _trim_blank(lines: list[str]) -> list[str]:
    """去掉块首尾的空行（块内注释与内容保留）。"""
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _detach_next_leading(body: list[str]) -> tuple[list[str], list[str]]:
    """摘出段体末尾「空行 + 注释」组，交还给下一个 section。

    section 段的物理范围是「本节头 → 下一个节头前」，其中紧贴下一个节头的注释块
    语义上属于下一个 section（人的写法：空行 + 说明 + 下一节头）。若不摘出，会把
    下一段的说明注释一起搬进 models.toml，源文件反而丢注释。

    注释块紧贴本节内容（无空行分隔）时不摘，视为本节内容的尾注释。

    Returns:
        ``(本节保留的段体, 交给下一块的尾部行)``（含分隔空行）。

    @author aceFelix
    """
    k = len(body)
    while k > 0 and not body[k - 1].strip():
        k -= 1
    start = k
    while start > 0 and body[start - 1].strip().startswith("#"):
        start -= 1
    # 无注释块、整段体都是注释、或注释块紧贴内容时都不摘（保持原样）
    if start == k or start == 0 or body[start - 1].strip():
        return body, []
    # 连同分隔空行一起交还：空行是「与下一块的分隔」，跟着下一块走，
    # 否则空行留在本段被裁掉，源文件里两段会贴在一起。
    while start > 0 and not body[start - 1].strip():
        start -= 1
    return body[:start], body[start:]


def _render_blocks(blocks: list[tuple[bool, list[str]]]) -> list[str]:
    """把若干行块拼成输出行列表（保留源文件的分组空行）。

    块元素为 ``(源文件中该块前是否有空行, 行列表)``：源文件里紧挨着的顶层键
    保持紧凑（不强行插入空行），节块前始终空一行，用户手写的分组空行原样保留。

    @author aceFelix
    """
    out: list[str] = []
    for preceded_blank, block in blocks:
        trimmed = _trim_blank(block)
        if not trimmed:
            continue
        if out and (preceded_blank or _block_header(trimmed)):
            out.append("")
        out.extend(trimmed)
    return out


def _block_key(block: list[str]) -> str | None:
    """取块对应的顶层键名（非顶层键块返回 None）。"""
    for line in block:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _KV_RE.match(s)
        return m.group(1) if m else None
    return None


def _block_header(block: list[str]) -> str | None:
    """取块对应的 section 头（非 section 块返回 None）。"""
    for line in block:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _SECTION_RE.match(s)
        return m.group(1) if m else None
    return None


def split_model_config(settings_path: Path, models_path: Path | None = None) -> bool:
    """把 settings.toml 里的模型域内容拆分到同层 models.toml（幂等、保留注释）。

    逐行扫描按「块」归属：块 = 前导注释/空行 + 一条顶层键行或一个 [section] 段。
    模型域顶层键与 [llm*] 段移入 models.toml（顶层键统一排在段之前，顺带修正
    顺序陷阱）；其余内容原地保留。models.toml 已有的同名键/段不覆盖（避免覆盖
    用户新配置）。写回前把原 settings.toml 备份为 ``<name>.bak``（仅首次）。

    Returns:
        True 表示发生了拆分（写入了 models.toml 且清理了 settings.toml）；
        False 表示无模型域内容可拆（已迁移过或本就干净）。

    @author aceFelix
    """
    if not settings_path.is_file():
        return False
    target = models_path or models_path_for(settings_path)

    text, eol = read_toml_text(settings_path)
    lines = text.split("\n")
    kept: list[str] = []
    # 块 = (源文件中该块前是否有空行, 行列表)，用于还原原文件的分组空行
    top_blocks: list[tuple[bool, list[str]]] = []
    section_blocks: list[tuple[bool, list[str]]] = []
    pending: list[str] = []  # 待归属行（注释 / 空行）

    # 文件开头的注释块若与后续内容之间有空行分隔，则是「文件头」（描述整份
    # 配置），留在原文件不搬走，否则 models.toml 里会带上 settings.toml 的文件头；
    # 紧贴第一个块的注释块仍属于该块（如「# 可选模型说明」+ [llm.models]）。
    if lines and lines[0].strip().startswith("#"):
        j = 0
        while j < len(lines) and lines[j].strip().startswith("#"):
            j += 1
        if j < len(lines) and not lines[j].strip():
            kept.extend(lines[: j + 1])
            lines = lines[j + 1 :]

    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if not s or s.startswith("#"):
            pending.append(lines[i])
            i += 1
            continue
        preceded_blank = bool(pending) and not pending[0].strip()
        block = [*pending, lines[i]]
        pending = []
        i += 1
        m_sec = _SECTION_RE.match(s)
        if m_sec:
            # 段 = 节头到下一个节头前的全部行（含段内注释与空行），
            # 但末尾属于下一段的「空行 + 注释」组要交还给下一块。
            body: list[str] = []
            while i < len(lines) and not _SECTION_RE.match(lines[i].strip()):
                body.append(lines[i])
                i += 1
            body, carry = _detach_next_leading(body)
            pending = carry
            block.extend(body)
            if _is_model_section(m_sec.group(1)):
                section_blocks.append((preceded_blank, block))
            else:
                kept.extend(block)
            continue
        m_kv = _KV_RE.match(s)
        if m_kv and m_kv.group(1) in MODEL_TOP_KEYS:
            top_blocks.append((preceded_blank, block))
        else:
            kept.extend(block)

    if not top_blocks and not section_blocks:
        return False

    # 目标已有同名键/段则跳过（不覆盖用户新写入的配置）
    existing = ""
    if target.is_file():
        existing, _ = read_toml_text(target)
    existing_top = existing[: _top_end(existing)]

    added_top = [
        (flag, b)
        for flag, b in top_blocks
        if not re.search(
            rf"^{re.escape(_block_key(b) or '')}[ \t]*=", existing_top, re.MULTILINE
        )
    ]
    added_sections = [
        (flag, b)
        for flag, b in section_blocks
        if _find_section(existing, _block_header(b) or "") is None
    ]

    new_lines = _render_blocks(added_top)
    section_lines = _render_blocks(added_sections)
    if new_lines and section_lines:
        new_lines.append("")
    new_lines.extend(section_lines)

    if new_lines:
        target_eol = eol
        if target.is_file():
            _, target_eol = read_toml_text(target)
        if existing.strip():
            merged = existing.rstrip("\n") + "\n\n" + "\n".join(new_lines) + "\n"
        else:
            merged = "\n".join([*HEADER_LINES, "", *new_lines]) + "\n"
        write_toml_text(target, merged, target_eol)

    # 清理源文件：删除已被搬走的块，压缩多余空行
    cleaned = "\n".join([*kept, *pending]).rstrip("\n") + "\n"
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if not cleaned.strip():
        cleaned = "# jarvis 配置文件（模型配置已拆分到 models.toml）\n"
    backup = settings_path.with_name(settings_path.name + ".bak")
    if not backup.exists():
        shutil.copy2(settings_path, backup)
    write_toml_text(settings_path, cleaned, eol)
    return True


def auto_split_user_config(settings_path: Path) -> bool:
    """启动期自动拆分（由 settings.load_settings 调用），失败静默不影响启动。

    仅在 settings.toml 确实含模型域内容时才动文件；迁移是整理性操作，异常一律
    吞掉（读写失败、权限不足等），并记 diag 日志留痕。

    @author aceFelix
    """
    try:
        if not settings_path.is_file():
            return False
        text, _ = read_toml_text(settings_path)
        if not has_model_content(text):
            return False
        changed = split_model_config(settings_path)
        if changed:
            from agent.core.diag import diag_log

            diag_log(
                "config",
                f"模型配置已拆分到 {models_path_for(settings_path)}（原文件备份为 *.bak）",
            )
        return changed
    except Exception:
        return False
