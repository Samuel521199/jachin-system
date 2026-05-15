#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FB 广告日报调度守护进程（独立于 L3 进程）

职责
----
1. 每天定时（默认本地 09:00）触发 fb_report_nexus.py，抓取广告数据并推送 Lark。
2. 若当次失败，每隔 ``FB_SCHED_RETRY_INTERVAL_MIN`` 分钟自动补偿重试，直到成功或当日
   重试次数超过 ``FB_SCHED_MAX_RETRIES``（此后发送 Lark 告警并停止当日自动重试）。
3. 状态持久化至 ``~/.jachin/data/.last_fb_report_date.txt``；进程重启后可断点续策。

本机直连优化
-----------
当部署机器就是广告账号所在机器时，Chrome 以 ``--remote-debugging-port=9223`` 启动，
不需要 Ngrok 中间层。将 ``FB_REPORT_CDP_URL`` 设为 ``http://127.0.0.1:9223`` 即可
让 fb_report_nexus.py 直接走本机 CDP，消除跨洋 Ngrok 延迟（每条 CDP 指令省去一次
跨境 RTT，大幅提升稳定性与速度）。

用法
----
  python scripts/fb_report_scheduler.py            # 前台守护，持续运行
  python scripts/fb_report_scheduler.py --once     # 立即执行一次，完成后退出（测试/手动补跑）
  python scripts/fb_report_scheduler.py --run-now  # 立即触发，之后继续守护

关键环境变量（亦可写入 scripts/fb_report_scheduler.env，格式同 .env.example）
  FB_SCHED_TIME               每日触发时间（本地），格式 HH:MM，默认 09:00
  FB_SCHED_RETRY_INTERVAL_MIN 失败后重试间隔（分钟），默认 15
  FB_SCHED_MAX_RETRIES        每日最大重试次数（不含首次），默认 10
  FB_SCHED_ALERT_ON_EXHAUST   重试耗尽后是否发 Lark 告警，默认 1（开启）
  FB_SCHED_LARK_ALERT_RECEIVER  告警接收方（open_id/chat_id），与 Lark 设置一致
  FB_SCHED_NEXUS_SCRIPT       fb_report_nexus.py 绝对/相对路径，默认同目录下

  以下变量**透传**给 fb_report_nexus.py（无需重复设置，在 fb_report_nexus.env 里配置即可）：
  FB_REPORT_CDP_URL           本机直连推荐 http://127.0.0.1:9223
  FB_REPORT_PRESET            抓取区间，默认 yesterday（日报推荐）
  LARK_APP_ID / LARK_APP_SECRET / LARK_RECEIVER_ID / LARK_RECEIVE_ID_TYPE
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

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
SCHED_TIME_STR: str = _env_str("FB_SCHED_TIME", "09:00")
# 失败重试间隔（分钟）
RETRY_INTERVAL_MIN: int = _env_int("FB_SCHED_RETRY_INTERVAL_MIN", 15)
# 每日最大重试次数（不含首次）
MAX_RETRIES: int = _env_int("FB_SCHED_MAX_RETRIES", 10)
# 重试耗尽是否发告警
ALERT_ON_EXHAUST: bool = _env_bool("FB_SCHED_ALERT_ON_EXHAUST", True)

# fb_report_nexus.py 路径
_NEXUS_SCRIPT_DEFAULT = str(_SCRIPT_DIR / "fb_report_nexus.py")
NEXUS_SCRIPT: str = _env_str("FB_SCHED_NEXUS_SCRIPT", _NEXUS_SCRIPT_DEFAULT)

# 状态文件
_STATE_DIR = Path.home() / ".jachin" / "data"
_LAST_SENT_FILE = _STATE_DIR / ".last_fb_report_date.txt"

# ---------------------------------------------------------------------------
# 每日重试计数（进程内状态；重启后从状态文件推断）
# ---------------------------------------------------------------------------

_retry_lock: asyncio.Lock | None = None
_daily_attempts: int = 0  # 当日已尝试次数（首次 + 重试）
_exhausted_today: bool = False  # 当日已宣告耗尽，停止自动重试

# ---------------------------------------------------------------------------
# 状态文件读写
# ---------------------------------------------------------------------------


def _read_last_sent_date() -> str:
    """返回上次成功发送的日期字符串（YYYY-MM-DD），或空字符串。"""
    try:
        return _LAST_SENT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_last_sent_date(d: str) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        _LAST_SENT_FILE.write_text(d, encoding="utf-8")
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


async def _run_nexus_subprocess() -> int:
    """
    以子进程运行 fb_report_nexus.py，返回退出码（0 = 成功）。

    子进程继承当前环境变量，因此 FB_REPORT_CDP_URL、FB_REPORT_PRESET、
    LARK_* 等透传无需额外处理。
    """
    cmd = [sys.executable, NEXUS_SCRIPT]
    logger.info("[nexus] 启动子进程: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ.copy(),
    )

    # 实时转发输出
    assert proc.stdout is not None
    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if line:
            logger.info("[nexus] %s", line)

    rc = await proc.wait()
    logger.info("[nexus] 子进程退出码: %d", rc)
    return rc


