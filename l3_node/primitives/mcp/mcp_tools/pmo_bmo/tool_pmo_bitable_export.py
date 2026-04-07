"""
PMO 多维表定向导出 — 由 atom_pmo_lark_doc operation=export_pmo_tables 调用

- 默认：按固定 6 个 Wiki 链接（不同 table_id / view_id）拉取 Bitable 记录
- 特例：`req_march_coarse` 可为 Wiki 内 **云文档表格块**（table=ldxv…），走 docx/v1 官方接口拉块并解析为与导出兼容的 records
- JSON → ~/.jachin/client_volumes/PMO/raw/{date}_{slug}.json
- Markdown → 仓库 docs/pmo_bmo_plugin/raw/{slug}.md（固定文件名，每轮覆盖；JSON 仍为 ~/.jachin/.../raw/{date}_{slug}.json）
- DuckDB → ~/.jachin/client_volumes/PMO/duckdb/pmo.duckdb（表 pmo_bitable_records / pmo_bitable_export_meta）

依赖：requests；DuckDB 需安装（与 BI 共用 requirements-bi.txt 中的 duckdb）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections import deque
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_root = Path(__file__).resolve().parents[4]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

logger = logging.getLogger(__name__)

from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import (  # noqa: E402
    _cell_to_text,
    parse_wiki_url,
    sanitize_wiki_url,
)

# 六张表：slug 用于文件名与 DuckDB；url 含 wiki 节点 + table + view（view 可省略）
# export_mode=docx_table：拉取 Wiki 云文档内表格块（docx/v1），非 Bitable；需 docx_table_block_id，可选 docx_document_id / docx_wiki_title
# table_name_resolve：按子表名称在「该 Wiki 节点对应多维表」内解析真实 tbl_xxx（用于非 tbl 的分享链接）
# table_id_override：Bitable 模式下 Wiki 链接里 table= 非 tbl 时，写真实 tbl_xxx；view_id_override 可覆盖 URL 中的 view
PMO_SCHEDULED_BITABLES: list[dict[str, Any]] = [
    {
        "slug": "req_march_fine",
        "label": "3月需求细分",
        "url": "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblozlbpzHlL8m8m&view=vew8TxMcSh",
    },
    {
        "slug": "req_march_coarse",
        "label": "3月需求大表",
        "url": "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=ldxvjdZfkv69GwsB",
        "export_mode": "docx_table",
        "docx_table_block_id": "ldxvjdZfkv69GwsB",
        # 当 Wiki 根节点为多维表时，在子节点/同级/空间根级查找标题匹配的 docx；也可配置 docx_document_id
        "docx_wiki_title": "需求表3月",
        # 云文档标题常为「未命名文档」，与侧栏名称不一致时作备选匹配
        "docx_wiki_title_alt": ["未命名文档"],
    },
    {
        "slug": "dev_tasks_view_core",
        "label": "开发任务（版本核心需求等视图）",
        "url": "https://ssgkm409t6q5.sg.larksuite.com/wiki/GdQ7wTgSRiZ0olkXrNGlFcz0gad?table=tblhJN0G2EhRNwjZ&view=vewpI8lyYw",
    },
    {
        "slug": "dev_tasks_by_assignee",
        "label": "开发每人任务（执行人看板视图）",
        "url": "https://ssgkm409t6q5.sg.larksuite.com/wiki/GdQ7wTgSRiZ0olkXrNGlFcz0gad?table=tblhJN0G2EhRNwjZ&view=vewCz1FFJi",
    },
    {
        "slug": "art_tasks_completed",
        "label": "美术任务（完成/列表视图）",
        "url": "https://ssgkm409t6q5.sg.larksuite.com/wiki/DiSnwVB1OiDvPWkk0W9lzx6AgLd?table=tblDw87UlhddFIoY&view=vew5taB9H1",
    },
    {
        "slug": "art_tasks_by_designer",
        "label": "美术每人任务（设计人看板视图）",
        "url": "https://ssgkm409t6q5.sg.larksuite.com/wiki/DiSnwVB1OiDvPWkk0W9lzx6AgLd?table=tblDw87UlhddFIoY&view=vewdTHmbTM",
    },
]


def _pmo_repo_md_basename(slug: str) -> str:
    """仓库 ``docs/pmo_bmo_plugin/raw`` 下 Markdown 使用 ``{slug}.md``，每轮导出覆盖同一文件。"""
    return f"{slug}.md"


_LEGACY_DATED_REPO_MD = re.compile(r"^\d{4}-\d{2}-\d{2}_(.+)\.md$")


def _remove_legacy_dated_repo_md(md_dir: Path, slugs: set[str]) -> None:
    """删除旧版 ``YYYY-MM-DD_<slug>.md``，避免与 ``<slug>.md`` 并存堆积。"""
    if not md_dir.is_dir():
        return
    for p in md_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".md":
            continue
        if p.name.lower() == "readme.md":
            continue
        m = _LEGACY_DATED_REPO_MD.match(p.name)
        if not m:
            continue
        if m.group(1) in slugs:
            try:
                p.unlink()
                logger.info("[pmo_bitable_export] 已删除旧版按日快照 MD: %s", p.name)
            except OSError as e:
                logger.warning("[pmo_bitable_export] 删除旧版 MD 失败 %s: %s", p, e)


def _lark_get(api_base: str, token: str, path: str, params: dict | None = None, timeout: int = 120) -> dict[str, Any]:
    import requests

    url = f"{api_base.rstrip('/')}{path}" if path.startswith("/") else f"{api_base}/{path}"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params or {}, timeout=timeout)
    try:
        return r.json()
    except Exception:
        return {"code": -1, "msg": r.text[:500]}


# Docx 表格块类型（与飞书开放平台文档一致）
_DOCX_BLOCK_TYPE_TABLE = 31


def _wiki_list_child_nodes(
    api_base: str, token: str, space_id: str, parent_node_token: str | None
) -> list[dict[str, Any]]:
    """
    列出知识空间节点。parent_node_token 为 None 时不传该参数，即拉取空间**根级**节点（与飞书文档一致）。
    """
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"page_size": 50}
        if parent_node_token:
            params["parent_node_token"] = parent_node_token
        if page_token:
            params["page_token"] = page_token
        data = _lark_get(api_base, token, f"/wiki/v2/spaces/{space_id}/nodes", params)
        if data.get("code") != 0:
            logger.warning("[pmo_bitable_export] wiki list nodes: %s", data.get("msg"))
            break
        chunk = data.get("data", {}).get("items", []) or []
        items.extend(chunk)
        page_token = data.get("data", {}).get("page_token")
        if not data.get("data", {}).get("has_more") or not chunk:
            break
    return items


def _norm_wiki_title(s: str) -> str:
    return "".join((s or "").split())


def _docx_wiki_title_needles(spec: dict[str, Any]) -> list[str]:
    """主标题 + docx_wiki_title_alt，去重保留顺序。"""
    out: list[str] = []
    primary = (spec.get("docx_wiki_title") or "需求表3月").strip()
    if primary:
        out.append(primary)
    for a in spec.get("docx_wiki_title_alt") or ():
        if isinstance(a, str):
            s = a.strip()
            if s and s not in out:
                out.append(s)
    return out if out else ["需求表3月"]


def _resolve_docx_document_id(
    api_base: str,
    token: str,
    node_token: str,
    node: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[str | None, str | None]:
    """返回 (document_id, error_msg)。"""
    explicit = (spec.get("docx_document_id") or "").strip()
    if explicit:
        return explicit, None
    obj_type = (node.get("obj_type") or "").lower()
    obj_token = (node.get("obj_token") or "").strip()
    if obj_type == "docx" and obj_token:
        return obj_token, None
    needles = _docx_wiki_title_needles(spec)
    if obj_type == "bitable":
        space_id = (node.get("space_id") or "").strip()
        if not space_id:
            return None, "Wiki 节点缺少 space_id，无法列举子节点"
        # 1) 当前 Wiki 节点（多为多维表根）的直接子节点
        children = _wiki_list_child_nodes(api_base, token, space_id, node_token)
        docx_children = [c for c in children if (c.get("obj_type") or "").lower() == "docx"]
        tried = ["当前节点子级"]
        # 2) 侧栏「需求表3月」常与多维表**同级**（同属父节点），内嵌云文档不会出现在多维表子节点里
        if not docx_children:
            ppt = (node.get("parent_node_token") or "").strip()
            if ppt:
                sib = _wiki_list_child_nodes(api_base, token, space_id, ppt)
                docx_children = [c for c in sib if (c.get("obj_type") or "").lower() == "docx"]
                tried.append("父节点下同级")
                if docx_children:
                    logger.info(
                        "[pmo_bitable_export] req_march_coarse: 在父节点下找到 %s 个 docx（父 parent_node_token=%s...）",
                        len(docx_children),
                        ppt[:12],
                    )
        # 3) 空间根级节点（不传 parent_node_token）
        if not docx_children:
            root_nodes = _wiki_list_child_nodes(api_base, token, space_id, None)
            docx_children = [c for c in root_nodes if (c.get("obj_type") or "").lower() == "docx"]
            tried.append("知识空间根级")
            if docx_children:
                logger.info(
                    "[pmo_bitable_export] req_march_coarse: 在知识空间根级找到 %s 个 docx",
                    len(docx_children),
                )
        if not docx_children:
            return None, (
                "Wiki 下列未找到可作为云文档的 docx 节点（已尝试: "
                + "、".join(tried)
                + "）。侧栏「需求表3月」若仅为页面内嵌表格，请打开该云文档复制链接，"
                "将 URL 中的 docx token 配置到 pipeline.pmo_export.docx_document_ids.req_march_coarse，"
                "或设置环境变量 PMO_REQ_MARCH_COARSE_DOCX_ID。"
            )
        for ch in docx_children:
            title = (ch.get("title") or "").strip()
            ntitle = _norm_wiki_title(title)
            for needle in needles:
                nn = _norm_wiki_title(needle)
                if not nn:
                    continue
                if nn == ntitle or nn in ntitle or ntitle in nn:
                    tid = (ch.get("obj_token") or "").strip()
                    if tid:
                        return tid, None
                    break
        for ch in docx_children:
            title = (ch.get("title") or "").strip()
            if "需求" in title and "3月" in title:
                tid = (ch.get("obj_token") or "").strip()
                if tid:
                    logger.info("[pmo_bitable_export] docx 选用子节点（标题含需求+3月）: %s", title[:80])
                    return tid, None
        if len(docx_children) == 1:
            tid = (docx_children[0].get("obj_token") or "").strip()
            if tid:
                logger.warning(
                    "[pmo_bitable_export] Wiki 下仅 1 个 docx 子文档，自动选用: %s",
                    (docx_children[0].get("title") or "")[:80],
                )
                return tid, None
        titles = [(c.get("title"), c.get("obj_token")) for c in docx_children]
        return None, (
            f"未解析到 docx（标题需匹配其一 {needles!r}）。当前候选 docx: {titles}。"
            "请配置 pipeline.pmo_export.docx_document_ids.req_march_coarse、环境变量 PMO_REQ_MARCH_COARSE_DOCX_ID，"
            '或 export 参数 docx_document_ids={"req_march_coarse": "<云文档 document_id>"}。'
        )
    return None, (
        f"Wiki 节点 obj_type={obj_type!r} 不是 docx，且未配置 docx_document_id。"
        "请将该表所在云文档的 obj_token 填入 docx_document_id。"
    )


def _docx_pick_block(payload: dict[str, Any]) -> dict[str, Any] | None:
    d = payload.get("data") or {}
    return d.get("block") if isinstance(d.get("block"), dict) else None


def _docx_revision_params() -> dict[str, Any]:
    """与开放平台 docx/v1 一致：使用整数 -1 表示最新版本。"""
    return {"document_revision_id": -1}


def _docx_get_block(api_base: str, token: str, document_id: str, block_id: str) -> dict[str, Any]:
    return _lark_get(
        api_base,
        token,
        f"/docx/v1/documents/{document_id}/blocks/{block_id}",
        _docx_revision_params(),
    )


def _docx_get_document(api_base: str, token: str, document_id: str) -> dict[str, Any]:
    return _lark_get(
        api_base,
        token,
        f"/docx/v1/documents/{document_id}",
        _docx_revision_params(),
    )


def _docx_list_document_blocks_all(api_base: str, token: str, document_id: str) -> list[dict[str, Any]]:
    """
    拉取文档内全部块（分页）。用于在 Wiki table= 与独立 docx URL 内块 ID 不一致时自动定位表格。
    GET /docx/v1/documents/:document_id/blocks
    """
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    for page_idx in range(80):
        params: dict[str, Any] = {"document_revision_id": -1, "page_size": 500}
        if page_token:
            params["page_token"] = page_token
        data = _lark_get(api_base, token, f"/docx/v1/documents/{document_id}/blocks", params)
        if data.get("code") != 0:
            logger.warning("[pmo_bitable_export] docx list document blocks: %s", data.get("msg"))
            break
        d = data.get("data") or {}
        chunk = d.get("items") or d.get("blocks") or []
        if isinstance(chunk, list):
            out.extend([x for x in chunk if isinstance(x, dict)])
        logger.info(
            "[pmo_bitable_export] docx list blocks 分页进度: 第 %s 页，当前累计 %s 块",
            page_idx + 1,
            len(out),
        )
        page_token = (d.get("page_token") or "").strip() or None
        if not d.get("has_more") or not chunk:
            break
    return out


def _docx_pick_largest_table_block_id(blocks: list[dict[str, Any]]) -> str | None:
    """在块列表中选 block_type=表格 且 cells 最多的一块（多表时取主表）。"""
    best: tuple[int, str] = (-1, "")
    for b in blocks:
        if int(b.get("block_type") or 0) != _DOCX_BLOCK_TYPE_TABLE:
            continue
        bid = (b.get("block_id") or "").strip()
        if not bid:
            continue
        tbl = b.get("table") if isinstance(b.get("table"), dict) else {}
        cells = tbl.get("cells") if isinstance(tbl.get("cells"), list) else []
        n = len(cells)
        if n > best[0]:
            best = (n, bid)
    if best[1]:
        return best[1]
    # list 接口有时不返回 table.cells，仍可按块类型取第一个表格
    for b in blocks:
        if int(b.get("block_type") or 0) != _DOCX_BLOCK_TYPE_TABLE:
            continue
        bid = (b.get("block_id") or "").strip()
        if bid:
            return bid
    return None


def _docx_extract_root_block_id_from_document(doc_resp: dict[str, Any]) -> str | None:
    """GET /documents/:id 响应中解析页面根块，供 BFS 兜底。"""
    data = doc_resp.get("data") or {}
    blk = data.get("block")
    if isinstance(blk, dict) and blk.get("block_id"):
        return str(blk["block_id"]).strip()
    for key in ("blocks", "items"):
        arr = data.get(key)
        if isinstance(arr, list) and arr:
            b0 = arr[0]
            if isinstance(b0, dict) and b0.get("block_id"):
                return str(b0["block_id"]).strip()
    doc = data.get("document")
    if isinstance(doc, dict):
        for k in ("root_block_id", "block_id"):
            v = doc.get(k)
            if v:
                return str(v).strip()
    return None


def _docx_bfs_first_table_block_id(
    api_base: str, token: str, document_id: str, root_block_id: str
) -> str | None:
    q: deque[str] = deque([root_block_id])
    seen: set[str] = set()
    while q:
        bid = q.popleft()
        if not bid or bid in seen:
            continue
        seen.add(bid)
        gr = _docx_get_block(api_base, token, document_id, bid)
        if gr.get("code") != 0:
            continue
        blk = _docx_pick_block(gr)
        if not isinstance(blk, dict):
            continue
        if int(blk.get("block_type") or 0) == _DOCX_BLOCK_TYPE_TABLE:
            return str(blk.get("block_id") or bid).strip()
        for cid in blk.get("children") or []:
            if isinstance(cid, str):
                q.append(cid)
    return None


def _docx_discover_table_block_id(api_base: str, token: str, document_id: str) -> str | None:
    """
    Wiki 链接里 table=ldxv… 与浏览器打开 /docx/{document_id} 时内部表格块 ID 可能不一致，
    导致 get_block 报 invalid param。此处列举文档内全部块或按文档树 BFS，找到表格块。
    """
    blocks = _docx_list_document_blocks_all(api_base, token, document_id)
    tid = _docx_pick_largest_table_block_id(blocks)
    if tid:
        logger.info(
            "[pmo_bitable_export] docx 自动选用表格块 id=%s…（来自 list blocks，共 %s 块）",
            tid[:18],
            len(blocks),
        )
        return tid
    gd = _docx_get_document(api_base, token, document_id)
    if gd.get("code") != 0:
        logger.warning("[pmo_bitable_export] docx get_document 失败: %s", gd.get("msg"))
        return None
    root = _docx_extract_root_block_id_from_document(gd)
    if not root:
        logger.warning("[pmo_bitable_export] docx get_document 未解析到根块")
        return None
    tid2 = _docx_bfs_first_table_block_id(api_base, token, document_id, root)
    if tid2:
        logger.info("[pmo_bitable_export] docx BFS 选用表格块 id=%s…", tid2[:18])
    return tid2


def _docx_plain_text_from_block_body(block: dict[str, Any]) -> str:
    t = block.get("text")
    if not isinstance(t, dict):
        return ""
    parts: list[str] = []
    for el in t.get("elements") or []:
        if not isinstance(el, dict):
            continue
        tr = el.get("text_run")
        if isinstance(tr, dict) and tr.get("content"):
            parts.append(str(tr.get("content")))
            continue
        if "text_run" in el and isinstance(el["text_run"], dict):
            parts.append(str(el["text_run"].get("content") or ""))
    return "".join(parts).strip()


def _docx_cell_text(cell_id: str, block_map: dict[str, Any]) -> str:
    b = block_map.get(cell_id)
    if not isinstance(b, dict):
        return ""
    parts: list[str] = [_docx_plain_text_from_block_body(b)]
    for cid in b.get("children") or []:
        if isinstance(cid, str):
            parts.append(_docx_cell_text(cid, block_map))
    return "".join(parts).strip()


def _docx_fetch_block_tree(
    api_base: str, token: str, document_id: str, block_id: str, block_map: dict[str, Any], *, depth: int = 0
) -> None:
    """递归 GET block，填充 block_map（单元格内段落等为子块）。"""
    if depth > 80 or block_id in block_map:
        return
    gr = _docx_get_block(api_base, token, document_id, block_id)
    if gr.get("code") != 0:
        logger.warning("[pmo_bitable_export] docx get_block %s: %s", block_id[:16], gr.get("msg"))
        return
    blk = _docx_pick_block(gr)
    if not isinstance(blk, dict) or not blk.get("block_id"):
        return
    block_map[str(blk["block_id"])] = blk
    for cid in blk.get("children") or []:
        if isinstance(cid, str):
            _docx_fetch_block_tree(api_base, token, document_id, cid, block_map, depth=depth + 1)


def _docx_access_denied_hint(msg: str) -> str:
    """docx 403 / scope 类错误时附加说明（本仓库 token 无落盘缓存）。"""
    m = (msg or "").lower()
    if "scope" not in m and "access denied" not in m:
        return msg
    return (
        f"{msg} "
        "【说明】本仓库每次导出都会重新请求 tenant_access_token，一般不存盘缓存；若控制台已勾选 "
        "docx:document / docx:document:readonly，仍报错时多为：应用未发布、租户未重新授权该应用，"
        "或云文档未对应用可见。请检查开放平台权限与授权状态。"
    )


def _docx_table_to_matrix(
    api_base: str,
    token: str,
    document_id: str,
    table_block_id: str,
) -> tuple[list[list[str]], str | None]:
    """返回 (行优先的二维文本矩阵, 错误信息)。"""
    gr = _docx_get_block(api_base, token, document_id, table_block_id)
    if gr.get("code") != 0:
        return [], _docx_access_denied_hint(str(gr.get("msg") or gr))
    blk = _docx_pick_block(gr)
    if not isinstance(blk, dict):
        return [], "docx 未返回 block"
    if int(blk.get("block_type") or 0) != _DOCX_BLOCK_TYPE_TABLE:
        return [], f"block 不是表格（block_type={blk.get('block_type')}）"
    table = blk.get("table") or {}
    prop = table.get("property") or {}
    col_size = int(prop.get("column_size") or 0)
    cells = table.get("cells") or []
    if not isinstance(cells, list) or not cells:
        return [], "表格无 cells"
    if col_size <= 0:
        col_size = len(cells)

    str_cells = [c for c in cells if isinstance(c, str)]
    total_cells = len(str_cells)
    logger.info(
        "[pmo_bitable_export] docx 表格开始逐格拉取子块：共 %s 个单元格（每格可能多次请求 API，大表需数分钟属正常）",
        total_cells,
    )

    block_map: dict[str, Any] = {table_block_id: blk}
    done = 0
    for cid in cells:
        if isinstance(cid, str):
            _docx_fetch_block_tree(api_base, token, document_id, cid, block_map)
            done += 1
            if done == 1 or done % 25 == 0 or done == total_cells:
                pct = (100.0 * done / total_cells) if total_cells else 100.0
                bar_w = 20
                filled = int(bar_w * done / total_cells) if total_cells else bar_w
                bar = "#" * filled + "-" * (bar_w - filled)
                logger.info(
                    "[pmo_bitable_export] docx 单元格进度 [%s] %s/%s (~%.1f%%)",
                    bar,
                    done,
                    total_cells,
                    pct,
                )

    matrix: list[list[str]] = []
    row: list[str] = []
    for i, cell_id in enumerate(cells):
        if not isinstance(cell_id, str):
            continue
        txt = _docx_cell_text(cell_id, block_map)
        row.append(txt)
        if len(row) >= col_size:
            matrix.append(row)
            row = []
    if row:
        matrix.append(row)
    return matrix, None


def _docx_matrix_to_export(
    matrix: list[list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(bitable 形 records, fields_meta, rows_disp 扁平行)。"""
    if not matrix:
        return [], [], []
    max_cols = max(len(r) for r in matrix)
    col_names = [f"列{i + 1}" for i in range(max_cols)]
    fields_meta: list[dict[str, Any]] = []
    for i, name in enumerate(col_names):
        fields_meta.append(
            {
                "field_id": f"fld_docx_{i}",
                "field_name": name,
                "is_primary": i == 0,
                "is_extend": False,
                "is_synced": False,
                "property": None,
                "type": 1,
                "ui_type": "Text",
            }
        )
    recs: list[dict[str, Any]] = []
    rows_disp: list[dict[str, Any]] = []
    for ri, row in enumerate(matrix):
        fields: dict[str, Any] = {}
        disp: dict[str, Any] = {}
        for i, name in enumerate(col_names):
            val = row[i] if i < len(row) else ""
            fields[name] = val
            disp[name] = val
        recs.append({"record_id": f"docx_r{ri}", "fields": fields})
        rows_disp.append(disp)
    return recs, fields_meta, rows_disp


