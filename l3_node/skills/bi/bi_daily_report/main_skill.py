"""
BI 每日战报 — 主技能逻辑（L3 Agent 的 MCP 编排层）

本 Skill 负责编排 BI 战报全流程，调度以下 MCP 与模块：
- mcp:atom_web_scraper — BI 页面抓取
- mcp:atom_lark_notifier — 飞书消息推送
- mcp:atom_email_sender — 邮件发送
- run_refiner + sync_refiner_to_lark — 数据提纯并同步到 Lark 多维表格（非 MCP，本地调用）

流程: 收集(A) -> 提纯(A.5) -> 对比提炼(B) -> LLM洞察(C) -> 分发(D)
设计规范: docs/bi_daily_report/03_SKILL_DESIGN.md

---

## L3 Agent MCP 调用指南

当 L3 Agent 需要执行 BI 战报或相关操作时，按以下契约调用：

### 1. 抓取 BI 数据 (Step A)
MCP: `mcp:atom_web_scraper`
输入 JSON:
```json
{
  "url": "BI 后台入口 URL",
  "output_path": "~/.jachin/client_volumes/bi_data/raw/{slug}.csv",
  "config": {
    "output_format": "csv",
    "timeout": 30,
    "cdp_url": "http://127.0.0.1:9222",
    "automation": {"start_url": "...", "actions": [...], "filters": {"date_range": ["YYYY-MM-DD", "YYYY-MM-DD"]}}
  }
}
```
输出: `{"status": "success", "file_path": "..."}` 或 `{"status": "error", "error": "..."}`

### 2. 数据提纯 (Step A.5，非 MCP)
直接调用 `l3_node.mcp_tools.bi.report_refiner.run_refiner()`，输出 11 个 CSV 到
`~/.jachin/client_volumes/bi_data/output/`。需先执行 `import_raw_to_duckdb` 将 raw CSV 导入 DuckDB。

### 3. Lark 多维表同步
由 `sync_refiner_to_lark(written_paths, lark_bitable_config)` 内部调用
`atom_lark_bitable_sync.sync_csv_to_bitable`，需配置 `bi_daily_report.yaml` 的 `lark_bitable.tables`。

### 4. 飞书战报推送 (Step D)
MCP: `mcp:atom_lark_notifier`
输入 JSON:
```json
{
  "webhook_url": "飞书 Webhook URL",
  "markdown_content": "战报 Markdown 内容",
  "title": "每日 BI 深度分析战报 — YYYY-MM-DD"
}
```

### 5. 邮件推送 (Step D)
MCP: `mcp:atom_email_sender`
输入 JSON:
```json
{
  "smtp_config": {"host", "port", "user", "password"},
  "to_addrs": ["email@example.com"],
  "subject": "每日 BI 深度分析战报 — YYYY-MM-DD",
  "body": "HTML 正文",
  "attachment_paths": ["path/to/01_xxx.csv", ...]
}
```

### 一键执行
调用 `run_bi_daily_report(config)` 将按配置自动执行上述步骤。

### Lark 同步失败排查（供 Agent 参考）
- `FieldNameNotFound`：CSV 列名与 Lark 表列名不一致，需调整 report_refiner 输出列
- `DatetimeFieldConvFail`：日期列需输出毫秒时间戳，非 "YYYY-MM-DD" 字符串
- `TextFieldConvFail`：纯数字值被误转为 float，文本列需保留字符串类型
- 同步错误会写入 `result["lark_bitable_sync_errors"]`，Agent 可根据错误类型决定重试或人工介入
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# LLM System Prompt（固定格式）
SYSTEM_PROMPT = """你是首席数据增长官。请基于数据给出极具商业价值的深度归因和战略建议，严禁废话。

输出格式必须包含以下三个部分（使用对应 emoji 标题）：
1. 📊昨日核心盘面
2. 🔍异动深度归因
3. 💡战略级行动建议

