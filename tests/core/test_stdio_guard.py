"""标准流编码兜底的回归测试 —— 钉住「重定向时 emoji 不能把命令打崩」。

背景（2026-11-10）：`jarvis --doctor > log.txt` 在简体中文 Windows 上以
`UnicodeEncodeError: 'gbk' codec can't encode character '\\U0001f4e6'` 退出。
根因是 Python 对非控制台的 stdout 用区域编码（cp936）且 errors=strict，
而诊断表格的标题带 emoji。修法是入口处把 errors 放宽为 `replace`。

这些用例全部用「自己造的 gbk 流」复现，不依赖本机代码页，因此在 Linux CI 的
utf-8 环境与 Windows 上都能稳定跑（gbk 编解码器是 Python 标准库自带的）。

@author aceFelix
"""

from __future__ import annotations

import io
import sys
from typing import Any

import pytest

from agent.core.stdio import make_stdio_crash_proof

#: 表格标题里那个真实的 emoji（cp936 编不出来）。别换成星号等 GBK 字符，
#: 否则用例会失去意义——它测的正是「编不出来」这条路径。
_EMOJI = "\U0001f4e6"


class _RawStream:
    """把 TextIOWrapper 与其底层 BytesIO 绑在一起，便于断言落盘字节。"""

    def __init__(self, encoding: str = "gbk") -> None:
        self.buffer = io.BytesIO()
        self.text = io.TextIOWrapper(self.buffer, encoding=encoding, errors="strict")

    def bytes(self) -> bytes:
        self.text.flush()
        return self.buffer.getvalue()


def _replace_stdio(monkeypatch: pytest.MonkeyPatch) -> _RawStream:
    """把 stdout / stderr 都换成真 gbk 流，返回 stdout 那一路的句柄。"""
    handle = _RawStream()
    monkeypatch.setattr(sys, "stdout", handle.text)
    monkeypatch.setattr(sys, "stderr", _RawStream().text)
    return handle


# ---- 先证明问题真的存在：没有兜底就必须抛 ----


def test_gbk_stream_raises_on_emoji_without_guard() -> None:
    """反证：同一段文字在 gbk 严格流上确实编不出去（否则下面的用例是空转的）。"""
    handle = _RawStream()
    with pytest.raises(UnicodeEncodeError):
        handle.text.write(_EMOJI)


# ---- 兜底本身的契约 ----


def test_guard_degrades_emoji_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """装兜底后同一段文字不再抛错，emoji 降级成 `?`，中文照常写进去。"""
    handle = _replace_stdio(monkeypatch)

    assert set(make_stdio_crash_proof()) == {"stdout", "stderr"}

    handle.text.write(_EMOJI + " Python 包状态")  # 此前这一行直接抛 UnicodeEncodeError
    raw = handle.bytes().decode("gbk")
    assert "Python 包状态" in raw, f"中文字段被误伤：{raw!r}"
    assert "?" in raw and _EMOJI not in raw


def test_streams_without_reconfigure_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """StringIO 这类没有 reconfigure 的流要静默跳过，不能因为"帮不上忙"就报错。"""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert make_stdio_crash_proof() == []


def test_none_stream_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """pythonw / 冻结 GUI 下 sys.stdout 可能是 None。"""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert make_stdio_crash_proof() == []


def test_reconfigure_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """流已关闭时 reconfigure 会抛 ValueError——兜底自己绝不能把命令再打崩一次。"""

    class _Broken:
        def reconfigure(self, **_kwargs: Any) -> None:
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(sys, "stdout", _Broken())
    monkeypatch.setattr(sys, "stderr", _Broken())
    assert make_stdio_crash_proof() == []


def test_guard_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个入口（main 与 run_doctor）都会调一次，重复调用必须无副作用。"""
    handle = _replace_stdio(monkeypatch)

    make_stdio_crash_proof()
    assert set(make_stdio_crash_proof()) == {"stdout", "stderr"}
    handle.text.write(_EMOJI)
    assert handle.bytes() == b"?"


# ---- 两个真实入口确实装了这道兜底 ----


def test_run_doctor_survives_redirected_gbk_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m agent.doctor > log.txt` 这条路径不经过 main()，必须自带兜底。

    `_build_*` 三张表的探针全部打桩为空，避免在 CI 上真的去探测 pip / uv / playwright。
    """
    import agent.doctor as doctor_mod

    handle = _replace_stdio(monkeypatch)
    monkeypatch.setattr(doctor_mod, "_build_pkg_rows", lambda: [])
    monkeypatch.setattr(doctor_mod, "_build_system_rows", lambda: [])
    monkeypatch.setattr(doctor_mod, "_build_config_rows", lambda: [])

    assert doctor_mod.run_doctor() == 0

    raw = handle.bytes().decode("gbk")
    assert "依赖健康检查" in raw, f"诊断正文没写进流，说明中途炸了：{raw!r}"


def test_main_calls_the_stdio_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """`jarvis` 主入口必须在 parse_args 之前就上兜底（argparse 报错也可能带非常规字符）。"""
    import agent.core.stdio as stdio_mod
    import agent.doctor as doctor_mod
    import agent.main as main_mod

    calls: list[str] = []
    monkeypatch.setattr(stdio_mod, "make_stdio_crash_proof", lambda: calls.append("x"))
    monkeypatch.setattr(doctor_mod, "run_doctor", lambda: 0)

    assert main_mod.main(["--doctor"]) == 0
    assert calls == ["x"], "main() 没有调用编码兜底"
