#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FB 广告日报 + 事件质量 调度守护进程（独立于 L3 进程）

职责
----
1. 每天定时（默认本地 09:00）依次触发：
     ① fb_report_nexus.py    — 抓取广告数据并推送 Lark CSV
     ② fb_events_quality.py  — 抓取事件质量分并推送 Lark 卡片
2. 两个任务独立管理状态和重试；任一失败均每隔 FB_SCHED_RETRY_INTERVAL_MIN 分钟补偿重试。
3. 当日重试超过 FB_SCHED_MAX_RETRIES 次后停止自动重试并发 Lark 告警。
4. 状态持久化至 ~/.jachin/data/；进程重启后可断点续策，不会重复投递。
5. 守护模式下每分钟刷新 ``~/.jachin/workspace/external_scheduled_hints.json`` 心跳，供 **同机** L3 ``format_combined_runtime_prompt_suffix`` 展示（关闭写：`FB_SCHED_EXTERNAL_HINT_DISABLE=1`）。

用法
----
  python scripts/fb_report_scheduler.py            # 前台守护，持续运行
  python scripts/fb_report_scheduler.py --once     # 立即执行一次（广告+事件），完成后退出
  python scripts/fb_report_scheduler.py --run-now  # 立即强制执行一次，之后继续守护

关键环境变量（亦可写入 scripts/fb_report_scheduler.env）
  FB_SCHED_TIME               每日触发时间（HH:MM，UTC+8 马来西亚时间），默认 08:00
  FB_SCHED_WINDOW_END         发送窗口截止时间（HH:MM，UTC+8），超过此时间当日不再重试，默认 10:00
  FB_SCHED_RETRY_INTERVAL_MIN 失败重试间隔（分钟），默认 15
  FB_SCHED_MAX_RETRIES        每日最大重试次数，默认 10
  FB_SCHED_ALERT_ON_EXHAUST   耗尽后 Lark 告警，默认 1
  FB_SCHED_NEXUS_SCRIPT       fb_report_nexus.py 路径，默认同目录
  FB_SCHED_EVENTS_SCRIPT      fb_events_quality.py 路径，默认同目录
  FB_SCHED_EVENTS_DELAY_SEC   广告任务完成后等待多少秒再跑事件任务，默认 10
  FB_SCHED_EXTERNAL_HINT_DISABLE  设为 1 则勿写入 external_scheduled_hints.json（L3 侧将看不到本守护）
  FB_SCHED_L3_REGISTRY_URL        可选：远地 L3 HTTP 根（如 http://127.0.0.1:18991）；与 JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN
                                  联用时，守护退出额外发 DELETE /api/v1/registry/external-sched-hint（同机只清文件即可）
  JACHIN_L3_HTTP_URL              FB_SCHED_L3_REGISTRY_URL 的别名（任设其一）

  透传给子脚本（在 fb_report_nexus.env 里统一配置即可）：
  FB_REPORT_CDP_URL / FB_REPORT_PRESET / LARK_APP_ID / LARK_APP_SECRET / LARK_RECEIVER_ID
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# 初始化日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [fb_sched] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fb_report_scheduler")

# ---------------------------------------------------------------------------
# 配置文件加载（scripts/fb_report_scheduler.env，不提交仓库）
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCHED_ENV_PATH = _SCRIPT_DIR / "fb_report_scheduler.env"
# fb_report_nexus.env 也在同目录，nexus.py 自己会读；这里只需读 scheduler 专属配置。


def _load_sched_env() -> None:
    """将 fb_report_scheduler.env 注入 os.environ（不覆盖已有变量）。"""
    if not _SCHED_ENV_PATH.is_file():
        return
    try:
        text = _SCHED_ENV_PATH.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


_load_sched_env()

# ---------------------------------------------------------------------------
# 运行时配置（从环境变量读取）
# ---------------------------------------------------------------------------


def _env_str(key: str, default: str) -> str:
    return (os.environ.get(key) or "").strip() or default