# ---------------------------------------------------------------------------
# 调度任务
# ---------------------------------------------------------------------------


async def _get_retry_lock() -> asyncio.Lock:
    global _retry_lock
    if _retry_lock is None:
        _retry_lock = asyncio.Lock()
    return _retry_lock


async def fb_report_task(*, force: bool = False) -> bool:
    """
    尝试执行一次 fb_report_nexus，更新状态。
    返回 True 表示本次执行成功（已发送）。

    Parameters
    ----------
    force : bool
        若为 True，忽略当日已成功标记，强制重新执行（用于 --run-now）。
    """
    global _daily_attempts, _exhausted_today

    lock = await _get_retry_lock()
    async with lock:
        today = _today_str()

        # 跨天重置
        last_sent = _read_last_sent_date()
        if last_sent != today:
            _daily_attempts = 0
            _exhausted_today = False

        # 今天已成功
        if last_sent == today and not force:
            logger.info("[sched] 今日报告已发送（%s），跳过。", today)
            return True

        # 当日已宣告耗尽（非强制）
        if _exhausted_today and not force:
            logger.info("[sched] 今日重试已耗尽，不再自动重试。")
            return False

        # 超过重试上限
        if _daily_attempts > MAX_RETRIES and not force:
            _exhausted_today = True
            logger.error(
                "[sched] 今日已尝试 %d 次（上限 %d），宣告耗尽。",
                _daily_attempts,
                MAX_RETRIES + 1,
            )
            if ALERT_ON_EXHAUST:
                await _send_lark_alert(
                    f"⚠️ **FB 广告日报发送失败**\n\n"
                    f"今日（{today}）已尝试 **{_daily_attempts} 次**，全部失败，"
                    f"已停止自动重试。\n\n请手动检查：\n"
                    f"- Chrome 9223 是否正在运行\n"
                    f"- FB Ads Manager 登录状态\n"
                    f"- Lark 机器人权限\n\n"
                    f"手动补跑：`python scripts/fb_report_scheduler.py --once`"
                )
            return False

        _daily_attempts += 1
        attempt_label = f"第 {_daily_attempts} 次（上限 {MAX_RETRIES + 1}）"
        logger.info("[sched] 开始执行 %s ...", attempt_label)

        try:
            rc = await _run_nexus_subprocess()
        except Exception as e:
            logger.exception("[sched] 子进程异常: %s", e)
            rc = 99

        if rc == 0:
            _write_last_sent_date(today)
            _exhausted_today = False
            logger.info("[sched] ✅ 发送成功（%s，%s）", today, attempt_label)
            return True
        else:
            logger.warning(
                "[sched] ❌ 本次失败（退出码 %d，%s），等待下次补偿重试（间隔 %d 分钟）。",
                rc,
                attempt_label,
                RETRY_INTERVAL_MIN,
            )
            return False


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
    logger.warning("[sched] FB_SCHED_TIME 格式无效（%r），使用默认 09:00", time_str)
    return 9, 0


async def _run_daemon(run_now_immediately: bool = False) -> None:
    """启动 APScheduler 守护，直到进程被杀。"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    sched_hour, sched_minute = _parse_sched_time(SCHED_TIME_STR)
    now_utc = datetime.now(timezone.utc)

    logger.info(
        "[sched] 启动守护 | 每日触发 %02d:%02d（本地）| 重试间隔 %d 分钟 | 最大重试 %d 次",
        sched_hour,
        sched_minute,
        RETRY_INTERVAL_MIN,
        MAX_RETRIES,
    )
    logger.info("[sched] CDP 目标: %s", os.environ.get("FB_REPORT_CDP_URL", "（未设置，nexus 默认值）"))
    logger.info("[sched] nexus 脚本: %s", NEXUS_SCRIPT)

    scheduler = AsyncIOScheduler()

    # --- 主触发：每日定点 ---
    scheduler.add_job(
        fb_report_task,
        CronTrigger(hour=sched_hour, minute=sched_minute),
        id="fb_daily_trigger",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # --- 补偿触发：每 N 分钟检查（当日未成功时重试）---
    scheduler.add_job(
        fb_report_task,
        IntervalTrigger(minutes=RETRY_INTERVAL_MIN),
        id="fb_retry_compensation",
        next_run_time=now_utc,  # 启动时立即扫一遍（如重启补偿）
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )

    scheduler.start()

    # 若用户传了 --run-now，启动后立即强制执行一次
    if run_now_immediately:
        logger.info("[sched] --run-now：立即强制触发一次（不受当日状态限制）。")
        await fb_report_task(force=True)

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("[sched] 收到退出信号，关闭调度器。")
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


async def _run_once() -> None:
    """--once 模式：立即执行一次，成功则退出 0，失败则退出 1。"""
    logger.info("[once] 立即执行 fb_report_nexus（--once 模式）。")
    ok = await fb_report_task(force=True)
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
