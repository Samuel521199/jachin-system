#!/usr/bin/env python3
"""
PMO 多维表变更 — 实时监看「机器人是否收到回调」

默认 **主动轮询**（hybrid/poll 每 10s 拉表 diff）+ tail raw_events.ndjson，
不依赖后台守护进程也能在改表后 ~15s 内在本窗口打出 RECEIVED。

用法：
  python scripts/watch_pmo_bitable_lark_events.py          # 推荐：主动轮询 + tail 事件文件
  python scripts/watch_pmo_bitable_lark_events.py --status
  python scripts/watch_pmo_bitable_lark_events.py --passive   # 仅 tail 文件（旧行为）
  python scripts/watch_pmo_bitable_lark_events.py --log       # tail 长连接日志

Lark 官方事件（lark_event）仍需长连接守护：
  .\\scripts\\start_pmo_bitable_watch_daemon.ps1
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env", override=False)
    load_dotenv(Path.home() / ".jachin" / ".env", override=False)
except Exception:
    pass

_PID_FILE = Path.home() / ".jachin" / "data" / "pmo_bitable_watch_long_connection.pid"


def _utc_now_short() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _fmt_event_line(record: dict) -> str:
    src = str(record.get("source") or "?")
    at = str(record.get("received_at") or "")[:19]
    events = record.get("events") or []
    parts: list[str] = []
    for ev in events[:3]:
        if not isinstance(ev, dict):
            continue
        ct = str(ev.get("change_type") or "?")
        rid = str(ev.get("record_id") or "")[:16]
        label = str(ev.get("label") or "")[:40]
        parts.append(f"{ct}:{rid}:{label}")
    more = f" +{len(events) - 3}" if len(events) > 3 else ""
    detail = " | ".join(parts) if parts else "(无明细)"
    icon = "✅" if src == "lark_event" else "📡"
    return f"{icon} RECEIVED [{src}] {at}  n={len(events)}{more}  {detail}"


def _fmt_tick_line(out: dict) -> str | None:
    action = str(out.get("action") or "")
    if action == "session_active":
        n = int(out.get("new_changes") or 0)
        if n < 1:
            return None
        return (
            f"📡 POLL_HIT {_utc_now_short()}  action=session_active "
            f"new_changes={n} session_total={out.get('session_event_count')} "
            f"msg={out.get('message') or ''}"
        )
    if action == "session_finalized_notify":
        return (
            f"🔔 FINALIZED {_utc_now_short()}  events={out.get('event_count')} "
            f"notified={out.get('notified')}"
        )
    if action == "fetch_failed":
        return f"❌ POLL_ERROR {_utc_now_short()}  {out.get('error')}"
    return None


def _daemon_alive() -> tuple[bool, str]:
    if not _PID_FILE.is_file():
        return False, "无 pid 文件"
    try:
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return False, "pid 无效"
    try:
        import os

        os.kill(pid, 0)
        return True, str(pid)
    except OSError:
        return False, f"PID {pid} 已退出"


def cmd_status() -> int:
    from l3_node.tools.pmo_bitable_watch import run_bitable_watch_status

    st = run_bitable_watch_status()
    alive, pid_info = _daemon_alive()
    print(json.dumps(st, ensure_ascii=False, indent=2))
    print("\n--- 判读 ---")
    print(f"  mode={st.get('mode')}  session_active={st.get('session_active')}")
    print(f"  session_source={st.get('session_source')}  session_event_count={st.get('session_event_count')}")
    print(f"  last_event_at={st.get('last_event_at')}")
    print(f"  last_tick_at={st.get('last_tick_at')}  (poll/hybrid 拉表)")
    print(f"  长连接守护: {'运行中 PID=' + pid_info if alive else '未运行 — ' + pid_info}")
    print(f"  raw_events → {st.get('raw_events_ndjson')}")
    return 0


def _tail_ndjson_worker(path: Path, *, from_start: bool, stop: threading.Event) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.touch()
    with path.open("r", encoding="utf-8") as f:
        if not from_start:
            f.seek(0, 2)
        while not stop.is_set():
            line = f.readline()
            if not line:
                time.sleep(0.25)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                print(f"[watch] (非 JSON) {line[:200]}", flush=True)
                continue
            if isinstance(rec, dict):
                print(_fmt_event_line(rec), flush=True)


def _active_watch(*, poll_interval: float, from_start: bool) -> int:
    from l3_node.jobs.pmo_bitable_watch_scheduler import run_pmo_bitable_watch_once
    from l3_node.tools.pmo_bitable_watch import (
        _LONG_CONNECTION_LOG,
        _RAW_EVENTS_NDJSON,
        _load_watch_config,
        run_bitable_watch_status,
    )

    cfg = _load_watch_config()
    mode = str(cfg.get("mode") or "webhook").lower()
    poll_iv = max(5.0, float(poll_interval or cfg.get("poll_interval_seconds") or 10))

    print(
        "[watch] PMO 多维表变更实时监看（主动轮询 + 事件 tail）\n"
        "  改表后本窗口应出现 📡 POLL_HIT 或 ✅ RECEIVED [lark_event]\n"
        "  Ctrl+C 退出\n"
    )
    print(f"[watch] mode={mode} poll_every={poll_iv}s idle={cfg.get('idle_seconds')}s table={cfg.get('table_id')}")

    alive, pid_info = _daemon_alive()
    if alive:
        print(f"[watch] 长连接守护: 运行中 PID={pid_info}（可收 lark_event）")
    else:
        print(
            f"[watch] ⚠️ 长连接守护未运行（{pid_info}）— 仅 poll_diff 可见；"
            f"要 Lark 官方回调请运行: .\\scripts\\start_pmo_bitable_watch_daemon.ps1"
        )
    print(f"[watch] tail {_RAW_EVENTS_NDJSON}\n")

    stop = threading.Event()
    t = threading.Thread(
        target=_tail_ndjson_worker,
        args=(_RAW_EVENTS_NDJSON,),
        kwargs={"from_start": from_start, "stop": stop},
        daemon=True,
    )
    t.start()

    heartbeat_at = 0.0
    use_poll = mode in ("poll", "hybrid")

    try:
        while True:
            if use_poll:
                try:
                    out = run_pmo_bitable_watch_once()
                    line = _fmt_tick_line(out)
                    if line:
                        print(line, flush=True)
                    elif str(out.get("action") or "") == "waiting_debounce":
                        since = out.get("seconds_since_last_change")
                        if since is not None and float(since) >= float(cfg.get("idle_seconds") or 20) * 0.8:
                            print(
                                f"[watch] ⏳ debounce {since}s / {cfg.get('idle_seconds')}s "
                                f"events={out.get('session_event_count')}",
                                flush=True,
                            )
                except Exception as e:
                    print(f"[watch] ❌ tick 异常: {e}", flush=True)
            else:
                try:
                    out = run_pmo_bitable_watch_once()
                    if int(out.get("merged") or 0) > 0:
                        print(
                            f"✅ WEBHOOK_DEBOUNCE {_utc_now_short()} merged={out.get('merged')} "
                            f"session={out.get('session_event_count')}",
                            flush=True,
                        )
                except Exception as e:
                    print(f"[watch] ❌ debounce tick 异常: {e}", flush=True)

            now = time.time()
            if now - heartbeat_at >= 60.0:
                st = run_bitable_watch_status()
                alive, pid_info = _daemon_alive()
                print(
                    f"[watch] ♥ heartbeat {_utc_now_short()} "
                    f"session_active={st.get('session_active')} "
                    f"last_tick={str(st.get('last_tick_at') or '-')[:19]} "
                    f"lc={'up:' + pid_info if alive else 'down'}",
                    flush=True,
                )
                heartbeat_at = now

            time.sleep(poll_iv)
    except KeyboardInterrupt:
        stop.set()
        print("\n[watch] 已退出", flush=True)
        return 0


def _tail_log(path: Path, *, from_start: bool) -> int:
    keywords = (
        "pmo_bitable_events",
        "长连接摄入",
        "Lark 事件触发",
        "新编辑会话",
        "bitable",
        "merged=",
        "PMO webhook",
    )
    if not path.is_file():
        print(f"[watch] 日志不存在: {path}")
        return 1
    with path.open("r", encoding="utf-8", errors="replace") as f:
        if not from_start:
            f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.3)
                continue
            if any(k in line for k in keywords):
                print(line.rstrip(), flush=True)


def _passive_tail(path: Path, *, from_start: bool) -> int:
    print("[watch] 被动模式：仅 tail raw_events（需其它进程写入）\n")
    stop = threading.Event()
    try:
        _tail_ndjson_worker(path, from_start=from_start, stop=stop)
    except KeyboardInterrupt:
        return 0
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="PMO 多维表变更 — 实时监看")
    ap.add_argument("--status", action="store_true", help="打印当前状态后退出")
    ap.add_argument("--passive", action="store_true", help="仅 tail raw_events（不主动拉表）")
    ap.add_argument("--log", action="store_true", help="tail 长连接日志")
    ap.add_argument("--poll-interval", type=float, default=0, help="主动轮询间隔秒（默认读配置 poll_interval_seconds）")
    ap.add_argument("--from-start", action="store_true", help="从 raw_events 文件头开始读")
    args = ap.parse_args()

    from l3_node.tools.pmo_bitable_watch import _LONG_CONNECTION_LOG, _RAW_EVENTS_NDJSON

    if args.status:
        return cmd_status()
    if args.log:
        return _tail_log(_LONG_CONNECTION_LOG, from_start=args.from_start)
    if args.passive:
        return _passive_tail(_RAW_EVENTS_NDJSON, from_start=args.from_start)
    return _active_watch(poll_interval=args.poll_interval, from_start=args.from_start)


if __name__ == "__main__":
    raise SystemExit(main())