def _run_export_docx_table(
    api_base: str,
    token: str,
    spec: dict[str, Any],
    su: str,
    node_token: str,
    node: dict[str, Any],
    snap: str,
    slug: str,
    label: str,
    raw_dir: Path,
    md_dir: Path,
    project_root: Path,
    db_path: Path,
    max_recs: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """成功返回 (result 字典), 失败返回 (None, error_msg)。"""
    block_id = (spec.get("docx_table_block_id") or "").strip()
    if not block_id:
        return None, "docx_table 模式需要 docx_table_block_id"
    doc_id, err = _resolve_docx_document_id(api_base, token, node_token, node, spec)
    if err or not doc_id:
        return None, err or "无法解析 docx document_id"

    auto_ok = spec.get("docx_auto_discover_table", True) is not False
    matrix, merr = _docx_table_to_matrix(api_base, token, doc_id, block_id)
    if merr and auto_ok and doc_id:
        discovered = _docx_discover_table_block_id(api_base, token, doc_id)
        if discovered and discovered != block_id:
            matrix, merr = _docx_table_to_matrix(api_base, token, doc_id, discovered)
            if not merr:
                block_id = discovered
    if merr:
        return None, f"docx 表格解析: {merr}"
    if max_recs and len(matrix) > max_recs:
        matrix = matrix[:max_recs]

    recs, fields_meta, rows_disp = _docx_matrix_to_export(matrix)
    col_order_final = [f.get("field_name") for f in fields_meta if f.get("field_name")]
    ferr: str | None = None
    rerr: str | None = None

    payload = {
        "snapshot_date": snap,
        "slug": slug,
        "label": label,
        "export_mode": "docx_table",
        "wiki_url": su,
        "node_token": node_token,
        "docx_document_id": doc_id,
        "docx_table_block_id": block_id,
        "table_id": block_id,
        "view_id": None,
        "app_token": None,
        "fields": fields_meta,
        "records": recs,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "field_error": ferr,
        "record_error": rerr,
        "docx_matrix_error": merr,
    }

    json_name = f"{snap}_{slug}.json"
    json_path = raw_dir / json_name
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_body = _render_md_table(label, block_id, None, col_order_final, rows_disp)
    md_path = md_dir / _pmo_repo_md_basename(slug)
    meta_header = (
        "## 同步元数据\n\n```json\n"
        + json.dumps(
            {"snapshot_date": snap, "slug": slug, "json_file": str(json_path), "source": "docx_table"},
            ensure_ascii=False,
            indent=2,
        )
        + "\n```\n\n---\n\n"
    )
    md_path.write_text(meta_header + md_body, encoding="utf-8")

    db_r = _duckdb_ingest(
        db_path,
        snap,
        slug,
        label,
        str(json_path),
        str(md_path),
        recs,
        rows_disp,
    )

    try:
        md_rel = str(md_path.relative_to(project_root))
    except ValueError:
        md_rel = str(md_path)
    return {
        "slug": slug,
        "label": label,
        "record_count": len(recs),
        "table_id": block_id,
        "field_error": ferr,
        "record_error": rerr,
        "json": str(json_path),
        "md": md_rel,
        **db_r,
    }, None


def _bitable_list_tables(api_base: str, token: str, app_token: str) -> list[dict[str, Any]]:
    """列出多维表应用下全部子表（用于 WrongTableId 诊断与按名称解析）。"""
    out: list[dict[str, Any]] = []
    page_token = None
    while True:
        params: dict[str, Any] = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        data = _lark_get(api_base, token, f"/bitable/v1/apps/{app_token}/tables", params)
        if data.get("code") != 0:
            logger.warning("[pmo_bitable_export] list_tables app_token=%s...: %s", app_token[:12], data.get("msg"))
            break
        chunk = data.get("data", {}).get("items", []) or []
        out.extend(chunk)
        page_token = data.get("data", {}).get("page_token")
        if not data.get("data", {}).get("has_more") or not chunk:
            break
    return out


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
        logger.warning("[pmo_bitable_export] fields table_id=%r: %s", table_id, msg)
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
            logger.warning("[pmo_bitable_export] records table_id=%r: %s", table_id, msg)
            return records, msg
        items = data.get("data", {}).get("items", [])
        records.extend(items)
        page_token = data.get("data", {}).get("page_token")
        if not page_token or not items:
            break
    return records[:max_records], None


def _norm_fields_map(fields_meta: list[dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    fid_to_name: dict[str, str] = {}
    order: list[str] = []
    for f in fields_meta:
        fn = (f.get("field_name") or f.get("name") or "").strip()
        fid = f.get("field_id")
        if fid and fn:
            fid_to_name[str(fid)] = fn
            if fn not in order:
                order.append(fn)
    return fid_to_name, order


def _record_to_display_row(fields: dict[str, Any], fid_to_name: dict[str, str], col_order: list[str]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for fid, val in (fields or {}).items():
        name = fid_to_name.get(str(fid), str(fid))
        row[name] = val
    # 稳定列顺序
    out: dict[str, Any] = {}
    for c in col_order:
        if c in row:
            out[c] = row[c]
    for k in sorted(row.keys()):
        if k not in out:
            out[k] = row[k]
    return out


def _render_md_table(label: str, table_id: str, view_id: str | None, col_order: list[str], rows_disp: list[dict[str, Any]]) -> str:
    lines = [
        f"# {label}",
        "",
        f"- table_id: `{table_id}`",
        f"- view_id: `{view_id or ''}`",
        f"- 记录数: {len(rows_disp)}",
        "",
    ]
    if not col_order:
        col_order = list(rows_disp[0].keys()) if rows_disp else []
    if not rows_disp:
        lines.append("（无记录）")
        return "\n".join(lines)
    lines.append("| " + " | ".join(col_order) + " |")
    lines.append("| " + " | ".join(["---"] * len(col_order)) + " |")
    for m in rows_disp:
        row = [_cell_to_text(m.get(c)) for c in col_order]
        lines.append("| " + " | ".join(x.replace("|", "\\|").replace("\n", " ")[:2000] for x in row) + " |")
    lines.append("")
    return "\n".join(lines)


def _duckdb_ingest(
    db_path: Path,
    snapshot_date: str,
    slug: str,
    label: str,
    json_path: str,
    md_path: str,
    records: list[dict[str, Any]],
    rows_disp: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError:
        return {"duckdb": "skipped", "error": "duckdb 未安装（pip install duckdb 或安装 l3_node/requirements-bi.txt）"}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS pmo_bitable_export_meta (
                snapshot_date VARCHAR,
                slug VARCHAR,
                label VARCHAR,
                record_count INTEGER,
                json_path VARCHAR,
                md_path VARCHAR,
                exported_at TIMESTAMP,
                PRIMARY KEY (snapshot_date, slug)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS pmo_bitable_records (
                snapshot_date VARCHAR,
                slug VARCHAR,
                label VARCHAR,
                record_id VARCHAR,
                fields_json VARCHAR,
                PRIMARY KEY (snapshot_date, slug, record_id)
            )
        """)
        con.execute("DELETE FROM pmo_bitable_records WHERE snapshot_date = ? AND slug = ?", [snapshot_date, slug])
        con.execute("DELETE FROM pmo_bitable_export_meta WHERE snapshot_date = ? AND slug = ?", [snapshot_date, slug])
        now = datetime.now(timezone.utc)
        for i, rec in enumerate(records):
            rid = str(rec.get("record_id") or rec.get("id") or f"row_{i}")
            fld = rec.get("fields") or {}
            disp = rows_disp[i] if i < len(rows_disp) else fld
            con.execute(
                "INSERT INTO pmo_bitable_records VALUES (?, ?, ?, ?, ?)",
                [snapshot_date, slug, label, rid, json.dumps(disp, ensure_ascii=False)],
            )
        con.execute(
            "INSERT INTO pmo_bitable_export_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
            [snapshot_date, slug, label, len(records), json_path, md_path, now],
        )
        con.commit()
        return {"duckdb": str(db_path), "rows_inserted": len(records)}
    finally:
        con.close()


def run_pmo_bitable_export(cfg: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """
    cfg: app_id, app_secret, lark_use_feishu, snapshot_date?, max_export_records?,
         json_raw_dir? (override), md_raw_rel? (override)
    """
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token
    from l3_node.primitives.mcp.mcp_tools.pmo_bmo.paths import (
        ensure_pmo_dirs,
        get_pmo_docs_raw_rel,
        get_pmo_duckdb_path,
        get_pmo_raw_dir,
    )

    ensure_pmo_dirs()
    snap = (cfg.get("snapshot_date") or "").strip()[:10] or date.today().isoformat()
    max_recs = int(cfg.get("max_export_records") or cfg.get("max_records_per_table") or 8000)

    app_id = (cfg.get("app_id") or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
    app_secret = (cfg.get("app_secret") or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if cfg.get("lark_use_feishu") in (True, "true", "1", "yes"):
        os.environ["LARK_USE_FEISHU"] = "1"
    elif cfg.get("lark_use_feishu") in (False, "false", "0", "no"):
        os.environ.pop("LARK_USE_FEISHU", None)

    if not app_id or not app_secret or app_id.startswith("${"):
        return {"status": "error", "error": "未配置 app_id/app_secret"}

    raw_dir = Path((cfg.get("json_raw_dir") or str(get_pmo_raw_dir())).strip())
    raw_dir.mkdir(parents=True, exist_ok=True)

    md_rel = (cfg.get("md_raw_rel") or get_pmo_docs_raw_rel()).strip()
    md_dir = (project_root / md_rel).resolve()
    md_dir.mkdir(parents=True, exist_ok=True)
    _remove_legacy_dated_repo_md(
        md_dir,
        {str(s.get("slug") or "") for s in PMO_SCHEDULED_BITABLES} - {""},
    )

    api_base = get_lark_api_base()
    try:
        token = get_tenant_access_token(app_id=app_id, app_secret=app_secret, api_base=api_base)
    except Exception as e:
        return {"status": "error", "error": f"token: {e}"}

    db_path = get_pmo_duckdb_path()
    if cfg.get("duckdb_path"):
        db_path = Path(str(cfg["duckdb_path"]).strip())

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    if os.environ.get("LARK_USE_FEISHU", "").lower() in ("1", "true", "yes"):
        if any("larksuite.com" in str(s.get("url", "")) for s in PMO_SCHEDULED_BITABLES):
            warnings.append(
                "已启用 lark_use_feishu（API 为 open.feishu.cn），但 PMO 种子链接为 *.larksuite.com；"
                "易出现 WrongTableId。国际 Lark 请在 pmo_bmo.yaml 设 lark_use_feishu: false。"
            )

    docx_ids_cfg = cfg.get("docx_document_ids")
    if not isinstance(docx_ids_cfg, dict):
        docx_ids_cfg = {}
    env_docx = (os.environ.get("PMO_REQ_MARCH_COARSE_DOCX_ID") or "").strip()
    if env_docx and not (str(docx_ids_cfg.get("req_march_coarse") or "").strip()):
        docx_ids_cfg = dict(docx_ids_cfg)
        docx_ids_cfg["req_march_coarse"] = env_docx
    # 与 main_skill.PMO_DEFAULT_REQ_MARCH_COARSE_DOCX_ID 一致：K11「需求表3月」云文档
    _default_march_docx = "ZcpedCREaoNrQUxvM7EluZGugWg"
    if not (str(docx_ids_cfg.get("req_march_coarse") or "").strip()):
        docx_ids_cfg = dict(docx_ids_cfg)
        docx_ids_cfg["req_march_coarse"] = _default_march_docx

    for spec_raw in PMO_SCHEDULED_BITABLES:
        spec = dict(spec_raw)
        slug = spec["slug"]
        oid = docx_ids_cfg.get(slug)
        if oid:
            spec["docx_document_id"] = str(oid).strip()
        label = spec["label"]
        su = sanitize_wiki_url(spec["url"])
        parsed = parse_wiki_url(su)
        node_token = parsed.get("node_token") or ""
        table_id_from_url = (parsed.get("table_id") or "").strip()
        view_id = parsed.get("view_id") or None
        v_override = (spec.get("view_id_override") or "").strip()
        if v_override:
            view_id = v_override
        spec_name_resolve = (spec.get("table_name_resolve") or "").strip()
        spec_table_id_override = (spec.get("table_id_override") or "").strip()
        if not node_token:
            errors.append(f"{slug}: 无法解析 wiki URL（缺少 node_token）")
            continue

        g = _lark_get(api_base, token, "/wiki/v2/spaces/get_node", {"token": node_token})
        if g.get("code") != 0:
            errors.append(f"{slug}: get_node {g.get('msg')}")
            continue
        raw_d = g.get("data") or {}
        node = raw_d.get("node") if isinstance(raw_d.get("node"), dict) else raw_d
        if not isinstance(node, dict):
            errors.append(f"{slug}: 无效 node")
            continue

        export_mode = (spec.get("export_mode") or "bitable").strip().lower()
        if export_mode == "docx_table":
            doc_res, doc_err = _run_export_docx_table(
                api_base,
                token,
                spec,
                su,
                node_token,
                node,
                snap,
                slug,
                label,
                raw_dir,
                md_dir,
                project_root,
                db_path,
                max_recs,
            )
            if doc_err:
                errors.append(f"{slug}: {doc_err}")
                continue
            results.append(doc_res)
            continue

        obj_type = (node.get("obj_type") or "").lower()
        app_token = (node.get("obj_token") or "").strip()
        if obj_type != "bitable" or not app_token:
            errors.append(f"{slug}: 节点非 bitable 或缺少 obj_token（obj_type={obj_type}）")
            continue

        tables_cache: list[dict[str, Any]] | None = None

        def _tables_cached() -> list[dict[str, Any]]:
            nonlocal tables_cache
            if tables_cache is None:
                tables_cache = _bitable_list_tables(api_base, token, app_token)
            return tables_cache

        if spec_table_id_override:
            table_id = spec_table_id_override
            if not str(table_id).startswith("tbl"):
                errors.append(
                    f"{slug}: table_id_override 须为多维表开放接口可用的 tbl_ 子表 ID（当前 {table_id!r}）"
                )
                continue
        elif spec_name_resolve:
            tables = _tables_cached()
            resolved = _resolve_table_id_by_name(tables, spec_name_resolve)
            if not resolved:
                catalog = [(t.get("name"), t.get("table_id")) for t in tables]
                errors.append(f"{slug}: 按名称未找到子表 {spec_name_resolve!r}，现有: {catalog}")
                continue
            table_id = resolved
        else:
            if not table_id_from_url:
                errors.append(f"{slug}: URL 缺少 table= 且未配置 table_name_resolve / table_id_override")
                continue
            table_id = table_id_from_url
            if not str(table_id).startswith("tbl"):
                tables = _tables_cached()
                resolved = _resolve_table_id_by_name(tables, label) or _resolve_table_id_by_name(
                    tables, slug.replace("_", " ")
                )
                if resolved:
                    table_id = resolved
                else:
                    catalog = [(t.get("name"), t.get("table_id")) for t in tables]
                    errors.append(
                        f"{slug}: table 参数非标准 tbl ID ({table_id!r})，且无法按名称匹配；"
                        f"请配置 table_id_override（真实 tbl_）或 table_name_resolve。现有子表: {catalog}"
                    )
                    continue

        fields_meta, ferr = _bitable_list_fields(api_base, token, app_token, table_id)
        recs: list[dict[str, Any]] = []
        rerr: str | None = None
        if ferr:
            catalog = [(t.get("name"), t.get("table_id")) for t in _tables_cached()]
            errors.append(f"{slug}: 拉取字段失败 table_id={table_id!r} msg={ferr!r}。子表列表: {catalog}")
        else:
            recs, rerr = _bitable_list_records(api_base, token, app_token, table_id, max_recs, view_id)
            if rerr:
                errors.append(f"{slug}: 拉取记录失败 table_id={table_id!r} msg={rerr!r}")

        fid_to_name, col_order = _norm_fields_map(fields_meta)
        rows_disp = [_record_to_display_row(r.get("fields") or {}, fid_to_name, col_order) for r in recs]
        all_cols: set[str] = set()
        for m in rows_disp:
            all_cols.update(m.keys())
        col_order_final = [c for c in col_order if c in all_cols] + sorted(all_cols - set(col_order))

        payload = {
            "snapshot_date": snap,
            "slug": slug,
            "label": label,
            "wiki_url": su,
            "node_token": node_token,
            "app_token": app_token,
            "table_id": table_id,
            "view_id": view_id,
            "fields": fields_meta,
            "records": recs,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "field_error": ferr,
            "record_error": rerr,
        }

        json_name = f"{snap}_{slug}.json"
        json_path = raw_dir / json_name
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        md_body = _render_md_table(label, table_id, view_id, col_order_final, rows_disp)
        md_path = md_dir / _pmo_repo_md_basename(slug)
        meta_header = (
            "## 同步元数据\n\n```json\n"
            + json.dumps(
                {"snapshot_date": snap, "slug": slug, "json_file": str(json_path)},
                ensure_ascii=False,
                indent=2,
            )
            + "\n```\n\n---\n\n"
        )
        md_path.write_text(meta_header + md_body, encoding="utf-8")

        db_r = _duckdb_ingest(
            db_path,
            snap,
            slug,
            label,
            str(json_path),
            str(md_path),
            recs,
            rows_disp,
        )

        try:
            md_rel = str(md_path.relative_to(project_root))
        except ValueError:
            md_rel = str(md_path)
        results.append({
            "slug": slug,
            "label": label,
            "record_count": len(recs),
            "table_id": table_id,
            "field_error": ferr,
            "record_error": rerr,
            "json": str(json_path),
            "md": md_rel,
            **db_r,
        })

    ok = len(results) > 0 and len(errors) == 0
    try:
        md_dir_rel = str(md_dir.relative_to(project_root))
    except ValueError:
        md_dir_rel = str(md_dir)
    return {
        "status": "success" if ok else ("partial" if results else "error"),
        "snapshot_date": snap,
        "tables_ok": len(results),
        "errors": errors,
        "warnings": warnings,
        "api_base": api_base,
        "results": results,
        "duckdb_path": str(db_path),
        "md_dir": md_dir_rel,
        "json_raw_dir": str(raw_dir),
    }