数据与上下文由调用方注入，你仅负责分析与建议，不要编造数据。"""


def _resolve_env(val: str) -> str:
    """解析 ${VAR} 占位符为环境变量值"""
    if not isinstance(val, str):
        return val
    m = re.match(r"^\$\{([^}]+)\}$", val.strip())
    if m:
        return os.environ.get(m.group(1), val)
    return val


def _resolve_config_values(cfg: dict[str, Any]) -> dict[str, Any]:
    """递归解析配置中的 ${VAR} 占位符"""
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            out[k] = _resolve_config_values(v)
        elif isinstance(v, list):
            out[k] = [_resolve_env(x) if isinstance(x, str) else x for x in v]
        elif isinstance(v, str):
            out[k] = _resolve_env(v)
        else:
            out[k] = v
    return out


def _load_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """加载 BI 战报配置。优先使用传入的 config，否则从文件加载"""
    if config and isinstance(config, dict):
        return _resolve_config_values(config)

    # 规范 075：优先 ~/.jachin/config/skills/，开发期回退项目 config/skills/
    from l3_node.paths import get_app_root
    jachin_root = Path.home() / ".jachin"
    project_root = get_app_root()
    candidates = [
        jachin_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
        project_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
    ]
    for path in candidates:
        if path.exists():
            try:
                import yaml
                with open(path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                return _resolve_config_values(raw)
            except Exception as e:
                logger.warning("[BI Daily Report] 配置加载失败 %s: %s", path, e)
    logger.warning("[BI Daily Report] 未找到配置文件，尝试路径: %s", candidates)
    return {}


def _markdown_to_html(md: str) -> str:
    """简单 Markdown 转 HTML（用于邮件正文）"""
    if not md:
        return ""
    html = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html)
    html = re.sub(r"\*(.+?)\*", r"<i>\1</i>", html)
    html = html.replace("\n", "<br>\n")
    return f"<pre style='white-space:pre-wrap;font-family:sans-serif'>{html}</pre>"


async def _run_bi_daily_report_async(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """异步主流程"""
    cfg = _load_config(config)
    result: dict[str, Any] = {
        "success": False,
        "stage": "init",
        "report_sent": False,
        "lark_ok": False,
        "email_ok": False,
        "error": "",
        "lark_bitable_sync_errors": [],  # Lark 多维表同步失败明细，供 agent 排查
    }

    # Step A: 收集（可选）
    # collect_mode: single=单表(atom_web_scraper) | full_spa=批量(spa_collector)
    skip_collect = cfg.get("skip_collect", True)
    collect_mode = (cfg.get("collect_mode") or "single").strip().lower()
    data_source = cfg.get("data_source") or {}
    raw_file_paths: list[str] = []

    if not skip_collect:
        if collect_mode == "full_spa":
            # 批量抓取：调用 spa_collector（需 Chrome 已登录）
            try:
                from l3_node.mcp_tools.bi.spa_collector import run_full_spa_collect
                from l3_node.mcp_tools.bi.paths import get_bi_raw_dir

                full_spa_cfg = cfg.get("full_spa") or {}
                base_url = full_spa_cfg.get("base_url") or data_source.get("url") or "https://bi-admin-web.heronpro.xin/#/layout/person"
                cdp_url = full_spa_cfg.get("cdp_url") or data_source.get("cdp_url") or "http://127.0.0.1:9222"
                slugs = full_spa_cfg.get("slugs") or []

                ok, fail, failed = await asyncio.to_thread(
                    run_full_spa_collect,
                    slugs=slugs if slugs else None,
                    base_url=base_url,
                    cdp_url=cdp_url,
                    use_discover=False,
                    auto_ingest=True,
                    raw_dir=get_bi_raw_dir(),
                )
                if fail > 0 and ok == 0:
                    result["stage"] = "collect"
                    result["error"] = f"full_spa 全部失败，failed={failed[:5]}"
                    return result
                raw_dir = get_bi_raw_dir()
                if slugs:
                    successful = [s for s in slugs if s not in failed]
                    raw_file_paths = [str(raw_dir / f"{s}.csv") for s in successful]
                else:
                    raw_file_paths = [str(p) for p in sorted(raw_dir.glob("*.csv"))]
            except Exception as e:
                logger.exception("[BI Daily Report] Step A full_spa 异常: %s", e)
                result["stage"] = "collect"
                result["error"] = str(e)
                return result
        elif data_source.get("url"):
            # 单表抓取：调用 atom_web_scraper
            from l3_node.skills.mcp_registry import get_mcp_registry
            from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs
            from l3_node.mcp_tools.bi.data_store import ingest_csv

            ensure_bi_dirs()
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            today = datetime.now().strftime("%Y-%m-%d")
            slug = data_source.get("slug", "daily_ops_summary")
            output_path = str(get_bi_raw_dir() / f"{slug}.csv")

            automation = dict(data_source.get("automation") or {})
            filters = automation.get("filters") or {}
            filters["date_range"] = [yesterday, today]
            automation["filters"] = filters

            scraper_config = {
                "output_format": data_source.get("output_format", "csv"),
                "timeout": int(data_source.get("timeout", 30)),
                "extract_rules": data_source.get("extract_rules"),
                "headers": data_source.get("headers"),
                "cdp_url": data_source.get("cdp_url", "http://127.0.0.1:9222"),
                "automation": automation,
            }

            inp = json.dumps({
                "url": data_source["url"],
                "output_path": output_path,
                "config": scraper_config,
            }, ensure_ascii=False)

            try:
                registry = get_mcp_registry()
                obs = await registry.invoke("mcp:atom_web_scraper", inp, timeout=90.0)
                scraped = json.loads(obs) if (obs or "").strip().startswith("{") else {}
                if scraped.get("status") != "success":
                    result["stage"] = "collect"
                    result["error"] = scraped.get("error", obs or "抓取失败")
                    return result

                fp = scraped.get("file_path", output_path)
                raw_file_paths.append(fp)
                ingest_r = ingest_csv(fp, slug)
                if ingest_r.get("status") != "success":
                    logger.warning("[BI Daily Report] ingest_csv 失败: %s", ingest_r.get("error"))
            except Exception as e:
                logger.exception("[BI Daily Report] Step A 异常: %s", e)
                result["stage"] = "collect"
                result["error"] = str(e)
                return result

    # Step A.5: 数据提纯（可选）— 输出 Lark 多维表格可导入的 CSV
    refiner_paths: list[str] = []
    if cfg.get("run_refiner", False):
        try:
            from l3_node.mcp_tools.bi.report_refiner import run_refiner, sync_refiner_to_lark
            from l3_node.mcp_tools.bi.paths import get_bi_output_dir

            storage = cfg.get("storage") or {}
            output_override = storage.get("refiner_output_path") or ""
            output_dir = get_bi_output_dir(output_override)

            date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            written, errs = run_refiner(date_str=date_str, output_dir=output_dir)
            refiner_paths = [str(p) for p in written]
            if errs:
                logger.warning("[BI Daily Report] Refiner 部分失败: %s", errs)

            # 同步到 Lark 多维表格（若配置了 lark_bitable.enabled）
            lark_bitable = cfg.get("lark_bitable") or {}
            if lark_bitable.get("enabled") and written:
                sync_ok, sync_errs = sync_refiner_to_lark(
                    [Path(p) for p in refiner_paths],
                    lark_bitable,
                )
                if sync_errs:
                    result["lark_bitable_sync_errors"] = sync_errs
                    logger.warning("[BI Daily Report] Lark 多维表同步部分失败: %s", sync_errs)
                elif sync_ok:
                    logger.info("[BI Daily Report] Lark 多维表已同步 %d 个表", sync_ok)
        except Exception as e:
            logger.warning("[BI Daily Report] Refiner 异常: %s", e)

    # Step B: 对比提炼（bi_metrics 引擎）
    try:
        from l3_node.mcp_tools.bi.metrics.engine import run as run_bi_metrics
        import l3_node.mcp_tools.bi.metrics.plugins  # noqa: F401

        bi_metrics_config = cfg.get("bi_metrics_config")
        config_path = bi_metrics_config if isinstance(bi_metrics_config, (str, Path)) else None
        if not config_path:
            from l3_node.paths import get_app_root
            project_root = get_app_root()
            jachin_root = Path.home() / ".jachin"
            for p in [
                jachin_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_metrics.yaml",
                project_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_metrics.yaml",
            ]:
                if p.exists():
                    config_path = p
                    break
            if not config_path:
                config_path = project_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_metrics.yaml"

        date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        metrics_data, _ = run_bi_metrics(
            date_str=date_str,
            show_compare=True,
            compare_period="day",
            output_format="console",
            config_path=config_path,
        )
        if metrics_data.get("_error"):
            result["stage"] = "compare"
            result["error"] = metrics_data["_error"]
            return result
    except Exception as e:
        logger.exception("[BI Daily Report] Step B 异常: %s", e)
        result["stage"] = "compare"
        result["error"] = str(e)
        return result

    # Step C: LLM 深度洞察
    now = datetime.now()
    external_context = {
        "当前日期": now.strftime("%Y-%m-%d"),
        "星期几": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()],
        "节假日标记": "",  # 可扩展
    }
    user_content = f"""## 数据指标（当日 vs 上日环比）

