#!/usr/bin/env python3
"""
Kalaroko Monitor MCP — 默认四场景（KALAROKO_DEFAULT_SCENARIOS）全流程联调。

串联：
  1) execute_playwright_perf_test（scenarios=[] → 内置首页 + 3 款游戏）
  2) fetch_api_health（探测 gwp.heronpro.xin 大厅后端 API 列表 + summary）
  3) manage_perf_history（持久 JSONL：append + query_recent，路径 ``~/.jachin/data/kalaroko_e2e.jsonl``）

巡检时间展示时区由 ``KALAROKO_REPORT_TZ`` 控制（默认 ``utc8``=北京时间 UTC+8；``malaysia``=马来西亚时间 UTC+8）。
单轮 Markdown 报告（``render_report_md``）为 **飞书看板卡片式**：分隔线 + 树状符号（├└）+ 全量秒数加粗；无表格、正文不展示 run_id/game_id。
多轮时追加「📈 多轮趋势对比」；异常区为单行简报 + jsonl 提示。**落库 jsonl 仍写入全量** ``browser_exceptions``（见 ``_run_history``）。

前置（仓库根）：
  pip install -r requirements_kalaroko.txt
  playwright install chromium
  （脚本会 ``load_dotenv`` 合并仓库根 ``.env``）

Playwright 巡检 **仅连接 CDP**：请在运行前启动带 ``--remote-debugging-port`` 的 Chrome（如 ``scripts/launch_chrome_debug.ps1``），
并在 ``.env`` 中设置 ``KALAROKO_CDP_ENDPOINT=http://127.0.0.1:9222``（与端口一致）。MCP **不会**每次新起浏览器进程。

用法：
  python scripts/test_kalaroko_default_scenarios_e2e.py
  python scripts/test_kalaroko_default_scenarios_e2e.py --runs 3 --interval 60   # 共 3 轮（默认 4 轮），轮间隔 60 秒
  python scripts/test_kalaroko_default_scenarios_e2e.py --skip-playwright   # 仅测 HTTP + 历史，跳过浏览器（更快）
  python scripts/test_qwen_llm_probe.py   # 仅探测 LLM_COMPLEX_MODEL（如 qwen3-max）DashScope 调用是否可用（与 E2E 同源）

脚本默认 ``KALAROKO_HEADLESS=false``（对 CDP 仅作标注）；是否显示窗口由已打开的 Chrome 决定。

阶段性进度输出在 **stderr**（前缀 ``[E2E progress]``），与 **stdout** 中 JSON/报告分离，不改变断言与退出码。

多轮结束后的大模型综合分析：模型名读取 ``LLM_COMPLEX_MODEL``（如 ``qwen3-max``）；**API 与 endpoint 与 L3 一致**，经 ``core.brain.llm.dashscope_regional`` 选择：``JACHIN_ACTIVE_REGION=SEA`` 时用国际域 ``dashscope-intl.aliyuncs.com`` 及 ``DASHSCOPE_API_KEY_SEA``（或通用 ``DASHSCOPE_API_KEY``）。

退出码：0 成功；非 0 失败。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time

import httpx
import sys
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.kalaroko_e2e_jsonl_store import (  # noqa: E402
    KALAROKO_E2E_JSONL_PATH,
    kalaroko_e2e_jsonl_lock,
)

# 与 run_bi_* 等脚本一致：合并仓库根 .env，否则 CHROME_* / KALAROKO_* 写在 .env 里也不会进 os.environ
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", encoding="utf-8")
except ImportError:
    pass
except OSError:
    pass

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault(
    "KALAROKO_MONITOR_ALLOWED_HOSTS",
    "kalaroko.com,www.kalaroko.com,gweb.kalaroko.com,gwp.heronpro.xin",
)

# 持久化 E2E 落库（供 7x24 调度、晨报与历史 query_recent）
DATA_DIR = Path.home() / ".jachin" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
KALAROKO_E2E_JSONL = KALAROKO_E2E_JSONL_PATH

# 串行化 Playwright/落库，避免与定时任务交错写 JSONL
_E2E_SERIAL_LOCK = asyncio.Lock()

# 停止标志在 `l3_node.kalaroko_e2e_control`（与 HTTP / importlib 加载路径解耦）
from l3_node.kalaroko_e2e_control import (  # noqa: E402
    is_manual_run_cancel_requested,
    reset_manual_run_flag,
    stop_manual_run,
)
# 联调默认有头，便于观察点击流与登录；CI/无人值守可显式设置 KALAROKO_HEADLESS=true
os.environ.setdefault("KALAROKO_HEADLESS", "false")

# 与 Word 模板展示名对齐；业务 game_id 以 MCP 返回的 document_game_id / url_game_id 为准
_GAME_LABEL: dict[str, str] = {
    "tongits_king": "Tongits King",
    "royal_pusoy": "Royal Pusoy",
    "color_blitz": "Color Blitz Social",
}


def _game_heading(g: dict) -> str:
    """小节标题：对齐「游戏名（game_id=n）」；文档 ID 与 gweb URL 不一致时双写。"""
    key = str(g.get("game_id") or "")
    label = _GAME_LABEL.get(key, key)
    doc_id = g.get("document_game_id")
    url_id = g.get("url_game_id")
    if doc_id is not None and url_id is not None and int(doc_id) != int(url_id):
        return (
            f"### {label}（文档 game_id={doc_id}，当前巡检 URL 中 gweb 参数 game_id={url_id}）"
        )
    if doc_id is not None:
        return f"### {label}（game_id={doc_id}）"
    if url_id is not None:
        return f"### {label}（URL game_id={url_id}）"
    return f"### {label}"

_DISCLAIMER_GAME_FRAME = (
    "注意：本次测量为 game-frame 页面及导航时序；未单独断言游戏引擎内部渲染完毕。"
    "若需「进入牌桌」端到端（含 WebSocket、匹配、引擎初始化），数值与仅 frame 加载会存在差异，属正常现象。"
)

# 报告第六节 / 附录中展示的浏览器异常条数上限（正文仅 1～2 条精简摘要）
REPORT_BROWSER_EXCEPTION_SAMPLE_MAX = 2
_NET_ERR_RE = re.compile(r"net::[A-Z0-9_]+")


def compact_browser_exception(ex: dict) -> dict:
    """
    将单条 browser_exception 压成三字段：type / net（如有）/ target（主机+路径或短文案）。
    用于 Markdown 与附录 JSON，避免长 URL 与 query 刷屏。
    """
    msg = str(ex.get("message") or "")
    mnet = _NET_ERR_RE.search(msg)
    net_err = mnet.group(0) if mnet else ""
    url = _exception_source_url(ex)
    target = ""
    if url:
        try:
            pr = urlparse(url)
            host = pr.hostname or ""
            path = pr.path or ""
            q = (pr.query or "")[:72]
            base = f"{host}{path}"
            if q:
                base = f"{base}?{q}"
            target = base[:200]
        except Exception:
            target = url[:180]
    if not target:
        target = msg.replace("\n", " ").strip()[:120]
    out: dict[str, str] = {
        "type": str(ex.get("type") or ""),
        "target": target or "(unknown)",
    }
    if net_err:
        out["net"] = net_err
    return out

# 低价值域名（埋点/统计）；抽样时排在后面，名额不足时可忽略
_EXCEPTION_NOISE_HOST_FRAGMENTS: tuple[str, ...] = (
    "google-analytics.com",
    "www.google-analytics.com",
    "googletagmanager.com",
    "googleadservices.com",
    "doubleclick.net",
    "facebook.com",
    "facebook.net",
    "connect.facebook.net",
)


def _exception_source_url(ex: dict) -> str:
    """从 source 或 message 中尽量解析出请求 URL。"""
    s = ex.get("source")
    if isinstance(s, str) and s.strip().lower().startswith("http"):
        return s.strip()
    m = str(ex.get("message") or "")
    for tok in m.split():
        if tok.startswith("http://") or tok.startswith("https://"):
            return tok.split("?")[0][:900]
    return ""


def _exception_priority(ex: dict) -> tuple[int, str]:
    """
    排序键：(优先级升序 = 越靠前越重要, 去重键)。
    requestfailed 中 SPA 切换或战术撤离会导致大量 ERR_ABORTED（含 GA），业务域名优先展示。
    """
    typ = str(ex.get("type") or "")
    url = _exception_source_url(ex)
    host = ""
    path = ""
    try:
        pr = urlparse(url)
        host = (pr.hostname or "").lower()
        path = pr.path or ""
    except Exception:
        pass

    dedup = f"{typ}|{host}|{path[:80]}"

    if typ == "pageerror":
        return (0, dedup)
    if typ == "error":
        return (2, dedup)
    if typ != "requestfailed":
        return (4, dedup)

    if any(x in host for x in _EXCEPTION_NOISE_HOST_FRAGMENTS):
        return (90, dedup)
    if "/g/collect" in url or "google-analytics" in host:
        return (90, dedup)
    if "facebook.com" in host:
        return (88, dedup)
    # 埋点 batch（仍有业务含义但噪声大，次于业务静态资源）
    if "gwbi.heronpro.xin" in host and "/bi/" in path:
        return (70, dedup)
    if "events" in path and "aws" in host:
        return (85, dedup)

    if "kalaroko.com" in host or host.endswith("kalaroko.com"):
        return (15, dedup)
    if "gweb.kalaroko.com" in host:
        return (14, dedup)
    if "gwp.heronpro.xin" in host or "app-oss.heronpro.xin" in host:
        return (18, dedup)
    if "heronpro.xin" in host:
        return (22, dedup)
    if "aliyuncs.com" in host:
        return (25, dedup)

    return (40, dedup)


def select_key_browser_exceptions(
    bex: list,
    max_items: int = REPORT_BROWSER_EXCEPTION_SAMPLE_MAX,
) -> list[dict]:
    """从全量 browser_exceptions 中抽取少量关键项（去重 + 优先级）。"""
    if not bex or max_items <= 0:
        return []
    ranked = sorted(bex, key=lambda x: _exception_priority(x))
    out: list[dict] = []
    seen: set[str] = set()
    for ex in ranked:
        pri = _exception_priority(ex)
        key = pri[1]
        if key in seen:
            continue
        seen.add(key)
        out.append(ex)
        if len(out) >= max_items:
            break
    return out


# L3 HTTP SSE / 控制台：可为每行文本注册回调（与 stderr 并行）
_e2e_line_sink: Callable[[str], None] | None = None


def _e2e_echo(line: str, *, file=sys.stdout) -> None:
    """stdout/stderr 一行；若注册了 line_sink，同步推送（供 SSE）。"""
    print(line, file=file, flush=True)
    sink = _e2e_line_sink
    if sink:
        try:
            sink(line)
        except Exception:
            pass


def _e2e_progress(msg: str) -> None:
    """写入 stderr，与 stdout 中 JSON/报告分离，不影响断言与退出码。"""
    line = f"[E2E progress] {msg}"
    print(line, file=sys.stderr, flush=True)
    sink = _e2e_line_sink
    if sink:
        try:
            sink(line)
        except Exception:
            pass


def _j(obj: dict, limit: int | None = None) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    if limit is not None and len(s) > limit:
        return s[:limit] + f"\n... [截断，总长 {len(s)} 字符]"
    return s


def _fmt_v(v) -> str:
    if v is None:
        return "null"
    return str(v)


def _fmt_ms_business(val) -> str:
    """毫秒业务展示：千分位 + ms；无效或 None 为 N/A。"""
    if val is None:
        return "N/A"
    try:
        n = int(round(float(val)))
        return f"{n:,}ms"
    except (TypeError, ValueError):
        return "N/A"


def _conclusion_homepage(h: dict) -> str:
    st = h.get("load_status")
    if st == "success":
        return "结论：✅ 首页加载正常。"
    if st == "partial":
        return "结论：⚠️ 首页部分成功，请结合 web_vitals 与异常列表。"
    if st in ("failed", "timeout"):
        return f"结论：❌ 首页异常（load_status={st}）。"
    return f"结论：load_status={_fmt_v(st)}。"


def _conclusion_api(items: list) -> str:
    if not items:
        return "结论：未配置或未探测到 API 端点。"
    bad = [x for x in items if not x.get("healthy")]
    if not bad:
        return "结论：✅ 所有 API 健康（已探测端点均返回 healthy）。"
    return f"结论：⚠️ 存在不健康端点 {len(bad)} 个，见下表。"


def _api_summary_status_codes_cell(summary: dict) -> str:
    """summary.status_codes：若全部为 HTTP 200 则展示「全部 200」，否则逗号分隔。"""
    if not isinstance(summary, dict):
        return "null"
    sc = summary.get("status_codes") or []
    if not sc:
        return "null"
    non_null = [c for c in sc if c is not None]
    if non_null and set(non_null) == {200}:
        return "全部 200"
    return ", ".join("null" if c is None else str(c) for c in sc)


_CMP_LLM_META_KEYS = frozenset({"inspection_time", "captured_at"})


def _extract_comparison_metrics(pw_data: dict) -> dict:
    """提取当前轮次的核心加载指标，用于多轮对比。"""
    m: dict[str, Any] = {}
    hp = pw_data.get("homepage") or {}
    hp_metrics = hp.get("metrics") or {}
    m["page_ttfb"] = hp_metrics.get("ttfb_ms")
    m["page_load"] = hp_metrics.get("page_load_ms")
    m["page_success"] = hp.get("load_status") == "success"

    cat = pw_data.get("captured_at")
    m["captured_at"] = cat
    if cat:
        m["inspection_time"] = _format_inspection_timestamp(pw_data, None)
    else:
        m["inspection_time"] = "N/A（无 captured_at）"

    for g in pw_data.get("games") or []:
        gid = str(g.get("game_id") or "")
        if not gid:
            continue
        m[f"{gid}_ttfb"] = g.get("shell_navigation_ttfb_ms")
        m[f"{gid}_load"] = g.get("real_engine_load_ms")
        m[f"{gid}_success"] = g.get("load_status") == "success"
    return m


def _fmt_cmp_ms(val: Any) -> str:
    """对比表毫秒展示：整数千分位 + ms；无效为 N/A。"""
    if val is None:
        return "N/A"
    try:
        return f"{int(round(float(val))):,}ms"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_ms_as_s(val: Any) -> str:
    """毫秒 → 秒，保留两位小数 + s；无效为 N/A。"""
    if val is None:
        return "N/A"
    try:
        return f"{float(val) / 1000.0:.2f}s"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_cmp_s(val: Any) -> str:
    """对比表：毫秒值格式化为秒（两位小数）。"""
    if val is None:
        return "N/A"
    try:
        return f"{float(val) / 1000.0:.2f}s"
    except (TypeError, ValueError):
        return "N/A"


def _load_status_zh(st: Any) -> str:
    s = str(st or "").strip().lower()
    if s == "success":
        return "成功"
    if s == "partial":
        return "部分成功"
    if s in ("failed", "failure"):
        return "失败"
    if s == "timeout":
        return "超时"
    if s == "skipped":
        return "跳过"
    if not s or s in ("none", "null"):
        return "未知"
    return str(st)


def _summary_remark_homepage(hp: dict) -> str:
    st = hp.get("load_status")
    if st == "success":
        return ""
    if st == "partial":
        return "首页未完全就绪，详见第二节"
    if st in ("failed", "timeout"):
        return f"首页加载异常（{st}）"
    if st == "skipped":
        return "已跳过浏览器巡检"
    return f"load_status={st}" if st else ""


def _kalaroko_report_tzinfo() -> tuple[Any, str]:
    """
    巡检报告「巡检时间」展示用 IANA 时区 + 文案。

    环境变量 ``KALAROKO_REPORT_TZ``（不区分大小写）：
    - ``utc8`` / ``china`` / ``cn`` / ``asia/shanghai``（默认）：北京时间，UTC+8
    - ``malaysia`` / ``my`` / ``asia/kuala_lumpur``：马来西亚时间，同为 UTC+8（标注不同）
    """
    raw = (os.environ.get("KALAROKO_REPORT_TZ") or "utc8").strip().lower()
    malaysia_keys = frozenset(
        {
            "malaysia",
            "my",
            "asia/kuala_lumpur",
            "kuala_lumpur",
        }
    )
    try:
        from zoneinfo import ZoneInfo

        if raw in malaysia_keys:
            return ZoneInfo("Asia/Kuala_Lumpur"), "马来西亚时间 (UTC+8)"
        return ZoneInfo("Asia/Shanghai"), "北京时间 (UTC+8)"
    except Exception:
        tz8 = timezone(timedelta(hours=8))
        if raw in malaysia_keys:
            return tz8, "马来西亚时间 (UTC+8)"
        return tz8, "北京时间 (UTC+8)"


def _format_inspection_timestamp(pw: dict, record: dict | None) -> str:
    """将 ``captured_at``（通常 UTC）换算为配置时区并带时区说明。"""
    tz, tz_label = _kalaroko_report_tzinfo()
    raw = pw.get("captured_at") or ((record or {}).get("captured_at"))
    if not raw:
        dt = datetime.now(timezone.utc).astimezone(tz)
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')} ({tz_label})"
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(tz)
        return f"{local.strftime('%Y-%m-%d %H:%M:%S')} ({tz_label})"
    except ValueError:
        head = s[:19] if len(s) >= 19 else s
        return f"{head} ({tz_label})"


def _e2e_summary_model() -> str:
    """与仓库根 `.env` 中 `LLM_COMPLEX_MODEL` 对齐（复杂任务 = qwen3-max），未设时默认 qwen3-max。"""
    s = (os.environ.get("LLM_COMPLEX_MODEL") or "").strip()
    return s or "qwen3-max"


def _mask_api_key_preview(key: str) -> str:
    """日志脱敏：仅保留前缀与末 4 位。"""
    k = (key or "").strip()
    if not k:
        return "(空)"
    if len(k) <= 12:
        return "***"
    return f"{k[:7]}…{k[-4:]}"


def _e2e_dashscope_chat_url_and_key() -> tuple[str, str | None, str]:
    """
    Chat Completions 完整 URL + API Key，与 ``core.brain.llm.dashscope_regional`` 一致：
    ``JACHIN_ACTIVE_REGION=SEA`` 时使用 ``dashscope-intl.aliyuncs.com`` 与
    ``DASHSCOPE_API_KEY_SEA``（或回退）；国服为 ``dashscope.aliyuncs.com``。
    """
    try:
        from core.brain.llm.dashscope_regional import (
            get_dashscope_regional_credentials,
            get_jachin_active_region,
        )

        api_key, api_base = get_dashscope_regional_credentials()
        region = get_jachin_active_region()
        base = (api_base or "").strip()
        if not base:
            base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        url = base.rstrip("/") + "/chat/completions"
        key = (api_key or "").strip() or None
        return url, key, region
    except Exception as e:
        print(
            f"[LLM] dashscope_regional 导入/解析失败，回退直连国服: {e!r}",
            flush=True,
        )
        fallback = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        k = (
            (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY") or "")
            .strip()
            or None
        )
        return fallback, k, "?"


def _parse_captured_at_iso(ts: Any) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def compact_record_for_daily_llm(record: dict) -> dict[str, Any]:
    """从完整 jsonl 记录压缩为晨报 LLM 输入（homepage + games 核心字段）。"""
    hp = record.get("homepage") or {}
    hm = hp.get("metrics") or {}
    games_out: list[dict[str, Any]] = []
    for g in record.get("games") or []:
        games_out.append(
            {
                "game_id": g.get("game_id"),
                "load_status": g.get("load_status"),
                "shell_navigation_ttfb_ms": g.get("shell_navigation_ttfb_ms"),
                "real_engine_load_ms": g.get("real_engine_load_ms"),
            }
        )
    cat = record.get("captured_at")
    inspection_time = (
        _format_inspection_timestamp(record, None)
        if cat
        else "N/A（无 captured_at）"
    )
    return {
        "captured_at": cat,
        "inspection_time": inspection_time,
        "run_id": record.get("run_id"),
        "homepage_url": hp.get("url"),
        "homepage_load_status": hp.get("load_status"),
        "page_ttfb_ms": hm.get("ttfb_ms"),
        "page_load_ms": hm.get("page_load_ms"),
        "games": games_out,
    }


def _round_e2e_structural_green(pw: dict, fh: dict) -> bool:
    """单轮结构是否全绿：Playwright 成功、首页与各游戏 success、API 均 healthy。"""
    if pw.get("ok") is not True:
        return False
    hp = pw.get("homepage") or {}
    if hp.get("load_status") != "success":
        return False
    for g in pw.get("games") or []:
        if g.get("load_status") != "success":
            return False
    for it in fh.get("items") or []:
        if not it.get("healthy"):
            return False
    return True


def _llm_analysis_indicates_issue_or_special(text: str) -> bool:
    """
    多轮综合分析是否值得入库：配置/调用失败、显式告警符号、或明显负面语义。
    全绿平稳结论（无下列触发词）返回 False，避免向量库被小时巡检刷屏。
    """
    s = (text or "").strip()
    if not s:
        return False
    if "未配置" in s and "Key" in s:
        return True
    if "跳过" in s and ("大模型" in s or "DashScope" in s):
        return True
    if "❌" in s or "⚠️" in s:
        return True
    keywords = (
        "失败",
        "异常",
        "不健康",
        "超时",
        "报错",
        "部分成功",
        "退化",
        "飙升",
        "不可用",
        "风险",
        "故障",
        "急剧",
        "恶化",
        "骤降",
        "骤升",
        "剧烈波动",
        "明显波动",
    )
    if any(k in s for k in keywords):
        return True
    low = s.lower()
    if "unhealthy" in low or "timeout" in low or "degraded" in low:
        return True
    return False


def _should_commit_kalaroko_e2e_memory_nexus(
    *,
    skip_playwright: bool,
    had_round_exception: bool,
    structural_all_ok: bool,
    llm_analysis: str | None,
) -> bool:
    """仅异常/退化/特殊 LLM 结论时入库；全绿且摘要平稳则跳过。"""
    if skip_playwright:
        return False
    if had_round_exception or not structural_all_ok:
        return True
    return _llm_analysis_indicates_issue_or_special(llm_analysis or "")


def jsonl_records_last_hours(
    jsonl_path: str | Path,
    *,
    hours: float = 24.0,
    max_records: int = 400,
) -> list[dict]:
    """读取 JSONL 中 captured_at 落在过去 ``hours`` 小时内的记录（UTC 语义）。"""
    p = Path(jsonl_path)
    if not p.is_file():
        return []
    try:
        with kalaroko_e2e_jsonl_lock(p):
            raw = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_captured_at_iso(rec.get("captured_at"))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff or ts > now + timedelta(minutes=10):
            continue
        rows.append(rec)
    if len(rows) > max_records:
        step = len(rows) / max_records
        idxs = [min(len(rows) - 1, int(i * step)) for i in range(max_records)]
        rows = [rows[i] for i in idxs]
    return rows


async def generate_llm_daily_report_from_jsonl(
    jsonl_path: str | Path | None = None,
    *,
    hours: float = 24.0,
) -> str:
    """晨报：聚合过去 24h jsonl → 调用 LLM（mode=daily_24h）。"""
    path = Path(jsonl_path or KALAROKO_E2E_JSONL)
    records = jsonl_records_last_hours(path, hours=hours)
    compact: list[dict[str, Any]] = []
    for i, r in enumerate(records):
        row = compact_record_for_daily_llm(r)
        row["sequence_in_24h"] = i + 1
        compact.append(row)
    if not compact:
        return (
            "> ⚠️ 过去 24 小时内持久化库中无采样记录（或 captured_at 不可解析），"
            "跳过晨报正文生成。"
        )
    return await _generate_llm_summary(compact, mode="daily_24h")


async def _generate_llm_summary(
    payload: list[dict],
    *,
    mode: Literal["multi_round", "daily_24h"] = "multi_round",
) -> str:
    """调用大模型：``multi_round`` 为多轮对比指标；``daily_24h`` 为 24 小时 jsonl 采样晨报。"""
    print(f"[LLM] _generate_llm_summary: 开始 mode={mode!r}", flush=True)

    url, api_key, active_region = _e2e_dashscope_chat_url_and_key()
    print(
        f"[LLM] JACHIN_ACTIVE_REGION≈{active_region!r}（与 core 区域化逻辑一致）",
        flush=True,
    )

    if not api_key:
        print(
            "[LLM] 跳过: 无可用 DashScope Key（CN 请配置 DASHSCOPE_API_KEY_CN 或通用 DASHSCOPE_API_KEY；"
            " SEA 请配置 DASHSCOPE_API_KEY_SEA 或通用 Key；并确认 JACHIN_ACTIVE_REGION）",
            flush=True,
        )
        return (
            "> ⚠️ 未配置可用的 DashScope Key：当前区域需 "
            "`DASHSCOPE_API_KEY_CN` / `DASHSCOPE_API_KEY_SEA`（推荐）或通用 "
            "`DASHSCOPE_API_KEY` / `QWEN_API_KEY`，并已设置正确的 `JACHIN_ACTIVE_REGION`。"
            " 已跳过大模型综合分析。"
        )

    model = _e2e_summary_model()
    print(f"[LLM] 解析模型: {model!r}（来自 LLM_COMPLEX_MODEL，默认 qwen3-max）", flush=True)
    print(f"[LLM] api_key 预览: {_mask_api_key_preview(api_key)}", flush=True)

    if mode == "daily_24h":
        n_samples = len(payload)
        payload_json = json.dumps(payload, ensure_ascii=False)
        if len(payload_json) > 280_000:
            payload_json = payload_json[:280_000] + "\n…(截断，仅保留前 280KB 字符)"
        prompt = f"""
