"""
PMO 数据可视化 — 飞书消息卡片原生 Chart（VChart）驱动，不生成静态图、不上传图片。

- ``build_pmo_chart_data_from_csv``：读提纯 CSV → 聚合指标
- ``build_k11_battle_report_card``：组装交互卡片（环形图 + 横向条形图 + Markdown + 按钮）
- ``send_pmo_k11_battle_report_card``：序列化卡片并走 IM ``send_interactive_card`` 发送
- ``send_pmo_three_dashboard_cards``：连发三张卡片（需求战报多图 + 资源负荷表 + 版本发布），数据源为 Lark 多维表

MCP 入口：``run_data_visualizer``（operation: ``send_battle_report`` | ``send_three_dashboard_cards`` | ``build_card`` | ``build_data_from_csv``）
"""
from __future__ import annotations

import csv
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 需求战报「大盘」横向条形图最多条数（过多时飞书 VChart 易返回 Internal Error）
PMO_BATTLE_OVERVIEW_BAR_MAX = 30


def _pmo_safe_chart_progress(val: Any) -> float:
    """
    VChart 的 ``progress`` 必须为有限数值；``NaN``/``Inf`` 经 ``json.dumps`` 会变成非标准 JSON，
    飞书 IM 接口常表现为 **Internal Error**。
    """
    try:
        v = float(val)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return max(0.0, min(100.0, v))


def _load_pmo_skill_yaml(project_root: Path) -> dict[str, Any]:
    import yaml

    candidates = [
        project_root / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
        Path.home() / ".jachin" / "config" / "skills" / "com.jachin.pmo.bmo" / "pmo_bmo.yaml",
    ]
    for p in candidates:
        if p.is_file():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception as e:
                logger.warning("[data_visualizer] 读取配置失败 %s: %s", p, e)
    return {}


# 与 write_pmo_dashboard_csvs 产出文件名一致（与 pmo_dashboard_push.tables 键一致）
CSV_REQUIREMENT = "PMO_需求完成情况.csv"
CSV_PERSON = "PMO_人员分配.csv"
CSV_REQ_PARTICIPATION = "PMO_需求人员参与情况.csv"
CSV_VERSION_RELEASE = "PMO_版本发布.csv"


def _pmo_apply_lark_env_from_skill_yaml(lk: dict[str, Any] | None) -> None:
    """与 main_skill / atom_pmo 一致：国际 Lark 须 unset LARK_USE_FEISHU。"""
    if not isinstance(lk, dict):
        return
    if lk.get("lark_use_feishu") in (True, "true", "1", "yes"):
        os.environ["LARK_USE_FEISHU"] = "1"
    else:
        os.environ.pop("LARK_USE_FEISHU", None)


def _bitable_list_records_paginated(
    api_base: str,
    tenant_token: str,
    app_token: str,
    table_id: str,
    *,
    max_records: int = 8000,
) -> list[dict[str, Any]]:
    """GET bitable/v1/apps/{app_token}/tables/{table_id}/records，分页直至无 page_token。"""
    import requests

    out: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(out) < max_records:
        params: dict[str, Any] = {"page_size": min(500, max_records - len(out))}
        if page_token:
            params["page_token"] = page_token
        url = f"{api_base.rstrip('/')}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {tenant_token}"},
            params=params,
            timeout=120,
        )
        try:
            data = r.json()
        except Exception:
            logger.warning("[data_visualizer] bitable records 非 JSON: %s", r.text[:200])
            break
        if data.get("code") != 0:
            logger.warning("[data_visualizer] bitable records 失败: %s", data.get("msg", data))
            break
        chunk = data.get("data", {}).get("items", []) or []
        out.extend(chunk)
        page_token = (data.get("data") or {}).get("page_token") or ""
        page_token = page_token.strip() or None
        if not chunk or not page_token:
            break
    return out[:max_records]


def build_pmo_chart_data_from_bitable(
    *,
    app_token: str,
    requirement_table_id: str,
    person_table_id: str,
    app_id: str,
    app_secret: str,
    api_base: str | None = None,
) -> dict[str, Any]:
    """
    从 Lark 多维表（与提纯 CSV 对应的子表）拉记录，聚合为与 ``build_pmo_chart_data_from_csv`` 相同结构的 ``data_dict``。

    - 需求完成情况：按字段「完成到哪一步了」分组计数。
    - 人员分配：主键列为「人员」（兼容「姓名」）；统计每行所有「任务1」「任务2」… 非空列数作为负荷。
    """
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    base = (api_base or get_lark_api_base()).rstrip("/")
    token = get_tenant_access_token(app_id=app_id, app_secret=app_secret, api_base=base)

    req_recs = _bitable_list_records_paginated(base, token, app_token, requirement_table_id)
    per_recs = _bitable_list_records_paginated(base, token, app_token, person_table_id)

    requirement_status: dict[str, int] = {}
    col_step = "完成到哪一步了"
    for rec in req_recs:
        fld = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
        raw = _cell_to_text(fld.get(col_step)).strip()
        if not raw or raw in ("—", "-"):
            key = "未标注"
        else:
            key = raw.split("|")[0].strip() if "|" in raw else raw
            if len(key) > 24:
                key = key[:21] + "…"
        requirement_status[key] = requirement_status.get(key, 0) + 1

    person_load: dict[str, float] = {}
    task_col = re.compile(r"^任务\d+$")
    for rec in per_recs:
        fld = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
        name = (_cell_to_text(fld.get("人员")) or _cell_to_text(fld.get("姓名"))).strip()
        if not name:
            continue
        load = 0.0
        for k in fld:
            ks = str(k)
            if task_col.match(ks) and (_cell_to_text(fld.get(k)) or "").strip():
                load += 1.0
        person_load[name] = person_load.get(name, 0.0) + load

    return {"requirement_status": requirement_status, "person_load": person_load}


