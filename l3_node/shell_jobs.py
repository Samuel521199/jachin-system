"""
P1+：core:shell_exec 后台执行与任务状态。

- 日志：~/.jachin/workspace/.shell_jobs/{job_id}.log
- 注册表：~/.jachin/workspace/.shell_jobs/registry.json
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _workspace_root() -> Path:
    return Path.home() / ".jachin" / "workspace"


def jobs_dir() -> Path:
    d = _workspace_root() / ".shell_jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path() -> Path:
    return jobs_dir() / "registry.json"


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_registry() -> dict[str, Any]:
    p = _registry_path()
    if not p.exists():
        return {"jobs": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"jobs": {}}
    except Exception as e:
        logger.warning("[shell_jobs] 读取 registry 失败: %s", e)
        return {"jobs": {}}


def _max_bg_jobs() -> int:
    try:
        from l3_node.intelligence_p1 import get_intel_p1_config

        v = get_intel_p1_config().get("shell_background_max_jobs", 16)
        n = int(v)
        return max(1, min(64, n))
    except Exception:
        return 16


def start_background_shell(command: str, workspace: Path, timeout_sec: int = 30) -> dict[str, Any]:
    """
    启动后台 shell（不阻塞等待结束）。timeout_sec 保留供将来 watchdog 使用。
    """
    from l3_node.intelligence_p1 import assert_shell_exec_allowed

    cmd = (command or "").strip()
    assert_shell_exec_allowed(cmd)

    reg = _load_registry()
    jobs = reg.setdefault("jobs", {})
    if not isinstance(jobs, dict):
        jobs = {}
        reg["jobs"] = jobs

    running = sum(
        1 for j in jobs.values() if isinstance(j, dict) and j.get("status") == "running"
    )
    lim = _max_bg_jobs()
    if running >= lim:
        raise ValueError(f"后台 shell 任务已达上限 ({lim})，请用 core:shell_job_status 查看或等待完成")

    job_id = uuid.uuid4().hex[:12]
    log_path = jobs_dir() / f"{job_id}.log"

    popen_kw: dict[str, Any] = dict(
        shell=True,
        cwd=str(workspace.resolve()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=False,
    )
    if sys.platform == "win32":
        # 不弹出控制台窗口
        popen_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kw["start_new_session"] = True

    # 直接写入日志文件，避免 PIPE 缓冲区塞满导致子进程阻塞
    log_f = open(log_path, "wb")
    popen_kw["stdout"] = log_f
    popen_kw["stderr"] = subprocess.STDOUT
    try:
        p = subprocess.Popen(cmd, **popen_kw)
    except Exception:
        log_f.close()
        raise
    finally:
        try:
            log_f.close()
        except OSError:
            pass

    entry = {
        "job_id": job_id,
        "command": cmd[:800],
        "pid": p.pid,
        "started_ts": int(time.time()),
        "log_path": str(log_path.resolve()),
        "status": "running",
        "exit_code": None,
        "timeout_sec": int(timeout_sec) if timeout_sec else 30,
    }
    jobs[job_id] = entry
    _atomic_write_json(_registry_path(), reg)

    def _reaper() -> None:
        try:
            code = p.wait()
        except Exception as e:
            logger.debug("[shell_jobs] wait job=%s err=%s", job_id, e)
            code = -1
        reg2 = _load_registry()
        jobs2 = reg2.setdefault("jobs", {})
        cur = jobs2.get(job_id)
        if isinstance(cur, dict):
            cur["status"] = "done"
            cur["exit_code"] = int(code) if code is not None else None
            cur["finished_ts"] = int(time.time())
            _atomic_write_json(_registry_path(), reg2)

    threading.Thread(target=_reaper, daemon=True).start()
    logger.info("[shell_jobs] 后台任务启动 job=%s pid=%s", job_id, p.pid)
    return entry


def get_job_record(job_id: str) -> dict[str, Any] | None:
    jid = (job_id or "").strip()
    if not jid:
        return None
    reg = _load_registry()
    j = reg.get("jobs", {}).get(jid)
    return j if isinstance(j, dict) else None


def _tail_log(path: Path, max_lines: int, max_bytes: int) -> str:
    if not path.exists():
        return "(日志文件尚未生成)"
    try:
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raw = raw[-max_bytes:]
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return "\n".join(lines[-max_lines:])
    except Exception as e:
        return f"(读取日志失败: {e})"


def format_job_status_report(job_id: str) -> str:
    rec = get_job_record(job_id)
    if not rec:
        return f"[shell_job_status] 未知 job_id: {job_id!r}"
    try:
        from l3_node.intelligence_p1 import get_intel_p1_config

        cfg = get_intel_p1_config()
        tail_lines = int(cfg.get("shell_job_status_tail_lines", 80))
        max_bytes = int(cfg.get("shell_job_status_max_log_bytes", 65536))
    except Exception:
        tail_lines, max_bytes = 80, 65536
    tail_lines = max(10, min(500, tail_lines))
    max_bytes = max(4096, min(2_000_000, max_bytes))

    log_path = Path(rec.get("log_path", ""))
    tail = _tail_log(log_path, tail_lines, max_bytes)
    st = rec.get("status", "?")
    exit_code = rec.get("exit_code")
    lines = [
        f"job_id: {rec.get('job_id')}",
        f"status: {st}",
        f"pid: {rec.get('pid')}",
        f"exit_code: {exit_code}",
        f"started_ts: {rec.get('started_ts')}",
        f"finished_ts: {rec.get('finished_ts', '')}",
        f"log: {log_path}",
        "--- log tail ---",
        tail,
    ]
    return "\n".join(lines)


def cancel_shell_job(job_id: str) -> str:
    """终止后台任务（需配置开启）。"""
    try:
        from l3_node.intelligence_p1 import get_intel_p1_config

        if not get_intel_p1_config().get("shell_job_cancel_enabled"):
            return (
                "[已禁用] 取消后台任务需在 ~/.jachin/nexus_config.json 设置 "
                '"intelligence_p1.shell_job_cancel_enabled": true'
            )
    except ImportError:
        return "[取消失败] intelligence_p1 不可用"

    rec = get_job_record(job_id)
    if not rec:
        return f"[shell_job_cancel] 未知 job_id: {job_id!r}"
    if rec.get("status") != "running":
        return f"[shell_job_cancel] 任务已结束 status={rec.get('status')} exit_code={rec.get('exit_code')}"
    pid = rec.get("pid")
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return "[shell_job_cancel] 无效 pid"
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid_i), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        else:
            os.kill(pid_i, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception as e:
        return f"[shell_job_cancel] 发送终止信号失败: {e}"

    reg = _load_registry()
    cur = reg.setdefault("jobs", {}).get(job_id.strip())
    if isinstance(cur, dict):
        cur["status"] = "cancelled"
        cur["finished_ts"] = int(time.time())
        _atomic_write_json(_registry_path(), reg)
    return f"[shell_job_cancel] 已请求终止 job_id={job_id} pid={pid_i}"
