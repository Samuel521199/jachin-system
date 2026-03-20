#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仪表盘分析 + Lark 定时发送配置 — 独立测试入口

仅测试：LLM 分析仪表盘 → 保存到 output → 打开 Lark 仪表盘 → 点击「设置自动化发送」→ 填入分析、设置定时 →「保存并启用」。
前置：output 目录需有 CSV（可先运行 run_bi_report_refiner 或 run_bi_analysis 一次）。cdp_url 留空时 Playwright 自动启动浏览器，需首次登录 Lark。

用法:
  python scripts/run_bi_dashboard_automation.py              # 用 config 中的 scheduled_time
  python scripts/run_bi_dashboard_automation.py --time 18:05 # 覆盖 config 中的 scheduled_time
  python scripts/run_bi_dashboard_automation.py --dry-run     # 只生成分析、保存，不打开 Lark
  python scripts/run_bi_dashboard_automation.py --push-cards # 仅测试：生成分析 + 每个仪表盘单独发一条 Lark 卡片（分析+URL），不发浏览器自动化
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env", encoding="utf-8")
except ImportError:
    pass
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _load_config() -> dict:
    from l3_node.skills.bi.bi_daily_report.main_skill import _load_config as _load
    return _load(None)


def _run_push_cards_only() -> int:
    """仅测试：生成仪表盘分析 + 每个仪表盘单独发一条 Lark 卡片（分析+URL）"""
    cfg = _load_config()
    da_cfg = cfg.get("dashboard_automation") or {}
    dist = cfg.get("distribution") or {}
    dashboards = da_cfg.get("dashboards") or []
    if not dashboards:
        print("[push-cards] dashboards 未配置")
        return 1

    from l3_node.mcp_tools.bi.paths import get_bi_output_dir, ensure_bi_dirs
    from l3_node.skills.bi.bi_daily_report.dashboard_automation import (
        generate_dashboard_analysis_async,
        _save_analysis_to_file,
    )

    ensure_bi_dirs()
    output_dir = get_bi_output_dir((cfg.get("storage") or {}).get("refiner_output_path") or "")
    analysis_output_subdir = str(da_cfg.get("analysis_output_subdir") or "统计分析").strip() or "统计分析"
    analysis_output_dir = output_dir / analysis_output_subdir
    analysis_output_dir.mkdir(parents=True, exist_ok=True)

    print("\n[push-cards] 1. 生成仪表盘分析...")
    analyses: list[tuple[str, str]] = []
    for i, dash in enumerate(dashboards):
        if not isinstance(dash, dict):
            continue
        name = (dash.get("name") or "").strip()
        if not name:
            continue
        print(f"  [{i + 1}/{len(dashboards)}] {name}")
        analysis = asyncio.run(generate_dashboard_analysis_async(name, output_dir, cfg))
        _save_analysis_to_file(analysis_output_dir, name, analysis)
        analyses.append((name, analysis))

    if not analyses:
        print("[push-cards] 无分析产出，退出")
        return 1

    print("\n[push-cards] 2. 发送 Lark 卡片（每个仪表盘一条）...")
    _lark_webhook = (dist.get("lark_webhook_url") or "").strip()
    _lark_chat_id = (dist.get("lark_chat_id") or "").strip()
    if str(_lark_webhook).startswith("${"):
        _lark_webhook = ""
    if str(_lark_chat_id).startswith("${"):
        _lark_chat_id = ""
    if not _lark_chat_id and not _lark_webhook:
        import os
        _lark_chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
    if not _lark_chat_id and not _lark_webhook:
        try:
            from l3_node.jachin_config import load_mcp_config
            from l3_node.paths import get_app_root
            _mcp = load_mcp_config("atom_lark_notifier", project_root=get_app_root())
            _lark_chat_id = (_mcp.get("default_chat_id") or "").strip()
            if str(_lark_chat_id).startswith("${"):
                _lark_chat_id = ""
        except Exception:
            pass
    if not _lark_webhook and not _lark_chat_id:
        print("[push-cards] 未配置 lark_webhook_url 或 lark_chat_id，跳过推送")
        return 0

    url_by_name = {str(d.get("name", "")).strip(): str(d.get("url", "")).strip() for d in dashboards if isinstance(d, dict)}
    lark_cfg = cfg.get("lark_bitable") or {}
    if lark_cfg.get("app_id") and lark_cfg.get("app_secret"):
        import os
        os.environ.setdefault("LARK_APP_ID", str(lark_cfg.get("app_id", "")).strip())
        os.environ.setdefault("LARK_APP_SECRET", str(lark_cfg.get("app_secret", "")).strip())
    if lark_cfg.get("lark_use_feishu"):
        import os
        os.environ["LARK_USE_FEISHU"] = "1"

    from l3_node.mcp_tools.bi.tool_lark_notifier import send_lark_markdown
    from l3_node.skills.bi.bi_daily_report.main_skill import _DASHBOARD_DISPLAY_URLS
    sent_ok = 0
    for name, text in analyses:
        url = _DASHBOARD_DISPLAY_URLS.get(name) or url_by_name.get(name, "")
        if url and not url.startswith("${"):
            card_md = f"{text}\n\n---\n\n[打开仪表盘]({url})"
        else:
            card_md = text
        r = send_lark_markdown(_lark_webhook or "", card_md[:6000], title=f"📊 {name}", chat_id=_lark_chat_id or None)
        if r.get("status") == "success":
            sent_ok += 1
            print(f"  ✓ {name}")
        else:
            print(f"  ✗ {name}: {r.get('error', '')}")
    print(f"\n[push-cards] 完成: 成功 {sent_ok}/{len(analyses)} 条")
    return 0


