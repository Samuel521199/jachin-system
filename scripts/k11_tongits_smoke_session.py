#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K11 冒烟 · Tongits 并行一局 + 协议结算金币记录（与统合 / 开门脚本解耦）。

设计：
- 在**独立浏览器页签**进 Tongits King 并启动 ``main_bot_loop``，不占用主冒烟页签。
- 后台 ``ResultMonitor`` HTTP 服务接收 ``tongits_result_monitor_snippet.js`` 转发的 3016 结算。
- 收尾时写入一条 Lark 结果行（备注含「游戏金币变化: 通过/失败」与逐人盈亏文案）。
- 任意步骤失败均 **不 raise**，由调用方追加 SKIP/FAIL 行；默认 **不计入** 统合退出码。

环境变量（可选）：
  K11_TONGITS_SMOKE=1           并入统合/开门冒烟时开启（默认 0；独立按钮见 test_k11_tongits_autoplay_smoke.py）
  K11_TONGITS_MONITOR_PORT      监控端口（默认 17889）
  K11_TONGITS_MY_NAME           结算里我方昵称（默认 victor）
  K11_TONGITS_ROUND_WAIT_SEC    收尾额外等待结算秒数（默认 90）
  K11_TONGITS_SKIP_BOT=1        不启动 main_bot_loop（仅协议监控）
  K11_TONGITS_SMOKE_AFFECT_EXIT=1  Tongits FAIL 也拉高统合退出码（默认 0）
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def tongits_smoke_enabled(args: Any | None = None) -> bool:
    if args is not None and bool(getattr(args, "skip_tongits_smoke", False)):
        return False
    return _env_bool("K11_TONGITS_SMOKE", False)


def tongits_affects_exit_code() -> bool:
    return _env_bool("K11_TONGITS_SMOKE_AFFECT_EXIT", False)


