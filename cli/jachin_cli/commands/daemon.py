"""
jachin daemon - 启动 nexus_daemon
"""
import subprocess
import sys
from pathlib import Path


def _project_root() -> Path:
    """定位 jachin-system 项目根目录"""
    p = Path(__file__).resolve()
    # cli/jachin_cli/commands/daemon.py -> ../../.. = project root
    for _ in range(4):
        p = p.parent
        if (p / "core" / "nexus_daemon").exists():
            return p
    return Path.cwd()


def run_daemon(args) -> int:
    root = _project_root()
    core_path = root / "core"
    if not (core_path / "nexus_daemon").exists():
        print("[ERROR] 未找到 core.nexus_daemon，请确保在 jachin-system 项目根目录下运行")
        return 1

    cmd = [sys.executable, "-m", "core.nexus_daemon"]
    try:
        subprocess.run(cmd, cwd=str(root))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        print(f"[ERROR] 启动 daemon 失败: {e}")
        return 1
