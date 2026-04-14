#!/usr/bin/env python3
"""
Daily Nexus Commander — 一键晨间早报（联调 Native + SQLite + 可选 MCP）。

用法（在仓库根目录）:
  python scripts/run_daily_nexus.py
  python scripts/run_daily_nexus.py --skip-mcp    # 跳过「飞书任务队列」小节
  python scripts/run_daily_nexus.py --send-lark   # 强制尝试飞书推送（须已配置 notifier）

说明：飞书任务列表默认**直连**本仓库 `com.jachin.hr.recruitment` 插件内 `atom_lark_list_tasks`（与 MCP server 同源），
**不**经 MCP stdio，避免脚本结束时 MCP 客户端在 asyncio 关闭阶段触发 anyio cancel scope 报错。

详细日志协议：skills_repo/daily-nexus-commander/DAILY_NEXUS_LOGGING_PROTOCOL.md
默认日志目录：D:\\zzz\\jachin\\健康skill（可配置 DAILY_NEXUS_LOG_DIR 或 daily_nexus.yaml 的 log_dir）

依赖：项目根 .env 可选；PyYAML；与 L3 相同的 Python 环境。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

PROTOCOL_REL = Path("skills_repo/daily-nexus-commander/DAILY_NEXUS_LOGGING_PROTOCOL.md")
DEFAULT_LOG_DIR_WIN = Path(r"D:\zzz\jachin\健康skill")


def _setup_path() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        for p in (ROOT / ".env", Path.home() / ".jachin" / ".env"):
            if p.is_file():
                load_dotenv(p, encoding="utf-8")
                break
    except ImportError:
        pass


def _ensure_user_config() -> Path:
    """若 ~/.jachin 下无配置，从仓库默认 daily_nexus.yaml 复制。"""
    jachin = Path.home() / ".jachin"
    cfg_dir = jachin / "config" / "skills" / "com.jachin.daily_nexus_commander"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    dst = cfg_dir / "daily_nexus.yaml"
    if dst.is_file():
        return dst
    src = ROOT / "config" / "skills" / "com.jachin.daily_nexus_commander" / "daily_nexus.yaml"
    if not src.is_file():
        ex = ROOT / "config" / "skills" / "com.jachin.daily_nexus_commander" / "daily_nexus.yaml.example"
        if ex.is_file():
            src = ex
    if src.is_file():
        shutil.copy2(src, dst)
        print(f"[daily-nexus] 已初始化配置: {dst}", file=sys.stderr)
    return dst


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as e:
        raise SystemExit("需要 PyYAML：pip install pyyaml") from e
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    return data if isinstance(data, dict) else {}


def _mask_url(s: str, keep: int = 24) -> str:
    t = (s or "").strip()
    if len(t) <= keep:
        return t if not t else t[:3] + "****" + t[-2:] if len(t) > 8 else "****"
    return t[:keep] + "…[masked]"


def _resolve_log_dir(cfg: dict[str, Any]) -> Path:
    env = (os.environ.get("DAILY_NEXUS_LOG_DIR") or "").strip()
    if env:
        return Path(env).expanduser()
    ld = cfg.get("log_dir")
    if isinstance(ld, str) and ld.strip():
        return Path(ld.strip()).expanduser()
    if sys.platform == "win32":
        return DEFAULT_LOG_DIR_WIN
    return Path.home() / ".jachin" / "logs" / "daily_nexus"


def _logging_level_from_cfg(cfg: dict[str, Any]) -> int:
    lv = str(cfg.get("log_level") or "DEBUG").strip().upper()
    return getattr(logging, lv, logging.DEBUG)


def _install_file_logger(
    log_dir: Path,
    level: int,
    trace_id: str,
) -> tuple[logging.Logger, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:-3]
    log_path = log_dir / f"daily_nexus_run_{ts}.log"

    lg = logging.getLogger("daily_nexus")
    lg.handlers.clear()
    lg.setLevel(logging.DEBUG)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-5s | [%(name)s] [trace=%(trace_id)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    class _CtxFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.trace_id = trace_id  # type: ignore[attr-defined]
            return True

    fh.addFilter(_CtxFilter())
    fh.setFormatter(fmt)
    lg.addHandler(fh)
    lg.propagate = False
    return lg, log_path


def _copy_protocol_to_log_dir(log_dir: Path, log: logging.Logger) -> None:
    src = ROOT / PROTOCOL_REL
    if not src.is_file():
        log.warning("协议文档缺失（仓库内）: %s", src)
        return
    dst = log_dir / "DAILY_NEXUS_LOGGING_PROTOCOL.md"
    try:
        shutil.copy2(src, dst)
        log.info("已同步日志协议副本 -> %s", dst)
    except OSError as e:
        log.warning("复制协议文档失败: %s", e)


def _maybe_copy_latest(log_dir: Path, log_path: Path, cfg: dict[str, Any], log: logging.Logger) -> None:
    if cfg.get("log_copy_latest") not in (True, "true", "1", "yes"):
        return
    latest = log_dir / "daily_nexus_latest.log"
    try:
        shutil.copy2(log_path, latest)
        log.info("已写入 latest 副本: %s", latest)
    except OSError as e:
        log.warning("写入 daily_nexus_latest.log 失败: %s", e)


def _clip(s: str, max_len: int = 12000) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n… [truncated, total_len={len(s)}]"


def _section_machine(log: logging.Logger) -> str:
    log.info("SECTION begin: machine / sys:health_stats")
    t0 = time.perf_counter()
    from l3_node.primitives.tools.core_util_tools import run_health_stats

    try:
        r = run_health_stats()
        body = json.dumps(r, ensure_ascii=False, indent=2) if isinstance(r, dict) else str(r)
        log.debug("machine raw: %s", _clip(body, 8000))
    except Exception as e:
        log.exception("machine failed: %s", e)
        body = f"(失败: {e})"
    log.info("SECTION end: machine ms=%.2f", (time.perf_counter() - t0) * 1000)
    return f"## 机器脉搏\n\n```json\n{body}\n```\n"


def _section_weather(log: logging.Logger, city: str) -> str:
    log.info("SECTION begin: weather / util:get_weather_lite city=%r", city)
    t0 = time.perf_counter()
    from l3_node.primitives.tools.core_util_tools import run_get_weather_lite

    try:
        r = run_get_weather_lite(city=city or "上海")
        body = json.dumps(r, ensure_ascii=False, indent=2) if isinstance(r, dict) else str(r)
        log.debug("weather raw: %s", _clip(body, 8000))
    except Exception as e:
        log.exception("weather failed: %s", e)
        body = f"(失败: {e})"
    log.info("SECTION end: weather ms=%.2f", (time.perf_counter() - t0) * 1000)
    return f"## 今日天气\n\n```json\n{body}\n```\n"


def _section_sqlite(log: logging.Logger, cfg: dict[str, Any]) -> str:
    log.info("SECTION begin: sqlite read-only")
    t0 = time.perf_counter()
    db = Path.home() / ".jachin" / "workspace" / "my_life_data.db"
    log.info("sqlite db path=%s exists=%s", db, db.is_file())
    lines = ["## 个人数据（SQLite 只读）", ""]
    if not db.is_file():
        lines.append(f"*数据库不存在（预期路径 `{db}`），已跳过。*\n")
        log.warning("sqlite skipped: file missing")
        return "\n".join(lines)
    snippets = cfg.get("sqlite_snippets")
    if not isinstance(snippets, list) or not snippets:
        snippets = [
            {
                "name": "用户表一览",
                "sql": "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 30;",
            }
        ]
        log.info("sqlite using default snippets count=%d", len(snippets))
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except Exception as e:
        log.exception("sqlite connect failed: %s", e)
        lines.append(f"*打开数据库失败: {e}*\n")
        return "\n".join(lines)
    try:
        for item in snippets:
            if not isinstance(item, dict):
                log.debug("sqlite skip non-dict item: %r", item)
                continue
            name = str(item.get("name") or "query")
            sql = str(item.get("sql") or "").strip()
            log.info("sqlite snippet name=%r sql=%r", name, sql)
            if not sql:
                continue
            if not sql.strip().upper().startswith("SELECT"):
                lines.append(f"### {name}\n\n*已跳过非 SELECT 语句（安全策略）。*\n")
                log.warning("sqlite rejected non-SELECT name=%s", name)
                continue
            try:
                cur = conn.execute(sql)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
                log.info("sqlite result name=%s rows=%d cols=%s", name, len(rows), cols)
                lines.append(f"### {name}\n")
                lines.append("")
                if cols:
                    lines.append("| " + " | ".join(cols) + " |")
                    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
                    for row in rows[:50]:
                        lines.append(
                            "| "
                            + " | ".join(str(x) if x is not None else "" for x in row)
                            + " |"
                        )
                    if len(rows) > 50:
                        lines.append(f"\n*（仅显示前 50 行，共 {len(rows)} 行）*")
                else:
                    lines.append(f"*{rows!r}*")
                lines.append("")
            except Exception as e:
                log.exception("sqlite snippet failed name=%s: %s", name, e)
                lines.append(f"### {name}\n\n*执行失败: {e}*\n")
    finally:
        conn.close()
        log.info("SECTION end: sqlite ms=%.2f", (time.perf_counter() - t0) * 1000)
    return "\n".join(lines) + "\n"


def _section_background_tasks(log: logging.Logger) -> str:
    log.info("SECTION begin: background_task list_recent")
    t0 = time.perf_counter()
    from l3_node.primitives.agent_tasks.background_task_service import (
        check_background_task_status_sync,
    )

    inp = json.dumps({"list_recent": True}, ensure_ascii=False)
    log.debug("check_background_task input=%s", inp)
    try:
        body = check_background_task_status_sync(inp)
        log.debug("check_background_task output=%s", _clip(str(body), 8000))
    except Exception as e:
        log.exception("check_background_task failed: %s", e)
        body = f"(失败: {e})"
    log.info("SECTION end: background_task ms=%.2f", (time.perf_counter() - t0) * 1000)
    return f"## 后台任务（submit_background_task）\n\n```\n{body}\n```\n"


def _read_shell_registry() -> dict[str, Any]:
    p = Path.home() / ".jachin" / "workspace" / ".shell_jobs" / "registry.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _section_shell_jobs(log: logging.Logger) -> str:
    log.info("SECTION begin: shell_jobs registry")
    t0 = time.perf_counter()
    reg = _read_shell_registry()
    jobs = reg.get("jobs") if isinstance(reg.get("jobs"), dict) else {}
    log.info("shell_jobs registry jobs_count=%d path=%s", len(jobs), Path.home() / ".jachin/workspace/.shell_jobs/registry.json")
    lines = ["## Shell 后台（workspace/.shell_jobs）", ""]
    if not jobs:
        lines.append("*无登记记录。*\n")
        log.info("SECTION end: shell_jobs empty ms=%.2f", (time.perf_counter() - t0) * 1000)
        return "\n".join(lines)
    lines.append("| job_id | status | pid | exit | command |")
    lines.append("| --- | --- | --- | --- | --- |")
    for jid, rec in list(jobs.items())[-20:]:
        if not isinstance(rec, dict):
            continue
        cmd = str(rec.get("command") or "")[:80]
        lines.append(
            f"| `{jid}` | {rec.get('status')} | {rec.get('pid')} | {rec.get('exit_code', '')} | {cmd} |"
        )
    lines.append("")
    log.info("SECTION end: shell_jobs ms=%.2f", (time.perf_counter() - t0) * 1000)
    return "\n".join(lines)


def _section_lark_tasks(log: logging.Logger, skip: bool) -> str:
    log.info("SECTION begin: lark_tasks atom_lark_list_tasks skip=%s", skip)
    t0 = time.perf_counter()
    if skip:
        log.info("lark_tasks skipped by flag")
        log.info("SECTION end: lark_tasks skipped ms=%.2f", (time.perf_counter() - t0) * 1000)
        return "## 飞书任务队列\n\n*已 --skip-mcp，跳过本小节。*\n"
    plugin = ROOT / "skills_repo" / "plugin" / "com.jachin.hr.recruitment"
    tools_py = plugin / "tools" / "atom_lark_send_message.py"
    log.info("plugin_root=%s tools_exists=%s", plugin, tools_py.is_file())
    data_json = plugin / "data" / "lark_tasks.json"
    log.info("lark_tasks.json exists=%s path=%s", data_json.is_file(), data_json)
    if not tools_py.is_file():
        log.error("HR plugin tools missing: %s", tools_py)
        return (
            "## 飞书任务队列\n\n"
            f"*未找到 HR 插件文件 `{tools_py}`，无法直连读取任务列表。*\n"
        )
    pr = str(plugin.resolve())
    if pr not in sys.path:
        sys.path.insert(0, pr)
    try:
        from tools.atom_lark_send_message import atom_lark_list_tasks

        r = atom_lark_list_tasks()
        body = json.dumps(r, ensure_ascii=False, indent=2)
        log.debug("atom_lark_list_tasks result=%s", _clip(body, 8000))
    except Exception as e:
        log.exception("atom_lark_list_tasks failed: %s", e)
        body = json.dumps(
            {"success": False, "error": str(e), "traceback": traceback.format_exc()},
            ensure_ascii=False,
            indent=2,
        )
    note = (
        "*（与 `hr-atomic-tools` MCP 中 `atom_lark_list_tasks` 同源；数据文件见插件目录 `data/lark_tasks.json`。）*\n\n"
    )
    log.info("SECTION end: lark_tasks ms=%.2f", (time.perf_counter() - t0) * 1000)
    return f"## 飞书任务队列\n\n{note}```json\n{body}\n```\n"


def _run_reminders(log: logging.Logger, cfg: dict[str, Any]) -> list[str]:
    rems = cfg.get("reminders")
    if not isinstance(rems, list) or not rems:
        log.info("reminders: none configured")
        return []
    from l3_node.primitives.tools.core_util_tools import run_schedule_desktop_reminder

    notes: list[str] = []
    log.info("reminders: count=%d", len(rems))
    for i, r in enumerate(rems):
        if not isinstance(r, dict):
            log.warning("reminders[%d] skip non-dict", i)
            continue
        body = str(r.get("body") or r.get("message") or "").strip()
        if not body:
            continue
        title = str(r.get("title") or "Jachin").strip() or "Jachin"
        kwargs: dict[str, Any] = {"title": title, "body": body}
        if r.get("fire_at_iso"):
            kwargs["fire_at_iso"] = str(r["fire_at_iso"])
        elif r.get("delay_seconds") is not None:
            kwargs["delay_seconds"] = r.get("delay_seconds")
        elif r.get("fire_at_unix_ms") is not None:
            kwargs["fire_at_unix_ms"] = r.get("fire_at_unix_ms")
        else:
            notes.append(f"- 提醒{i+1}: 跳过（需 fire_at_iso / delay_seconds / fire_at_unix_ms 之一）")
            log.warning("reminder[%d] missing time field", i)
            continue
        log.info("reminder[%d] kwargs keys=%s title=%r", i, list(kwargs.keys()), title)
        try:
            res = run_schedule_desktop_reminder(**kwargs)
            ok = isinstance(res, dict) and res.get("ok") is True
            log.debug("reminder[%d] result=%s", i, _clip(json.dumps(res, ensure_ascii=False) if isinstance(res, dict) else str(res), 4000))
            notes.append(f"- 提醒「{title}」: {'已请求' if ok else res}")
        except Exception as e:
            log.exception("reminder[%d] failed: %s", i, e)
            notes.append(f"- 提醒失败: {e}")
    return notes


def _maybe_send_lark(
    log: logging.Logger,
    markdown: str,
    title: str,
    cfg: dict[str, Any],
    force: bool,
) -> str:
    ch = str(cfg.get("notify_channel") or "reply_only").strip().lower()
    wh = str(cfg.get("lark_webhook_url") or os.environ.get("BI_LARK_WEBHOOK_URL") or "").strip()
    log.info(
        "send_lark: notify_channel=%r force_send=%s webhook_configured=%s webhook_preview=%s",
        ch,
        force,
        bool(wh and not str(wh).startswith("${")),
        _mask_url(wh) if wh else "(empty)",
    )
    if ch != "lark" and not force:
        log.info("send_lark: skipped (reply_only and no --send-lark)")
        return ""
    try:
        from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown
    except Exception as e:
        log.exception("send_lark import failed: %s", e)
        return f"飞书推送跳过（导入失败: {e}）"
    try:
        log.info("send_lark: invoking send_lark_markdown markdown_len=%d title=%r", len(markdown), title)
        r = send_lark_markdown(
            webhook_url=wh,
            markdown_content=markdown,
            title=title,
            chat_id=None,
        )
        log.info("send_lark: result=%s", _clip(json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else str(r), 4000))
        return json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else str(r)
    except Exception as e:
        log.exception("send_lark failed: %s", e)
        return f"飞书推送失败: {e}"


def main() -> int:
    _setup_path()
    _load_dotenv()

    ap = argparse.ArgumentParser(description="Daily Nexus Commander 一键早报")
    ap.add_argument(
        "--skip-mcp",
        action="store_true",
        help="跳过「飞书任务队列」小节（不导入 HR 插件）",
    )
    ap.add_argument(
        "--send-lark",
        action="store_true",
        help="尝试飞书推送（依赖 atom_lark_notifier 配置或 lark_webhook_url）",
    )
    ap.add_argument("--no-save", action="store_true", help="不写 workspace/daily_nexus 文件")
    ap.add_argument("--no-file-log", action="store_true", help="不写文件日志（仅 stdout 早报）")
    ap.add_argument("--log-dir", type=str, default="", help="覆盖日志目录（默认见协议）")
    args = ap.parse_args()

    cfg_path = _ensure_user_config()
    cfg = _load_yaml(cfg_path)
    if args.log_dir.strip():
        cfg = {**cfg, "log_dir": args.log_dir.strip()}

    trace_id = uuid.uuid4().hex[:12]
    log_dir = _resolve_log_dir(cfg)
    log: logging.Logger = logging.getLogger("daily_nexus")
    log_path: Path | None = None

    if not args.no_file_log:
        lvl = _logging_level_from_cfg(cfg)
        log, log_path = _install_file_logger(log_dir, lvl, trace_id)
        log.info("=== Daily Nexus run start ===")
        log.info("trace_id=%s", trace_id)
        log.info("argv=%s", sys.argv)
        log.info("pid=%s python=%s platform=%s", os.getpid(), sys.version, sys.platform)
        log.info("ROOT=%s cwd=%s", ROOT, os.getcwd())
        log.info("config_path=%s", cfg_path.resolve())
        log.info(
            "config_keys: weather_city=%r notify_channel=%r log_dir_resolved=%s",
            cfg.get("weather_city"),
            cfg.get("notify_channel"),
            log_dir,
        )
        _copy_protocol_to_log_dir(log_dir, log)
    else:
        log.handlers.clear()
        log.addHandler(logging.NullHandler())
        log.setLevel(logging.DEBUG)
        log.propagate = False

    city = str(cfg.get("weather_city") or "上海")
    t_run0 = time.perf_counter()

    today = datetime.now().strftime("%Y-%m-%d")
    title_doc = f"Daily Nexus — {today}"
    parts = [
        f"# {title_doc}\n",
        f"*配置: `{cfg_path}`*\n",
    ]

    if not args.no_file_log and log_path:
        log.debug("report header built title_doc=%r", title_doc)

    parts.append(_section_machine(log))
    parts.append(_section_weather(log, city))
    parts.append(_section_sqlite(log, cfg))
    parts.append(_section_background_tasks(log))
    parts.append(_section_shell_jobs(log))
    parts.append(_section_lark_tasks(log, args.skip_mcp))

    rem_notes = _run_reminders(log, cfg)
    if rem_notes:
        parts.append("## 桌面提醒\n\n" + "\n".join(rem_notes) + "\n")

    report = "\n".join(parts)

    out_dir = Path.home() / ".jachin" / "workspace" / "daily_nexus"
    out_md = out_dir / f"{today}.md"
    if not args.no_save:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_md.write_text(report, encoding="utf-8")
        msg = f"[daily-nexus] 已写入: {out_md}"
        print(msg, file=sys.stderr)
        if not args.no_file_log:
            log.info("report saved path=%s bytes=%d", out_md, len(report.encode("utf-8")))
    else:
        if not args.no_file_log:
            log.info("report save skipped (--no-save) markdown_len=%d", len(report))

    send_result = _maybe_send_lark(log, report, title_doc, cfg, force=args.send_lark)
    if send_result:
        print(f"[daily-nexus] 飞书: {send_result}", file=sys.stderr)

    total_ms = (time.perf_counter() - t_run0) * 1000
    if not args.no_file_log and log_path:
        log.info("=== Daily Nexus run end === total_ms=%.2f exit=0", total_ms)
        _maybe_copy_latest(log_dir, log_path, cfg, log)
        print(f"[daily-nexus] 详细日志: {log_path}", file=sys.stderr)

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
