# -*- coding: utf-8 -*-
"""
K11 统合冒烟：将结果同步到 Lark 知识库内嵌「多维表 bitable」或「电子表格 sheet」，
并向指定会话发送完成通知。电子表格与本地 xlsx 相同：在含「测试项目/结果/备注」表头的工作表
中按用例关键词匹配行并回写（见 ``test_k11_unified_platform_smoke_playwright.write_k11_unified_results_to_xlsx``）。

**发消息卡片 / 写 Wiki 表格** 的应用凭证与通知会话 **仅** 读取环境变量（与仓库根 ``.env`` 中 ``K11_SMOKE_LARK_*`` 一致，勿把 app_secret 提交到 git）：
  - ``K11_SMOKE_LARK_APP_ID``
  - ``K11_SMOKE_LARK_APP_SECRET``
  - ``K11_SMOKE_LARK_NOTIFY_CHAT_ID``（发卡片/消息的 oc_ 会话；**不再**回退 ``LARK_CHAT_ID`` / 内置默认）

可选（与表同步相关）：
  K11_SMOKE_LARK_WIKI_URL  知识库节点链接；多维表可带 table=；电子表格可带 sheet= 子表 id
  K11_SMOKE_LARK_TABLE_ID  仅多维表：子表 id（tbl...）；不填则按子表名「冒烟」等自动解析
  K11_SMOKE_LARK_SHEET_ID  仅电子表格：子表 sheet_id；不填则优先 URL 的 sheet=，否则选标题含「冒烟」的工作表

应用需具备：Wiki 读节点；多维表编辑或电子表格（sheets:spreadsheet）编辑等权限，且机器人有文档协作者权限。

飞书 API code **230002** / ``Bot/User can NOT be out of the chat``：发消息/卡片的 **chat_id** 对应会话里**没有本应用机器人**（与打包无关）。请把机器人拉入目标群/话题，或把 ``K11_SMOKE_LARK_NOTIFY_CHAT_ID`` 改为已含该机器人的 ``oc_...`` 会话。

电子表格若「结果」列为复选框，同步时写入 **1=勾选（PASS）** 与 **0=未选（非 PASS）**，不写字符串 "PASS"。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DEFAULT_WIKI_URL = (
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZyWlwhdW1iNQuykvy7qlw93sgTe"
)

# 战报标题：与业务侧「综合冒烟」用语一致；勿与「统合」脚本文件名混为显示名
K11_LARK_CARD_TITLE = "【K11 综合冒烟】战报"
K11_LARK_CARD_TARGET_URL = "https://www.kalaroko.com/"
# 飞书 IM 交互卡 content 过大时首包失败，会误退化为 lark_md 列表样式
_K11_CARD_JSON_SOFT_MAX = 28_000


def _apply_k11_smoke_lark_env() -> None:
    """
    冒烟飞书三键 **只认应用根目录 .env**（与仓库根 ``.env`` 候选链一致）：

    1. 先 ``apply_packaged_lark_to_os_environ``（frozen 下 **不会** 写入 ``K11_SMOKE_LARK_*``，见 packaged_lark_env）。
    2. 再对首个存在的候选 ``.env`` 执行 ``load_dotenv(..., override=True)``，保证安装目录三行覆盖进程内任何旧值。

    候选顺序：``JACHIN_APP_ROOT/.env`` → ``get_app_root()/.env`` → 本仓库 ``ROOT/.env``（开发机）。
    """
    try:
        from l3_node.packaged_lark_env import apply_packaged_lark_to_os_environ

        apply_packaged_lark_to_os_environ()
    except Exception:
        pass
    try:
        from dotenv import load_dotenv

        _cands: list[Path] = []
        _ja = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
        if _ja:
            _cands.append(Path(_ja).expanduser().resolve() / ".env")
        try:
            from l3_node.paths import get_app_root

            _cands.append(get_app_root() / ".env")
        except Exception:
            pass
        _cands.append(ROOT / ".env")
        _seen: set[str] = set()
        for _p in _cands:
            _k = str(_p)
            if _k in _seen:
                continue
            _seen.add(_k)
            if _p.is_file():
                # 必须与安装目录 .env 一致；override=True 否则 frozen 下其它路径已写入的占位不会被子进程里的 .env 覆盖
                load_dotenv(_p, encoding="utf-8", override=True)
                break
    except Exception:
        pass


def resolve_k11_lark_app_credentials() -> tuple[str, str, str | None]:
    """
    仅返回 ``K11_SMOKE_LARK_APP_ID``、``K11_SMOKE_LARK_APP_SECRET`` 与 ``api_base=None``（走默认域）。
    不再回退 ``LARK_APP_ID`` / ``resolve_lark_credentials``。
    """
    _apply_k11_smoke_lark_env()
    a = (os.environ.get("K11_SMOKE_LARK_APP_ID") or "").strip()
    s = (os.environ.get("K11_SMOKE_LARK_APP_SECRET") or "").strip()
    return a, s, None


def log_k11_lark_runtime_identity(
    log: Callable[[str], None],
    *,
    phase: str,
    effective_app_id: str,
    effective_app_secret: str,
    send_chat_id: str | None = None,
) -> None:
    """冒烟飞书：诊断行仅反映 K11_SMOKE_LARK_* 三键（与 .env 一致）。"""
    k11_a = (os.environ.get("K11_SMOKE_LARK_APP_ID") or "").strip()
    n_c = (os.environ.get("K11_SMOKE_LARK_NOTIFY_CHAT_ID") or "").strip()
    ja = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
    app_root = ""
    try:
        from l3_node.paths import get_app_root

        app_root = str(get_app_root())
    except Exception:
        pass
    parts = [
        f"  [lark·身份·{phase}] 生效_app_id={effective_app_id!r}",
        f"app_secret_len={len(effective_app_secret)}",
        f"env_K11_SMOKE_LARK_APP_ID={'(已设)' if k11_a else '(未设)'}",
        f"env_K11_SMOKE_LARK_NOTIFY_CHAT_ID={n_c or '(未设)'}",
        f"JACHIN_APP_ROOT={ja or '(未设)'}",
        f"get_app_root={app_root or '(n/a)'}",
    ]
    if send_chat_id is not None:
        parts.append(
            f"发消息_receive_id(chat_id)={send_chat_id!r}（230002=机器人未进该会话）"
        )
    log(" | ".join(parts))


def _lark_get(api_base: str, token: str, path: str, params: dict | None = None) -> dict[str, Any]:
    import requests

    url = f"{api_base.rstrip('/')}{path}" if path.startswith("/") else f"{api_base.rstrip('/')}/{path}"
    r = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=120,
    )
    try:
        return r.json()
    except Exception:
        return {"code": -1, "msg": (r.text or "")[:500]}


def _lark_post(
    api_base: str, token: str, path: str, body: dict[str, Any]
) -> dict[str, Any]:
    import requests

    url = f"{api_base.rstrip('/')}{path}" if path.startswith("/") else f"{api_base.rstrip('/')}/{path}"
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    try:
        return r.json()
    except Exception:
        return {"code": -1, "msg": (r.text or "")[:500]}


def _lark_put(
    api_base: str, token: str, path: str, body: dict[str, Any]
) -> dict[str, Any]:
    import requests

    url = f"{api_base.rstrip('/')}{path}" if path.startswith("/") else f"{api_base.rstrip('/')}/{path}"
    r = requests.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
        json=body,
        timeout=120,
    )
    try:
        return r.json()
    except Exception:
        return {"code": -1, "msg": (r.text or "")[:500]}


def _cell_to_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, list):
        parts: list[str] = []
        for x in val:
            parts.append(_cell_to_text(x))
        return "; ".join(p for p in parts if p)
    if isinstance(val, dict):
        if "text" in val:
            t = val.get("text")
            return str(t) if t is not None else ""
        if "name" in val:
            return str(val.get("name", ""))
        if val.get("type") == "url" and val.get("link"):
            return str(val.get("link") or "")
        return json.dumps(val, ensure_ascii=False)[:2000]
    return str(val)[:2000]


def _resolve_table_id(
    api_base: str,
    token: str,
    app_token: str,
    explicit: str | None,
) -> str | None:
    tid = (explicit or "").strip()
    if tid.startswith("tbl"):
        return tid
    from l3_node.primitives.mcp.mcp_tools.bi.lark_bitable_client import (
        _resolve_table_id_by_name,
    )

    data = _lark_get(
        api_base, token, f"/bitable/v1/apps/{app_token}/tables", {"page_size": 200}
    )
    if data.get("code") != 0:
        return None
    items = (data.get("data") or {}).get("items", []) or []
    env_name = (os.environ.get("K11_SMOKE_LARK_TABLE_NAME") or "冒烟").strip()
    t = _resolve_table_id_by_name(items, env_name)
    if t:
        return t
    t = _resolve_table_id_by_name(items, "冒烟测试")
    if t:
        return t
    for it in items:
        n = (it.get("name") or "").strip()
        if "冒烟" in n or "测试" in n:
            x = it.get("table_id")
            return str(x).strip() if x else None
    if len(items) == 1 and items[0].get("table_id"):
        return str(items[0]["table_id"]).strip()
    return None


def _lark_sheet_result_checkbox_01(verdict: str) -> int:
    """
    飞书电子表格「结果」列为**复选框**时，校验只允许 0/1；PASS 写 1 为勾选，其余为 0。
    """
    return 1 if str(verdict or "").strip().upper() == "PASS" else 0


def _col_1based_to_letters(n: int) -> str:
    s = ""
    x = n
    while x:
        x, r = divmod(x - 1, 26)
        s = chr(65 + r) + s
    return s


def _lark_sheet_read_range(
    api_base: str, token: str, spreadsheet_token: str, range_a1: str
) -> dict[str, Any]:
    enc = quote(range_a1, safe="")
    return _lark_get(
        api_base, token, f"/sheets/v2/spreadsheets/{spreadsheet_token}/values/{enc}", None
    )


def _find_sheet_header_row(
    values: list[list[Any]],
) -> tuple[int, int, int, int] | None:
    """与 ``_find_smoke_sheet_header``（xlsx）一致：在二维数组中找表头行与列下标（0-based）。"""
    max_r = min(len(values), 45)
    max_c = 40
    for r in range(max_r):
        row = values[r] if r < len(values) else []
        col_map: dict[str, int] = {}
        for c in range(min(len(row), max_c) if row else 0):
            v = row[c] if c < len(row) else None
            s = _cell_to_text(v).strip()
            if s and s not in col_map:
                col_map[s] = c
        if "结果" not in col_map or "备注" not in col_map:
            continue
        item_col: int | None = None
        for k in ("测试项目", "测试项", "用例名称"):
            if k in col_map:
                item_col = col_map[k]
                break
        if item_col is None:
            continue
        return r, item_col, col_map["结果"], col_map["备注"]
    return None


def _lark_sheet_id_row(s: dict[str, Any]) -> str:
    """飞书 metainfo 工作表为 camelCase：``sheetId``（见开放平台文档）。"""
    v = s.get("sheetId") or s.get("sheet_id")
    return str(v).strip() if v is not None else ""


def _lark_sheets_grid_only(
    sheets: list[dict[str, Any]], log: Callable[[str], None]
) -> list[dict[str, Any]]:
    """含 ``blockInfo`` 的子表非行列网格，API 的 range 写入不适用，须跳过。"""
    out: list[dict[str, Any]] = []
    for s in sheets:
        blk = s.get("blockInfo") or s.get("block_info")
        if isinstance(blk, dict) and blk:
            continue
        if _lark_sheet_id_row(s):
            out.append(s)
    if (len(sheets) - len(out)) > 0:
        log(
            f"  [lark·sheet] 已从 metainfo 的 {len(sheets)} 个子表中筛掉 "
            f"{len(sheets) - len(out)} 个非网格子页（blockInfo）。"
        )
    return out


def _resolve_lark_worksheet_id(
    meta: dict[str, Any],
    prefer: str | None,
    log: Callable[[str], None],
) -> str | None:
    raw = (meta.get("data") or {}) if isinstance(meta.get("data"), dict) else {}
    raw_sheets: list[dict[str, Any]] = raw.get("sheets", []) or []
    sheets = _lark_sheets_grid_only(raw_sheets, log)
    pref = (prefer or "").strip()
    if pref:
        for s in sheets:
            if _lark_sheet_id_row(s) == pref:
                return pref
        log(f"  [lark·sheet] 未在「可写网格子表」中找到 id={pref!r}，将按标题「冒烟」或首表回退。")
    for s in sheets:
        title = str(s.get("title") or s.get("name") or "")
        if "冒烟" in title:
            sid = _lark_sheet_id_row(s)
            if sid:
                return sid
    if sheets:
        sid = _lark_sheet_id_row(sheets[0])
        if sid:
            return sid
    return None


def _write_k11_to_lark_wiki_sheet(
    *,
    api_base: str,
    token: str,
    spreadsheet_token: str,
    case_to_item_key: dict[str, str],
    results: list[dict[str, Any]],
    prefer_sheet_id: str | None,
    log: Callable[[str], None],
) -> int:
    """
    向 Wiki 内嵌电子表格回写结果（与 xlsx 匹配逻辑一致）。
    使用 ``/sheets/v2/.../values_batch_update`` 批量写入。
    """
    st_short = f"…{spreadsheet_token[-10:]}" if len(spreadsheet_token) > 10 else spreadsheet_token
    log(
        f"  [lark·sheet] 开始：spreadsheetToken={st_short}（共 {len(spreadsheet_token)} 字符）"
    )
    log(f"  [lark·sheet] GET …/sheets/v2/spreadsheets/…/metainfo")

    metainfo = _lark_get(
        api_base, token, f"/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo", None
    )
    if metainfo.get("code") != 0:
        log(
            f"  [lark·sheet] metainfo 失败：code={metainfo.get('code')} msg={metainfo.get('msg', metainfo)}；"
            "请确认应用已开通电子表格/sheets:spreadsheet 并已被加入该表格协作者。"
        )
        return 0

    d = metainfo.get("data")
    dkeys = list(d.keys()) if isinstance(d, dict) else []
    log(f"  [lark·sheet] metainfo 成功：data 顶层键 = {dkeys!r}")
    raw_sheets: list = (d or {}).get("sheets", []) or [] if isinstance(d, dict) else []
    log(f"  [lark·sheet] 原始子表数 = {len(raw_sheets)}")
    for i, s in enumerate(raw_sheets[:12]):
        if not isinstance(s, dict):
            log(f"  [lark·sheet]   [{i}] (非 object，略)")
            continue
        bid = _lark_sheet_id_row(s)
        bi = s.get("blockInfo") or s.get("block_info")
        blk = "有 blockInfo(非网格)" if (isinstance(bi, dict) and bi) else "网格"
        log(
            f"  [lark·sheet]   [{i}] sheetId={bid!r} title={str(s.get('title') or '')!r} {blk}"
        )
    if len(raw_sheets) > 12:
        log(f"  [lark·sheet]   … 另有 {len(raw_sheets) - 12} 个子表未列出")

    env_sheet = (os.environ.get("K11_SMOKE_LARK_SHEET_ID") or "").strip() or None
    pref = (prefer_sheet_id or env_sheet or "").strip() or None
    if pref:
        log(f"  [lark·sheet] 优先子表 id（URL sheet= 或 K11_SMOKE_LARK_SHEET_ID）= {pref!r}")
    else:
        log("  [lark·sheet] 未指定优先子表，将按标题含「冒烟」或第一个可写网格子表。")

    sheet_id = _resolve_lark_worksheet_id(metainfo, prefer_sheet_id or env_sheet, log)
    if not sheet_id:
        log(
            "  [lark·sheet] 无可用子表：可能 metainfo 中无 sheets、"
            "或均为 block 子页、或 sheetId 字段解析失败（需 sheetId 不能仅靠 sheet_id）。"
        )
        return 0
    log(f"  [lark·sheet] 选用子表 sheetId={sheet_id!r}，将读范围 {sheet_id}!A1:ZZ500")

    range_a1 = f"{sheet_id}!A1:ZZ500"
    val_resp = _lark_sheet_read_range(api_base, token, spreadsheet_token, range_a1)
    if val_resp.get("code") != 0:
        log(
            f"  [lark·sheet] 读单元格失败：code={val_resp.get('code')} msg={val_resp.get('msg', val_resp)}"
        )
        return 0
    vr = val_resp.get("data") or {}
    values = (vr.get("valueRange") or {}).get("values") or vr.get("values") or []
    if not isinstance(values, list) or not values:
        log("  [lark·sheet] 表为空或无可读范围（valueRange.values 无数据）。")
        return 0
    log(f"  [lark·sheet] 已拉取 {len(values)} 行（最多扫前 500 行找表头）。")

    parsed = _find_sheet_header_row(values)
    if not parsed:
        log(
            "  [lark·sheet] 未找到表头行（需同时含列：结果、备注 与 测试项目/测试项/用例名称 之一）。"
        )
        return 0
    hdr, c_item, c_res, c_note = parsed
    c_item_l = _col_1based_to_letters(c_item + 1)
    c_res_l = _col_1based_to_letters(c_res + 1)
    c_note_l = _col_1based_to_letters(c_note + 1)
    log(
        f"  [lark·sheet] 表头行=第 {hdr + 1} 行（1-based）；"
        f"列 测试项≈{c_item_l} 结果={c_res_l} 备注={c_note_l}"
    )
    log("  [lark·sheet] 结果列按复选框写入：PASS→1，其它 verdict→0。")
    last_r = min(len(values), 500)

    _remark_max = 30000

    value_ranges: list[dict[str, Any]] = []
    n_sched = 0
    for resrow in results:
        cid = str(resrow.get("case") or "")
        key = case_to_item_key.get(cid)
        if not key:
            continue
        v = str(resrow.get("verdict") or "")
        cell_v = _lark_sheet_result_checkbox_01(v)
        remark = (str(resrow.get("detail") or ""))[:_remark_max]
        for r0 in range(hdr + 1, last_r):
            row = values[r0] if r0 < len(values) else []
            t = _cell_to_text(row[c_item] if c_item < len(row) else None).strip()
            if not t:
                continue
            if key not in t:
                continue
            excel_row = r0 + 1
            c1 = _col_1based_to_letters(c_res + 1)
            c2 = _col_1based_to_letters(c_note + 1)
            rng = f"{sheet_id}!{c1}{excel_row}:{c2}{excel_row}"
            value_ranges.append(
                {
                    "range": rng,
                    "values": [[cell_v, remark]],
                }
            )
            n_sched += 1
            break

    if not value_ranges:
        log(
            "  [lark·sheet] 无匹配行（请确认「测试项目」列文案含与脚本一致的关键词，如「环境访问」「首页加载」）。"
        )
        return 0

    log(f"  [lark·sheet] 待写回 {len(value_ranges)} 个范围（与用例条数一致则正常）。")
    if len(value_ranges) > 90:
        value_ranges = value_ranges[:90]
        log("  [lark·sheet] 单次超过 90 个范围，已截断。")

    body = {"valueRanges": value_ranges}
    log("  [lark·sheet] POST …/sheets/v2/spreadsheets/…/values_batch_update")
    out = _lark_post(
        api_base,
        token,
        f"/sheets/v2/spreadsheets/{spreadsheet_token}/values_batch_update",
        body,
    )
    if out.get("code") != 0:
        log(
            f"  [lark·sheet] values_batch_update 失败：code={out.get('code')} msg={out.get('msg', out)}；"
            "请确认协作者含编辑权限。"
        )
        return 0
    n_ok = n_sched
    rev = (out.get("data") or {}).get("revision", "")
    log(
        f"  [lark·sheet] 已写入飞书电子表格 {n_ok} 处；子表={sheet_id!r}；"
        f"revision={rev!r} spreadsheet …{spreadsheet_token[-8:]!r}"
    )
    return n_ok


def _result_field_value(
    field: dict[str, Any], verdict: str, verdict_cell: dict[str, str]
) -> Any:
    """按列类型将 PASS/FAIL 等写入为多维表 API 可接受的值。"""
    ftype = field.get("type")
    v = verdict_cell.get(verdict, verdict)
    # 1 Text 2Number 3SingleSelect 4MultiSelect 5DateTime 7Checkbox ...
    if ftype in (1, 2):
        return v
    if ftype == 3:
        prop = field.get("property") or {}
        options = prop.get("options") or []
        for opt in options:
            if (opt.get("name") or "").strip().upper() == v.strip().upper():
                return opt.get("id")
        for opt in options:
            if v.strip().upper() in (opt.get("name") or "").upper():
                return opt.get("id")
        return v
    if ftype in (0, 15, 18):
        return v
    return v


def write_k11_unified_results_to_lark_bitable(
    *,
    case_to_item_key: dict[str, str],
    results: list[dict[str, Any]],
    wiki_url: str,
    app_id: str,
    app_secret: str,
    table_id: str | None,
    log: Callable[[str], None],
) -> int:
    """
    将 results 写回知识库内嵌子文档：支持 **多维表（bitable）** 与 **电子表格（sheet）**。
    列匹配与 xlsx 相同（「测试项目/结果/备注」及用例关键词）。

    返回成功更新的行/单元格批次数，失败为 0（并打日志，不 raise）。
    """
    try:
        from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
        from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import (
            parse_wiki_url,
            sanitize_wiki_url,
        )
    except ImportError as e:
        log(f"  [lark] 导入 l3_node 失败，跳过：{e}")
        return 0

    # 写 Wiki/表：仅使用 .env 中 K11_SMOKE_LARK_APP_ID / K11_SMOKE_LARK_APP_SECRET（忽略调用方传入的 app_id/secret）
    _apply_k11_smoke_lark_env()
    aid = (os.environ.get("K11_SMOKE_LARK_APP_ID") or "").strip()
    sec = (os.environ.get("K11_SMOKE_LARK_APP_SECRET") or "").strip()
    yb: str | None = None
    if not aid or not sec:
        log(
            "  [lark] 无可用应用凭证；请在 .env 设置 K11_SMOKE_LARK_APP_ID 与 K11_SMOKE_LARK_APP_SECRET。跳过飞书写入。"
        )
        return 0

    log_k11_lark_runtime_identity(
        log,
        phase="写Wiki/表格",
        effective_app_id=aid,
        effective_app_secret=sec,
    )

    try:
        import requests
    except ImportError:
        log("  [lark] 未安装 requests，跳过")
        return 0

    wiki_url = sanitize_wiki_url((wiki_url or "").strip() or _DEFAULT_WIKI_URL)
    parsed = parse_wiki_url(wiki_url)
    node_token = (parsed.get("node_token") or "").strip()
    url_table = (parsed.get("table_id") or "").strip() or None
    if not node_token:
        log("  [lark] Wiki URL 中无有效 node token，跳过")
        return 0

    api_base = (yb or get_lark_api_base()).rstrip("/")
    try:
        token = get_tenant_access_token(
            app_id=aid, app_secret=sec, api_base=yb or get_lark_api_base()
        )
    except Exception as e:
        log(f"  [lark] 获取 tenant_access_token 失败：{e!s}")
        return 0

    g = _lark_get(api_base, token, "/wiki/v2/spaces/get_node", {"token": node_token})
    if g.get("code") != 0:
        log(f"  [lark] wiki get_node 失败：{g.get('msg', g)}")
        return 0
    raw = g.get("data") or {}
    node = raw.get("node") if isinstance(raw.get("node"), dict) else raw
    if not isinstance(node, dict):
        log("  [lark] get_node 返回异常，跳过")
        return 0
    obj_type = (node.get("obj_type") or "").lower()
    app_token = (node.get("obj_token") or "").strip()
    url_sheet = (parsed.get("sheet_id") or "").strip() or None

    if obj_type == "sheet" and app_token:
        return _write_k11_to_lark_wiki_sheet(
            api_base=api_base,
            token=token,
            spreadsheet_token=app_token,
            case_to_item_key=case_to_item_key,
            results=results,
            prefer_sheet_id=url_sheet,
            log=log,
        )

    if obj_type != "bitable" or not app_token:
        log(
            f"  [lark] 该 Wiki 节点无法同步冒烟结果：obj_type={obj_type!r}。"
            "请使用内嵌「多维表」或「电子表格（与 Excel 同布局的 sheet）」节点；"
            "电子表格链接可带 `?sheet=子表id` 指定工作表。"
        )
        return 0

    use_table = (table_id or url_table or "").strip() or None
    if not (use_table or "").startswith("tbl"):
        use_table = _resolve_table_id(api_base, token, app_token, use_table)
    if not use_table:
        log("  [lark] 无法解析子表 table_id，请设环境变量 K11_SMOKE_LARK_TABLE_ID= tbl...")
        return 0

    fields_data = _lark_get(
        api_base, token, f"/bitable/v1/apps/{app_token}/tables/{use_table}/fields", {}
    )
    if fields_data.get("code") != 0:
        log(f"  [lark] 拉取列失败：{fields_data.get('msg', fields_data)}")
        return 0
    field_items: list[dict[str, Any]] = (
        fields_data.get("data", {}).get("items", []) or []
    )
    by_name: dict[str, dict[str, Any]] = {}
    for f in field_items:
        fn = (f.get("field_name") or f.get("name") or "").strip()
        if fn and fn not in by_name:
            by_name[fn] = f

    def _pick_col(
        cands: tuple[str, ...], fallback: str
    ) -> dict[str, Any] | None:
        for c in cands:
            if c in by_name:
                return by_name[c]
        for name, meta in by_name.items():
            if fallback in name:
                return meta
        return None

    col_item = _pick_col(("测试项目", "测试项", "用例名称"), "测试")
    col_res = _pick_col(("结果", "测试结论", "通过"), "果")
    col_note = _pick_col(("备注", "说明", "说明信息"), "注")
    if not col_item or not col_res:
        log(
            f"  [lark] 表中未找到「测试项目/结果」列，已有列名：{list(by_name.keys())[:20]}..."
        )
        return 0

    fid_item = str(col_item.get("field_id") or "")
    fid_res = str(col_res.get("field_id") or "")
    fid_note = str(col_note.get("field_id") or "") if col_note else ""

    # 拉取全表记录（分页）
    all_recs: list[dict[str, Any]] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        url = f"{api_base}/bitable/v1/apps/{app_token}/tables/{use_table}/records"
        r = requests.get(
            url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=120
        )
        try:
            data = r.json()
        except Exception:
            break
        if data.get("code") != 0:
            log(f"  [lark] 拉取记录失败：{data.get('msg', data)}")
            return 0
        all_recs.extend(data.get("data", {}).get("items", []) or [])
        page_token = (data.get("data") or {}).get("page_token")
        if not page_token:
            break

    verdict_cell = {
        "PASS": "PASS",
        "FAIL": "FAIL",
        "SKIP": "SKIP",
        "BLOCKED": "BLOCKED",
    }
    _remark_max = 30000
    to_update: list[dict[str, Any]] = []

    for resrow in results:
        cid = str(resrow.get("case") or "")
        key = case_to_item_key.get(cid)
        if not key:
            continue
        v = str(resrow.get("verdict") or "")
        detail = (str(resrow.get("detail") or ""))[:_remark_max]
        res_val = _result_field_value(col_res, v, verdict_cell)
        for rec in all_recs:
            fields = rec.get("fields") or {}
            hit = False
            for _fid, val in fields.items():
                t = _cell_to_text(val)
                if t and key in t:
                    hit = True
                    break
            if not hit:
                continue
            r_id = (rec.get("record_id") or "").strip()
            if not r_id:
                continue
            patch: dict[str, Any] = {fid_res: res_val}
            if fid_note:
                patch[fid_note] = detail
            to_update.append({"record_id": r_id, "fields": patch})
            break

    if not to_update:
        log("  [lark] 无匹配行（检查「测试项目」列是否含与脚本一致的关键字）。")
        return 0

    n_ok = 0
    for i in range(0, len(to_update), 500):
        chunk = to_update[i : i + 500]
        body = {"records": chunk}
        data = _lark_post(
            api_base,
            token,
            f"/bitable/v1/apps/{app_token}/tables/{use_table}/records/batch_update",
            body,
        )
        if data.get("code") != 0:
            log(
                f"  [lark] batch_update 失败：{data.get('msg', data)}；"
                "请确认应用有多维表编辑权限，且目标 Base 已添加机器人为协作者。"
            )
            return n_ok
        n_ok += len(chunk)
    log(
        f"  [lark] 已写入飞书多维表 {n_ok} 行（子表 {use_table!r}）"
        f" 链接：{wiki_url}"
    )
    return n_ok


def _k11_lark_md_escape(s: str, max_len: int = 2000) -> str:
    """lark_md 列内弱转义，减少换行/管道符破坏展示。"""
    t = (s or "").replace("\r", " ").replace("\n", " ").replace("|", "｜")
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t


def _k11_result_column_lark_md(verdict: str, verdict_zh: str) -> str:
    """结果列：参考巡检卡用 🟢/🔴/🟡（PASS/失败或阻塞/跳过）。"""
    m = str(verdict or "").strip().upper()
    z = (verdict_zh or "").strip() or m
    if m == "PASS":
        return f"🟢 {z}"
    if m == "SKIP":
        return f"🟡 {z}"
    if m in ("FAIL", "BLOCKED"):
        return f"🔴 {z}"
    return f"🟡 {z}"


def _k11_result_column_plain(verdict: str, verdict_zh: str) -> str:
    """无 emoji，供「体积/兼容性」降级后的结果列（text）。"""
    return _k11_lark_md_escape(str(verdict_zh or verdict or "").strip() or "—", 32)


def _build_k11_smoke_table_element(
    results: list[dict[str, Any]],
    *,
    title_max: int = 200,
    remark_max: int = 240,
    plain_result: bool = False,
) -> dict[str, Any]:
    """
    飞书消息卡片 1.0 原生 table（需客户端 ≥7.4）。列：测试项目、结果、备注。

    **备注/标题必须截断**：交互卡 ``content`` 有体积极限，行数一多+长 detail 会首包失败，触发旧版 lark_md 回退。

    文档：https://open.larksuite.com/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components/content-components/table
    """
    rows: list[dict[str, Any]] = []
    for r in results:
        tzh = _k11_lark_md_escape(
            str(r.get("case_title_zh") or r.get("case") or ""), max(60, title_max)
        )
        v_raw = r.get("verdict")
        v = str(v_raw if v_raw is not None else "").strip()
        vzh = str(r.get("verdict_zh") or v or "").strip()
        if plain_result:
            res_val = _k11_result_column_plain(v, vzh)
        else:
            res_val = _k11_result_column_lark_md(v, vzh)
        note = _k11_lark_md_escape(str(r.get("detail") or ""), max(40, remark_max))
        if not note:
            note = "—"
        elif len(str(r.get("detail") or "")) > remark_max and remark_max > 0:
            note = note.rstrip() + "（表内截断，全文见飞书/JSON）"
        rows.append(
            {
                "c_item": tzh or "—",
                "c_res": res_val,
                "c_note": note,
            }
        )
    if not rows:
        rows = [
            {"c_item": "—", "c_res": "—", "c_note": "无结果行"},
        ]
    res_col: dict[str, Any] = {
        "name": "c_res",
        "display_name": "结果",
        "data_type": "text" if plain_result else "lark_md",
        "horizontal_align": "center",
    }
    return {
        "tag": "table",
        "page_size": 10,
        "row_height": "low",
        "header_style": {
            "text_align": "center",
            "background_style": "grey",
            "bold": True,
        },
        "columns": [
            {
                "name": "c_item",
                "display_name": "测试项目",
                "data_type": "text",
                "width": "auto",
                "horizontal_align": "left",
            },
            res_col,
            {
                "name": "c_note",
                "display_name": "备注",
                "data_type": "text",
                "width": "auto",
                "horizontal_align": "left",
            },
        ],
        "rows": rows,
    }


def _k11_card_table_json_size(card: dict[str, Any]) -> int:
    try:
        return len(json.dumps(card, ensure_ascii=False))
    except Exception:
        return 0


def send_k11_smoke_lark_notification(
    *,
    results: list[dict[str, Any]],
    target_url: str,
    wiki_url: str,
    lark_wrote: int,
    app_id: str,
    app_secret: str,
    chat_id: str,
    log: Callable[[str], None],
) -> bool:
    """发送完成通知：优先「原生 table + 表头 + 汇总」交互卡片，失败则降级 lark_md 块/纯文本。

    应用与会话 **仅** 从环境变量读取：``K11_SMOKE_LARK_APP_ID``、``K11_SMOKE_LARK_APP_SECRET``、
    ``K11_SMOKE_LARK_NOTIFY_CHAT_ID``（与调用方传入的 app_id/app_secret/chat_id 无关，避免与 LARK_APP_* 混淆）。
    """
    _apply_k11_smoke_lark_env()
    aid = (os.environ.get("K11_SMOKE_LARK_APP_ID") or "").strip()
    sec = (os.environ.get("K11_SMOKE_LARK_APP_SECRET") or "").strip()
    yb: str | None = None
    if not aid or not sec:
        log(
            "  [lark] 发消息：无应用凭证。请在 .env 设置 K11_SMOKE_LARK_APP_ID 与 K11_SMOKE_LARK_APP_SECRET。跳过通知。"
        )
        return False
    cid = (os.environ.get("K11_SMOKE_LARK_NOTIFY_CHAT_ID") or "").strip()
    if not cid:
        log("  [lark] 发消息：未设置 K11_SMOKE_LARK_NOTIFY_CHAT_ID，跳过通知。")
        return False
    log_k11_lark_runtime_identity(
        log,
        phase="发通知",
        effective_app_id=aid,
        effective_app_secret=sec,
        send_chat_id=cid,
    )
    try:
        from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
        from l3_node.channels.lark.im import send_interactive_card, send_text
    except Exception as e:
        log(f"  [lark] 发消息：导入失败 {e!s}")
        return False

    case_lines: list[str] = []
    for r in results:
        tier = (r.get("tier") or "").strip()
        tzh = (r.get("case_title_zh") or r.get("case") or "").strip()
        vzh = (r.get("verdict_zh") or r.get("verdict") or "").strip()
        mark = "✓" if r.get("verdict") == "PASS" else ("○" if r.get("verdict") == "SKIP" else "✗")
        case_lines.append(f"{mark} [{tier}] {tzh} → {vzh}")
    fails = [r for r in results if r.get("verdict") in ("FAIL", "BLOCKED")]
    if fails:
        tail = (
            f"未通过/阻塞共 {len(fails)} 条，请从 Wiki 与本地 JSON 查看 detail。"
        )
    else:
        tail = "本轮回报：无 FAIL / BLOCKED。"

    md = "\n".join(
        [
            f"**目标** {K11_LARK_CARD_TARGET_URL}",
            f"**飞书表更新** {lark_wrote} 行；[Wiki]({wiki_url})",
            "",
            *case_lines,
            "",
            tail,
        ]
    )
    if len(md) > 12000:
        md = md[:11800] + "\n\n…（已截断）"

    text_plain = "\n".join(
        [
            f"{K11_LARK_CARD_TITLE}（纯文本）",
            f"目标：{K11_LARK_CARD_TARGET_URL}",
            f"飞书表更新：{lark_wrote} 行；Wiki：{wiki_url}",
            "",
            *case_lines,
            "",
            tail,
        ]
    )
    if len(text_plain) > 15000:
        text_plain = text_plain[:14900] + "\n…（已截断）"

    try:
        api_base = (yb or get_lark_api_base()).rstrip("/")
        token = get_tenant_access_token(
            app_id=aid, app_secret=sec, api_base=yb or get_lark_api_base()
        )
    except Exception as e:
        log(f"  [lark] 发消息：token 失败 {e!s}")
        return False

    summary_md = "\n".join(
        [
            f"**目标** {_k11_lark_md_escape(K11_LARK_CARD_TARGET_URL, 2000)}",
            f"**飞书表** 已回写 **{lark_wrote}** 行 · [打开 Wiki]({wiki_url})",
            f"**汇总** {tail}（共 {len(results)} 条；下表备注在卡内已截断，完整见 Wiki/JSON）",
        ]
    )

    def _card_with_table(table: dict[str, Any]) -> dict[str, Any]:
        return {
            "config": {"wide_screen_mode": True, "enable_forward": True},
            "header": {
                "template": "blue",
                "title": {
                    "tag": "plain_text",
                    "content": K11_LARK_CARD_TITLE,
                },
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": summary_md}},
                {"tag": "hr"},
                table,
            ],
        }

    table_attempts: list[tuple[int, int, bool, str]] = [
        (240, 200, False, "表格·默认截断"),
        (120, 160, False, "表格·收紧备注"),
        (64, 96, True, "表格·结果列改纯文本"),
    ]
    r: dict[str, Any] = {"status": "error", "error": "未尝试"}
    for i, (rem, tit, plain, tag) in enumerate(table_attempts):
        te = _build_k11_smoke_table_element(
            results, remark_max=rem, title_max=tit, plain_result=plain
        )
        card_table = _card_with_table(te)
        jsz = _k11_card_table_json_size(card_table)
        if jsz > _K11_CARD_JSON_SOFT_MAX and i < len(table_attempts) - 1:
            log(
                f"  [lark] 卡片 JSON≈{jsz}B（>{_K11_CARD_JSON_SOFT_MAX}），"
                f"跳过本档，改用更紧截断…"
            )
            continue
        r = send_interactive_card(
            receive_id=cid,
            card=card_table,
            receive_id_type="chat_id",
            token=token,
            api_base=api_base,
            http_timeout=55.0,
        )
        if r.get("status") == "success":
            log(
                f"  [lark] 已发送完成通知（原生表格·{tag}，{len(results)} 行，JSON≈{jsz}B）"
                f"到会话 {cid[:20]}…"
            )
            return True
        err = str(r.get("error", r))
        lc = r.get("lark_code", "")
        log(
            f"  [lark] 原生表格失败（{tag}） err={err!r}"
            + (f" code={lc!r}" if lc != "" else "")
        )

    log(
        f"  [lark] 全部表格策略未成功，最后一次：{r.get('error', r)!r}；"
        "降级 lark_md 列表（非表格）。"
    )
    card_md_only: dict[str, Any] = {
        "config": {"wide_screen_mode": True, "enable_forward": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": K11_LARK_CARD_TITLE},
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": md}}],
    }
    r_md = send_interactive_card(
        receive_id=cid,
        card=card_md_only,
        receive_id_type="chat_id",
        token=token,
        api_base=api_base,
        http_timeout=45.0,
    )
    if r_md.get("status") == "success":
        log(f"  [lark] 已发送完成通知（lark_md 卡片）到会话 {cid[:20]}…")
        return True
    log(f"  [lark] lark_md 卡片也失败，改发纯文本：{r_md.get('error', r_md)}")
    r2 = send_text(
        receive_id=cid,
        text=text_plain,
        receive_id_type="chat_id",
        token=token,
        api_base=api_base,
    )
    if r2.get("status") == "success":
        log(f"  [lark] 已发送完成通知（纯文本）到会话 {cid[:20]}…")
        return True
    log(f"  [lark] 发送消息失败：{r2.get('error', r2)}")
    return False
