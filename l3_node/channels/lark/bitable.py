"""
Lark 通道 — 多维表（Bitable）同步

将 HR 排行榜 MD 文档解析后导入 Lark 多维表格，供 HR 查看、协作。
支持：自动建列、覆盖更新、群通知。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from l3_node.channels.lark.client import (
    LARK_API_BASE,
    _api_base_from_domain,
    get_tenant_access_token,
)
from l3_node.channels.lark.im import send_text as send_im_text

logger = logging.getLogger(__name__)

DEFAULT_APP_TOKEN = "RJgcbE9LtaBPILsnttmlS8iHgbf"
DEFAULT_TABLE_ID = "tblzQatxI7op9oBp"

DEFAULT_COLUMNS = {
    "候选人": 1,
    "职位": 1,
    "裁决": 1,
    "技术评分": 2,
    "稳定性评分": 2,
    "推荐理由": 1,
    "技术理由": 1,
    "稳定性理由": 1,
    "评审时间": 1,
    "RunID": 1,
    "PDF链接": 1,
}

LOG_TABLE_COLUMNS = {
    "更新时间": 1,
    "职位": 1,
    "更新人数": 2,
    "更新名单": 1,
    "备注": 1,
}

MAX_CANDIDATES_PER_JOB = 10


def _get_api_base() -> str:
    """获取 Lark API 基地址，支持 LARK_DOMAIN/FEISHU_DOMAIN"""
    domain = os.environ.get("LARK_DOMAIN") or os.environ.get("FEISHU_DOMAIN")
    return _api_base_from_domain(domain) or LARK_API_BASE


def _send_lark_chat_notify(token: str, chat_id: str, text: str, api_base: str | None = None) -> bool:
    base = api_base or _get_api_base()
    result = send_im_text(receive_id=chat_id, text=text, receive_id_type="chat_id", token=token, api_base=base)
    return result.get("status") == "success"


def list_bitable_fields(app_token: str = "", table_id: str = "") -> dict[str, Any]:
    """列出多维表的所有列名，用于对照 field_mapping。"""
    app_token = app_token or os.environ.get("LARK_APP_TOKEN") or DEFAULT_APP_TOKEN
    table_id = table_id or os.environ.get("LARK_TABLE_ID") or DEFAULT_TABLE_ID
    try:
        token = get_tenant_access_token()
        base = _get_api_base()
        url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        import requests
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        data = resp.json()
        if data.get("code") != 0:
            return {"success": False, "error": data.get("msg", data)}
        items = data.get("data", {}).get("items", [])
        fields = [{"field_name": f.get("field_name"), "type": f.get("type")} for f in items]
        return {"success": True, "fields": fields}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _ensure_bitable_columns(
    token: str,
    app_token: str,
    table_id: str,
    api_base: str,
    field_mapping: dict[str, str] | None,
) -> list[str]:
    cols_to_ensure: list[tuple[str, int]] = []
    for default_name, ftype in DEFAULT_COLUMNS.items():
        col_name = (field_mapping or {}).get(default_name, default_name)
        cols_to_ensure.append((col_name, ftype))

    import requests
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取列列表失败: {data.get('msg', data)}")
    existing = {f.get("field_name") for f in data.get("data", {}).get("items", []) if f.get("field_name")}

    created: list[str] = []
    create_url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    for col_name, ftype in cols_to_ensure:
        if col_name in existing:
            continue
        payload = {"field_name": col_name, "type": ftype}
        cre = requests.post(create_url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=10)
        cr_data = cre.json()
        if cr_data.get("code") != 0:
            raise RuntimeError(f"创建列「{col_name}」失败: {cr_data.get('msg', cr_data)}")
        created.append(col_name)
        existing.add(col_name)
        logger.info("已创建列: %s (type=%s)", col_name, ftype)
    return created


def _list_all_records(token: str, app_token: str, table_id: str, api_base: str) -> list[dict[str, Any]]:
    import requests
    records: list[dict[str, Any]] = []
    page_token = None
    while True:
        url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"列出记录失败: {data.get('msg', data)}")
        items = data.get("data", {}).get("items", [])
        records.extend(items)
        page_token = data.get("data", {}).get("page_token")
        if not page_token or not items:
            break
    return records


def _list_records_for_job(
    token: str,
    app_token: str,
    table_id: str,
    api_base: str,
    job_name: str,
    job_col: str = "职位",
) -> list[dict[str, Any]]:
    import requests
    records: list[dict] = []
    page_token = None
    while True:
        url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"列出记录失败: {data.get('msg', data)}")
        items = data.get("data", {}).get("items", [])
        for r in items:
            fields = r.get("fields", {})
            if fields.get(job_col) == job_name:
                records.append(r)
        page_token = data.get("data", {}).get("page_token")
        if not page_token or not items:
            break
    return records


def _batch_delete_records(
    token: str,
    app_token: str,
    table_id: str,
    api_base: str,
    record_ids: list[str],
) -> bool:
    if not record_ids:
        return True
    import requests
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    for i in range(0, len(record_ids), 500):
        chunk = record_ids[i : i + 500]
        payload = {"records": chunk}
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=15)
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"批量删除失败: {data.get('msg', data)}")
    return True


def _append_log_record(
    token: str,
    app_token: str,
    log_table_id: str,
    api_base: str,
    job_name: str,
    count: int,
    names: list[str],
    note: str = "",
) -> bool:
    import requests
    from datetime import datetime
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{log_table_id}/records"
    fields = {
        "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "职位": job_name,
        "更新人数": count,
        "更新名单": "、".join(names[:20]) + ("..." if len(names) > 20 else ""),
        "备注": note or "全量覆盖更新",
    }
    payload = {"fields": fields}
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=15)
    data = resp.json()
    return data.get("code") == 0


def _ensure_log_table_columns(token: str, app_token: str, log_table_id: str, api_base: str) -> None:
    import requests
    url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{log_table_id}/fields"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取日志表列失败: {data.get('msg', data)}")
    existing = {f.get("field_name") for f in data.get("data", {}).get("items", []) if f.get("field_name")}
    create_url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{log_table_id}/fields"
    for col_name, ftype in LOG_TABLE_COLUMNS.items():
        if col_name in existing:
            continue
        payload = {"field_name": col_name, "type": ftype}
        cre = requests.post(create_url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=10)
        if cre.json().get("code") != 0:
            logger.warning("创建日志表列「%s」失败，跳过", col_name)


def _extract_pdf_link(cell: str) -> str:
    if not cell:
        return ""
    m = re.search(r"\[原简历\]\((file://[^)]+)\)", cell)
    return m.group(1) if m else ""


def _parse_ranking_md(md_path: str | Path) -> list[dict[str, Any]]:
    path = Path(md_path)
    if not path.exists():
        raise FileNotFoundError(f"MD 文件不存在: {path}")
    job_name = path.parent.name if path.parent.name != "data" else ""
    text = path.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []

    def _parse_table(block: str, headers: list[str], decision: str, reason_col: str) -> None:
        rows = [r for r in block.split("\n") if r.strip().startswith("|")]
        data_rows = []
        for row in rows:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if not cells or all("-" in c for c in cells):
                continue
            if "求职者" in str(cells[0]) or "排名" in str(cells[0]):
                continue
            data_rows.append(cells)

        name_idx = next((i for i, h in enumerate(headers) if "求职者" in h or "姓名" in h), 1)
        score_idx = next((i for i, h in enumerate(headers) if "打分" in h), -1)
        reason_idx = next((i for i, h in enumerate(headers) if reason_col in h or "理由" in h or "原因" in h), -1)
        link_idx = next((i for i, h in enumerate(headers) if "原简历" in h or "Agent" in h), -1)
        edu_idx = next((i for i, h in enumerate(headers) if "学历" in h), -1)
        exp_idx = next((i for i, h in enumerate(headers) if "经验" in h), -1)
        salary_idx = next((i for i, h in enumerate(headers) if "薪资" in h), -1)

        for cells in data_rows:
            if name_idx >= len(cells):
                continue
            candidate = cells[name_idx] if name_idx < len(cells) else ""
            if not candidate:
                continue
            score_val = 0
            if score_idx >= 0 and score_idx < len(cells):
                try:
                    score_val = float(re.search(r"[\d.]+", cells[score_idx]).group())
                except (AttributeError, ValueError):
                    pass
            reason_text = cells[reason_idx] if reason_idx >= 0 and reason_idx < len(cells) else ""
            pdf_link = _extract_pdf_link(cells[link_idx]) if link_idx >= 0 and link_idx < len(cells) else ""
            tech_reason_parts = []
            if edu_idx >= 0 and edu_idx < len(cells) and cells[edu_idx]:
                tech_reason_parts.append(f"学历:{cells[edu_idx]}")
            if exp_idx >= 0 and exp_idx < len(cells) and cells[exp_idx]:
                tech_reason_parts.append(f"经验:{cells[exp_idx]}")
            if salary_idx >= 0 and salary_idx < len(cells) and cells[salary_idx] and cells[salary_idx] != "-":
                tech_reason_parts.append(f"薪资:{cells[salary_idx]}")
            tech_reason = " | ".join(tech_reason_parts)
            records.append({
                "candidate": candidate,
                "job": job_name,
                "review_time": "",
                "run_id": "",
                "decision": decision,
                "tech_score": score_val,
                "hr_score": 0,
                "brief": reason_text,
                "tech_reason": tech_reason,
                "hr_reason": "",
                "pdf_link": pdf_link,
            })

    rec_match = re.search(r"##\s*[^\n]*推荐面试区[^\n]*\n+\|[^\n]+\n\|[^\n]+\n([\s\S]*?)(?=\n##|\Z)", text)
    if rec_match:
        headers = ["排名", "求职者姓名", "学历", "经验", "薪资要求", "打分", "推荐理由", "推荐星级", "原简历 / Agent分析"]
        _parse_table(rec_match.group(1), headers, "推荐面试", "推荐理由")
    rej_match = re.search(r"##\s*[^\n]*淘汰区[^\n]*\n+\|[^\n]+\n\|[^\n]+\n([\s\S]*?)(?=\n##|\Z)", text)
    if rej_match:
        headers = ["排名", "求职者姓名", "学历", "经验", "薪资要求", "打分", "淘汰原因", "推荐星级", "原简历 / Agent分析"]
        _parse_table(rej_match.group(1), headers, "淘汰", "淘汰原因")
    return records


def _parse_md_result(md_path: str | Path) -> dict[str, Any]:
    path = Path(md_path)
    if not path.exists():
        raise FileNotFoundError(f"MD 文件不存在: {path}")
    text = path.read_text(encoding="utf-8")
    out: dict[str, Any] = {
        "candidate": "", "job": "", "review_time": "", "run_id": "",
        "decision": "", "tech_score": 0, "hr_score": 0,
        "brief": "", "tech_reason": "", "hr_reason": "", "pdf_link": "",
    }
    meta_match = re.search(r"##\s*评审元信息\s*\n[\s\S]*?\n\|.+\|\s*\n([\s\S]*?)(?=\n\n|\n##|\Z)", text)
    if meta_match:
        block = meta_match.group(1).strip()
        rows = [r for r in block.split("\n") if r.strip().startswith("|")]
        for row in rows:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if len(cells) >= 2:
                k, v = cells[0], cells[1]
                if "候选人" in k:
                    out["candidate"] = v
                elif "职位" in k:
                    out["job"] = v
                elif "评审时间" in k:
                    out["review_time"] = v
                elif "RunID" in k or "run" in k.lower():
                    out["run_id"] = v
    concl_match = re.search(r"##\s*四、终局结论\s*\n[\s\S]*?\n\|.+\|\s*\n([\s\S]*?)(?=\n\n|\n##|\Z)", text)
    if concl_match:
        block = concl_match.group(1).strip()
        rows = [r for r in block.split("\n") if r.strip().startswith("|")]
        for row in rows:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if len(cells) >= 2:
                k, v = cells[0], cells[1]
                if "裁决" in k:
                    out["decision"] = v
                elif "技术" in k and "评分" in k:
                    try:
                        out["tech_score"] = int(re.search(r"\d+", v).group())
                    except (AttributeError, ValueError):
                        pass
                elif "稳定性" in k and "评分" in k:
                    try:
                        out["hr_score"] = int(re.search(r"\d+", v).group())
                    except (AttributeError, ValueError):
                        pass
                elif "摘要" in k or "brief" in k.lower():
                    out["brief"] = v
    judge_json_match = re.search(r"##\s*三、主理法官[\s\S]*?```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if judge_json_match:
        try:
            obj = json.loads(judge_json_match.group(1))
            out["tech_reason"] = obj.get("tech_reason", out["tech_reason"])
            out["hr_reason"] = obj.get("hr_reason", out["hr_reason"])
            out["brief"] = obj.get("brief") or out["brief"]
            out["decision"] = obj.get("decision") or out["decision"]
            out["tech_score"] = obj.get("tech_score", out["tech_score"])
            out["hr_score"] = obj.get("hr_score", out["hr_score"])
        except json.JSONDecodeError:
            pass
    return out


def _to_lark_fields(parsed: dict[str, Any], field_mapping: dict[str, str] | None) -> dict[str, Any]:
    mapping = field_mapping or {}
    default_cols = {
        "候选人": parsed.get("candidate", ""),
        "职位": parsed.get("job", ""),
        "裁决": parsed.get("decision", ""),
        "技术评分": parsed.get("tech_score", 0),
        "稳定性评分": parsed.get("hr_score", 0),
        "推荐理由": parsed.get("brief", ""),
        "技术理由": parsed.get("tech_reason", ""),
        "稳定性理由": parsed.get("hr_reason", ""),
        "评审时间": parsed.get("review_time", ""),
        "RunID": parsed.get("run_id", ""),
        "PDF链接": parsed.get("pdf_link", ""),
    }
    result = {}
    for col, val in default_cols.items():
        key = mapping.get(col, col)
        result[key] = val
    return result


def sync_bitable_from_md(
    md_path: str,
    app_token: str = "",
    table_id: str = "",
    log_table_id: str = "",
    field_mapping: str = "",
    dry_run: bool = False,
    notify_group: bool = True,
    chat_id: str = "",
    max_per_job: int = 0,
    replace_entire_table: bool = False,
) -> dict[str, Any]:
    """
    将 MD 文档导入到 Lark 多维表格。
    支持排行榜 Summary（推荐面试区 + 淘汰区）或单候选人评审结果格式。
    """
    app_token = app_token or os.environ.get("LARK_APP_TOKEN") or DEFAULT_APP_TOKEN
    replace_entire_table = replace_entire_table or (os.environ.get("LARK_REPLACE_ENTIRE_TABLE", "").lower() in ("1", "true", "yes"))
    table_id = table_id or os.environ.get("LARK_TABLE_ID") or DEFAULT_TABLE_ID
    log_table_id = log_table_id or os.environ.get("LARK_LOG_TABLE_ID", "")
    fm = json.loads(field_mapping) if field_mapping else None
    max_n = max_per_job or MAX_CANDIDATES_PER_JOB
    job_col = (fm or {}).get("职位", "职位")

    try:
        parsed_list = _parse_ranking_md(md_path)
        is_ranking = len(parsed_list) > 0
        if not is_ranking:
            parsed_list = [_parse_md_result(md_path)]
        parsed_list = parsed_list[:max_n]
        job_name = (parsed_list[0].get("job", "") if parsed_list else "") or Path(md_path).parent.name

        if dry_run:
            fields_list = [_to_lark_fields(p, fm) for p in parsed_list]
            return {
                "success": True,
                "record_id": None,
                "record_ids": [],
                "parsed": parsed_list,
                "fields_preview": fields_list,
                "count": len(parsed_list),
                "job_name": job_name,
                "message": f"[dry_run] 解析到 {len(parsed_list)} 条（最多{max_n}），未写入 Lark",
            }

        token = get_tenant_access_token()
        api_base = _get_api_base()

        created_cols: list[str] = []
        try:
            created_cols = _ensure_bitable_columns(token, app_token, table_id, api_base, fm)
        except RuntimeError as e:
            return {"success": False, "record_id": None, "parsed": parsed_list, "error": str(e)}

        deleted_count = 0
        try:
            if replace_entire_table:
                existing = _list_all_records(token, app_token, table_id, api_base)
                if existing:
                    ids = [r.get("record_id") for r in existing if r.get("record_id")]
                    _batch_delete_records(token, app_token, table_id, api_base, ids)
                    deleted_count = len(ids)
                    logger.info("已删除表中全部 %d 条旧记录", deleted_count)
            elif job_name:
                existing = _list_records_for_job(token, app_token, table_id, api_base, job_name, job_col)
                if existing:
                    ids = [r.get("record_id") for r in existing if r.get("record_id")]
                    _batch_delete_records(token, app_token, table_id, api_base, ids)
                    deleted_count = len(ids)
                    logger.info("已删除职位「%s」下 %d 条旧记录", job_name, deleted_count)
        except Exception as e:
            logger.warning("删除旧记录失败（继续写入）: %s", e)

        import requests
        url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        record_ids: list[str] = []
        for parsed in parsed_list:
            fields = _to_lark_fields(parsed, fm)
            resp = requests.post(url, headers=headers, json={"fields": fields}, timeout=15)
            data = resp.json()
            if resp.status_code != 200 or data.get("code") != 0:
                err_msg = data.get("msg", str(data))
                return {
                    "success": False,
                    "record_id": None,
                    "record_ids": record_ids,
                    "parsed": parsed_list,
                    "error": f"Lark API 错误: {err_msg}",
                }
            rec = data.get("data", {}).get("record", {})
            record_ids.append(rec.get("record_id", ""))

        log_written = False
        if log_table_id and job_name:
            try:
                _ensure_log_table_columns(token, app_token, log_table_id, api_base)
                names = [p.get("candidate", "") for p in parsed_list if p.get("candidate")]
                log_written = _append_log_record(
                    token, app_token, log_table_id, api_base,
                    job_name, len(record_ids), names,
                    note=f"覆盖更新，删除{deleted_count}条，写入{len(record_ids)}条",
                )
            except Exception as e:
                logger.warning("写入更新日志失败: %s", e)

        notify_sent = False
        notify_msg = f"【{job_name}】候选人榜单已更新，本次共 {len(record_ids)} 人（最多{max_n}条，覆盖式更新）。请查收。"
        if notify_group:
            target_chat = chat_id or os.environ.get("LARK_CHAT_ID", "")
            if target_chat:
                notify_sent = _send_lark_chat_notify(token, target_chat, notify_msg, api_base)

        msg = f"已覆盖更新 Lark 多维表「{job_name}」，共 {len(record_ids)} 条"
        if deleted_count:
            msg += f"，删除旧记录 {deleted_count} 条"
        if log_written:
            msg += "；已写入更新日志"
        if notify_sent:
            msg += "；已通知 HR"

        return {
            "success": True,
            "record_id": record_ids[0] if record_ids else None,
            "record_ids": record_ids,
            "parsed": parsed_list,
            "created_columns": created_cols,
            "notify_sent": notify_sent,
            "log_written": log_written,
            "deleted_count": deleted_count,
            "count": len(record_ids),
            "job_name": job_name,
            "message": msg,
        }
    except FileNotFoundError as e:
        return {"success": False, "record_id": None, "parsed": [], "error": str(e)}
    except json.JSONDecodeError as e:
        return {"success": False, "record_id": None, "parsed": [], "error": f"field_mapping JSON 解析失败: {e}"}
    except Exception as e:
        logger.exception("sync_bitable_from_md failed")
        return {"success": False, "record_id": None, "parsed": [], "error": str(e)}
