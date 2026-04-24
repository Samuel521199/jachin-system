#!/usr/bin/env python3
"""
混沌工程：验证 Kalaroko MCP 在 CDP（9222）断连后的 OS 级 Chrome 复活 + 二次 connect_over_cdp。

运行（仓库根目录）::

    python scripts/test_chrome_revival_chaos.py

依赖：本机已安装 Google Chrome（或可执行路径由 CHROME_EXECUTABLE_PATH 指定）、
``pip install playwright`` 且 ``playwright install chromium``（仅用于 async_playwright 入口；
实际被测路径会拉起 **系统 Chrome** 并 CDP 连接）。

前置：建议在 .env 中配置与日常巡检一致的 ``CHROME_USER_DATA_DIR`` / ``CHROME_EXECUTABLE_PATH``，
避免复活时使用错误 profile 触发 SingletonLock。
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import signal
import sys
from pathlib import Path

# —— 仓库根加入 sys.path，便于导入 l3_client ——
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import subprocess  # noqa: E402

_DEFAULT_CDP_PORT = 9222


def _subprocess_run_kw() -> dict:
    kw: dict = {"timeout": 45}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kw


def _is_chrome_family_image(name: str) -> bool:
    n = (name or "").lower().strip()
    if not n:
        return False
    # 覆盖常见 Chromium 系可执行名（CDP 调试端口通常由其一占用）
    return any(
        x in n
        for x in (
            "chrome",
            "chromium",
            "msedge",
            "brave",
            "vivaldi",
        )
    )


def _windows_listeners_pids(port: int) -> set[int]:
    pids: set[int] = set()
    try:
        cp = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_subprocess_run_kw(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        print(f"⚠️  Phase 1: netstat 不可用或失败（忽略）: {e}")
        return pids

    for line in (cp.stdout or "").splitlines():
        line_l = line.strip()
        if not line_l or not line_l.upper().startswith("TCP"):
            continue
        # 英文 LISTENING / 简写 LISTEN；部分本地化系统仍可能混用
        if not re.search(r"\b(LISTENING|LISTEN)\b", line_l, re.I):
            continue
        if f":{port}" not in line_l:
            continue
        m = re.search(r"\b(\d+)\s*$", line_l)
        if m:
            try:
                pids.add(int(m.group(1)))
            except ValueError:
                continue
    return pids


def _windows_task_image_name(pid: int) -> str | None:
    try:
        cp = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_subprocess_run_kw(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    raw = (cp.stdout or "").strip()
    if not raw:
        return None
    first = raw.splitlines()[0]
    try:
        row = next(csv.reader(io.StringIO(first)))
    except StopIteration:
        return None
    if not row:
        return None
    return (row[0] or "").strip().lower()


def _windows_kill_pid(pid: int) -> bool:
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_subprocess_run_kw(),
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        print(f"⚠️  Phase 1: taskkill PID={pid} 失败（忽略）: {e}")
        return False


def _posix_listeners_pids_and_comm(port: int) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        cp = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        print("ℹ️  Phase 1: 未找到 lsof（常见于精简镜像）；跳过按端口查杀。")
        return out
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"⚠️  Phase 1: lsof 失败（忽略）: {e}")
        return out

    lines = (cp.stdout or "").strip().splitlines()
    if not lines:
        return out
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        comm = (parts[0] or "").strip()
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        out.append((pid, comm.lower()))
    return out


def _posix_kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError) as e:
        print(f"⚠️  Phase 1: kill -9 PID={pid} 跳过: {e}")


def _kill_port_9222_process(port: int = _DEFAULT_CDP_PORT) -> None:
    """
    物理抹杀：尽力终止监听 ``port`` 的 **Chromium 系** 进程。
    未找到、无权限或平台命令缺失时 **静默继续**，不抛异常阻断 Phase 2。
    """
    print(f"\n{'=' * 60}\n💥 Phase 1 — 物理抹杀：扫描 TCP:{port} 上的 LISTEN 进程…\n{'=' * 60}")

    if sys.platform == "win32":
        pids = _windows_listeners_pids(port)
        if not pids:
            print(
                f"ℹ️  Phase 1: 未发现监听 {port} 的 TCP 会话（或 netstat 无输出）。\n"
                f"   提示：若你正用「普通 Chrome」玩游戏，该窗口通常 **没有** --remote-debugging-port；\n"
                f"   Phase 2 会再拉起 **第二个** 带 CDP 的 Chrome（独立 user-data-dir），与当前窗口并存。\n"
                f"   直接进入 Phase 2。\n"
            )
            return

        killed_any = False
        for pid in sorted(pids):
            img = _windows_task_image_name(pid)
            if img and not _is_chrome_family_image(img):
                print(
                    f"🛡️  Phase 1: 跳过 PID={pid}（{img} 非 Chromium 系，避免误杀其它服务）"
                )
                continue
            label = img or f"pid_{pid}"
            print(f"🔪 Phase 1: 正在 taskkill /F /PID {pid}（{label}）…")
            if _windows_kill_pid(pid):
                killed_any = True

        if killed_any:
            print(
                f"\n💥 灾难模拟：已强行切断 {port} 端口侧监听进程（Chrome/Chromium 系），"
                "原 CDP 会话应已失效！\n"
            )
        else:
            print(
                f"ℹ️  Phase 1: 发现端口占用但未执行 Chrome 族查杀（或已全部跳过）。继续 Phase 2。\n"
            )
        return

    # macOS / Linux / *BSD
    rows = _posix_listeners_pids_and_comm(port)
    if not rows:
        print(
            f"ℹ️  Phase 1: 未发现监听 {port} 的进程。\n"
            f"   提示：零售 Chrome 与 CDP 调试实例是不同进程；Phase 2 将尝试复活 CDP 实例。\n"
            f"   直接进入 Phase 2。\n"
        )
        return

    killed_any = False
    for pid, comm in rows:
        if not _is_chrome_family_image(comm):
            print(f"🛡️  Phase 1: 跳过 PID={pid}（{comm} 非 Chromium 系）")
            continue
        print(f"🔪 Phase 1: 正在 kill -9 {pid}（{comm}）…")
        _posix_kill_pid(pid)
        killed_any = True

    if killed_any:
        print(
            f"\n💥 灾难模拟：已强行切断 {port} 端口，Chrome/Chromium 监听进程已死！\n"
        )
    else:
        print(f"ℹ️  Phase 1: 无 Chromium 系监听者可杀。继续 Phase 2。\n")


async def _chaos_main() -> int:
    port = int(os.environ.get("KALAROKO_CDP_CHAOS_PORT") or str(_DEFAULT_CDP_PORT))
    os.environ["KALAROKO_CDP_ENDPOINT"] = f"http://127.0.0.1:{port}"
    os.environ["KALAROKO_CDP_REVIVE_ON_CONNECT_FAIL"] = "1"
    # 与已打开的无调试 Chrome 并存：跳过「先绑 CHROME_USER_DATA_DIR」档（常 SingletonLock 秒退），直接独立 profile
    os.environ["KALAROKO_CDP_REVIVE_TRY_CONFIGURED_PROFILE_FIRST"] = "0"
    os.environ.setdefault("KALAROKO_CDP_REVIVE_WAIT_SEC", "0")
    # 每档 /json/version 轮询上限（秒）；复活最多两档 → 总墙钟约 2× 该值
    os.environ.setdefault("KALAROKO_CDP_REVIVE_READY_TIMEOUT_SEC", "16")
    os.environ.setdefault("KALAROKO_CDP_POST_REVIVE_CONNECT_ATTEMPTS", "3")
    os.environ.setdefault("KALAROKO_CDP_POST_REVIVE_CONNECT_GAP_MS", "400")
    os.environ.setdefault("KALAROKO_CDP_POST_REVIVE_CONNECT_TIMEOUT_SEC", "14")
    os.environ.setdefault("KALAROKO_CDP_CONNECT_TIMEOUT_SEC", "15")

    _kill_port_9222_process(port)
    # 给 OS 释放端口一点时间（非必须，但可降低偶发「地址仍在 TIME_WAIT」噪声）
    await asyncio.sleep(1.2)

    print(
        f"{'=' * 60}\n"
        f"⚡ Phase 2 — 死者苏生：首次 CDP 失败 → OS 拉起 **独立 profile** 的 Chrome → /json/version 就绪 → connect_over_cdp\n"
        f"{'=' * 60}"
    )

    from playwright.async_api import async_playwright

    from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
        _launch_kalaroko_browser_context,
        _load_repo_dotenv_for_kalaroko_monitor,
    )

    _load_repo_dotenv_for_kalaroko_monitor()

    browser = None
    context = None
    page = None
    must_close = True

    try:
        async with async_playwright() as p:
            browser, context, page, must_close = await _launch_kalaroko_browser_context(
                p,
                viewport_width=390,
                viewport_height=844,
                device_scale_factor=2.0,
                headless=True,
                preferred_host="kalaroko.com",
            )

            print(f"\n{'=' * 60}\n👑 Phase 3 — 王者验证：CDP 统治力自检…\n{'=' * 60}")

            connected = getattr(browser, "is_connected", True)
            if callable(connected):
                ok_conn = bool(connected())
            else:
                ok_conn = bool(connected)
            if not ok_conn:
                raise AssertionError("browser 未处于已连接状态（is_connected 为假）")

            print("✅ browser.is_connected → True")

            await page.goto(
                "https://kalaroko.com/",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
            title = await page.evaluate("() => document.title")
            print(f"✅ page.goto + evaluate(document.title) → {title!r}")

            if must_close:
                raise AssertionError(
                    "must_close_context 为 True：说明走了 Playwright 自建 launch 兜底，"
                    "未证明 CDP 复活主路径（期望 CDP 复用用户 context → False）。"
                )
            print("✅ must_close_context == False（确认为 CDP 复用路径，非临时 Chromium launch）")

    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception as e:
                print(f"⚠️  收尾 browser.close 忽略: {e}")

    print(f"\n{'=' * 60}\n🏆 Phase 4 — 混沌测试通过：Chrome 死者苏生机制按设计运行！\n{'=' * 60}\n")
    return 0


def main() -> None:
    try:
        raise SystemExit(asyncio.run(_chaos_main()))
    except AssertionError as e:
        print(f"\n❌ 混沌测试失败（断言）: {e}\n", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        print("\n⏹️  用户中断。\n", file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f"\n❌ 混沌测试异常: {e}\n", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