def _env_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# 每日触发时间（本地时间）
# 触发时间默认 08:00 MYT（UTC+8，马来西亚/菲律宾/新加坡时间）
SCHED_TIME_STR: str = _env_str("FB_SCHED_TIME", "08:00")
# 发送窗口截止时间：超过此时间当日不再重试（默认 10:00 MYT）
SCHED_WINDOW_END_STR: str = _env_str("FB_SCHED_WINDOW_END", "10:00")
# 显式时区：无论部署机器本地时区是什么，始终以 UTC+8 计算触发点
SCHED_TIMEZONE = ZoneInfo("Asia/Kuala_Lumpur")
# 失败重试间隔（分钟）
RETRY_INTERVAL_MIN: int = _env_int("FB_SCHED_RETRY_INTERVAL_MIN", 15)
# 每日最大重试次数（不含首次）
MAX_RETRIES: int = _env_int("FB_SCHED_MAX_RETRIES", 10)
# 重试耗尽是否发告警
ALERT_ON_EXHAUST: bool = _env_bool("FB_SCHED_ALERT_ON_EXHAUST", True)

# 脚本路径
_NEXUS_SCRIPT_DEFAULT = str(_SCRIPT_DIR / "fb_report_nexus.py")
_EVENTS_SCRIPT_DEFAULT = str(_SCRIPT_DIR / "fb_events_quality.py")
NEXUS_SCRIPT: str = _env_str("FB_SCHED_NEXUS_SCRIPT", _NEXUS_SCRIPT_DEFAULT)
EVENTS_SCRIPT: str = _env_str("FB_SCHED_EVENTS_SCRIPT", _EVENTS_SCRIPT_DEFAULT)
EVENTS_DELAY_SEC: int = _env_int("FB_SCHED_EVENTS_DELAY_SEC", 10)

# 状态文件
_STATE_DIR = Path.home() / ".jachin" / "data"
_LAST_ADS_FILE   = _STATE_DIR / ".last_fb_report_date.txt"   # 广告任务
_LAST_EVENTS_FILE = _STATE_DIR / ".last_fb_events_date.txt"  # 事件质量任务

# 与 l3_node/task_runtime_registry.external_scheduled_hints_path() 同路径：供 L3 prompt 感知本守护进程
_FB_EXTERNAL_PROC_KEY = "fb_report_scheduler"


def _fb_external_hint_write_disabled() -> bool:
    return os.environ.get("FB_SCHED_EXTERNAL_HINT_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def touch_fb_external_sched_hint() -> None:
    """写入 external_scheduled_hints（与 L3 **M** 同源）；须能以仓库根 import l3_node。"""
    if _fb_external_hint_write_disabled():
        return
    sched_desc = f"每日本地 {SCHED_TIME_STR}；失败每 {RETRY_INTERVAL_MIN}min 补偿（PID {os.getpid()}）"
    try:
        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from l3_node.task_runtime_registry import merge_external_scheduled_process_hint

        merge_external_scheduled_process_hint(
            process_key=_FB_EXTERNAL_PROC_KEY,
            title="FB 广告日报 + 事件质量",
            schedule_summary=sched_desc,
            pid=os.getpid(),
        )
    except Exception as e:
        logger.debug("[ext_hint] 写入 external_scheduled_hints 跳过: %s", e)


def clear_fb_external_sched_hint() -> None:
    """守护进程退出时移除 **M** 文件中心跳；若配置远地 L3 则额外 HTTP **DELETE**（**O** 同源）。"""
    try:
        root = Path(__file__).resolve().parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from l3_node.task_runtime_registry import remove_external_scheduled_process_hint

        if remove_external_scheduled_process_hint(_FB_EXTERNAL_PROC_KEY):
            logger.info("[ext_hint] 已清除 external_scheduled_hints：%s", _FB_EXTERNAL_PROC_KEY)
    except Exception as e:
        logger.debug("[ext_hint] 本地清除跳过: %s", e)

    base = (
        (os.environ.get("FB_SCHED_L3_REGISTRY_URL") or os.environ.get("JACHIN_L3_HTTP_URL") or "")
        .strip()
        .rstrip("/")
    )
    tok = (os.environ.get("JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN") or "").strip()
    if not base or not tok:
        return
    try:
        import urllib.error
        import urllib.request

        body = json.dumps({"process_key": _FB_EXTERNAL_PROC_KEY}).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/v1/registry/external-sched-hint",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Jachin-Registry-Token": tok,
            },
            method="DELETE",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            _ = resp.read()
        logger.info("[ext_hint] 已对 L3 发送 DELETE external-sched-hint（%s）", base)
    except urllib.error.HTTPError as e:
        logger.debug("[ext_hint] L3 HTTP DELETE %s %s", e.code, e.reason)
    except Exception as e:
        logger.debug("[ext_hint] L3 HTTP DELETE 跳过: %s", e)


