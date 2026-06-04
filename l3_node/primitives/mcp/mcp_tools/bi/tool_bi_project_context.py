"""
BI 项目上下文同步 — mcp:atom_bi_project_context

从配置的一组 Lark 知识库 Wiki 链接拉取：节点元信息、多维表格全量（或指定子表）、
电子表格指定 sheet、新版文档 docx 正文，并从正文中发现同域 Wiki 链接做有限深度跟抓。
产出写入项目 docs/bi_daily_report/bi_project/，供 BI 分析理解业务背景。

配置: config/mcps/atom_bi_project_context/config.yaml（或 ~/.jachin/config/mcps/...）
需自建应用具备：知识库只读/编辑、云文档只读、电子表格只读、多维表格只读等权限；
应用须被加入对应知识空间为成员。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse, urlencode, urlunparse

_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_REL = "docs/bi_daily_report/bi_project"
# 防止异常表无限分页；可用环境变量抬高
BITABLE_RECORD_HARD_CAP = int(os.environ.get("JACHIN_BITABLE_RECORD_HARD_CAP", "250000"))
# 百科子节点 / 正文内链接展开预算（种子 URL 不受此限，仍可逐个落盘）
DISCOVERED_NODE_BUDGET = int(os.environ.get("JACHIN_BI_WIKI_DISCOVER_BUDGET", "200"))


def _manifest_file_relpath(path: Path, project_root: Path, out_dir: Path) -> str:
    """manifest.files 条目：在仓库下则相对 project_root；否则相对本次 out_dir；再否则绝对路径。"""
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        pass
    try:
        return str(path.resolve().relative_to(out_dir.resolve()))
    except ValueError:
        return str(path.resolve())
WIKI_LINK_RE = re.compile(
    r"https://[a-zA-Z0-9.-]*(?:larksuite\.com|feishu\.cn)/wiki/([A-Za-z0-9]+)",
    re.IGNORECASE,
)


def _default_wiki_urls() -> list[str]:
    """内置种子链接（已去掉一次性登录 token，仅保留业务 query）。"""
    return [
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=ldxeuHgiN5L2gXBH",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblNdv7DIlycuqxp&view=vew8TxMcSh",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblNdv7DIlycuqxp&view=vewL9Mofgd",
        # K11 开发多维表 B19… 同表 tblfK9… 多视图（各 view 独立过滤/排序，须分别拉取）
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewpI8lyYw",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewjSEz5Xr",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewCz1FFJi",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vew4Im7GO3",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewpxQxeGw",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewQKcyDAV",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewpYzbZ29",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewswB05Wi",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vew0gcyAUk",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/DiSnwVB1OiDvPWkk0W9lzx6AgLd?table=tblDw87UlhddFIoY&view=vew5taB9H1",
        # 勿默认种子「测试记录」TO3twk…：会 recurse 出多张子表 MD，易导致 PMO 记忆噪声；需要时仅在配置中显式写 wiki_urls
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/HS8qw9XvDiN7u3kolkOlEplCgvb?sheet=3045da",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/FxOlwGQOniz5H2k5qiYl4Fzgggb?sheet=eIdFLx",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/UVR8wY8NUiyItnkA5dal4De7gUh?fromScene=spaceOverview",
    ]


def sanitize_wiki_url(url: str) -> str:
    """去掉 disposable_login_token 等敏感或无用 query，避免泄露。"""
    u = urlparse((url or "").strip())
    if not u.scheme or not u.netloc:
        return (url or "").strip()
    qs = parse_qs(u.query, keep_blank_values=True)
    drop = {"disposable_login_token", "token", "from", "fromScene"}
    for k in list(qs.keys()):
        if k in drop:
            del qs[k]
    # 每项取完整查询值 v（勿写 v[0]，否则会把 table=tblXXX 截成 table=t）
    new_q = urlencode([(k, v) for k, vals in sorted(qs.items()) for v in vals if vals], doseq=True)
    return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))


def parse_wiki_url(url: str) -> dict[str, Any]:
    u = urlparse((url or "").strip())
    parts = [p for p in u.path.split("/") if p]
    node_token = ""
    if "wiki" in parts:
        i = parts.index("wiki")
        if i + 1 < len(parts):
            node_token = parts[i + 1]
    qs = parse_qs(u.query)
    return {
        "node_token": node_token,
        "table_id": (qs.get("table") or [None])[0],
        "sheet_id": (qs.get("sheet") or [None])[0],
        "view_id": (qs.get("view") or [None])[0],
        "raw_url": url.strip(),
    }


def _safe_name(s: str, max_len: int = 72) -> str:
    s = (s or "").strip() or "untitled"
    s = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", s)
    return s[:max_len]


# K11 PMO：飞书多维表 view_id → 落盘文件名中的中文语义片段（便于 Agent 检索；末尾仍带 view_id 防碰撞）
# 与 skills_repo/pmo-copilot/SKILL.md §1.1 视图含义对齐。
_K11_WIKI_VIEW_SLUG_BY_VIEW_ID: dict[str, str] = {
    "vew8TxMcSh": "产品任务需求完成度与人员分配",
    "vewL9Mofgd": "产品端人员任务看板_按人员分组",
    "vewpI8lyYw": "开发计划核心版本需求_任务完成度与人员",
    "vewjSEz5Xr": "人工甘特图_人员与任务周期",
    "vewCz1FFJi": "人工看板_按员工任务与执行情况",
    "vew4Im7GO3": "任务甘特_各任务甘特",
    "vewpxQxeGw": "任务看板_已完成",
    "vewQKcyDAV": "任务看板_未完成",
    "vewpYzbZ29": "产品方任务",
    "vewswB05Wi": "设计方任务",
    "vew0gcyAUk": "开发方任务",
    "vew5taB9H1": "设计专用_美术视图",
}


def _bitable_filename_semantic_slug(view_id: str | None) -> str:
    vid = (view_id or "").strip()
    if not vid:
        return ""
    return _K11_WIKI_VIEW_SLUG_BY_VIEW_ID.get(vid, "")


def _cell_to_text(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, list):
        parts = [_cell_to_text(x) for x in val]
        return "; ".join(p for p in parts if p)
    if isinstance(val, dict):
        lr = val.get("link_record_ids")
        if isinstance(lr, list) and lr:
            part = "; ".join(str(x) for x in lr[:12])
            if len(lr) > 12:
                part += f" …(+{len(lr) - 12})"
            return part
        if "text" in val:
            t = val.get("text")
            link = val.get("link")
            if link:
                return f"{t} ({link})" if t else str(link)
            return str(t) if t is not None else ""
        if val.get("type") == "url" and val.get("link"):
            return str(val.get("link"))
        if "name" in val:
            return str(val.get("name", ""))
        return json.dumps(val, ensure_ascii=False)[:500]
    return str(val)[:500]


def _extract_link_record_ids(val: Any) -> list[str]:
    """解析多维表「单向/双向关联」等单元格中的 record_id（飞书常见 `link_record_ids`）。"""
    out: list[str] = []
    if val is None:
        return out
    if isinstance(val, str) and val.startswith("rec"):
        return [val]
    if isinstance(val, list):
        for x in val:
            out.extend(_extract_link_record_ids(x))
        return out
    if isinstance(val, dict):
        lr = val.get("link_record_ids")
        if isinstance(lr, list):
            for x in lr:
                if isinstance(x, str):
                    out.append(x)
        rid = val.get("record_id")
        if isinstance(rid, str) and rid.startswith("rec"):
            out.append(rid)
        return out
    return out


_PARENT_COL_EXACT = (
    "parent items",
    "parent item",
    "父记录",
    "父项",
    "父任务",
    "parent",
)


def _is_parentish_column(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    if n in _PARENT_COL_EXACT:
        return True
    hints = ("parent", "父", "上级", "关联父")
    return any(h in n for h in hints)


def _pick_parent_column(col_order: list[str]) -> str | None:
    for key in _PARENT_COL_EXACT:
        for c in col_order:
            if c.strip().lower() == key:
                return c
    for c in col_order:
        if _is_parentish_column(c):
            return c
    return None


def _pick_title_column(col_order: list[str], parent_col: str | None) -> str | None:
    skip = {parent_col} if parent_col else set()
    hints = ("需求", "标题", "名称", "任务", "简述", "史诗", "主题")
    eng_h = ("title", "name", "task", "summary", "topic", "epic")
    for c in col_order:
        if c in skip:
            continue
        cl = c.lower()
        if any(h in c for h in hints):
            return c
        if any(h in cl for h in eng_h):
            return c
    for c in col_order:
        if c not in skip:
            return c
    return None


def _format_bitable_hierarchy(
    norm_rows: list[tuple[str, dict[str, Any]]],
    col_order: list[str],
    parent_col: str,
) -> list[str]:
    rid_to_norm: dict[str, dict[str, Any]] = {rid: m for rid, m in norm_rows if rid}
    id_set = set(rid_to_norm.keys())
    parent_of: dict[str, str] = {}
    for rid, m in norm_rows:
        if not rid:
            continue
        pids = _extract_link_record_ids(m.get(parent_col))
        par = pids[0] if pids else ""
        if not par or par == rid or par not in id_set:
            par = ""
        parent_of[rid] = par
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for rid, m in norm_rows:
        if not rid:
            continue
        p = parent_of.get(rid, "")
        if not p:
            roots.append(rid)
        else:
            children.setdefault(p, []).append(rid)
    order_idx = {rid: i for i, (rid, _) in enumerate(norm_rows)}
    roots.sort(key=lambda x: order_idx.get(x, 0))
    for pid in list(children.keys()):
        children[pid].sort(key=lambda x: order_idx.get(x, 0))

    title_col = _pick_title_column(col_order, parent_col)
    lines: list[str] = []
    path_stack: list[str] = []

    def visit(rid: str, depth: int) -> None:
        if rid in path_stack:
            ind = "  " * depth
            lines.append(f"{ind}- （父链成环，截断 `{rid[:18]}…`）")
            return
        path_stack.append(rid)
        try:
            m = rid_to_norm[rid]
            ind = "  " * depth
            head = ""
            if title_col:
                head = _cell_to_text(m.get(title_col)).strip().replace("\n", " ")
            if not head:
                head = f"`{rid[:18]}…`"
            tail_bits: list[str] = []
            for c in col_order:
                if c == parent_col or c == title_col:
                    continue
                if len(tail_bits) >= 5:
                    break
                raw = _cell_to_text(m.get(c)).strip().replace("\n", " ")
                if raw:
                    short = (c[:12] + "…") if len(c) > 12 else c
                    tail_bits.append(f"{short}: {raw[:56]}{'…' if len(raw) > 56 else ''}")
            tail = " · ".join(tail_bits)
            if tail:
                lines.append(f"{ind}- **{head}** · {tail}")
            else:
                lines.append(f"{ind}- **{head}**")
            for ch in children.get(rid, []):
                visit(ch, depth + 1)
        finally:
            path_stack.pop()

    for r in roots:
        visit(r, 0)
    return lines


class _LarkProjectClient:
    def __init__(self, api_base: str, tenant_token: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.tenant_token = tenant_token
        self._headers = {"Authorization": f"Bearer {tenant_token}"}

    def _get(self, path: str, params: dict | None = None, timeout: int = 60) -> dict[str, Any]:
        import requests

        url = f"{self.api_base}{path}" if path.startswith("/") else f"{self.api_base}/{path}"
        r = requests.get(url, headers=self._headers, params=params or {}, timeout=timeout)
        try:
            return r.json()
        except Exception:
            return {"code": -1, "msg": r.text[:500]}

    def wiki_get_node(self, node_token: str) -> dict[str, Any]:
        return self._get("/wiki/v2/spaces/get_node", params={"token": node_token})

    def wiki_list_children(self, space_id: str, parent_node_token: str | None = None) -> list[dict]:
        import requests

        items: list[dict] = []
        page_token = None
        while True:
            path = f"/wiki/v2/spaces/{space_id}/nodes"
            params: dict[str, Any] = {"page_size": 50}
            if parent_node_token:
                params["parent_node_token"] = parent_node_token
            if page_token:
                params["page_token"] = page_token
            url = f"{self.api_base}{path}"
            r = requests.get(url, headers=self._headers, params=params, timeout=60)
            data = r.json()
            if data.get("code") != 0:
                logger.warning("[bi_project_context] list_children failed: %s", data.get("msg", data))
                break
            chunk: list[dict] = data.get("data", {}).get("items") or []
            items.extend(chunk)
            page_token = data.get("data", {}).get("page_token")
            if not data.get("data", {}).get("has_more") or not chunk:
                break
        return items

    def docx_raw_content(self, document_id: str) -> tuple[str, str | None]:
        data = self._get(f"/docx/v1/documents/{document_id}/raw_content", timeout=90)
        if data.get("code") != 0:
            return "", data.get("msg") or json.dumps(data, ensure_ascii=False)[:300]
        content = data.get("data", {}).get("content")
        if content is None:
            content = data.get("data", {}).get("text", "")
        return (str(content) if content else ""), None

    def sheet_metainfo(self, spreadsheet_token: str) -> dict[str, Any]:
        return self._get(f"/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo")

    def sheet_range(self, spreadsheet_token: str, range_a1: str) -> dict[str, Any]:
        enc = quote(range_a1, safe="")
        return self._get(f"/sheets/v2/spreadsheets/{spreadsheet_token}/values/{enc}", timeout=120)

    def bitable_list_tables(self, app_token: str) -> list[dict]:
        import requests

        out: list[dict] = []
        page_token = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token
            url = f"{self.api_base}/bitable/v1/apps/{app_token}/tables"
            r = requests.get(url, headers=self._headers, params=params, timeout=60)
            data = r.json()
            if data.get("code") != 0:
                logger.warning("[bi_project_context] bitable list tables: %s", data.get("msg"))
                break
            # 用 `or []` 而非 `.get("items", [])` 防止 API 显式返回 null 时得到 None
            items: list[dict] = data.get("data", {}).get("items") or []
            out.extend(items)
            page_token = data.get("data", {}).get("page_token")
            if not page_token or not items:
                break
        return out

    def bitable_list_fields(self, app_token: str, table_id: str) -> list[dict]:
        data = self._get(f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields")
        if data.get("code") != 0:
            return []
        # 用 `or []` 防止 API 返回 `{"items": null}` 时 .get("items", []) 仍给出 None
        return data.get("data", {}).get("items") or []

    def bitable_list_records(
        self,
        app_token: str,
        table_id: str,
        max_records: int,
        view_id: str | None = None,
    ) -> list[dict]:
        import requests

        records: list[dict] = []
        page_token = None
        cap = max(1, min(max_records, BITABLE_RECORD_HARD_CAP))
        while len(records) < cap:
            params: dict[str, Any] = {"page_size": min(500, cap - len(records))}
            if page_token:
                params["page_token"] = page_token
            if view_id:
                params["view_id"] = view_id
            url = f"{self.api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            r = requests.get(url, headers=self._headers, params=params, timeout=120)
            data = r.json()
            if data.get("code") != 0:
                logger.warning("[bi_project_context] bitable records: %s", data.get("msg"))
                break
            # 用 `or []` 防止 API 显式返回 null 时 extend/for 抛 TypeError
            items: list[dict] = data.get("data", {}).get("items") or []
            records.extend(items)
            page_token = data.get("data", {}).get("page_token")
            has_more = data.get("data", {}).get("has_more")
            if len(records) >= cap:
                if page_token or has_more:
                    logger.warning(
                        "[bi_project_context] 表 %s 记录数已达上限 %s（view=%s），后续页未拉取",
                        table_id,
                        cap,
                        view_id or "",
                    )
                break
            if not page_token or not items:
                break
        return records


def _render_bitable_markdown(
    client: _LarkProjectClient,
    app_token: str,
    table_id: str | None,
    table_name_hint: str,
    max_records: int,
    *,
    view_id: str | None = None,
    emit_hierarchy: bool = True,
) -> tuple[str, dict[str, Any] | None]:
    lines: list[str] = [f"# 多维表格 {table_name_hint}", "", f"- app_token: `{app_token}`", ""]
    if view_id:
        lines.append(f"- **view_id**（记录按此视图过滤/排序，与飞书前端一致）: `{view_id}`")
        lines.append("")
    targets: list[dict] = []
    if table_id:
        # PMO/BI 种子 URL 已带 table= 时直接拉字段与记录，跳过 list tables（避免 WrongRequestBody 噪声）
        targets = [{"table_id": table_id, "name": table_name_hint or table_id}]
    else:
        tables = client.bitable_list_tables(app_token)
        if not tables:
            lines.append("（无法列出子表或无权访问）")
            return "\n".join(lines), None
        targets = tables

    export_tables: list[dict[str, Any]] = []
    for ti, tbl in enumerate(targets):
        tid = tbl.get("table_id", "")
        tname = tbl.get("name", tid)
        lines.append(f"## 子表 {ti + 1}: {tname}")
        lines.append("")
        fields = client.bitable_list_fields(app_token, tid)
        fid_to_name: dict[str, str] = {}
        field_names: list[str] = []
        for f in fields:
            fn = (f.get("field_name") or f.get("name") or "").strip()
            fid = f.get("field_id")
            if fn and fn not in field_names:
                field_names.append(fn)
            if fid and fn:
                fid_to_name[str(fid)] = fn
        recs = client.bitable_list_records(app_token, tid, max_records, view_id=view_id)

        def _norm_record_fields(flds: dict) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for k, v in (flds or {}).items():
                disp = fid_to_name.get(str(k), str(k))
                out[disp] = v
            return out

        norm_rows: list[tuple[str, dict[str, Any]]] = []
        for r in recs:
            rid = str(r.get("record_id") or "").strip()
            norm_rows.append((rid, _norm_record_fields(r.get("fields") or {})))
        norm_recs = [m for _, m in norm_rows]
        all_cols: set[str] = set()
        for m in norm_recs:
            all_cols.update(m.keys())
        col_order = [c for c in field_names if c in all_cols] + sorted(all_cols - set(field_names))
        if not col_order:
            col_order = ["(无列)"]
        lines.append(f"- 记录数（本次拉取上限 {max_records}）: {len(recs)}")
        lines.append("")
        parent_col = _pick_parent_column(col_order) if emit_hierarchy else None
        if parent_col and any(rid for rid, _ in norm_rows):
            lines.append(
                f"### 层级视图（按 `{parent_col}` 还原父子关系；完整字段仍以平面表为准）"
            )
            lines.append("")
            lines.extend(_format_bitable_hierarchy(norm_rows, col_order, parent_col))
            lines.append("")
        lines.append("| " + " | ".join(col_order) + " |")
        lines.append("| " + " | ".join(["---"] * len(col_order)) + " |")
        for m in norm_recs:
            row = [_cell_to_text(m.get(c)) for c in col_order]
            lines.append("| " + " | ".join(x.replace("|", "\\|").replace("\n", " ") for x in row) + " |")
        lines.append("")
        export_tables.append(
            {
                "table_id": tid,
                "name": tname,
                "columns": col_order,
                "records": [
                    {"record_id": rid, "fields": fields}
                    for rid, fields in norm_rows
                    if rid or fields
                ],
            }
        )
    records_export: dict[str, Any] | None = None
    if export_tables:
        records_export = {
            "kind": "bitable",
            "app_token": app_token,
            "view_id": view_id,
            "tables": export_tables,
        }
    return "\n".join(lines), records_export


def _render_sheet_markdown(client: _LarkProjectClient, spreadsheet_token: str, sheet_sub_id: str | None) -> str:
    lines: list[str] = [f"# 电子表格", "", f"- spreadsheet_token: `{spreadsheet_token}`", ""]
    meta = client.sheet_metainfo(spreadsheet_token)
    if meta.get("code") != 0:
        lines.append(f"（metainfo 失败: {meta.get('msg', meta)}）")
        return "\n".join(lines)
    sheets = meta.get("data", {}).get("sheets", [])
    picked = None
    if sheet_sub_id:
        for s in sheets:
            if s.get("sheet_id") == sheet_sub_id:
                picked = s
                break
    if not picked and sheets:
        picked = sheets[0]
    if not picked:
        lines.append("（无工作表）")
        return "\n".join(lines)
    sid = picked.get("sheet_id", "")
    title = picked.get("title", sid)
    lines.append(f"## 工作表: {title} (`{sid}`)")
    lines.append("")
    range_a1 = f"{sid}!A1:ZZ3000"
    val_resp = client.sheet_range(spreadsheet_token, range_a1)
    if val_resp.get("code") != 0:
        lines.append(f"（读取单元格失败: {val_resp.get('msg', val_resp)}）")
        return "\n".join(lines)
    vr = val_resp.get("data") or {}
    values = (vr.get("valueRange") or {}).get("values") or vr.get("values") or []
    if not values:
        lines.append("（空表或无可读范围）")
        return "\n".join(lines)
    for row in values:
        if not isinstance(row, list):
            continue
        lines.append("| " + " | ".join(_cell_to_text(c).replace("|", "\\|") for c in row) + " |")
    lines.append("")
    return "\n".join(lines)


def _discover_wiki_tokens(text: str) -> list[str]:
    found = WIKI_LINK_RE.findall(text or "")
    out: list[str] = []
    seen: set[str] = set()
    for t in found:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _wiki_skip_prefixes(cfg: dict[str, Any]) -> set[str]:
    """配置项 wiki_node_skip_tokens：node_token 前缀（与飞书 path 段一致即可），子页面/正文内链均跳过。"""
    raw = cfg.get("wiki_node_skip_tokens")
    if not raw or not isinstance(raw, (list, tuple)):
        return set()
    out: set[str] = set()
    for x in raw:
        if not isinstance(x, str) or not x.strip():
            continue
        s = x.split("#", 1)[0].strip()
        if s:
            out.add(s)
    return out


def _node_token_skipped(node_token: str, skip: set[str]) -> bool:
    if not (node_token or "").strip() or not skip:
        return False
    nt = node_token.strip()
    for s in skip:
        if not s:
            continue
        if nt == s or nt.startswith(s) or s.startswith(nt):
            return True
    return False


def _load_merge_config(runtime: dict[str, Any] | None, project_root: Path) -> dict[str, Any]:
    from l3_node.jachin_config import load_mcp_config

    base = load_mcp_config("atom_bi_project_context", project_root=project_root)
    merged = dict(base)
    if runtime and isinstance(runtime, dict):
        for k, v in runtime.items():
            if v is not None:
                merged[k] = v
    return merged


def sync_bi_project_context(
    config: dict[str, Any] | None = None,
    *,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """
    同步 BI 项目上下文到 docs/bi_daily_report/bi_project/。

    config 可与 YAML 合并；常用键：wiki_urls, output_dir_relative, max_records_per_table（默认 50000，受
    JACHIN_BITABLE_RECORD_HARD_CAP 限制；分页直至无下一页或触顶）,
    max_discovered_links, recurse_children_depth, bitable_emit_hierarchy（默认 true：多维表导出含层级块）,
    wiki_node_skip_tokens（node_token 前缀列表：种子/子页面/docx 内链发现一律跳过，不落盘）,
    app_id, app_secret, lark_use_feishu。同一 Wiki 节点 + 不同 view= 的种子 URL 会 **分别落盘**，不会合并去重。
    """
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    cfg = _load_merge_config(config, root)

    urls = cfg.get("wiki_urls")
    # 模型有时将 wiki_urls 序列化为 JSON 字符串而非数组，兼容性解析
    if isinstance(urls, str):
        try:
            parsed = json.loads(urls)
            if isinstance(parsed, list):
                urls = parsed
        except (json.JSONDecodeError, ValueError):
            urls = None
    if not urls or not isinstance(urls, list):
        urls = _default_wiki_urls()

    out_rel = (cfg.get("output_dir_relative") or DEFAULT_OUTPUT_REL).strip() or DEFAULT_OUTPUT_REL
    outp = Path(out_rel).expanduser()
    out_dir = outp.resolve() if outp.is_absolute() else (root / outp).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    max_records = int(cfg.get("max_records_per_table") or 50000)
    max_records = max(1, min(max_records, BITABLE_RECORD_HARD_CAP))
    max_disc = int(cfg.get("max_discovered_links") or 40)
    max_depth = int(cfg.get("recurse_children_depth") or 2)
    skip_tokens = _wiki_skip_prefixes(cfg)

    _raw_hi = cfg.get("bitable_emit_hierarchy", True)
    if isinstance(_raw_hi, str):
        bitable_emit_hierarchy = _raw_hi.strip().lower() not in ("0", "false", "no", "off")
    else:
        bitable_emit_hierarchy = bool(_raw_hi)

    _raw_json = cfg.get("emit_pull_records_json", True)
    if isinstance(_raw_json, str):
        emit_pull_records_json = _raw_json.strip().lower() not in ("0", "false", "no", "off")
    else:
        emit_pull_records_json = bool(_raw_json)

    app_id = (cfg.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
    app_secret = (cfg.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if cfg.get("lark_use_feishu") in (True, "true", "1", "yes"):
        os.environ["LARK_USE_FEISHU"] = "1"
    elif cfg.get("lark_use_feishu") in (False, "false", "0", "no"):
        os.environ.pop("LARK_USE_FEISHU", None)

    manifest: dict[str, Any] = {
        "output_dir": str(out_dir),
        "files": [],
        "errors": [],
        "nodes": [],
    }

    if not app_id or not app_secret or app_id.startswith("${"):
        err = "未配置 app_id/app_secret（YAML 或环境变量 LARK_APP_ID / LARK_APP_SECRET）"
        manifest["errors"].append(err)
        return {"status": "error", "error": err, **manifest}

    api_base = get_lark_api_base()
    try:
        token = get_tenant_access_token(app_id=app_id, app_secret=app_secret, api_base=api_base)
    except Exception as e:
        err = f"获取 tenant_access_token 失败: {e}"
        manifest["errors"].append(err)
        return {"status": "error", "error": err, **manifest}

    client = _LarkProjectClient(api_base, token)

    visited_nodes: set[str] = set()
    # (node_token, table_id, sheet_id, view_id, source, depth, seed_url)
    queue: list[tuple[str, str | None, str | None, str | None, str, int, str]] = []
    for u in urls:
        su = sanitize_wiki_url(u)
        p = parse_wiki_url(su)
        if p["node_token"]:
            if _node_token_skipped(p["node_token"], skip_tokens):
                continue
            queue.append(
                (p["node_token"], p["table_id"], p["sheet_id"], p["view_id"], "seed", 0, su)
            )

    discovered = 0
    file_idx = 0

    def write_pull_artifacts(
        slug: str,
        body: str,
        meta: dict[str, Any],
        *,
        records_export: dict[str, Any] | None = None,
    ) -> Path:
        """落盘 md（GFM 表 + 元数据块）；多维表可附带同前缀 .records.json 供检索。"""
        nonlocal file_idx
        file_idx += 1
        base = f"{file_idx:02d}_{_safe_name(slug, max_len=160)}"
        md_path = out_dir / f"{base}.md"
        meta_block = (
            "## 同步元数据\n\n```json\n"
            + json.dumps(meta, ensure_ascii=False, indent=2)
            + "\n```\n\n---\n\n"
        )
        md_path.write_text(meta_block + body, encoding="utf-8")
        manifest["files"].append(_manifest_file_relpath(md_path, root, out_dir))
        if emit_pull_records_json and records_export:
            payload = {
                "sync_meta": meta,
                **records_export,
            }
            json_path = out_dir / f"{base}.records.json"
            json_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            manifest["files"].append(_manifest_file_relpath(json_path, root, out_dir))
        return md_path

    while queue:
        node_token, pref_table, pref_sheet, pref_view, source, depth, seed_url = queue.pop(0)
        if not node_token:
            continue
        if _node_token_skipped(node_token, skip_tokens):
            continue
        is_seed = source == "seed"
        if not is_seed:
            if node_token in visited_nodes:
                continue
            if len(visited_nodes) >= DISCOVERED_NODE_BUDGET:
                continue
            visited_nodes.add(node_token)

        g = client.wiki_get_node(node_token)
        if g.get("code") != 0:
            msg = g.get("msg", str(g))
            manifest["errors"].append(f"get_node {node_token}: {msg}")
            write_pull_artifacts(
                f"error_{node_token}",
                f"# 节点拉取失败\n\n`{node_token}`\n\n{msg}\n",
                {"node_token": node_token, "error": msg, "source": source, "seed_url": seed_url},
            )
            continue

        raw_d = g.get("data") or {}
        node = raw_d.get("node") if isinstance(raw_d.get("node"), dict) else raw_d
        if not isinstance(node, dict):
            node = {}
        title = node.get("title", node_token)
        obj_type = (node.get("obj_type") or "").lower()
        obj_token = node.get("obj_token") or ""
        space_id = node.get("space_id") or ""
        meta = {
            "title": title,
            "node_token": node_token,
            "obj_type": obj_type,
            "obj_token": obj_token,
            "space_id": space_id,
            "source": source,
            "depth": depth,
            "seed_url": seed_url,
            "table_id_hint": pref_table,
            "view_id_hint": pref_view,
        }
        manifest["nodes"].append(meta.copy())

        body_parts: list[str] = [
            f"# {title}",
            "",
            "## 节点信息",
            "",
            f"- obj_type: `{obj_type}`",
            f"- obj_token: `{obj_token}`",
            f"- space_id: `{space_id}`",
            f"- has_child: `{node.get('has_child')}`",
            "",
        ]

        records_export: dict[str, Any] | None = None
        if obj_type == "bitable" and obj_token:
            bitable_md, records_export = _render_bitable_markdown(
                client,
                obj_token,
                pref_table,
                title,
                max_records,
                view_id=pref_view,
                emit_hierarchy=bitable_emit_hierarchy,
            )
            body_parts.append(bitable_md)
        elif obj_type == "sheet" and obj_token:
            body_parts.append(_render_sheet_markdown(client, obj_token, pref_sheet))
        elif obj_type == "docx" and obj_token:
            text, err = client.docx_raw_content(obj_token)
            if err:
                body_parts.append(f"## 正文\n\n（docx raw_content 失败: {err}）\n")
            else:
                body_parts.append("## 正文（raw_content）\n\n")
                body_parts.append(text or "（空）")
                body_parts.append("\n")
                if depth < max_depth and discovered < max_disc:
                    for nt in _discover_wiki_tokens(text):
                        if (
                            nt not in visited_nodes
                            and discovered < max_disc
                            and not _node_token_skipped(nt, skip_tokens)
                        ):
                            discovered += 1
                            queue.append(
                                (nt, None, None, None, f"link_from:{node_token}", depth + 1, seed_url)
                            )
        elif obj_type == "doc" and obj_token:
            body_parts.append(
                f"## 旧版文档\n\nobj_type=`doc` 未自动导出正文，请到 Wiki 打开或升级 docx。token=`{obj_token}`\n"
            )
        elif obj_type == "mindnote":
            body_parts.append("## 思维导图\n\nmindnote 类型暂不支持 API 导出正文。\n")
        elif obj_type == "file":
            body_parts.append("## 文件\n\nfile 类型未拉取二进制；请在云文档中查看。\n")
        else:
            body_parts.append(f"## 未专门处理类型\n\n`{obj_type}`\n")

        # 子节点（目录）
        if space_id and node.get("has_child") and depth < max_depth:
            children = client.wiki_list_children(space_id, node_token)
            body_parts.append("\n## 子页面列表\n\n")
            for ch in children:
                cn = ch.get("title", "")
                ctok = ch.get("node_token", "")
                cot = ch.get("obj_type", "")
                body_parts.append(f"- **{cn}** — `{ctok}` ({cot})\n")
                if (
                    ctok
                    and ctok not in visited_nodes
                    and not _node_token_skipped(ctok, skip_tokens)
                ):
                    queue.append(
                        (ctok, None, None, None, f"child_of:{node_token}", depth + 1, seed_url)
                    )

        slug_core = f"{title}_{node_token[:8]}"
        sem = _bitable_filename_semantic_slug(pref_view)
        if sem:
            slug_core = f"{slug_core}_{sem}"
        if pref_view:
            slug_core = f"{slug_core}_{pref_view}"
        elif pref_table:
            slug_core = f"{slug_core}_tbl{pref_table[-6:]}" if len(pref_table) > 6 else f"{slug_core}_{pref_table}"
        write_pull_artifacts(
            slug_core,
            "\n".join(body_parts),
            meta,
            records_export=records_export,
        )

    man_path = out_dir / "00_SYNC_MANIFEST.json"
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"].append(_manifest_file_relpath(man_path, root, out_dir))

    wrote_md = file_idx
    ok = wrote_md > 0 or len(manifest["nodes"]) > 0
    return {
        "status": "success" if ok else "error",
        "msg": f"已写入 {len(manifest['files'])} 个文件（含 manifest）到 {out_dir}",
        **manifest,
    }


def atom_bi_project_context(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """MCP 入口：与 sync_bi_project_context 相同。"""
    return sync_bi_project_context(config=config)
