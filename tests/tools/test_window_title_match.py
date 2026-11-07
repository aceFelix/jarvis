"""窗口标题匹配顺序的回归测试（工具层）。

2026-11-08 实测：``helloJarvis.txt`` 同时命中记事本与 Notepad++，
旧实现取第一条，结果点击落到了 Notepad++。模糊匹配必须优先短标题。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.tools.system.window import _find_windows


class FakeGw:
    def __init__(self, titles: list[str]) -> None:
        self._wins = [SimpleNamespace(title=t, _handle=i) for i, t in enumerate(titles)]

    def getAllWindows(self):
        return list(self._wins)


def test_fuzzy_match_prefers_shorter_title() -> None:
    gw = FakeGw(
        [
            r"E:\work\helloJarvis.txt - Notepad++",
            "helloJarvis.txt - 记事本",
        ]
    )

    matches = _find_windows(gw, "helloJarvis.txt", exact=False)

    assert matches[0].title == "helloJarvis.txt - 记事本"


def test_exact_match_still_first() -> None:
    gw = FakeGw(["helloJarvis.txt - 记事本", "helloJarvis.txt"])

    matches = _find_windows(gw, "helloJarvis.txt", exact=False)

    assert matches[0].title == "helloJarvis.txt"
