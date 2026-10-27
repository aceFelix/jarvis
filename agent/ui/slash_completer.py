"""终端 REPL 的斜杠命令注册表与自动补全。

从 `agent/ui/cli.py` 拆出（RichCLI 已远超单文件 800 行上限）。本模块只管
"输入助手指引"这一件事：命令清单、工具名的语音描述，以及 prompt_toolkit
补全器的候选生成；渲染与交互循环仍留在 cli.py 的 RichCLI。

拆分后 cli.py 以别名方式重新导入（``SlashCompleter as _SlashCompleter`` 等），
因此既有调用点、测试与文档里的 `from agent.ui.cli import SLASH_COMMANDS`
路径全部保持不变。

补全器在无 prompt_toolkit 的环境下也能声明：用 Completer / Completion /
CompleteEvent 的哑元基类兜底，避免 ImportError 连带拖垮整个 CLI 模块。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

try:
    from prompt_toolkit.completion import CompleteEvent, Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import FormattedText

    _HAS_PT = True
except ImportError:  # pragma: no cover
    _HAS_PT = False

    # 无 prompt_toolkit 时提供哑元类，让 SlashCompleter 能声明
    class Completer:  # type: ignore[no-redef]
        pass

    class Completion:  # type: ignore[no-redef]
        pass

    class CompleteEvent:  # type: ignore[no-redef]
        pass

    class Document:  # type: ignore[no-redef]
        pass

    class FormattedText:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass


# ---- 斜杠命令注册表 ----

#: 全部可用命令 (name, description) 列表。main.py / daemon.py 的 REPL 循环按此列表补全。
SLASH_COMMANDS = [
    ("/exit",       "退出贾维斯"),
    ("/quit",       "退出贾维斯"),
    ("/q",          "退出贾维斯（/exit 简写）"),
    ("/help",       "查看所有命令帮助"),
    ("/h",          "查看帮助（/help 简写）"),
    ("/mode <m>",   "切换权限模式 (default/plan/accept_edits/yolo)"),
    ("/away",        "进入无人值守（确认类操作拒绝并入待批清单）"),
    ("/back",        "结束 away（播报待批清单简报并清空）"),
    ("/pending",     "查看无人值守待批清单"),
    ("/lease",       "授权租约（/lease 列表 | revoke <id> | <工具名> [路径] [分钟] 签发）"),
    ("/model <前缀>","前缀匹配切换模型（支持模糊输入）"),
    ("/models",     "交互式选择或添加模型"),
    ("/reset",      "清空对话历史，重新开始"),
    ("/clear",      "清空对话历史（/reset 别名）"),
    ("/compact",    "手动压缩上下文（摘要旧消息节省 token）"),
    ("/context",    "查看上下文窗口使用情况（按角色统计）"),
    ("/cost",       "显示本会话 token 用量与成本估算（含缓存命中率）"),
    ("/c",          "显示成本统计（/cost 简写）"),
    ("/doctor",     "系统诊断（环境/配置/日志/自愈统计）"),
    ("/update",     "检查并升级 jarvis 到最新版本（重启生效）"),
    ("/config show","查看当前生效的完整配置（LLM/MCP/自定义模型等）"),
    ("/config list","列出可以改的配置项（协议键/当前值/要不要确认）"),
    ("/config set <键> <值>","改一项配置（需确认的项先给预览，再加 --yes 执行）"),
    ("/init",       "首启三问（厂商/Key/模型，模型实时拉取可选）；/init full 完整引导"),
    ("/rewind [n]", "回退最近 n 条消息（默认 1，可连带回滚工作区文件）"),
    ("/undo",       "撤销上一步副作用（文件类按本轮检查点回滚）"),
    ("/undo list",  "列出最近副作用与各自可否撤销"),
    ("/undo cancel", "撤回还在延时窗口里、尚未执行的不可逆动作"),
    ("/prefs",      "查看记住的偏好（带条件的「以后…」）"),
    ("/prefs forget <id>", "忘掉一条偏好"),
    ("/diff [path]","显示 git diff（工作区改动）"),
    ("/save [name]","保存当前会话"),
    ("/load <前缀>","前缀匹配加载会话（支持模糊输入）"),
    ("/loads",      "列出并交互选择已保存会话"),
    ("/sessions",   "列出已保存会话（/loads 别名）"),
    ("/memory",     "画像记忆（add/del/clear/file 管理长期记忆）"),
    ("/skills",     "列出已加载的技能包"),
    ("/agents",     "查看多 Agent 团队状态与成员"),
    ("/tasks",      "查看共享任务与跨会话目标两节"),
    ("/tasks add",  "建立跨会话目标（--wait time:+2h / file:<路径> / event:<源>/<kind>）"),
    ("/tasks show", "展开一个目标的步骤、等待条件与副作用 id"),
    ("/tasks approve", "批准卡在「需要授权」的步骤（签限定租约并立刻重试）"),
    ("/tasks advance", "跳过等待条件立刻推进（测试用）"),
    ("/tasks cancel", "取消一个目标（台账保留，可复盘）"),
    ("/plan",       "切换规划模式（进入/退出只读规划）"),
    ("/think",      "开关或调节深度思考强度（/think on|off|low|medium|high）"),
    ("/mcp",        "查看 MCP server 连接状态与工具"),
    ("/tools",      "列出可用工具列表"),
    ("/image <path>", "添加本地图片到待发送列表（下条消息附带）"),
    ("/img <path>",   "添加本地图片（/image 别名）"),
    ("/paste",        "添加剪贴板图片到待发送列表（同 Ctrl+V，下条消息附带）"),
    ("/p",            "添加剪贴板图片（/paste 别名）"),
    ("/say <text>", "用 TTS 语音朗读一段文字"),
    ("/listen",     "录音并识别成文字（麦克风→STT→文本）"),
    ("/mic",        "录音并识别文字（/listen 别名）"),
    ("/voice",      "进入语音对话模式（连续听→想→说循环）"),
    ("/talk",       "进入实时双工语音对话（全双工，说话即可打断）"),
    ("/tts-voice <前缀>", "选择 / 切换 / 添加 TTS 音色（DashScope）"),
    ("/connect-phone", "手机扫码连接 JARVIS（共享当前会话）"),
    ("/connect-wechat", "微信扫码连接 JARVIS（通过 ClawBot 在微信中对话）"),
    ("/disconnect-wechat", "断开微信 ClawBot 连接"),
    ("/plugin",                 "列出已安装插件（Plugin 系统）"),
    ("/plugin install <名称>",  "安装 Plugin 系统的插件"),
    ("/plugin uninstall <名称>","卸载 Plugin 系统的插件"),
    ("/plugin search <关键词>", "搜索 Plugin 系统市场"),
    ("/plugin info <名称>",     "查看 Plugin 插件详情"),
    ("/plugin update",          "检查 Plugin 插件更新"),
    ("/plugin enable <名称>",   "启用被禁用的插件（通用）"),
    ("/plugin disable <名称>",  "禁用插件，不卸载（通用）"),
    ("/plugin create <名称>",   "创建新插件脚手架（--type harness|plugin）"),
    ("/plugin validate <路径>", "校验 plugin.json / SKILL.md（通用）"),
    ("/cli_anything",           "列出已安装 CLI-Anything harness"),
    ("/cli_anything list",      "列出已安装 CLI-Anything harness"),
    ("/cli_anything market",    "列出市场可用 harness"),
    ("/cli_anything install <id>",  "安装指定 harness"),
    ("/cli_anything uninstall <id>","卸载指定 harness"),
    ("/harnesses",     "列出已安装 CLI-Anything harness（别名）"),
    ("/server [目录] [--port N]", "启动开发服务器（Vite/Next 等）"),
    ("/server stop [--pid N|--port N]", "关闭由 Jarvis 启动的开发服务器"),
]

# 工具名 → 语音播报描述（方言/中文，适合 TTS 朗读）
TOOL_VOICE_DESC: dict[str, str] = {
    "Bash": "正在执行命令",
    "Glob": "正在搜索文件",
    "Grep": "正在搜索代码",
    "Read": "正在读取文件",
    "Edit": "正在编辑文件",
    "Write": "正在写入文件",
    "ScreenShot": "正在截图",
    "CameraShot": "正在拍照",
    "WebFetch": "正在访问网页",
    "WebSearch": "正在搜索网络",
    "TodoWrite": "正在整理计划",
    "Subagent": "正在派生子代理协助",
    "ListCameras": "正在检测摄像头",
    "VisionWatch": "正在开启视觉监控",
}


def tool_voice_desc(tool_name: str) -> str:
    """工具名的中文语音描述。未知工具返回默认短语。"""
    return TOOL_VOICE_DESC.get(tool_name, "正在处理")


class SlashCompleter(Completer):
    """斜杠命令自动补全器。

    输入 '/' 时弹出全部命令及描述。
    输入 '/v' 后只匹配 voice 等前缀命中的项。

    子命令补全:
    - /mode <prefix> → 匹配权限模式 (default/plan/accept_edits/yolo)
    - /think <prefix> → 匹配思考强度档位 (on/off/low/medium/high)
    - /model <prefix> → 匹配模型名（内置 + 自定义）
    - /load <prefix> → 匹配已保存会话名
    - /save <prefix> → 匹配已保存会话名

    显示风格:
      /exit          退出贾维斯  ← 命令名亮青蓝 + 描述浅灰
    选中项：深蓝灰底 + 白/浅蓝字（高对比，一眼可见）
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> list[Completion]:
        text_before_cursor = document.text_before_cursor.lstrip()
        if not text_before_cursor.startswith("/"):
            return []

        text_lower = text_before_cursor.lower()

        # ---- /mode <prefix> 子命令补全 ----
        if text_lower.startswith("/mode ") or text_before_cursor.startswith("/mode "):
            return self._complete_modes(text_before_cursor)

        # ---- /think <prefix> 参数补全（思考强度档位）----
        if text_lower.startswith("/think ") or text_before_cursor.startswith("/think "):
            return self._complete_thinking(text_before_cursor)

        # ---- /model <prefix> 子命令补全 ----
        if text_lower.startswith("/model ") or text_before_cursor.startswith("/model "):
            return self._complete_models(text_before_cursor)

        # ---- /tts-voice <prefix> 子命令补全 ----
        if text_lower.startswith("/tts-voice ") or text_before_cursor.startswith("/tts-voice "):
            return self._complete_tts_voices(text_before_cursor)

        # ---- /load <prefix> 子命令补全 ----
        if text_lower.startswith("/load ") or text_before_cursor.startswith("/load "):
            return self._complete_sessions(text_before_cursor, prefix_cmd="/load ")

        # ---- /save <prefix> 子命令补全 ----
        if text_lower.startswith("/save ") or text_before_cursor.startswith("/save "):
            return self._complete_sessions(text_before_cursor, prefix_cmd="/save ")

        # ---- 默认：斜杠命令补全 + 技能补全 ----
        word = text_lower
        cmd_matches = [
            (c, d) for c, d in SLASH_COMMANDS if c.lower().startswith(word)
        ]
        results = _build_completion_list(
            cmd_matches, word, width=16, desc_color="#aaaaaa"
        )

        # 技能名补全（每个 skill 也作为 /<skill-name> 使用）
        skill_matches: list[tuple[str, str]] = []
        try:
            import os

            from agent.core.extensions.skills import load_skills
            workdir = os.getcwd()
            for skill in load_skills(workdir):
                skill_cmd = f"/{skill.name}"
                if skill_cmd.lower().startswith(word):
                    skill_matches.append((skill_cmd, skill.description or "技能包"))
        except Exception:
            pass

        results += _build_completion_list(
            skill_matches, word, width=16, desc_color="#aaaaaa"
        )
        return results

    @staticmethod
    def _complete_modes(text: str) -> list[Completion]:
        """补全 /mode <prefix> 的权限模式名。"""
        # 4 种权限模式及描述
        all_modes: list[tuple[str, str]] = [
            ("default",      "默认：写操作需确认，危险命令拒绝"),
            ("plan",         "规划：只读规划，拒绝所有写操作"),
            ("accept_edits", "接受编辑：文件编辑自动放行，其他需确认"),
            ("yolo",         "全自动：自动放行所有操作（危险命令除外）"),
        ]

        prefix = text[len("/mode "):].lower()
        matches = [(n, d) for n, d in all_modes if n.lower().startswith(prefix)]

        return _build_completion_list(matches, prefix, width=16)

    @staticmethod
    def _complete_thinking(text: str) -> list[Completion]:
        """补全 /think <prefix> 的思考强度档位（与终端模糊前缀匹配同口径）。"""
        # 5 档取值及描述（off/on + 三档强度，后端按厂商 THINKING_CONFIGS 翻译）
        all_efforts: list[tuple[str, str]] = [
            ("on",     "开启：用厂商默认强度"),
            ("off",    "关闭深度思考"),
            ("low",    "低强度（1/2/3 速记三档）"),
            ("medium", "中强度"),
            ("high",   "高强度"),
        ]

        prefix = text[len("/think "):].strip().lower()
        matches = [(n, d) for n, d in all_efforts if n.startswith(prefix)]

        return _build_completion_list(matches, prefix, width=16)

    @staticmethod
    def _complete_models(text: str) -> list[Completion]:
        """补全 /model <prefix> 的模型名。"""
        # 提取 /model 后的参数部分
        prefix = text[len("/model "):].lower()

        # 加载模型列表（懒加载，避免循环导入）
        all_models: list[tuple[str, str]] = []  # [(name, desc), ...]
        try:
            from agent.config.settings import load_settings
            settings = load_settings()
            for name, desc in settings.models.items():
                all_models.append((name, desc))
            for name, cfg in settings.custom_models.items():
                if isinstance(cfg, dict):
                    mtype = cfg.get("model_type", "multimodal")
                    tag = "纯文本" if mtype == "text" else "多模态"
                    all_models.append((name, f"[{tag}] {cfg.get('name', name)}"))
        except Exception:
            pass

        # 前缀过滤
        matches = [(n, d) for n, d in all_models if n.lower().startswith(prefix)]

        return _build_completion_list(matches, prefix, desc_color="#888888")

    @staticmethod
    def _complete_tts_voices(text: str) -> list[Completion]:
        """补全 /tts-voice <prefix> 的 TTS 音色名（内置 + 自定义）。"""
        prefix = text[len("/tts-voice "):].lower()

        all_voices: list[tuple[str, str]] = []  # [(name, desc), ...]
        try:
            from agent.config.settings import load_settings
            from agent.voice.tts_voices import all_tts_voices
            settings = load_settings()
            for name, cfg in all_tts_voices(settings).items():
                desc = cfg.get("description", "")
                all_voices.append((name, desc))
                # 自定义音色 voice_id 与 name 不同时也收录，便于按 ID 补全
                if cfg.get("voice_id") and cfg["voice_id"] != name:
                    all_voices.append((cfg["voice_id"], desc))
        except Exception:
            pass

        matches = [(n, d) for n, d in all_voices if n.lower().startswith(prefix)]

        return _build_completion_list(matches, prefix, desc_color="#888888")

    @staticmethod
    def _complete_sessions(text: str, prefix_cmd: str) -> list[Completion]:
        """补全 /load <prefix> 或 /save <prefix> 的会话名。"""
        prefix = text[len(prefix_cmd):].lower()

        all_sessions: list[tuple[str, str]] = []  # [(name, desc), ...]
        try:
            from agent.core.memory.store import list_sessions
            for s in list_sessions():
                desc = f"{s.message_count} 条消息 | {s.workdir or '(无)'}"
                all_sessions.append((s.name, desc))
        except Exception:
            pass

        matches = [(n, d) for n, d in all_sessions if n.lower().startswith(prefix)]

        return _build_completion_list(matches, prefix, desc_color="#888888")


