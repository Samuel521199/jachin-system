"""
自然周/自然月留存对比 — 仅抓取两张对比表，写入 raw_natural/

菜单：数据统计分析 → 留存数据统计 → 新增用户留存对比 / 新增付费留存对比
对应 slug：stats_retention_user_compare、stats_retention_paid_compare

供 mcp:atom_bi_natural_retention_collect 与 bi_natural skill 调用。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

NATURAL_RETENTION_SLUGS: tuple[str, ...] = (
    "stats_retention_user_compare",
    "stats_retention_paid_compare",
)


def run_natural_retention_compare_collect(
    period1_start: str,
    period1_end: str,
    period2_start: str,
    period2_end: str,
    *,
    raw_dir: Path | str | None = None,
    base_url: str | None = None,
    cdp_url: str | None = None,
    direct_url_map: dict[str, str] | None = None,
    auto_ingest: bool = False,
    progress_cb: Callable[[int, int, str, dict], None] | None = None,
    scraper_filter_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    按给定两段时间（各为闭区间 YYYY-MM-DD）填写对比页并抓取 CSV 到 raw_natural。

    scraper_filter_overrides: 合并进每条 automation.filters（如 date_range_compare_use_visual_order、wait_after_query_ms）。

    Returns:
        {"ok": bool, "ok_count": int, "fail_count": int, "failed_slugs": [...], "raw_dir": str, "files": {...}}
    """
    from l3_node.mcp_tools.bi.spa_collector import (
        DEFAULT_BASE_URL,
        DEFAULT_CDP_URL,
        MENU_ITEMS,
        TABLE_SEL,
        _DEFAULT_QUERY_REFRESH_FILTERS,
        _apply_slug_specific_automation,
        get_automation_for_direct_url,
    )
    from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
    from l3_node.mcp_tools.bi.paths import ensure_bi_dirs, get_bi_raw_natural_dir

    ensure_bi_dirs()
    out_dir = Path(raw_dir) if raw_dir else get_bi_raw_natural_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    p1s, p1e = str(period1_start)[:10], str(period1_end)[:10]
    p2s, p2e = str(period2_start)[:10], str(period2_end)[:10]
    custom_compare = [[p1s, p1e], [p2s, p2e]]

    try:
        as_of = date.fromisoformat(p1e)
    except ValueError:
        as_of = datetime.now().date()

    _base = (base_url or DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    _cdp = (cdp_url or DEFAULT_CDP_URL).strip() or DEFAULT_CDP_URL
    durl = direct_url_map or {}

    prod_sales_slugs = ("prod_sales", "prod_sales_compare")
    recharge_expand_slugs = ("recharge_status", "recharge_compare")
    game_stats_expand_slugs = (
        "stats_game_daily",
        "stats_game_compare",
        "stats_game_core",
        "stats_game_active",
        "stats_game_new",
    )
    expand_pages = ("stats_user_dau", "stats_user_new") + game_stats_expand_slugs + prod_sales_slugs + recharge_expand_slugs

    slug_set = set(NATURAL_RETENTION_SLUGS)
    all_items = [(s, n, a) for s, n, a in MENU_ITEMS if s in slug_set]

    manifest_path = out_dir / "last_collect_manifest.json"
    try:
        manifest_path.write_text(
            json.dumps(
                {
                    "period1": [p1s, p1e],
                    "period2": [p2s, p2e],
                    "collected_at": datetime.now().isoformat(timespec="seconds"),
                    "slugs": list(NATURAL_RETENTION_SLUGS),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("[natural_retention] manifest write: %s", e)

    ok, fail = 0, 0
    failed_slugs: list[str] = []
    files: dict[str, str] = {}
    total = len(all_items)

    for idx, (slug_name, display_name, actions) in enumerate(all_items):
        out_path = str(out_dir / f"{slug_name}.csv")
        raw_du = str(durl.get(slug_name) or "").strip()
        use_direct = bool(raw_du) and raw_du.lower().startswith("http")

        if use_direct:
            automation = get_automation_for_direct_url(slug_name, raw_du)
            automation["split_merged_cells"] = True
            _apply_slug_specific_automation(automation, slug_name, report_date_end=as_of)
            if not automation.get("filters"):
                automation["filters"] = dict(_DEFAULT_QUERY_REFRESH_FILTERS)
            page_url = raw_du
        else:
            is_expand_heavy = slug_name in prod_sales_slugs or slug_name in recharge_expand_slugs
            needs_longer_expand = slug_name in expand_pages or slug_name in prod_sales_slugs or slug_name in recharge_expand_slugs
            game_stats_wait = 2000 if slug_name in game_stats_expand_slugs else 800
            automation = {
                "start_url": _base,
                "actions": actions,
                "expand_table_rows": True,
                "expand_wait_ms": game_stats_wait
                if slug_name in game_stats_expand_slugs
                else (800 if needs_longer_expand else 600),
                "expand_post_wait_ms": 3000
                if slug_name in game_stats_expand_slugs
                else (1500 if is_expand_heavy else (800 if slug_name in expand_pages else 500)),
                "split_merged_cells": True,
            }
            _apply_slug_specific_automation(automation, slug_name, report_date_end=as_of)
            page_url = _base

        flt = automation.get("filters") or {}
        flt = dict(flt)
        flt["date_range_compare"] = custom_compare
        flt.setdefault(
            "query_selector",
            "button:has-text('对比查询'), .el-button:has-text('对比查询')",
        )
        flt.setdefault("wait_after_query_ms", 5000)
        flt.setdefault("wait_for_data_timeout", 45)
        # 用户/付费留存对比页标签文案可能不同；按主表单内日期框「从左到右」对应段1/段2（见 tool_web_scraper）
        flt.setdefault("date_range_compare_use_visual_order", True)
        # 勿在段与段之间按 Escape：会卸掉整块筛选 DOM（日志中 ed=0 ri=0）
        flt.setdefault("date_range_compare_no_escape_after_fill", True)
        if isinstance(scraper_filter_overrides, dict) and scraper_filter_overrides:
            flt.update(scraper_filter_overrides)
        automation["filters"] = flt

        # 直链进入时无菜单步骤，Vue 未挂载完就填日期会失败；菜单路径亦需表单就绪
        _pre_actions: list[dict[str, Any]] = [
            {"type": "wait_ms", "ms": 2500},
            {
                "type": "wait_visible",
                "selector": ".el-date-editor",
                "timeout": 30,
            },
        ]
        automation["actions"] = _pre_actions + list(automation.get("actions") or [])

        try:
            diff_label = f"{slug_name}|{display_name}|natural"
            r = harvest_table_data(
                url=page_url,
                output_path=out_path,
                config={
                    "cdp_url": _cdp,
                    "output_format": "csv",
                    "timeout": 60,
                    "extract_rules": TABLE_SEL,
                    "automation": automation,
                    "diff_log_context": diff_label,
                },
            )
            if progress_cb:
                progress_cb(idx + 1, total, slug_name, r)

            if r.get("status") == "success":
                ok += 1
                files[slug_name] = r.get("file_path", out_path)
                if auto_ingest:
                    from l3_node.mcp_tools.bi.data_store import ingest_csv

                    ingest_r = ingest_csv(files[slug_name], slug_name)
                    if ingest_r.get("status") != "success":
                        logger.warning("[natural_retention] ingest %s: %s", slug_name, ingest_r.get("error"))
            else:
                fail += 1
                failed_slugs.append(slug_name)
                logger.warning("[natural_retention] %s FAIL: %s", slug_name, r.get("error", r))
        except Exception as e:
            fail += 1
            failed_slugs.append(slug_name)
            logger.exception("[natural_retention] %s: %s", slug_name, e)
            if progress_cb:
                progress_cb(idx + 1, total, slug_name, {"status": "error", "error": str(e)})

    return {
        "ok": fail == 0 and ok > 0,
        "ok_count": ok,
        "fail_count": fail,
        "failed_slugs": failed_slugs,
        "raw_dir": str(out_dir.resolve()),
        "files": files,
        "period1": [p1s, p1e],
        "period2": [p2s, p2e],
    }


def atom_bi_natural_retention_collect_mcp(
    period1_start: str = "",
    period1_end: str = "",
    period2_start: str = "",
    period2_end: str = "",
    raw_dir: str = "",
    base_url: str = "",
    cdp_url: str = "",
    auto_ingest: bool = False,
    full_spa_config: dict[str, Any] | None = None,
) -> str:
    """MCP 入口：JSON 字符串结果。"""
    from l3_node.mcp_tools.bi.spa_collector import parse_direct_url_map_from_full_spa

    dmap = None
    if isinstance(full_spa_config, dict):
        dmap = parse_direct_url_map_from_full_spa(full_spa_config)
    rd = Path(raw_dir) if (raw_dir or "").strip() else None
    out = run_natural_retention_compare_collect(
        period1_start or "",
        period1_end or "",
        period2_start or "",
        period2_end or "",
        raw_dir=rd,
        base_url=base_url or None,
        cdp_url=cdp_url or None,
        direct_url_map=dmap,
        auto_ingest=bool(auto_ingest),
    )
    return json.dumps(out, ensure_ascii=False)