```json
{json.dumps(metrics_data, ensure_ascii=False, indent=2)}
```

## 外部上下文
{json.dumps(external_context, ensure_ascii=False)}

请基于以上数据输出战报，格式必须包含：📊昨日核心盘面、🔍异动深度归因、💡战略级行动建议。"""

    report_md = ""
    llm_cfg = cfg.get("llm") or {}
    model = llm_cfg.get("model", "dashscope/qwen3.5-flash-2026-02-23")

    try:
        from l3_node.llm_client import LiteLLMEngine, SecurityContext, _inject_env_keys_into_ctx
        ctx = SecurityContext()
        _inject_env_keys_into_ctx(ctx)
        engine = LiteLLMEngine(ctx, model_name=model, timeout=90.0)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        report_md = await engine.generate_response(messages, temperature=0.7, max_tokens=2048)
        if not (report_md or "").strip():
            report_md = "## 📊昨日核心盘面\n数据已收集，LLM 未返回内容。\n\n## 🔍异动深度归因\n待补充。\n\n## 💡战略级行动建议\n待补充。"
    except Exception as e:
        logger.exception("[BI Daily Report] Step C LLM 异常: %s", e)
        result["stage"] = "llm"
        result["error"] = str(e)
        report_md = f"## 📊昨日核心盘面\nLLM 调用失败: {e}\n\n## 🔍异动深度归因\n待补充。\n\n## 💡战略级行动建议\n待补充。"

    # Step D: 分发
    dist = cfg.get("distribution") or {}
    lark_url = (dist.get("lark_webhook_url") or "").strip() or (os.environ.get("BI_LARK_WEBHOOK_URL") or "").strip()
    lark_chat_id = (dist.get("lark_chat_id") or "").strip() or (os.environ.get("BI_LARK_CHAT_ID") or "").strip()
    # 有效 Webhook：非空且非占位符
    has_webhook = lark_url and not lark_url.startswith("${")
    has_chat_id = bool(lark_chat_id)
    email_cfg = dist.get("email") or {}

    if (has_webhook or has_chat_id) and report_md:
        try:
            from l3_node.skills.mcp_registry import get_mcp_registry
            registry = get_mcp_registry()
            inp = json.dumps({
                "webhook_url": lark_url or "",
                "markdown_content": report_md,
                "title": f"每日 BI 深度分析战报 — {datetime.now().strftime('%Y-%m-%d')}",
                "chat_id": lark_chat_id or "",
            }, ensure_ascii=False)
            obs = await registry.invoke("mcp:atom_lark_notifier", inp, timeout=15.0)
            lark_res = json.loads(obs) if (obs or "").strip().startswith("{") else {}
            result["lark_ok"] = lark_res.get("status") == "success"
            if not result["lark_ok"]:
                logger.warning("[BI Daily Report] 飞书推送失败: %s", lark_res.get("error", obs))
        except Exception as e:
            logger.warning("[BI Daily Report] 飞书推送异常: %s", e)

    smtp_host = email_cfg.get("smtp_host") or os.environ.get("BI_SMTP_HOST", "")
    to_addrs = email_cfg.get("to_addrs") or []
    if not to_addrs and os.environ.get("BI_EMAIL_TO"):
        to_addrs = [os.environ["BI_EMAIL_TO"]]
    if smtp_host and to_addrs and report_md:
        try:
            from l3_node.skills.mcp_registry import get_mcp_registry
            smtp_config = {
                "host": smtp_host,
                "port": email_cfg.get("smtp_port", 587),
                "user": email_cfg.get("smtp_user") or os.environ.get("BI_SMTP_USER", ""),
                "password": email_cfg.get("smtp_password") or os.environ.get("BI_SMTP_PASSWORD", ""),
            }
            body_html = _markdown_to_html(report_md)
            attachments = refiner_paths[:11] if refiner_paths else raw_file_paths[:3]
            inp = json.dumps({
                "smtp_config": smtp_config,
                "to_addrs": to_addrs,
                "subject": f"每日 BI 深度分析战报 — {datetime.now().strftime('%Y-%m-%d')}",
                "body": body_html,
                "attachment_paths": attachments,
            }, ensure_ascii=False)
            registry = get_mcp_registry()
            obs = await registry.invoke("mcp:atom_email_sender", inp, timeout=30.0)
            email_res = json.loads(obs) if (obs or "").strip().startswith("{") else {}
            result["email_ok"] = email_res.get("status") == "success"
            if not result["email_ok"]:
                logger.warning("[BI Daily Report] 邮件推送失败: %s", email_res.get("error", obs))
        except Exception as e:
            logger.warning("[BI Daily Report] 邮件推送异常: %s", e)

    result["success"] = True
    result["stage"] = "done"
    result["report_sent"] = result["lark_ok"] or result["email_ok"]
    result["refiner_csv_paths"] = refiner_paths
    return result


def run_bi_daily_report(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    BI 每日战报主入口（同步封装）。

    步骤:
      A. 调用 mcp:atom_web_scraper 抓取昨日数据（可选，skip_collect=true 时跳过）
      B. 读取 DuckDB，计算同环比 -> metrics_data（bi_metrics 引擎）
      C. 将 metrics_data 喂给 LLM，生成战报 markdown
      D. 调用 mcp:atom_lark_notifier、mcp:atom_email_sender 分发

    Args:
        config: BiReportConfig，若为空则从 config/bi_daily_report.yaml 加载

    Returns:
        {"success": bool, "stage": str, "report_sent": bool, "lark_ok": bool, "email_ok": bool, "error": str}
    """
    try:
        return asyncio.run(_run_bi_daily_report_async(config))
    except Exception as e:
        logger.exception("[BI Daily Report] run_bi_daily_report 异常: %s", e)
        return {
            "success": False,
            "stage": "error",
            "report_sent": False,
            "lark_ok": False,
            "email_ok": False,
            "error": str(e),
        }
