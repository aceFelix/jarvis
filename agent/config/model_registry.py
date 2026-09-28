"""模型注册与 TOML 持久化。

模型域配置（自定义模型、最近使用的模型）已拆分到 ~/.jarvis/models.toml，
写入实现统一在 agent/config/models_config.py，本模块保留对外函数作为薄封装，
并继续管理其余运行时持久化：
- save_custom_model / save_last_model: 委托 models_config 写 models.toml
- save_custom_voice: 保存自定义 TTS 音色（settings.toml [tts.custom_voices]）
- remove_custom_voice: 删除自定义 TTS 音色段（/tts-voice 与桌面壳共用）
- save_tts_voice: 持久化当前 TTS 音色选择（settings.toml [tts]）
- save_tts_model: 持久化当前 TTS 模型（settings.toml [tts]，/tts-voice 联动切换用）
- save_proactive_tts_enabled: 持久化主动播报 TTS 开关（[daemon] 节）

从 settings.py 拆分出来，独立维护模型与语音持久化逻辑。

@author aceFelix
"""

from __future__ import annotations

import re
from pathlib import Path

from agent.config.models_config import upsert_custom_model, upsert_last_model


def save_custom_model(name: str, config: dict[str, str]) -> bool:
    """保存自定义模型到 ~/.jarvis/models.toml 的 [llm.custom_models] 节。

    2026-09 模型域拆分后实际写入实现移入 agent/config/models_config.py
    （保留注释的外科式 upsert，api_key 同时存系统 keyring）。
    如果模型已存在则更新，否则追加。支持 name/base_url/api_key/provider_type/model_type。
    返回 True 表示保存成功。

    @author aceFelix
    """
    return upsert_custom_model(name, config)


def save_last_model(model_name: str) -> bool:
    """保存最近使用的模型到 ~/.jarvis/models.toml 顶层 last_model 字段。

    last_model 和 model/provider/base_url 一样是顶层字段（不在 [llm] 节内），
    这样 _apply_toml 才能正确读取。下次启动时若未指定 --model，会自动恢复此模型。
    返回 True 表示保存成功。

    @author aceFelix
    """
    return upsert_last_model(model_name)


def save_custom_voice(name: str, config: dict[str, str]) -> bool:
    """保存自定义 TTS 音色到 ~/.jarvis/settings.toml 的 [tts.custom_voices] 节。

    如果音色已存在则更新，否则追加。支持 name/voice_id/description/vendor/model。
    返回 True 表示保存成功。

    @author aceFelix
    """
    toml_path = Path.home() / ".jarvis" / "settings.toml"
    if not toml_path.exists():
        return False

    content = toml_path.read_text(encoding="utf-8")

    entry_lines = [f'[tts.custom_voices."{name}"]', f'name = "{name}"']
    voice_id = config.get("voice_id", "")
    if voice_id:
        entry_lines.append(f'voice_id = "{voice_id}"')
    description = config.get("description", "")
    if description:
        entry_lines.append(f'description = "{description}"')
    vendor = config.get("vendor", "dashscope")
    entry_lines.append(f'vendor = "{vendor}"')
    # 适配模型（/tts-voice 音色-模型硬约束）：空串表示不限，仍显式写入便于重编
    entry_lines.append(f'model = "{config.get("model", "")}"')
    entry = "\n".join(entry_lines)

    marker = f'[tts.custom_voices."{name}"]'
    if marker in content:
        # 已存在 → 替换旧段
        start = content.index(marker)
        rest = content[start + len(marker):]
        m = re.search(r'\n\[', rest)
        if m:
            end = start + len(marker) + m.start()
            while end < len(content) and content[end] == '\n':
                end += 1
            content = content[:start].rstrip() + "\n" + entry.strip() + "\n" + content[end:]
        else:
            content = content[:start].rstrip() + "\n" + entry.strip()
    else:
        # 不存在 → 追加到 [tts] 表末尾（或文件末尾）
        tts_marker = '[tts]'
        if tts_marker in content:
            # 找到 [tts] 节起点，追加在其内部末尾
            tts_start = content.index(tts_marker)
            rest = content[tts_start + len(tts_marker):]
            m = re.search(r'\n\[', rest)
            if m:
                insert_at = tts_start + len(tts_marker) + m.start()
                content = content[:insert_at].rstrip() + "\n" + entry.strip() + "\n" + content[insert_at:]
            else:
                content = content.rstrip() + "\n" + entry.strip() + "\n"
        else:
            content = content.rstrip() + "\n\n[tts]\n" + entry.strip() + "\n"

    toml_path.write_text(content, encoding="utf-8")
    return True


