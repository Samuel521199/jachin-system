"""
原子 Tool: atom_lark_bitable_sync
将多 Agent 评审结果 MD 文档解析后，自动导入到 Lark 多维表格，供 HR 查看、修改、协作。

功能：
  - 写入前自动检查列是否存在，不存在则创建（候选人、职位、裁决、技术评分等）
  - 一键执行，无需提前手动建列
  - 写入完成后向绑定群发送「新一批的候选人信息已更新完成，请查收」

前置条件：
  - 环境变量 LARK_APP_ID、LARK_APP_SECRET
  - 可选：LARK_APP_TOKEN、LARK_TABLE_ID、LARK_CHAT_ID（通知群聊 ID）
  - Lark 应用需有 base:record:create、base:field:create、im:message（发送群消息）权限
  - 多维表需对应用开放可编辑；机器人需已添加到目标群组
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认多维表（从用户提供的 URL 解析）
# https://ssgkm409t6q5.sg.larksuite.com/base/RJgcbE9LtaBPILsnttmlS8iHgbf?table=tblzQatxI7op9oBp
DEFAULT_APP_TOKEN = "RJgcbE9LtaBPILsnttmlS8iHgbf"
DEFAULT_TABLE_ID = "tblzQatxI7op9oBp"

# Lark 国际版 API 域名；飞书中国版需设置 LARK_USE_FEISHU=1
LARK_API_BASE = "https://open.larksuite.com/open-apis"


def _get_api_base() -> str:
    """根据 LARK_USE_FEISHU 返回飞书中国版或 Lark 国际版 API 地址"""
    if os.environ.get("LARK_USE_FEISHU", "").lower() in ("1", "true", "yes"):
        return "https://open.feishu.cn/open-apis"
    return LARK_API_BASE

# 确保 l3_node 可导入（plugin 脚本可能从 plugin 目录启动）
def _ensure_l3_importable() -> None:
    import sys
    if "l3_node" in sys.modules:
        return
    from pathlib import Path
    _p = Path(__file__).resolve()
    for _ in range(6):
        _p = _p.parent
        if (_p / "l3_node").is_dir():
            _s = str(_p)
            if _s not in sys.path:
                sys.path.insert(0, _s)
            break

# 默认列定义：列名 -> type (1=文本, 2=数字)
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

# 更新日志表列定义（commit 式记录）
LOG_TABLE_COLUMNS = {
    "更新时间": 1,
    "职位": 1,
    "更新人数": 2,
    "更新名单": 1,
    "备注": 1,
}

MAX_CANDIDATES_PER_JOB = 10


def _send_lark_chat_notify(token: str, chat_id: str, text: str) -> bool:
    """向 Lark 群聊发送文本消息。返回是否成功。委托 channels.lark。"""
    _ensure_l3_importable()
    from l3_node.channels.lark import send_im_text

    result = send_im_text(
        receive_id=chat_id,
        text=text,
        receive_id_type="chat_id",
        token=token,
    )
    return result.get("status") == "success"


def _ensure_dotenv_loaded() -> None:
    """若 LARK 相关变量未设置，尝试从项目根 .env 加载"""
    if os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID"):
        return
    try:
        from dotenv import load_dotenv
        # tools/ -> com.jachin.hr.recruitment/ -> plugin/
        plugin_root = Path(__file__).resolve().parent.parent.parent
        env_path = plugin_root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        else:
            load_dotenv()  # 从 cwd 往上找
    except ImportError:
        pass


def _get_tenant_access_token() -> str:
    """获取 Lark tenant_access_token。HR 招聘场景使用 HR_LARK_APP_*（与通用 LARK_APP_ID 分离）。"""
    _ensure_dotenv_loaded()
    _ensure_l3_importable()
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token, resolve_hr_lark_credentials

    aid, sec, yb = resolve_hr_lark_credentials()
    base = yb or get_lark_api_base()
    if aid and sec:
        return get_tenant_access_token(app_id=aid, app_secret=sec, api_base=base)
    return get_tenant_access_token()


def _list_bitable_fields_internal(
    token: str, app_token: str, table_id: str
) -> list[dict[str, Any]]:
    """内部：获取多维表字段列表（含 type），供 sync 按类型格式化写入。"""
    base = _get_api_base()
    url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    import requests
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        return []
    return data.get("data", {}).get("items", [])


def list_bitable_fields(app_token: str = "", table_id: str = "") -> dict[str, Any]:
    """列出多维表的所有列名，用于对照 field_mapping。"""
    _ensure_dotenv_loaded()
    app_token = app_token or os.environ.get("LARK_APP_TOKEN") or DEFAULT_APP_TOKEN
    table_id = table_id or os.environ.get("LARK_TABLE_ID") or DEFAULT_TABLE_ID
    try:
        token = _get_tenant_access_token()
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
    field_mapping: dict[str, str] | None,
) -> list[str]:
    """
    确保多维表有所需列，不存在则创建。
    返回创建的新列名列表（用于日志）。
    """
    import requests

    # 获取待写入的列名及类型：(列名, type)
    cols_to_ensure: list[tuple[str, int]] = []
    for default_name, ftype in DEFAULT_COLUMNS.items():
        col_name = (field_mapping or {}).get(default_name, default_name)
        cols_to_ensure.append((col_name, ftype))

    # 获取现有列
    url = f"{LARK_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取列列表失败: {data.get('msg', data)}")
    existing = {f.get("field_name") for f in data.get("data", {}).get("items", []) if f.get("field_name")}

    created: list[str] = []
    create_url = f"{LARK_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
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


def _list_all_records(
    token: str,
    app_token: str,
    table_id: str,
) -> list[dict[str, Any]]:
    """列出主表中所有记录"""
    import requests
    records: list[dict[str, Any]] = []
    page_token = None
    base = _get_api_base()
    while True:
        url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
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
    job_name: str,
    job_col: str = "职位",
) -> list[dict[str, Any]]:
    """列出主表中 职位=job_name 的所有记录（用于更新前删除）"""
    import requests
    base = _get_api_base()
    records: list[dict] = []
    page_token = None
    while True:
        url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
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
    record_ids: list[str],
) -> bool:
    """批量删除记录（单次最多 500 条）"""
    if not record_ids:
        return True
    import requests
    base = _get_api_base()
    url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    for i in range(0, len(record_ids), 500):
        chunk = record_ids[i : i + 500]
        payload = {"records": chunk}
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"批量删除失败: {data.get('msg', data)}")
    return True


def _ensure_bitable_columns_from_csv(
    token: str,
    app_token: str,
    table_id: str,
    column_names: list[str],
    sample_row: dict[str, Any] | None = None,
) -> list[str]:
    """
    根据 CSV 列名确保多维表有所需列，不存在则创建。
    返回创建的新列名列表。
    """
    import requests
    if not column_names:
        return []
    # 推断类型：1=文本, 2=数字
    def _guess_type(col: str, val: Any) -> int:
        v = val if sample_row is None else (sample_row.get(col) if isinstance(sample_row, dict) else None)
        if v is not None:
            if isinstance(v, (int, float)):
                return 2
            s = str(v).strip()
            if s.replace(".", "").replace("-", "").replace("%", "").replace(",", "").isdigit():
                return 2
        return 1

    cols_to_ensure = [(c, _guess_type(c, sample_row.get(c) if sample_row else None)) for c in column_names]

    base = _get_api_base()
    url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取列列表失败: {data.get('msg', data)}")
    existing = {f.get("field_name") for f in data.get("data", {}).get("items", []) if f.get("field_name")}

    created: list[str] = []
    create_url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
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


def sync_csv_to_bitable(
    csv_path: str,
    app_token: str = "",
    table_id: str = "",
    replace_table: bool = True,
    ensure_columns: bool = True,
    dry_run: bool = False,
    text_columns: list[str] | None = None,
    field_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    将 CSV 文件同步到 Lark 多维表格。供 BI 日报等场景使用。

    :param csv_path: CSV 文件路径
    :param app_token: 多维表 app_token（base ID），空则从 LARK_APP_TOKEN 读取
    :param table_id: 目标表 ID，必填
    :param replace_table: 是否先清空表再写入，默认 True
    :param ensure_columns: 是否根据 CSV 表头自动创建列，默认 True
    :param dry_run: 仅解析不写入
    :param text_columns: 必须按文本发送的列名列表，避免纯数字被误转 float 导致 TextFieldConvFail
    :param field_mapping: CSV 列名 -> Lark 字段名映射，用于 FieldNameNotFound（Lark 表字段名与 CSV 不一致时）
    :return: {"success": bool, "count": int, "error": str|None}
    """
    import csv as csv_module
    app_token = app_token or os.environ.get("LARK_APP_TOKEN") or os.environ.get("BI_LARK_APP_TOKEN")
    table_id = table_id or os.environ.get("LARK_TABLE_ID")

    if not app_token or not table_id:
        return {"success": False, "count": 0, "error": "app_token 与 table_id 必填，或设置 LARK_APP_TOKEN/LARK_TABLE_ID / BI_LARK_APP_TOKEN"}

    path = Path(csv_path)
    if not path.exists():
        return {"success": False, "count": 0, "error": f"CSV 文件不存在: {csv_path}"}

    try:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv_module.DictReader(f)
            rows = list(reader)
        if not rows:
            return {"success": True, "count": 0, "message": "CSV 无数据行"}

        fieldnames = list(rows[0].keys())
        if dry_run:
            return {"success": True, "count": len(rows), "fields_preview": fieldnames, "message": f"[dry_run] 解析到 {len(rows)} 行，未写入"}

        _ensure_dotenv_loaded()
        token = _get_tenant_access_token()

        text_cols = set((text_columns or []))
        col_map = field_mapping or {}

        if ensure_columns:
            cols_to_ensure = [col_map.get(c, c) for c in fieldnames]
            # 样本行键名需与创建列名一致（Lark 侧）
            sample = None
            if rows:
                sample = {col_map.get(k, k): v for k, v in rows[0].items() if k in fieldnames}
            _ensure_bitable_columns_from_csv(token, app_token, table_id, cols_to_ensure, sample)

        # 获取 Lark 字段类型，按类型格式化写入值（解决 TextFieldConvFail：数字列误传 float 到文本列等）
        lark_items = _list_bitable_fields_internal(token, app_token, table_id)
        lark_field_types: dict[str, int] = {}
        for f in lark_items:
            fn = f.get("field_name")
            if fn:
                try:
                    lark_field_types[fn] = int(f.get("type") or 1)
                except (TypeError, ValueError):
                    lark_field_types[fn] = 1

        valid_lark_fields = frozenset(lark_field_types.keys())
        csv_only_cols = sorted(
            {col_map.get(c, c) for c in fieldnames if col_map.get(c, c) not in valid_lark_fields}
        )
        if csv_only_cols:
            logger.warning(
                "sync_csv_to_bitable: %d 个 CSV 列在多维表中不存在，已跳过写入（可设 ensure_columns=true 自动建列，或配置 field_mapping）。"
                " 列名示例: %s%s",
                len(csv_only_cols),
                csv_only_cols[:20],
                " ..." if len(csv_only_cols) > 20 else "",
            )

        import requests
        if replace_table:
            existing = _list_all_records(token, app_token, table_id)
            if existing:
                ids = [r.get("record_id") for r in existing if r.get("record_id")]
                _batch_delete_records(token, app_token, table_id, ids)
                logger.info("已清空表 %s，删除 %d 条", table_id, len(ids))

        base = _get_api_base()
        url = f"{base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        record_ids: list[str] = []
        for row in rows:
            fields = {}
            for k, v in row.items():
                if k not in fieldnames:
                    continue
                lark_key = col_map.get(k, k)  # 使用映射后的 Lark 字段名
                if lark_key not in valid_lark_fields:
                    continue
                s = (v or "").strip()
                ft = lark_field_types[lark_key]  # 1=文本, 2=数字, 5=日期
                # 数字列空串：不提交该字段（勿传 ""，否则 Lark NumberFieldConvFail）
                if ft == 2 and not s:
                    continue
                # 数字列：CSV 占位「-」表示无数据，勿提交字符串（NumberFieldConvFail）；省略字段即留空
                if ft == 2 and s in ("-", "—", "－", "N/A", "n/a", "--"):
                    continue
                # 日期类型(5)：必须发毫秒时间戳(整数)；解析失败或空值则省略，勿传字符串（否则 DatetimeFieldConvFail）
                if ft == 5:
                    if not s:
                        continue
                    ts_val = None
                    try:
                        from datetime import datetime

                        # 仅当原串为「纯数字时间戳」时才走数值分支。
                        num_s = s.replace(",", "").strip()
                        looks_like_epoch = num_s and (
                            "-" not in s
                            and "/" not in s
                            and ":" not in s
                            and (num_s.replace(".", "", 1).isdigit())
                        )
                        if looks_like_epoch:
                            try:
                                raw = int(float(num_s))
                                if 1000000000000 <= raw <= 9999999999999:
                                    ts_val = raw
                                elif 1e9 <= raw < 1e10:
                                    ts_val = raw * 1000
                            except (ValueError, OverflowError):
                                pass
                        if ts_val is None:
                            for fmt in (
                                "%Y-%m-%d %H:%M:%S",
                                "%Y-%m-%d %H:%M",
                                "%Y-%m-%d",
                                "%Y/%m/%d %H:%M:%S",
                                "%Y/%m/%d %H:%M",
                                "%Y/%m/%d",
                                "%Y%m%d",
                            ):
                                try:
                                    dt = datetime.strptime(s[:19], fmt)
                                    ts_val = int(dt.timestamp() * 1000)
                                    break
                                except ValueError:
                                    pass
                        if ts_val is not None:
                            fields[lark_key] = ts_val
                        else:
                            logger.debug(
                                "sync_csv_to_bitable: 日期列 %r 值 %r 无法解析，已省略",
                                lark_key,
                                s[:80],
                            )
                    except (ValueError, OverflowError):
                        logger.debug(
                            "sync_csv_to_bitable: 日期列 %r 解析异常，已省略",
                            lark_key,
                        )
                    continue
                if k in text_cols or ft != 2:
                    fields[lark_key] = s
                elif ft == 2:
                    num_ok = (
                        s.replace(".", "", 1).replace("-", "", 1).replace("%", "").replace(",", "").isdigit()
                        or (
                            s
                            and s[-1] == "%"
                            and s[:-1].replace(".", "").replace("-", "").replace(",", "").isdigit()
                        )
                    )
                    if num_ok:
                        try:
                            fields[lark_key] = float(s.replace("%", "").replace(",", ""))
                        except ValueError:
                            logger.debug(
                                "sync_csv_to_bitable: 数字列 %r 值 %r 转 float 失败，已省略",
                                lark_key,
                                s[:80],
                            )
                    else:
                        logger.debug(
                            "sync_csv_to_bitable: 数字列 %r 值 %r 非数字格式，已省略",
                            lark_key,
                            s[:80],
                        )
                else:
                    fields[lark_key] = s
            if not fields:
                logger.warning(
                    "sync_csv_to_bitable: 跳过无有效字段的行（请检查列名是否与多维表一致，或配置 field_mapping）"
                )
                continue
            resp = requests.post(url, headers=headers, json={"fields": fields}, timeout=15)
            data = resp.json()
            if resp.status_code != 200 or data.get("code") != 0:
                return {"success": False, "count": len(record_ids), "error": f"Lark API: {data.get('msg', str(data))}"}
            rec = data.get("data", {}).get("record", {})
            record_ids.append(rec.get("record_id", ""))

        return {"success": True, "count": len(record_ids), "record_ids": record_ids}
    except Exception as e:
        logger.exception("sync_csv_to_bitable failed")
        return {"success": False, "count": 0, "error": str(e)}


