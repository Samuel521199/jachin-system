"""飞书/Lark 开放平台 Bitable HTTP 辅助（脚本与 BI 共用，无 PMO 业务耦合）。"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _lark_get(api_base: str, token: str, path: str, params: dict | None = None, timeout: int = 120) -> dict[str, Any]:
    import requests

    url = f"{api_base.rstrip('/')}{path}" if path.startswith("/") else f"{api_base}/{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=timeout)
    try:
        return r.json()
    except Exception:
        return {"code": -1, "msg": r.text[:500]}


def _resolve_table_id_by_name(tables: list[dict[str, Any]], needle: str) -> str | None:
    needle = (needle or "").strip()
    if not needle:
        return None
    for t in tables:
        name = (t.get("name") or "").strip()
        if name == needle:
            tid = t.get("table_id")
            return str(tid).strip() if tid else None
    for t in tables:
        name = (t.get("name") or "").strip()
        if needle in name:
            tid = t.get("table_id")
            return str(tid).strip() if tid else None
    return None


def _bitable_list_fields(
    api_base: str, token: str, app_token: str, table_id: str
) -> tuple[list[dict[str, Any]], str | None]:
    data = _lark_get(api_base, token, f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", {})
    if data.get("code") != 0:
        msg = str(data.get("msg") or data)
        logger.warning("[lark_bitable] fields table_id=%r: %s", table_id, msg)
        return [], msg
    return data.get("data", {}).get("items", []) or [], None


def _bitable_list_records(
    api_base: str,
    token: str,
    app_token: str,
    table_id: str,
    max_records: int,
    view_id: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    import requests

    records: list[dict[str, Any]] = []
    page_token = None
    while len(records) < max_records:
        params: dict[str, Any] = {"page_size": min(500, max_records - len(records))}
        if page_token:
            params["page_token"] = page_token
        if view_id:
            params["view_id"] = view_id
        url = f"{api_base.rstrip('/')}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=120)
        try:
            data = r.json()
        except Exception:
            return records, "records: invalid json"
        if data.get("code") != 0:
            msg = str(data.get("msg") or data)
            logger.warning("[lark_bitable] records table_id=%r: %s", table_id, msg)
            return records, msg
        items = data.get("data", {}).get("items", [])
        records.extend(items)
        page_token = data.get("data", {}).get("page_token")
        if not page_token or not items:
            break
    return records[:max_records], None


__all__ = [
    "_lark_get",
    "_resolve_table_id_by_name",
    "_bitable_list_fields",
    "_bitable_list_records",
]
