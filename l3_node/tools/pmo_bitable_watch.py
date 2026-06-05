"""
PMO 飞书多维表变更监控：轮询 diff + 防抖会话 + 空闲后回调。

设计 SSOT：skills_repo/pmo-copilot/SKILL.change-alert.md · docs/architecture/PMO_CHANGE_ALERT_DESIGN.md

- 原子能力：core:pmo_bitable_watch_tick / core:pmo_bitable_watch_status / core:pmo_change_diff
- 长时轮询：l3_node/jobs/pmo_bitable_watch_scheduler.py
- 推送：send_watch_notification → atom_lark_notifier / channels.lark
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_PATH = Path.home() / ".jachin" / "data" / "pmo_bitable_watch_state.json"
_CALLBACK_DIR = Path.home() / ".jachin" / "data" / "pmo_bitable_watch_callbacks"
_CALLBACK_NDJSON = _CALLBACK_DIR / "callbacks.ndjson"
_CALLBACK_LATEST_MD = _CALLBACK_DIR / "latest.md"
_SKILL_ID = "pmo-copilot"
_CONFIG_NAME = "pmo_bitable_watch.yaml"

_DEFAULT_TABLE_ID = "tblB2uMLGIQrAttB"
_DEFAULT_VIEW_ID = "vewpI8lyYw"
_DEFAULT_CHAT_ID = "oc_b1b9cff6804517c79b7f5a617ab30483"
_DEFAULT_IDLE_SECONDS = 20
_DEFAULT_POLL_SECONDS = 15
_DEFAULT_MAX_RECORDS = 5000

_CHANGE_TYPE_LABELS = {
    "created": "新增",
    "updated": "修改",
    "deleted": "删除",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _seconds_since(ts: str | None) -> float | None:
    dt = _parse_iso(ts)
    if not dt:
        return None
    return (_utc_now() - dt).total_seconds()


def _load_yaml_config() -> dict[str, Any]:
    import yaml

    from l3_node.jachin_config import get_config_root, get_skill_config_dir
    from l3_node.paths import get_app_root

    candidates = [
        get_skill_config_dir(_SKILL_ID) / _CONFIG_NAME,
        get_app_root() / "config" / "skills" / _SKILL_ID / _CONFIG_NAME,
        get_config_root() / "skills" / _SKILL_ID / _CONFIG_NAME,
    ]
    for path in candidates:
        if path.is_file():
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                return raw if isinstance(raw, dict) else {}
            except Exception as e:
                logger.warning("[pmo_bitable_watch] 读取配置失败 %s: %s", path, e)
    return {}


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _expand_placeholder(val: Any) -> str:
    s = str(val or "").strip()
    if s.startswith("${") and s.endswith("}"):
        return (os.environ.get(s[2:-1]) or "").strip()
    return s


def _parse_bitable_url(url: str) -> dict[str, str]:
    """解析飞书多维表链接（/base/{app_token} 或 /wiki/{node}?table=&view=）。"""
    from urllib.parse import parse_qs, urlparse

    u = urlparse((url or "").strip())
    qs = parse_qs(u.query)
    parts = [p for p in u.path.split("/") if p]
    app_token = ""
    if "base" in parts:
        idx = parts.index("base")
        if idx + 1 < len(parts):
            app_token = parts[idx + 1]
    table_id = (qs.get("table") or [""])[0] or ""
    view_id = (qs.get("view") or [""])[0] or ""
    return {
        "app_token": str(app_token).strip(),
        "table_id": str(table_id).strip(),
        "view_id": str(view_id).strip(),
    }


def _load_watch_config() -> dict[str, Any]:
    """合并 YAML 与 PMO_BITABLE_WATCH_* 环境变量。"""
    yaml_cfg = _load_yaml_config()

    def _cfg(key: str, env_key: str, default: Any) -> Any:
        env_val = _env_str(env_key)
        if env_val:
            return env_val
        if key in yaml_cfg and yaml_cfg.get(key) not in (None, ""):
            return yaml_cfg[key]
        return default

    app_id = _expand_placeholder(_cfg("app_id", "PMO_BITABLE_WATCH_APP_ID", ""))
    app_secret = _expand_placeholder(_cfg("app_secret", "PMO_BITABLE_WATCH_APP_SECRET", ""))
    notify_app_id = _expand_placeholder(
        _cfg("notify_app_id", "PMO_BITABLE_WATCH_NOTIFY_APP_ID", yaml_cfg.get("notify_app_id") or "")
    )
    notify_app_secret = _expand_placeholder(
        _cfg("notify_app_secret", "PMO_BITABLE_WATCH_NOTIFY_APP_SECRET", yaml_cfg.get("notify_app_secret") or "")
    )
    if not notify_app_id or not notify_app_secret:
        try:
            from l3_node.jachin_config import load_mcp_config
            from l3_node.paths import get_app_root

            notifier = load_mcp_config("atom_lark_notifier", project_root=get_app_root())
            naid = str(notifier.get("app_id") or "").strip()
            nsec = str(notifier.get("app_secret") or "").strip()
            if naid and nsec and not naid.startswith("${"):
                notify_app_id = notify_app_id or naid
                notify_app_secret = notify_app_secret or nsec
        except Exception:
            pass
    if not app_id:
        app_id = (
            os.environ.get("LARK_APP_ID")
            or os.environ.get("FEISHU_APP_ID")
            or ""
        ).strip()
    if not app_secret:
        app_secret = (
            os.environ.get("LARK_APP_SECRET")
            or os.environ.get("FEISHU_APP_SECRET")
            or ""
        ).strip()

    bitable_url = str(
        _cfg("bitable_url", "PMO_BITABLE_WATCH_BITABLE_URL", "")
        or yaml_cfg.get("document_url")
        or ""
    ).strip()
    url_bits = _parse_bitable_url(bitable_url) if bitable_url else {}

    table_id = str(_cfg("table_id", "PMO_BITABLE_WATCH_TABLE_ID", _DEFAULT_TABLE_ID)).strip()
    if not table_id or table_id == _DEFAULT_TABLE_ID:
        table_id = url_bits.get("table_id") or table_id
    view_id = str(_cfg("view_id", "PMO_BITABLE_WATCH_VIEW_ID", _DEFAULT_VIEW_ID)).strip()
    if not _env_str("PMO_BITABLE_WATCH_VIEW_ID") and url_bits.get("view_id"):
        view_id = url_bits["view_id"]
    app_token = str(_cfg("app_token", "PMO_BITABLE_WATCH_APP_TOKEN", "")).strip()
    if not app_token:
        app_token = url_bits.get("app_token") or ""

    return {
        "enabled": _env_bool("PMO_BITABLE_WATCH_ENABLED", bool(yaml_cfg.get("enabled", True))),
        "table_id": table_id,
        "view_id": view_id,
        "chat_id": str(_cfg("chat_id", "PMO_BITABLE_WATCH_CHAT_ID", _DEFAULT_CHAT_ID)).strip(),
        "monitor_chat_id": str(
            _cfg("monitor_chat_id", "PMO_BITABLE_WATCH_MONITOR_CHAT_ID", "")
        ).strip()
        or None,
        "bitable_url": bitable_url,
        "wiki_url": str(_cfg("wiki_url", "PMO_BITABLE_WATCH_WIKI_URL", "")).strip(),
        "app_token": app_token,
        "app_id": app_id,
        "app_secret": app_secret,
        "notify_app_id": notify_app_id,
        "notify_app_secret": notify_app_secret,
        "idle_seconds": max(
            10,
            _env_int("PMO_BITABLE_WATCH_IDLE_SECONDS", int(yaml_cfg.get("idle_seconds") or _DEFAULT_IDLE_SECONDS)),
        ),
        "mode": str(
            _cfg("mode", "PMO_BITABLE_WATCH_MODE", yaml_cfg.get("mode") or "webhook")
        ).strip()
        .lower(),
        "debounce_check_seconds": max(
            5,
            _env_int(
                "PMO_BITABLE_WATCH_DEBOUNCE_CHECK_SECONDS",
                int(yaml_cfg.get("debounce_check_seconds") or 10),
            ),
        ),
        "poll_interval_seconds": max(
            5,
            _env_int(
                "PMO_BITABLE_WATCH_POLL_SECONDS",
                int(yaml_cfg.get("poll_interval_seconds") or _DEFAULT_POLL_SECONDS),
            ),
        ),
        "max_records": max(
            100,
            int(yaml_cfg.get("max_records") or _DEFAULT_MAX_RECORDS),
        ),
        "run_change_alert": bool(yaml_cfg.get("run_change_alert", True)),
        "push_change_summary": bool(yaml_cfg.get("push_change_summary", True)),
        "persist_local": bool(yaml_cfg.get("persist_local", True)),
        "dry_run": bool(yaml_cfg.get("dry_run", False)),
    }


def _persist_callback_local(
    *,
    events: list[dict[str, Any]],
    markdown: str,
    table_id: str,
    view_id: str,
    session_started_at: str,
    session_ended_at: str,
    notify_result: dict[str, Any] | None = None,
    change_alert: dict[str, Any] | None = None,
) -> dict[str, str]:
    """每次会话结束写入本机：NDJSON 日志 + latest.md + 时间戳 md。"""
    _CALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    stamp = session_ended_at.replace(":", "").replace("+", "_")[:19]
    ts_file = _CALLBACK_DIR / f"{stamp}.md"
    record = {
        "finalized_at": session_ended_at,
        "session_started_at": session_started_at,
        "table_id": table_id,
        "view_id": view_id,
        "event_count": len(events),
        "events": events,
        "notify_result": notify_result,
        "change_alert_result": (change_alert or {}).get("change_alert_result"),
    }
    with _CALLBACK_NDJSON.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _CALLBACK_LATEST_MD.write_text(markdown, encoding="utf-8")
    ts_file.write_text(markdown, encoding="utf-8")
    return {
        "ndjson": str(_CALLBACK_NDJSON),
        "latest_md": str(_CALLBACK_LATEST_MD),
        "snapshot_md": str(ts_file),
    }


def _read_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {}
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("[pmo_bitable_watch] 读状态失败: %s", e)
        return {}


def _write_state(state: dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("[pmo_bitable_watch] 写状态失败: %s", e)


def _normalize_value(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, (dict, list)):
        try:
            return json.dumps(val, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(val)
    if isinstance(val, float) and val > 1e11:
        try:
            from datetime import datetime as _dt

            return _dt.utcfromtimestamp(val / 1000).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            pass
    return str(val).strip()


def _normalize_fields(fields: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (fields or {}).items():
        key = str(k).strip()
        if key:
            out[key] = _normalize_value(v)
    return out


def _record_label(fields: dict[str, str]) -> str:
    for key in ("Requirement", "任务名称", "名称", "name", "title", "标题"):
        v = (fields.get(key) or "").strip()
        if v:
            return v[:80]
    return "(无标题)"


def _fields_hash(fields: dict[str, str]) -> str:
    blob = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _records_map_from_list(records: list[dict[str, Any]] | dict[str, Any] | None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if isinstance(records, dict):
        for rid, flds in records.items():
            rid_s = str(rid).strip()
            if not rid_s:
                continue
            if isinstance(flds, dict) and "fields" in flds:
                out[rid_s] = _normalize_fields(flds.get("fields"))
            else:
                out[rid_s] = _normalize_fields(flds if isinstance(flds, dict) else {})
        return out
    for row in records or []:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("record_id") or "").strip()
        if rid:
            out[rid] = _normalize_fields(row.get("fields") or row)
    return out


def diff_record_maps(
    before: dict[str, dict[str, str]],
    after: dict[str, dict[str, str]],
    *,
    view_id: str = "",
    table_id: str = "",
) -> list[dict[str, Any]]:
    """记录级 diff：created / updated / deleted。"""
    events: list[dict[str, Any]] = []
    before_ids = set(before)
    after_ids = set(after)

    for rid in sorted(after_ids - before_ids):
        af = after[rid]
        events.append(
            {
                "change_type": "created",
                "record_id": rid,
                "label": _record_label(af),
                "before": {},
                "after": af,
                "changed_fields": {k: {"before": "", "after": v} for k, v in af.items()},
                "view_id": view_id,
                "table_id": table_id,
            }
        )

    for rid in sorted(before_ids - after_ids):
        bf = before[rid]
        events.append(
            {
                "change_type": "deleted",
                "record_id": rid,
                "label": _record_label(bf),
                "before": bf,
                "after": {},
                "changed_fields": {k: {"before": v, "after": ""} for k, v in bf.items()},
                "view_id": view_id,
                "table_id": table_id,
            }
        )

    for rid in sorted(before_ids & after_ids):
        bf, af = before[rid], after[rid]
        if _fields_hash(bf) == _fields_hash(af):
            continue
        changed: dict[str, dict[str, str]] = {}
        all_keys = set(bf) | set(af)
        for key in sorted(all_keys):
            b, a = bf.get(key, ""), af.get(key, "")
            if b != a:
                changed[key] = {"before": b, "after": a}
        if not changed:
            continue
        events.append(
            {
                "change_type": "updated",
                "record_id": rid,
                "label": _record_label(af) or _record_label(bf),
                "before": bf,
                "after": af,
                "changed_fields": changed,
                "view_id": view_id,
                "table_id": table_id,
            }
        )
    return events


def _parse_webhook_changed_fields(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    raw = payload.get("changed_fields") or payload.get("event", {}).get("changed_fields") or {}
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for key, pair in raw.items():
        if isinstance(pair, dict):
            out[str(key)] = {
                "before": _normalize_value(pair.get("before")),
                "after": _normalize_value(pair.get("after")),
            }
    return out


def _webhook_event_type(payload: dict[str, Any]) -> str:
    et = (
        payload.get("event_type")
        or payload.get("type")
        or (payload.get("event") or {}).get("type")
        or ""
    )
    et = str(et).lower()
    if "created" in et:
        return "created"
    if "deleted" in et:
        return "deleted"
    return "updated"


def run_change_diff(
    *,
    before_records: list[dict[str, Any]] | dict[str, Any] | None = None,
    after_records: list[dict[str, Any]] | dict[str, Any] | None = None,
    webhook_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """core:pmo_change_diff：记录级 diff 或 Webhook 解析。"""
    if webhook_payload and isinstance(webhook_payload, dict):
        wp = webhook_payload
        event_body = wp.get("event") if isinstance(wp.get("event"), dict) else wp
        record_id = str(
            event_body.get("record_id")
            or wp.get("record_id")
            or ""
        ).strip()
        changed = _parse_webhook_changed_fields(event_body if isinstance(event_body, dict) else wp)
        after_fields = _normalize_fields(event_body.get("fields") if isinstance(event_body, dict) else {})
        for key, pair in changed.items():
            if pair.get("after"):
                after_fields[key] = pair["after"]
        evt = {
            "change_type": _webhook_event_type(wp),
            "record_id": record_id,
            "label": _record_label(after_fields),
            "before": {},
            "after": after_fields,
            "changed_fields": changed,
            "view_id": str(wp.get("view_id") or event_body.get("view_id") or "").strip(),
            "table_id": str(wp.get("table_id") or event_body.get("table_id") or "").strip(),
        }
        return {
            "status": "ok",
            "events": [evt],
            "summary": {"created": 1 if evt["change_type"] == "created" else 0,
                        "updated": 1 if evt["change_type"] == "updated" else 0,
                        "deleted": 1 if evt["change_type"] == "deleted" else 0},
        }

    before_map = _records_map_from_list(before_records)
    after_map = _records_map_from_list(after_records)
    events = diff_record_maps(before_map, after_map)
    summary = {
        "created": sum(1 for e in events if e.get("change_type") == "created"),
        "updated": sum(1 for e in events if e.get("change_type") == "updated"),
        "deleted": sum(1 for e in events if e.get("change_type") == "deleted"),
    }
    return {"status": "ok", "events": events, "summary": summary}


def _merge_session_events(existing: list[dict[str, Any]], new_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同 record_id 合并为一条，字段 diff 取并集（after 以最新为准）。"""
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for evt in existing + new_events:
        rid = str(evt.get("record_id") or "").strip() or f"__anon_{len(order)}"
        if rid not in by_id:
            by_id[rid] = copy.deepcopy(evt)
            order.append(rid)
            continue
        cur = by_id[rid]
        cur_changed = dict(cur.get("changed_fields") or {})
        for k, pair in (evt.get("changed_fields") or {}).items():
            if k not in cur_changed:
                cur_changed[k] = pair
            else:
                cur_changed[k] = {
                    "before": cur_changed[k].get("before", ""),
                    "after": pair.get("after", cur_changed[k].get("after", "")),
                }
        cur["changed_fields"] = cur_changed
        cur["after"] = evt.get("after") or cur.get("after") or {}
        cur["change_type"] = evt.get("change_type") or cur.get("change_type")
        cur["label"] = evt.get("label") or cur.get("label")
    return [by_id[r] for r in order]


