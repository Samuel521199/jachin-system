#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BI 分析（从多维表格同步后开始）— 中间阶段测试入口

跳过：数据抓取、提纯、Lark 多维表同步。
执行：仪表盘分析 → 推送 Lark 卡片（每个仪表盘一条）→ 战略分析 → 推送 Lark → 邮件发送（与 main_skill 顺序一致）。

前置：output 目录已有 CSV（可先运行完整 run_bi_analysis 一次，或手动将 CSV 放入 output）。

用法:
  交互模式（输入「BI分析」触发）:
    python scripts/run_bi_analysis_from_sync.py

  直接执行（免输入）:
    python scripts/run_bi_analysis_from_sync.py --run
    python scripts/run_bi_analysis_from_sync.py -y
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import sys
from datetime import datetime, timedelta
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


def _run_analysis_flow() -> int:
    """从多维表格同步后开始：仪表盘分析 + 推送卡片 + 战略分析 + 推送 Lark + 邮件"""
    from l3_node.primitives.skills.bi.bi_daily_report.main_skill import (
        _bi_merge_dotenv_for_skill,
        _bi_reconcile_llm_engine_ref_with_agent,
        _load_config,
        _DASHBOARD_DISPLAY_URLS,
    )
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_output_dir, ensure_bi_dirs

    cfg = _load_config(None)
    ensure_bi_dirs()
    # 与 run_bi_daily_report / main_skill 一致：合并项目根 .env 与 ~/.jachin/.env，并注入 engine_ref
    _bi_merge_dotenv_for_skill()
    _bi_reconcile_llm_engine_ref_with_agent()
    output_dir = get_bi_output_dir((cfg.get("storage") or {}).get("refiner_output_path") or "")
    output_paths = list(output_dir.glob("*.csv"))
    output_paths = [str(p) for p in output_paths]

    print("\n[BI分析-中间] 从多维表格同步后开始：仪表盘分析 → Lark 卡片 → 战略分析 → Lark → 邮件")
    print(f"[BI分析-中间] output 目录: {output_dir}")
    print(f"[BI分析-中间] 已有 CSV: {len(output_paths)} 个")
    if not output_paths:
        print("[BI分析-中间] 警告: output 无 CSV，战略/仪表盘分析可能无数据。请先运行完整流程或手动放入 CSV。")

    async def _run() -> dict:
        result = {
            "strategic_report": "",
            "strategic_report_sent": False,
            "dashboard_analyses": [],
            "dashboard_analysis_sent": False,
            "email_ok": False,
            "email_error": "",
        }
        dist = cfg.get("distribution") or {}
        strategic_cfg = cfg.get("strategic_report") or {}
        da_cfg = cfg.get("dashboard_automation") or {}

        # Step 4a: 仪表盘分析 + 推送 Lark 卡片（先于大战报，与 main_skill 一致）
        dashboard_analyses: list[tuple[str, str]] = []
        if da_cfg.get("enabled", False):
            dashboards = da_cfg.get("dashboards") or []
            analysis_output_subdir = str(da_cfg.get("analysis_output_subdir") or "统计分析").strip() or "统计分析"
            analysis_output_dir = output_dir / analysis_output_subdir
            analysis_output_dir.mkdir(parents=True, exist_ok=True)
            print("\n[Step 4a] 生成仪表盘分析...")
            for i, dash in enumerate(dashboards):
                if not isinstance(dash, dict):
                    continue
                name = (dash.get("name") or "").strip()
                if not name:
                    continue
                try:
                    from l3_node.primitives.skills.bi.bi_daily_report.dashboard_automation import (
                        generate_dashboard_analysis_async,
                        _save_analysis_to_file,
                    )
                    analysis = await generate_dashboard_analysis_async(name, output_dir, cfg)
                    _save_analysis_to_file(analysis_output_dir, name, analysis)
                    dashboard_analyses.append((name, analysis))
                    print(f"  [{i + 1}/{len(dashboards)}] {name} -> 已生成")
                except Exception as e:
                    print(f"  [{i + 1}] {name} -> 失败: {e}")

            if dashboard_analyses and da_cfg.get("push_dashboard_to_lark", True):
                _lark_webhook = (dist.get("lark_webhook_url") or "").strip()
                _lark_chat_id = (dist.get("lark_chat_id") or "").strip()
                if str(_lark_webhook).startswith("${"):
                    _lark_webhook = ""
                if str(_lark_chat_id).startswith("${"):
                    _lark_chat_id = ""
                if not _lark_chat_id and not _lark_webhook:
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
                if _lark_webhook or _lark_chat_id:
                    url_by_name = {str(d.get("name", "")).strip(): str(d.get("url", "")).strip() for d in dashboards if isinstance(d, dict)}
                    lark_cfg = cfg.get("lark_bitable") or {}
                    if lark_cfg.get("app_id") and lark_cfg.get("app_secret"):
                        os.environ.setdefault("LARK_APP_ID", str(lark_cfg.get("app_id", "")).strip())
                        os.environ.setdefault("LARK_APP_SECRET", str(lark_cfg.get("app_secret", "")).strip())
                    if lark_cfg.get("lark_use_feishu"):
                        os.environ["LARK_USE_FEISHU"] = "1"
                    from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown
                    sent_ok = 0
                    for _name, _text in dashboard_analyses:
                        _url = _DASHBOARD_DISPLAY_URLS.get(_name) or url_by_name.get(_name, "")
                        _card_md = f"{_text}\n\n---\n\n[打开仪表盘]({_url})" if _url and not _url.startswith("${") else _text
                        _r = send_lark_markdown(_lark_webhook or "", _card_md[:6000], title=f"📊 {_name}", chat_id=_lark_chat_id or None)
                        if _r.get("status") == "success":
                            sent_ok += 1
                            print(f"  -> 卡片已推送: {_name}")
                        else:
                            print(f"  -> 卡片失败 {_name}: {_r.get('error', '')}")
                    if sent_ok:
                        result["dashboard_analysis_sent"] = True
                    print(f"  -> 共推送 {sent_ok}/{len(dashboard_analyses)} 条")
        result["dashboard_analyses"] = dashboard_analyses

        _bi_reconcile_llm_engine_ref_with_agent()

        # Step 3.5: 战略分析 + 推送 Lark（与 main_skill 一致：注入 bi_project + T/T-1 摘要）
        if strategic_cfg.get("enabled", True):
            print("\n[Step 3.5] 生成战略深度分析...")
            try:
                from l3_node.paths import get_app_root
                from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_dir
                from l3_node.primitives.skills.bi.bi_daily_report.main_skill import _merge_strategic_report_config_for_llm
                from l3_node.primitives.skills.bi.bi_daily_report.strategic_report import generate_bi_strategic_report_async

                raw_dir_collect = get_bi_raw_dir()
                cfg_strategic = _merge_strategic_report_config_for_llm(
                    cfg,
                    project_root=get_app_root(),
                    output_dir=output_dir,
                    raw_dir=raw_dir_collect,
                )
                strategic_md = await generate_bi_strategic_report_async(
                    metrics=None, output_dir=output_dir, config=cfg_strategic
                )
                result["strategic_report"] = strategic_md or ""
                print(f"  -> 已生成 ({len(strategic_md or '')} 字符)")
                if strategic_cfg.get("push_to_lark", True) and strategic_md:
                    webhook = (dist.get("lark_webhook_url") or "").strip()
                    chat_id = (dist.get("lark_chat_id") or "").strip()
                    if str(webhook).startswith("${"):
                        webhook = ""
                    if str(chat_id).startswith("${"):
                        chat_id = ""
                    if not chat_id and not webhook:
                        chat_id = (os.environ.get("BI_LARK_CHAT_ID") or os.environ.get("LARK_CHAT_ID") or "").strip()
                    if not chat_id and not webhook:
                        try:
                            from l3_node.jachin_config import load_mcp_config
                            from l3_node.paths import get_app_root
                            mcp = load_mcp_config("atom_lark_notifier", project_root=get_app_root())
                            chat_id = (mcp.get("default_chat_id") or "").strip()
                            if str(chat_id).startswith("${"):
                                chat_id = ""
                        except Exception:
                            pass
                    if webhook or chat_id:
                        lark_cfg = cfg.get("lark_bitable") or {}
                        if lark_cfg.get("app_id") and lark_cfg.get("app_secret"):
                            os.environ.setdefault("LARK_APP_ID", str(lark_cfg.get("app_id", "")).strip())
                            os.environ.setdefault("LARK_APP_SECRET", str(lark_cfg.get("app_secret", "")).strip())
                        if lark_cfg.get("lark_use_feishu"):
                            os.environ["LARK_USE_FEISHU"] = "1"
                        from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown
                        r = send_lark_markdown(webhook or "", strategic_md, title="📊 BI 战略深度分析战报", chat_id=chat_id or None)
                        if r.get("status") == "success":
                            result["strategic_report_sent"] = True
                            print("  -> 已推送到 Lark")
                        else:
                            print(f"  -> Lark 推送失败: {r.get('error', '')}")
                    else:
                        print("  -> 未配置 chat_id/webhook，跳过推送")
            except Exception as e:
                print(f"  -> 异常: {e}")

        # Step 3.6: 邮件
        email_cfg = dist.get("email") or {}
        if email_cfg.get("enabled", True) and isinstance(email_cfg, dict):
            print("\n[Step 3.6] 发送邮件...")
            smtp_config = {
                "host": (email_cfg.get("smtp_host") or email_cfg.get("host") or "smtp.qq.com"),
                "port": int(email_cfg.get("smtp_port") or email_cfg.get("port") or 587),
                "user": (email_cfg.get("smtp_user") or email_cfg.get("user") or "").strip(),
                "password": (email_cfg.get("smtp_password") or email_cfg.get("password") or "").strip(),
            }
            to_addrs = email_cfg.get("to_addrs") or []
            if isinstance(to_addrs, list):
                to_addrs = [str(a).strip() for a in to_addrs if str(a).strip() and not str(a).strip().startswith("${")]
            expanded = []
            for a in to_addrs:
                if "," in a:
                    expanded.extend(x.strip() for x in a.split(",") if x.strip())
                else:
                    expanded.append(a)
            to_addrs = expanded
            if not to_addrs:
                to_addrs = (os.environ.get("BI_SMTP_TO") or os.environ.get("BI_EMAIL_TO") or "").strip().split(",")
                to_addrs = [a.strip() for a in to_addrs if a.strip()]
            if not smtp_config.get("user") or str(smtp_config.get("user", "")).startswith("${"):
                smtp_config["user"] = (os.environ.get("BI_SMTP_USER") or os.environ.get("SMTP_USER") or "").strip()
            if not smtp_config.get("password") or str(smtp_config.get("password", "")).startswith("${"):
                smtp_config["password"] = (os.environ.get("BI_SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "").strip()
            if not smtp_config.get("user") or not smtp_config.get("password") or not to_addrs:
                try:
                    from l3_node.jachin_config import load_mcp_config
                    from l3_node.paths import get_app_root
                    mcp_cfg = load_mcp_config("atom_email_sender", project_root=get_app_root())
                    mcp_smtp = mcp_cfg.get("smtp") or {}
                    if isinstance(mcp_smtp, dict) and (mcp_smtp.get("user") or "").strip() and (mcp_smtp.get("password") or "").strip():
                        smtp_config = {"host": (mcp_smtp.get("host") or "smtp.qq.com"), "port": int(mcp_smtp.get("port") or 587),
                                       "user": str(mcp_smtp.get("user") or "").strip(), "password": str(mcp_smtp.get("password") or "").strip()}
                    mcp_to = mcp_cfg.get("default_to_addrs") or []
                    if isinstance(mcp_to, list) and mcp_to:
                        to_addrs = [str(a).strip() for a in mcp_to if str(a).strip() and not str(a).strip().startswith("${")]
                except Exception:
                    pass
            if smtp_config.get("user") and smtp_config.get("password") and to_addrs:
                report_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                subject = f"📊 BI 战略深度分析战报 ({report_date})"
                strategic_md = result.get("strategic_report") or ""
                strategic_escaped = html.escape(strategic_md or "（无战略分析内容）")
                strategic_html = strategic_escaped.replace("\n", "<br/>")
                tables_list = ", ".join([Path(p).name for p in output_paths[:12]]) + ("..." if len(output_paths) > 12 else "") if output_paths else "（本流程从分析阶段开始）"
                lark_section = f'<h3>二、Lark 多维表</h3><p>output CSV 共 <b>{len(output_paths)}</b> 个：{html.escape(tables_list)}</p>'
                dashboards = da_cfg.get("dashboards") or []
                analyses_by_name = {n: t for n, t in dashboard_analyses}
                try:
                    from l3_node.primitives.skills.bi.bi_daily_report.dashboard_automation import _DASHBOARD_CHARTS
                except ImportError:
                    _DASHBOARD_CHARTS = {}
                dashboard_section_parts = []
                for i, dash in enumerate(dashboards):
                    if not isinstance(dash, dict):
                        continue
                    dname = (dash.get("name") or "").strip()
                    durl = _DASHBOARD_DISPLAY_URLS.get(dname) or (dash.get("url") or "").strip()
                    if not dname:
                        continue
                    chart_names = [c[0] for c in _DASHBOARD_CHARTS.get(dname, [])]
                    analysis_text = analyses_by_name.get(dname, "（无分析）")
                    chart_list = "、".join(chart_names[:8]) + ("…" if len(chart_names) > 8 else "") if chart_names else "—"
                    link_html = f'<a href="{html.escape(durl)}">打开仪表盘</a>' if durl and not durl.startswith("${") else ""
                    analysis_escaped = html.escape(analysis_text).replace("\n", "<br/>")
                    dashboard_section_parts.append(f'<div style="margin:12px 0; padding:12px; background:#f8f9fa; border-radius:8px; border-left:4px solid #1890ff;"><h4 style="margin:0 0 8px 0;">{i + 1}. {html.escape(dname)}</h4><p style="margin:4px 0; color:#666; font-size:13px;">📊 {html.escape(chart_list)} {link_html}</p><p style="margin:8px 0 0 0; white-space: pre-wrap;">{analysis_escaped}</p></div>')
                dashboard_section = (
                    f'<h3>一、仪表盘统计图与分析</h3><p>与流程顺序一致：先于战略大战报。</p>{"".join(dashboard_section_parts)}'
                    if dashboard_section_parts
                    else ""
                )
                body = f"""<html><body style="font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; font-size:14px; line-height:1.6; color:#333;">
<div style="background:#e6f7ff; padding:12px 16px; border-radius:8px; margin-bottom:16px; border-left:4px solid #1890ff;">
<p style="margin:0 0 8px 0; font-weight:600;">本邮件由 jachin 系统自动发送</p>
<p style="margin:0; color:#c41d7f; font-size:13px;">⚠ 注意将此账号放入白名单，以防被当垃圾邮件误删！</p>
</div>
<h2 style="color:#1890ff;">📊 BI 每日战报 ({report_date})</h2>
{dashboard_section}
{lark_section}
<h3>三、战略深度分析</h3>
<div style="background:#f5f5f5; padding:16px; border-radius:8px; white-space: pre-wrap;">{strategic_html}</div>
<hr style="margin:20px 0; border:none; border-top:1px solid #eee;"/>
<p style="color:#999; font-size:12px;">— Jachin OS BI 战报系统 · 分析阶段</p>
</body></html>"""
                action_input = json.dumps({"smtp_config": smtp_config, "to_addrs": to_addrs, "subject": subject, "body": body, "attachment_paths": []}, ensure_ascii=False)
                from l3_node.primitives.mcp.registry import get_mcp_registry
                r_str = await get_mcp_registry().invoke("mcp:atom_email_sender", action_input, timeout=60.0)
                try:
                    r = json.loads(r_str) if isinstance(r_str, str) and r_str.strip().startswith("{") else {}
                except Exception:
                    r = {"status": "error", "error": str(r_str)}
                if r.get("status") == "success":
                    result["email_ok"] = True
                    print(f"  -> 已发送，收件人 {len(to_addrs)} 人")
                else:
                    result["email_error"] = r.get("error", "未知错误")
                    print(f"  -> 失败: {result['email_error']}")
            else:
                print("  -> 未配置 SMTP/to_addrs，跳过")

        return result

    # run_until_complete is deprecated when called from async context - use asyncio.run for the inner
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        res = loop.run_until_complete(_run())
    finally:
        loop.close()

    print("\n[BI分析-中间] ✅ 完成")
    if res.get("strategic_report_sent"):
        print("  - 战略分析已推送到 Lark")
    if res.get("dashboard_analysis_sent"):
        print("  - 仪表盘卡片已推送到 Lark")
    if res.get("email_ok"):
        print("  - 邮件已发送")
    return 0


def main() -> int:
    if "--run" in sys.argv or "-y" in sys.argv or "--yes" in sys.argv:
        return _run_analysis_flow()

    print("=" * 50)
    print("BI 分析（从多维表格同步后开始）")
    print("输入「BI分析」开始执行，输入「quit」或「q」退出")
    print("=" * 50)
    try:
        while True:
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见")
                break
            if not line:
                continue
            if line.lower() in ("quit", "q", "exit"):
                print("再见")
                break
            if "BI分析" in line or "bi分析" in line or line.strip().lower() == "bi分析":
                _run_analysis_flow()
                print()
                continue
            print("提示: 输入「BI分析」开始分析")
    except Exception as e:
        print(f"异常: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