你是资深 QA / SRE。以下为过去约 24 小时内 Kalaroko E2E 自动化巡检写入持久化库的**采样记录**（JSON 数组）。
每条包含 sequence_in_24h（按时间排序后的序号）、captured_at、inspection_time（已按巡检报告时区换算）、首页与各游戏的核心加载指标（TTFB、完全加载耗时、成功状态）。
数据可能较多：请先归纳**整体稳定性与趋势**，再指出**异常尖峰**（具体时间窗、游戏、毫秒级数值），最后给执行层可执行的 2～4 条建议。

记录条数（采样后）: {n_samples}

JSON 数据:
{payload_json}

任务要求：
1. 用一段话描述 24h 内的健康度与波动（高峰/低谷若可辨识）。
2. 点出最值得关注的异常或退化（若有），含数值与时间点/游戏。
3. **按序号或「第 N 条/轮」指称某次巡检时，必须写出该条 JSON 中的 inspection_time（与 sequence_in_24h 对应，原文照抄，禁止编造）**；列举多轮失败/峰值时建议用「第 10 条（inspection_time 原文）」形式，便于对照 jsonl。
4. 语言精炼、专业，直接输出晨报结论，不要用 Markdown 代码块，不要寒暄。
"""
        sys_msg = "你是专业的 QA/SRE 数据分析师，擅长长时序性能晨报。"
        log_n = n_samples
    else:
        compact_history: list[dict[str, Any]] = []
        for i, m in enumerate(payload):
            compact_history.append(
                {
                    "round": i + 1,
                    "inspection_time": m.get("inspection_time"),
                    "captured_at": m.get("captured_at"),
                    "page_ttfb_ms": m.get("page_ttfb"),
                    "page_load_ms": m.get("page_load"),
                    "games_metrics": {
                        k: v
                        for k, v in m.items()
                        if not str(k).startswith("page_")
                        and k not in _CMP_LLM_META_KEYS
                    },
                }
            )

        record_count = len(payload)
        ch_json = json.dumps(compact_history, ensure_ascii=False)

        if record_count <= 10:
            prompt = f"""
