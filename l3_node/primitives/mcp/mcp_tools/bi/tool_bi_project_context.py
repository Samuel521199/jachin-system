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
WIKI_LINK_RE = re.compile(
    r"https://[a-zA-Z0-9.-]*(?:larksuite\.com|feishu\.cn)/wiki/([A-Za-z0-9]+)",
    re.IGNORECASE,
)


def _default_wiki_urls() -> list[str]:
    """内置种子链接（已去掉一次性登录 token，仅保留业务 query）。"""
    return [
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=ldxeuHgiN5L2gXBH",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblNdv7DIlycuqxp&view=vew8TxMcSh",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/B19Iww8tBiXZqfky1hhlIZ6kg0P?table=tblfK9gk6vTQpJtB&view=vewpI8lyYw",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/DiSnwVB1OiDvPWkk0W9lzx6AgLd?table=tblDw87UlhddFIoY&view=vew5taB9H1",
        "https://ssgkm409t6q5.sg.larksuite.com/wiki/TO3twkDP3iPlBJklCHwln4FSgSc?table=tblsd9jtoIMhIskf&view=vewPvbGgis",
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
            chunk = data.get("data", {}).get("items", [])
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
            items = data.get("data", {}).get("items", [])
            out.extend(items)
            page_token = data.get("data", {}).get("page_token")
            if not page_token or not items:
                break
        return out

    def bitable_list_fields(self, app_token: str, table_id: str) -> list[dict]:
        data = self._get(f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields")
        if data.get("code") != 0:
            return []
        return data.get("data", {}).get("items", [])

    def bitable_list_records(
        self, app_token: str, table_id: str, max_records: int
    ) -> list[dict]:
        import requests

        records: list[dict] = []
        page_token = None
        while len(records) < max_records:
            params: dict[str, Any] = {"page_size": min(500, max_records - len(records))}
            if page_token:
                params["page_token"] = page_token
            url = f"{self.api_base}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            r = requests.get(url, headers=self._headers, params=params, timeout=120)
            data = r.json()
            if data.get("code") != 0:
                logger.warning("[bi_project_context] bitable records: %s", data.get("msg"))
                break
            items = data.get("data", {}).get("items", [])
            records.extend(items)
            page_token = data.get("data", {}).get("page_token")
            if not page_token or not items:
                break
        return records[:max_records]


def _render_bitable_markdown(
    client: _LarkProjectClient,
    app_token: str,
    table_id: str | None,
    table_name_hint: str,
    max_records: int,
) -> str:
    lines: list[str] = [f"# 多维表格 {table_name_hint}", "", f"- app_token: `{app_token}`", ""]
    tables = client.bitable_list_tables(app_token)
    if not tables:
        lines.append("（无法列出子表或无权访问）")
        return "\n".join(lines)
    targets: list[dict] = []
    if table_id:
        for t in tables:
            if t.get("table_id") == table_id:
                targets = [t]
                break
        if not targets:
            lines.append(f"（URL 中 table=`{table_id}` 未在应用中找到，以下为全部子表摘要）")
            targets = tables
    else:
        targets = tables

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
        recs = client.bitable_list_records(app_token, tid, max_records)

        def _norm_record_fields(flds: dict) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for k, v in (flds or {}).items():
                disp = fid_to_name.get(str(k), str(k))
                out[disp] = v
            return out

        norm_recs = [_norm_record_fields(r.get("fields") or {}) for r in recs]
        all_cols: set[str] = set()
        for m in norm_recs:
            all_cols.update(m.keys())
        col_order = [c for c in field_names if c in all_cols] + sorted(all_cols - set(field_names))
        if not col_order:
            col_order = ["(无列)"]
        lines.append(f"- 记录数（本次拉取上限 {max_records}）: {len(recs)}")
        lines.append("")
        lines.append("| " + " | ".join(col_order) + " |")
        lines.append("| " + " | ".join(["---"] * len(col_order)) + " |")
        for m in norm_recs:
            row = [_cell_to_text(m.get(c)) for c in col_order]
            lines.append("| " + " | ".join(x.replace("|", "\\|").replace("\n", " ") for x in row) + " |")
        lines.append("")
    return "\n".join(lines)


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

    config 可与 YAML 合并；常用键：wiki_urls, output_dir_relative, max_records_per_table,
    max_discovered_links, recurse_children_depth, app_id, app_secret, lark_use_feishu
    """
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
    from l3_node.paths import get_app_root

    root = project_root or get_app_root()
    cfg = _load_merge_config(config, root)

    urls = cfg.get("wiki_urls")
    if not urls or not isinstance(urls, list):
        urls = _default_wiki_urls()

    out_rel = (cfg.get("output_dir_relative") or DEFAULT_OUTPUT_REL).strip() or DEFAULT_OUTPUT_REL
    out_dir = (root / out_rel).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    max_records = int(cfg.get("max_records_per_table") or 2000)
    max_disc = int(cfg.get("max_discovered_links") or 40)
    max_depth = int(cfg.get("recurse_children_depth") or 2)

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
    queue: list[tuple[str, str | None, str | None, str | None, int, str]] = []
    # (node_token, table_id, sheet_id, source_label, depth, seed_url_sanitized)
    for u in urls:
        su = sanitize_wiki_url(u)
        p = parse_wiki_url(su)
        if p["node_token"]:
            queue.append(
                (p["node_token"], p["table_id"], p["sheet_id"], "seed", 0, su)
            )

    discovered = 0
    file_idx = 0

    def write_md(slug: str, body: str, meta: dict[str, Any]) -> Path:
        nonlocal file_idx
        file_idx += 1
        name = f"{file_idx:02d}_{_safe_name(slug)}.md"
        path = out_dir / name
        meta_block = (
            "## 同步元数据\n\n```json\n"
            + json.dumps(meta, ensure_ascii=False, indent=2)
            + "\n```\n\n---\n\n"
        )
        path.write_text(meta_block + body, encoding="utf-8")
        manifest["files"].append(str(path.relative_to(root)))
        return path

    while queue and len(visited_nodes) < 200:
        node_token, pref_table, pref_sheet, source, depth, seed_url = queue.pop(0)
        if not node_token or node_token in visited_nodes:
            continue
        visited_nodes.add(node_token)

        g = client.wiki_get_node(node_token)
        if g.get("code") != 0:
            msg = g.get("msg", str(g))
            manifest["errors"].append(f"get_node {node_token}: {msg}")
            write_md(
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

        if obj_type == "bitable" and obj_token:
            body_parts.append(
                _render_bitable_markdown(
                    client, obj_token, pref_table, title, max_records
                )
            )
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
                        if nt not in visited_nodes and discovered < max_disc:
                            discovered += 1
                            queue.append((nt, None, None, f"link_from:{node_token}", depth + 1, seed_url))
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
                if ctok and ctok not in visited_nodes:
                    queue.append((ctok, None, None, f"child_of:{node_token}", depth + 1, seed_url))

        write_md(f"{title}_{node_token[:8]}", "\n".join(body_parts), meta)

    man_path = out_dir / "00_SYNC_MANIFEST.json"
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["files"].append(str(man_path.relative_to(root)))

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