def _run_dashboard_only(scheduled_time_override: str | None = None, dry_run: bool = False) -> int:
    cfg = _load_config()
    da_cfg = cfg.get("dashboard_automation") or {}
    if not da_cfg.get("enabled"):
        print("[仪表盘] 已禁用，请在 config 中设置 dashboard_automation.enabled: true")
        return 1
    dashboards = da_cfg.get("dashboards") or []
    if not dashboards:
        print("[仪表盘] dashboards 未配置")
        return 1

    from l3_node.mcp_tools.bi.paths import get_bi_output_dir, ensure_bi_dirs
    from l3_node.skills.bi.bi_daily_report.dashboard_automation import (
        generate_dashboard_analysis_async,
        _save_analysis_to_file,
        setup_lark_dashboard_automation_via_browser,
    )

    ensure_bi_dirs()
    output_dir = get_bi_output_dir((cfg.get("storage") or {}).get("refiner_output_path") or "")
    analysis_output_subdir = str(da_cfg.get("analysis_output_subdir") or "统计分析").strip() or "统计分析"
    analysis_output_dir = output_dir / analysis_output_subdir
    analysis_output_dir.mkdir(parents=True, exist_ok=True)

    scheduled_time = scheduled_time_override or str(da_cfg.get("scheduled_time") or "").strip()
    if not scheduled_time_override and (not scheduled_time or ":" not in scheduled_time or len(scheduled_time) < 4):
        print("[仪表盘] dashboard_automation.scheduled_time 未配置或格式无效，请在 config 中设置 HH:MM 或使用 --time HH:MM")
        return 1
    cdp_url = str(da_cfg.get("cdp_url") or "").strip()

    print(f"\n[仪表盘] 定时时间: {scheduled_time} (来自 {'命令行' if scheduled_time_override else 'config'})")
    print(f"[仪表盘] 分析 MD 保存目录: {analysis_output_dir}")
    print()

    async def _run():
        analyses: list[tuple[str, str]] = []
        for i, dash in enumerate(dashboards):
            if not isinstance(dash, dict):
                continue
            name = (dash.get("name") or "").strip()
            if not name:
                continue
            print(f"[{i + 1}/{len(dashboards)}] 生成分析: {name}")
            analysis = await generate_dashboard_analysis_async(name, output_dir, cfg)
            saved = _save_analysis_to_file(analysis_output_dir, name, analysis)
            print(f"      -> 保存到 {saved.name}")
            analyses.append((name, analysis))

        if dry_run:
            print("\n[dry-run] 跳过 Lark 仪表盘配置")
            return

        analyses_by_name = {n: t for n, t in analyses}
        done, failed = 0, 0
        for i, dash in enumerate(dashboards):
            if not isinstance(dash, dict):
                continue
            name = (dash.get("name") or "").strip()
            url = (dash.get("url") or "").strip()
            if not url or str(url).startswith("${"):
                print(f"[{i + 1}] 跳过 {name} (未配置 URL)")
                continue
            analysis = analyses_by_name.get(name, "")
            if not analysis:
                print(f"[{i + 1}] 跳过 {name} (无分析)")
                continue
            print(f"[{i + 1}] 配置 Lark 仪表盘: {name}...")
            r = await asyncio.to_thread(
                setup_lark_dashboard_automation_via_browser,
                dashboard_url=url,
                analysis_text=analysis,
                scheduled_time=scheduled_time,
                cdp_url=cdp_url,
            )
            if r.get("status") == "success":
                done += 1
                print(f"      -> 成功")
            else:
                failed += 1
                print(f"      -> 失败: {r.get('error', '')}")
        print(f"\n完成: 成功 {done} / 失败 {failed}")

    asyncio.run(_run())
    return 0


def main() -> int:
    if "--push-cards" in sys.argv:
        return _run_push_cards_only()
    scheduled_time = None
    dry_run = False
    for i, a in enumerate(sys.argv):
        if a == "--time" and i + 1 < len(sys.argv):
            scheduled_time = sys.argv[i + 1].strip()
        elif a == "--dry-run":
            dry_run = True
    return _run_dashboard_only(scheduled_time_override=scheduled_time, dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