def build_pmo_chart_data_from_csv(
    requirement_status_csv: str | Path,
    person_allocation_csv: str | Path,
) -> dict[str, Any]:
    """
    从 PMO 仪表盘提纯 CSV 聚合为图表用 ``data_dict``。

    - 需求：按「完成到哪一步了」首段或整段分组计数（空则记为「未标注」）。
    - 人员：每名非空「任务1」「任务2」各计 1 点负荷（0～2），可反映任务条数。
    """
    req_path = Path(requirement_status_csv)
    per_path = Path(person_allocation_csv)
    requirement_status: dict[str, int] = {}
    person_load: dict[str, float] = {}

    if req_path.is_file():
        with open(req_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            col_step = "完成到哪一步了"
            for row in reader:
                raw = (row.get(col_step) or "").strip()
                if not raw or raw in ("—", "-"):
                    key = "未标注"
                else:
                    key = raw.split("|")[0].strip() if "|" in raw else raw
                    if len(key) > 24:
                        key = key[:21] + "…"
                requirement_status[key] = requirement_status.get(key, 0) + 1

    task_col = re.compile(r"^任务\d+$")
    if per_path.is_file():
        with open(per_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("人员") or row.get("姓名") or "").strip()
                if not name:
                    continue
                load = 0.0
                for k in row:
                    if task_col.match(str(k)) and (row.get(k) or "").strip():
                        load += 1.0
                person_load[name] = person_load.get(name, 0.0) + load

    return {"requirement_status": requirement_status, "person_load": person_load}


def _pie_values_from_requirement(req: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for k, v in req.items():
        if v is None:
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        out.append({"状态": str(k), "数量": n})
    return out


def _bar_top_values_from_person(person: dict[str, Any], top_n: int) -> list[dict[str, Any]]:
    items = sorted(
        ((str(k), float(v)) for k, v in person.items() if v is not None),
        key=lambda x: x[1],
        reverse=True,
    )[: max(0, top_n)]
    return [{"姓名": name, "负荷": val} for name, val in items]


def build_vchart_pie_spec(requirement_status: dict[str, Any]) -> dict[str, Any] | None:
    """环形图（pie + innerRadius）— 需求完成情况。"""
    values = _pie_values_from_requirement(requirement_status)
    if not values:
        return None
    return {
        "type": "pie",
        "title": {"text": "需求完成情况"},
        "data": {"values": values},
        "categoryField": "状态",
        "valueField": "数量",
        "outerRadius": 0.88,
        "innerRadius": 0.38,
        "label": {"visible": True},
        "legends": {"visible": True, "orient": "bottom"},
    }


def build_vchart_bar_top10_spec(person_load: dict[str, Any], top_n: int = 10) -> dict[str, Any] | None:
    """横向条形图 — 人员负荷 TOP N（配色交给卡片 ``color_theme``，避免移动端不兼容的扩展样式）。"""
    values = _bar_top_values_from_person(person_load, top_n)
    if not values:
        return None
    return {
        "type": "bar",
        "title": {"text": f"人员负荷 TOP {min(top_n, len(values))}"},
        "data": {"values": values},
        "direction": "horizontal",
        "xField": "负荷",
        "yField": "姓名",
        "label": {"visible": True},
        "legends": {"visible": False},
        "axes": [
            {"orient": "left"},
            {"orient": "bottom"},
        ],
    }


def build_k11_battle_report_card(
    data_dict: dict[str, Any],
    *,
    snapshot_date: str,
    ai_markdown: str,
    bitable_open_url: str,
    header_title: str = "📊 K11 研发效能大盘日线图",
) -> dict[str, Any]:
    """
    拼装飞书**卡片 JSON 1.0**（IM ``send_interactive_card`` 兼容；含 chart + lark_md + 按钮）。

    说明：发群消息 API 对 Schema 2.0（``schema``/``body``/``markdown`` 组件）支持不完整时会被降级为图片，
    故采用与 ``send_markdown_card`` 相同的根级 ``elements`` 结构。

    ``data_dict``：须含 ``requirement_status``、``person_load``（与 ``build_pmo_chart_data_from_csv`` 一致）。
    """
    req = data_dict.get("requirement_status") or {}
    person = data_dict.get("person_load") or {}
    if not isinstance(req, dict):
        req = {}
    if not isinstance(person, dict):
        person = {}

    md = (ai_markdown or "").strip()
    if not md:
        md = (
            "**AI 诊断（占位）**\n\n"
            "暂无毒舌点评。请在 YAML ``pmo_battle_report_card.ai_markdown`` 或调用参数中传入 LLM 生成的 "
            "阻塞点与过载警告。"
        )
    md = f"**快照日期：** {snapshot_date}\n\n{md}"

    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": md}}
    ]

    pie_spec = build_vchart_pie_spec(req)
    if pie_spec:
        elements.append(
            {
                "tag": "chart",
                "aspect_ratio": "16:9",
                "color_theme": "complementary",
                "chart_spec": pie_spec,
            }
        )
    else:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "*(需求完成情况：暂无有效数据)*",
                },
            }
        )

    bar_spec = build_vchart_bar_top10_spec(person, top_n=10)
    if bar_spec:
        elements.append(
            {
                "tag": "chart",
                "aspect_ratio": "16:9",
                "color_theme": "primary",
                "chart_spec": bar_spec,
            }
        )
    else:
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "*(人员负荷：暂无有效数据)*",
                },
            }
        )

    url = (bitable_open_url or "").strip()
    if url:
        # 卡片 1.0：交互区用 action + button.url（勿用 Schema2 的 behaviors，避免 IM 降级）
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看原生多维表格"},
                        "type": "primary",
                        "url": url,
                    }
                ],
            }
        )

    card: dict[str, Any] = {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": header_title[:100]},
        },
        "elements": elements,
    }
    return card


def _ensure_lark_credentials_from_notifier_config() -> None:
    if os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID"):
        return
    try:
        from l3_node.jachin_config import load_mcp_config
        from l3_node.paths import get_app_root

        cfg = load_mcp_config("atom_lark_notifier", project_root=get_app_root())
        aid = (cfg.get("app_id") or "").strip()
        asec = (cfg.get("app_secret") or "").strip()
        if aid and asec and not str(aid).startswith("${"):
            os.environ.setdefault("LARK_APP_ID", aid)
            os.environ.setdefault("LARK_APP_SECRET", asec)
        if cfg.get("lark_use_feishu") in (True, "true", "1", "yes"):
            os.environ.setdefault("LARK_USE_FEISHU", "1")
    except Exception:
        pass


