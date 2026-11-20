"""会话记忆在只读工作目录下的降级回归测试。

锁定的用户现象（2026-11-20 实机）：
    在 ``C:\\Program Files`` 下启动 jarvis，直接 traceback：
    ``PermissionError: [WinError 5] 拒绝访问: 'C:\\Program Files\\.jarvis'``

根因：会话记忆路径无条件 ``makedirs(workdir/.jarvis)``，而 workdir 可能是
只读目录；同时 ``load_session_memory`` 的 try 在 makedirs 之后，异常直接冒泡
到 ``compose_system_prompt`` → REPL 启动即崩。

修复：路径解析做「可写探测 + 用户级回退」，两个位置都不可用时返回空串，
读写全程不抛。

@author aceFelix
"""

from __future__ import annotations

import os

import pytest

from agent.core.memory import compactor as C


@pytest.fixture(autouse=True)
def _reset_cache():
    """路径解析带进程级缓存，用例之间必须清空。"""
    C._SESSION_MEMORY_PATH_CACHE.clear()
    C._FALLBACK_NOTIFIED.clear()
    yield
    C._SESSION_MEMORY_PATH_CACHE.clear()
    C._FALLBACK_NOTIFIED.clear()


def test_load_returns_empty_when_workdir_unwritable(tmp_path, monkeypatch):
    """只读 workdir：load 不抛异常，而是回退到用户级目录。"""
    real_makedirs = os.makedirs

    def fake_makedirs(path, *args, **kwargs):
        text = str(path)
        # 只让「workdir/.jarvis」失败；按 basename 判断，避免 tmp_path 里
        # 恰好含 “fallback”（用例函数名）而误判
        if os.path.basename(text.rstrip("\\/")) == ".jarvis":
            raise PermissionError(13, "denied", text)
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(os, "makedirs", fake_makedirs)
    monkeypatch.setattr(C, "_project_memory_dir", lambda wd: str(tmp_path / "fallback"))

    workdir = str(tmp_path / "readonly-project")
    os.makedirs(workdir, exist_ok=True)

    assert C.load_session_memory(workdir) == ""  # 不抛，按无记忆处理

    path = C._session_memory_path(workdir)
    assert path.startswith(str(tmp_path / "fallback"))
    assert os.path.isdir(str(tmp_path / "fallback"))


def test_update_writes_to_fallback(tmp_path, monkeypatch):
    """回退路径可写时，压缩结果应落到用户级目录而不是 workdir。"""
    real_makedirs = os.makedirs

    def fake_makedirs(path, *args, **kwargs):
        text = str(path)
        if os.path.basename(text.rstrip("\\/")) == ".jarvis":
            raise PermissionError(13, "denied", text)
        return real_makedirs(path, *args, **kwargs)

    monkeypatch.setattr(os, "makedirs", fake_makedirs)
    monkeypatch.setattr(C, "_project_memory_dir", lambda wd: str(tmp_path / "fallback"))

    workdir = str(tmp_path / "readonly-project")
    os.makedirs(workdir, exist_ok=True)

    result = C.CompactResult(
        summary="### 3. 文件与代码变更\n- 改了 a.py",
        new_messages=[],
        messages_kept=0,
        messages_summarized=3,
        pre_compact_tokens=9000,
        post_compact_tokens=2000,
    )
    written = C.update_session_memory(workdir, result)
    assert written is not None
    assert written.startswith(str(tmp_path / "fallback"))
    assert "文件与代码变更" in C.load_session_memory(workdir)
