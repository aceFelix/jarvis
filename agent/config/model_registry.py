"""模型注册与 TOML 持久化。

管理 ~/.jarvis/settings.toml 中的自定义模型配置和运行时状态：
- save_custom_model: 保存自定义模型配置
- save_last_model: 持久化最近使用的模型名
- save_realtime_talk_auto_start: 实时语音自动启动开关

从 settings.py 拆分出来，独立维护模型持久化逻辑。

@author aceFelix
"""

from __future__ import annotations

import re
from pathlib import Path


def save_custom_model(name: str, config: dict[str, str]) -> bool:
    """保存自定义模型到 ~/.jarvis/settings.toml 的 [llm.custom_models] 节。

    如果模型已存在则更新，否则追加。支持 name/base_url/api_key/provider_type/model_type。
    返回 True 表示保存成功。

    @author aceFelix
    """
    toml_path = Path.home() / ".jarvis" / "settings.toml"
    if not toml_path.exists():
        return False

    content = toml_path.read_text(encoding="utf-8")

    # 构建新子表条目
    entry = f'''
[llm.custom_models."{name}"]
name = "{name}"
base_url = "{config.get('base_url', '')}"
api_key = "{config.get('api_key', '')}"
provider_type = "{config.get('provider_type', 'openai')}"
model_type = "{config.get('model_type', 'multimodal')}"
vendor = "{config.get('vendor', 'dashscope')}"
'''

    marker = f'[llm.custom_models."{name}"]'
    if marker in content:
        # 已存在 → 替换旧段
        start = content.index(marker)
        rest = content[start + len(marker):]
        m = re.search(r'\n\[', rest)
        if m:
            end = start + len(marker) + m.start()
            # 跳过末尾的空行
            while end < len(content) and content[end] == '\n':
                end += 1
            content = content[:start].rstrip() + "\n" + entry.strip() + "\n" + content[end:]
        else:
            content = content[:start].rstrip() + "\n" + entry.strip()
    else:
        # 不存在 → 追加
        if "[llm.custom_models" not in content:
            # 首次添加，确保有节头注释
            content = content.rstrip() + "\n\n# 自定义模型（通过 /models 添加）\n"
        content = content.rstrip() + "\n" + entry.strip() + "\n"

    toml_path.write_text(content, encoding="utf-8")
    return True


def save_last_model(model_name: str) -> bool:
    """保存最近使用的模型到 ~/.jarvis/settings.toml 顶层 last_model 字段。

    last_model 和 model/provider/base_url 一样是顶层字段（不在 [llm] 节内），
    这样 _apply_toml 才能正确读取。
    下次启动时若未指定 --model，会自动恢复此模型。
    返回 True 表示保存成功。

    @author aceFelix
    """
    toml_path = Path.home() / ".jarvis" / "settings.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)

    if not toml_path.exists():
        toml_path.write_text(f'last_model = "{model_name}"\n', encoding="utf-8")
        return True

    content = toml_path.read_text(encoding="utf-8")

    # last_model 是顶层字段，必须在任何 [...] 节头之前
    # 策略：找到第一个 [...] 节头位置，在该位置之前操作顶层字段
    first_section = re.search(r'^\[', content, re.MULTILINE)
    top_end = first_section.start() if first_section else len(content)
    top_part = content[:top_end]
    rest_part = content[top_end:]

    # 在顶层部分查找/更新/插入 last_model
    if re.search(r'^last_model\s*=', top_part, re.MULTILINE):
        # 替换已有值
        new_top = re.sub(
            r'^last_model\s*=.*$',
            f'last_model = "{model_name}"',
            top_part,
            flags=re.MULTILINE,
        )
    else:
        # 插入：优先放在 model = 行后面，否则放在顶层末尾
        model_line = re.search(r'^model\s*=.*$', top_part, re.MULTILINE)
        if model_line:
            insert_at = model_line.end()
            new_top = top_part[:insert_at] + f'\nlast_model = "{model_name}"' + top_part[insert_at:]
        else:
            new_top = top_part.rstrip() + f'\nlast_model = "{model_name}"\n'

    content = new_top + rest_part
    toml_path.write_text(content, encoding="utf-8")
    return True


def save_realtime_talk_auto_start(enabled: bool) -> bool:
    """保存实时语音对话自动启动开关到 ~/.jarvis/settings.toml 的 [realtime_talk] 节。

    返回 True 表示保存成功。

    @author aceFelix
    """
    toml_path = Path.home() / ".jarvis" / "settings.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)

    value = "true" if enabled else "false"
    if not toml_path.exists():
        toml_path.write_text(f"[realtime_talk]\nauto_start = {value}\n", encoding="utf-8")
        return True

    content = toml_path.read_text(encoding="utf-8")

    # 定位或创建 [realtime_talk] 节
    section_match = re.search(r'^\[realtime_talk\]\s*$', content, re.MULTILINE)
    if section_match:
        section_start = section_match.end()
        next_section = re.search(r'^\[', content[section_start + 1:], re.MULTILINE)
        section_end = section_start + 1 + (next_section.start() if next_section else len(content[section_start + 1:]))
        section = content[section_start + 1:section_start + 1 + section_end - (section_start + 1)]

        if re.search(r'^auto_start\s*=', section, re.MULTILINE):
            new_section = re.sub(
                r'^auto_start\s*=.*$',
                f"auto_start = {value}",
                section,
                flags=re.MULTILINE,
            )
        else:
            new_section = section.rstrip() + f"\nauto_start = {value}\n"

        content = content[:section_start + 1] + new_section + content[section_start + 1 + section_end - (section_start + 1):]
    else:
        content = content.rstrip() + f"\n\n[realtime_talk]\nauto_start = {value}\n"

    toml_path.write_text(content, encoding="utf-8")
    return True
