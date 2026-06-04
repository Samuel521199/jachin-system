"""
PMO INIT 确定性路径：拉表 + mirror_import，**零 ReAct**。

SSOT：skills_repo/pmo-copilot/SKILL.md §1.1（12 视图）+ config/mcps/atom_bi_project_context/config.yaml。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

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

    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import _load_merge_config

    return _load_merge_config({"wiki_urls": pmo_init_wiki_urls()}, get_app_root())


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

        import_result = run_mirror_import()
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
        lines.append(f"拉表: {len(files)} 个 md 文件 → {out_dir or '（见 pull.output_dir）'}")
    if imp:
        total = int(imp.get("total_records") or 0)
        views = imp.get("views") if isinstance(imp.get("views"), list) else []
        lines.append(f"入库: total_records={total:,}，views={len(views)}")
    st = str(result.get("status") or "")
    msg = str(result.get("message") or "").strip()
    lines.append(f"状态: {st}" + (f" — {msg}" if msg else ""))
    return "\n".join(lines)
