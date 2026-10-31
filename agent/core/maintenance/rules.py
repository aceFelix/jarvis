"""系统维护层 —— 清理规则库的解析、路径展开与白名单校验。

## 为什么要有这一层

清理是**唯一会主动删用户文件的子系统**，所以「删什么」不能写在代码里，必须是一张
可审阅、可 diff、可被用户改的表（``agent/config/cleaners.toml``）。本模块只做三件事：
把表读进来、把路径展开成真实绝对路径、把不安全的规则拦在外面。

## 三道闸（缺一条都不放行）

1. **路径必须是绝对的**。展开 ``%VAR%`` 与 ``~`` 之后再判断；展开完还不是绝对的，
   说明这条规则写错了，整条作废。
2. **路径里不许有通配符**。要限定文件用 ``include`` 字段，别在路径里写 ``*``——
   路径级通配一错就是灾难，文件级通配错最多少清几个文件。
3. **不许指向盘根/目录根**。``C:\\`` 这类路径一旦进了清单，一次误操作就是整盘。

命令型规则（``kind = "command"``）另有一道：可执行文件名必须落在
:data:`ALLOWED_COMMAND_BINARIES` 白名单里。规则文件是用户可改的，没有这道闸，
改一行 TOML 就能让维护层跑任意命令。

而且命令型规则**从不代执行**：它们只以「命令文本 + 后果说明」的形式出现在
``/disk --deep`` 里，跑不跑由用户决定。原因是这些命令不可还原（DISM 清掉的
旧组件、vssadmin 删掉的还原点、powercfg 关掉的休眠文件都回不来），隔离区那套
救不了它们。

@author aceFelix
"""

from __future__ import annotations

import fnmatch
import os
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+ 自带；3.10 及以下没有 tomllib，维护层整体降级为不可用
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 项目要求 3.11+
    tomllib = None  # type: ignore[assignment]

#: 内置规则库位置（``agent/config/cleaners.toml``）
DEFAULT_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "cleaners.toml"

#: 命令型清理允许调用的可执行文件（小写 basename）。
#: 刻意只收系统自带、且只做「清理/分析」这四件事的：
#: - dism      清 WinSxS 旧组件（/StartComponentCleanup、/AnalyzeComponentStore）
#: - vssadmin  删卷影副本（delete shadows / list shadowstorage）
#: - powercfg  关休眠（/hibernate off、/a）
#: - cleanmgr  Windows 磁盘清理（/sagerun:N 按预存配置执行）
#:
#: 关于 cleanmgr：它是白名单里**唯一能删到用户文件**的一个——/sageset 的勾选项里
#: 包含「下载」文件夹，用户勾了就会被清。能放进来是因为它必须先用 /sageset 走一遍
#: GUI 勾选，等于用户亲手审过一遍范围；而且命令型规则在本项目里**从不代执行**
#: （见 cleaner.clean 里对 is_command 的拒绝），只把命令文本交给用户。
#:
#: 往这里加东西前先问：这条命令能不能删到用户的工作文件？能就别加；
#: 如果非加不可（像 cleanmgr 这样实在有用），就必须同时确认它不会被自动执行。
ALLOWED_COMMAND_BINARIES = frozenset(
    {"dism.exe", "vssadmin.exe", "powercfg.exe", "cleanmgr.exe"}
)

#: 风险等级（与 sandbox.RiskLevel 的语义对齐，但只用三档：清理不需要 CRITICAL）
VALID_RISKS = ("low", "medium", "high")

#: 报告分组
VALID_CATEGORIES = ("temp", "system", "dev", "browser", "recycle")

#: 多盘展开占位符：回收站、卷影副本这类按盘存在的东西用它
ALL_DRIVES_PLACEHOLDER = "%ALLDRIVES%"