def _load_script_module(path: Path, unique_name: str) -> Any | None:
    """动态加载 scripts/*.py；须在 exec_module 前写入 sys.modules（@dataclass 等依赖）。"""
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(unique_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_coin_smoke_module() -> Any | None:
    return _load_script_module(
        SCRIPTS / "test_k11_game_open_coin_smoke.py",
        "k11_game_open_coin_smoke_embed",
    )


def _load_monitor_module() -> Any | None:
    return _load_script_module(
        SCRIPTS / "tongits_result_monitor.py",
        "tongits_result_monitor_embed",
    )


def _smoke_executed_at_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def tongits_safe_select_coins_join(
    page: Any,
    *,
    log: Callable[[str], None],
    mcp_mod: Any,
    budget_sec: float = 18.0,
) -> tuple[bool, str]:
    """
    在 Select Coins / One Round 弹层内点筹码 + Join。
    禁止「饱和打击」式全页 mouse 连点（易误触 YouTube subscribers 转化层）。
    """
    try:
        mcp_mod.register_kalaroko_popup_guardian(page.context)
    except Exception:
        pass

    def progress(msg: str) -> None:
        log(f"  [tongits·join] {msg}")

    await mcp_mod._dismiss_kalaroko_subscribers_modal(  # type: ignore[attr-defined]
        page, progress=progress, click_timeout_ms=4000
    )
    try:
        await mcp_mod._dismiss_kalaroko_blocking_promos(  # type: ignore[attr-defined]
            page, progress=progress, click_timeout_ms=3000, lobby_fast=True
        )
    except Exception:
        pass

    coin_title = re.compile(r"Select\s+Coins", re.I)
    one_round = re.compile(r"One\s+Round", re.I)
    join_pat = re.compile(r"^\s*Join\s*$", re.I)
    chip_pat = re.compile(r"^(100|200|500|1000|2000|5000|10000|20000|50000)$")

    deadline = time.monotonic() + max(3.0, float(budget_sec))
    last_note = "等待 Select Coins 弹层…"

    while time.monotonic() < deadline:
        try:
            await mcp_mod._dismiss_kalaroko_subscribers_modal(  # type: ignore[attr-defined]
                page, progress=None, click_timeout_ms=1500
            )
        except Exception:
            pass

        try:
            frames = [page.main_frame] + [
                f for f in page.frames if f != page.main_frame
            ]
        except Exception:
            frames = [page.main_frame]

        for fr in frames:
            try:
                has_coin = await fr.get_by_text(coin_title).count()
                has_round = await fr.get_by_text(one_round).count()
                if has_coin < 1 and has_round < 1:
                    continue

                fr_label = (fr.url or "main")[:72]
                log(f"  [tongits·join] 发现选币弹层 frame={fr_label}")

                chips = fr.get_by_text(chip_pat)
                chip_cnt = await chips.count()
                for i in range(min(chip_cnt, 8)):
                    chip = chips.nth(i)
                    try:
                        if await chip.is_visible(timeout=180):
                            await chip.click(timeout=900, force=True)
                            log("  [tongits·join] 已选筹码")
                            await page.wait_for_timeout(220)
                            break
                    except Exception:
                        continue

                jn = fr.get_by_role("button", name=join_pat)
                if await jn.count() > 0:
                    await jn.last.click(timeout=5000, force=True)
                    log("  [tongits·join] 已点击 Join（选币弹层内）")
                    await page.wait_for_timeout(600)
                    return True, "Select Coins 弹层 Join 已点击"

                jt = fr.get_by_text(join_pat)
                if await jt.count() > 0:
                    await jt.last.click(timeout=5000, force=True)
                    log("  [tongits·join] 已点击 Join 文本（选币弹层内）")
                    await page.wait_for_timeout(600)
                    return True, "Select Coins 弹层 Join 文本已点击"
            except Exception as exc:
                last_note = f"选币弹层尝试异常: {type(exc).__name__}"
                continue

        await page.wait_for_timeout(450)

    return False, last_note


def format_settlement_remark(settlement: dict[str, Any] | None, *, coin_ok: bool) -> str:
    """生成 Lark 备注：游戏金币变化 + 逐人盈亏（与 settlement.log 风格一致）。"""
    flag = "通过" if coin_ok else "失败"
    head = f"游戏金币变化: {flag}"
    if not settlement:
        return f"{head} | 未收到 3016 协议结算"
    my_delta = settlement.get("my_delta")
    outcome = settlement.get("outcome") or "UNKNOWN"
    cn = {"WIN": "胜", "LOSE": "负", "DRAW": "平", "UNKNOWN": "未知"}.get(str(outcome), str(outcome))
    game_no = settlement.get("game_no")
    my_txt = f"{int(my_delta):+d}" if my_delta is not None else "未知"
    opp_parts: list[str] = []
    for p in settlement.get("opponents") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("uid") or p.get("seat") or "?")
        d = p.get("delta")
        if d is None:
            opp_parts.append(name)
        else:
            opp_parts.append(f"{name} {int(d):+d}")
    opp_txt = " | ".join(opp_parts) if opp_parts else "-"
    net = settlement.get("net")
    wins = settlement.get("wins")
    losses = settlement.get("losses")
    draws = settlement.get("draws")
    tail = f"我方 {my_txt} | 对手 {opp_txt}"
    if net is not None:
        tail += f" ｜累计 净{int(net):+d}"
        if wins is not None and losses is not None:
            tail += f" 胜{wins} 负{losses}"
            if draws is not None:
                tail += f" 平{draws}"
    body = f"第{game_no}局 {cn} | {tail}" if game_no else tail
    msg_type = settlement.get("msg_type") or settlement.get("msgType") or "3016"
    return f"{head} | {body}（msgType={msg_type}）"


