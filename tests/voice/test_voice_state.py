"""agent.voice.voice_state 语音互斥锁单元测试。

锁语义：心跳续约 + 时间戳过期判定（不依赖 os.kill 检活）。
覆盖路径：
1. 无锁文件 → 获取成功，写入当前 PID
2. 他进程持锁且时间戳未过期 → 获取失败
3. 持锁时间戳过期（持锁进程已崩溃）→ 锁可被覆盖
4. 锁文件损坏 → 视为失效可覆盖
5. 同 PID 重复获取 → 放行（自身续锁）
6. release 删除锁文件，重复释放不报错

@author aceFelix
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from agent.voice import voice_state


@pytest.fixture
def lock_in_tmp(tmp_path, monkeypatch):
    """把锁文件重定向到临时目录，避免污染 ~/.jarvis。"""
    monkeypatch.setattr(voice_state, "_lock_path", lambda: tmp_path / "voice.lock")
    return tmp_path / "voice.lock"


@pytest.fixture
def alive_other_pid():
    """启动一个短命存活子进程，返回其 PID（跨平台可靠的"他进程存活"样本）。"""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    yield proc.pid
    proc.terminate()
    proc.wait()


def test_acquire_when_no_lock_writes_current_pid(lock_in_tmp):
    """无锁文件时获取成功，锁内容为 "当前PID,时间戳"。"""
    ok, info = voice_state.acquire_voice_lock()
    assert ok is True
    assert info == ""
    content = lock_in_tmp.read_text(encoding="utf-8")
    assert content.startswith(f"{os.getpid()},")
    voice_state.release_voice_lock()


def test_acquire_fails_when_held_by_alive_process(lock_in_tmp, alive_other_pid):
    """他进程持锁且时间戳未过期 → 拒绝获取并返回占用者信息。"""
    lock_in_tmp.write_text(f"{alive_other_pid},{time.time()}", encoding="utf-8")
    ok, info = voice_state.acquire_voice_lock()
    assert ok is False
    assert f"PID {alive_other_pid}" in info


def test_acquire_succeeds_when_lock_expired(lock_in_tmp, alive_other_pid):
    """他进程持锁但时间戳超过 TTL（心跳中断=进程已崩溃）→ 锁可被覆盖。"""
    expired_ts = time.time() - voice_state._LOCK_TTL - 10
    lock_in_tmp.write_text(f"{alive_other_pid},{expired_ts}", encoding="utf-8")
    ok, _ = voice_state.acquire_voice_lock()
    assert ok is True
    # 锁文件已被当前进程覆盖
    assert lock_in_tmp.read_text(encoding="utf-8").startswith(f"{os.getpid()},")
    voice_state.release_voice_lock()


def test_acquire_succeeds_when_lock_file_corrupt(lock_in_tmp):
    """锁文件内容损坏 → 视为失效，可覆盖获取。"""
    lock_in_tmp.write_text("not-a-valid-lock", encoding="utf-8")
    ok, _ = voice_state.acquire_voice_lock()
    assert ok is True
    voice_state.release_voice_lock()


def test_reacquire_by_same_pid_allowed(lock_in_tmp):
    """同 PID 重复获取（自身续锁场景）应放行。"""
    lock_in_tmp.write_text(f"{os.getpid()},{time.time()}", encoding="utf-8")
    ok, _ = voice_state.acquire_voice_lock()
    assert ok is True
    voice_state.release_voice_lock()


def test_release_removes_lock_file(lock_in_tmp):
    """release 后锁文件被删除；无锁文件时调用也不报错。"""
    ok, _ = voice_state.acquire_voice_lock()
    assert ok is True
    assert lock_in_tmp.exists()
    voice_state.release_voice_lock()
    assert not lock_in_tmp.exists()
    # 重复释放不应抛异常
    voice_state.release_voice_lock()