def _local_drives() -> list[str]:
    """列出本机存在的盘符（形如 ``["C:", "D:"]``）。非 Windows 返回空表。"""
    if os.name != "nt":
        return []
    out: list[str] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            out.append(f"{letter}:")
    return out


def expand_path(raw: str) -> str:
    """把规则里的路径展开成真实绝对路径。

    支持三种写法（项目里其它地方没有这种先例，故在此说明）：
    - ``%VAR%``：Windows 风格环境变量（``ntpath.expandvars`` **只认 ``$``**，
      所以必须自己展开，不能直接调它）
    - ``~``：用户目录
    - ``%ALLDRIVES%``：展开为每个存在的盘符，返回多行由调用方逐个处理

    单盘路径返回展开后的字符串；含 ``%ALLDRIVES%`` 时调用方应先用
    :func:`expand_path_list`。
    """
    text = raw.strip()
    if not text:
        return ""
    text = os.path.expanduser(text)
    # 手工展开 %VAR%：从内到外反复替换，直到没有占位符或不再变化（防自引用死循环）
    for _ in range(8):
        if "%" not in text:
            break
        start = text.find("%")
        end = text.find("%", start + 1)
        if end == -1:
            break
        name = text[start + 1 : end]
        value = os.environ.get(name, "")
        if not name or (value == "" and f"%{name}%" not in os.environ):
            # 未知变量：保留原样，交由校验环节判为「不是绝对路径」而拒绝，
            # 比悄悄替换成空串安全（空串会让路径退化成父目录）。
            break
        text = text[:start] + value + text[end + 1 :]
    return text


def expand_path_list(raw: str) -> list[str]:
    """展开单条 ``paths`` 条目；含 ``%ALLDRIVES%`` 时按盘展开成多条。"""
    if ALL_DRIVES_PLACEHOLDER not in raw:
        return [expand_path(raw)]
    drives = _local_drives()
    if not drives:
        return []
    return [expand_path(raw.replace(ALL_DRIVES_PLACEHOLDER, d)) for d in drives]


def match_any(name: str, patterns: tuple[str, ...]) -> str | None:
    """文件名是否命中任一通配；命中返回该 pattern（给"跳过原因"用），否则 None。"""
    for pat in patterns:
        if fnmatch.fnmatchcase(name, pat):
            return pat
    return None


@dataclass(frozen=True)
class CleanerRule:
    """一条通过校验的清理规则。

    Attributes:
        id: 稳定标识，审计/账本/隔离区批次名都用它。
        name: 中文名。
        kind: ``paths``（按路径清）或 ``command``（走系统命令）。
        paths: 已展开的绝对路径列表（``kind=paths`` 时非空）。
        include: 文件名通配白名单；空表示整个目录。
        protect: 文件名通配保护名单，命中即跳过。
        category / risk / requires_admin / desc: 见 cleaners.toml 注释。
        clean_cmd / analyze_cmd: ``kind=command`` 时的命令向量。
        auto_ok: 是否允许**无人值守自动清理**。默认 False，逐条人工评估后显式打开。
            刻意不复用 :attr:`risk`——它衡量「删了会怎样」，不是「删了会不会被
            发现」。``recycle_bin`` 是 low 但用户刚删的文件就在里面，``user_temp``
            是 low 但可能正被占用；把它们当自动许可就是拿风险等级顶替意愿。
            自动清理的真实标准见 :mod:`agent.core.maintenance.auto_clean`。
        unaddressable: 该规则在这台机器上是否无法落地（路径不存在/平台不支持），
            非空即原因。**报告里要如实列出来**，不能悄悄消失。
    """

    id: str
    name: str
    kind: str
    desc: str
    category: str
    risk: str
    requires_admin: bool
    paths: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    protect: tuple[str, ...] = ()
    clean_cmd: tuple[str, ...] = ()
    analyze_cmd: tuple[str, ...] = ()
    auto_ok: bool = False
    unaddressable: str = ""

    @property
    def is_command(self) -> bool:
        return self.kind == "command"

    @property
    def risk_rank(self) -> int:
        """风险排序用（high 最大）。"""
        return VALID_RISKS.index(self.risk)

    def default_selected(self) -> bool:
        """是否默认勾选。high 一律不勾（PLAN 第八节坑 1 的直接规避）。"""
        return self.risk != "high"