你是资深的产品体验官兼 QA 负责人。请根据以下 {record_count} 轮的 E2E 自动化巡检时序数据，给出一份简明扼要的综合分析。
数据包含首页与各游戏的核心耗时（内部为毫秒，请你自行换算为秒 s 向读者表述）及成功状态。

测试数据 (JSON):
{ch_json}

任务要求：
1. 请用产品经理友好的业务语言进行总结。
2. 结论置顶：首先给出这段时间内的整体业务可用性定性结论（是否可认为玩家可正常访问大厅与进入游戏）。
3. 不要在正文里罗列枯燥的毫秒级时序数据；请将核心耗时转换为秒（s）进行表述，并点出相对偏慢或偏快的轮次/页面。
4. 如果有异常，请指出对玩家体验的实际影响（如进入大厅变慢、某款游戏多次未就绪等）。
5. **凡按「第 N 轮」或轮次指称失败、峰值、最慢加载等，必须在紧挨轮次处括号写出该条 JSON 的 inspection_time（与 round 对应，原文照抄，禁止编造）**；多轮并列时示例：第 10 轮（2026-04-21 03:12:00（北京时间 (UTC+8)））、第 16 轮（…）。
6. 可简要补充最优表现（哪一轮/哪个环节相对最快）。不要 Markdown 代码块包裹。
"""
            sys_msg = (
                "你是资深的产品体验官兼 QA 负责人，擅长把技术指标翻译为业务与体验语言。"
            )
        else:
            prompt = f"""