# ---------------------------------------------------------------------------
# 每日重试计数（广告 / 事件 各自独立；进程内状态）
# ---------------------------------------------------------------------------

_retry_lock: asyncio.Lock | None = None

# 广告任务
_ads_attempts: int = 0
_ads_exhausted: bool = False

# 事件质量任务
_events_attempts: int = 0
_events_exhausted: bool = False

# ---------------------------------------------------------------------------
# 状态文件读写
# ---------------------------------------------------------------------------


def _read_date_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_date_file(path: Path, d: str) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(d, encoding="utf-8")
    except Exception as e:
        logger.warning("[state] 写入状态文件失败（忽略）: %s", e)


def _today_str() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Lark 告警（使用 fb_report_nexus.env 中的 Lark 配置）
# ---------------------------------------------------------------------------


async def _send_lark_alert(message: str) -> None:
    """通过 fb_report_nexus 的 Lark 配置发送告警消息。"""
    if not ALERT_ON_EXHAUST:
        return
    try:
        # 动态导入，避免模块级变量冲突
        import importlib.util

        spec = importlib.util.spec_from_file_location("fb_report_nexus", NEXUS_SCRIPT)
        if spec is None or spec.loader is None:
            logger.warning("[alert] 无法加载 fb_report_nexus 模块，跳过告警")
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        send_text_fn = getattr(mod, "_send_lark_text_message", None)
        if send_text_fn is None:
            logger.warning("[alert] fb_report_nexus 未导出 _send_lark_text_message，跳过告警")
            return

        await send_text_fn(message)
        logger.info("[alert] Lark 告警已发送")
    except Exception as e:
        logger.warning("[alert] Lark 告警发送失败（忽略）: %s", e)


# ---------------------------------------------------------------------------
# 核心：以子进程运行 fb_report_nexus.py
# ---------------------------------------------------------------------------


async def _run_subprocess(script_path: str, tag: str) -> int:
    """以子进程运行指定脚本，实时转发输出，返回退出码。"""
    cmd = [sys.executable, script_path]
    logger.info("[%s] 启动子进程: %s", tag, " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"          # 强制子进程 stdout/stderr 使用 UTF-8（解决 Windows GBK 问题）
    env["PYTHONIOENCODING"] = "utf-8"
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    assert proc.stdout is not None
    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if line:
            logger.info("[%s] %s", tag, line)
    rc = await proc.wait()
    logger.info("[%s] 退出码: %d", tag, rc)
    return rc


# ---------------------------------------------------------------------------
# 调度任务
# ---------------------------------------------------------------------------


async def _get_retry_lock() -> asyncio.Lock:
    global _retry_lock
    if _retry_lock is None:
        _retry_lock = asyncio.Lock()
    return _retry_lock


def _parse_window_end() -> tuple[int, int]:
    """解析 FB_SCHED_WINDOW_END（HH:MM），返回 (hour, minute)，默认 10:00。"""
    try:
        parts = SCHED_WINDOW_END_STR.strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, IndexError):
        pass
    return 10, 0


def _within_send_window(force: bool = False) -> bool:
    """
    判断当前 MYT 时间是否在发送窗口内（[FB_SCHED_TIME, FB_SCHED_WINDOW_END]）。
    force=True 时始终返回 True（手动触发不受窗口限制）。
    """
    if force:
        return True
    from datetime import time as dtime
    sched_h, sched_m = _parse_sched_time(SCHED_TIME_STR)
    end_h, end_m = _parse_window_end()
    now_myt = datetime.now(SCHED_TIMEZONE).time()
    window_start = dtime(sched_h, sched_m)
    window_end   = dtime(end_h, end_m)
    return window_start <= now_myt <= window_end


