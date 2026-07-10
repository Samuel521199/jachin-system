"""
生物钟（cron_thinker）— 发版维护公告 → 次日北京时间 10:00 统合冒烟

- 监听入口：

  1. **飞书邮箱**：应用具备 ``mail:user_mailbox.message:readonly``（及主题/正文只读权限）时，使用
     ``tenant_access_token`` 轮询指定用户邮箱最新若干封邮件；**标题**（或主题+正文前部）须匹配「生产环境…维护…公告」宽松样式（含历史精确子串 ``【生产环境发版维护公告】``），
     走与线上一致的日期提取 + 定时登记（**不**依赖 Webhook/HTTP 收信）。
  2. 飞书/Lark **群消息**（``l3_node.im_channels.dispatcher``）。
  3. 可选 ``POST /api/v1/cron-thinker/ingest-release-announcement``（手工/自动化转发）。

- **不使用 LLM**：固定标题宽松匹配 + 正则提取维护日。**「【维护时间】」行取首段 Y-M-D 作为维护日**；若同行随后还有第二段日期且**早于**首段，记审计并可选发飞书告警（笔误），**仍以首段日期**登记冒烟。**冒烟执行时刻**仅由控制台 BIOS 决定。
- 调度：进程内 ``BackgroundScheduler`` + ``DateTrigger``（冒烟）+ ``IntervalTrigger``（邮箱轮询）。

环境变量：

- ``JACHIN_CRON_THINKER_RELEASE_SMOKE``：默认开启；设为 ``0`` / ``false`` / ``off`` 关闭整套逻辑。
- ``JACHIN_CRON_THINKER_MAIL_POLL``：默认 ``1``；设为 ``0`` / ``false`` / ``off`` 关闭邮箱轮询（仍保留群消息与 HTTP）。
- ``JACHIN_CRON_THINKER_MAILBOX``：轮询的邮箱，默认 ``vivian@herontech.net``。
- ``JACHIN_CRON_THINKER_MAIL_POLL_INTERVAL_SEC``：轮询间隔秒数，默认 ``1800``（30 分钟），最小 ``30``（秒；过短易与到点冒烟等任务抢资源，可酌情调大）。
- ``JACHIN_CRON_THINKER_MAIL_PAGE_SIZE``：每次列出邮件条数，默认 ``3``（接口允许 1～20）。
- ``JACHIN_CRON_THINKER_MAIL_HTTP_TIMEOUT_SEC``：邮箱 Open API HTTP 读超时秒数，默认 ``60``（最小 ``10``、最大 ``300``）。
- ``JACHIN_CRON_THINKER_PARSE_ALERT_CHAT_ID``：可选；当解析到「【维护时间】」结束日期早于开始日期等笔误时，向该飞书群 ``oc_...`` 发一条文本告警（需 IM 发送权限）。
- 应用凭证：与 IM 一致， ``LARK_APP_ID`` / ``LARK_APP_SECRET``（或 ``im_channels.yaml``）；须在中国大陆域生效时使用 ``LARK_USE_FEISHU=1`` 等以指向 ``open.feishu.cn``。
- ``JACHIN_RELEASE_ANNOUNCEMENT_LARK_CHAT_IDS``：可选；仅对飞书 **群消息** 入口生效。
- ``JACHIN_CRON_THINKER_INGEST_TOKEN``：可选；HTTP 投递鉴权。
- ``JACHIN_CRON_THINKER_AUDIT_LOG``：可选；生物钟专用审计 NDJSON 日志路径（默认可见代码内 ``jachin_debug/健康skill``）。
- ``JACHIN_CRON_THINKER_AUDIT_DISABLE``：设为 ``1`` / ``true`` 关闭审计写入。
- ``JACHIN_CRON_THINKER_AUDIT_SMOKE_MAX_LINES``：审计中记录冒烟子进程 stdout 的最大行数，默认 ``4000``；``0`` 表示不限制。
- ``K11_UNIFIED_SMOKE_TARGET_URL``：可选；生物钟到点统合冒烟传给 ``--target-url``（默认 ``https://www.kalaroko.com/``），与控制台「启动统合冒烟」及 ``clients/desktop`` 默认一致。
- ``K11_UNIFIED_SMOKE_CDP_HTTP``：可选；若设置则传入 ``--cdp-http``，与 SSE ``cdp_http`` 查询参数对齐。
- ``K11_SCHEDULED_SMOKE_NO_LARK``：与每日定时批跑相同；设为 ``1`` / ``true`` 时统合子进程加 ``--no-lark-report``（另保留 ``JACHIN_CRON_THINKER_SMOKE_NO_LARK``）。
- 子进程冒烟与现网一致，见下文。

持久化：冒烟任务 ``~/.jachin/data/cron_thinker_release_smoke_jobs.json``；
已处理邮件 id ``~/.jachin/data/cron_thinker_mail_seen.json``（防重复解析）。

到点执行统合冒烟时，子进程标准输出经 ``k11_scheduled_log_emit_from_thread`` 写入与「每日定时」相同的
``/api/v1/k11-unified-smoke/schedule/log-stream``，控制台 **MIND STREAM** 可见（L3 ``http_server`` 启动时已
``register_k11_schedule_log_loop``）。
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# --- 业务规则（与产品约定一致，勿改语义除非需求变更） ---

RELEASE_TITLE_NEEDLE = "【生产环境发版维护公告】"
# 人工标题常见漏字/换序，如「【生产环境维护公告】」：须在同一对「【…】」内含 生产环境 → 维护 → 公告
_RELEASE_TITLE_FLEX_RE = re.compile(
    r"【\s*生产环境\s*.*?维护\s*.*?公告\s*】",
    re.DOTALL | re.IGNORECASE,
)
MAINTENANCE_DATE_RE = re.compile(
    r"【维护时间】\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2})",
)

TZ_BEIJING = ZoneInfo("Asia/Shanghai")


def release_title_present(text: str | None) -> bool:
    """是否含发版维护公告标题：历史精确子串或宽松「【…生产环境…维护…公告…】」。"""
    t = text or ""
    if RELEASE_TITLE_NEEDLE in t:
        return True
    return bool(_RELEASE_TITLE_FLEX_RE.search(t))


def _parse_iso_date_fragment(fragment: str) -> dt.date | None:
    normalized = (fragment or "").replace("/", "-").strip()
    if not normalized:
        return None
    try:
        y_s, mo_s, d_s = normalized.split("-", 2)
        return dt.date(int(y_s), int(mo_s), int(d_s))
    except ValueError:
        return None


def _maintenance_time_line_segment(text: str) -> str:
    """「【维护时间】」起至行尾（含换行）。"""
    m = re.search(r"【维护时间】", text or "")
    if not m:
        return ""
    rest = (text or "")[m.start() :]
    nl = rest.find("\n")
    return rest if nl == -1 else rest[: nl]


def _audit_maintenance_window_order(primary: dt.date, segment: str) -> None:
    """
    若【维护时间】行内出现第二段 Y-M-D 且早于首段，视为常见笔误：审计 + 可选飞书告警。
    不修改 ``primary``（仍以 ``MAINTENANCE_DATE_RE`` 首段日期为维护日）。
    """
    dates_raw = re.findall(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", segment or "")
    if len(dates_raw) < 2:
        return
    d_second = _parse_iso_date_fragment(dates_raw[1])
    if d_second is None or d_second >= primary:
        return
    _audit_log(
        "maintenance_window_end_before_start",
        primary_date=primary.isoformat(),
        second_date_raw=dates_raw[1],
        second_date_parsed=d_second.isoformat(),
        segment_preview=_audit_trunc(segment.strip(), 500),
    )
    logger.warning(
        "[cron_thinker] 【维护时间】行第二处日期 %s 早于维护日 %s，疑为笔误；仍以首日期登记冒烟",
        d_second.isoformat(),
        primary.isoformat(),
    )
    _try_notify_maintenance_parse_anomaly(primary, d_second, segment)


def _try_notify_maintenance_parse_anomaly(
    start: dt.date,
    end_bad: dt.date,
    segment: str,
) -> None:
    cid = (os.environ.get("JACHIN_CRON_THINKER_PARSE_ALERT_CHAT_ID") or "").strip()
    if not cid:
        return
    try:
        from l3_node.channels.lark.im import send_text

        msg = (
            "【生物钟】公告【维护时间】解析告警：行内结束日期早于开始日期（疑笔误）。\n"
            f"维护日（沿用首段日期）：{start.isoformat()}\n"
            f"同行第二段日期：{end_bad.isoformat()}\n"
            f"摘录：{_audit_trunc(segment.strip(), 400)}"
        )
        r = send_text(cid, msg[:3500])
        if r.get("status") != "success":
            logger.warning("[cron_thinker] 解析告警飞书未送达: %s", r)
    except Exception as e:
        logger.warning("[cron_thinker] 解析告警飞书异常: %s", e)


_STATE_LOCK = threading.RLock()
_SCHEDULER = None  # type: ignore[var-annotated]
_SCHEDULER_STARTED = False

_PERSIST_PATH = Path.home() / ".jachin" / "data" / "cron_thinker_release_smoke_jobs.json"
_MAIL_SEEN_PATH = Path.home() / ".jachin" / "data" / "cron_thinker_mail_seen.json"
_BIOS_SETTINGS_PATH = Path.home() / ".jachin" / "data" / "cron_thinker_bios_settings.json"
_LOG_PATH = Path.home() / ".jachin" / "logs" / "cron_thinker_k11_smoke.log"

# --- 生物钟专用审计日志（NDJSON，一行一条 JSON，便于 grep / jq）---
# 默认：~/.jachin/jachin_debug/健康skill/cron_thinker_bios_clock_audit.log
# 覆盖：JACHIN_CRON_THINKER_AUDIT_LOG=/path/to/file.log
# 关闭：JACHIN_CRON_THINKER_AUDIT_DISABLE=1
_AUDIT_LOCK = threading.Lock()
_AUDIT_MAX_STR = 16000
_AUDIT_RAW_HEAD = 6000


def _audit_log_path() -> Path:
    custom = (os.environ.get("JACHIN_CRON_THINKER_AUDIT_LOG") or "").strip()
    if custom:
        return Path(custom).expanduser()
    return (
        Path.home()
        / ".jachin"
        / "jachin_debug"
        / "健康skill"
        / "cron_thinker_bios_clock_audit.log"
    )


def _audit_disabled() -> bool:
    return (os.environ.get("JACHIN_CRON_THINKER_AUDIT_DISABLE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _audit_trunc(text: str | None, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated total_len={len(text)}]"


def _audit_log(event: str, **fields: Any) -> None:
    """写入专用审计日志；任意异常静默，不干扰主流程。"""
    if _audit_disabled():
        return
    try:
        rec: dict[str, Any] = {
            "ts_beijing": dt.datetime.now(tz=TZ_BEIJING).isoformat(),
            "event": event,
            "pid": os.getpid(),
            "thread": threading.current_thread().name,
        }
        for k, v in fields.items():
            if isinstance(v, str) and len(v) > _AUDIT_MAX_STR:
                rec[k] = _audit_trunc(v, _AUDIT_MAX_STR)
            else:
                rec[k] = v
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        path = _audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass


def _audit_env_snapshot() -> dict[str, Any]:
    return {
        "JACHIN_CRON_THINKER_RELEASE_SMOKE": (os.environ.get("JACHIN_CRON_THINKER_RELEASE_SMOKE") or "(unset)"),
        "JACHIN_CRON_THINKER_MAIL_POLL": (os.environ.get("JACHIN_CRON_THINKER_MAIL_POLL") or "(unset)"),
        "JACHIN_CRON_THINKER_MAILBOX": _mail_mailbox(),
        "JACHIN_CRON_THINKER_MAIL_POLL_INTERVAL_SEC": _mail_poll_interval_sec(),
        "JACHIN_CRON_THINKER_MAIL_PAGE_SIZE": _mail_poll_page_size(),
        "JACHIN_CRON_THINKER_MAIL_FOLDER_ID": _mail_folder_id(),
        "mail_folder_id_effective": _mail_folder_id_effective(),
        "JACHIN_CRON_THINKER_MAIL_HTTP_TIMEOUT_SEC": _mail_lark_http_timeout_sec(),
        "JACHIN_RELEASE_ANNOUNCEMENT_LARK_CHAT_IDS": (
            _audit_trunc(os.environ.get("JACHIN_RELEASE_ANNOUNCEMENT_LARK_CHAT_IDS") or "", 500)
        ),
        "JACHIN_CRON_THINKER_PARSE_ALERT_CHAT_ID": _audit_trunc(
            os.environ.get("JACHIN_CRON_THINKER_PARSE_ALERT_CHAT_ID") or "(unset)", 96
        ),
        "audit_log_path": str(_audit_log_path()),
        "audit_smoke_max_lines": _audit_smoke_max_lines(),
    }


def _audit_token_hint(token: str | None) -> dict[str, Any]:
    t = (token or "").strip()
    if not t:
        return {"present": False}
    suf = t[-8:] if len(t) > 8 else "*" * min(6, len(t))
    return {"present": True, "len": len(t), "suffix8": suf}


def _audit_mail_list_query_path(mailbox: str, page_size: int, folder_id: str) -> str:
    enc_box = quote(mailbox, safe="")
    fid = (folder_id or "").strip() or "INBOX"
    q = [f"page_size={page_size}", f"folder_id={quote(fid, safe='')}"]
    return f"/mail/v1/user_mailboxes/{enc_box}/messages?{'&'.join(q)}"


def _audit_smoke_max_lines() -> int:
    try:
        n = int((os.environ.get("JACHIN_CRON_THINKER_AUDIT_SMOKE_MAX_LINES") or "4000").strip())
    except ValueError:
        n = 4000
    if n <= 0:
        return 0
    return min(n, 100_000)

_BIOS_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "day_offset": 1,
    "hour_beijing": 10,
    "minute_beijing": 0,
}

_JOB_MAIL_POLL = "cron_thinker_mail_poll"
_DEFAULT_MAIL_POLL_INTERVAL_SEC = 1800  # 30 分钟；可用 JACHIN_CRON_THINKER_MAIL_POLL_INTERVAL_SEC 覆盖
_MAIL_SEEN_CAP = 500
_mail_poll_last_error: str | None = None
_mail_poll_last_ok_ts: str | None = None


def _release_smoke_enabled() -> bool:
    v = (os.environ.get("JACHIN_CRON_THINKER_RELEASE_SMOKE") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _mail_poll_enabled() -> bool:
    v = (os.environ.get("JACHIN_CRON_THINKER_MAIL_POLL") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _mail_mailbox() -> str:
    return (
        os.environ.get("JACHIN_CRON_THINKER_MAILBOX") or "vivian@herontech.net"
    ).strip()


def _mail_poll_interval_sec() -> int:
    raw = (os.environ.get("JACHIN_CRON_THINKER_MAIL_POLL_INTERVAL_SEC") or "").strip()
    if not raw:
        n = _DEFAULT_MAIL_POLL_INTERVAL_SEC
    else:
        try:
            n = int(raw)
        except ValueError:
            n = _DEFAULT_MAIL_POLL_INTERVAL_SEC
    return max(30, n)


def _mail_poll_page_size() -> int:
    try:
        n = int((os.environ.get("JACHIN_CRON_THINKER_MAIL_PAGE_SIZE") or "3").strip())
    except ValueError:
        n = 3
    return max(1, min(20, n))


def _mail_folder_id() -> str | None:
    v = (os.environ.get("JACHIN_CRON_THINKER_MAIL_FOLDER_ID") or "").strip()
    return v or None


def _mail_folder_id_effective() -> str:
    """
    ``GET .../messages`` 在若干租户/域名下要求携带 ``folder_id`` 或 ``label_id``。

    未设置 ``JACHIN_CRON_THINKER_MAIL_FOLDER_ID`` 时使用 ``INBOX``，与「收件箱/收到的信」一致，
    满足发版公告轮询场景。
    """
    return _mail_folder_id() or "INBOX"


def load_bios_settings() -> dict[str, Any]:
    """
    控制台可编辑的「发版公告生物钟」：是否开启、相对维护日的第几天、北京时间时刻。

    持久化：``~/.jachin/data/cron_thinker_bios_settings.json``；缺省为「开启 + 次日 10:00」。
    """
    data = dict(_BIOS_DEFAULTS)
    if _BIOS_SETTINGS_PATH.is_file():
        try:
            raw = json.loads(_BIOS_SETTINGS_PATH.read_text(encoding="utf-8").strip() or "{}")
            if isinstance(raw, dict):
                for k in _BIOS_DEFAULTS:
                    if k in raw:
                        data[k] = raw[k]
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[cron_thinker] BIOS 设置读取失败: %s", e)
    if isinstance(data.get("enabled"), str):
        data["enabled"] = data["enabled"].strip().lower() in ("1", "true", "yes", "on")
    else:
        data["enabled"] = bool(data.get("enabled", True))
    try:
        data["day_offset"] = max(0, min(14, int(data["day_offset"])))
    except (TypeError, ValueError):
        data["day_offset"] = int(_BIOS_DEFAULTS["day_offset"])
    for key, lo, hi, default in (
        ("hour_beijing", 0, 23, _BIOS_DEFAULTS["hour_beijing"]),
        ("minute_beijing", 0, 59, _BIOS_DEFAULTS["minute_beijing"]),
    ):
        try:
            data[key] = max(lo, min(hi, int(data[key])))
        except (TypeError, ValueError):
            data[key] = int(default)
    return data


def save_bios_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """合并写入 BIOS 设置；校验后落盘。"""
    cur = load_bios_settings()
    if not isinstance(updates, dict):
        return cur
    if "enabled" in updates:
        v = updates["enabled"]
        if isinstance(v, bool):
            cur["enabled"] = v
        else:
            cur["enabled"] = str(v).strip().lower() in ("1", "true", "yes", "on")
    if "day_offset" in updates:
        try:
            cur["day_offset"] = max(0, min(14, int(updates["day_offset"])))
        except (TypeError, ValueError):
            pass
    if "hour_beijing" in updates:
        try:
            cur["hour_beijing"] = max(0, min(23, int(updates["hour_beijing"])))
        except (TypeError, ValueError):
            pass
    if "minute_beijing" in updates:
        try:
            cur["minute_beijing"] = max(0, min(59, int(updates["minute_beijing"])))
        except (TypeError, ValueError):
            pass
    try:
        _BIOS_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BIOS_SETTINGS_PATH.write_text(
            json.dumps(cur, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[cron_thinker] BIOS 设置已保存 %s", cur)
        _audit_log(
            "bios_settings_saved",
            merged_settings=cur,
            update_keys=sorted(str(k) for k in updates.keys()),
            path=str(_BIOS_SETTINGS_PATH),
        )
    except OSError as e:
        logger.warning("[cron_thinker] BIOS 设置写入失败: %s", e)
        _audit_log("bios_settings_save_failed", error=str(e), path=str(_BIOS_SETTINGS_PATH))
    return cur


def _bios_console_enabled() -> bool:
    return bool(load_bios_settings().get("enabled", True))


def apply_bios_runtime() -> dict[str, Any]:
    """保存设置后调用：按当前开关重挂邮箱轮询等（需环境未全局关闭发版冒烟）。"""
    if not _release_smoke_enabled():
        out = {"ok": False, "reason": "cron_thinker_release_smoke_disabled"}
        _audit_log("bios_apply_runtime", **out)
        return out
    sched = _get_scheduler()
    _ensure_mail_poll_job(sched)
    if _release_smoke_enabled() and _mail_poll_enabled() and _bios_console_enabled():
        _mail_poll_schedule_immediate(reason="bios_apply_runtime")
    out = {"ok": True, "settings": load_bios_settings()}
    _audit_log(
        "bios_apply_runtime",
        **out,
        env=_audit_env_snapshot(),
        scheduler_started=_SCHEDULER_STARTED,
    )
    return out


def _allowed_lark_chat_ids() -> frozenset[str] | None:
    raw = (os.environ.get("JACHIN_RELEASE_ANNOUNCEMENT_LARK_CHAT_IDS") or "").strip()
    if not raw:
        return None
    parts = {p.strip() for p in raw.split(",") if p.strip()}
    return frozenset(parts) if parts else None


def parse_release_maintenance_date(raw: str) -> dt.date | None:
    """
    从公告全文中解析「维护日」。

    - 全文须通过 :func:`release_title_present`（精确标题子串或宽松【生产环境…维护…公告】）；
    - 须匹配 ``【维护时间】`` 后的第一段 ``YYYY-M-D`` 样式日期；
    - 若同行还有第二段日期且早于首段，记审计并可选飞书告警，**仍以首段日期**为维护日。
    """
    text = raw or ""
    if not release_title_present(text):
        return None
    m = MAINTENANCE_DATE_RE.search(text)
    if not m:
        return None
    primary = _parse_iso_date_fragment(m.group(1))
    if primary is None:
        return None
    seg = _maintenance_time_line_segment(text)
    if seg.strip():
        _audit_maintenance_window_order(primary, seg)
    return primary


def compute_smoke_run_at_beijing(maintenance_day: dt.date) -> dt.datetime:
    """按控制台 BIOS 设置：维护日后第 ``day_offset`` 天、北京时间 ``hour_beijing:minute_beijing``。"""
    st = load_bios_settings()
    offset = int(st["day_offset"])
    h, m = int(st["hour_beijing"]), int(st["minute_beijing"])
    target_day = maintenance_day + dt.timedelta(days=offset)
    return dt.datetime.combine(target_day, dt.time(h, m), tzinfo=TZ_BEIJING)


def job_id_for_maintenance_day(maintenance_day: dt.date) -> str:
    return f"smoke_test_{maintenance_day.isoformat()}"


def _b64url_to_str(raw: str | None) -> str:
    if not raw or not str(raw).strip():
        return ""
    s = str(raw).strip()
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad
    try:
        return base64.urlsafe_b64decode(s.encode("ascii")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _load_mail_seen_ids() -> list[str]:
    if not _MAIL_SEEN_PATH.is_file():
        return []
    try:
        data = json.loads(_MAIL_SEEN_PATH.read_text(encoding="utf-8").strip() or "{}")
        ids = data.get("message_ids")
        if isinstance(ids, list):
            return [str(i) for i in ids if i]
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[cron_thinker/mail] 读取已处理邮件 id 失败: %s", e)
    return []


def _mail_mark_seen(message_id: str) -> None:
    mid = (message_id or "").strip()
    if not mid:
        return
    with _STATE_LOCK:
        ids = _load_mail_seen_ids()
        if mid in ids:
            return
        ids.insert(0, mid)
        ids = ids[:_MAIL_SEEN_CAP]
        try:
            _MAIL_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            _MAIL_SEEN_PATH.write_text(
                json.dumps({"message_ids": ids}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("[cron_thinker/mail] 写入已处理 id 失败: %s", e)


def _lark_http_json_get(url: str, token: str, timeout: float = 30.0) -> dict[str, Any]:
    try:
        import requests
    except ImportError as e:
        raise RuntimeError("请安装 requests 以使用飞书邮箱 API") from e
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"飞书 API 非 JSON 响应 http={resp.status_code}") from None
    if not isinstance(data, dict):
        raise RuntimeError("飞书 API 响应格式异常")
    return data


def _lark_feishu_errcode(data: dict[str, Any]) -> int:
    """
    飞书/Lark Open API 业务 ``code``：``0`` 表示成功。

    不得写成 ``int(data.get(\"code\") or -1)``：在 Python 中 ``0 or -1`` 为 ``-1``，会把成功误判为失败。
    """
    c = data.get("code")
    if c is None:
        return -1
    try:
        return int(c)
    except (TypeError, ValueError):
        return -1


def _mail_lark_http_timeout_sec() -> float:
    try:
        n = float((os.environ.get("JACHIN_CRON_THINKER_MAIL_HTTP_TIMEOUT_SEC") or "60").strip())
    except ValueError:
        n = 60.0
    return float(max(10.0, min(300.0, n)))


def _mail_fetch_token_and_base() -> tuple[str, str]:
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token

    token = get_tenant_access_token()
    base = get_lark_api_base().rstrip("/")
    return token, base


def _mail_list_message_ids(
    *,
    token: str,
    api_base: str,
    mailbox: str,
    page_size: int,
    folder_id: str,
) -> list[str]:
    enc_box = quote(mailbox, safe="")
    fid = (folder_id or "").strip() or "INBOX"
    q = [
        f"page_size={page_size}",
        f"folder_id={quote(fid, safe='')}",
    ]
    url = f"{api_base}/mail/v1/user_mailboxes/{enc_box}/messages?{'&'.join(q)}"
    data = _lark_http_json_get(url, token, timeout=_mail_lark_http_timeout_sec())
    if _lark_feishu_errcode(data) != 0:
        raise RuntimeError(f"列出邮件失败: {data.get('msg')!s} raw={data!r}")
    inner = data.get("data") or {}
    items = inner.get("items")
    if not isinstance(items, list):
        return []
    return [str(i) for i in items if i]


def _mail_get_message_detail(
    *,
    token: str,
    api_base: str,
    mailbox: str,
    message_id: str,
) -> tuple[str, str]:
    enc_box = quote(mailbox, safe="")
    enc_mid = quote(message_id, safe="")
    url = (
        f"{api_base}/mail/v1/user_mailboxes/{enc_box}/messages/{enc_mid}"
        "?format=plain_text_full"
    )
    data = _lark_http_json_get(url, token, timeout=_mail_lark_http_timeout_sec())
    if _lark_feishu_errcode(data) != 0:
        raise RuntimeError(f"获取邮件详情失败: {data.get('msg')!s} mid={message_id[:16]}…")
    inner = data.get("data") or {}
    msg = inner.get("message")
    if not isinstance(msg, dict):
        return "", ""
    subj = str(msg.get("subject") or "")
    body = _b64url_to_str(msg.get("body_plain_text"))
    if not body.strip():
        body = _b64url_to_str(msg.get("body_preview"))
    return subj, body


def mail_poll_tick_once() -> dict[str, Any]:
    """
    执行一次邮箱轮询（供 APScheduler 或单测调用）。

    :return: 摘要 dict，含 ``ok``、``checked``、``matched`` 等。
    """
    global _mail_poll_last_error, _mail_poll_last_ok_ts

    summary: dict[str, Any] = {"ok": True, "mailbox": "", "checked": 0, "matched": 0, "errors": []}
    tick_id = f"{os.getpid()}_{time.time():.6f}"
    if not _release_smoke_enabled():
        summary["skipped"] = "release_smoke_disabled"
        _audit_log("mail_poll_tick_skipped", tick_id=tick_id, reason=summary["skipped"])
        return summary
    if not _mail_poll_enabled():
        summary["skipped"] = "mail_poll_disabled"
        _audit_log("mail_poll_tick_skipped", tick_id=tick_id, reason=summary["skipped"])
        return summary
    if not _bios_console_enabled():
        summary["skipped"] = "bios_disabled"
        _audit_log("mail_poll_tick_skipped", tick_id=tick_id, reason=summary["skipped"])
        return summary

    mailbox = _mail_mailbox()
    summary["mailbox"] = mailbox
    page_size = _mail_poll_page_size()
    folder_id = _mail_folder_id_effective()

    _audit_log(
        "mail_poll_tick_start",
        tick_id=tick_id,
        env=_audit_env_snapshot(),
        bios=load_bios_settings(),
        seen_ids_count=len(_load_mail_seen_ids()),
        list_query_path=_audit_mail_list_query_path(mailbox, page_size, folder_id),
    )

    try:
        token, api_base = _mail_fetch_token_and_base()
    except Exception as e:
        _mail_poll_last_error = str(e)
        logger.warning("[cron_thinker/mail] 获取 token 失败: %s", e)
        summary["ok"] = False
        summary["errors"].append(str(e))
        _audit_log(
            "mail_poll_token_failed",
            tick_id=tick_id,
            error=str(e),
            exc_type=type(e).__name__,
            api_base="(unavailable)",
        )
        return summary

    _audit_log(
        "mail_poll_token_ok",
        tick_id=tick_id,
        api_base=api_base,
        token=_audit_token_hint(token),
    )

    try:
        ids = _mail_list_message_ids(
            token=token,
            api_base=api_base,
            mailbox=mailbox,
            page_size=page_size,
            folder_id=folder_id,
        )
    except Exception as e:
        _mail_poll_last_error = str(e)
        logger.warning("[cron_thinker/mail] 列出邮件失败: %s", e)
        summary["ok"] = False
        summary["errors"].append(str(e))
        _audit_log(
            "mail_poll_list_failed",
            tick_id=tick_id,
            error=str(e),
            exc_type=type(e).__name__,
            query_path=_audit_mail_list_query_path(mailbox, page_size, folder_id),
        )
        return summary

    _audit_log(
        "mail_poll_list_ok",
        tick_id=tick_id,
        message_ids=ids,
        ids_len=len(ids),
        page_size=page_size,
    )

    seen_local = set(_load_mail_seen_ids())
    for mid in ids[:page_size]:
        if mid in seen_local:
            _audit_log("mail_poll_message_skip_already_seen", tick_id=tick_id, message_id=mid)
            continue
        summary["checked"] += 1
        try:
            subject, body = _mail_get_message_detail(
                token=token,
                api_base=api_base,
                mailbox=mailbox,
                message_id=mid,
            )
        except Exception as e:
            logger.warning("[cron_thinker/mail] 读取邮件 %s… 失败: %s", mid[:12], e)
            summary["errors"].append(f"{mid[:12]}:{e}")
            _audit_log(
                "mail_poll_message_detail_failed",
                tick_id=tick_id,
                message_id=mid,
                error=str(e),
                exc_type=type(e).__name__,
            )
            continue

        head_for_title = f"{subject or ''}\n{(body or '')[:12000]}"
        title_hit = release_title_present(head_for_title)
        _audit_log(
            "mail_poll_message_detail_ok",
            tick_id=tick_id,
            message_id=mid,
            subject=_audit_trunc(subject or "", 2000),
            subject_len=len(subject or ""),
            subject_has_needle=title_hit,
            body_char_len=len(body or ""),
            body_head=_audit_trunc(body or "", _AUDIT_RAW_HEAD),
        )

        if not title_hit:
            _mail_mark_seen(mid)
            seen_local.add(mid)
            _audit_log(
                "mail_poll_message_marked_seen_title_miss",
                tick_id=tick_id,
                message_id=mid,
                subject_preview=_audit_trunc(subject or "", 400),
            )
            continue

        raw = f"{subject}\n{body}"
        _audit_log(
            "mail_poll_title_hit_before_feed",
            tick_id=tick_id,
            message_id=mid,
            raw_head=_audit_trunc(raw, _AUDIT_RAW_HEAD),
            raw_total_len=len(raw),
        )
        result = feed_release_announcement_text(raw, source="mail")
        summary["matched"] += 1
        logger.info(
            "[cron_thinker/mail] 标题命中公告 | mid=%s… feed=%s",
            mid[:16],
            {k: result.get(k) for k in ("ok", "ignored", "reason", "job_id") if k in result},
        )
        _audit_log(
            "mail_poll_feed_result",
            tick_id=tick_id,
            message_id=mid,
            feed={k: result.get(k) for k in ("ok", "ignored", "reason", "job_id", "maintenance_date", "scheduled_for", "source") if k in result},
        )
        if result.get("ok"):
            _mail_mark_seen(mid)
            seen_local.add(mid)
        elif result.get("reason") == "duplicate_job_id":
            _mail_mark_seen(mid)
            seen_local.add(mid)
        elif result.get("reason") == "run_at_in_past":
            # 仅「预定执行日早于今天」才标为已读，避免历史邮件无限重试。
            # 若预定日是今天、只是 BIOS 时刻已过当前时间，不标记，便于用户在控制台把时刻调晚后下一轮轮询会再次 feed。
            sf = result.get("scheduled_for")
            run_at_sf: dt.datetime | None = None
            if sf:
                try:
                    run_at_sf = dt.datetime.fromisoformat(str(sf))
                    if run_at_sf.tzinfo is None:
                        run_at_sf = run_at_sf.replace(tzinfo=TZ_BEIJING)
                    else:
                        run_at_sf = run_at_sf.astimezone(TZ_BEIJING)
                except ValueError:
                    run_at_sf = None
            now_bj = dt.datetime.now(tz=TZ_BEIJING)
            if run_at_sf is not None and run_at_sf.date() < now_bj.date():
                _mail_mark_seen(mid)
                seen_local.add(mid)
                _audit_log(
                    "mail_poll_run_at_in_past_mark_seen_stale_day",
                    tick_id=tick_id,
                    message_id=mid,
                    scheduled_for=sf,
                    note="预定执行日早于今天，视为历史公告",
                )
            else:
                logger.info(
                    "[cron_thinker/mail] 预定执行时刻早于当前时间，未标已读以便调整生物钟后重试 | mid=%s… scheduled_for=%s",
                    mid[:16],
                    sf,
                )
                _audit_log(
                    "mail_poll_run_at_in_past_keep_unseen",
                    tick_id=tick_id,
                    message_id=mid,
                    scheduled_for=sf,
                    now_beijing=now_bj.isoformat(),
                    hint="请把「时:分」调到当前时间之后并保存，或改「维护日后第几天」",
                )
        elif result.get("reason") == "no_matching_release_announcement":
            logger.warning(
                "[cron_thinker/mail] 标题命中但正文未解析到维护日，下轮重试 | mid=%s…",
                mid[:16],
            )

    _mail_poll_last_error = None if not summary["errors"] else "; ".join(summary["errors"][:3])
    _mail_poll_last_ok_ts = dt.datetime.now(tz=TZ_BEIJING).isoformat()
    _audit_log("mail_poll_tick_end", tick_id=tick_id, summary=summary, last_ok_ts=_mail_poll_last_ok_ts)
    return summary


def _mail_poll_tick() -> None:
    try:
        mail_poll_tick_once()
    except Exception as e:
        logger.exception("[cron_thinker/mail] 轮询未捕获异常")
        _audit_log(
            "mail_poll_tick_uncaught_exception",
            exc_type=type(e).__name__,
            error=str(e),
        )


def _mail_poll_schedule_immediate(*, reason: str) -> None:
    """非阻塞：在守护线程立即跑一轮邮箱轮询（控制台保存生物钟后立即拉信，随后仍按 Interval 执行）。"""

    def _run() -> None:
        try:
            _mail_poll_tick()
        except Exception:
            logger.exception("[cron_thinker/mail] immediate poll 线程异常")

    threading.Thread(
        target=_run,
        name="cron-thinker-mail-immediate",
        daemon=True,
    ).start()
    _audit_log("mail_poll_immediate_thread_started", reason=reason)


def _ensure_mail_poll_job(sched) -> None:
    from apscheduler.triggers.interval import IntervalTrigger

    if not _release_smoke_enabled() or not _mail_poll_enabled() or not _bios_console_enabled():
        try:
            sched.remove_job(_JOB_MAIL_POLL)
        except Exception:
            pass
        _audit_log(
            "mail_poll_job_removed_or_skipped_detail",
            job_id=_JOB_MAIL_POLL,
            release_smoke=_release_smoke_enabled(),
            mail_poll=_mail_poll_enabled(),
            bios_console_enabled=_bios_console_enabled(),
        )
        return
    sec = _mail_poll_interval_sec()
    misfire = max(60, sec)
    sched.add_job(
        _mail_poll_tick,
        IntervalTrigger(seconds=sec, timezone=TZ_BEIJING),
        id=_JOB_MAIL_POLL,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=misfire,
    )
    logger.info(
        "[cron_thinker/mail] 已注册轮询 | mailbox=%s every=%ss page_size=%s",
        _mail_mailbox(),
        sec,
        _mail_poll_page_size(),
    )
    j = None
    try:
        j = sched.get_job(_JOB_MAIL_POLL)
    except Exception:
        pass
    _audit_log(
        "mail_poll_job_registered",
        job_id=_JOB_MAIL_POLL,
        interval_sec=sec,
        mailbox=_mail_mailbox(),
        page_size=_mail_poll_page_size(),
        folder_id=_mail_folder_id_effective(),
        folder_id_env=_mail_folder_id(),
        misfire_grace_time=misfire,
        next_run_time=str(getattr(j, "next_run_time", None)) if j else "",
        trigger=str(getattr(j, "trigger", None)) if j else "",
    )


def _load_persisted_jobs() -> list[dict[str, Any]]:
    if not _PERSIST_PATH.is_file():
        return []
    try:
        data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8").strip() or "{}")
        jobs = data.get("jobs")
        if isinstance(jobs, list):
            return [j for j in jobs if isinstance(j, dict)]
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[cron_thinker] 读取持久化任务失败: %s", e)
    return []


def _write_persisted_jobs(jobs: list[dict[str, Any]]) -> None:
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST_PATH.write_text(
            json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("[cron_thinker] 写入持久化任务失败: %s", e)


def _persist_add(job_id: str, run_at: dt.datetime, source: str) -> None:
    with _STATE_LOCK:
        jobs = _load_persisted_jobs()
        if any(j.get("job_id") == job_id for j in jobs):
            _audit_log("persist_add_skipped_duplicate", job_id=job_id, run_at=run_at.isoformat(), source=source)
            return
        jobs.append(
            {
                "job_id": job_id,
                "run_at": run_at.isoformat(),
                "source": source,
                "registered_at": dt.datetime.now(tz=TZ_BEIJING).isoformat(),
            }
        )
        _write_persisted_jobs(jobs)
    _audit_log(
        "persist_add",
        job_id=job_id,
        run_at=run_at.isoformat(),
        source=source,
        total_jobs=len(jobs),
    )


def _persist_remove(job_id: str) -> None:
    with _STATE_LOCK:
        before = _load_persisted_jobs()
        jobs = [j for j in before if j.get("job_id") != job_id]
        if len(jobs) == len(before):
            _audit_log("persist_remove_noop", job_id=job_id)
        else:
            _write_persisted_jobs(jobs)
            _audit_log("persist_remove", job_id=job_id, remaining=len(jobs))


def _k11_unified_smoke_subprocess_passthrough() -> list[str]:
    """
    构造统合冒烟子进程参数，与 ``l3_node.http_server._handle_k11_unified_smoke_stream``
    在默认/常见 query 下的行为对齐：``--target-url``、可选 ``--cdp-http``、``-v``、可选 ``--no-lark-report``。
    """
    target = (os.environ.get("K11_UNIFIED_SMOKE_TARGET_URL") or "").strip() or "https://www.kalaroko.com/"
    passthrough: list[str] = ["--target-url", target]
    cdp = (os.environ.get("K11_UNIFIED_SMOKE_CDP_HTTP") or "").strip()
    if cdp:
        passthrough.extend(["--cdp-http", cdp])
    passthrough.append("-v")
    no_lark = (
        (os.environ.get("JACHIN_CRON_THINKER_SMOKE_NO_LARK") or "").strip().lower() in ("1", "true", "yes", "on")
        or (os.environ.get("K11_SCHEDULED_SMOKE_NO_LARK") or "").strip().lower() in ("1", "true", "yes", "on")
    )
    if no_lark:
        passthrough.append("--no-lark-report")
    return passthrough


def _run_k11_unified_smoke_subprocess() -> None:
    """
    生物钟到点：拉起统合冒烟子进程；stdout 推送到与「每日定时」相同的 MIND STREAM / schedule/log-stream。

    同时追加 ``~/.jachin/logs/cron_thinker_k11_smoke.log`` 便于落盘排查。
    """
    from l3_node.k11_subprocess_cli import build_k11_l3_subprocess_cmd

    passthrough = _k11_unified_smoke_subprocess_passthrough()
    cmd = build_k11_l3_subprocess_cmd("--jachin-k11-unified-smoke-subprocess", passthrough)
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    cwd: str | None = None
    try:
        from l3_node.paths import get_app_root

        root = get_app_root().resolve()
        cwd = str(root)
        env["JACHIN_APP_ROOT"] = cwd
    except Exception:
        cwd = os.getcwd()

    emit_log = None
    try:
        from l3_node.jobs.k11_unified_smoke_scheduler import k11_scheduled_log_emit_from_thread

        emit_log = k11_scheduled_log_emit_from_thread
    except Exception:
        pass

    def emit(obj: dict[str, Any]) -> None:
        if emit_log is None:
            return
        try:
            emit_log(obj)
        except Exception:
            pass

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_f = open(_LOG_PATH, "a", encoding="utf-8")
    except OSError as e:
        logger.error("[cron_thinker] 无法打开冒烟日志 %s: %s", _LOG_PATH, e)
        log_f = None

    no_lark_report = "--no-lark-report" in passthrough

    _audit_log(
        "smoke_subprocess_prepare",
        cmd=cmd,
        cwd=cwd,
        log_path=str(_LOG_PATH),
        env_jachin_app_root=env.get("JACHIN_APP_ROOT"),
        pythonunbuffered=env.get("PYTHONUNBUFFERED"),
        no_lark_report=no_lark_report,
        mind_stream_emit_registered=emit_log is not None,
        audit_smoke_max_lines=_audit_smoke_max_lines(),
    )

    logger.info(
        "[cron_thinker] 拉起统合冒烟子进程 cmd=%s cwd=%s",
        cmd,
        cwd,
    )

    emit(
        {
            "type": "scheduled_start",
            "ts": time.time(),
            "runs": 1,
            "interval_sec": 0,
            "script": "cron_thinker（发版公告）",
        }
    )

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=False,
        )
    except Exception as e:
        logger.exception("[cron_thinker] subprocess.Popen 失败")
        _audit_log(
            "smoke_subprocess_popen_failed",
            error=str(e),
            exc_type=type(e).__name__,
            cmd=cmd,
            cwd=cwd,
        )
        emit({"type": "error", "message": f"子进程启动失败: {e!s}"})
        emit({"type": "scheduled_done", "ok": False, "runs": 1})
        if log_f:
            try:
                log_f.close()
            except Exception:
                pass
        return

    max_lines = _audit_smoke_max_lines()
    _audit_log("smoke_subprocess_started", pid=proc.pid, cmd=cmd, cwd=cwd)

    def pump() -> None:
        all_ok = True
        line_no = 0
        truncated_notice = False
        try:
            if proc.stdout is not None:
                for raw in iter(proc.stdout.readline, b""):
                    try:
                        text = raw.decode("utf-8", errors="replace").rstrip("\n\r")
                    except Exception:
                        text = ""
                    line_no += 1
                    if text:
                        emit({"line": f"[生物钟] {text}"})
                    if text:
                        if max_lines == 0 or line_no <= max_lines:
                            _audit_log(
                                "smoke_subprocess_stdout_line",
                                pid=proc.pid,
                                line_no=line_no,
                                line=_audit_trunc(text, 8000),
                            )
                        elif max_lines > 0 and line_no == max_lines + 1 and not truncated_notice:
                            truncated_notice = True
                            _audit_log(
                                "smoke_subprocess_stdout_truncated",
                                pid=proc.pid,
                                max_lines=max_lines,
                                note="其余行见 cron_thinker_k11_smoke.log",
                            )
                    if log_f:
                        try:
                            log_f.write(text + "\n")
                            log_f.flush()
                        except Exception:
                            pass
            code = int(proc.wait())
            all_ok = code == 0
            emit({"type": "scheduled_progress", "round": 1, "total": 1, "exit_code": code})
            emit({"type": "scheduled_done", "ok": all_ok, "runs": 1})
            _audit_log(
                "smoke_subprocess_exit",
                pid=proc.pid,
                exit_code=code,
                ok=all_ok,
                stdout_line_count=line_no,
                audit_lines_capped=max_lines if max_lines > 0 else None,
            )
        except Exception as e:
            logger.exception("[cron_thinker] 子进程输出泵送异常")
            _audit_log(
                "smoke_subprocess_pump_exception",
                pid=proc.pid,
                error=str(e),
                exc_type=type(e).__name__,
                stdout_line_count=line_no,
            )
            emit({"type": "error", "message": str(e)})
            emit({"type": "scheduled_done", "ok": False, "runs": 1})
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            if log_f:
                try:
                    log_f.close()
                except Exception:
                    pass

    threading.Thread(target=pump, name="cron-thinker-smoke-pump", daemon=True).start()


def _get_scheduler():
    global _SCHEDULER, _SCHEDULER_STARTED
    from apscheduler.schedulers.background import BackgroundScheduler

    with _STATE_LOCK:
        if _SCHEDULER is not None:
            return _SCHEDULER
        sched = BackgroundScheduler(timezone=str(TZ_BEIJING))
        sched.start()
        _SCHEDULER = sched
        _SCHEDULER_STARTED = True
        logger.info("[cron_thinker] BackgroundScheduler 已启动（timezone=Asia/Shanghai）")
        _audit_log(
            "background_scheduler_spawned",
            timezone="Asia/Shanghai",
            scheduler_repr=str(sched),
        )
        return sched


def start_cron_thinker_daemon() -> None:
    """
    L3 启动时调用：拉起调度器并恢复持久化中的未来任务。

    幂等：多次调用不会重复起多个 scheduler。
    """
    if not _release_smoke_enabled():
        logger.info("[cron_thinker] JACHIN_CRON_THINKER_RELEASE_SMOKE 已关闭，跳过后台调度")
        _audit_log("cron_thinker_daemon_skip", reason="release_smoke_disabled")
        return
    _audit_log(
        "cron_thinker_daemon_enter",
        env=_audit_env_snapshot(),
        bios=load_bios_settings(),
        persisted_count=len(_load_persisted_jobs()),
    )
    sched = _get_scheduler()
    _restore_persisted_jobs(sched)
    _ensure_mail_poll_job(sched)
    mail_job = None
    try:
        mail_job = sched.get_job(_JOB_MAIL_POLL)
    except Exception:
        pass
    _audit_log(
        "cron_thinker_daemon_ready",
        persisted_count=len(_load_persisted_jobs()),
        mail_poll_next=str(getattr(mail_job, "next_run_time", None)) if mail_job else "",
    )


def _restore_persisted_jobs(sched) -> None:
    """将仍为未来的任务重新挂到调度器（进程重启后）。"""
    from apscheduler.triggers.date import DateTrigger

    now = dt.datetime.now(tz=TZ_BEIJING)
    with _STATE_LOCK:
        jobs = _load_persisted_jobs()
    _audit_log("persist_restore_begin", persisted_file=str(_PERSIST_PATH), row_count=len(jobs), now_beijing=now.isoformat())
    keep: list[dict[str, Any]] = []
    restored_adds = 0
    for row in jobs:
        jid = str(row.get("job_id") or "")
        run_s = str(row.get("run_at") or "")
        if not jid or not run_s:
            _audit_log("persist_restore_skip_invalid_row", row=row)
            continue
        try:
            run_at = dt.datetime.fromisoformat(run_s)
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=TZ_BEIJING)
            else:
                run_at = run_at.astimezone(TZ_BEIJING)
        except ValueError:
            logger.warning("[cron_thinker] 持久化项 run_at 无效 job_id=%s raw=%s", jid, run_s)
            _audit_log("persist_restore_invalid_run_at", job_id=jid, run_at_raw=run_s)
            continue
        if run_at <= now:
            logger.info("[cron_thinker] 持久化任务已过期，丢弃 job_id=%s run_at=%s", jid, run_s)
            _audit_log(
                "persist_restore_drop_expired",
                job_id=jid,
                run_at=run_at.isoformat(),
                now=now.isoformat(),
            )
            continue
        keep.append(row)
        existing = None
        try:
            existing = sched.get_job(jid)
        except Exception:
            pass
        if existing:
            _audit_log(
                "persist_restore_skip_already_in_scheduler",
                job_id=jid,
                run_at=run_at.isoformat(),
                next_run_time=str(getattr(existing, "next_run_time", None)),
            )
            continue

        def _make_cb(j_id: str) -> Callable[[], None]:
            def _cb() -> None:
                _audit_log("scheduled_smoke_job_fire", job_id=j_id, fire_kind="persisted_restore")
                logger.info("[cron_thinker] 到点执行（恢复任务）job_id=%s", j_id)
                threading.Thread(
                    target=_run_k11_unified_smoke_subprocess,
                    name=f"cron-thinker-smoke-{j_id}",
                    daemon=True,
                ).start()
                try:
                    sched.remove_job(j_id)
                except Exception:
                    pass
                _persist_remove(j_id)

            return _cb

        sched.add_job(
            _make_cb(jid),
            DateTrigger(run_date=run_at),
            id=jid,
            replace_existing=False,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        recovered = None
        try:
            recovered = sched.get_job(jid)
        except Exception:
            pass
        logger.info(
            "[cron_thinker] 已从持久化恢复 job_id=%s run_at=%s",
            jid,
            run_at.isoformat(),
        )
        _audit_log(
            "persist_restore_job_scheduled",
            job_id=jid,
            run_at=run_at.isoformat(),
            misfire_grace_time=3600,
            next_run_time=str(getattr(recovered, "next_run_time", None)) if recovered else "",
            trigger=str(getattr(recovered, "trigger", None)) if recovered else "",
        )
        restored_adds += 1
    if len(keep) != len(jobs):
        with _STATE_LOCK:
            _write_persisted_jobs(keep)
        _audit_log(
            "persist_restore_pruned_file",
            before=len(jobs),
            after=len(keep),
            dropped=len(jobs) - len(keep),
        )
    _audit_log(
        "persist_restore_done",
        input_rows=len(jobs),
        kept_future_rows=len(keep),
        new_date_jobs_registered=restored_adds,
    )


def feed_release_announcement_text(
    raw: str,
    *,
    source: str = "ingest",
    chat_id: str | None = None,
) -> dict[str, Any]:
    """
    接收飞书/邮件等原始文本，匹配公告后按 **控制台 BIOS 设置** 注册统合冒烟时间。

    :param raw: 公告全文（含标题行、维护时间行）。
    :param source: 日志打点来源：``lark`` / ``http`` / ``manual`` 等。
    :param chat_id: 飞书会话 id；若配置了 ``JACHIN_RELEASE_ANNOUNCEMENT_LARK_CHAT_IDS`` 则须命中。
    :return: 结构化结果，供 HTTP JSON / 日志。
    """
    base: dict[str, Any] = {
        "ok": False,
        "ignored": True,
        "source": source,
    }
    enter_extra: dict[str, Any] = {
        "source": source,
        "chat_id": _audit_trunc((chat_id or ""), 96),
        "raw_len": len(raw or ""),
        "has_title_needle": release_title_present(raw or ""),
        "bios": load_bios_settings(),
    }
    if source in ("mail", "http"):
        enter_extra["raw_head"] = _audit_trunc(raw or "", _AUDIT_RAW_HEAD)
    _audit_log("feed_release_enter", **enter_extra)

    if not _release_smoke_enabled():
        base["reason"] = "cron_thinker_release_smoke_disabled"
        _audit_log("feed_release_outcome", **base)
        return base

    if not _bios_console_enabled():
        base["reason"] = "cron_thinker_bios_disabled"
        _audit_log("feed_release_outcome", **base)
        return base

    allowed = _allowed_lark_chat_ids()
    if allowed is not None and source == "lark":
        cid = (chat_id or "").strip()
        if not cid or cid not in allowed:
            base["reason"] = "chat_id_not_in_allowlist"
            base["chat_id"] = cid[:48]
            _audit_log(
                "feed_release_outcome",
                **base,
                allowlist_size=len(allowed),
            )
            return base

    maintenance = parse_release_maintenance_date(raw or "")
    if maintenance is None:
        base["reason"] = "no_matching_release_announcement"
        _audit_log(
            "feed_release_outcome",
            **base,
            note="正文须含宽松/标准发版公告标题与【维护时间】日期行",
        )
        return base

    run_at = compute_smoke_run_at_beijing(maintenance)
    jid = job_id_for_maintenance_day(maintenance)
    now = dt.datetime.now(tz=TZ_BEIJING)
    if run_at <= now:
        logger.warning(
            "[cron_thinker] 预定执行时间已在过去，跳过 scheduling | maintenance=%s run_at=%s now=%s",
            maintenance.isoformat(),
            run_at.isoformat(),
            now.isoformat(),
        )
        out = {
            "ok": False,
            "ignored": False,
            "reason": "run_at_in_past",
            "maintenance_date": maintenance.isoformat(),
            "scheduled_for": run_at.isoformat(),
            "job_id": jid,
            "source": source,
        }
        _audit_log("feed_release_outcome", **out, now_beijing=now.isoformat())
        return out

    sched = _get_scheduler()
    from apscheduler.triggers.date import DateTrigger

    registered: dict[str, str] = {"next_run_time": "", "trigger": ""}
    with _STATE_LOCK:
        if sched.get_job(jid) is not None:
            logger.info(
                "[cron_thinker] 已存在同日维护任务，跳过重复注册 job_id=%s captured_from=%s",
                jid,
                source,
            )
            dup = {
                "ok": True,
                "ignored": True,
                "reason": "duplicate_job_id",
                "maintenance_date": maintenance.isoformat(),
                "scheduled_for": run_at.isoformat(),
                "job_id": jid,
                "source": source,
            }
            _audit_log("feed_release_outcome", **dup)
            return dup

        def _cb() -> None:
            _audit_log(
                "scheduled_smoke_job_fire",
                job_id=jid,
                fire_kind="feed_release_date",
                maintenance_date=maintenance.isoformat(),
            )
            logger.info(
                "[cron_thinker] 到点执行（发版次日冒烟）job_id=%s maintenance=%s",
                jid,
                maintenance.isoformat(),
            )
            threading.Thread(
                target=_run_k11_unified_smoke_subprocess,
                name=f"cron-thinker-smoke-{jid}",
                daemon=True,
            ).start()
            try:
                sched.remove_job(jid)
            except Exception:
                pass
            _persist_remove(jid)

        try:
            sched.add_job(
                _cb,
                DateTrigger(run_date=run_at),
                id=jid,
                replace_existing=False,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=3600,
            )
        except Exception as e:
            logger.exception("[cron_thinker] add_job 失败 job_id=%s", jid)
            err = {
                "ok": False,
                "ignored": False,
                "reason": f"scheduler_error:{e}",
                "job_id": jid,
                "source": source,
            }
            _audit_log(
                "feed_release_outcome",
                **err,
                exc_type=type(e).__name__,
            )
            return err
        aj = sched.get_job(jid)
        if aj:
            registered["next_run_time"] = str(getattr(aj, "next_run_time", None))
            registered["trigger"] = str(getattr(aj, "trigger", None))

    _persist_add(jid, run_at, source)
    logger.info(
        "[cron_thinker] 已登记发版次日冒烟 | captured_from=%s maintenance=%s job_id=%s run_at=%s",
        source,
        maintenance.isoformat(),
        jid,
        run_at.isoformat(),
    )
    ok_out = {
        "ok": True,
        "ignored": False,
        "maintenance_date": maintenance.isoformat(),
        "scheduled_for": run_at.isoformat(),
        "job_id": jid,
        "source": source,
    }
    _audit_log(
        "feed_release_outcome",
        **ok_out,
        scheduled_trigger_next=registered.get("next_run_time", ""),
        trigger_repr=registered.get("trigger", ""),
        misfire_grace_time=3600,
    )
    return ok_out


def cron_thinker_scheduler_status() -> dict[str, Any]:
    """调试/运维：当前调度器内与持久化中的发版冒烟任务摘要。"""
    jobs_sched: list[dict[str, str]] = []
    try:
        sched = _SCHEDULER
        if sched is not None:
            for j in sched.get_jobs():
                jid = getattr(j, "id", "") or ""
                if jid.startswith("smoke_test_"):
                    nt = getattr(j, "next_run_time", None)
                    jobs_sched.append(
                        {
                            "id": jid,
                            "next_run_time": str(nt) if nt else "",
                        }
                    )
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "scheduler_started": _SCHEDULER_STARTED,
        "scheduled_jobs": jobs_sched,
        "persisted_jobs": _load_persisted_jobs(),
        "enabled": _release_smoke_enabled(),
        "bios_settings": load_bios_settings(),
        "bios_console_enabled": _bios_console_enabled(),
        "mail_poll": {
            "enabled": _mail_poll_enabled(),
            "mailbox": _mail_mailbox(),
            "interval_sec": _mail_poll_interval_sec(),
            "page_size": _mail_poll_page_size(),
            "folder_id": _mail_folder_id_effective(),
            "folder_id_env": _mail_folder_id(),
            "last_ok_ts": _mail_poll_last_ok_ts,
            "last_error": _mail_poll_last_error,
        },
    }