def _append_log_record(
    token: str,
    app_token: str,
    log_table_id: str,
    job_name: str,
    count: int,
    names: list[str],
    note: str = "",
) -> bool:
    """向更新日志表追加一条 commit 式记录"""
    import requests
    from datetime import datetime
    url = f"{LARK_API_BASE}/bitable/v1/apps/{app_token}/tables/{log_table_id}/records"
    fields = {
        "更新时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "职位": job_name,
        "更新人数": count,
        "更新名单": "、".join(names[:20]) + ("..." if len(names) > 20 else ""),
        "备注": note or "全量覆盖更新",
    }
    payload = {"fields": fields}
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    data = resp.json()
    return data.get("code") == 0


def _ensure_log_table_columns(
    token: str,
    app_token: str,
    log_table_id: str,
) -> None:
    """确保更新日志表有所需列"""
    import requests
    url = f"{LARK_API_BASE}/bitable/v1/apps/{app_token}/tables/{log_table_id}/fields"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取日志表列失败: {data.get('msg', data)}")
    existing = {f.get("field_name") for f in data.get("data", {}).get("items", []) if f.get("field_name")}
    create_url = f"{LARK_API_BASE}/bitable/v1/apps/{app_token}/tables/{log_table_id}/fields"
    for col_name, ftype in LOG_TABLE_COLUMNS.items():
        if col_name in existing:
            continue
        payload = {"field_name": col_name, "type": ftype}
        cre = requests.post(create_url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=10)
        if cre.json().get("code") != 0:
            logger.warning("创建日志表列「%s」失败，跳过", col_name)