async def _single_task(
    *,
    tag: str,
    script: str,
    state_file: Path,
    attempts_ref: list[int],   # [当日次数]（用 list 实现可变引用）
    exhausted_ref: list[bool], # [是否耗尽]
    alert_title: str,
    force: bool = False,
) -> bool:
    """通用单任务执行器（广告 / 事件 复用）。"""
    today = _today_str()
    last_sent = _read_date_file(state_file)

    # 跨天重置
    if last_sent != today:
        attempts_ref[0] = 0
        exhausted_ref[0] = False

    if last_sent == today and not force:
        logger.info("[%s] 今日已完成（%s），跳过。", tag, today)
        return True

    # 窗口检查：08:00–10:00 MYT 之外不触发（force 模式除外）
    if not _within_send_window(force):
        end_h, end_m = _parse_window_end()
        logger.info(
            "[%s] 当前 MYT 时间 %s 不在发送窗口（%s–%02d:%02d），今日不再重试。",
            tag,
            datetime.now(SCHED_TIMEZONE).strftime("%H:%M"),
            SCHED_TIME_STR,
            end_h, end_m,
        )
        return False

    if exhausted_ref[0] and not force:
        logger.info("[%s] 今日重试已耗尽，不再自动重试。", tag)
        return False

    if attempts_ref[0] > MAX_RETRIES and not force:
        exhausted_ref[0] = True
        logger.error("[%s] 今日已尝试 %d 次，宣告耗尽。", tag, attempts_ref[0])
        if ALERT_ON_EXHAUST:
            await _send_lark_alert(
                f"⚠️ **{alert_title}**\n\n"
                f"今日（{today}）已尝试 **{attempts_ref[0]} 次**，全部失败，已停止自动重试。\n\n"
                f"请检查：Ngrok 隧道是否在线、Chrome 9223 是否运行、FB 是否登录。\n\n"
                f"手动补跑：`python scripts/fb_report_scheduler.py --once`"
            )
        return False

    attempts_ref[0] += 1
    logger.info("[%s] 第 %d 次尝试（上限 %d）...", tag, attempts_ref[0], MAX_RETRIES + 1)

    try:
        rc = await _run_subprocess(script, tag)
    except Exception as e:
        logger.exception("[%s] 子进程异常: %s", tag, e)
        rc = 99

    if rc == 0:
        _write_date_file(state_file, today)
        exhausted_ref[0] = False
        logger.info("[%s] ✅ 成功（%s，第 %d 次）", tag, today, attempts_ref[0])
        return True

    logger.warning(
        "[%s] ❌ 失败（退出码 %d），%d 分钟后补偿重试。",
        tag, rc, RETRY_INTERVAL_MIN,
    )
    return False


# 两个任务各自的可变状态容器
_ads_attempts_ref:    list[int]  = [0]
_ads_exhausted_ref:   list[bool] = [False]
_events_attempts_ref: list[int]  = [0]
_events_exhausted_ref: list[bool] = [False]


async def fb_ads_task(*, force: bool = False) -> bool:
    """广告数据任务（fb_report_nexus.py）。"""
    return await _single_task(
        tag="ads",
        script=NEXUS_SCRIPT,
        state_file=_LAST_ADS_FILE,
        attempts_ref=_ads_attempts_ref,
        exhausted_ref=_ads_exhausted_ref,
        alert_title="FB 广告日报发送失败",
        force=force,
    )


async def fb_events_task(*, force: bool = False) -> bool:
    """事件质量任务（fb_events_quality.py）。"""
    return await _single_task(
        tag="events",
        script=EVENTS_SCRIPT,
        state_file=_LAST_EVENTS_FILE,
        attempts_ref=_events_attempts_ref,
        exhausted_ref=_events_exhausted_ref,
        alert_title="FB 事件质量报告发送失败",
        force=force,
    )


async def fb_report_task(*, force: bool = False) -> None:
    """
    每日组合任务入口：先跑广告，等待 EVENTS_DELAY_SEC 秒，再跑事件质量。
    两个任务独立统计成功/失败，互不影响。
    """
    lock = await _get_retry_lock()
    async with lock:
        logger.info("[daily] ── 开始每日任务 ──")
        await fb_ads_task(force=force)
        logger.info("[daily] 等待 %ds 后执行事件质量任务...", EVENTS_DELAY_SEC)
        await asyncio.sleep(EVENTS_DELAY_SEC)
        await fb_events_task(force=force)
        logger.info("[daily] ── 每日任务结束 ──")


# ---------------------------------------------------------------------------
# APScheduler 驱动（守护模式）
# ---------------------------------------------------------------------------


def _parse_sched_time(time_str: str) -> tuple[int, int]:
    """解析 HH:MM，返回 (hour, minute)；格式错误则默认 09:00。"""
    try:
        parts = time_str.strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, IndexError):
        pass
    logger.warning("[sched] FB_SCHED_TIME 格式无效（%r），使用默认 08:00", time_str)
    return 9, 0


