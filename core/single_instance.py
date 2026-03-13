"""
单实例锁：同一设备上仅允许运行一个 L2 和一个 L3。

锁文件位于 ~/.jachin/{name}.lock，内含 PID。
启动时若检测到已有实例在运行，会先杀死旧实例再继续。
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import time
from pathlib import Path


def _jachin_dir() -> Path:
    return Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))


def _is_process_alive(pid: int) -> bool:
    """检查进程是否仍在运行。跨平台。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                k32.CloseHandle(h)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_process(pid: int, *, force: bool = False) -> bool:
    """尝试终止进程。返回是否成功。"""
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import subprocess
            # taskkill /F 强制终止
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            sig = signal.SIGKILL if force else signal.SIGTERM
            os.kill(pid, sig)
        return True
    except Exception:
        return False


def acquire_single_instance_lock(name: str, *, kill_previous: bool = False) -> bool:
    """
    尝试获取单实例锁。成功则返回 True，进程退出时自动释放锁。

    Args:
        name: 锁名称，如 "l2" 或 "l3"
        kill_previous: 若为 True，检测到已有实例时先杀死旧进程再继续；否则退出并提示
    """
    jachin = _jachin_dir()
    jachin.mkdir(parents=True, exist_ok=True)
    lock_path = jachin / f"{name}.lock"
    label = "L2" if name == "l2" else "L3"

    # 检查已有实例
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            if pid == os.getpid():
                return True  # 同一进程，已是持有者
            if _is_process_alive(pid):
                if kill_previous:
                    print(f"[{label}] 检测到旧实例 PID {pid}，正在终止...", file=sys.stderr, flush=True)
                    _kill_process(pid)
                    for _ in range(15):
                        time.sleep(0.5)
                        if not _is_process_alive(pid):
                            break
                    if _is_process_alive(pid):
                        print(f"[{label}] 强制终止 PID {pid}...", file=sys.stderr, flush=True)
                        _kill_process(pid, force=True)
                        time.sleep(1)
                    print(f"[{label}] 旧实例已终止，继续启动。", file=sys.stderr, flush=True)
                else:
                    print(f"[{label}] 本设备已有 {label} 实例在运行 (PID {pid})，请先关闭后再启动。", file=sys.stderr)
                    sys.exit(1)
        except (ValueError, OSError):
            pass
        lock_path.unlink(missing_ok=True)

    # 写入当前 PID
    lock_path.write_text(str(os.getpid()), encoding="utf-8")

    def _release():
        try:
            if lock_path.exists() and lock_path.read_text().strip() == str(os.getpid()):
                lock_path.unlink(missing_ok=True)
        except Exception:
            pass

    atexit.register(_release)
    return True
