"""
单实例锁：同一设备上仅允许运行一个 L2 和一个 L3。

锁文件位于 ~/.jachin/{name}.lock，内含 PID。
启动时若检测到已有实例在运行，会先杀死旧实例再继续。
"""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
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


def _append_lock_debug(name: str, message: str) -> None:
    """Write lock diagnostics without importing L3 logging during early startup."""
    try:
        log_dir = os.environ.get("JACHIN_LOG_DIR")
        if log_dir:
            path = Path(log_dir) / "l3_debug.log"
        else:
            path = _jachin_dir() / "l3_debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"[single_instance:{name}] {message}\n")
    except Exception:
        pass


def _windows_process_command_line(pid: int) -> str:
    if sys.platform != "win32" or pid <= 0:
        return ""
    try:
        creationflags = 0x08000000  # CREATE_NO_WINDOW
        script = (
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" "
            "-ErrorAction SilentlyContinue; "
            "if ($null -eq $p) { exit 2 }; "
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
            "Write-Output (($p.Name) + \"`t\" + ($p.CommandLine))"
        )
        cp = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=creationflags,
        )
        if cp.returncode != 0:
            return ""
        return (cp.stdout or "").strip()
    except Exception:
        return ""


def _is_expected_lock_owner(name: str, pid: int) -> bool:
    if not _is_process_alive(pid):
        return False
    if sys.platform != "win32":
        return True
    cmd = _windows_process_command_line(pid).lower()
    if not cmd:
        return False
    if name == "l3":
        return "l3_node" in cmd or ("python" in cmd and "l3_node" in cmd)
    if name == "l2":
        return "l2" in cmd or "gateway" in cmd or "jachin" in cmd
    return True


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


def _is_pmo_copilot_child() -> bool:
    try:
        from l3_node.pmo_copilot_env import is_pmo_copilot_run

        return is_pmo_copilot_run()
    except Exception:
        return "--run-pmo-copilot" in sys.argv


def acquire_single_instance_lock(name: str, *, kill_previous: bool = False) -> bool:
    """
    尝试获取单实例锁。成功则返回 True，进程退出时自动释放锁。

    Args:
        name: 锁名称，如 "l2" 或 "l3"
        kill_previous: 若为 True，检测到已有实例时先杀死旧进程再继续；否则退出并提示
    """
    # PMO 一次性任务与 start-layer3 常驻 L3 并存；不得 taskkill 聊天进程
    if name == "l3" and _is_pmo_copilot_child():
        return True

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
            if _is_expected_lock_owner(name, pid):
                if kill_previous:
                    _append_lock_debug(name, f"existing {label} lock owner pid={pid}; killing previous")
                    print(f"[{label}] 检测到旧实例 PID {pid}，正在终止...", file=sys.stderr, flush=True)
                    _kill_process(pid)
                    for _ in range(15):
                        time.sleep(0.5)
                        if not _is_expected_lock_owner(name, pid):
                            break
                    if _is_expected_lock_owner(name, pid):
                        print(f"[{label}] 强制终止 PID {pid}...", file=sys.stderr, flush=True)
                        _kill_process(pid, force=True)
                        time.sleep(1)
                    print(f"[{label}] 旧实例已终止，继续启动。", file=sys.stderr, flush=True)
                else:
                    _append_lock_debug(name, f"refuse start: existing {label} owner pid={pid}")
                    print(f"[{label}] 本设备已有 {label} 实例在运行 (PID {pid})，请先关闭后再启动。", file=sys.stderr)
                    sys.exit(1)
            else:
                _append_lock_debug(name, f"stale lock cleared: pid={pid}")
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