def _build_completion_list(
    matches: list[tuple[str, str]],
    prefix: str,
    *,
    width: int = 24,
    desc_color: str = "#888888",
) -> list[Completion]:
    """把 (名称, 描述) 候选转成 prompt_toolkit Completion 列表。

    五个子命令补全的渲染逻辑完全一致，此前各自内联复制了一遍，现统一到此
    函数（code-structure 规则：相同逻辑出现第二处即抽取公共实现）。

    Args:
        matches: 已通过前缀过滤的候选项。
        prefix: 用户已输入的参数字串，决定补全回退位置。
        width: 名称列宽（命令名用 16，模型/音色/会话用 24）。
        desc_color: 描述文字颜色。

    Returns: 可直接交给 PromptSession 的 Completion 列表。
    """
    results: list[Completion] = []
    start_pos = -len(prefix) if prefix else 0
    for name, desc in matches:
        results.append(
            Completion(
                text=name,
                start_position=start_pos,
                display=FormattedText([
                    ("#5bc8ff", f"{name:<{width}}"),
                    (desc_color, f"  {desc}"),
                ]),
            )
        )
    return results


__all__ = [
    "SLASH_COMMANDS",
    "TOOL_VOICE_DESC",
    "SlashCompleter",
    "tool_voice_desc",
]
