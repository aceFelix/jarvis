"""首启三问与使用台账（阶段 12 P4）。

P1–P3 之后，"改配置"已经不是问题了——真正的问题是**第一次**：新机器上打开 jarvis
什么都没配，用户得先读文档、跑命令、手改 TOML，才能说上第一句话。所以 P4 收两件事：

1. **首启只问三件**（厂商 / API Key / 模型），其余全默认。判据是"全新机器到能用
   ≤ 3 个问题"。模型清单**实时向厂商拉取**（`agent/llm/model_catalog.py`），不内置
   一张会过期的"最新型号表"，也不替用户决定他该用哪个模型。
   工作目录不单独问：默认当前目录（有安全默认值），问券预算留给没有默认值的模型。

   **语音（DashScope）API Key 是“条件第四问”**：说话/听话固定走阿里云百炼，与 LLM
   厂商无关，所以选 DeepSeek/OpenAI/Claude 的用户必须单独给一个百炼 key——否则装完
   机器“能打字但不会说话”，而界面上没有任何一处说缺什么。但它**不总问**：

   - LLM 本身选的就是 DashScope → 它的 key 就是百炼 key，直接复用、不问也不写盘；
   - 配置里已有 `dashscope_api_key`，或环境变量已有 `DASHSCOPE_API_KEY` → 不问；

   于是常见路径（选阿里云百炼）仍然是三问，只有“LLM 用别家且语音还没配”的才多
   一问，而且可以回车跳过（跳过 = 只有文字）。解析优先级见
   `agent/config/voice_key.py`。
2. **用几天后主动提议调整**（`maybe_suggest_adjustment`）：把 P3 提炼出的候选拿出来
   问一次，用户点头才生效。只提一次，且不占打扰预算（终端里一行，不是系统通知）。

三条设计底线：

- **非交互不问**：`stdin` 不是终端（管道、CI、脚本）时直接跳过——否则一道交互提示会
  把自动化卡死，这比"没引导"严重得多。
- **用户说不就别再问**：取消一次就写 `declined`，以后不再自动弹；想配就自己跑 `/init`。
- **不在引导里编造能力**：认不出厂商就让用户选，不靠 key 前缀猜；多模态默认关闭
  （要用视觉就 `/models` 改），宁可少猜一步也不要把"支持图片"错误地写在配置里。
- **不替用户选型号**：模型列表来自厂商实时返回；内置表只作断网兑底，且会明说没拉到。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.config.settings import Settings

#: 首启只问这三件（顺序即提问顺序）。
#: 语音（DashScope）API Key 是**条件第四问**（见模块说明）：LLM 已在 DashScope 或
#: 语音已配好的情形一律不问，所以这里仍只列三件，不把这承诺改成四。
FIRST_RUN_QUESTIONS: tuple[str, str, str] = ("厂商", "API Key", "模型")

#: 「用几天后主动提议」的门槛：至少启动过 5 次、且首启满 3 天
SUGGEST_AFTER_STARTS = 5
SUGGEST_AFTER_DAYS = 3

_REL_PATH = (".jarvis", "usage.json")


def _usage_path(path: Path | str | None = None) -> Path:
    """台账路径（模块级不拼全路径：`Path.home()` 必须在调用时求值，测试靠 patch）。"""
    if path is not None:
        return Path(path)
    return Path.home().joinpath(*_REL_PATH)


@dataclass
class Usage:
    """一份很小的使用台账：只回答"是不是第一次""用了多久""提议过没有"。"""

    first_seen: float = field(default_factory=time.time)
    starts: int = 0
    onboarded: bool = False      # 是否已完成首启三问
    declined: bool = False       # 用户取消过首启（以后不再自动问）
    suggested: bool = False      # 是否已主动提议过偏好调整（只提一次）

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_seen": self.first_seen,
            "starts": self.starts,
            "onboarded": self.onboarded,
            "declined": self.declined,
            "suggested": self.suggested,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "Usage":
        """容错还原：坏数据当全新台账（这份文件丢了也不该让人重装一遍）。"""
        if not isinstance(data, dict):
            return cls()
        def _num(key: str, default: float) -> float:
            try:
                return float(data.get(key, default))
            except (TypeError, ValueError):
                return default
        return cls(
            first_seen=_num("first_seen", time.time()),
            starts=int(_num("starts", 0)),
            onboarded=bool(data.get("onboarded")),
            declined=bool(data.get("declined")),
            suggested=bool(data.get("suggested")),
        )

    def days(self, now: float | None = None) -> float:
        """首启至今的天数。"""
        return ((now or time.time()) - self.first_seen) / 86400.0


def load_usage(path: Path | str | None = None) -> Usage:
    """读台账；文件不存在/损坏都返回全新台账（不抛）。"""
    target = _usage_path(path)
    try:
        return Usage.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        return Usage()


def save_usage(usage: Usage, path: Path | str | None = None) -> bool:
    """写台账（失败只返回 False：记账不该拦住启动）。"""
    target = _usage_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(usage.to_dict(), ensure_ascii=False), encoding="utf-8"
        )
        return True
    except OSError:
        return False


def note_start(path: Path | str | None = None) -> Usage:
    """启动计数 +1 并落盘（用于"用了几天"的门槛）。"""
    usage = load_usage(path)
    usage.starts += 1
    save_usage(usage, path)
    return usage


def mark_onboarded(path: Path | str | None = None) -> Usage:
    usage = load_usage(path)
    usage.onboarded = True
    save_usage(usage, path)
    return usage


def mark_declined(path: Path | str | None = None) -> Usage:
    """用户取消过首启：以后不再自动问（想配就自己跑 /init）。"""
    usage = load_usage(path)
    usage.declined = True
    save_usage(usage, path)
    return usage


def mark_suggested(path: Path | str | None = None) -> Usage:
    usage = load_usage(path)
    usage.suggested = True
    save_usage(usage, path)
    return usage


# ---------------------------------------------------------------------------
# 首启判定
# ---------------------------------------------------------------------------


def is_unconfigured(settings: "Settings | None") -> bool:
    """是不是"装了但还一句都说不上"的状态。

    判据故意宽松（任一就够）：
    - 没设过模型、也没 custom_models（`/models` 加的都会进这里）；
    - 且没有可用的 key（配置里或环境变量里）。

    宽松的好处是不打扰那些用环境变量跑起来的老用户；坏处是"配了一半"的人不会再被
    自动问——那种情况本来就该自己跑 `/init`。
    """
    if settings is None:
        return True
    if getattr(settings, "model", "") or getattr(settings, "custom_models", None):
        return False
    if getattr(settings, "api_key", ""):
        return False
    if any(os.environ.get(k) for k in (
        "JARVIS_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "ZAI_API_KEY",
        "ANTHROPIC_API_KEY", "KIMI_API_KEY", "MINIMAX_API_KEY", "MIMO_API_KEY",
        "OPENAI_API_KEY",
    )):
        return False
    return True


def can_prompt() -> bool:
    """现在能不能问人（非交互环境一律不问）。"""
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:  # noqa: BLE001 - 取不到就当非交互，宁可不问
        return False


def should_onboard(settings: "Settings | None", *, path: Path | str | None = None) -> bool:
    """该不该自动走首启三问。"""
    if not can_prompt():
        return False
    usage = load_usage(path)
    if usage.onboarded or usage.declined:
        return False
    return is_unconfigured(settings)


# ---------------------------------------------------------------------------
# 三问（其余全默认）
# ---------------------------------------------------------------------------


def _ask_voice_key(ui: Any, settings: "Settings | None", *, vendor_key: str,
                   llm_api_key: str) -> tuple[str, bool]:
    """条件第四问：语音（DashScope）API Key。能复用 / 已配就不问。

    为什么必须有这一问：说话与听话（TTS / STT）固定走阿里云百炼，**与 LLM 厂商
    无关**。旧引导只配了 LLM key，选 DeepSeek / OpenAI 的用户装完机器“能打字但
    不会说话”，而界面上没有任何一处告诉他缺的是哪个 key。

    为什么它不破坏「三问」承诺（三种情况一律不问）：

    1. LLM 本身就是 DashScope → 它的 key 就是百炼 key，`voice_key.resolve()` 的
       第三条会自动复用。这里**不额外写盘**：同一串 key 存两份，日后改一处忘了
       另一处，就是下一条“配了不生效”的钉子。
    2. 配置里已有 ``dashscope_api_key``（含上一轮引导写的）。
    3. 环境变量已有 ``DASHSCOPE_API_KEY``。

    Args:
        ui: 终端 UI。
        settings: 当前配置（用于判断“已经配过了”）。
        vendor_key: 本次首启刚选的 LLM 厂商 key（**不能读 settings.provider**：
            此刻新选择还没写盘，settings 里还是旧值）。
        llm_api_key: 本次首启刚填的 LLM key（用于判断能否复用）。

    Returns:
        ``(要写入 models.toml 的新 key, 语音是否已可用)``。
        新 key 为空串表示无需写入；“已可用”分别对应复用 / 已配置（True）
        与用户跳过（False）——调用方靠它决定结尾要不要提示“语音还没配”。

    @author aceFelix
    """
    from agent.config import voice_key as vk

    # ① 已配过就不问（配置优先，环境变量次之——与 voice_key.resolve 同口径）。
    #    但**不能只说“已配置”**：用户需要知道是哪把 key（脱敏）、从哪来，
    #    以及想换时去哪换。否则这句提示等于“此项已自动处理”的噪声。@author aceFelix
    if vk.is_configured(settings):
        ui.info(
            f"语音（阿里云百炼）：{vk.describe_detail(settings)}，此项跳过"
        )
        if vk.source(settings) != "configured":
            # 靠复用（LLM 的 key / 顶层 key）时才提醒：这种“配了”是推导出来的，
            # 用户很可能以为自己从来没配过语音，想单独换的时候会找不到入口。
            ui.info(
                "  当前没有独立的语音 Key，是靠上层配置复用出来的。"
                "想单独指定一个就跑 /init voice"
            )
        else:
            ui.info("  要更换这个 Key：运行 /init voice")
        return "", True

    # ② LLM 就是 DashScope：同一串 key 就是百炼 key，复用且不写盘
    if (vendor_key or "").strip().lower() == "dashscope" and (llm_api_key or "").strip():
        from agent.utils.mask import mask_key

        ui.info(
            "语音（阿里云百炼）：复用上面这个 key "
            f"{mask_key(llm_api_key.strip())}——当前 LLM 就是百炼，"
            "说话/听话走同一账号"
        )
        return "", True

    # ③ 真需要单独配一个百炼 key（LLM 用别家 / 刚好没填 LLM key）
    ui.info(
        "语音能力（说话与听话）固定走阿里云百炼，与上面的 LLM 厂商无关，"
        "需要单独一个百炼 API Key（在 bailian.console.aliyun.com 取）"
    )
    try:
        key = ui.ask_user(
            "语音 API Key（阿里云百炼；回车跳过 = 只有文字，没有语音）"
        ).strip()
    except (EOFError, KeyboardInterrupt):
        return "", False
    if not key:
        ui.info(
            "已跳过语音配置（不影响打字）。之后可运行 /init 补，"
            "或在 models.toml 顶层写 dashscope_api_key"
        )
        return "", False
    ui.info("语音 API Key：已填写")
    return key, True


async def run_quick_setup(ui: Any, settings: "Settings | None" = None, *,
                          ctx: Any = None,
                          path: Path | str | None = None) -> bool:
    """只问三件：厂商 / API Key / 模型。返回是否写盘成功。

    **模型不由我们替用户选**：拿到 key 后实时向厂商拉取这个账号真实可用的模型，
    让用户自己挑（可筛选、可手动输入）。拉不到才回落到内置推荐款，并明说原因。

    工作目录**不单独问**：它有安全默认值（当前目录），而模型没有——型号写错第一次
    调用就报错。问券预算只有三问，必须留给没有默认值的那件事。

    Args:
        ui: 终端 UI。
        settings: 当前配置（用于展示默认工作目录）。
        ctx: 命令上下文。从 `/init` 进入时传入，保存后能把新配置**热生效到当前
            会话**；从 `jarvis --init` 进入时为 None（独立进程，提示重启即可）。
        path: 台账路径（测试注入）。
    """
    from agent.commands.handlers.init_command import (
        _VENDORS,
        _save_config,
        _test_connection,
    )
    from agent.llm import model_catalog
    from agent.ui.terminal_picker import pick_from_list

    ui._console.print("\n[bold cyan]═══ J.A.R.V.I.S 首次配置 ═══[/bold cyan]")
    # 标题不写死“3 个问题”：语音那一问是条件出现的，写死数字会和实际显示对不上
    # （实测有人跑完反问“没有第四问啊”——他反而不确定是不是漏了）。
    ui._console.print(
        "[dim]厂商 / API Key / 模型三问，模型清单由厂商实时提供、不由我们替你选；\n"
        "语音（阿里云百炼）的 Key 仅在还缺时才多问一句。其余都用默认值，之后随时可改[/dim]\n"
    )

    # ① 厂商（含“其他 OpenAI 兼容服务”，自建也能接）
    items = [(v["key"], v["name"], v["desc"]) for v in _VENDORS]
    picked = pick_from_list(items, title="用哪家厂商的模型")
    if picked is None:
        ui.info("已取消首次配置（以后可随时运行 /init）")
        mark_declined(path)
        return False
    found = next((v for v in _VENDORS if v["key"] == picked), None)
    if found is None:
        mark_declined(path)
        return False
    vendor = dict(found)          # 复制一份再改：_VENDORS 是模块级共享表，不能写脏

    if vendor["key"] == "custom":
        # 自建服务没有默认接入地址，这一项没默认值，必须问
        try:
            vendor["default_base_url"] = ui.ask_user(
                "base_url（OpenAI 兼容接入地址）"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            pass

    # ② API Key
    try:
        api_key = ui.ask_user("API Key（回车跳过，稍后用 /init 补）").strip()
    except (EOFError, KeyboardInterrupt):
        api_key = ""
    ui.info("API Key：" + ("已填写" if api_key else "跳过"))

    # ③ 模型：在线目录优先，内置推荐兑底，手动输入永远可用
    online: list[str] = []
    reason = ""
    if api_key:
        ui.info("正在向厂商查询这个账号可用的模型…")
        online, reason = model_catalog.fetch_with_reason(
            api_format=vendor.get("api_format", "openai"),
            base_url=vendor.get("default_base_url", ""),
            api_key=api_key,
        )
    else:
        reason = "未提供 API Key，未发起在线查询"
    builtin = str(vendor.get("default_model", "") or "")
    if online:
        ordered = model_catalog.rank_for_picker(online, builtin)
        # 厂商目录里混着图像/语音/ embedding 等专用模型（实测 DashScope 返回 181 个），
        # 不能按名字猜着过滤（各家命名不同、会误伤），但要提醒用户选的是“对话模型”
        ui.info(
            f"厂商返回 {len(online)} 个可用模型。请选一个**对话模型**"
            "（可输入字符筛选；图像/语音专用模型不适合聊天）"
        )
    else:
        ordered = [builtin] if builtin else []
        # 把**具体原因**说出来：是“没给 key”还是“给了但没拉到”，用户才知道下一步
        # 该补 key 还是查网络。只显示一个模型时他必须能看懂为什么。
        ui.info(
            reason
            + (f"。先用内置推荐 {builtin}，也可以手动输入。" if builtin
               else "。请手动输入模型名。")
        )
    options = [(m, m, "") for m in ordered]
    options.append(("__manual__", "手动输入模型名…", "列表里没有你要的？自己填，不受列表限制"))
    chosen = pick_from_list(options, title="选模型")
    if chosen in (None, "__manual__"):
        try:
            typed = ui.ask_user(
                f"模型名{('［默认 ' + builtin + '］') if builtin else ''}"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            typed = ""
        model = typed or (chosen if chosen not in (None, "__manual__") else builtin)
    else:
        model = chosen
    vendor["default_model"] = model
    ui.info(f"模型：{model or '（未填，稍后 /models 设置）'}")

    llm_key = api_key if isinstance(api_key, str) else ""

    # ④ 语音（阿里云百炼）API Key —— 条件第四问。
    # 放在这里而不是最前面：前两问（厂商 / LLM key）已经决定了“能不能复用”。
    voice_key, voice_ready = _ask_voice_key(
        ui, settings, vendor_key=str(vendor.get("key", "")), llm_api_key=llm_key
    )

    # 其余全默认：api_format / base_url 取厂商默认，model_type 保守给 text
    # （要用图片输入就 /models 改——猜成多模态会让不支持视觉的模型在发图时报错）
    path_saved = _save_config(vendor, llm_key, "text")
    ui._console.print(f"\n[green]✓ 配置已保存到 {path_saved}[/green]")

    # 语音 key 单独写：它与 LLM 的 api_key 是**两套凭据**，但同住 models.toml 顶层
    # （dashscope_api_key 属 MODEL_TOP_KEYS，写在 settings.toml 会被启动期拆分搬走）
    if voice_key:
        from agent.config.models_config import save_dashscope_api_key

        voice_path = save_dashscope_api_key(voice_key)
        if voice_path:
            ui._console.print(
                f"[green]✓ 语音已配置（百炼 Key 写入 {voice_path}）[/green]"
            )
        else:
            ui._console.print(
                "[yellow]⚠ 语音 Key 写入失败，请手改 models.toml 的 "
                "dashscope_api_key[/yellow]"
            )

    if api_key:
        ok, msg = await _test_connection(vendor, api_key)
        if ok:
            ui._console.print(f"[green]✓ {msg}[/green]")
        else:
            ui._console.print(f"[yellow]⚠ 连接测试失败: {msg}[/yellow]")
            ui._console.print("[dim]配置已保存；确认 key 正确后重试即可[/dim]")

    workdir = str(getattr(settings, "workdir", "") or os.getcwd())
    mark_onboarded(path)
    # 写完盘必须让当前会话也用上：否则用户看到“✓ 连接成功”，下一句却走旧 provider
    # 报 Connection error（真机实测就是这个）。会话不存在时提示重启。
    if ctx is not None:
        from agent.commands.handlers.init_command import _apply_to_session

        ui._console.print(f"[dim]{_apply_to_session(ctx, model)}[/dim]")
    else:
        ui._console.print("[dim]重启 jarvis 后生效[/dim]")
    ui._console.print(
        f"\n[dim]工作目录用当前目录：{workdir}\n"
        "要换：jarvis --workdir <路径>；要换模型：/models[/dim]"
    )
    # 语音没配时把“怎么补”留在一屏可见的地方：否则用户只会在 /voice 里看到
    # 一句“TTS 连接建立失败”，完全不知道缺的是百炼 key。@author aceFelix
    if not voice_ready:
        from agent.config.voice_key import missing_hint

        ui._console.print(f"[yellow]ℹ {missing_hint(settings)}[/yellow]")
    ui._console.print("\n[bold cyan]可以开始了：直接说你想做什么。[/bold cyan]")
    return True


# ---------------------------------------------------------------------------
# 用几天后：主动提议调整（只提一次）
# ---------------------------------------------------------------------------


def maybe_suggest_adjustment(ui: Any, *, path: Path | str | None = None,
                             prefs_path: Path | str | None = None) -> bool:
    """用够几天后，把提炼出的候选拿出来问一次。返回是否真的提了。

    三条门槛（缺一不可）：启动 ≥ `SUGGEST_AFTER_STARTS` 次、首启满
    `SUGGEST_AFTER_DAYS` 天、且没提过。候选来自 P3 的证据式提炼——**不生效**，
    用户要自己 `/prefs accept`。
    """
    usage = load_usage(path)
    if usage.suggested:
        return False
    if usage.starts < SUGGEST_AFTER_STARTS or usage.days() < SUGGEST_AFTER_DAYS:
        return False

    try:
        from agent.config import prefs as prefs_mod
        from agent.core.actions import get_journal

        records = [rec.to_dict() for rec in get_journal().recent(200)]
        created = prefs_mod.suggest_from_records(records, path=prefs_path)
    except Exception:  # noqa: BLE001 - 提炼失败不该影响启动
        return False

    mark_suggested(path)      # 无论有没有候选都只提一次：没候选时别每次启动都扫一遍
    if not created:
        return False

    lines = ["我注意到你可能想让我记住这些（**还没生效**，需要你点头）："]
    for cand in created[:3]:
        lines.append(f"  · {cand.describe()}")
        lines.append(f"    依据：{cand.said}")
    lines.append("  看全部并接受：/prefs    不要就忽略这条，我不会再自动提。")
    ui.info("\n".join(lines))
    return True