def _resolve_app_token(client: Any, cfg: dict[str, Any]) -> str:
    direct = str(cfg.get("app_token") or "").strip()
    if direct:
        return direct
    wiki_url = str(cfg.get("wiki_url") or "").strip()
    if not wiki_url:
        raise ValueError(
            "缺少 app_token 或 wiki_url：请在 pmo_bitable_watch.yaml 配置飞书 Wiki 链接以解析多维表 app_token"
        )
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import parse_wiki_url

    parsed = parse_wiki_url(wiki_url)
    node_token = parsed.get("node_token") or ""
    if not node_token:
        raise ValueError(f"wiki_url 无法解析 node_token: {wiki_url}")
    g = client.wiki_get_node(node_token)
    if g.get("code") != 0:
        raise ValueError(f"wiki_get_node 失败: {g.get('msg', g)}")
    raw_d = g.get("data") or {}
    node = raw_d.get("node") if isinstance(raw_d.get("node"), dict) else raw_d
    obj_type = str((node or {}).get("obj_type") or "").lower()
    obj_token = str((node or {}).get("obj_token") or "").strip()
    if obj_type != "bitable" or not obj_token:
        raise ValueError(f"wiki 节点不是 bitable: obj_type={obj_type}")
    return obj_token


def _fetch_bitable_records(cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import (
        BITABLE_RECORD_HARD_CAP,
        _LarkProjectClient,
    )

    app_id = str(cfg.get("app_id") or "").strip()
    app_secret = str(cfg.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise ValueError("未配置 app_id/app_secret（YAML、PMO_BITABLE_WATCH_* 或 LARK_APP_ID/SECRET）")

    api_base = get_lark_api_base()
    token = get_tenant_access_token(app_id=app_id, app_secret=app_secret, api_base=api_base)
    client = _LarkProjectClient(api_base, token)
    app_token = _resolve_app_token(client, cfg)
    table_id = str(cfg.get("table_id") or "").strip()
    view_id = str(cfg.get("view_id") or "").strip() or None
    max_records = min(int(cfg.get("max_records") or _DEFAULT_MAX_RECORDS), BITABLE_RECORD_HARD_CAP)

    fields_meta = client.bitable_list_fields(app_token, table_id)
    fid_to_name: dict[str, str] = {}
    for f in fields_meta:
        fn = (f.get("field_name") or f.get("name") or "").strip()
        fid = f.get("field_id")
        if fid and fn:
            fid_to_name[str(fid)] = fn

    raw_recs = client.bitable_list_records(app_token, table_id, max_records, view_id=view_id)
    out: dict[str, dict[str, str]] = {}
    for r in raw_recs:
        rid = str(r.get("record_id") or "").strip()
        if not rid:
            continue
        norm: dict[str, Any] = {}
        for k, v in (r.get("fields") or {}).items():
            disp = fid_to_name.get(str(k), str(k))
            norm[disp] = v
        out[rid] = _normalize_fields(norm)
    return out


def format_change_summary_markdown(
    events: list[dict[str, Any]],
    *,
    table_id: str = "",
    view_id: str = "",
    session_started_at: str | None = None,
    session_ended_at: str | None = None,
) -> str:
    lines = [
        "## 📋 多维表变更回调",
        "",
        f"- **table**: `{table_id}` · **view**: `{view_id}`",
    ]
    if session_started_at:
        lines.append(f"- **编辑会话**: {session_started_at[:19]} → {(session_ended_at or _iso_now())[:19]}")
    lines.append(f"- **变更条数**: {len(events)}")
    lines.append("")

    for i, evt in enumerate(events, 1):
        ct = str(evt.get("change_type") or "updated")
        label = _CHANGE_TYPE_LABELS.get(ct, ct)
        req = str(evt.get("label") or "(无标题)")
        rid = str(evt.get("record_id") or "")
        lines.append(f"### {i}. {label} · {req}")
        if rid:
            lines.append(f"- record_id: `{rid}`")
        changed = evt.get("changed_fields") or {}
        if isinstance(changed, dict) and changed:
            lines.append("")
            lines.append("| 字段 | 变更前 | 变更后 |")
            lines.append("| :--- | :--- | :--- |")
            for fname, pair in list(changed.items())[:20]:
                if not isinstance(pair, dict):
                    continue
                b = str(pair.get("before") or "")[:120]
                a = str(pair.get("after") or "")[:120]
                lines.append(f"| {fname} | {b or '—'} | {a or '—'} |")
            if len(changed) > 20:
                lines.append(f"| … | 另有 {len(changed) - 20} 个字段 | … |")
        lines.append("")

    lines.append("---")
    lines.append("`pmo_bitable_watch: session_finalized`")
    return "\n".join(lines)


def send_watch_notification(
    markdown: str,
    *,
    chat_id: str,
    title: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        return {"status": "ok", "dry_run": True, "chat_id": chat_id, "preview": markdown[:500]}

    from l3_node.channels.lark.client import get_lark_api_base
    from l3_node.channels.lark.im import send_markdown_card
    from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown

    cfg = _load_watch_config()
    aid = (app_id or cfg.get("notify_app_id") or cfg.get("app_id") or "").strip()
    sec = (app_secret or cfg.get("notify_app_secret") or cfg.get("app_secret") or "").strip()
    try:
        if aid and sec:
            result = send_markdown_card(
                receive_id=chat_id,
                markdown_content=markdown,
                title=title or "【PMO】多维表变更通知",
                receive_id_type="chat_id",
                api_base=get_lark_api_base(),
                app_id=aid,
                app_secret=sec,
            )
        else:
            result = send_lark_markdown(
                "",
                markdown,
                title=title or "【PMO】多维表变更通知",
                chat_id=chat_id,
            )
        if isinstance(result, dict):
            return result
        return {"status": "success", "raw": str(result)[:300]}
    except Exception as e:
        logger.warning("[pmo_bitable_watch] 推送失败: %s", e)
        return {"status": "error", "error": str(e), "error_class": "transient"}


def _finalize_session(
    state: dict[str, Any],
    cfg: dict[str, Any],
    *,
    dry_run: bool | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> dict[str, Any]:
    session = state.get("session") if isinstance(state.get("session"), dict) else {}
    events = list(session.get("events") or [])
    if not events:
        state["session"] = {"active": False, "events": []}
        _write_state(state)
        return {
            "status": "ok",
            "action": "session_finalized_notify",
            "notified": False,
            "message": "会话结束但无累积变更",
        }

    is_dry = cfg.get("dry_run") if dry_run is None else dry_run
    table_id = str(cfg.get("table_id") or "")
    view_id = str(cfg.get("view_id") or "")
    chat_id = str(cfg.get("chat_id") or "").strip()
    started = str(session.get("started_at") or "")
    ended = _iso_now()

    out: dict[str, Any] = {
        "status": "ok",
        "action": "session_finalized_notify",
        "event_count": len(events),
        "session_started_at": started,
        "session_ended_at": ended,
    }

    notify_results: list[dict[str, Any]] = []

    if cfg.get("push_change_summary", True) and chat_id:
        md = format_change_summary_markdown(
            events,
            table_id=table_id,
            view_id=view_id,
            session_started_at=started,
            session_ended_at=ended,
        )
        title = f"【PMO】表变更 · {len(events)} 条"
        nr = send_watch_notification(
            md,
            chat_id=chat_id,
            title=title,
            app_id=app_id or str(cfg.get("notify_app_id") or "") or None,
            app_secret=app_secret or str(cfg.get("notify_app_secret") or "") or None,
            dry_run=bool(is_dry),
        )
        notify_results.append({"kind": "change_summary", **nr})
        out["change_summary_notify"] = nr

    md_for_disk = format_change_summary_markdown(
        events,
        table_id=table_id,
        view_id=view_id,
        session_started_at=started,
        session_ended_at=ended,
    )

    if cfg.get("run_change_alert", True):
        try:
            from l3_node.tools.pmo_change_alert import run_change_alert_pipeline

            alert_out = run_change_alert_pipeline(
                events,
                table_id=table_id,
                view_id=view_id,
                session_started_at=started,
                chat_id=chat_id,
                monitor_chat_id=cfg.get("monitor_chat_id"),
                push_monitor=True,
                dry_run=bool(is_dry),
                app_id=app_id or str(cfg.get("notify_app_id") or "") or None,
                app_secret=app_secret or str(cfg.get("notify_app_secret") or "") or None,
            )
            out["change_alert"] = alert_out
        except Exception as e:
            logger.warning("[pmo_bitable_watch] change_alert 流水线失败: %s", e)
            out["change_alert"] = {"status": "error", "error": str(e), "error_class": "permanent"}

    if cfg.get("persist_local", True):
        paths = _persist_callback_local(
            events=events,
            markdown=md_for_disk,
            table_id=table_id,
            view_id=view_id,
            session_started_at=started,
            session_ended_at=ended,
            notify_result=out.get("change_summary_notify"),
            change_alert=out.get("change_alert") if isinstance(out.get("change_alert"), dict) else None,
        )
        out["local_paths"] = paths

    notified = any(
        str(r.get("status") or "").lower() in ("success", "ok")
        for r in notify_results
        if not r.get("dry_run")
    ) or bool((out.get("change_alert") or {}).get("notified"))

    # 基线推进到当前快照
    current = state.get("current_records") if isinstance(state.get("current_records"), dict) else {}
    state["baseline_records"] = current
    state["session"] = {"active": False, "events": []}
    state["last_notify_at"] = ended
    state["last_finalized_at"] = ended
    _write_state(state)

    out["notified"] = notified
    out["message"] = "变更会话已结束并回调" if notified or is_dry else "变更会话已结束（推送未确认成功）"
    logger.info(
        "[pmo_bitable_watch] 会话结束 events=%d notified=%s dry_run=%s",
        len(events),
        notified,
        is_dry,
    )
    return out


def run_bitable_watch_tick(
    *,
    force_finalize: bool = False,
    dry_run: bool | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> dict[str, Any]:
    """
    单次监控 tick：拉表 → diff → 防抖会话 → 空闲满 idle_seconds 后汇总推送。

    返回 action:
    - baseline_initialized
    - session_active
    - waiting_debounce
    - session_finalized_notify
    """
    cfg = _load_watch_config()
    if not cfg.get("enabled"):
        return {"status": "ok", "action": "disabled", "message": "PMO_BITABLE_WATCH_ENABLED=0"}

    state = _read_state()
    state.setdefault("table_id", cfg["table_id"])
    state.setdefault("view_id", cfg["view_id"])

    try:
        current = _fetch_bitable_records(cfg)
    except Exception as e:
        logger.warning("[pmo_bitable_watch] 拉表失败: %s", e)
        err_class = "config" if "未配置" in str(e) or "缺少" in str(e) else "transient"
        return {
            "status": "error",
            "action": "fetch_failed",
            "error": str(e),
            "error_class": err_class,
        }

    state["current_records"] = current
    state["last_tick_at"] = _iso_now()
    state["record_count"] = len(current)

    baseline = state.get("baseline_records") if isinstance(state.get("baseline_records"), dict) else {}
    session = state.get("session") if isinstance(state.get("session"), dict) else {}
    if not session:
        session = {"active": False, "events": []}

    if not baseline:
        state["baseline_records"] = current
        state["session"] = {"active": False, "events": []}
        _write_state(state)
        return {
            "status": "ok",
            "action": "baseline_initialized",
            "record_count": len(current),
            "message": "已建立基线，后续变更将进入防抖会话",
        }

    tick_events = diff_record_maps(
        baseline,
        current,
        view_id=cfg["view_id"],
        table_id=cfg["table_id"],
    )

    idle_seconds = int(cfg.get("idle_seconds") or _DEFAULT_IDLE_SECONDS)
    last_change_at = str(session.get("last_change_at") or "")
    idle_elapsed = _seconds_since(last_change_at)

    if tick_events:
        if not session.get("active"):
            session = {
                "active": True,
                "started_at": _iso_now(),
                "last_change_at": _iso_now(),
                "events": [],
            }
            logger.info("[pmo_bitable_watch] 新编辑会话开始 changes=%d", len(tick_events))
        else:
            session["last_change_at"] = _iso_now()
        session["events"] = _merge_session_events(list(session.get("events") or []), tick_events)
        state["session"] = session
        state["baseline_records"] = current
        _write_state(state)
        return {
            "status": "ok",
            "action": "session_active",
            "new_changes": len(tick_events),
            "session_event_count": len(session.get("events") or []),
            "idle_seconds": idle_seconds,
            "seconds_since_last_change": 0,
            "message": "检测到变更，继续防抖监控",
        }

    if session.get("active"):
        should_finalize = force_finalize or (
            idle_elapsed is not None and idle_elapsed >= idle_seconds
        )
        if should_finalize:
            if dry_run is not None:
                cfg = {**cfg, "dry_run": dry_run}
            return _finalize_session(
                state,
                cfg,
                dry_run=dry_run,
                app_id=app_id,
                app_secret=app_secret,
            )
        _write_state(state)
        return {
            "status": "ok",
            "action": "waiting_debounce",
            "session_event_count": len(session.get("events") or []),
            "idle_seconds": idle_seconds,
            "seconds_since_last_change": round(idle_elapsed or 0, 1),
            "message": f"会话进行中，距上次变更 {round(idle_elapsed or 0)}s / {idle_seconds}s",
        }

    state["baseline_records"] = current
    state["session"] = {"active": False, "events": []}
    _write_state(state)
    return {
        "status": "ok",
        "action": "idle",
        "record_count": len(current),
        "message": "无变更",
    }


def run_bitable_watch_status() -> dict[str, Any]:
    """core:pmo_bitable_watch_status：只读状态。"""
    cfg = _load_watch_config()
    state = _read_state()
    session = state.get("session") if isinstance(state.get("session"), dict) else {}
    last_change = str(session.get("last_change_at") or "")
    return {
        "status": "ok",
        "enabled": bool(cfg.get("enabled")),
        "table_id": cfg.get("table_id"),
        "view_id": cfg.get("view_id"),
        "chat_id": cfg.get("chat_id"),
        "mode": cfg.get("mode"),
        "idle_seconds": cfg.get("idle_seconds"),
        "debounce_check_seconds": cfg.get("debounce_check_seconds"),
        "poll_interval_seconds": cfg.get("poll_interval_seconds"),
        "session_active": bool(session.get("active")),
        "session_started_at": session.get("started_at"),
        "session_event_count": len(session.get("events") or []),
        "seconds_since_last_change": round(_seconds_since(last_change) or 0, 1) if last_change else None,
        "baseline_record_count": len(state.get("baseline_records") or {}),
        "current_record_count": state.get("record_count"),
        "last_tick_at": state.get("last_tick_at"),
        "last_notify_at": state.get("last_notify_at"),
        "state_path": str(_STATE_PATH),
        "callback_dir": str(_CALLBACK_DIR),
        "callback_latest_md": str(_CALLBACK_LATEST_MD),
        "callback_ndjson": str(_CALLBACK_NDJSON),
    }


_LARK_ACTION_TO_CHANGE = {
    "record_added": "created",
    "record_edited": "updated",
    "record_deleted": "deleted",
}


def _field_value_list_to_map(items: list[Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        fid = str(item.get("field_id") or "").strip()
        if fid:
            out[fid] = _normalize_value(item.get("field_value"))
    return out


def parse_lark_bitable_record_changed(body: dict[str, Any]) -> list[dict[str, Any]]:
    """
    解析飞书官方事件 drive.file.bitable_record_changed_v1 → ChangeEvent 列表。
    文档：https://open.larksuite.com/document/server-docs/docs/drive-v1/event/list/bitable-record-changed
    """
    header = body.get("header") if isinstance(body.get("header"), dict) else {}
    event_type = str(header.get("event_type") or body.get("event_type") or "").strip()
    if event_type and event_type != "drive.file.bitable_record_changed_v1":
        return []

    event = body.get("event") if isinstance(body.get("event"), dict) else body
    if not isinstance(event, dict):
        return []

    table_id = str(event.get("table_id") or "").strip()
    file_token = str(event.get("file_token") or "").strip()
    cfg = _load_watch_config()
    want_table = str(cfg.get("table_id") or "").strip()
    want_token = str(cfg.get("app_token") or "").strip()

    if want_table and table_id and table_id != want_table:
        return []
    if want_token and file_token and file_token != want_token:
        return []

    out: list[dict[str, Any]] = []
    for action in event.get("action_list") or []:
        if not isinstance(action, dict):
            continue
        act = str(action.get("action") or "").strip()
        change_type = _LARK_ACTION_TO_CHANGE.get(act, "updated")
        record_id = str(action.get("record_id") or "").strip()
        before_map = _field_value_list_to_map(action.get("before_value"))
        after_map = _field_value_list_to_map(action.get("after_value"))
        changed: dict[str, dict[str, str]] = {}
        for fid in sorted(set(before_map) | set(after_map)):
            b, a = before_map.get(fid, ""), after_map.get(fid, "")
            if b != a:
                changed[fid] = {"before": b, "after": a}
        label = ""
        for pair in changed.values():
            if pair.get("after"):
                label = str(pair["after"])[:80]
                break
        out.append(
            {
                "change_type": change_type,
                "record_id": record_id,
                "label": label or record_id or "(无标题)",
                "before": before_map,
                "after": after_map,
                "changed_fields": changed,
                "view_id": str(cfg.get("view_id") or ""),
                "table_id": table_id or want_table,
                "file_token": file_token,
                "source": "lark_event",
            }
        )
    return out


def ingest_bitable_change_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """将变更事件并入防抖会话并重置 idle 计时（不拉全表）。"""
    if not events:
        return {"status": "ok", "merged": 0}

    cfg = _load_watch_config()
    state = _read_state()
    session = state.get("session") if isinstance(state.get("session"), dict) else {}
    if not session.get("active"):
        session = {
            "active": True,
            "started_at": _iso_now(),
            "last_change_at": _iso_now(),
            "events": [],
            "source": "lark_event",
        }
        logger.info("[pmo_bitable_watch] Lark 事件触发新编辑会话 changes=%d", len(events))
    else:
        session["last_change_at"] = _iso_now()
    session["events"] = _merge_session_events(list(session.get("events") or []), events)
    state["session"] = session
    state["last_event_at"] = _iso_now()
    _write_state(state)
    return {
        "status": "ok",
        "merged": len(events),
        "session_event_count": len(session.get("events") or []),
        "idle_seconds": cfg.get("idle_seconds"),
        "mode": cfg.get("mode"),
    }


def handle_lark_bitable_record_changed(body: dict[str, Any]) -> dict[str, Any]:
    """Lark 多维表变更事件统一入口：过滤 → 解析 → 入队 → 防抖会话。"""
    events = parse_lark_bitable_record_changed(body)
    if not events:
        return {"status": "ok", "merged": 0, "skipped": "table_or_token_mismatch_or_empty"}

    queue_id = None
    try:
        from l3_node.pmo_webhook_receiver import enqueue_lark_bitable_payload

        queue_id = enqueue_lark_bitable_payload(body, events)
    except Exception as e:
        logger.warning("[pmo_bitable_watch] 入队失败: %s", e)

    debounce = ingest_bitable_change_events(events)
    debounce["queue_id"] = queue_id
    return debounce


def run_bitable_watch_debounce_tick(
    *,
    force_finalize: bool = False,
    dry_run: bool | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> dict[str, Any]:
    """
    Webhook 模式专用 tick：不拉全表，仅检查防抖会话是否 idle 到期并 finalize。
    """
    cfg = _load_watch_config()
    if not cfg.get("enabled"):
        return {"status": "ok", "action": "disabled", "message": "PMO_BITABLE_WATCH_ENABLED=0"}

    state = _read_state()
    session = state.get("session") if isinstance(state.get("session"), dict) else {}
    if not session.get("active"):
        return {"status": "ok", "action": "idle", "message": "无活跃编辑会话"}

    idle_seconds = int(cfg.get("idle_seconds") or _DEFAULT_IDLE_SECONDS)
    last_change_at = str(session.get("last_change_at") or "")
    idle_elapsed = _seconds_since(last_change_at)

    if force_finalize or (idle_elapsed is not None and idle_elapsed >= idle_seconds):
        if dry_run is not None:
            cfg = {**cfg, "dry_run": dry_run}
        out = _finalize_session(
            state,
            cfg,
            dry_run=dry_run,
            app_id=app_id,
            app_secret=app_secret,
        )
        out["mode"] = "webhook"
        return out

    return {
        "status": "ok",
        "action": "waiting_debounce",
        "mode": "webhook",
        "session_event_count": len(session.get("events") or []),
        "idle_seconds": idle_seconds,
        "seconds_since_last_change": round(idle_elapsed or 0, 1),
        "message": f"等待编辑结束 {round(idle_elapsed or 0)}s / {idle_seconds}s",
    }


def touch_webhook_debounce(webhook_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    供未来 pmo_webhook_receiver 调用：解析 Webhook 事件并刷新防抖计时。
    当前实现：将事件并入活跃会话并重置 last_change_at。
    """
    body = webhook_payload or {}
    header = body.get("header") if isinstance(body.get("header"), dict) else {}
    if str(header.get("event_type") or "") == "drive.file.bitable_record_changed_v1":
        return handle_lark_bitable_record_changed(body)

    cfg = _load_watch_config()
    diff = run_change_diff(webhook_payload=body)
    events = diff.get("events") or []
    if not events:
        return {"status": "ok", "merged": 0}
    out = ingest_bitable_change_events(events)
    out["idle_seconds"] = cfg.get("idle_seconds")
    return out