def _extract_pdf_link(cell: str) -> str:
    """从「原简历 / Agent分析」单元格提取 [原简历](url) 中的 url"""
    if not cell:
        return ""
    m = re.search(r"\[原简历\]\((file://[^)]+)\)", cell)
    return m.group(1) if m else ""


def _parse_ranking_md(md_path: str | Path) -> list[dict[str, Any]]:
    """
    解析排行榜 Summary MD 文档（推荐面试区 + 淘汰区 表格格式）。
    职位从文件路径的父目录名推导（如 java工程师_杭州 10-15K）。
    返回多条记录，每条对应一个候选人。
    """
    path = Path(md_path)
    if not path.exists():
        raise FileNotFoundError(f"MD 文件不存在: {path}")

    # 职位从路径推导：data/java工程师_杭州 10-15K/排行榜_Summary.md -> java工程师_杭州 10-15K
    job_name = path.parent.name if path.parent.name != "data" else ""

    text = path.read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []

    def _parse_table(block: str, headers: list[str], decision: str, reason_col: str) -> None:
        rows = [r for r in block.split("\n") if r.strip().startswith("|")]
        # 跳过表头分隔行（------）
        data_rows = []
        for row in rows:
            cells = [c.strip() for c in row.split("|") if c.strip()]
            if not cells or all("-" in c for c in cells):
                continue
            if "求职者" in str(cells[0]) or "排名" in str(cells[0]):
                continue  # 表头行
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

    # 推荐面试区：匹配 ## ... 推荐面试区 ... 后的表格（表头行 + 分隔行后为数据）
    rec_match = re.search(
        r"##\s*[^\n]*推荐面试区[^\n]*\n+\|[^\n]+\n\|[^\n]+\n([\s\S]*?)(?=\n##|\Z)",
        text,
    )
    if rec_match:
        headers = ["排名", "求职者姓名", "学历", "经验", "薪资要求", "打分", "推荐理由", "推荐星级", "原简历 / Agent分析"]
        _parse_table(rec_match.group(1), headers, "推荐面试", "推荐理由")

    # 淘汰区（与推荐区结构一致：排名|求职者姓名|学历|经验|...）
    rej_match = re.search(
        r"##\s*[^\n]*淘汰区[^\n]*\n+\|[^\n]+\n\|[^\n]+\n([\s\S]*?)(?=\n##|\Z)",
        text,
    )
    if rej_match:
        headers = ["排名", "求职者姓名", "学历", "经验", "薪资要求", "打分", "淘汰原因", "推荐星级", "原简历 / Agent分析"]
        _parse_table(rej_match.group(1), headers, "淘汰", "淘汰原因")

    return records


