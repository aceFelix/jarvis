"""开发服务器进程与登记表管理。

从 DevServerTool 中抽离出来的底层能力，负责跨会话的服务器登记与进程生命周期：

- 登记表 ``~/.jarvis/dev_servers.json``：记录由 Jarvis 启动的开发服务器
  （pid / port / project_dir / command / url / log_file / started_at），
  使「关闭项目」在待机唤醒或重启后依然能定位到目标进程。
- 按 PID 判断存活、按进程树终止（Windows ``taskkill /F /T``，
  Unix 先向进程组发 SIGTERM 再 SIGKILL 兜底）。
- 按监听端口反查 PID（Windows ``netstat -ano``，Unix ``lsof -ti``）。
- 端口占用探测与等待释放。

Windows / Unix 双平台适配均使用标准库实现，不依赖 psutil。

@author aceFelix
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 登记表读写
# ---------------------------------------------------------------------------

def registry_path() -> Path:
    """开发服务器登记表路径：~/.jarvis/dev_servers.json。"""
    return Path.home() / ".jarvis" / "dev_servers.json"


def load_registry() -> list[dict]:
    """读取登记表，返回条目列表（文件不存在或解析失败时返回空列表）。"""
    path = registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("读取开发服务器登记表失败: %s", e)
        return []
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    return []


def save_registry(entries: list[dict]) -> None:
    """写入登记表（best-effort，失败仅记录日志，不抛异常影响主流程）。"""
    path = registry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning("写入开发服务器登记表失败: %s", e)


def register_server(entry: dict) -> None:
    """登记一个已启动的服务器（同 PID 覆盖旧记录，避免重复条目）。"""
    entries = load_registry()
    pid = entry.get("pid")
    entries = [e for e in entries if e.get("pid") != pid]
    entries.append(entry)
    save_registry(entries)


def unregister_server(pid: int | None = None, port: int | None = None) -> None:
    """从登记表移除条目：给 pid 删 pid 匹配项，给 port 删 port 匹配项。"""
    entries = load_registry()

    def _keep(e: dict) -> bool:
        if pid is not None and e.get("pid") == pid:
            return False
        if port is not None and e.get("port") == port:
            return False
        return True

    save_registry([e for e in entries if _keep(e)])


# ---------------------------------------------------------------------------
# 进程探测与终止
# ---------------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """判断进程是否存活。

    Windows 用 ``tasklist`` 按 PID 过滤；Unix 用 ``os.kill(pid, 0)`` 探测。

    @author aceFelix
    """
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return False
        for line in out.stdout.splitlines():
            cols = line.split()
            if len(cols) >= 2 and cols[1] == str(pid):
                return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但无权限探测
        return True
    except OSError:
        return False


def kill_process_tree(pid: int) -> tuple[bool, str]:
    """终止进程及其子进程。

    - Windows：``taskkill /F /T /PID``（``/T`` 一并结束子进程树，
      因为 npm/vite 会派生 node 子进程）
    - Unix：先向进程组发 SIGTERM，等待 5s 未退出再 SIGKILL 兜底

    返回 ``(是否成功, 说明文字)``。

    @author aceFelix
    """
    if not pid or pid <= 0:
        return False, "无效 PID"

    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:
            return False, f"taskkill 执行失败: {e}"
        if out.returncode == 0:
            return True, "已终止"
        detail = (out.stderr or out.stdout or "").strip()
        return False, detail or "taskkill 返回非零"

    # Unix：优先按进程组终止（启动时用了 start_new_session=True）
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        return True, "进程已不存在"
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True, "进程已不存在"
        except Exception as e:
            return False, str(e)

    deadline = time.time() + 5
    while time.time() < deadline:
        if not pid_alive(pid):
            return True, "已终止"
        time.sleep(0.3)

    # SIGKILL 兜底
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True, "进程已不存在"
        except Exception:
            pass
    return (not pid_alive(pid)), "已强制终止"


def find_pids_by_port(port: int) -> list[int]:
    """按监听端口查找进程 PID。

    - Windows：解析 ``netstat -ano -p tcp`` 的 LISTENING 行
    - Unix：``lsof -ti tcp:PORT``

    @author aceFelix
    """
    pids: list[int] = []
    if sys.platform == "win32":
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:
            logger.warning("netstat 查询端口失败: %s", e)
            return []
        for line in out.stdout.splitlines():
            if "LISTENING" not in line.upper():
                continue
            cols = line.split()
            if len(cols) < 5:
                continue
            local_addr = cols[1]
            if local_addr.rsplit(":", 1)[-1] != str(port):
                continue
            try:
                pid = int(cols[-1])
            except ValueError:
                continue
            if pid not in pids:
                pids.append(pid)
        return pids

    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as e:
        logger.warning("lsof 查询端口失败: %s", e)
        return []
    for tok in out.stdout.split():
        try:
            pid = int(tok)
        except ValueError:
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


# ---------------------------------------------------------------------------
# 端口探测
# ---------------------------------------------------------------------------

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检查端口是否被占用。

    同时尝试 IPv4 (127.0.0.1) 和 IPv6 (::1)，因为部分开发服务器
    （如 Vite）可能仅绑定到 IPv6 环回地址，导致 IPv4 检测不到。

    @author aceFelix
    """
    for h in (host, "::1"):
        try:
            with socket.create_connection((h, port), timeout=0.5):
                return True
        except OSError:
            continue
    return False


def wait_for_port_free(port: int, timeout: float = 5.0) -> bool:
    """等待端口释放（进程退出后端口可能仍处于 TIME_WAIT/CLOSE_WAIT）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_port_in_use(port):
            return True
        time.sleep(0.3)
    return not is_port_in_use(port)


# ---------------------------------------------------------------------------
# 路径比较
# ---------------------------------------------------------------------------

def same_path(a: str, b: str) -> bool:
    """比较两个路径是否指向同一位置（Windows 大小写不敏感）。"""
    if not a or not b:
        return False
    try:
        pa = Path(a).resolve()
        pb = Path(b).resolve()
    except Exception:
        return False
    if sys.platform == "win32":
        return str(pa).lower() == str(pb).lower()
    return str(pa) == str(pb)
