"""
PMO INIT 确定性路径：拉表 + mirror_import，**零 ReAct**。

拉表阶段将飞书 API 记录（JSON）落盘为 ``pmo_lark_pull/*.md``（GFM 表）及可选 ``*.records.json``，
供后续 ``core:pmo_mirror_import`` 与宿主/模型检索。

SSOT：skills_repo/pmo-copilot/SKILL.md §1.1（12 视图）+ config/mcps/atom_bi_project_context/config.yaml。
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# PMO 12 视图（与 tool_bi_project_context._default_wiki_urls 中 PMO 种子对齐，不含 BI 辅助 sheet/wiki）
PMO_INIT_WIKI_URLS: tuple[str, ...] = (
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblNdv7DIlycuqxp&view=vew8TxMcSh",
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZItbw4omRi6Sbsksb6jlwYq8gYq?table=tblNdv7DIlycuqxp&view=vewL9Mofgd",
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
)

_PMO_INIT_ALLOWED_TOOLS = frozenset({
    "atom_bi_project_context",
    "pmo_mirror_import",
})


def pmo_init_wiki_urls() -> list[str]:
    """返回 PMO INIT 须拉取的 12 个 wiki_urls。"""
    return list(PMO_INIT_WIKI_URLS)


def _load_bi_pull_config() -> dict[str, Any]:
    from l3_node.paths import get_app_root

    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import (
        PMO_LARK_PULL_REL,
        _load_merge_config,
    )

    cfg = _load_merge_config({"wiki_urls": pmo_init_wiki_urls()}, get_app_root())
    # PMO INIT 拉盘 SSOT：与 mirror_import 默认 manifest 目录一致（勿相对安装目录）
    cfg["output_dir_relative"] = PMO_LARK_PULL_REL
    cfg["emit_pull_records_json"] = True
    return cfg


def _mirror_import_kwargs_from_pull(pull_result: dict[str, Any] | None) -> dict[str, str]:
    """从本轮拉盘结果推导 mirror_import 的 manifest_path / pull_dir。"""
    if not pull_result:
        return {}
    out_dir = str(pull_result.get("output_dir") or "").strip()
    if not out_dir:
        return {}
    base = Path(out_dir)
    return {
        "manifest_path": str(base / "00_SYNC_MANIFEST.json"),
        "pull_dir": out_dir,
    }


def pmo_skip_pull_markdown_refresh() -> bool:
    """为真时跳过多 Agent 前的「拉表 → md」刷新（环境变量 ``PMO_SKIP_PULL_MD``）。"""
    return os.environ.get("PMO_SKIP_PULL_MD", "").strip().lower() in ("1", "true", "yes", "on")


def pmo_expected_view_ids() -> frozenset[str]:
    """§1.1 十二视图 view_id（与 PMO_INIT_WIKI_URLS 对齐）。"""
    out: set[str] = set()
    for url in PMO_INIT_WIKI_URLS:
        v = (parse_qs(urlparse(url).query).get("view") or [None])[0]
        if v:
            out.add(str(v).strip())
    return frozenset(out)


def _parse_sync_local_date(iso_str: str) -> date | None:
    if not (iso_str or "").strip():
        return None
    try:
        s = iso_str.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.date()
    except ValueError:
        return None


def _pmo_views_meta_coverage() -> tuple[bool, int, int]:
    """(是否覆盖全部预期 view_id, 已登记数, 预期数)。"""
    expected = pmo_expected_view_ids()
    if not expected:
        return False, 0, 0
    from l3_node.tools.pmo_db_tools import _connect, ensure_pmo_schema

    placeholders = ",".join("?" * len(expected))
    conn = _connect()
    try:
        ensure_pmo_schema(conn)
        rows = conn.execute(
            f"SELECT view_id FROM pmo_views_meta WHERE view_id IN ({placeholders})",
            list(expected),
        ).fetchall()
        found = {str(r["view_id"]) for r in rows}
        return expected <= found, len(found), len(expected)
    finally:
        conn.close()


def _pmo_latest_mirror_sync_local_date() -> date | None:
    from l3_node.tools.pmo_db_tools import _connect, ensure_pmo_schema

    conn = _connect()
    try:
        ensure_pmo_schema(conn)
        row = conn.execute(
            "SELECT MAX(synced_at) AS ts FROM pmo_views_meta"
        ).fetchone()
        if not row or not row["ts"]:
            return None
        return _parse_sync_local_date(str(row["ts"]))
    finally:
        conn.close()


def _pmo_manifest_local_date() -> date | None:
    from l3_node.tools.pmo_db_tools import get_default_pmo_manifest_path

    path = get_default_pmo_manifest_path()
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def pmo_resolve_refresh_pull_markdown() -> tuple[bool, str]:
    """
    是否需要在 FanOut 前执行拉表 + mirror_import。

    默认：**今日**（本地日历日）已成功落盘且十二视图已入库 → **不拉**；
    镜像库空、视图不齐、或上次入库非今日 → **拉**。
    """
    if os.environ.get("PMO_FORCE_PULL_MD", "").strip().lower() in ("1", "true", "yes", "on"):
        return True, "PMO_FORCE_PULL_MD=1 强制拉表"
    if pmo_skip_pull_markdown_refresh():
        return False, "PMO_SKIP_PULL_MD=1 跳过拉表"

    from l3_node.tools.pmo_db_tools import pmo_mirror_db_ready

    if not pmo_mirror_db_ready():
        return True, "镜像库无数据（pmo_raw_records 为空）"

    ok_views, n_found, n_exp = _pmo_views_meta_coverage()
    if not ok_views:
        return True, f"pmo_views_meta 视图不齐（{n_found}/{n_exp}），需拉表"

    today = date.today()
    manifest_day = _pmo_manifest_local_date()
    sync_day = _pmo_latest_mirror_sync_local_date()

    if manifest_day == today or sync_day == today:
        hint = []
        if manifest_day == today:
            hint.append("manifest 今日已更新")
        if sync_day == today:
            hint.append(f"入库 synced_at={sync_day}")
        return False, "；".join(hint) + f"（{n_found}/{n_exp} 视图）"

    if sync_day:
        return True, f"上次镜像入库为 {sync_day}（非今日），将重新拉表"
    if manifest_day:
        return True, f"拉盘 manifest 日期为 {manifest_day}（非今日），将重新拉表"
    return True, "无 manifest/入库时间记录，将拉表"


def run_pmo_pull_markdown() -> dict[str, Any]:
    """仅拉表落盘：sync_bi_project_context（JSON→md + 可选 records.json），不入库。"""
    out: dict[str, Any] = {"status": "error", "steps": ["pull"]}
    try:
        from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import (
            sync_bi_project_context,
        )

        cfg = _load_bi_pull_config()
        pull_result = sync_bi_project_context(cfg)
        out["pull"] = pull_result
        st = str(pull_result.get("status") or "").lower()
        if st == "error" or pull_result.get("error"):
            out["message"] = str(pull_result.get("error") or pull_result.get("msg") or "拉表失败")
            return out
        files = pull_result.get("files") if isinstance(pull_result.get("files"), list) else []
        md_n = sum(1 for f in files if str(f).endswith(".md"))
        json_n = sum(1 for f in files if str(f).endswith(".records.json"))
        out["status"] = "ok"
        out["message"] = f"拉表落盘完成：{md_n} 个 md，{json_n} 个 records.json"
        return out
    except Exception as e:
        logger.exception("[PMO] pull markdown failed")
        out["message"] = f"拉表异常: {e}"
        out["pull"] = {"status": "error", "error": str(e)}
        return out


def run_pmo_init_direct(*, skip_pull: bool = False) -> dict[str, Any]:
    """
    确定性 INIT：sync_bi_project_context → run_mirror_import。
    不经过 ReAct / LLM。
    """
    out: dict[str, Any] = {"status": "error", "steps": []}

    pull_result: dict[str, Any] | None = None
    if not skip_pull:
        try:
            from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import (
                sync_bi_project_context,
            )

            cfg = _load_bi_pull_config()
            pull_result = sync_bi_project_context(cfg)
            out["pull"] = pull_result
            out["steps"].append("pull")
            st = str(pull_result.get("status") or "").lower()
            if st == "error" or pull_result.get("error"):
                out["status"] = "error"
                out["message"] = str(pull_result.get("error") or pull_result.get("msg") or "拉表失败")
                return out
        except Exception as e:
            logger.exception("[PMO INIT] pull failed")
            out["status"] = "error"
            out["message"] = f"拉表异常: {e}"
            out["pull"] = {"status": "error", "error": str(e)}
            return out

    try:
        from l3_node.tools.pmo_mirror_import import run_mirror_import

        import_result = run_mirror_import(**_mirror_import_kwargs_from_pull(pull_result))
        out["import"] = import_result
        out["steps"].append("mirror_import")
    except Exception as e:
        logger.exception("[PMO INIT] mirror_import failed")
        out["status"] = "error"
        out["message"] = f"镜像入库异常: {e}"
        out["import"] = {"status": "error", "error": str(e)}
        return out

    imp_st = str(import_result.get("status") or "").lower()
    total = int(import_result.get("total_records") or 0)
    if imp_st != "ok" or total <= 0:
        out["status"] = "error"
        out["message"] = str(
            import_result.get("message")
            or import_result.get("error")
            or f"mirror_import 未成功（total_records={total}）"
        )
        return out

    from l3_node.tools.pmo_db_tools import pmo_mirror_db_ready

    out["db_ready"] = pmo_mirror_db_ready()
    out["status"] = "ok" if out["db_ready"] else "error"
    if not out["db_ready"]:
        out["message"] = "mirror_import 返回 ok 但 pmo_mirror_db_ready() 仍为 false"
    else:
        out["message"] = f"INIT 完成：{total:,} 条记录已镜像入库"
    return out


def format_pmo_init_direct_summary(result: dict[str, Any]) -> str:
    """人类可读摘要（CLI / debug 日志）。"""
    lines: list[str] = []
    pull = result.get("pull") if isinstance(result.get("pull"), dict) else {}
    imp = result.get("import") if isinstance(result.get("import"), dict) else {}
    if pull:
        files = pull.get("files") if isinstance(pull.get("files"), list) else []
        out_dir = str(pull.get("output_dir") or "").strip()
        md_n = sum(1 for f in files if str(f).endswith(".md"))
        json_n = sum(1 for f in files if str(f).endswith(".records.json"))
        lines.append(
            f"拉表: {md_n} 个 md + {json_n} 个 records.json（共 {len(files)} 项）"
            f" → {out_dir or '（见 pull.output_dir）'}"
        )
    if imp:
        total = int(imp.get("total_records") or 0)
        views = imp.get("views") if isinstance(imp.get("views"), list) else []
        lines.append(f"入库: total_records={total:,}，views={len(views)}")
    st = str(result.get("status") or "")
    msg = str(result.get("message") or "").strip()
    lines.append(f"状态: {st}" + (f" — {msg}" if msg else ""))
    return "\n".join(lines)
