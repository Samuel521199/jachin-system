#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
调度触发测试脚本

模拟「刚好到达触发时间」的场景：将触发时间设为「当前时间 + DELAY_SEC 秒」，
启动 APScheduler，等待一次触发完成后退出并报告结果。

用途
----
- 验证 CronTrigger + 时区（UTC+8 MYT）配置是否正确
- 验证广告数据、事件质量两个子任务都能被调度器正常拉起
- 验证 Ngrok CDP 链路此刻是否可用

用法
----
  python scripts/test_fb_scheduler_trigger.py            # 30 秒后触发
  python scripts/test_fb_scheduler_trigger.py --delay 10 # 10 秒后触发
  python scripts/test_fb_scheduler_trigger.py --ads-only  # 只跑广告，跳过事件
  python scripts/test_fb_scheduler_trigger.py --events-only # 只跑事件质量

注意
----
  本脚本会真实调用 fb_report_nexus.py 和 fb_events_quality.py，
  包括 CDP 浏览器操作和 Lark 消息推送。
  如需跳过 Lark 推送，在 fb_events_quality.py 加 --dry-run 时另行处理；
  本脚本专注于测试「调度器能否在正确时间触发」。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [trigger_test] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("trigger_test")

# ---------------------------------------------------------------------------
# 加载 fb_report_nexus.env（CDP URL、Lark 凭证等）
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(_SCRIPT_DIR / "fb_report_nexus.env")
_load_env(_SCRIPT_DIR / "fb_report_scheduler.env")

MYT = ZoneInfo("Asia/Kuala_Lumpur")

# ---------------------------------------------------------------------------
# 子进程执行
# ---------------------------------------------------------------------------


async def _run(script: str, tag: str) -> int:
    logger.info("[%s] 启动: %s", tag, script)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"          # 强制子进程 UTF-8 输出（解决 Windows GBK 乱码）
    env["PYTHONIOENCODING"] = "utf-8"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            logger.info("[%s] %s", tag, line)
    rc = await proc.wait()
    return rc


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------


async def run_test(delay_sec: int, ads_only: bool, events_only: bool) -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.date import DateTrigger

    now_myt = datetime.now(MYT)
    trigger_at = now_myt + timedelta(seconds=delay_sec)

    logger.info("=" * 60)
    logger.info("FB 调度触发测试")
    logger.info("当前时间  : %s MYT", now_myt.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("触发时间  : %s MYT（%d 秒后）", trigger_at.strftime("%Y-%m-%d %H:%M:%S"), delay_sec)
    logger.info("CDP 目标  : %s", os.environ.get("FB_REPORT_CDP_URL", "（未设置）"))
    logger.info("模式      : %s", "仅广告" if ads_only else "仅事件" if events_only else "广告 + 事件")
    logger.info("=" * 60)

    # 结果容器
    results: dict[str, int] = {}
    done_event = asyncio.Event()

    async def job() -> None:
        logger.info("🔔 调度器触发！开始执行任务...")

        if not events_only:
            nexus = str(_SCRIPT_DIR / "fb_report_nexus.py")
            rc_ads = await _run(nexus, "ads")
            results["ads"] = rc_ads
            logger.info("[ads] %s（退出码 %d）", "✅ 成功" if rc_ads == 0 else "❌ 失败", rc_ads)

        if not ads_only:
            if not events_only:
                logger.info("等待 10 秒后启动事件质量任务...")
                await asyncio.sleep(10)
            events = str(_SCRIPT_DIR / "fb_events_quality.py")
            rc_evt = await _run(events, "events")
            results["events"] = rc_evt
            logger.info("[events] %s（退出码 %d）", "✅ 成功" if rc_evt == 0 else "❌ 失败", rc_evt)

        done_event.set()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        job,
        DateTrigger(run_date=trigger_at, timezone=MYT),
        id="test_trigger",
        max_instances=1,
    )
    scheduler.start()

    logger.info("调度器已启动，等待触发（%d 秒）...", delay_sec)

    # 等待任务完成，超时为 delay_sec + 30 分钟（留足浏览器操作时间）
    timeout = delay_sec + 1800
    try:
        await asyncio.wait_for(done_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("❌ 超时（%d 秒），任务未完成！", timeout)
        scheduler.shutdown(wait=False)
        raise SystemExit(1)

    scheduler.shutdown(wait=False)

    # 汇总
    logger.info("")
    logger.info("=" * 60)
    logger.info("测试结果汇总")
    logger.info("=" * 60)
    all_ok = True
    for name, rc in results.items():
        status = "✅ 成功" if rc == 0 else f"❌ 失败（退出码 {rc}）"
        logger.info("  %-10s %s", name, status)
        if rc != 0:
            all_ok = False

    if not results:
        logger.warning("  无任务执行（检查 --ads-only / --events-only 参数）")

    logger.info("=" * 60)

    if all_ok and results:
        logger.info("🎉 所有任务通过！调度触发机制正常。")
    else:
        logger.error("部分任务失败，请检查上方日志。")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="测试调度器在指定秒数后触发抓取任务",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--delay", type=int, default=30, metavar="SEC",
        help="触发延迟秒数（默认 30 秒）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ads-only", action="store_true", help="只运行广告数据任务")
    mode.add_argument("--events-only", action="store_true", help="只运行事件质量任务")
    args = parser.parse_args()

    asyncio.run(run_test(args.delay, args.ads_only, args.events_only))


if __name__ == "__main__":
    main()