def _parse_md_result(md_path: str | Path) -> dict[str, Any]:
    """
    解析多 Agent 评审结果 MD 文档（旧格式，单候选人），提取：
    - 评审元信息（候选人、职位、评审时间、RunID）
    - 终局结论（裁决、技术评分、稳定性评分、一句话摘要）
    - 技术理由、稳定性理由（从 JSON 块提取）
    """
    path = Path(md_path)
    if not path.exists():
        raise FileNotFoundError(f"MD 文件不存在: {path}")

    text = path.read_text(encoding="utf-8")
    out: dict[str, Any] = {
        "candidate": "",
        "job": "",
        "review_time": "",
        "run_id": "",
        "decision": "",
        "tech_score": 0,
        "hr_score": 0,
        "brief": "",
        "tech_reason": "",
        "hr_reason": "",
        "pdf_link": "",
    }

    # 解析「评审元信息」表格：| 字段 | 值 |
    meta_match = re.search(
        r"##\s*评审元信息\s*\n[\s\S]*?\n\|.+\|\s*\n([\s\S]*?)(?=\n\n|\n##|\Z)",
        text,
    )
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

    # 解析「终局结论」表格
    concl_match = re.search(
        r"##\s*四、终局结论\s*\n[\s\S]*?\n\|.+\|\s*\n([\s\S]*?)(?=\n\n|\n##|\Z)",
        text,
    )
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

    # 解析「主理法官 (Judge) 终局裁决」JSON 块，补齐 tech_reason、hr_reason
    judge_json_match = re.search(
        r"##\s*三、主理法官[\s\S]*?```(?:json)?\s*(\{[\s\S]*?\})\s*```",
        text,
    )
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