def _resolve_chat_id(explicit: str | None) -> str:
    cid = (explicit or "").strip()
    if cid and not str(cid).startswith("${"):
        return cid
    try:
        from l3_node.jachin_config import load_mcp_config
        from l3_node.paths import get_app_root

        cfg = load_mcp_config("atom_lark_notifier", project_root=get_app_root())
        cid = (cfg.get("default_chat_id") or os.environ.get("BI_LARK_CHAT_ID") or "").strip()
    except Exception:
        cid = (os.environ.get("BI_LARK_CHAT_ID") or "").strip()
    return cid if not str(cid).startswith("${") else ""


def send_pmo_k11_battle_report_card(
    project_root: Path | None = None,
    *,
    snapshot_date: str | None = None,
    ai_markdown: str | None = None,
    bitable_open_url: str | None = None,
    chat_id: str | None = None,
    cfg: dict[str, Any] | None = None,
    csv_output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    组 VChart 卡片 → IM 发送（无图片上传）。

    **数据源**（默认）：从 Lark 多维表读取，与 ``pmo_dashboard_push.tables`` 中
    ``PMO_需求完成情况.csv`` / ``PMO_人员分配.csv`` 对应的 ``table_id`` 一致（与本地 CSV 一一对应）。
    配置 ``pmo_battle_report_card.data_source: csv`` 或环境变量 ``PMO_BATTLE_REPORT_DATA_SOURCE=csv`` 时改读 ``PMO/output`` 下 CSV。

    csv_output_dir：CSV 模式时与 ``write_pmo_dashboard_csvs`` 的 output_dir 一致。

    凭证：``pmo_bmo.yaml`` 的 ``lark`` 段（app_id/app_secret）；调用前会合并 notifier 环境变量。
    """
    from datetime import date

    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_output_client_dir
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    merged = dict(cfg) if cfg is not None else _load_pmo_skill_yaml(root)
    br = (merged.get("pmo_battle_report_card") or {}) if isinstance(merged, dict) else {}
    lk = merged.get("lark") if isinstance(merged.get("lark"), dict) else {}
    push = merged.get("pmo_dashboard_push") if isinstance(merged.get("pmo_dashboard_push"), dict) else {}

    snap = (snapshot_date or (merged.get("pipeline") or {}).get("snapshot_date") or date.today().isoformat())
    snap = str(snap).strip()[:10]

    ds = (os.environ.get("PMO_BATTLE_REPORT_DATA_SOURCE") or br.get("data_source") or "bitable")
    ds = str(ds).strip().lower()
    use_csv = ds in ("csv", "file", "local")

    data_dict: dict[str, Any] | None = None
    data_note = ""

    if not use_csv:
        _pmo_apply_lark_env_from_skill_yaml(lk)
        _ensure_lark_credentials_from_notifier_config()
        aid = (lk.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
        sec = (lk.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
        app_token = (push.get("app_token") or os.environ.get("PMO_DASHBOARD_APP_TOKEN") or "").strip()
        tables = push.get("tables") if isinstance(push.get("tables"), dict) else {}
        req_tid = (tables.get(CSV_REQUIREMENT) or "").strip()
        per_tid = (tables.get(CSV_PERSON) or "").strip()
        if aid and sec and app_token and req_tid and per_tid:
            try:
                from l3_node.channels.lark.client import get_lark_api_base

                api_base = get_lark_api_base()
                data_dict = build_pmo_chart_data_from_bitable(
                    app_token=app_token,
                    requirement_table_id=req_tid,
                    person_table_id=per_tid,
                    app_id=aid,
                    app_secret=sec,
                    api_base=api_base,
                )
                data_note = "bitable"
            except Exception as e:
                logger.warning("[data_visualizer] 多维表拉取失败，将尝试 CSV: %s", e)
                data_dict = None

    if data_dict is None:
        out_dir = Path(csv_output_dir).expanduser().resolve() if csv_output_dir else get_pmo_output_client_dir()
        req_csv = out_dir / CSV_REQUIREMENT
        per_csv = out_dir / CSV_PERSON
        if not req_csv.is_file() and not per_csv.is_file():
            return {
                "status": "error",
                "error": (
                    "战报数据源：多维表未成功拉取且本地无 CSV。"
                    f" 请检查 pmo_dashboard_push（app_token、tables）与 lark 凭证，或运行 write_pmo_dashboard_csvs。"
                    f"（CSV 路径 {req_csv} / {per_csv}）"
                ),
            }
        data_dict = build_pmo_chart_data_from_csv(req_csv, per_csv)
        data_note = "csv_fallback" if not use_csv else "csv"

    md = ai_markdown if ai_markdown is not None else str(br.get("ai_markdown") or "")
    url = (bitable_open_url if bitable_open_url is not None else str(br.get("bitable_open_url") or "")).strip()

    title = str(br.get("header_title") or "📊 K11 研发效能大盘日线图")[:100]
    card = build_k11_battle_report_card(
        data_dict,
        snapshot_date=snap,
        ai_markdown=md,
        bitable_open_url=url,
        header_title=title,
    )

    if isinstance(lk, dict):
        aid = (lk.get("app_id") or "").strip()
        sec = (lk.get("app_secret") or "").strip()
        if aid and sec:
            os.environ.setdefault("LARK_APP_ID", aid)
            os.environ.setdefault("LARK_APP_SECRET", sec)
        if lk.get("lark_use_feishu"):
            os.environ["LARK_USE_FEISHU"] = "1"

    cid = _resolve_chat_id(chat_id if chat_id is not None else str(br.get("chat_id") or ""))
    if not cid:
        return {
            "status": "error",
            "error": "未配置 chat_id：请在 pmo_battle_report_card.chat_id 或 atom_lark_notifier.default_chat_id 中设置",
            "card": card,
        }

    from l3_node.channels.lark.im import send_interactive_card

    r = send_interactive_card(receive_id=cid, card=card, receive_id_type="chat_id")
    out: dict[str, Any] = {
        "status": "success" if r.get("status") == "success" else "error",
        "lark_send": r,
        "snapshot_date": snap,
        "chat_id": cid[:8] + "…",
        "chart_data_source": data_note,
    }
    if r.get("status") != "success":
        out["error"] = r.get("error", "send failed")
    out["card_preview_keys"] = list(card.keys())
    logger.info("[data_visualizer] K11 战报卡片已投递: %s（数据源=%s）", out["status"], data_note)
    return out


def _pmo_format_date_mmdd(raw: Any) -> str:
    """
    将多维表日期/时间戳统一为 **MM-DD**（如 03-25）。

    实现委托 ``main_skill.pmo_bitable_date_cell_to_mmdd``：正确识别 Lark 日期列常见的 **毫秒时间戳**，
    并与战报「时间跨度」所用 ``YYYY-MM-DD`` 解析同源。
    """
    from l3_node.primitives.skills.pmo_bmo.main_skill import pmo_bitable_date_cell_to_mmdd

    return pmo_bitable_date_cell_to_mmdd(raw)


def _pmo_ascii_progress_bar(pct: float, width: int = 10) -> str:
    p = _pmo_safe_chart_progress(pct)
    filled = int(round(width * p / 100.0))
    filled = min(width, max(0, filled))
    return "▓" * filled + "░" * (width - filled)


def _pmo_short_label(s: str, max_len: int = 22) -> str:
    t = " ".join((s or "").strip().split())
    if len(t) <= max_len:
        return t or "（空）"
    return t[: max_len - 1] + "…"


def _pmo_norm_req_key(s: str) -> str:
    return " ".join((s or "").strip().split())


def _pmo_field_first(fld: dict[str, Any], names: tuple[str, ...]) -> Any:
    for n in names:
        if n in fld and fld[n] is not None:
            return fld[n]
    return None


def _pmo_parse_pct_cell(val: Any) -> float:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    t = _cell_to_text(val).strip()
    if not t or t in ("—", "-", "—", "未完成"):
        return 0.0
    t = t.replace("%", "").replace("％", "").strip()
    try:
        return max(0.0, min(100.0, float(t)))
    except (TypeError, ValueError):
        return 0.0


def _pmo_req_title_cell(fld: dict[str, Any]) -> str:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    v = _pmo_field_first(
        fld,
        (
            "需求内容",
            "需求名称",
        ),
    )
    return _cell_to_text(v).strip() if v is not None else ""


def _pmo_pct_field(fld: dict[str, Any]) -> float:
    """
    从多维表 ``fields`` 取完成度 0～100。

    飞书子表字段名常与 CSV 表头不一致（例如「当前完成度 (%)」在 ``%`` 前有空格，与 main_skill 同步映射一致），
    故须多别名；仍无匹配时再扫任意含「当前完成度」且含百分号的列名。
    """
    v = _pmo_field_first(
        fld,
        (
            "当前完成度 (%)",  # 与 sync 默认 field_mapping / 飞书界面常见列名一致
            "当前完成度(%)",
            "当前完成度（%）",
            "当前完成度（％）",
            "当前完成度",
        ),
    )
    if v is not None:
        return _pmo_parse_pct_cell(v)
    for k, val in (fld or {}).items():
        if not isinstance(k, str):
            continue
        if "当前完成度" in k and ("%" in k or "％" in k):
            return _pmo_parse_pct_cell(val)
    return 0.0


def _pmo_time_field(fld: dict[str, Any], key: str) -> str:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    v = fld.get(key)
    return _cell_to_text(v).strip() if v is not None else ""


def _pmo_parse_participation_pairs(fld: dict[str, Any]) -> list[tuple[str, float]]:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    nums: list[int] = []
    for k in fld:
        m = re.match(r"^人员(\d+)$", str(k))
        if m:
            nums.append(int(m.group(1)))
    out: list[tuple[str, float]] = []
    for i in sorted(nums):
        pn = _cell_to_text(fld.get(f"人员{i}")).strip()
        if not pn:
            continue
        pv = _pmo_parse_pct_cell(fld.get(f"完成度{i}"))
        out.append((pn, pv))
    return out


def _pmo_req_chart_display_name(start_mmdd: Any, requirement_title: str) -> str:
    """
    VChart 类目轴专用：开始时间 + 需求内容。
    格式：`[03-12起] 博弈游戏`（日期经 _pmo_format_date_mmdd 强制为 MM-DD）。
    """
    s = _pmo_format_date_mmdd(start_mmdd)
    ts = " ".join((requirement_title or "").strip().split()) or "（未命名）"
    if s in ("—", "", "未完成"):
        return f"[—起] {ts}"
    return f"[{s}起] {ts}"


def _pmo_req_chart_display_label(end_mmdd: Any, pct: float) -> str:
    """
    柱条末端 Label 专用：结束时间 + 完成度（禁止回退为纯数字）。
    格式：`至03-30 (45%)`。
    """
    e = _pmo_format_date_mmdd(end_mmdd)
    p = _pmo_safe_chart_progress(pct)
    return f"至{e} ({p:.0f}%)"


def _pmo_vchart_requirement_battle_label() -> dict[str, Any]:
    """强制绑定 display_label，避免 label 回退为默认数字。"""
    return {
        "visible": True,
        "position": "right",
        "style": {"fill": "#646A73"},
        "valueField": "display_label",
    }


def _pmo_vchart_slim_bar_style() -> dict[str, Any]:
    """横向柱条「瘦身」：避免单条数据时柱子被撑成大方块（飞书 VChart 支持 barMaxWidth / barWidth）。"""
    return {
        "barMaxWidth": 20,
        "barWidth": 20,
        "bar": {
            "style": {
                "maxWidth": 20,
            }
        },
    }


def _pmo_build_overview_bar_spec(rows: list[dict[str, Any]], *, title: str) -> dict[str, Any]:
    """需求大盘：单条数据必须为 display_name / progress / display_label（飞书 VChart 显式字段名）。"""
    values: list[dict[str, Any]] = []
    for r in rows:
        st = r.get("start_raw")
        en = r.get("end_raw")
        if st is None:
            st = "—"
        if en is None:
            en = "—"
        pct = _pmo_safe_chart_progress(r.get("pct"))
        title_text = _pmo_short_label(r["title"], 40)
        values.append(
            {
                "display_name": _pmo_req_chart_display_name(st, title_text),
                "progress": pct,
                "display_label": _pmo_req_chart_display_label(en, pct),
            }
        )
    spec: dict[str, Any] = {
        "type": "bar",
        "title": {"text": title[:80]},
        "data": {"values": values},
        "direction": "horizontal",
        "xField": "progress",
        "yField": "display_name",
        "label": _pmo_vchart_requirement_battle_label(),
        "legends": {"visible": False},
        "axes": [
            {"orient": "left"},
            {"orient": "bottom", "min": 0, "max": 100},
        ],
        "color": ["#3370FF"],
    }
    spec.update(_pmo_vchart_slim_bar_style())
    return spec


def _pmo_build_single_req_total_progress_spec(
    *,
    title_short: str,
    start_date: Any,
    end_date: Any,
    pct: float,
) -> dict[str, Any]:
    """单条大需求总进度：与大盘相同字段契约（display_name / progress / display_label）。"""
    p = _pmo_safe_chart_progress(pct)
    fill = "#24A159" if p >= 99.5 else "#3370FF"
    st = "—" if start_date is None else start_date
    en = "—" if end_date is None else end_date
    spec: dict[str, Any] = {
        "type": "bar",
        "title": {"text": "本需求 · 总完成度", "subtext": f"{p:.0f}%"},
        "data": {
            "values": [
                {
                    "display_name": _pmo_req_chart_display_name(st, title_short),
                    "progress": p,
                    "display_label": _pmo_req_chart_display_label(en, p),
                }
            ]
        },
        "direction": "horizontal",
        "xField": "progress",
        "yField": "display_name",
        "label": _pmo_vchart_requirement_battle_label(),
        "legends": {"visible": False},
        "axes": [
            {"orient": "left"},
            {"orient": "bottom", "min": 0, "max": 100},
        ],
        "color": [fill],
    }
    spec.update(_pmo_vchart_slim_bar_style())
    return spec


def _pmo_chart_element_single_bar_slim(chart_spec: dict[str, Any]) -> dict[str, Any]:
    """单条主进度：固定高度，修长紧凑（飞书卡片 chart 支持 height 时优先于 aspect_ratio）。"""
    return {
        "tag": "chart",
        "height": "140px",
        "chart_spec": chart_spec,
    }


def _pmo_chart_element_overview(chart_spec: dict[str, Any]) -> dict[str, Any]:
    """需求大盘：多行条形，保持宽屏比例；柱宽仍由 chart_spec 内 barMaxWidth 限制。"""
    return {
        "tag": "chart",
        "aspect_ratio": "16:9",
        "chart_spec": chart_spec,
    }


def _pmo_build_requirement_battle_card(
    *,
    snapshot_date: str,
    req_rows: list[dict[str, Any]],
    part_by_key: dict[str, list[tuple[str, float]]],
    max_detail: int,
    bitable_open_url: str,
) -> dict[str, Any]:
    """需求完成情况战报：总览横向条形图（X=0～100）+ 分条总进度图 + Markdown 人员字符进度条。"""
    from l3_node.primitives.skills.pmo_bmo.main_skill import (
        pmo_requirement_battle_person_details_md,
        pmo_requirement_battle_time_span_md,
    )

    elements: list[dict[str, Any]] = []
    intro = (
        f"📅 **快照日期：** `{snapshot_date}`\n\n"
        f"共 **{len(req_rows)}** 条需求 · 时间均为 **MM-DD** · 进度条横轴 **0～100%** 对齐\n\n"
        f"以下为 **需求大盘**，随后展开前 **{min(max_detail, len(req_rows))}** 条「总进度图 + 人员明细」。"
    )
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": intro}})

    overview_rows = req_rows[:PMO_BATTLE_OVERVIEW_BAR_MAX]
    note = ""
    if len(req_rows) > PMO_BATTLE_OVERVIEW_BAR_MAX:
        note = (
            f"\n\n*大盘条形图仅含前 **{PMO_BATTLE_OVERVIEW_BAR_MAX}** 条，全量共 {len(req_rows)} 条。*"
        )
    if overview_rows:
        elements.append(
            _pmo_chart_element_overview(
                _pmo_build_overview_bar_spec(
                    overview_rows, title="需求完成度大盘（Y 轴：需求 + 时间跨度）"
                )
            )
        )
    if note:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": note.strip()}})

    total_to_show = min(max_detail, len(req_rows))
    # 需求总体大盘与第一条需求明细之间：留白 + 分割线（与块间逻辑一致）
    if total_to_show > 0:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n\n"}})
        elements.append({"tag": "hr"})

    shown = 0
    for r in req_rows:
        if shown >= max_detail:
            break
        title = r["title"]
        pct = _pmo_safe_chart_progress(r.get("pct"))
        st_raw = r.get("start_raw")
        en_raw = r.get("end_raw")
        k = _pmo_norm_req_key(title)
        pairs = part_by_key.get(k) or []

        elements.append(
            _pmo_chart_element_single_bar_slim(
                _pmo_build_single_req_total_progress_spec(
                    title_short=_pmo_short_label(title, 22),
                    start_date=st_raw,
                    end_date=en_raw,
                    pct=pct,
                )
            )
        )
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": pmo_requirement_battle_person_details_md(title, pairs),
                },
            }
        )
        elements.append(
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": pmo_requirement_battle_time_span_md(
                        r.get("start_cal") or "",
                        r.get("end_cal") or "",
                    ),
                },
            }
        )
        shown += 1
        # 需求区块之间：留白 + 飞书分割线（最后一条不追加，避免与底部按钮挤在一起）
        if shown < total_to_show:
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n\n"}})
            elements.append({"tag": "hr"})

    if bitable_open_url.strip():
        elements.append(
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开 PMO 多维表格"},
                        "type": "primary",
                        "url": bitable_open_url.strip(),
                    }
                ],
            }
        )

    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "需求完成情况战报"},
        },
        "elements": elements,
    }


def _pmo_resource_load_task_rows(person_recs: list[dict[str, Any]]) -> list[dict[str, str]]:
    """
    解析「人员分配」多维表记录为表格行；**仅保留** 任务1…N 中至少有一条非空的人员。
    列语义与任务列顺序一致：第 1 条非空任务列 → P0 展示列，第 2 条 → P1/P2，其余 → 其它（多行用 ``<br>``）。
    """
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    task_re = re.compile(r"^任务(\d+)$")
    rows: list[dict[str, str]] = []
    for rec in person_recs:
        fld = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
        name = (_cell_to_text(fld.get("人员")) or _cell_to_text(fld.get("姓名"))).strip()
        if not name:
            continue
        tasks: list[tuple[int, str]] = []
        for k in fld:
            m = task_re.match(str(k))
            if not m:
                continue
            t = _cell_to_text(fld.get(k)).strip()
            if t:
                tasks.append((int(m.group(1)), t))
        tasks.sort(key=lambda x: x[0])
        names = [x[1] for x in tasks]
        if not names:
            continue
        p0 = names[0] if names else ""
        p1p2 = names[1] if len(names) > 1 else ""
        other_parts = names[2:] if len(names) > 2 else []
        other = "<br>".join(other_parts) if other_parts else ""
        rows.append({"person": name, "p0": p0, "p1p2": p1p2, "other": other})
    return rows


def _pmo_resource_load_cell_lark_md(text: str, *, max_len: int = 2400) -> str:
    """表格单元格 lark_md：空为全角破折号；换行转为 ``<br>``，避免 ``|`` 破坏渲染。"""
    s = (text or "").strip()
    if not s:
        return "—"
    s = s.replace("|", "｜").replace("\r\n", "\n").replace("\r", "\n")
    s = "<br>".join(part.strip() for part in s.split("\n") if part.strip())
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s


def _pmo_build_resource_load_interactive_card(
    person_recs: list[dict[str, Any]],
) -> tuple[dict[str, Any], int]:
    """
    「资源任务负荷」交互卡片：顶部说明（lark_md）+ **飞书原生 table 组件**（v7.4+），
    仅包含至少有一条任务的人员；多维表同步仍为全员，不在此改动。

    返回 ``(card_dict, 展示行数)``。
    """
    rows_data = _pmo_resource_load_task_rows(person_recs)
    intro = (
        "**说明：** 下列仅包含「人员分配」中 **任务1… 至少有一条非空** 的人员（"
        f"共 **{len(rows_data)}** 人）；多维表仍为全员。\n\n"
        "🔴 **任务1** → P0 高优　🟠 **任务2** → P1/P2　🟢 **任务3 及以后** → 其它/日常"
    )
    if not rows_data:
        md = intro + "\n\n**（当前无任务分配；任务列全空的人员未列出。）**"
        return _pmo_card_markdown_only("资源任务负荷", md, template="wathet"), 0

    table_rows: list[dict[str, str]] = []
    for r in rows_data:
        table_rows.append(
            {
                "c_person": r["person"][:500],
                "c_p0": _pmo_resource_load_cell_lark_md(r["p0"]),
                "c_p12": _pmo_resource_load_cell_lark_md(r["p1p2"]),
                "c_ot": _pmo_resource_load_cell_lark_md(r["other"]),
            }
        )

    n = len(table_rows)
    page_sz = max(1, min(10, n))

    table_el: dict[str, Any] = {
        "tag": "table",
        "page_size": page_sz,
        # low 过扁、high 留白多；middle 为默认行高，兼顾多行任务与纵向占用
        "row_height": "middle",
        "freeze_first_column": True,
        "header_style": {
            "text_align": "left",
            "text_size": "normal",
            "background_style": "grey",
            "text_color": "default",
            "bold": True,
            "lines": 1,
        },
        "columns": [
            {
                "name": "c_person",
                "display_name": "👷 研发人员",
                "data_type": "text",
                "width": "20%",
                "vertical_align": "top",
                "horizontal_align": "left",
            },
            {
                "name": "c_p0",
                "display_name": "🔴 P0 高优",
                "data_type": "lark_md",
                "width": "27%",
                "vertical_align": "top",
                "horizontal_align": "left",
            },
            {
                "name": "c_p12",
                "display_name": "🟠 P1/P2",
                "data_type": "lark_md",
                "width": "26%",
                "vertical_align": "top",
                "horizontal_align": "left",
            },
            {
                "name": "c_ot",
                "display_name": "🟢 其它/日常",
                "data_type": "lark_md",
                "width": "27%",
                "vertical_align": "top",
                "horizontal_align": "left",
            },
        ],
        "rows": table_rows,
    }

    card = {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "wathet",
            "title": {"tag": "plain_text", "content": "资源任务负荷"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": intro[:8000]}},
            table_el,
        ],
    }
    return card, n


def _md_table_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")[:2000]


def _md_table_cell_br(s: str) -> str:
    """表格单元格：保留 `<br>` 换行，仅转义 `|`。"""
    return (s or "").replace("|", "\\|")[:2000]


def _pmo_format_release_date_line(raw: Any, snapshot_date: str) -> str:
    """版本卡片顶栏日期：优先表内发布字段，否则用快照日。"""
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    s = _cell_to_text(raw).strip() if raw is not None else ""
    if s:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
        if m:
            return f"📅 发布时间: {m.group(1)}"
        if s.isdigit():
            n = int(s)
            if n > 10_000_000_000_000:
                n = n // 1000
            if n > 1_000_000_000:
                from datetime import datetime

                try:
                    dt = datetime.fromtimestamp(n)
                    return f"📅 发布时间: {dt.strftime('%Y-%m-%d')}"
                except (OSError, OverflowError, ValueError):
                    pass
    return f"📅 快照参照: {snapshot_date}"


def _pmo_build_version_release_markdown(ver_recs: list[dict[str, Any]], *, snapshot_date: str) -> str:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _cell_to_text

    blocks: list[str] = []
    for rec in ver_recs:
        fld = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
        ver = _cell_to_text(_pmo_field_first(fld, ("版本号", "版本"))).strip()
        done = _cell_to_text(_pmo_field_first(fld, ("完成的需求", "完成需求"))).strip()
        rel_raw = _pmo_field_first(fld, ("发布时间", "发布日期", "日期"))
        if not ver and not done:
            continue
        head = f"📦 **版本号：** `{ver or '（未命名）'}` （{_pmo_format_release_date_line(rel_raw, snapshot_date)}）\n\n"
        if not done:
            blocks.append(head + "✨ **核心需求完成：**\n\n- *暂无*\n")
            continue
        parts = [p.strip() for p in re.split(r"[；;\n]+", done) if p.strip()]
        core: list[str] = []
        bugs: list[str] = []
        for p in parts:
            pl = p.lower()
            if any(k in p for k in ("修复", "修復", "bug", "缺陷", "优化", "联调")) or "fix" in pl:
                bugs.append(p)
            else:
                core.append(p)
        sec_core = "\n".join(f"✅ {c}" for c in core) if core else "✅ *（无独立条目）*"
        out = head + "✨ **核心需求完成：**\n\n" + sec_core + "\n"
        if bugs:
            out += "\n🐛 **修复与优化：**\n\n"
            out += "\n".join(f"🛠️ {b}" for b in bugs) + "\n"
        blocks.append(out)
    if not blocks:
        return "**（版本发布表暂无数据）**"
    return "\n---\n\n".join(blocks)


def _pmo_card_markdown_only(title: str, md: str, *, template: str = "wathet") -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title[:100]},
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": md[:12000]}}],
    }


def send_pmo_three_dashboard_cards(
    project_root: Path | None = None,
    *,
    snapshot_date: str | None = None,
    cfg: dict[str, Any] | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """
    从 Lark 多维表拉取四张子表，向群推送三条消息卡片：

    1. 需求完成情况战报（VChart：总览 + 每条堆叠进度 + 人员进度）
    2. 资源任务负荷（飞书原生 **table** 组件 + 说明；仅包含有任务人员）
    3. 版本发布情况（Markdown 排版）

    若 ``pmo_monthly_master_line.enabled``：第 1 张卡仅汇报锚点 MD 中的条目（见 ``main_skill.pmo_try_monthly_master_line_battle_bundle``），
    否则仍为「需求完成情况」子表全量行。
    """
    from datetime import date

    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
    from l3_node.channels.lark.im import send_interactive_card
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    merged = dict(cfg) if cfg is not None else _load_pmo_skill_yaml(root)
    tdc = (merged.get("pmo_dashboard_three_cards") or {}) if isinstance(merged, dict) else {}
    if not bool(tdc.get("enabled", False)):
        return {"status": "skipped", "reason": "pmo_dashboard_three_cards.enabled 未为 true"}

    lk = merged.get("lark") if isinstance(merged.get("lark"), dict) else {}
    push = merged.get("pmo_dashboard_push") if isinstance(merged.get("pmo_dashboard_push"), dict) else {}
    br = (merged.get("pmo_battle_report_card") or {}) if isinstance(merged, dict) else {}

    snap = (
        snapshot_date
        or (merged.get("pipeline") or {}).get("snapshot_date")
        or date.today().isoformat()
    )
    snap = str(snap).strip()[:10]

    max_detail = int(tdc.get("max_requirement_detail_charts") or 12)
    max_detail = max(1, min(40, max_detail))

    _pmo_apply_lark_env_from_skill_yaml(lk)
    _ensure_lark_credentials_from_notifier_config()
    aid = (lk.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
    sec = (lk.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    app_token = (push.get("app_token") or os.environ.get("PMO_DASHBOARD_APP_TOKEN") or "").strip()
    tables = push.get("tables") if isinstance(push.get("tables"), dict) else {}
    tid_req = (tables.get(CSV_REQUIREMENT) or "").strip()
    tid_per = (tables.get(CSV_PERSON) or "").strip()
    tid_part = (tables.get(CSV_REQ_PARTICIPATION) or "").strip()
    tid_ver = (tables.get(CSV_VERSION_RELEASE) or "").strip()

    if not (aid and sec and app_token and tid_req and tid_per and tid_part and tid_ver):
        return {
            "status": "error",
            "error": "缺少 lark 凭证或 pmo_dashboard_push.app_token / tables 四张表 table_id",
        }

    api_base = get_lark_api_base().rstrip("/")
    token = get_tenant_access_token(app_id=aid, app_secret=sec, api_base=api_base)

    req_recs = _bitable_list_records_paginated(api_base, token, app_token, tid_req)
    per_recs = _bitable_list_records_paginated(api_base, token, app_token, tid_per)
    part_recs = _bitable_list_records_paginated(api_base, token, app_token, tid_part)
    ver_recs = _bitable_list_records_paginated(api_base, token, app_token, tid_ver)

    from l3_node.primitives.skills.pmo_bmo.main_skill import (
        pmo_bitable_date_cell_to_yyyy_mm_dd,
        pmo_try_monthly_master_line_battle_bundle,
    )

    bundle = pmo_try_monthly_master_line_battle_bundle(root, snap, merged, req_recs, part_recs)
    if bundle is not None:
        req_rows = bundle["req_rows"]
        part_by_key = bundle["part_by_key"]
    else:
        req_rows = []
        for rec in req_recs:
            fld = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
            title = _pmo_req_title_cell(fld)
            if not title:
                continue
            st_raw = _pmo_field_first(fld, ("开始时间", "开始日期"))
            en_raw = _pmo_field_first(fld, ("结束时间", "结束日期"))
            req_rows.append(
                {
                    "title": title,
                    "pct": _pmo_pct_field(fld),
                    "start_raw": st_raw,
                    "end_raw": en_raw,
                    "start_cal": pmo_bitable_date_cell_to_yyyy_mm_dd(st_raw),
                    "end_cal": pmo_bitable_date_cell_to_yyyy_mm_dd(en_raw),
                }
            )

        part_by_key = {}
        for rec in part_recs:
            fld = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
            rk = _pmo_req_title_cell(fld)
            if not rk:
                continue
            part_by_key[_pmo_norm_req_key(rk)] = _pmo_parse_participation_pairs(fld)

    url = (str(tdc.get("bitable_open_url") or "") or str(br.get("bitable_open_url") or "")).strip()

    card1 = _pmo_build_requirement_battle_card(
        snapshot_date=snap,
        req_rows=req_rows,
        part_by_key=part_by_key,
        max_detail=max_detail,
        bitable_open_url=url,
    )
    card2, n_resource_load = _pmo_build_resource_load_interactive_card(per_recs)
    md3 = _pmo_build_version_release_markdown(ver_recs, snapshot_date=snap)
    card3 = _pmo_card_markdown_only("版本发布情况", md3, template="green")

    if isinstance(lk, dict) and lk.get("app_id") and lk.get("app_secret"):
        os.environ.setdefault("LARK_APP_ID", str(lk.get("app_id")).strip())
        os.environ.setdefault("LARK_APP_SECRET", str(lk.get("app_secret")).strip())
        if lk.get("lark_use_feishu"):
            os.environ["LARK_USE_FEISHU"] = "1"

    cid = _resolve_chat_id(
        (chat_id if chat_id is not None else str(tdc.get("chat_id") or br.get("chat_id") or ""))
    )
    if not cid:
        return {
            "status": "error",
            "error": "未配置 chat_id：pmo_dashboard_three_cards.chat_id 或 pmo_battle_report_card.chat_id / atom_lark_notifier.default_chat_id",
        }

    results: list[dict[str, Any]] = []
    for name, card in (
        ("requirement_battle", card1),
        ("resource_load", card2),
        ("version_release", card3),
    ):
        r = send_interactive_card(receive_id=cid, card=card, receive_id_type="chat_id")
        results.append(
            {
                "name": name,
                "status": "success" if r.get("status") == "success" else "error",
                "lark_send": r,
            }
        )

    ok = all(x.get("status") == "success" for x in results)
    return {
        "status": "success" if ok else "partial",
        "snapshot_date": snap,
        "chat_id": cid[:8] + "…",
        "counts": {
            "requirement_rows": len(req_rows),
            "person_rows": len(per_recs),
            "resource_load_rows_shown": n_resource_load,
            "participation_rows": len(part_recs),
            "version_rows": len(ver_recs),
        },
        "cards": results,
    }


def run_data_visualizer(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    MCP 统一入口。

    ``operation``:
      - ``send_battle_report``（默认）：默认从 Lark 多维表拉数（与 ``pmo_dashboard_push.tables``）；YAML ``data_source: csv`` 或 ``PMO_BATTLE_REPORT_DATA_SOURCE=csv`` 时读 PMO/output CSV；可选 ``snapshot_date``、``ai_markdown``、``chat_id``、``bitable_open_url``
      - ``send_three_dashboard_cards``：需 ``pmo_dashboard_three_cards.enabled``；连发三张卡片（需求战报 VChart + 资源负荷表 + 版本发布）
      - ``build_card``：仅返回 ``card`` JSON，不发送
      - ``build_data_from_csv``：仅返回 ``data_dict``（需 ``requirement_csv`` + ``person_csv`` 或默认 PMO/output）
    """
    args = dict(arguments or {})
    op = (args.get("operation") or "send_battle_report").strip().lower()

    try:
        if op in ("build_data_from_csv", "data_from_csv"):
            from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_output_client_dir

            out_dir = get_pmo_output_client_dir()
            req = args.get("requirement_csv") or str(out_dir / CSV_REQUIREMENT)
            per = args.get("person_csv") or str(out_dir / CSV_PERSON)
            data_dict = build_pmo_chart_data_from_csv(req, per)
            return {"status": "success", "data_dict": data_dict, "operation": op}

        if op in ("build_card", "card"):
            from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import get_pmo_output_client_dir
            from datetime import date

            out_dir = get_pmo_output_client_dir()
            req = args.get("requirement_csv") or str(out_dir / CSV_REQUIREMENT)
            per = args.get("person_csv") or str(out_dir / CSV_PERSON)
            data_dict = build_pmo_chart_data_from_csv(req, per)
            snap = str(args.get("snapshot_date") or date.today().isoformat())[:10]
            card = build_k11_battle_report_card(
                data_dict,
                snapshot_date=snap,
                ai_markdown=str(args.get("ai_markdown") or ""),
                bitable_open_url=str(args.get("bitable_open_url") or ""),
            )
            return {"status": "success", "card": card, "data_dict": data_dict, "operation": op}

        if op in ("send_battle_report", "send", "push"):
            from l3_node.paths import get_app_root

            root = Path(args.get("project_root") or get_app_root())
            cfg_path = args.get("cfg") or args.get("skill_yaml")
            cfg: dict[str, Any] | None = None
            if cfg_path:
                import yaml

                p = Path(str(cfg_path))
                if p.is_file():
                    with open(p, encoding="utf-8") as f:
                        cfg = yaml.safe_load(f)
            else:
                cfg = _load_pmo_skill_yaml(root)
            return send_pmo_k11_battle_report_card(
                root,
                snapshot_date=args.get("snapshot_date"),
                ai_markdown=args.get("ai_markdown"),
                bitable_open_url=args.get("bitable_open_url"),
                chat_id=args.get("chat_id"),
                cfg=cfg,
            )

        if op in ("send_three_dashboard_cards", "three_cards", "three-dashboard-cards"):
            from l3_node.paths import get_app_root

            root = Path(args.get("project_root") or get_app_root())
            cfg_path = args.get("cfg") or args.get("skill_yaml")
            cfg_t: dict[str, Any] | None = None
            if cfg_path:
                import yaml

                p = Path(str(cfg_path))
                if p.is_file():
                    with open(p, encoding="utf-8") as f:
                        cfg_t = yaml.safe_load(f)
            else:
                cfg_t = _load_pmo_skill_yaml(root)
            return send_pmo_three_dashboard_cards(
                root,
                snapshot_date=args.get("snapshot_date"),
                chat_id=args.get("chat_id"),
                cfg=cfg_t,
            )

        return {"status": "error", "error": f"未知 operation: {op}"}
    except Exception as e:
        logger.exception("run_data_visualizer 失败")
        return {"status": "error", "error": str(e), "operation": op}