@dataclass
class RuleBook:
    """一次加载的结果：可用规则 + 被拒绝的规则（都要给用户看到）。"""

    rules: list[CleanerRule] = field(default_factory=list)
    #: ``(规则 id 或 "<无 id>", 拒绝原因)``——静默丢规则是最糟的失败模式，
    #: 用户会以为自己有一条规则在跑，其实早被跳过了。
    rejected: list[tuple[str, str]] = field(default_factory=list)
    source: str = ""

    def by_id(self, rule_id: str) -> CleanerRule | None:
        for r in self.rules:
            if r.id == rule_id:
                return r
        return None

    def ids(self) -> list[str]:
        return [r.id for r in self.rules]


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    """把单字符串或字符串数组统一成 tuple。"""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if str(v).strip())
    return ()


def _has_wildcard(path: str) -> bool:
    return any(ch in path for ch in "*?[")


def _is_root_path(path: str) -> bool:
    """是否指向盘根或文件系统根（这类路径绝不许进清单）。"""
    p = os.path.normpath(path)
    drive, tail = os.path.splitdrive(p)
    rest = tail.replace("\\", "").replace("/", "")
    if not rest:
        return True  # "C:\" 或 "/"
    return not drive and p in ("/", "\\")


def parse_rule(raw: dict[str, Any]) -> CleanerRule:
    """把一条 TOML 表解析成 :class:`CleanerRule`；不合法抛 :class:`ValueError`。

    抛错而不是返回 None：让 :func:`load_rulebook` 统一收集原因，
    调用方永远看得到「哪条规则为什么不生效」。
    """
    rule_id = str(raw.get("id") or "").strip()
    if not rule_id:
        raise ValueError("缺少 id")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("缺少 name")
    desc = str(raw.get("desc") or "").strip()
    if not desc:
        # desc 是强制字段：没有「删了会怎样」的说明，就不许出现在用户面前
        raise ValueError("缺少 desc（必须写清删了会怎样，这段话会直接展示给用户）")

    category = str(raw.get("category") or "").strip()
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category 必须是 {'/'.join(VALID_CATEGORIES)} 之一，实际 {category!r}")
    risk = str(raw.get("risk") or "").strip()
    if risk not in VALID_RISKS:
        raise ValueError(f"risk 必须是 {'/'.join(VALID_RISKS)} 之一，实际 {risk!r}")

    kind = str(raw.get("kind") or "paths").strip()
    if kind not in ("paths", "command"):
        raise ValueError(f"kind 必须是 paths/command 之一，实际 {kind!r}")

    requires_admin = bool(raw.get("requires_admin", False))
    include = _as_str_tuple(raw.get("include"))
    protect = _as_str_tuple(raw.get("protect"))

    # auto_ok 的两道反向闸：标错比不标危险得多，宁可直接作废这条规则。
    #
    # 1. 命令型永不自动执行——DISM/vssadmin/powercfg/cleanmgr 都是不可还原的，
    #    隔离区那套救不了它们（模块头注释已经交代过这条线）。允许 auto_ok 等于
    #    把「从不代执行」这条承诺开了个后门。
    auto_ok = bool(raw.get("auto_ok", False))
    if auto_ok and kind == "command":
        raise ValueError(
            "command 型规则不许 auto_ok：命令型不可还原，从不代执行"
        )
    # 2. 需管理员的一律不能自动——后台进程提不了权，只会在日志里刷 PermissionError，
    #    用户既没看到清理发生了，也没看到它失败。
    if auto_ok and requires_admin:
        raise ValueError(
            "需管理员（requires_admin）的规则不许 auto_ok：后台进程提不了权，只会白跑"
        )

    if kind == "paths":
        raw_paths = _as_str_tuple(raw.get("paths"))
        if not raw_paths:
            raise ValueError("kind=paths 必须给 paths")
        expanded: list[str] = []
        for one in raw_paths:
            if _has_wildcard(one):
                raise ValueError(
                    f"路径不许含通配符：{one!r}（要限定文件请用 include 字段）"
                )
            for real in expand_path_list(one):
                if not real or not os.path.isabs(real):
                    raise ValueError(f"展开后不是绝对路径：{one!r} -> {real!r}")
                if _is_root_path(real):
                    raise ValueError(f"不许指向盘根：{real!r}")
                expanded.append(os.path.normpath(real))
        if not expanded:
            raise ValueError("paths 展开后为空（%ALLDRIVES% 在这台机器上没有可用盘？）")
        return CleanerRule(
            id=rule_id, name=name, kind=kind, desc=desc, category=category,
            risk=risk, requires_admin=requires_admin, paths=tuple(expanded),
            include=include, protect=protect, auto_ok=auto_ok,
        )

    # ---- kind == "command" ----
    clean_cmd = _as_str_tuple(raw.get("clean_cmd"))
    if not clean_cmd:
        raise ValueError("kind=command 必须给 clean_cmd")
    binary = os.path.basename(clean_cmd[0]).lower()
    if binary not in ALLOWED_COMMAND_BINARIES:
        raise ValueError(
            f"命令不在白名单：{binary!r}（允许 {', '.join(sorted(ALLOWED_COMMAND_BINARIES))}）"
        )
    analyze_cmd = _as_str_tuple(raw.get("analyze_cmd"))
    if analyze_cmd:
        abinary = os.path.basename(analyze_cmd[0]).lower()
        if abinary not in ALLOWED_COMMAND_BINARIES:
            raise ValueError(f"analyze_cmd 不在白名单：{abinary!r}")
    return CleanerRule(
        id=rule_id, name=name, kind=kind, desc=desc, category=category,
        risk=risk, requires_admin=requires_admin, include=include, protect=protect,
        clean_cmd=clean_cmd, analyze_cmd=analyze_cmd,
    )