你是 Jachin AI OS 的首席 SRE (站点可靠性工程师)。你现在需要向上级汇报过去 24 小时内，Kalaroko 平台的 E2E 自动化巡检全天大盘监控报告。
以下是过去 24 小时内抽样提取的 {record_count} 次测试指标。

测试数据 (JSON):
{ch_json}

任务要求：
1. 【全天可用性定调】：用一句话总结过去 24 小时系统的整体可用性和健康度（如：全天运行平稳，或夜间出现剧烈波动）。
2. 【极端异常点名】：不要报流水账！只挑出全天数据中**最慢的加载时间**、**异常的 TTFB 飙升**或**非 success 的失败记录**。明确指出是哪个游戏、在第几次测试中出现的。**凡写出「第 N 轮」或轮次编号，必须紧跟括号写出该条 JSON 的 inspection_time（与 round 对应，原文照抄，禁止编造）**，便于按时间回溯 jsonl/CDN 日志。如果全天数据极度健康，请直接说明「全天无异常超时或报错」。
3. 【趋势建议】：基于 24 小时的数据走向，给出 1-2 条运维视角的建议。
4. 语言要求：必须具备高管汇报的专业性（Executive Summary 风格），客观冷酷，不讲废话，直接输出结论段落，切勿使用 Markdown 代码块包裹。
"""
            sys_msg = (
                "你是 Jachin AI OS 的首席站点可靠性工程师，面向管理层撰写客观、精炼的技术运维汇报。"
            )

        log_n = record_count
        print(
            f"\n[LLM 分析] 正在调用大模型 ({model}) 分析 {record_count} 条时序数据…",
            flush=True,
        )

    if mode == "daily_24h":
        print(
            f"\n[LLM 分析] 正在调用大模型 ({model}) mode={mode} items={log_n} …",
            flush=True,
        )

    if mode != "daily_24h":
        timeout_s = 95.0 if record_count > 10 else 45.0
    else:
        timeout_s = 95.0
    print(f"[LLM] endpoint={url}", flush=True)
    print(
        f"[LLM] 请求规模: mode={mode} items={log_n}, user_prompt_chars={len(prompt)}",
        flush=True,
    )

    _retry_delays_s = [2.0, 4.0, 8.0]

    try:
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    print(
                        f"[LLM] POST chat/completions timeout={timeout_s}s "
                        f"(attempt {attempt + 1}/4) …",
                        flush=True,
                    )
                    t0 = time.monotonic()
                    resp = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": [
                                {"role": "system", "content": sys_msg},
                                {"role": "user", "content": prompt},
                            ],
                            "temperature": 0.2,
                        },
                    )
                    elapsed_ms = (time.monotonic() - t0) * 1000.0
                    print(
                        f"[LLM] HTTP status={resp.status_code} elapsed_ms={elapsed_ms:.0f}",
                        flush=True,
                    )

                    if resp.status_code >= 400:
                        snippet = (resp.text or "")[:1500]
                        print(f"[LLM] 非成功状态，响应体（截断）:\n{snippet}", flush=True)

                    resp.raise_for_status()
                    data = resp.json()

                    if isinstance(data, dict) and data.get("error"):
                        err_obj = data["error"]
                        print(f"[LLM] JSON 内含 error 字段: {err_obj!r}", flush=True)
                        return f"> ❌ DashScope API 错误: {err_obj}"

                    choices = data.get("choices")
                    if not isinstance(choices, list) or not choices:
                        keys = (
                            list(data.keys()) if isinstance(data, dict) else type(data)
                        )
                        print(f"[LLM] choices 缺失或为空，顶层 keys={keys}", flush=True)
                        body_preview = json.dumps(data, ensure_ascii=False)[:800]
                        print(f"[LLM] body 预览: {body_preview}", flush=True)
                        return "> ❌ 大模型综合分析调用失败: 响应无 choices"

                    msg0 = (choices[0] or {}).get("message") or {}
                    content = msg0.get("content")
                    if content is None:
                        print(
                            f"[LLM] message.content 为 None，choices[0]={choices[0]!r}",
                            flush=True,
                        )
                        return "> ❌ 大模型综合分析调用失败: 无 assistant content"

                    out = str(content).strip()
                    print(
                        f"[LLM] 解析成功: assistant 回复长度={len(out)} 字符",
                        flush=True,
                    )
                    return out

            except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
                print(
                    f"[LLM] 可重试错误 ({type(e).__name__}) attempt={attempt + 1}: {e!r}",
                    flush=True,
                )
                if attempt < 3:
                    delay = _retry_delays_s[attempt]
                    print(f"[LLM] {delay}s 后重试…", flush=True)
                    await asyncio.sleep(delay)
                    continue
                # 第 4 次仍失败：按原逻辑返回错误文案
                if isinstance(e, httpx.HTTPStatusError):
                    tb = ""
                    try:
                        tb = (e.response.text or "")[:1500]
                    except Exception:
                        tb = "(无法读取 response.text)"
                    print(
                        f"[LLM] HTTPStatusError 重试耗尽: {e!r} body_trunc=\n{tb}",
                        flush=True,
                    )
                    return (
                        f"> ❌ 大模型综合分析调用失败: HTTP "
                        f"{e.response.status_code if e.response else '?'} {tb[:500]}"
                    )
                print(f"[LLM] TimeoutException 重试耗尽: {e!r}", flush=True)
                return (
                    "> ❌ 大模型综合分析调用失败: 请求超时（已多次重试） "
                    f"{type(e).__name__}: {e}"
                )

    except httpx.RequestError as e:
        print(f"[LLM] RequestError: {type(e).__name__}: {e!r}", flush=True)
        return f"> ❌ 大模型综合分析调用失败: 网络请求异常 {type(e).__name__}: {e}"

    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
        print(f"[LLM] 解析异常: {type(e).__name__}: {e!r}", flush=True)
        return f"> ❌ 大模型综合分析调用失败: 解析响应失败 {type(e).__name__}: {e}"

    except Exception as e:
        print(f"[LLM] 未分类异常: {type(e).__name__}: {e!r}", flush=True)
        detail = str(e) or repr(e)
        return f"> ❌ 大模型综合分析调用失败: {detail}"


def _bold_duration_s(val: Any) -> str:
    """时长（ms 入参）→ 加粗的「X.XXs」或 **N/A**。"""
    inner = _fmt_ms_as_s(val)
    if inner == "N/A":
        return "**N/A**"
    return f"**{inner}**"


def _status_dot_normal(load_status: Any) -> str:
    """看板用：成功 🟢 正常，否则 🔴 失败。"""
    st = str(load_status or "").strip().lower()
    return "🟢 正常" if st == "success" else "🔴 失败"


def _game_dot_emoji(g: dict | None) -> str:
    if not g:
        return "🔴"
    st = str(g.get("load_status") or "").strip().lower()
    return "🟢" if st == "success" else "🔴"


def _homepage_failed_resources(mcore: dict) -> int:
    raw = mcore.get("failed_resources", 0)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _avg_three_games_load_ms(row: dict) -> float | None:
    """多轮 hist 单行：三款游戏 real_engine_load_ms 的算术平均（毫秒）。"""
    acc = 0.0
    n = 0
    for gid in ("tongits_king", "royal_pusoy", "color_blitz"):
        v = row.get(f"{gid}_load")
        if v is None:
            continue
        try:
            acc += float(v)
            n += 1
        except (TypeError, ValueError):
            continue
    if n == 0:
        return None
    return acc / n


def _first_exception_summary_line(bex: list) -> str:
    ranked = select_key_browser_exceptions(bex, 1)
    if not ranked:
        return "（无可用摘要）"
    ex = ranked[0]
    comp = compact_browser_exception(ex)
    parts = [str(comp.get("net") or ""), str(comp.get("type") or ""), str(comp.get("target") or "")]
    raw = " — ".join(p for p in parts if p).strip()
    msg = str(ex.get("message") or "").replace("\n", " ").strip()
    pick = raw if len(raw) >= 8 else (msg[:200] if msg else raw)
    return (pick or "unknown")[:220]


def _fmt_ms_inline_s(val: Any) -> str:
    """毫秒 → 反引号包裹的秒数字符串（两位小数）；无效为 `null` / `N/A`。"""
    if val is None:
        return "`null`"
    try:
        return f"`{float(val) / 1000.0:.2f}s`"
    except (TypeError, ValueError):
        return "`N/A`"


def _inline_scalar(val: Any, *, max_len: int = 220) -> str:
    """标量转飞书内联代码；空为 `null`。"""
    if val is None:
        return "`null`"
    t = str(val).strip()
    if not t:
        return "`null`"
    t = t.replace("`", "'")[:max_len]
    return f"`{t}`"


def _format_online_players_detail(g: dict) -> str:
    """与采数逻辑一致的人类可读在线人数文案（无表格）。"""
    online_data = g.get("online_players")
    display_parts: list[str] = []
    game_key = str(g.get("game_id") or "")
    ug = g.get("url_game_id")
    ug_s = "" if ug is None else str(ug).strip()

    if isinstance(online_data, dict):
        table_val = online_data.get("table")
        lobby_val = online_data.get("lobby")
        is_color = "color_blitz" in game_key or ug_s == "6"
        if is_color:
            if lobby_val:
                display_parts.append(f"{lobby_val} 人")
            elif table_val and "/" in str(table_val):
                display_parts.append(f"{str(table_val).split('/')[0].strip()} 人")
            elif table_val:
                display_parts.append(f"{str(table_val).strip()} 人")
        else:
            if table_val and "/" in str(table_val):
                current_num = str(table_val).split("/")[0].strip()
                display_parts.append(f"{current_num} 人（牌桌）")
            if lobby_val:
                display_parts.append(f"{lobby_val} 人（大厅）")
    elif (
        isinstance(online_data, str)
        and online_data.strip()
        and online_data not in ("N/A", "None")
    ):
        display_parts.append(str(online_data).strip())
    return " + ".join(display_parts) if display_parts else "N/A"


def _append_developer_details_section(
    lines: list[str],
    hp: dict,
    mcore: dict,
    wv: dict,
    games_by_id: dict[str, dict],
) -> None:
    """单轮报告末尾附录：树状 + 内联代码，无 Markdown 表格。"""
    lines.append("## 📊 详细诊断数据 (Developer Details)")
    lines.append("")

    proto = mcore.get("protocol")
    lines.append("**1. 首页加载详情 (kalaroko.com)**")
    lines.append(
        f"▪️ 网络与资源：协议 {_inline_scalar(proto)} | 总资源 {_inline_scalar(mcore.get('total_resources'))} | 失败 {_inline_scalar(mcore.get('failed_resources'))}"
    )
    fcp_ms = mcore.get("fcp_ms")
    if fcp_ms is None:
        fcp_ms = wv.get("fcp_ms")
    dom_ms = mcore.get("dom_content_loaded_ms")
    lines.append(
        f"▪️ 渲染生命周期：TTFB {_fmt_ms_inline_s(mcore.get('ttfb_ms') if mcore.get('ttfb_ms') is not None else wv.get('ttfb_ms'))} | "
        f"FCP {_fmt_ms_inline_s(fcp_ms)} | DOMContentLoaded {_fmt_ms_inline_s(dom_ms)} | 完全加载 {_fmt_ms_inline_s(mcore.get('page_load_ms'))}"
    )
    lines.append("")

    lines.append("**2. 游戏牌桌详情**")
    lines.append("")
    for gid_key, disp in (
        ("tongits_king", "Tongits King"),
        ("royal_pusoy", "Royal Pusoy"),
        ("color_blitz", "Color Blitz Social"),
    ):
        gg = games_by_id.get(gid_key)
        lines.append(f"🔹 **{disp}**")
        if gg is None:
            lines.append("   ├ 耗时详情：总加载 `N/A` (TTFB `N/A`)")
            lines.append("   ├ 房间状态：房间 ID `N/A` | 在线 `N/A`")
            lines.append("   ├ 资源加载：请求数 `N/A` | 失败 `N/A`")
            lines.append("   └ 业务报错：`N/A`（未采集）")
            lines.append("")
            continue
        ttfb_ms = gg.get("shell_navigation_ttfb_ms")
        if ttfb_ms is None:
            ttfb_ms = gg.get("ttfb_ms")
        load_ms = gg.get("real_engine_load_ms")
        room_id = gg.get("room_id")
        if room_id is not None and str(room_id).strip() == "":
            room_id = None
        online_txt = _format_online_players_detail(gg)
        total_req = gg.get("total_requests")
        if total_req is not None:
            try:
                req_disp = f"{int(total_req)}+"
            except (TypeError, ValueError):
                req_disp = "N/A"
        else:
            req_disp = "N/A"
        try:
            failed_res = int(gg.get("resource_errors_count", 0))
        except (TypeError, ValueError):
            failed_res = 0
        try:
            console_errs = int(gg.get("console_errors_count", 0))
        except (TypeError, ValueError):
            console_errs = 0
        if console_errs == 0:
            console_disp = "`0`（控制台无 error）"
        else:
            console_disp = _inline_scalar(console_errs)

        lines.append(
            f"   ├ 耗时详情：总加载 {_fmt_ms_inline_s(load_ms)} (TTFB {_fmt_ms_inline_s(ttfb_ms)})"
        )
        lines.append(
            f"   ├ 房间状态：房间 ID {_inline_scalar(room_id)} | 在线 {_inline_scalar(online_txt)}"
        )
        lines.append(
            f"   ├ 资源加载：请求数 `{req_disp}` | 失败 {_inline_scalar(failed_res)}"
        )
        lines.append(f"   └ 业务报错：{console_disp}")
        lines.append("")
    lines.append("")


def render_report_md(
    pw: dict,
    fh: dict,
    query_recent: dict | None,
    record: dict,
    *,
    current_run: int = 1,
    all_metrics_history: list[dict] | None = None,
) -> str:
    """飞书看板卡片式：无表格、无冗余 ID；时长 ms→s 两位小数并加粗。返回全文供 API/L3 汇总。"""
    hp = pw.get("homepage") or {}
    wv = hp.get("web_vitals") or {}
    mcore = hp.get("metrics") or {}
    games = pw.get("games") or []
    bex = pw.get("browser_exceptions") or []
    items = fh.get("items") or []
    api_sum = fh.get("summary") or {}
    lines: list[str] = []

    ts_line = _format_inspection_timestamp(pw, record)
    lines.append("---")
    lines.append(f"**🚀 Kalaroko PH 巡检快报 | 第 {current_run} 轮**")
    lines.append(f"📅 巡检时间: {ts_line}")
    lines.append("---")
    lines.append("")

    page_ttfb_ms = mcore.get("ttfb_ms")
    if page_ttfb_ms is None:
        page_ttfb_ms = wv.get("ttfb_ms")
    hp_fr = _homepage_failed_resources(mcore)
    lines.append("**🌐 首页加载 (kalaroko.com)**")
    lines.append(f"├ 状态: {_status_dot_normal(hp.get('load_status'))}")
    lines.append(
        f"├ 响应: {_bold_duration_s(mcore.get('page_load_ms'))} (首字节: {_bold_duration_s(page_ttfb_ms)})"
    )
    lines.append(f"└ 资源: 失败 {hp_fr} 个")
    hp_rm = _summary_remark_homepage(hp)
    if hp_rm:
        lines.append(f"　⚠️ {hp_rm}")
    lines.append("")

    lines.append("**🃏 游戏加载实测**")
    games_by_id = {str(g.get("game_id") or ""): g for g in games}
    _game_rows = [
        ("tongits_king", "Tongits King", "├"),
        ("royal_pusoy", "Royal Pusoy", "├"),
        ("color_blitz", "Color Blitz", "└"),
    ]
    for _, (gid_key, label, branch) in enumerate(_game_rows):
        gg = games_by_id.get(gid_key)
        if gg is None:
            lines.append(f"{branch} {label}: 🔴 **N/A**（未采集）")
            continue
        dot = _game_dot_emoji(gg)
        lines.append(f"{branch} {label}: {dot} {_bold_duration_s(gg.get('real_engine_load_ms'))}")
    lines.append("")

    bad_items = [x for x in items if not x.get("healthy")]
    codes = _api_summary_status_codes_cell(api_sum)
    lines.append("**🔌 后端 API 探活**")
    if not items:
        lines.append("├ 状态: （无探测数据）")
        lines.append("├ 均耗: **N/A**")
        lines.append("└ 瓶颈: **N/A**")
    elif not bad_items and codes == "全部 200":
        lines.append("├ 状态: 💯 全部正常 (200 OK)")
        _avg = api_sum.get("avg_latency_ms")
        lines.append(f"├ 均耗: {_bold_duration_s(_avg) if _avg is not None else '**N/A**'}")
        slowest_path = "N/A"
        slowest_ms: float | None = None
        for it in items:
            lat = it.get("latency_ms")
            try:
                lat_num = float(lat) if lat is not None else None
            except (TypeError, ValueError):
                lat_num = None
            if lat_num is None:
                continue
            if slowest_ms is None or lat_num > slowest_ms:
                slowest_ms = lat_num
                p = urlparse(it.get("url") or "").path or "/"
                slowest_path = p.replace("|", "／").strip()[:120] or "N/A"
        if slowest_ms is not None:
            lines.append(
                f"└ 瓶颈: `{slowest_path}` ({_bold_duration_s(slowest_ms)})"
            )
        else:
            lines.append("└ 瓶颈: **N/A**")
    elif not bad_items:
        lines.append(f"├ 状态: ✅ 端点均 healthy（码: {codes}）")
        _avg = api_sum.get("avg_latency_ms")
        lines.append(f"├ 均耗: {_bold_duration_s(_avg) if _avg is not None else '**N/A**'}")
        slowest_path, slowest_ms = "N/A", None
        for it in items:
            lat = it.get("latency_ms")
            try:
                lat_num = float(lat) if lat is not None else None
            except (TypeError, ValueError):
                lat_num = None
            if lat_num is None:
                continue
            if slowest_ms is None or lat_num > slowest_ms:
                slowest_ms = lat_num
                p = urlparse(it.get("url") or "").path or "/"
                slowest_path = p.replace("|", "／").strip()[:120] or "N/A"
        if slowest_ms is not None:
            lines.append(f"└ 瓶颈: `{slowest_path}` ({_bold_duration_s(slowest_ms)})")
        else:
            lines.append("└ 瓶颈: **N/A**")
    else:
        lines.append(f"├ 状态: ⚠️ {len(bad_items)} 个端点异常")
        _avg = api_sum.get("avg_latency_ms")
        lines.append(f"├ 均耗: {_bold_duration_s(_avg) if _avg is not None else '**N/A**'}")
        slowest_path, slowest_ms = "N/A", None
        for it in items:
            lat = it.get("latency_ms")
            try:
                lat_num = float(lat) if lat is not None else None
            except (TypeError, ValueError):
                lat_num = None
            if lat_num is None:
                continue
            if slowest_ms is None or lat_num > slowest_ms:
                slowest_ms = lat_num
                p = urlparse(it.get("url") or "").path or "/"
                slowest_path = p.replace("|", "／").strip()[:120] or "N/A"
        if slowest_ms is not None:
            lines.append(f"└ 瓶颈: `{slowest_path}` ({_bold_duration_s(slowest_ms)})")
        else:
            lines.append("└ 瓶颈: **N/A**")
    lines.append("")

    hist = all_metrics_history or []
    if len(hist) > 1:
        lines.append(f"## 📈 多轮趋势对比 (R1 → R{current_run})")
        lines.append("")
        home_chain = " → ".join(
            _bold_duration_s(hist[i].get("page_load")) for i in range(len(hist))
        )
        lines.append(f"• 首页时长: {home_chain}")
        avg_parts: list[str] = []
        for i in range(len(hist)):
            ams = _avg_three_games_load_ms(hist[i])
            avg_parts.append(_bold_duration_s(ams))
        lines.append(f"• 游戏平均: {' → '.join(avg_parts)}")
        lines.append("")

    lines.append("## 🔍 异常诊断")
    lines.append("")
    if not bex:
        lines.append("✅ 暂无影响业务的异常日志。")
    else:
        first_line = _first_exception_summary_line(bex)
        lines.append(
            f"🚨 拦截到 {len(bex)} 条异常，首条：{first_line}。"
        )
        lines.append("")
        lines.append("_完整日志见本地 jsonl；导航/埋点常见中断不一定为故障。_")
    lines.append("")

    recs = (query_recent or {}).get("records") or []
    if recs:
        lines.append(f"📦 本地已保留 **{len(recs)}** 条巡检快照（详见 jsonl，正文不展示技术 ID）。")
    else:
        lines.append("📦 本地尚无多期 jsonl 快照。")
    lines.append("")

    _append_developer_details_section(lines, hp, mcore, wv, games_by_id)

    lines.append(f"📎 {_DISCLAIMER_GAME_FRAME}")
    lines.append("")

    text = "\n".join(lines)
    print(text, flush=True)
    return text


async def _run_playwright() -> dict:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        emergency_kalaroko_playwright_cleanup,
        execute_playwright_perf_test,
        set_playwright_progress_callback,
    )

    print(
        "\n=== [1/3] execute_playwright_perf_test（scenarios=[] → KALAROKO_DEFAULT_SCENARIOS）===\n",
        flush=True,
    )
    _e2e_progress("开始 Playwright 巡检（阶段信息在 stderr，完整 JSON 仍在 stdout）…")
    set_playwright_progress_callback(_e2e_progress)
    try:
        out = await execute_playwright_perf_test(
            base_url=None,
            scenarios=[],
            collect_console=True,
            headless=False,
        )
    finally:
        set_playwright_progress_callback(None)
        try:
            await emergency_kalaroko_playwright_cleanup()
        except asyncio.CancelledError:
            raise
        except BaseException as e:
            print(
                f"[e2e] emergency_kalaroko_playwright_cleanup: {e}",
                file=sys.stderr,
                flush=True,
            )
    print(_j(out, limit=None), flush=True)
    return out


async def _run_fetch() -> dict:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import fetch_api_health

    print("\n=== [2/3] fetch_api_health ===\n", flush=True)
    _e2e_progress("HTTP 探测 gwp.heronpro.xin（大厅后端 API 列表）…")
    _to = 45000
    out = await fetch_api_health(
        endpoints=[
            {
                "id": "hall_server_status",
                "url": "https://gwp.heronpro.xin/hall/v1/hall/server-status",
                "method": "GET",
                "expected_status": 200,
                "timeout_ms": _to,
            },
            {
                "id": "user_info",
                "url": "https://gwp.heronpro.xin/user/v1/users/user-info",
                "method": "GET",
                "expected_status": 200,
                "timeout_ms": _to,
            },
            {
                "id": "games_list",
                "url": "https://gwp.heronpro.xin/hall/v1/game/games-list",
                "method": "GET",
                "expected_status": 200,
                "timeout_ms": _to,
            },
            {
                "id": "lottery_activities",
                "url": "https://gwp.heronpro.xin/hall/v1/lottery/activities",
                "method": "GET",
                "expected_status": 200,
                "timeout_ms": _to,
            },
            {
                "id": "lottery_activity_detail",
                "url": "https://gwp.heronpro.xin/hall/v1/lottery/activity-detail",
                "method": "GET",
                "expected_status": 200,
                "timeout_ms": _to,
            },
        ],
        run_id="e2e-fetch",
        parallel=True,
    )
    print(_j(out, limit=None), flush=True)
    return out


def _run_history(pw: dict, fh: dict, jsonl_path: str) -> tuple[dict, dict]:
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        manage_perf_history,
    )

    print("\n=== [3/3] manage_perf_history（jsonl append + query_recent）===\n", flush=True)
    _e2e_progress("写入持久化 jsonl 并 query_recent …")
    items = fh.get("items") or []
    api_health = []
    for it in items:
        api_health.append(
            {
                "id": it.get("id"),
                "url": it.get("url"),
                "method": it.get("method", "GET"),
                "status_code": it.get("status_code"),
                "latency_ms": it.get("latency_ms"),
                "healthy": it.get("healthy"),
                "error": it.get("error"),
            }
        )

    record: dict = {
        "schema_version": pw.get("schema_version", "1.0.0"),
        "run_id": pw.get("run_id"),
        "captured_at": pw.get("captured_at"),
        "network_profile": pw.get("network_profile"),
        "homepage": pw.get("homepage"),
        "api_health": api_health,
        "api_summary": fh.get("summary"),
        "games": pw.get("games", []),
        "browser_exceptions": pw.get("browser_exceptions", []),
    }
    if pw.get("aggregation_notes") is not None:
        record["aggregation_notes"] = pw.get("aggregation_notes")

    a = manage_perf_history(
        operation="append",
        storage="jsonl",
        path=jsonl_path,
        record=record,
    )
    print(_j(a, limit=None), flush=True)
    if not a.get("ok"):
        raise RuntimeError(f"append 失败: {a}")

    q = manage_perf_history(
        operation="query_recent",
        storage="jsonl",
        path=jsonl_path,
        limit=10,
    )
    print(_j(q, limit=None), flush=True)
    if not q.get("ok"):
        raise RuntimeError(f"query_recent 失败: {q}")
    return record, q


def _assert_playwright_shape(pw: dict) -> None:
    if pw.get("ok") is not True:
        raise AssertionError(f"Playwright 返回非成功: {pw.get('error_code')} {pw.get('message')}")
    notes = pw.get("aggregation_notes") or []
    if not any("KALAROKO_DEFAULT_SCENARIOS" in str(x) for x in notes):
        raise AssertionError("期望 aggregation_notes 标明使用默认场景")
    games = pw.get("games") or []
    if len(games) != 3:
        raise AssertionError(f"期望 3 条游戏场景，实际 games 条数={len(games)}")
    for g in games:
        if not g.get("game_id"):
            raise AssertionError(f"游戏项缺少 game_id: {g}")
        key = str(g.get("game_id"))
        if key in ("tongits_king", "royal_pusoy", "color_blitz"):
            if g.get("url_game_id") is None or g.get("document_game_id") is None:
                raise AssertionError(
                    f"默认三游戏应含 document_game_id 与 url_game_id（对齐 Word 与 gweb URL）: {g}"
                )


async def _run_full_cycle(
    runs: int,
    interval: int,
    *,
    skip_playwright: bool,
    line_sink: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """执行 N 轮 E2E 循环；多轮时生成末尾 AI 综合分析。供 CLI 与 L3 API 共用。"""
    reset_manual_run_flag()

    llm_analysis: str | None = None
    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        KALAROKO_DEFAULT_SCENARIOS,
    )

    n = len(KALAROKO_DEFAULT_SCENARIOS)
    _e2e_echo(f"KALAROKO_DEFAULT_SCENARIOS 条数: {n}（期望 4：首页 + 3 游戏）")
    if n != 4:
        _e2e_echo("[WARN] 默认场景条数非 4，请检查 mcp_kalaroko_monitor.py 常量")

    all_metrics_history: list[dict] = []
    markdown_rounds: list[str] = []
    cancelled = False
    structural_all_ok = True
    had_round_exception = False

    jsonl_path = str(KALAROKO_E2E_JSONL)

    for current_run in range(1, runs + 1):
        _e2e_echo("")
        _e2e_echo("=" * 54)
        _e2e_echo(f"🚀 开始执行第 {current_run}/{runs} 轮测试…")
        _e2e_echo("=" * 54)

        if is_manual_run_cancel_requested():
            cancelled = True
            break

        try:
            if skip_playwright:
                pw = {
                    "ok": True,
                    "schema_version": "1.0.0",
                    "run_id": "e2e-skip-pw",
                    "captured_at": "1970-01-01T00:00:00Z",
                    "network_profile": "wifi",
                    "homepage": {
                        "url": "https://kalaroko.com/",
                        "load_status": "skipped",
                        "metrics": {
                            "ttfb_ms": None,
                            "fcp_ms": None,
                            "dom_content_loaded_ms": None,
                            "page_load_ms": None,
                            "total_resources": 0,
                            "failed_resources": 0,
                            "protocol": "UNKNOWN",
                        },
                        "web_vitals": {
                            "lcp_ms": None,
                            "fid_ms": None,
                            "cls": None,
                            "inp_ms": None,
                            "ttfb_ms": None,
                            "fcp_ms": None,
                        },
                        "navigation_timing": {},
                    },
                    "games": [],
                    "browser_exceptions": [],
                    "aggregation_notes": [
                        "--skip-playwright：未执行真实 KALAROKO_DEFAULT_SCENARIOS"
                    ],
                }
                _e2e_echo("[INFO] 已跳过 Playwright，使用占位 pw 载荷")
            else:
                pw = await _run_playwright()
                if isinstance(pw, dict) and pw.get("error_code") == "USER_CANCELLED":
                    cancelled = True
                    break
                _assert_playwright_shape(pw)

            if is_manual_run_cancel_requested():
                cancelled = True
                break

            fh = await _run_fetch()
            if fh.get("ok") is not True:
                raise AssertionError(f"fetch_api_health 失败: {fh}")

            if not skip_playwright:
                structural_all_ok = structural_all_ok and _round_e2e_structural_green(
                    pw, fh
                )

            if is_manual_run_cancel_requested():
                cancelled = True
                break

            record, query_recent = _run_history(pw, fh, jsonl_path)

            if is_manual_run_cancel_requested():
                cancelled = True
                break

            current_metrics = (
                _extract_comparison_metrics(pw) if not skip_playwright else {}
            )
            all_metrics_history.append(current_metrics)

            print("\n", flush=True)
            _e2e_progress(
                f"生成巡检快报（stdout，第 {current_run}/{runs} 轮）…"
            )
            md_round = render_report_md(
                pw,
                fh,
                query_recent,
                record,
                current_run=current_run,
                all_metrics_history=all_metrics_history,
            )
            markdown_rounds.append(md_round)
            _e2e_echo(
                f"[报告] 第 {current_run}/{runs} 轮巡检快报已生成（{len(md_round)} 字符）；"
                "飞书与 done.markdown_report 均为扁平 Emoji 格式。"
            )

            if is_manual_run_cancel_requested():
                cancelled = True
                break

            if current_run < runs:
                _e2e_echo(
                    f"\n⏳ 第 {current_run} 轮执行完毕。等待 {interval} 秒后进行下一轮…\n"
                )
                await asyncio.sleep(interval)
                if is_manual_run_cancel_requested():
                    cancelled = True
                    break

        except Exception as round_exc:
            had_round_exception = True
            msg = f"{type(round_exc).__name__}: {round_exc}"
            _e2e_echo(
                f"[E2E] 第 {current_run}/{runs} 轮异常，已记入报告并继续下一轮：{msg[:600]}"
            )
            markdown_rounds.append(
                f"# Kalaroko PH 巡检快报 (第 {current_run} 轮 · 异常中断)\n\n"
                f"本轮流水线失败（可能含 Page Closed / CDP 断开 / fetch 失败等）。\n\n"
                f"```\n{msg}\n```\n\n"
                f"```\n{traceback.format_exc()[:8000]}\n```\n"
            )
            all_metrics_history.append({})
            continue

    if cancelled:
        combined_md = "\n\n---\n\n".join(markdown_rounds) if markdown_rounds else ""
        _e2e_progress("巡检已由用户停止。")
        _e2e_echo("\n=== E2E 已中断 ===")
        return {
            "ok": False,
            "exit_code": 130,
            "cancelled": True,
            "error": "巡检已由用户停止",
            "markdown_report": combined_md or None,
            "llm_analysis": None,
            "runs": runs,
            "interval": interval,
            "skip_playwright": skip_playwright,
        }

    if all_metrics_history and len(all_metrics_history) > 1:
        _e2e_echo(
            f"[LLM] main: 满足多轮条件 (history_len={len(all_metrics_history)})，"
            "开始调用 _generate_llm_summary"
        )
        llm_analysis = await _generate_llm_summary(all_metrics_history)
        _e2e_echo(
            f"[LLM] main: _generate_llm_summary 返回，文本长度={len(llm_analysis)} "
            f"开头={llm_analysis[:80]!r}…"
        )
        _m = _e2e_summary_model()
        # 长文在 CLI 仍完整打印；SSE 仅推送标题行，正文由 L3 「done.llm_analysis」下发，避免日志区重复巨量文本
        print("\n" + "=" * 72)
        print(f"# 🧠 AI 专家时序综合大盘分析 (基于 {_m})")
        print("=" * 72)
        print(f"\n{llm_analysis}\n")
        print("=" * 72 + "\n")
        _e2e_echo(
            f"[LLM] 综合分析已完成（模型 {_m}），长度={len(llm_analysis)} 字符；"
            "SSE 收尾事件将附带全文。"
        )

    _e2e_progress("全部步骤完成。")
    _e2e_echo("\n=== E2E 通过 ===")
    combined_md = "\n\n---\n\n".join(markdown_rounds) if markdown_rounds else ""

    try:
        from l3_node.channels.lark.kalaroko_inspection_notify import (
            send_kalaroko_inspection_to_lark,
        )

        await send_kalaroko_inspection_to_lark(
            markdown_report=combined_md or None,
            llm_analysis=llm_analysis,
            runs=runs,
            interval=interval,
            summary_model=_e2e_summary_model(),
            line_sink=line_sink,
        )
    except Exception as e:
        print(f"[Lark inspect] 推送跳过或失败（不影响 E2E 退出码）: {e!r}", flush=True)

    # 记忆宫殿：仅异常/退化/特殊 LLM 结论时 verbatim 入库；全绿平稳则跳过（24h 晨报由调度器单独浓缩）
    _want_mem = _should_commit_kalaroko_e2e_memory_nexus(
        skip_playwright=skip_playwright,
        had_round_exception=had_round_exception,
        structural_all_ok=structural_all_ok,
        llm_analysis=llm_analysis,
    )
    if (
        _want_mem
        and (combined_md or "").strip()
        and (llm_analysis or "").strip()
    ):
        try:
            from l3_client.local_mcps.jachin_memory_nexus.memory_backend import commit_drawer

            mem_blob = f"{llm_analysis}\n\n{combined_md}"
            extra_meta = {
                "source": "kalaroko_e2e",
                "runs": runs,
                "interval_sec": interval,
                "summary_model": _e2e_summary_model(),
                "skip_playwright": skip_playwright,
                "memory_commit_reason": "anomaly_or_special_llm",
            }

            def _sync_commit() -> str:
                return commit_drawer(
                    text=mem_blob,
                    wing="E2E_Monitors",
                    room="Kalaroko_Default",
                    extra_meta=extra_meta,
                )

            drawer_id = await asyncio.to_thread(_sync_commit)
            _e2e_echo(
                f"[Memory] 已写入 palace_db drawer_id={drawer_id} "
                "(wing=E2E_Monitors room=Kalaroko_Default)"
            )
            if line_sink:
                try:
                    line_sink(
                        f"[Memory] Verbatim 已入库 drawer_id={drawer_id}"
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[Memory] Nexus 写入跳过或失败（不影响 E2E 退出码）: {e!r}", flush=True)
    elif (combined_md or "").strip() and (llm_analysis or "").strip():
        _e2e_echo(
            "[Memory] 本轮全绿平稳，跳过 Memory Nexus 写入（避免向量库膨胀）"
        )

    return {
        "ok": True,
        "exit_code": 0,
        "markdown_report": combined_md,
        "llm_analysis": llm_analysis,
        "runs": runs,
        "interval": interval,
        "skip_playwright": skip_playwright,
    }


async def run_kalaroko_batch_test(
    runs: int,
    interval: int,
    *,
    skip_playwright: bool = False,
    line_sink: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """L3 HTTP/SSE：多轮 Kalaroko 默认场景 E2E；`line_sink` 可接收与 stdout 同步的文本行（不含 Playwright 大 JSON）。"""
    global _e2e_line_sink
    prev = _e2e_line_sink
    _e2e_line_sink = line_sink
    try:
        if runs < 1:
            return {
                "ok": False,
                "exit_code": 2,
                "error": "--runs 须为 >= 1",
                "markdown_report": None,
                "llm_analysis": None,
            }
        if interval < 0:
            return {
                "ok": False,
                "exit_code": 2,
                "error": "--interval 须为 >= 0",
                "markdown_report": None,
                "llm_analysis": None,
            }
        try:
            async with _E2E_SERIAL_LOCK:
                return await _run_full_cycle(
                    runs,
                    interval,
                    skip_playwright=skip_playwright,
                    line_sink=line_sink,
                )
        except AssertionError as e:
            msg = str(e)
            print(f"\n[E2E FAIL] {msg}", file=sys.stderr, flush=True)
            _e2e_progress(f"断言失败: {msg}")
            return {
                "ok": False,
                "exit_code": 1,
                "error": msg,
                "markdown_report": None,
                "llm_analysis": None,
            }
        except Exception as e:
            print("\n[E2E FAIL] 未预期异常:", file=sys.stderr, flush=True)
            traceback.print_exc()
            _e2e_progress(f"异常: {e!r}")
            return {
                "ok": False,
                "exit_code": 2,
                "error": repr(e),
                "markdown_report": None,
                "llm_analysis": None,
            }
    finally:
        _e2e_line_sink = prev


async def main() -> int:
    ap = argparse.ArgumentParser(description="Kalaroko MCP 默认场景 E2E")
    ap.add_argument(
        "--skip-playwright",
        action="store_true",
        help="跳过浏览器（不跑 Playwright），仅构造最小快照后测 fetch + history",
    )
    ap.add_argument(
        "--runs",
        type=int,
        default=4,
        help="连续测试的总轮数（默认 4）",
    )
    ap.add_argument(
        "--interval",
        type=int,
        default=30,
        help="两轮测试之间的间隔秒数（默认 30）",
    )
    args = ap.parse_args()

    r = await run_kalaroko_batch_test(
        args.runs,
        args.interval,
        skip_playwright=args.skip_playwright,
    )
    return int(r.get("exit_code", 2))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
