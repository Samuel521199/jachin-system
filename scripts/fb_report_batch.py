#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FB 广告数据批量回填工具

对指定日期范围内的每一天，逐日调用 fb_report_nexus.py 抓取广告数据并推送 Lark。
抓取完所有日期后，发送一条汇总消息（成功/跳过/失败明细）。

用法
----
  # 回填 5.1 ~ 5.14 每天（含两端）
  python scripts/fb_report_batch.py --start 2026-05-01 --end 2026-05-14

  # 仅抓昨天（等价于日常日报手动触发）
  python scripts/fb_report_batch.py --start yesterday --end yesterday

  # 控制每日之间等待时间（避免 Facebook 限速）
  python scripts/fb_report_batch.py --start 2026-05-01 --end 2026-05-14 --delay 60

  # 跳过已有 CSV 的日期（断点续跑）
  python scripts/fb_report_batch.py --start 2026-05-01 --end 2026-05-14 --skip-existing

环境变量（亦可写入 fb_report_scheduler.env，格式同 .env.example）
  FB_BATCH_DATE_START    起始日期（YYYY-MM-DD / yesterday / today），与 --start 等价
  FB_BATCH_DATE_END      结束日期，与 --end 等价
  FB_BATCH_DELAY_SEC     每两次抓取之间的等待秒数，默认 30
  FB_BATCH_MAX_RETRIES   单日最大重试次数（不含首次），默认 2
  FB_BATCH_SKIP_EXISTING 已有同日 CSV 时跳过，默认 0（不跳过，重新抓）

  其余变量透传给 fb_report_nexus.py（FB_REPORT_CDP_URL、LARK_* 等）。

输出
----
  - 每日 CSV 落盘（路径由 fb_report_nexus.py 决定，通常在脚本同目录）
  - Lark 批量汇总消息（成功 / 跳过 / 失败各几天，附失败日期列表）
  - 本地日志（标准输出）
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import logging
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [fb_batch] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fb_report_batch")

# ---------------------------------------------------------------------------
# 配置加载（从 fb_report_scheduler.env 或 fb_report_nexus.env 读取公共配置）
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


# scheduler env 优先（含 CDP URL 等），再读 nexus env（含 Lark 凭证）
_load_env_file(_SCRIPT_DIR / "fb_report_scheduler.env")
_load_env_file(_SCRIPT_DIR / "fb_report_nexus.env")


def _env_int(key: str, default: int) -> int:
    try:
        return int((os.environ.get(key) or "").strip() or default)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    return raw in ("1", "true", "yes", "on") if raw else default


DELAY_SEC: int = _env_int("FB_BATCH_DELAY_SEC", 30)
MAX_RETRIES: int = _env_int("FB_BATCH_MAX_RETRIES", 2)
SKIP_EXISTING: bool = _env_bool("FB_BATCH_SKIP_EXISTING", False)
NEXUS_SCRIPT: str = str(_SCRIPT_DIR / "fb_report_nexus.py")

# ---------------------------------------------------------------------------
# 日期解析
# ---------------------------------------------------------------------------


def _parse_date(s: str) -> date:
    """解析 YYYY-MM-DD / yesterday / today。"""
    s = s.strip().lower()
    today = date.today()
    if s == "today":
        return today
    if s == "yesterday":
        return today - timedelta(days=1)
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"日期格式错误：{s!r}（支持 YYYY-MM-DD / today / yesterday）"
        )


def _date_range(start: date, end: date) -> list[date]:
    """返回 [start, end] 闭区间内每一天（含两端）。"""
    if start > end:
        raise ValueError(f"起始日期 {start} 晚于结束日期 {end}")
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# 已有 CSV 检测（用于 --skip-existing）
# ---------------------------------------------------------------------------


def _has_existing_csv(target_date: date) -> bool:
    """检查当前目录是否已有含该日期的 CSV 文件。"""
    date_str = target_date.strftime("%Y-%m-%d")
    pattern = str(_SCRIPT_DIR / f"fb_ads_report_{date_str}*.csv")
    return bool(glob.glob(pattern))


# ---------------------------------------------------------------------------
# 单日执行
# ---------------------------------------------------------------------------


class DayResult(NamedTuple):
    date: date
    status: str   # "ok" | "skip" | "fail"
    attempts: int
    note: str


async def _run_one_day(target_date: date) -> DayResult:
    """对单个日期执行 fb_report_nexus，返回结果。"""
    date_str = target_date.strftime("%Y-%m-%d")

    if SKIP_EXISTING and _has_existing_csv(target_date):
        logger.info("[%s] 已有 CSV，跳过（--skip-existing）。", date_str)
        return DayResult(target_date, "skip", 0, "已有文件")

    env = os.environ.copy()
    env["FB_REPORT_PRESET"] = date_str  # 单日绝对日期

    last_rc = -1
    for attempt in range(1, MAX_RETRIES + 2):  # 1 次首跑 + MAX_RETRIES 次重试
        logger.info("[%s] 第 %d 次尝试...", date_str, attempt)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, NEXUS_SCRIPT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                logger.info("[%s] %s", date_str, line)
        last_rc = await proc.wait()
        if last_rc == 0:
            logger.info("[%s] ✅ 成功（第 %d 次）", date_str, attempt)
            return DayResult(target_date, "ok", attempt, "")
        logger.warning("[%s] ❌ 失败（退出码 %d，第 %d 次）", date_str, last_rc, attempt)
        if attempt <= MAX_RETRIES:
            logger.info("[%s] %d 秒后重试...", date_str, DELAY_SEC)
            await asyncio.sleep(DELAY_SEC)

    return DayResult(target_date, "fail", MAX_RETRIES + 1, f"退出码={last_rc}")