def load_rulebook(rules_file: str | os.PathLike[str] | None = None) -> RuleBook:
    """加载规则库。

    Args:
        rules_file: 自定义规则文件；留空用内置 ``agent/config/cleaners.toml``。

    Returns:
        :class:`RuleBook`。任一规则解析失败只记进 ``rejected``，不影响其余规则——
        一张表里写错一条不该让整套清理瘫痪。
    """
    path = Path(rules_file) if rules_file else DEFAULT_RULES_PATH
    book = RuleBook(source=str(path))
    if tomllib is None:
        book.rejected.append(("<rules>", "当前 Python 版本缺少 tomllib，规则库不可用"))
        return book
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        book.rejected.append((str(path), "规则文件不存在"))
        return book
    except (OSError, ValueError) as exc:
        # TOML 语法错误必须报出来：静默退回空表会让 /disk 显示"没什么可清"，
        # 用户以为磁盘很干净，其实规则一条都没加载。
        book.rejected.append((str(path), f"规则文件解析失败：{type(exc).__name__}: {exc}"))
        return book

    seen: set[str] = set()
    for raw in data.get("cleaner") or []:
        if not isinstance(raw, dict):
            book.rejected.append(("<非表项>", "cleaner 必须是 [[cleaner]] 表"))
            continue
        try:
            rule = parse_rule(raw)
        except ValueError as exc:
            book.rejected.append((str(raw.get("id") or "<无 id>"), str(exc)))
            continue
        if rule.id in seen:
            book.rejected.append((rule.id, "id 重复，后一条被跳过"))
            continue
        seen.add(rule.id)
        book.rules.append(rule)
    return book