"""首次启动感知询问的落盘记录 —— “这条引导还应该问一次吗”。

背景（aceFelix，2026-10 阶段 9 W2 第 7 步）：阶段 9 的隐私承诺是**默认全关**，但
“关着”与“用户知道自己可以开”是两件事——一个从来没打开过感知层的人，很可能只是
不知道有这项能力。本模块不采集任何现场信息，只回答两个问题：**还没问过吗**、
**用户表过态吗**，并把答案落在 `~/.jarvis/percept/consent.json`。

三条口径（都写在代码里，别只留在文档里）：

1. **询问只发生一次**：无论用户当时怎么反应（去面板拨开关 / 直接忽略），投递时就
   落一份 asked 记录，此后不再打扰。刻意不做“没回应就下次再问”——那正是感知层
   要避免的碎嘴，而且“问了三次还没等到回答”该理解为**拒绝**。
2. **已经在用 / 已表态的人不再问**：总开关开着（说明用户或配置已经选过），或
   `settings.toml` 里存在 `[percept]` 表（手改过配置的人不需要被科普一遍），或有
   本模块的记录（面板与 CLI 的表态都记在这里）。
3. **文件存在但读坏 → 按“已问过”处理**：宁可少问一次，也不要在一个长期运行的
   宿主里每次启动都弹同一条引导；把“读不出来”如实记进日志而不是当作没发生过。

投递时机不在这里（本模块无状态、无 I/O 之外的职责）：宿主把待投递的引导存进
pending 槽，等桌面壳真正连上 WS 时才取出投递并记账——启动期直接投会因为
“还没有在线客户端”被事件泵丢弃（这个教训已有前例：`init` 事件改为按连接首推）。

@author aceFelix
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.percept.consent")

#: 询问记录所在的文件名（与事件留痕、邮件已报集同目录：~/.jarvis/percept/）
STATE_FILENAME = "consent.json"

#: 用户可表达的表态（面板与 CLI 共用这三个值，其余一律拒绝）
CHOICES: tuple[str, ...] = ("on", "off", "dismissed")


def consent_path() -> Path:
    """询问记录路径：`~/.jarvis/percept/consent.json`。

    目录单一真源用 `core/memory/store.py` 的 `user_jarvis_dir()`（与
    ``percept.default_log_path()`` 同一口径），并且**每次现取**：测试靠 patch
    `Path.home` 或本函数来隔离，模块级常量会把用户真实目录写脏。
    """
    from agent.core.memory.store import user_jarvis_dir

    try:
        root = user_jarvis_dir()
    except Exception:  # pragma: no cover - 只防循环导入/记忆模块意外
        root = Path.home() / ".jarvis"
    return root / "percept" / STATE_FILENAME


def read_consent() -> dict[str, Any] | None:
    """读询问记录；不存在或读不出来都返回 None（读侧永不抛异常）。

    损坏文本只记日志不回抛：调用方只需要知道“有没有内容”，是否该再问由
    :func:`needs_ask` 用**文件存在性**判定（避免坏一次就问一次）。
    """
    path = consent_path()
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("感知询问记录读取失败(%s): %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def has_record() -> bool:
    """是否已有询问/表态记录（**只看文件存在性**，内容坏掉也算问过）。

    这是 :func:`needs_ask` 的第一道判据：内容能读出来当然更好，但读不出来时
    保守地不再问，比每次启动都弹同一条引导更符合“打扰预算”的取向。
    """
    try:
        return consent_path().exists()
    except OSError:  # pragma: no cover - 极端文件系统状态下按“没记录”处理
        return False


def configured_in_toml() -> bool:
    """用户级 `settings.toml` 里是否存在 `[percept]` 表。

    手改过配置的人已经对感知层做过选择（哪怕选择是“全关”），不必再被引导科普
    一遍。读不到/解析失败一律返回 False——那种情况下用户确实没配过，该问。
    """
    from agent.config.toml_writer import settings_path

    try:
        raw = settings_path().read_bytes()
        return isinstance(tomllib.loads(raw.decode("utf-8")).get("percept"), dict)
    except Exception:  # noqa: BLE001 - 文件缺失/编码坏/语法坏都按“没配过”处理
        return False


def needs_ask(settings: Any) -> bool:
    """现在是否还需要问一次（三条判据全过才问）。

    Args:
        settings: 全局 Settings（读 `percept_enabled`）。

    Returns:
        True = 从没问过、总开关没开、也没在手改的配置里表过态。
    """
    if has_record():
        return False
    if bool(getattr(settings, "percept_enabled", False)):
        return False
    return not configured_in_toml()


def mark_asked(**extra: Any) -> dict[str, Any]:
    """落一份“已询问”记录（幂等：已有记录时只补 asked_at，不清空 choice）。

    Args:
        **extra: 需要一并留档的上下文字段（如投递时的开关快照），原样存入。

    Returns:
        写入后的完整记录 dict；落盘失败返回内存里的同一份（调用方据此继续，
        最坏后果是下次启动再问一次，不影响本次投递）。
    """
    record = read_consent() or {}
    record.setdefault("asked_at", round(time.time(), 3))
    record.setdefault("choice", "")
    record["updated_at"] = round(time.time(), 3)
    record.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool))})
    if not _write(record):
        logger.warning("感知询问记录落盘失败，本次仍按已询问处理")
    return record


def record_choice(choice: str) -> dict[str, Any]:
    """记录用户的表态（面板拨开关 / CLI 明确表态时调用）。

    Args:
        choice: ``on`` / ``off`` / ``dismissed`` 之一。

    Raises:
        ValueError: 非法表态值（不静默忽略，避免写出一眼看不懂的文件）。
    """
    if choice not in CHOICES:
        raise ValueError(f"未知的感知表态 {choice}（可选：{', '.join(CHOICES)}）")
    record = read_consent() or {}
    record["choice"] = choice
    record["updated_at"] = round(time.time(), 3)
    # 表态本身就是“做过选择”，等价于问过；asked_at 缺失时补当前时间
    record.setdefault("asked_at", None)
    record.setdefault("chosen_at", round(time.time(), 3))
    _write(record)
    return record


def _write(record: dict[str, Any]) -> bool:
    """原子落盘（临时文件 + ``os.replace``），与感知源快照同一手法。

    半截 JSON 会让下次启动“读不出来”，虽然 :func:`needs_ask` 已按存在性兜底，
    但把 choice 丢了就是丢了用户的表态，所以这里坚持先写临时文件再改名。
    """
    path = consent_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("感知询问记录写入失败(%s): %s", path, e)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:  # pragma: no cover - 清理临时文件失败不再往上抛
            pass
        return False
    return True