class TongitsSmokeSession:
    """并行 Tongits 一局：监控服务 + 游戏页签 + 可选 bot 子进程。"""

    def __init__(
        self,
        *,
        my_name: str,
        monitor_port: int,
        out_dir: Path,
        log: Callable[[str], None],
    ) -> None:
        self.my_name = my_name
        self.monitor_port = monitor_port
        self.out_dir = out_dir
        self.log = log
        self.started_at = time.monotonic()
        self.start_error: str | None = None
        self.game_page: Any | None = None
        self._bot_proc: subprocess.Popen[Any] | None = None
        self._monitor: Any | None = None
        self._http_server: ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._settle_event = threading.Event()
        self._last_settlement: dict[str, Any] | None = None
        self._inject_task: asyncio.Task[Any] | None = None
        self._stopped = False

    def _on_settlement(self, data: dict[str, Any]) -> None:
        mt = str(data.get("msg_type") or data.get("msgType") or "")
        if mt and mt != "3016":
            return
        self._last_settlement = dict(data)
        self._settle_event.set()

    def start_monitor_server(self) -> bool:
        mon_mod = _load_monitor_module()
        if mon_mod is None:
            self.start_error = "未找到 tongits_result_monitor.py"
            return False
        try:
            self._monitor = mon_mod.ResultMonitor(  # type: ignore[attr-defined]
                self.out_dir,
                self.my_name,
                discover=False,
                on_settlement=self._on_settlement,
            )
            handler = mon_mod._build_handler(self._monitor)  # type: ignore[attr-defined]
            self._http_server = ThreadingHTTPServer(
                ("127.0.0.1", self.monitor_port),
                handler,
            )
            self._http_thread = threading.Thread(
                target=self._http_server.serve_forever,
                name="k11-tongits-monitor",
                daemon=True,
            )
            self._http_thread.start()
            self.log(
                f"  [tongits] 结算监控已启动 127.0.0.1:{self.monitor_port} "
                f"（my_name={self.my_name!r}）"
            )
            return True
        except Exception as e:
            self.start_error = f"监控启动失败: {type(e).__name__}: {e}"
            self.log(f"  [tongits] {self.start_error}")
            return False

    def _spawn_bot(self) -> None:
        if _env_bool("K11_TONGITS_SKIP_BOT", False):
            self.log("  [tongits] 已跳过 main_bot_loop（K11_TONGITS_SKIP_BOT=1）")
            return
        script = SCRIPTS / "main_bot_loop.py"
        if not script.is_file():
            self.log("  [tongits] 未找到 main_bot_loop.py，跳过自动出牌")
            return
        env = os.environ.copy()
        env.setdefault("TONGITS_AUTO_PLAY_DRY_RUN", "0")
        env.setdefault("TONGITS_AUTO_PLAY", "1")
        try:
            self._bot_proc = subprocess.Popen(
                [sys.executable, str(script)],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.log(f"  [tongits] main_bot_loop 已后台启动 pid={self._bot_proc.pid}")
        except Exception as e:
            self.log(f"  [tongits] main_bot_loop 启动失败（不阻断冒烟）: {e}")

    async def _inject_snippet_all_frames(self, page: Any) -> None:
        snippet_path = SCRIPTS / "tongits_result_monitor_snippet.js"
        if not snippet_path.is_file():
            return
        js = snippet_path.read_text(encoding="utf-8")
        port = str(self.monitor_port)
        if "17889" in js and port != "17889":
            js = js.replace("17889", port)
        try:
            await page.context.add_init_script(js)
        except Exception:
            pass
        for fr in page.frames:
            try:
                await fr.evaluate(js)
            except Exception:
                pass

    async def _periodic_inject(self, page: Any) -> None:
        while not self._stopped:
            try:
                await self._inject_snippet_all_frames(page)
            except Exception:
                pass
            await asyncio.sleep(8.0)

    async def enter_tongits_on_page(self, page: Any, *, target_url: str) -> bool:
        """在当前页签全自动：大厅 → 点 Tongits → Join → 等进桌。"""
        coin_mod = _load_coin_smoke_module()
        if coin_mod is None:
            self.start_error = "未找到 test_k11_game_open_coin_smoke.py"
            self.log(f"  [tongits] {self.start_error}")
            return False
        mcp = coin_mod.mcp
        cases = getattr(coin_mod, "GAME_CASES", ())
        case = next((c for c in cases if getattr(c, "game_id", "") == "tongits_king"), None)
        if case is None:
            self.start_error = "coin_smoke 中无 tongits_king"
            self.log(f"  [tongits] {self.start_error}")
            return False
        home = getattr(coin_mod, "TARGET_HOME", target_url) or target_url

        def progress(msg: str) -> None:
            self.log(f"  [tongits·mcp] {msg}")

        try:
            self.game_page = page
            self.log(f"  [tongits] 打开站点 {home}")
            await mcp._goto_resilient(page, home, "domcontentloaded", 30_000)
            await mcp._prepare_kalaroko_lobby_after_navigation(page, progress=progress)
            self.log("  [tongits] 点击 Tongits King 入口…")
            clicked, click_note = await coin_mod._click_entry_with_fallback(  # type: ignore[attr-defined]
                page, case, progress=progress
            )
            if not clicked:
                if not await coin_mod._shell_or_canvas_present(page, case):  # type: ignore[attr-defined]
                    raise RuntimeError(click_note or "进 Tongits 失败")
            await page.wait_for_timeout(800)
            self.log("  [tongits] 等待 Select Coins 弹层并安全 Join（避开 YouTube 转化层）…")
            joined, join_note = await tongits_safe_select_coins_join(
                page, log=self.log, mcp_mod=mcp, budget_sec=18.0
            )
            if joined:
                self.log(f"  [tongits] Join 成功: {join_note}")
            else:
                self.log(f"  [tongits] Join 未确认: {join_note}（继续 deep_wait 兜底）")
            self.log("  [tongits] 等待进桌 / 开局…")
            t0 = time.perf_counter()
            ws_times: list[float] = []
            await mcp._game_deep_wait_after_goto(
                page, t0, timeout_ms=90_000, ws_times=ws_times, click_flow=True
            )
            await self._inject_snippet_all_frames(page)
            if self._inject_task is None:
                self._inject_task = asyncio.create_task(self._periodic_inject(page))
            self._spawn_bot()
            self.log("  [tongits] 已进桌，协议监控 + 自动出牌运行中")
            return True
        except Exception as e:
            self.start_error = f"开桌失败: {type(e).__name__}: {e}"
            self.log(f"  [tongits] {self.start_error}")
            return False

    async def open_game_tab(self, context: Any, *, target_url: str) -> bool:
        coin_mod = _load_coin_smoke_module()
        if coin_mod is None:
            self.start_error = "未找到 test_k11_game_open_coin_smoke.py"
            self.log(f"  [tongits] {self.start_error}")
            return False
        try:
            self.game_page = await context.new_page()
            return await self.enter_tongits_on_page(self.game_page, target_url=target_url)
        except Exception as e:
            self.start_error = f"开桌失败: {type(e).__name__}: {e}"
            self.log(f"  [tongits] {self.start_error}（主冒烟继续）")
            if self.game_page is not None:
                try:
                    await self.game_page.close()
                except Exception:
                    pass
                self.game_page = None
            return False

    def wait_for_settlement(self, timeout_sec: float) -> dict[str, Any] | None:
        if self._last_settlement:
            return self._last_settlement
        if timeout_sec > 0:
            self._settle_event.wait(timeout=timeout_sec)
        return self._last_settlement

    def build_result_row(self, *, extra_wait_sec: float) -> dict[str, Any]:
        settlement = self.wait_for_settlement(extra_wait_sec)
        coin_ok = bool(
            settlement
            and settlement.get("my_delta") is not None
            and str(settlement.get("msg_type") or "3016") == "3016"
        )
        if settlement and settlement.get("my_delta") is None:
            coin_ok = False
        if self.start_error and not settlement:
            verdict = "SKIP"
            verdict_zh = "跳过"
        elif coin_ok:
            verdict = "PASS"
            verdict_zh = "通过"
        else:
            verdict = "FAIL"
            verdict_zh = "失败"

        detail = format_settlement_remark(settlement, coin_ok=coin_ok)
        if self.start_error and not settlement:
            detail = f"{detail} | 开桌: {self.start_error}"
        elapsed = time.monotonic() - self.started_at
        detail += f" | 并行时长 {elapsed:.0f}s"

        return {
            "case": "tongits_smoke_coin",
            "tier": "Tongits",
            "case_title_zh": "Tongits King 一局金币",
            "verdict": verdict,
            "verdict_zh": verdict_zh,
            "detail": detail[:8000],
            "executed_at": _smoke_executed_at_now(),
            "tongits_settlement": settlement,
            "coin_change_ok": coin_ok,
        }

    async def stop(self) -> None:
        self._stopped = True
        if self._inject_task is not None:
            self._inject_task.cancel()
            try:
                await self._inject_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self.game_page is not None:
            try:
                await self.game_page.close()
            except Exception:
                pass
            self.game_page = None
        if self._bot_proc is not None:
            try:
                self._bot_proc.terminate()
            except Exception:
                pass
            self._bot_proc = None
        if self._http_server is not None:
            try:
                self._http_server.shutdown()
            except Exception:
                pass
            self._http_server = None


async def tongits_smoke_start(
    context: Any,
    log: Callable[[str], None],
    *,
    target_url: str,
) -> TongitsSmokeSession | None:
    """启动并行 Tongits（失败返回 None 或部分启动的 session，不 raise）。"""
    my_name = (os.environ.get("K11_TONGITS_MY_NAME") or "victor").strip()
    port = int((os.environ.get("K11_TONGITS_MONITOR_PORT") or "17889").strip() or "17889")
    out_dir = SCRIPTS / "omnioutput"
    out_dir.mkdir(parents=True, exist_ok=True)

    log("———————— 并行：Tongits 一局 + 协议金币监控 ————————")
    session = TongitsSmokeSession(
        my_name=my_name,
        monitor_port=port,
        out_dir=out_dir,
        log=log,
    )
    if not session.start_monitor_server():
        return session
    await session.open_game_tab(context, target_url=target_url)
    return session


async def tongits_smoke_finalize_and_append(
    session: TongitsSmokeSession | None,
    results: list[dict[str, Any]],
    log: Callable[[str], None],
) -> None:
    if session is None:
        return
    log("")
    log("———————— Tongits 并行局 · 收尾采集 ————————")
    extra_wait = _env_float("K11_TONGITS_ROUND_WAIT_SEC", 90.0)
    try:
        row = session.build_result_row(extra_wait_sec=extra_wait)
        results.append(row)
        log(
            f"  [tongits] 并入 {row['case']!r} → {row['verdict_zh']}（{row['verdict']}）"
        )
        log(f"  [tongits] 备注: {row['detail'][:320]}")
    except Exception as e:
        log(f"  [tongits] 收尾异常（已记 SKIP，不阻断）: {type(e).__name__}: {e}")
        results.append(
            {
                "case": "tongits_smoke_coin",
                "tier": "Tongits",
                "case_title_zh": "Tongits King 一局金币",
                "verdict": "SKIP",
                "verdict_zh": "跳过",
                "detail": f"游戏金币变化: 失败 | 收尾异常: {e}",
                "executed_at": _smoke_executed_at_now(),
            }
        )
    finally:
        try:
            await session.stop()
        except Exception:
            pass


def filter_results_for_exit_code(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if tongits_affects_exit_code():
        return results
    return [r for r in results if r.get("case") != "tongits_smoke_coin"]


K11_DEFAULT_LARK_WIKI_URL = (
    "https://ssgkm409t6q5.sg.larksuite.com/wiki/ZyWlwhdW1iNQuykvy7qlw93sgTe"
)


def send_tongits_lark_notification(
    row: dict[str, Any],
    *,
    target_url: str,
    log: Callable[[str], None],
    no_lark: bool = False,
    lark_wiki_url: str = "",
) -> None:
    """独立 Tongits 冒烟：只发一条结算行到 Lark 卡片。"""
    if no_lark:
        log("  [lark] 已跳过：--no-lark-report")
        return
    lark_path = SCRIPTS / "k11_lark_smoke_report.py"
    mod = _load_script_module(lark_path, "k11_lark_smoke_report_tongits_embed")
    if mod is None:
        log(f"  [lark] 未找到或无法加载 {lark_path}，跳过通知")
        return
    try:
        from l3_node.packaged_lark_env import apply_packaged_lark_to_os_environ

        apply_packaged_lark_to_os_environ()
    except Exception:
        pass
    wiki = (
        (lark_wiki_url or "").strip()
        or (os.environ.get("K11_SMOKE_LARK_WIKI_URL") or "").strip()
        or K11_DEFAULT_LARK_WIKI_URL
    )
    v = str(row.get("verdict") or "SKIP").upper()
    vzh = str(row.get("verdict_zh") or ("通过" if v == "PASS" else "失败"))
    lark_row = {
        "tier": str(row.get("tier") or "Tongits"),
        "case": str(row.get("case") or "tongits_autoplay_smoke"),
        "case_title_zh": str(row.get("case_title_zh") or "Tongits 自动打牌金币"),
        "verdict": v,
        "verdict_zh": vzh,
        "detail": str(row.get("detail") or "")[:8000],
    }
    app_id = (os.environ.get("K11_SMOKE_LARK_APP_ID") or "").strip()
    app_secret = (os.environ.get("K11_SMOKE_LARK_APP_SECRET") or "").strip()
    chat_id = (os.environ.get("K11_SMOKE_LARK_NOTIFY_CHAT_ID") or "").strip()
    try:
        mod.send_k11_smoke_lark_notification(  # type: ignore[attr-defined]
            results=[lark_row],
            target_url=target_url,
            wiki_url=wiki,
            lark_wrote=0,
            app_id=app_id,
            app_secret=app_secret,
            chat_id=chat_id,
            log=log,
        )
        log("  [lark] Tongits 金币结算卡片已发送")
    except Exception as e:
        log(f"  [lark] 发送异常（不阻断本地结果）: {e}")