# ---------------------------------------------------------------------------
# Lark 汇总消息
# ---------------------------------------------------------------------------


async def _send_batch_summary(results: list[DayResult]) -> None:
    """通过 fb_report_nexus 的 Lark 配置发送批量汇总。"""
    ok = [r for r in results if r.status == "ok"]
    skip = [r for r in results if r.status == "skip"]
    fail = [r for r in results if r.status == "fail"]

    total = len(results)
    if not total:
        return

    start_d = results[0].date.strftime("%Y-%m-%d")
    end_d = results[-1].date.strftime("%Y-%m-%d")

    lines = [
        f"📊 **FB 广告数据批量回填完成**",
        f"区间：{start_d} ~ {end_d}（共 {total} 天）\n",
        f"✅ 成功：{len(ok)} 天",
    ]
    if skip:
        lines.append(f"⏭️ 跳过：{len(skip)} 天（已有文件）")
    if fail:
        lines.append(f"❌ 失败：{len(fail)} 天")
        lines.append("失败日期：" + "、".join(r.date.strftime("%m-%d") for r in fail))
        lines.append("\n手动补跑（逐日）：")
        for r in fail:
            lines.append(
                f"  `FB_REPORT_PRESET={r.date} python scripts/fb_report_nexus.py`"
            )

    md = "\n".join(lines)
    logger.info("[summary]\n%s", md)

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("fb_report_nexus", NEXUS_SCRIPT)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            send_fn = getattr(mod, "_send_lark_text_message", None)
            if send_fn:
                await send_fn(md)
                logger.info("[summary] Lark 汇总已发送。")
    except Exception as e:
        logger.warning("[summary] Lark 汇总发送失败（不影响本地结果）: %s", e)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def run_batch(start: date, end: date) -> int:
    days = _date_range(start, end)
    logger.info(
        "批量回填：%s ~ %s，共 %d 天 | 延迟 %ds | 最大重试 %d 次 | 跳过已有=%s",
        start, end, len(days), DELAY_SEC, MAX_RETRIES, SKIP_EXISTING,
    )
    logger.info("CDP: %s | 脚本: %s", os.environ.get("FB_REPORT_CDP_URL", "（nexus默认）"), NEXUS_SCRIPT)

    results: list[DayResult] = []
    for i, day in enumerate(days):
        result = await _run_one_day(day)
        results.append(result)

        # 日期之间等待（最后一天不等）
        if i < len(days) - 1 and result.status != "skip":
            logger.info("等待 %d 秒后处理下一天...", DELAY_SEC)
            await asyncio.sleep(DELAY_SEC)

    # 汇总
    ok_count = sum(1 for r in results if r.status == "ok")
    fail_count = sum(1 for r in results if r.status == "fail")
    logger.info("完成：成功 %d / 失败 %d / 总计 %d", ok_count, fail_count, len(results))

    await _send_batch_summary(results)

    return 1 if fail_count > 0 else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    global DELAY_SEC, MAX_RETRIES, SKIP_EXISTING

    parser = argparse.ArgumentParser(
        description="FB 广告数据批量回填（逐日调用 fb_report_nexus.py）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--start",
        default=os.environ.get("FB_BATCH_DATE_START", ""),
        metavar="YYYY-MM-DD",
        help="起始日期（含），支持 yesterday / today（默认取环境变量 FB_BATCH_DATE_START）",
    )
    parser.add_argument(
        "--end",
        default=os.environ.get("FB_BATCH_DATE_END", ""),
        metavar="YYYY-MM-DD",
        help="结束日期（含），支持 yesterday / today（默认取环境变量 FB_BATCH_DATE_END）",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=DELAY_SEC,
        metavar="SEC",
        help=f"每日之间等待秒数，默认 {DELAY_SEC}",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=SKIP_EXISTING,
        help="跳过当前目录已有同日 CSV 的日期（断点续跑）",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        metavar="N",
        help=f"单日最大重试次数（不含首次），默认 {MAX_RETRIES}",
    )

    args = parser.parse_args()

    if not args.start or not args.end:
        parser.error("必须通过 --start 和 --end 或环境变量 FB_BATCH_DATE_START / FB_BATCH_DATE_END 指定日期范围。")

    # 覆盖全局（支持 CLI 覆盖 env）
    DELAY_SEC = args.delay
    MAX_RETRIES = args.max_retries
    SKIP_EXISTING = args.skip_existing

    try:
        start_date = _parse_date(args.start)
        end_date = _parse_date(args.end)
    except (argparse.ArgumentTypeError, ValueError) as e:
        parser.error(str(e))
        return

    rc = asyncio.run(run_batch(start_date, end_date))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