def remove_custom_voice(name: str) -> bool:
    """从 ~/.jarvis/settings.toml 删除 [tts.custom_voices."name"] 段。

    终端 /tts-voice（Space 删除）与桌面壳 voices.delete 共用；段不存在时
    返回 False（调用方先清内存再调或自行决定顺序）。删除方式为外科式段
    移除：从段头到下一个段头（或文件尾）整段剪掉，其余内容不动。

    @author aceFelix
    """
    toml_path = Path.home() / ".jarvis" / "settings.toml"
    if not toml_path.exists():
        return False

    content = toml_path.read_text(encoding="utf-8")
    marker = f'[tts.custom_voices."{name}"]'
    if marker not in content:
        return False

    start = content.index(marker)
    rest = content[start + len(marker):]
    m = re.search(r'\n\[', rest)
    end = start + len(marker) + m.start() if m else len(content)
    while end < len(content) and content[end] == '\n':
        end += 1
    content = content[:start].rstrip() + "\n" + content[end:]
    toml_path.write_text(content, encoding="utf-8")
    return True


def save_tts_voice(voice_id: str) -> bool:
    """持久化当前 TTS 音色到 ~/.jarvis/settings.toml 的 [tts] 节 voice 字段。

    返回 True 表示保存成功。

    @author aceFelix
    """
    return _save_tts_field("voice", voice_id)


def save_tts_model(model: str) -> bool:
    """持久化当前 TTS 模型到 ~/.jarvis/settings.toml 的 [tts] 节 model 字段。

    /tts-voice 切换音色时若音色适配模型与当前 tts_model 不兼容，联动改模型
    后经本函数落盘，重启后保持。返回 True 表示保存成功。

    @author aceFelix
    """
    return _save_tts_field("model", model)


def _save_tts_field(key: str, value: str) -> bool:
    """外科式写入 settings.toml 的 [tts] 节单字段（voice/model 共用）。

    只替换/追加目标键行，保留节内其他字段与注释、以及 [tts.custom_voices]
    子表不受影响（子表在独立段，按段头 [tts] 行精确匹配段界）。

    @author aceFelix
    """
    toml_path = Path.home() / ".jarvis" / "settings.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)

    if not toml_path.exists():
        toml_path.write_text(f'[tts]\n{key} = "{value}"\n', encoding="utf-8")
        return True

    content = toml_path.read_text(encoding="utf-8")

    # 定位或创建 [tts] 节
    section_match = re.search(r'^\[tts\]\s*$', content, re.MULTILINE)
    if section_match:
        section_start = section_match.end()
        next_section = re.search(r'^\[', content[section_start + 1:], re.MULTILINE)
        section_end = section_start + 1 + (next_section.start() if next_section else len(content[section_start + 1:]))
        section = content[section_start + 1:section_start + 1 + section_end - (section_start + 1)]

        if re.search(rf'^{key}\s*=', section, re.MULTILINE):
            new_section = re.sub(
                rf'^{key}\s*=.*$',
                f'{key} = "{value}"',
                section,
                flags=re.MULTILINE,
            )
        else:
            new_section = section.rstrip() + f'\n{key} = "{value}"\n'

        content = content[:section_start + 1] + new_section + content[section_start + 1 + section_end - (section_start + 1):]
    else:
        content = content.rstrip() + f'\n\n[tts]\n{key} = "{value}"\n'

    toml_path.write_text(content, encoding="utf-8")
    return True


def save_proactive_tts_enabled(enabled: bool) -> bool:
    """持久化主动播报 TTS 开关到 ~/.jarvis/settings.toml 的 [daemon] 节 proactive_tts_enabled 字段。

    桌面壳设置面板经 settings.set 指令调用：运行时改 Settings 实例立即生效，
    本函数负责重启后仍生效的落盘部分。2026-09 起委托 desktop_settings.save_setting
    （通用外科式 writer，与 save_* 族同口径：保留注释与其他字段）。
    返回 True 表示保存成功；IO 失败返回 False（调用方据此回执报错，不改运行时）。

    @author aceFelix
    """
    from agent.config.desktop_settings import SPEC_BY_KEY, save_setting

    return save_setting(SPEC_BY_KEY["proactive_tts_enabled"], bool(enabled))