def _to_lark_fields(
    parsed: dict[str, Any],
    field_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    将解析结果映射为 Lark 多维表字段。
    默认列名（需与 Lark 表格列名一致）：
      候选人、职位、裁决、技术评分、稳定性评分、推荐理由、技术理由、稳定性理由、评审时间、RunID、PDF链接
    """
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
    # 允许通过 field_mapping 覆盖列名：{"候选人": "姓名"} 表示用「姓名」列存候选人
    result = {}
    for col, val in default_cols.items():
        key = mapping.get(col, col)
        result[key] = val
    return result


def atom_lark_bitable_sync(
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
    支持两种格式：
    1. 排行榜 Summary（推荐面试区 + 淘汰区 表格）→ 按职位覆盖更新，每职位最多 10 条
    2. 多 Agent 评审结果（评审元信息 + 终局结论）→ 单人写入

    更新策略：每职位主表最多保留 10 条候选人，每次同步为覆盖（先删后写），不累加。
    可选：配置 LARK_LOG_TABLE_ID，在更新日志表中记录每次同步（类似 Git commit）。

    :param md_path: MD 文档路径
    :param app_token: Lark 多维表 app_token（base ID）
    :param table_id: 主表 ID（候选人列表）
    :param log_table_id: 更新日志表 ID，不填则用 LARK_LOG_TABLE_ID 环境变量
    :param field_mapping: JSON 字符串，覆盖列名映射
    :param dry_run: 仅解析不写入
    :param notify_group: 写入成功后是否向绑定群发送通知
    :param chat_id: 通知群聊 ID
    :param max_per_job: 每职位最多保留条数，0 则用默认 10
    :param replace_entire_table: 为 True 时删除表中全部记录再写入，确保新数据从第一行开始
    :return: {"success": bool, "record_ids": list, "parsed": list, "error": str|None}
    """
    app_token = app_token or os.environ.get("LARK_APP_TOKEN") or DEFAULT_APP_TOKEN
    replace_entire_table = replace_entire_table or (os.environ.get("LARK_REPLACE_ENTIRE_TABLE", "").lower() in ("1", "true", "yes"))
    table_id = table_id or os.environ.get("LARK_TABLE_ID") or DEFAULT_TABLE_ID
    log_table_id = log_table_id or os.environ.get("LARK_LOG_TABLE_ID", "")
    fm = json.loads(field_mapping) if field_mapping else None
    max_n = max_per_job or MAX_CANDIDATES_PER_JOB
    job_col = (fm or {}).get("职位", "职位")

    try:
        # 优先尝试排行榜格式（多人）
        parsed_list = _parse_ranking_md(md_path)
        is_ranking = len(parsed_list) > 0

        if not is_ranking:
            parsed_list = [_parse_md_result(md_path)]

        # 每职位最多 max_n 条，按推荐优先（推荐面试区在前）
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

        token = _get_tenant_access_token()

        # 1. 确保主表列存在
        created_cols: list[str] = []
        try:
            created_cols = _ensure_bitable_columns(token, app_token, table_id, fm)
        except RuntimeError as e:
            return {"success": False, "record_id": None, "parsed": parsed_list, "error": str(e)}

        # 2. 删除旧记录：replace_entire_table 时删除全部，否则仅删除该职位的（确保新数据从第一行开始）
        deleted_count = 0
        try:
            if replace_entire_table:
                existing = _list_all_records(token, app_token, table_id)
                if existing:
                    ids = [r.get("record_id") for r in existing if r.get("record_id")]
                    _batch_delete_records(token, app_token, table_id, ids)
                    deleted_count = len(ids)
                    logger.info("已删除表中全部 %d 条旧记录（replace_entire_table），新数据将从第一行开始", deleted_count)
            elif job_name:
                existing = _list_records_for_job(token, app_token, table_id, job_name, job_col)
                if existing:
                    ids = [r.get("record_id") for r in existing if r.get("record_id")]
                    _batch_delete_records(token, app_token, table_id, ids)
                    deleted_count = len(ids)
                    logger.info("已删除职位「%s」下 %d 条旧记录", job_name, deleted_count)
        except Exception as e:
            logger.warning("删除旧记录失败（继续写入）: %s", e)

        # 3. 写入新记录
        url = f"{LARK_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        import requests
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

        # 4. 写入更新日志（若配置了 log_table_id）
        log_written = False
        if log_table_id and job_name:
            try:
                _ensure_log_table_columns(token, app_token, log_table_id)
                names = [p.get("candidate", "") for p in parsed_list if p.get("candidate")]
                log_written = _append_log_record(
                    token, app_token, log_table_id,
                    job_name, len(record_ids), names,
                    note=f"覆盖更新，删除{deleted_count}条，写入{len(record_ids)}条",
                )
            except Exception as e:
                logger.warning("写入更新日志失败: %s", e)

        # 5. 向 HR 发送通知
        notify_sent = False
        notify_msg = f"【{job_name}】候选人榜单已更新，本次共 {len(record_ids)} 人（最多{max_n}条，覆盖式更新）。请查收。"
        if notify_group:
            _ensure_dotenv_loaded()
            target_chat = chat_id or os.environ.get("LARK_CHAT_ID", "")
            if target_chat:
                notify_sent = _send_lark_chat_notify(token, target_chat, notify_msg)

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
        logger.exception("atom_lark_bitable_sync failed")
        return {"success": False, "record_id": None, "parsed": [], "error": str(e)}
