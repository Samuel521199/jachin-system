"""Generic Lark/Feishu Bitable operations for OS assistant workflows.

This module intentionally has no PMO semantics. It provides small data-layer
operations that can be exposed as MCP tools and composed with separate UI/OCR
verification tools when the user wants visible desktop evidence.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from l3_node.channels.lark.client import (
    get_lark_api_base,
    get_tenant_access_token,
    resolve_lark_credentials,
)


def _parse_bitable_url(url: str) -> dict[str, str]:
    u = urlparse((url or "").strip())
    qs = parse_qs(u.query)
    parts = [p for p in u.path.split("/") if p]
    app_token = ""
    node_token = ""
    if "base" in parts:
        idx = parts.index("base")
        if idx + 1 < len(parts):
            app_token = parts[idx + 1]
    if "wiki" in parts:
        idx = parts.index("wiki")
        if idx + 1 < len(parts):
            node_token = parts[idx + 1]
    return {
        "app_token": app_token.strip(),
        "node_token": node_token.strip(),
        "table_id": ((qs.get("table") or [""])[0] or "").strip(),
        "view_id": ((qs.get("view") or [""])[0] or "").strip(),
    }


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _lark_get(api_base: str, token: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    import requests

    url = f"{api_base.rstrip('/')}{path}" if path.startswith("/") else f"{api_base.rstrip('/')}/{path}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=30)
    try:
        return resp.json()
    except Exception:
        return {"code": -1, "msg": resp.text[:500]}


def _lark_post(api_base: str, token: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    import requests

    url = f"{api_base.rstrip('/')}{path}" if path.startswith("/") else f"{api_base.rstrip('/')}/{path}"
    resp = requests.post(url, headers=_headers(token), json=payload, timeout=30)
    try:
        return resp.json()
    except Exception:
        return {"code": -1, "msg": resp.text[:500]}


def _resolve_api_context(
    *,
    app_id: str = "",
    app_secret: str = "",
    api_base: str = "",
) -> tuple[str, str]:
    if app_id and app_secret:
        base = api_base or get_lark_api_base()
        return base, get_tenant_access_token(app_id=app_id, app_secret=app_secret, api_base=base)
    aid, sec, yb = resolve_lark_credentials()
    base = api_base or yb or get_lark_api_base()
    return base, get_tenant_access_token(app_id=aid or None, app_secret=sec or None, api_base=base)


def _resolve_app_token(
    *,
    api_base: str,
    token: str,
    app_token: str = "",
    bitable_url: str = "",
) -> tuple[str, dict[str, str]]:
    parsed = _parse_bitable_url(bitable_url)
    direct = (app_token or parsed.get("app_token") or os.environ.get("LARK_BITABLE_APP_TOKEN") or os.environ.get("LARK_APP_TOKEN") or "").strip()
    if direct:
        return direct, parsed
    node_token = parsed.get("node_token") or ""
    if not node_token:
        raise ValueError("缺少 app_token；请传 app_token、/base/ 链接，或包含 /wiki/{node_token} 的 bitable_url")
    data = _lark_get(api_base, token, "/wiki/v2/spaces/get_node", {"token": node_token})
    if data.get("code") != 0:
        raise RuntimeError(f"解析 wiki 节点失败: {data.get('msg', data)}")
    raw = data.get("data") or {}
    node = raw.get("node") if isinstance(raw.get("node"), dict) else raw
    obj_type = str((node or {}).get("obj_type") or "").lower()
    obj_token = str((node or {}).get("obj_token") or "").strip()
    if obj_type != "bitable" or not obj_token:
        raise ValueError(f"wiki 节点不是 bitable: obj_type={obj_type or '<empty>'}")
    return obj_token, parsed


def _resolve_table_id(
    *,
    api_base: str,
    token: str,
    app_token: str,
    table_id: str = "",
    table_name: str = "",
    parsed_url: dict[str, str] | None = None,
) -> tuple[str, str]:
    tid = (table_id or (parsed_url or {}).get("table_id") or os.environ.get("LARK_BITABLE_TABLE_ID") or os.environ.get("LARK_TABLE_ID") or "").strip()
    if tid:
        return tid, ""
    name = (table_name or "").strip()
    if not name:
        raise ValueError("缺少 table_id；请传 table_id、URL 中的 table=，或 table_name")
    data = _lark_get(api_base, token, f"/bitable/v1/apps/{app_token}/tables", {"page_size": 100})
    if data.get("code") != 0:
        raise RuntimeError(f"列出子表失败: {data.get('msg', data)}")
    tables = data.get("data", {}).get("items") or []
    for item in tables:
        if str(item.get("name") or "").strip() == name:
            return str(item.get("table_id") or "").strip(), str(item.get("name") or "").strip()
    for item in tables:
        if name in str(item.get("name") or ""):
            return str(item.get("table_id") or "").strip(), str(item.get("name") or "").strip()
    raise ValueError(f"未找到子表: {name}")


def _normalize_field_name(s: str) -> str:
    return re.sub(r"[\s_:\-：/\\]+", "", str(s or "").strip()).lower()


def _field_name_by_alias(fields_meta: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in fields_meta:
        name = str(f.get("field_name") or "").strip()
        if name:
            out[_normalize_field_name(name)] = name
    return out


def _map_fields(record: dict[str, Any], fields_meta: list[dict[str, Any]], field_aliases: dict[str, str] | None) -> tuple[dict[str, Any], list[str]]:
    alias = {str(k): str(v) for k, v in (field_aliases or {}).items()}
    by_norm = _field_name_by_alias(fields_meta)
    valid = {str(f.get("field_name") or "").strip() for f in fields_meta if str(f.get("field_name") or "").strip()}
    mapped: dict[str, Any] = {}
    missing: list[str] = []
    for raw_key, value in record.items():
        key = str(raw_key).strip()
        target = alias.get(key) or alias.get(_normalize_field_name(key)) or key
        if target not in valid:
            target = by_norm.get(_normalize_field_name(target), target)
        if target not in valid:
            missing.append(key)
            continue
        mapped[target] = value
    return mapped, missing


def _dangerous_write_bypassed(allow_dangerous: bool) -> bool:
    if allow_dangerous:
        return True
    return (os.environ.get("JACHIN_LARK_BITABLE_WRITE_NO_CONFIRM") or "").strip().lower() in ("1", "true", "yes", "on")


def lark_bitable_list_fields(
    app_token: str = "",
    table_id: str = "",
    table_name: str = "",
    bitable_url: str = "",
    app_id: str = "",
    app_secret: str = "",
    api_base: str = "",
) -> dict[str, Any]:
    base, token = _resolve_api_context(app_id=app_id, app_secret=app_secret, api_base=api_base)
    resolved_app, parsed = _resolve_app_token(api_base=base, token=token, app_token=app_token, bitable_url=bitable_url)
    resolved_table, resolved_name = _resolve_table_id(
        api_base=base,
        token=token,
        app_token=resolved_app,
        table_id=table_id,
        table_name=table_name,
        parsed_url=parsed,
    )
    data = _lark_get(base, token, f"/bitable/v1/apps/{resolved_app}/tables/{resolved_table}/fields")
    if data.get("code") != 0:
        return {"ok": False, "error": data.get("msg", data), "app_token": resolved_app, "table_id": resolved_table}
    fields = data.get("data", {}).get("items") or []
    return {
        "ok": True,
        "app_token": resolved_app,
        "table_id": resolved_table,
        "table_name": resolved_name or table_name,
        "fields": [{"field_name": f.get("field_name"), "type": f.get("type"), "property": f.get("property")} for f in fields],
    }


def lark_bitable_get_records(
    app_token: str = "",
    table_id: str = "",
    table_name: str = "",
    bitable_url: str = "",
    view_id: str = "",
    max_records: int = 20,
    app_id: str = "",
    app_secret: str = "",
    api_base: str = "",
) -> dict[str, Any]:
    base, token = _resolve_api_context(app_id=app_id, app_secret=app_secret, api_base=api_base)
    resolved_app, parsed = _resolve_app_token(api_base=base, token=token, app_token=app_token, bitable_url=bitable_url)
    resolved_table, resolved_name = _resolve_table_id(
        api_base=base,
        token=token,
        app_token=resolved_app,
        table_id=table_id,
        table_name=table_name,
        parsed_url=parsed,
    )
    params: dict[str, Any] = {"page_size": max(1, min(int(max_records or 20), 100))}
    vid = (view_id or parsed.get("view_id") or "").strip()
    if vid:
        params["view_id"] = vid
    data = _lark_get(base, token, f"/bitable/v1/apps/{resolved_app}/tables/{resolved_table}/records", params)
    if data.get("code") != 0:
        return {"ok": False, "error": data.get("msg", data), "app_token": resolved_app, "table_id": resolved_table}
    return {
        "ok": True,
        "app_token": resolved_app,
        "table_id": resolved_table,
        "table_name": resolved_name or table_name,
        "view_id": vid,
        "records": data.get("data", {}).get("items") or [],
    }


def lark_bitable_create_records(
    records_json: str,
    app_token: str = "",
    table_id: str = "",
    table_name: str = "",
    bitable_url: str = "",
    field_aliases_json: str = "{}",
    dry_run: bool = False,
    confirm: bool = False,
    allow_dangerous: bool = False,
    app_id: str = "",
    app_secret: str = "",
    api_base: str = "",
) -> dict[str, Any]:
    try:
        raw_records = json.loads(records_json or "[]")
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"records_json 不是合法 JSON: {exc}"}
    if isinstance(raw_records, dict):
        raw_records = [raw_records]
    if not isinstance(raw_records, list) or not all(isinstance(x, dict) for x in raw_records):
        return {"ok": False, "error": "records_json 必须是对象或对象数组"}
    try:
        aliases = json.loads(field_aliases_json or "{}")
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"field_aliases_json 不是合法 JSON: {exc}"}
    if not isinstance(aliases, dict):
        return {"ok": False, "error": "field_aliases_json 必须是对象"}
    if not dry_run and not confirm and not _dangerous_write_bypassed(allow_dangerous):
        return {
            "ok": False,
            "error": "confirmation_required",
            "confirmation_required": True,
            "message": "写入飞书多维表会修改共享办公数据；请传 confirm=true，或在设置中开启免确认。",
            "records_preview": raw_records,
        }

    base, token = _resolve_api_context(app_id=app_id, app_secret=app_secret, api_base=api_base)
    resolved_app, parsed = _resolve_app_token(api_base=base, token=token, app_token=app_token, bitable_url=bitable_url)
    resolved_table, resolved_name = _resolve_table_id(
        api_base=base,
        token=token,
        app_token=resolved_app,
        table_id=table_id,
        table_name=table_name,
        parsed_url=parsed,
    )
    fields_data = _lark_get(base, token, f"/bitable/v1/apps/{resolved_app}/tables/{resolved_table}/fields")
    if fields_data.get("code") != 0:
        return {"ok": False, "error": fields_data.get("msg", fields_data), "app_token": resolved_app, "table_id": resolved_table}
    fields_meta = fields_data.get("data", {}).get("items") or []
    mapped_records: list[dict[str, Any]] = []
    missing_by_record: list[list[str]] = []
    for rec in raw_records:
        mapped, missing = _map_fields(rec, fields_meta, aliases)
        mapped_records.append(mapped)
        missing_by_record.append(missing)
    if any(missing_by_record):
        return {
            "ok": False,
            "error": "field_mapping_failed",
            "app_token": resolved_app,
            "table_id": resolved_table,
            "table_name": resolved_name or table_name,
            "missing_fields_by_record": missing_by_record,
            "available_fields": [f.get("field_name") for f in fields_meta],
            "mapped_preview": mapped_records,
        }
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "app_token": resolved_app,
            "table_id": resolved_table,
            "table_name": resolved_name or table_name,
            "mapped_preview": mapped_records,
            "count": len(mapped_records),
        }

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for rec in mapped_records:
        data = _lark_post(base, token, f"/bitable/v1/apps/{resolved_app}/tables/{resolved_table}/records", {"fields": rec})
        if data.get("code") != 0:
            errors.append({"fields": rec, "error": data.get("msg", data)})
            continue
        created.append(data.get("data", {}).get("record") or {})
    return {
        "ok": not errors,
        "app_token": resolved_app,
        "table_id": resolved_table,
        "table_name": resolved_name or table_name,
        "count": len(created),
        "record_ids": [r.get("record_id") for r in created if r.get("record_id")],
        "records": created,
        "errors": errors,
    }


__all__ = [
    "lark_bitable_list_fields",
    "lark_bitable_get_records",
    "lark_bitable_create_records",
]
