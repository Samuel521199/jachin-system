"""
BI 每日战报 — 主技能逻辑

流程: 收集(A) -> 对比提炼(B) -> LLM洞察(C) -> 分发(D)
设计规范: docs/bi_daily_report/03_SKILL_DESIGN.md
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

    # 配置路径优先级：项目 config/ > ~/.jachin/config/skills/...
    # 使用 get_app_root() 确保打包/不同运行目录下路径正确
    from l3_node.paths import get_app_root
    jachin_root = Path.home() / ".jachin"
    project_root = get_app_root()
    candidates = [
        project_root / "config" / "bi_daily_report.yaml",
        jachin_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
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
    }

    # Step A: 收集（可选）
    skip_collect = cfg.get("skip_collect", True)
    data_source = cfg.get("data_source") or {}
    raw_file_paths: list[str] = []

    if not skip_collect and data_source.get("url"):
        from l3_node.skills.mcp_registry import get_mcp_registry
        from l3_node.bi_paths import get_bi_raw_dir, ensure_bi_dirs
        from l3_node.bi_data_store import ingest_csv

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

    # Step B: 对比提炼（bi_metrics 引擎）
    try:
        from l3_node.bi_metrics.engine import run as run_bi_metrics
        import l3_node.bi_metrics.plugins  # noqa: F401

        bi_metrics_config = cfg.get("bi_metrics_config")
        config_path = bi_metrics_config if isinstance(bi_metrics_config, (str, Path)) else None
        if not config_path:
            from l3_node.paths import get_app_root
            config_path = get_app_root() / "config" / "bi_metrics.yaml"

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
    email_cfg = dist.get("email") or {}

    if lark_url and report_md:
        try:
            from l3_node.skills.mcp_registry import get_mcp_registry
            registry = get_mcp_registry()
            inp = json.dumps({
                "webhook_url": lark_url,
                "markdown_content": report_md,
                "title": f"每日 BI 深度分析战报 — {datetime.now().strftime('%Y-%m-%d')}",
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
            inp = json.dumps({
                "smtp_config": smtp_config,
                "to_addrs": to_addrs,
                "subject": f"每日 BI 深度分析战报 — {datetime.now().strftime('%Y-%m-%d')}",
                "body": body_html,
                "attachment_paths": raw_file_paths[:3],
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