async def _run_daemon(run_now_immediately: bool = False) -> None:
    """启动 APScheduler 守护，直到进程被杀。"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    from datetime import time as dtime, timedelta

    sched_hour, sched_minute = _parse_sched_time(SCHED_TIME_STR)
    now_utc = datetime.now(timezone.utc)
    now_myt = datetime.now(SCHED_TIMEZONE)

    # 判断今天的定时点是否已经过了，且在窗口内，且报告还没发过
    # → 已过触发点 + 在窗口内 + 未发过 = 需要立即补偿（重启追补）
    # → 其他情况                        = 等一个间隔后首检，避免启动就跑
    today_sched_passed = now_myt.time() >= dtime(sched_hour, sched_minute)
    ads_sent_today = _read_date_file(_LAST_ADS_FILE) == _today_str()
    in_window = _within_send_window()
    need_immediate = today_sched_passed and in_window and not ads_sent_today
    first_compensation_time = now_utc if need_immediate else now_utc + timedelta(minutes=RETRY_INTERVAL_MIN)

    logger.info(
        "[sched] 启动守护 | 每日触发 %02d:%02d MYT（UTC+8，Asia/Kuala_Lumpur）| 重试间隔 %d 分钟 | 最大重试 %d 次",
        sched_hour,
        sched_minute,
        RETRY_INTERVAL_MIN,
        MAX_RETRIES,
    )
    logger.info("[sched] 当前 MYT: %s | 定时点已过: %s | 今日已发: %s",
                now_myt.strftime("%H:%M"), today_sched_passed, ads_sent_today)
    if need_immediate:
        logger.info("[sched] 定时点已过且今日未发 → 立即补偿触发")
    else:
        logger.info("[sched] 定时点未到或今日已发 → %d 分钟后首次补偿检查（不立即触发）",
                    RETRY_INTERVAL_MIN)
    logger.info("[sched] CDP 目标: %s", os.environ.get("FB_REPORT_CDP_URL", "（未设置，使用脚本默认值）"))
    logger.info("[sched] 广告脚本: %s", NEXUS_SCRIPT)
    logger.info("[sched] 事件脚本: %s", EVENTS_SCRIPT)
    logger.info("[sched] 事件任务延迟: %ds（广告任务完成后）", EVENTS_DELAY_SEC)

    scheduler = AsyncIOScheduler()

    # --- 主触发：每日定点 08:00 MYT ---
    scheduler.add_job(
        fb_report_task,
        CronTrigger(hour=sched_hour, minute=sched_minute, timezone=SCHED_TIMEZONE),
        id="fb_daily_trigger",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # --- 补偿触发：每 N 分钟检查（当日未成功时重试）---
    # need_immediate=True  → 08:00 已过且未发，立即补偿（进程重启追补场景）
    # need_immediate=False → 08:00 未到或已发，等一个间隔后再首检，避免启动就跑
    scheduler.add_job(
        fb_report_task,
        IntervalTrigger(minutes=RETRY_INTERVAL_MIN),
        id="fb_retry_compensation",
        next_run_time=first_compensation_time,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )

    scheduler.start()
    touch_fb_external_sched_hint()

    # 若用户传了 --run-now，启动后立即强制执行一次
    if run_now_immediately:
        logger.info("[sched] --run-now：立即强制触发一次（不受当日状态限制）。")
        await fb_report_task(force=True)

    try:
        while True:
            touch_fb_external_sched_hint()
            await asyncio.sleep(60)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("[sched] 收到退出信号，关闭调度器。")
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        clear_fb_external_sched_hint()


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


async def _run_once() -> None:
    """--once 模式：立即依次执行广告+事件任务，完成后退出。"""
    logger.info("[once] 立即执行广告数据 + 事件质量（--once 模式）。")
    await fb_report_task(force=True)
    ok = True  # 组合任务不阻塞退出，各任务状态已记录
    raise SystemExit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FB 广告日报调度守护（独立于 L3 进程）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--once",
        action="store_true",
        help="立即执行一次 fb_report_nexus 后退出（测试/手动补跑）",
    )
    mode_group.add_argument(
        "--run-now",
        action="store_true",
        dest="run_now",
        help="立即强制执行一次，然后继续守护（忽略当日已发标记）",
    )
    args = parser.parse_args()

    if args.once:
        asyncio.run(_run_once())
    else:
        asyncio.run(_run_daemon(run_now_immediately=args.run_now))


if __name__ == "__main__":
    main()
