#!/usr/bin/env python3
"""
将 hierarchical_sprint_req_arch_biz_v1 格式的「树形结构.json」导入飞书多维表格。

策略：
- 目标表需与本项目开发计划表字段名兼容（文本/单多选/人员/日期等按飞书写入格式转换）。
- 跳过公式列（进度、预计、预计人天、实际人天）。
- 每个 Sprint 下每个「需求项」先插入一行父记录（任务列写归纳标题，不加 [需求项] 等前缀），再插入子任务，
  子任务「父记录」指向该父行的 record_id（同表自关联）。
- 源 JSON 中的 record_id / 父记录 指向源表，不会沿用。

用法：
  python scripts/import_tree_json_to_bitable.py --json D:\\zzz\\树形结构.json --dry-run
  python scripts/import_tree_json_to_bitable.py --json D:\\zzz\\树形结构.json --apply
  python scripts/import_tree_json_to_bitable.py --json ... --apply --clear-first
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# 项目根目录
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token

SKIP_FIELDS = frozenset(
    {
        "进度",
        "预计",
        "预计人天",
        "实际人天",
        "父记录",
        "父记录 2",
    }
)


def _normalize_text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        parts: list[str] = []
        for x in v:
            if isinstance(x, dict) and x.get("type") == "text":
                parts.append(str(x.get("text") or ""))
            elif isinstance(x, str):
                parts.append(x)
        return "".join(parts) if parts else None
    return str(v)


def _field_meta_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f["field_name"]: f for f in items if f.get("field_name")}


def _convert_field_for_write(
    name: str,
    raw: Any,
    meta: dict[str, Any] | None,
) -> Any | None:
    if raw is None:
        return None
    if not meta:
        return None
    ft = meta.get("type")
    ui = meta.get("ui_type")
    if ft == 1 and ui == "Text":
        return _normalize_text(raw) or ""
    if ft == 3:  # SingleSelect
        return raw if isinstance(raw, str) else None
    if ft == 4:  # MultiSelect
        if not isinstance(raw, list):
            return None
        out: list[str] = []
        for x in raw:
            if isinstance(x, str):
                out.append(x)
        return out
    if ft == 5:  # DateTime
        if isinstance(raw, (int, float)):
            return int(raw)
        return None
    if ft == 11:  # User
        if not isinstance(raw, list):
            return None
        return [{"id": x["id"]} for x in raw if isinstance(x, dict) and x.get("id")]
    if ft == 17:  # Attachment
        if not isinstance(raw, list):
            return None
        # 仅传递可复用的 token
        cleaned = []
        for x in raw:
            if isinstance(x, dict) and x.get("file_token"):
                cleaned.append(
                    {
                        "file_token": x["file_token"],
                        "name": x.get("name", ""),
                        "type": x.get("type", "application/octet-stream"),
                        "size": x.get("size", 0),
                    }
                )
        return cleaned or None
    if ft == 18:
        return None
    return None


def _build_child_fields(
    fields_in: dict[str, Any],
    field_by_name: dict[str, dict[str, Any]],
    parent_record_id: str,
    parent_link_name: str,
    _category_tag: str,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, raw in fields_in.items():
        if name in SKIP_FIELDS:
            continue
        meta = field_by_name.get(name)
        if not meta:
            continue
        if meta.get("type") == 20:
            continue
        val = _convert_field_for_write(name, raw, meta)
        if val is None and meta.get("type") not in (1,):
            continue
        if name == "任务" and meta.get("type") == 1:
            # 与源 JSON 一致，不追加 [架构]/[业务]（层级由父记录与分组表达）
            out[name] = _normalize_text(raw) or ""
        else:
            if val is not None:
                out[name] = val
    # 写入单向关联：使用 record_id 字符串数组（与读接口返回的 object 不同）
    out[parent_link_name] = [parent_record_id]
    return out


def _list_all_record_ids(
    api_base: str, token: str, app_token: str, table_id: str
) -> list[str]:
    import requests

    ids: list[str] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=60,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(data.get("msg", data))
        items = data.get("data", {}).get("items") or []
        for it in items:
            rid = it.get("record_id")
            if rid:
                ids.append(rid)
        page_token = (data.get("data") or {}).get("page_token")
        if not page_token or not items:
            break
    return ids


def _batch_delete(
    api_base: str, token: str, app_token: str, table_id: str, record_ids: list[str]
) -> None:
    import requests

    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    for i in range(0, len(record_ids), 500):
        chunk = record_ids[i : i + 500]
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"records": chunk},
            timeout=60,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(data.get("msg", data))


def _batch_create(
    api_base: str, token: str, app_token: str, table_id: str, records: list[dict[str, Any]]
) -> list[str]:
    import requests

    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"records": records},
        timeout=120,
    )
    data = r.json()
    if data.get("code") != 0:
        code = data.get("code")
        msg = data.get("msg", data)
        if code == 91403:
            raise RuntimeError(
                f"{msg} (code={code})：当前应用对该多维表格无写入权限。"
                "请在飞书开放平台确认应用已开通多维表格相关权限，并在目标 Base 中将应用/机器人设为「可编辑」协作者后重试。"
            )
        raise RuntimeError(f"{msg} (code={code})")
    items = (data.get("data") or {}).get("records") or []
    return [x.get("record_id") for x in items if x.get("record_id")]


def main() -> None:
    ap = argparse.ArgumentParser(description="树形结构 JSON → 飞书多维表")
    ap.add_argument("--json", required=True, help="树形结构.json 路径")
    ap.add_argument("--app-token", default="SVXsbpu75atAF6sgbpElGcgPgGf")
    ap.add_argument("--table-id", default="tblk4vLy8sZxkU2G")
    ap.add_argument("--parent-link-field", default="父记录", help="子任务挂载的关联列名")
    ap.add_argument("--dry-run", action="store_true", help="只统计与打印样例，不写表")
    ap.add_argument("--apply", action="store_true", help="实际写入（与 --dry-run 互斥）")
    ap.add_argument(
        "--clear-first",
        action="store_true",
        help="写入前先删除目标表全部记录（谨慎）",
    )
    args = ap.parse_args()
    if not args.dry_run and not args.apply:
        print("请指定 --dry-run 或 --apply", file=sys.stderr)
        sys.exit(2)

    path = Path(args.json)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("format") != "hierarchical_sprint_req_arch_biz_v1":
        print("警告: format 非 hierarchical_sprint_req_arch_biz_v1，仍尝试解析 tree", file=sys.stderr)

    tree = doc.get("tree") or []
    api_base = get_lark_api_base().rstrip("/")
    token = get_tenant_access_token()

    import requests

    fields_url = f"{api_base}/bitable/v1/apps/{args.app_token}/tables/{args.table_id}/fields"
    fr = requests.get(
        fields_url, headers={"Authorization": f"Bearer {token}"}, timeout=30
    )
    fd = fr.json()
    if fd.get("code") != 0:
        print("获取字段失败:", fd, file=sys.stderr)
        sys.exit(1)
    items = fd.get("data", {}).get("items") or []
    field_by_name = _field_meta_map(items)
    if args.parent_link_field not in field_by_name:
        print(f"目标表无关联列「{args.parent_link_field}」", file=sys.stderr)
        sys.exit(1)

    parents_payload: list[dict[str, Any]] = []
    children_plan: list[tuple[str, dict[str, Any]]] = []
    # (parent_key -> will fill record_id after create) 使用顺序列表对齐 batch 返回

    ParentKey = tuple[str, str]  # sprint, 需求项名
    order_keys: list[ParentKey] = []

    for sp in tree:
        sprint_name = sp.get("Sprint") or sp.get("Sprint_raw") or ""
        for req in sp.get("需求项") or []:
            req_name = req.get("需求项") or ""
            key = (str(sprint_name), str(req_name))
            order_keys.append(key)
            pfields: dict[str, Any] = {
                "任务": req_name,
            }
            if "Sprint" in field_by_name:
                pfields["Sprint"] = [str(sprint_name)] if sprint_name else []
            parents_payload.append({"fields": pfields})

            # 子任务：架构与工程、业务逻辑
            for cat in ("架构与工程", "业务逻辑"):
                for rec in req.get(cat) or []:
                    raw_fields = (rec.get("fields") or {}).copy()
                    children_plan.append(
                        (
                            f"{key[0]}||{key[1]}",
                            {
                                "fields_in": raw_fields,
                                "category": cat,
                            },
                        )
                    )

    print(
        f"统计: 需求项父行 {len(parents_payload)}，子任务 {len(children_plan)}，合计记录 {len(parents_payload) + len(children_plan)}"
    )
    if args.dry_run:
        if parents_payload:
            print("父行样例 fields:", json.dumps(parents_payload[0]["fields"], ensure_ascii=False))
        for i, (pk, plan) in enumerate(children_plan[:2]):
            cf = _build_child_fields(
                plan["fields_in"],
                field_by_name,
                "recPLACEHOLDER",
                args.parent_link_field,
                plan["category"],
            )
            print(f"子任务样例{i}:", json.dumps(cf, ensure_ascii=False)[:1200])
        return

    if args.clear_first:
        rids = _list_all_record_ids(api_base, token, args.app_token, args.table_id)
        print(f"--clear-first: 删除已有 {len(rids)} 条记录")
        if rids:
            _batch_delete(api_base, token, args.app_token, args.table_id, rids)

    # 创建父行
    parent_ids: list[str] = []
    for i in range(0, len(parents_payload), 500):
        chunk = parents_payload[i : i + 500]
        parent_ids.extend(_batch_create(api_base, token, args.app_token, args.table_id, chunk))

    if len(parent_ids) != len(order_keys):
        print(
            f"父行创建数量不一致: 期望 {len(order_keys)} 实际 {len(parent_ids)}",
            file=sys.stderr,
        )
        sys.exit(1)

    key_to_parent_id: dict[str, str] = {}
    for k, rid in zip(order_keys, parent_ids, strict=True):
        key_to_parent_id[f"{k[0]}||{k[1]}"] = rid

    child_records: list[dict[str, Any]] = []
    for pk, plan in children_plan:
        prid = key_to_parent_id.get(pk)
        if not prid:
            print("找不到父 record_id:", pk, file=sys.stderr)
            sys.exit(1)
        fields_out = _build_child_fields(
            plan["fields_in"],
            field_by_name,
            prid,
            args.parent_link_field,
            plan["category"],
        )
        child_records.append({"fields": fields_out})

    created_children = 0
    for i in range(0, len(child_records), 500):
        chunk = child_records[i : i + 500]
        out_ids = _batch_create(api_base, token, args.app_token, args.table_id, chunk)
        created_children += len(out_ids)

    print(f"完成: 父行 {len(parent_ids)}，子任务 {created_children} 已写入。")


if __name__ == "__main__":
    main()
