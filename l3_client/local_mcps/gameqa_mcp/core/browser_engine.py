"""
Playwright：自治（无头）/ 影子（有头）+ CDP 共享。

- 首轮 launch 时使用 ``--remote-debugging-port``，并将 ``http://127.0.0.1:<port>`` 写入
  ``$GAMEQA_DATA_DIR/cdp_http.txt``，便于 **L3 HTTP** 与 **MCP stdio** 共用同一 Chromium。
- 第二轮（另一进程）：优先 ``connect_over_cdp``（``GAMEQA_CDP_URL`` / ``KALAROKO_CDP_ENDPOINT`` / ``cdp_http.txt``）附着，
  默认调试端口与 ``scripts/launch_chrome_debug.ps1`` 一致（**9222**），不重开隐身实例。
- 默认 Playwright 视口为竖屏 **360×760**（``GAMEQA_VIEWPORT_WIDTH`` / ``GAMEQA_VIEWPORT_HEIGHT`` 可覆盖）；附着已有 Chrome 时对当前页 ``set_viewport_size`` 以对齐 H5 尺寸。
"""
from __future__ import annotations

import asyncio
import logging
import os
import traceback
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

logger = logging.getLogger("gameqa.browser_engine")
# filelock 阻塞获取时默认每 0.05s 打 DEBUG，控制台易「刷重影」；常规仅保留 WARNING+
logging.getLogger("filelock").setLevel(logging.WARNING)

EmitFn = Optional[Callable[[str], Awaitable[None]]]


async def _emit_line(emit: EmitFn, line: str) -> None:
    """同时写 Python logging（L3 stderr）与可选 SSE 行。"""
    logger.info("%s", line)
    if emit:
        await emit(line)


async def _apply_gameqa_viewport_to_page(emit: EmitFn, page: Any) -> None:
    """附着到已有 Chrome 时强制与默认/环境变量一致的逻辑视口（便于与 mock 视觉坐标对齐）。"""
    if not page:
        return
    vp = gameqa_playwright_viewport()
    try:
        await page.set_viewport_size(vp)
        await _emit_line(
            emit,
            f"[gameqa][browser] set_viewport_size width={vp['width']} height={vp['height']}",
        )
    except Exception as e:
        await _emit_line(
            emit,
            f"[gameqa][browser] set_viewport_size skipped ({type(e).__name__}: {e!r})",
        )


def _snapshot_relevant_env() -> list[str]:
    keys = (
        "GAMEQA_DATA_DIR",
        "GAMEQA_CDP_URL",
        "KALAROKO_CDP_ENDPOINT",
        "GAMEQA_FORCE_NEW_BROWSER",
        "GAMEQA_REMOTE_DEBUG_HOST",
        "GAMEQA_REMOTE_DEBUG_PORT",
        "GAMEQA_VIEWPORT_WIDTH",
        "GAMEQA_VIEWPORT_HEIGHT",
        "GAMEQA_LAUNCH_LOCK_TIMEOUT_S",
        "GAMEQA_LAUNCH_LOCK_POLL_S",
        "GAMEQA_CLEAR_STALE_BROWSER_LOCK",
        "GAMEQA_STALE_LOCK_MAX_AGE_S",
        "GAMEQA_YOLO_MODEL",
        "GAMEQA_OCR_ENABLED",
        "GAMEQA_OCR_MAX_CHARS",
        "GAMEQA_REFRESH_SOFT_RELOAD",
        "GAMEQA_REFRESH_SETTLE_MS",
    )
    out: list[str] = []
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        out.append(f"[gameqa][env] {k}={v!r}" if v else f"[gameqa][env] {k}=(unset)")
    return out


async def _page_debug_line(emit: EmitFn, page: Any, tag: str) -> None:
    if not page:
        await _emit_line(emit, f"[gameqa][browser] {tag}: page=None")
        return
    try:
        u = page.url
        await _emit_line(emit, f"[gameqa][browser] {tag}: page.url={u!r}")
    except Exception as e:
        await _emit_line(
            emit,
            f"[gameqa][browser] {tag}: cannot read page.url: {type(e).__name__}: {e!r}",
        )


CDP_HTTP_FILE = "cdp_http.txt"
BROWSER_LAUNCH_LOCK = "gameqa_browser.launch.lock"


def gameqa_data_dir() -> Path:
    raw = os.environ.get("GAMEQA_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".gameqa_mcp").resolve()


def _maybe_clear_stale_browser_launch_lock(lock_path: str) -> None:
    """
    减轻「僵尸」残留：在环境变量显式开启且锁文件过旧时尝试删除。

    - ``GAMEQA_CLEAR_STALE_BROWSER_LOCK=1`` / ``true`` / ``yes``
    - ``GAMEQA_STALE_LOCK_MAX_AGE_S``：秒，默认 180；仅当锁文件 mtime 超过该值才 unlink。

    若仍有进程持有系统层文件锁，删除可能失败；此时需结束进程或手动删锁。
    """
    flag = (os.environ.get("GAMEQA_CLEAR_STALE_BROWSER_LOCK") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return
    try:
        import time

        p = Path(lock_path)
        if not p.is_file():
            return
        try:
            max_age = float(os.environ.get("GAMEQA_STALE_LOCK_MAX_AGE_S", "180"))
        except ValueError:
            max_age = 180.0
        max_age = max(30.0, max_age)
        age = time.time() - p.stat().st_mtime
        if age < max_age:
            return
        p.unlink()
        logger.warning(
            "[gameqa][browser] 已按 GAMEQA_CLEAR_STALE_BROWSER_LOCK 删除过旧 launch 锁文件 "
            "age=%.1fs max_age=%.1fs path=%s",
            age,
            max_age,
            lock_path,
        )
    except OSError as e:
        logger.info("[gameqa][browser] stale launch lock 清理未生效: %s", e)


def _normalize_cdp_http(url: str) -> str:
    return (url or "").strip().rstrip("/")


def remote_debug_http() -> tuple[str, int]:
    """当前使用的 CDP HTTP 基底，默认与 ``scripts/launch_chrome_debug.ps1`` / ``KALAROKO_CDP_ENDPOINT`` 一致（9222）。"""
    port = int(os.environ.get("GAMEQA_REMOTE_DEBUG_PORT", "9222").strip())
    host = os.environ.get("GAMEQA_REMOTE_DEBUG_HOST", "127.0.0.1").strip() or "127.0.0.1"
    return (f"http://{host}:{port}", port)


def gameqa_playwright_viewport() -> dict[str, int]:
    """
    Playwright ``new_context(viewport=…)`` / ``page.set_viewport_size`` 使用的 CSS 视口。
    默认竖屏 **360×760**（H5 / 手机向）；可用 ``GAMEQA_VIEWPORT_WIDTH`` / ``GAMEQA_VIEWPORT_HEIGHT`` 覆盖。
    """
    try:
        w = int((os.environ.get("GAMEQA_VIEWPORT_WIDTH") or "360").strip())
    except ValueError:
        w = 360
    try:
        h = int((os.environ.get("GAMEQA_VIEWPORT_HEIGHT") or "760").strip())
    except ValueError:
        h = 760
    if w < 16:
        w = 360
    if h < 16:
        h = 760
    return {"width": w, "height": h}


def explicit_cdp_url() -> str:
    """直接指定附着地址；优先 ``GAMEQA_CDP_URL``，其次 ``KALAROKO_CDP_ENDPOINT``（与 K11 / launch_chrome_debug 共用 .env）。"""
    for key in ("GAMEQA_CDP_URL", "KALAROKO_CDP_ENDPOINT"):
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return ""


def cdp_env_source_label() -> str:
    """日志用：当前显式 CDP 来自哪条环境变量（无则空字符串）。"""
    if (os.environ.get("GAMEQA_CDP_URL") or "").strip():
        return "GAMEQA_CDP_URL"
    if (os.environ.get("KALAROKO_CDP_ENDPOINT") or "").strip():
        return "KALAROKO_CDP_ENDPOINT"
    return ""


def endpoint_file_read() -> str:
    p = gameqa_data_dir() / CDP_HTTP_FILE
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace").strip()
    except Exception as e:
        logger.debug("[gameqa] read cdp file: %s", e)
    return ""


def endpoint_file_write(url: str) -> None:
    d = gameqa_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / CDP_HTTP_FILE).write_text(url.strip() + "\n", encoding="utf-8")


def endpoint_file_clear() -> None:
    try:
        p = gameqa_data_dir() / CDP_HTTP_FILE
        if p.is_file():
            p.unlink()
    except Exception:
        pass


def _cdp_tab_url_driver_safe(url: str) -> bool:
    """与 ``test_k11_unified_platform_smoke_playwright._cdp_tab_url_driver_safe`` 一致。"""
    u = (url or "").strip().lower()
    if u.startswith("devtools://") or u.startswith("chrome-devtools://"):
        return False
    if u.startswith("chrome-extension://") or u.startswith("moz-extension://"):
        return False
    if u.startswith("ms-browser-extension://"):
        return False
    return True


def _norm_url_for_bfcache_compare(s: str) -> str:
    """同文档 BFCache 判定用：scheme/host 小写、去尾斜杠、忽略 fragment。"""
    raw = (s or "").strip()
    if not raw:
        return ""
    try:
        p = urlparse(raw)
        path = p.path or ""
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        netloc = (p.netloc or "").lower()
        scheme = (p.scheme or "").lower()
        return urlunparse((scheme, netloc, path, p.params, p.query, ""))
    except Exception:
        return raw.lower().rstrip("/")


def _urls_same_for_bfcache(a: str, b: str) -> bool:
    return _norm_url_for_bfcache_compare(a) == _norm_url_for_bfcache_compare(b)


def _url_ok_for_cold_refresh(url: str) -> bool:
    """HTTP(S) 真实页：可做 ``about:blank`` → ``goto`` 冷导航；排除扩展/DevTools/空白页。"""
    if not _cdp_tab_url_driver_safe(url):
        return False
    u = (url or "").strip().lower()
    if not u or u == "about:blank" or u.startswith("about:"):
        return False
    if u in ("chrome://newtab/", "chrome://newtab"):
        return False
    return u.startswith("http://") or u.startswith("https://")


def _refresh_settle_ms() -> int:
    try:
        v = int((os.environ.get("GAMEQA_REFRESH_SETTLE_MS") or "600").strip())
    except ValueError:
        v = 600
    return max(0, v)


async def _robust_page_goto(
    page: Any,
    url: str,
    *,
    emit: EmitFn = None,
    tag: str = "goto",
    settle_ms: int | None = None,
) -> None:
    """
    抄写 ``_robust_goto_kalaroko_home``：SPA/CDP 下 ``domcontentloaded`` 可能卡住时的多段策略 + 短轮重试。
    """
    if settle_ms is None:
        settle_ms = _refresh_settle_ms()
    last: BaseException | None = None
    for round_i in range(3):
        for phase, tmo, extra in (
            ("domcontentloaded", 90_000, None),
            ("commit", 22_000, "dcl"),
        ):
            try:
                if extra == "dcl":
                    await page.goto(url, wait_until="commit", timeout=tmo)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=75_000)
                    except Exception:
                        await page.wait_for_timeout(1800)
                else:
                    await page.goto(url, wait_until=phase, timeout=tmo)
                await _emit_line(
                    emit,
                    f"[gameqa][browser] {tag} ok wait_until={phase!r} round={round_i + 1}",
                )
                if settle_ms > 0:
                    await page.wait_for_timeout(settle_ms)
                return
            except Exception as e:
                last = e
                await _emit_line(
                    emit,
                    f"[gameqa][browser] {tag} round={round_i + 1} {phase!r} failed: "
                    f"{type(e).__name__}: {e!r}",
                )
        if round_i < 2:
            await _emit_line(
                emit,
                f"[gameqa][browser] {tag} backoff {(1.0 + round_i * 0.5):.1f}s before next round",
            )
            await page.wait_for_timeout(int(1000 + 500 * round_i))
    if last is not None:
        raise last
    raise RuntimeError(f"{tag}: unknown navigation failure")


async def _robust_page_reload(
    page: Any,
    *,
    emit: EmitFn = None,
    tag: str = "reload",
    settle_ms: int | None = None,
) -> None:
    """与 ``_robust_page_goto`` 相同阶梯，用于 ``page.reload``。"""
    if settle_ms is None:
        settle_ms = _refresh_settle_ms()
    last: BaseException | None = None
    for round_i in range(3):
        for phase, tmo, extra in (
            ("domcontentloaded", 90_000, None),
            ("commit", 22_000, "dcl"),
        ):
            try:
                if extra == "dcl":
                    await page.reload(wait_until="commit", timeout=tmo)
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=75_000)
                    except Exception:
                        await page.wait_for_timeout(1800)
                else:
                    await page.reload(wait_until=phase, timeout=tmo)
                await _emit_line(
                    emit,
                    f"[gameqa][browser] {tag} ok wait_until={phase!r} round={round_i + 1}",
                )
                if settle_ms > 0:
                    await page.wait_for_timeout(settle_ms)
                return
            except Exception as e:
                last = e
                await _emit_line(
                    emit,
                    f"[gameqa][browser] {tag} round={round_i + 1} {phase!r} failed: "
                    f"{type(e).__name__}: {e!r}",
                )
        if round_i < 2:
            await page.wait_for_timeout(int(1000 + 500 * round_i))
    if last is not None:
        raise last
    raise RuntimeError(f"{tag}: unknown reload failure")


async def _cold_blank_then_goto(
    page: Any,
    url: str,
    emit: EmitFn,
    *,
    tag: str,
) -> None:
    """
    统合冒烟弱网段的思路：同址 ``goto`` 常走 BFCache；先 ``about:blank`` 再 ``goto`` 强制走网络与完整导航。
    """
    await _emit_line(emit, f"[gameqa][browser] {tag}: about:blank (cold tab nav) → goto")
    try:
        await page.goto("about:blank", wait_until="commit", timeout=15_000)
    except Exception as e:
        await _emit_line(
            emit,
            f"[gameqa][browser] {tag}: about:blank non-fatal: {type(e).__name__}: {e!r}",
        )
    await _robust_page_goto(page, url, emit=emit, tag=f"{tag}_goto")


class BrowserEngine:
    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._headless: bool = True
        self._shadow_mode: bool = False
        self._shadow_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._binding_installed: bool = False
        self._owns_browser_process: bool = False

    @property
    def page(self) -> Any:
        return self._page

    @property
    def cdp_http(self) -> str:
        if explicit_cdp_url():
            return explicit_cdp_url()
        return endpoint_file_read() or (remote_debug_http()[0] if self._owns_browser_process else "")

    @property
    def owns_browser_process(self) -> bool:
        return self._owns_browser_process

    async def close(self, *, discard_shared_endpoint_file: bool | None = None) -> None:
        """
        断开 Playwright。
        ``discard_shared_endpoint_file``：True 时在 **本进程曾拥有浏览器进程** 时删掉 cdp_http.txt，
        False 永不删文件；默认 None ⇒ 仅在 ``_owns_browser_process`` 时为 True。
        """
        discard = discard_shared_endpoint_file
        owned_here = self._owns_browser_process
        if discard is None:
            discard = owned_here

        try:
            if self._context:
                await self._context.close()
        except Exception as e:
            logger.warning("context.close: %s", e)
        try:
            if self._browser:
                await self._browser.close()
        except Exception as e:
            logger.warning("browser.close: %s", e)
        try:
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            logger.warning("playwright.stop: %s", e)
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._binding_installed = False
        self._shadow_mode = False
        if discard and owned_here:
            endpoint_file_clear()
        self._owns_browser_process = False

    async def _with_launch_lock(self, coro_factory: Any) -> Any:
        d = gameqa_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        lock_path = str(d / BROWSER_LAUNCH_LOCK)
        _maybe_clear_stale_browser_launch_lock(lock_path)
        lock = FileLock(lock_path, timeout=-1)

        try:
            poll = float(os.environ.get("GAMEQA_LAUNCH_LOCK_POLL_S", "1.0"))
        except ValueError:
            poll = 1.0
        if poll < 0.05:
            poll = 0.05
        try:
            tout = float(os.environ.get("GAMEQA_LAUNCH_LOCK_TIMEOUT_S", "90"))
        except ValueError:
            tout = 90.0
        if tout <= 0:
            tout = 90.0

        def _acquire() -> None:
            logger.info(
                "[gameqa][browser] 等待 launch 互斥锁（最长 %.0fs，轮询 %.2fs）: %s",
                tout,
                poll,
                lock_path,
            )
            try:
                lock.acquire(timeout=tout, poll_interval=poll)
            except FileLockTimeout:
                logger.error(
                    "[gameqa][browser] launch 锁超时 (%ss): %s — "
                    "请确认无其他 GameQA/ Python 任务占用；可结束进程后删除该 .lock，"
                    "或设置 GAMEQA_CLEAR_STALE_BROWSER_LOCK=1（并可选调低 GAMEQA_STALE_LOCK_MAX_AGE_S）后重试",
                    tout,
                    lock_path,
                )
                raise

        def _release() -> None:
            try:
                lock.release(force=True)
            except Exception as _rel_e:
                logger.warning("[gameqa][browser] launch 锁 release 异常（可能导致后续 acquire 超时）: %s", _rel_e)

        await asyncio.to_thread(_acquire)
        try:
            return await coro_factory()
        finally:
            await asyncio.to_thread(_release)

    async def _try_connect_over_cdp(
        self,
        endpoint: str,
        url: str,
        *,
        shadow: bool,
        on_shadow_click: Callable[[dict[str, Any]], Awaitable[None]] | None,
        emit: EmitFn = None,
        attempt_label: str = "",
    ) -> bool:
        label = f" label={attempt_label!r}" if attempt_label else ""
        await _emit_line(
            emit,
            (
                f"[gameqa][browser] connect_over_cdp TRY{label} endpoint={endpoint!r} "
                f"nav={(url.strip() if url else '(skip goto)')!r} shadow={shadow} "
                f"connect_timeout_ms=15000 goto_timeout_ms=60000"
            ),
        )
        try:
            from playwright.async_api import async_playwright

            await _emit_line(emit, "[gameqa][browser] playwright async_playwright().start() …")
            self._pw = await async_playwright().start()
            await _emit_line(
                emit,
                f"[gameqa][browser] chromium.connect_over_cdp({endpoint!r}) …",
            )
            self._browser = await self._pw.chromium.connect_over_cdp(
                endpoint,
                timeout=15_000,
            )
            self._owns_browser_process = False
            contexts = list(self._browser.contexts or [])
            await _emit_line(
                emit,
                f"[gameqa][browser] CDP connected: browser.contexts count={len(contexts)}",
            )
            if not contexts:
                _vp = gameqa_playwright_viewport()
                await _emit_line(
                    emit,
                    f"[gameqa][browser] no existing context → new_context viewport={_vp['width']}x{_vp['height']}",
                )
                self._context = await self._browser.new_context(
                    viewport=_vp,
                    ignore_https_errors=True,
                )
            else:
                self._context = contexts[0]
                await _emit_line(emit, "[gameqa][browser] reusing first existing browser context[0]")
            pages = list(self._context.pages or [])
            await _emit_line(
                emit,
                f"[gameqa][browser] context.pages count={len(pages)}",
            )
            if pages:
                self._page = pages[0]
                await _emit_line(emit, "[gameqa][browser] reusing first existing page[0]")
            else:
                await _emit_line(emit, "[gameqa][browser] no pages → new_page()")
                self._page = await self._context.new_page()
            await _apply_gameqa_viewport_to_page(emit, self._page)
            await _page_debug_line(emit, self._page, "page BEFORE goto")
            if url and url.strip():
                await _emit_line(
                    emit,
                    f"[gameqa][browser] page.goto wait_until=domcontentloaded timeout_ms=60000 url={url!r}",
                )
                await self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            else:
                await _emit_line(emit, "[gameqa][browser] skip page.goto (empty url)")
            await _page_debug_line(emit, self._page, "page AFTER goto")
            if shadow:
                await _emit_line(
                    emit,
                    "[gameqa][browser] shadow mode: expose_binding + click sniffer …",
                )
                await self._install_shadow_bridge(on_shadow_click)
                await self._inject_click_sniffer()
            await _emit_line(
                emit,
                f"[gameqa][browser] connect_over_cdp OK{label} endpoint={endpoint!r} owns_process=False",
            )
            return True
        except Exception as e:
            tb = traceback.format_exc()
            flat = " ⏎ ".join(tb.strip().splitlines())
            if len(flat) > 3200:
                flat = flat[:3200] + " …[traceback truncated]"
            await _emit_line(
                emit,
                f"[gameqa][browser] connect_over_cdp FAIL{label} endpoint={endpoint!r} "
                f"type={type(e).__name__} err={e!r}",
            )
            cause = getattr(e, "__cause__", None)
            if cause is not None:
                await _emit_line(
                    emit,
                    f"[gameqa][browser] __cause__ type={type(cause).__name__} err={cause!r}",
                )
            await _emit_line(emit, f"[gameqa][browser] traceback: {flat}")
            await self.close(discard_shared_endpoint_file=False)
            return False

    async def _launch_new_chromium(
        self,
        url: str,
        *,
        headless: bool,
        shadow: bool,
        on_shadow_click: Callable[[dict[str, Any]], Awaitable[None]] | None,
        emit: EmitFn = None,
    ) -> str:
        from playwright.async_api import async_playwright

        cdp_http, port = remote_debug_http()
        await _emit_line(
            emit,
            (
                f"[gameqa][browser] launch NEW Chromium: headless={headless} shadow={shadow} "
                f"remote_debug_port={port} cdp_http={cdp_http!r} "
                f"(from GAMEQA_REMOTE_DEBUG_HOST/PORT)"
            ),
        )
        launch_args = (
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            f"--remote-debugging-port={port}",
        )
        await _emit_line(
            emit,
            f"[gameqa][browser] chromium.launch args={launch_args!r}",
        )
        await _emit_line(emit, "[gameqa][browser] playwright async_playwright().start() …")
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=headless,
            args=launch_args,
        )
        self._owns_browser_process = True
        out_file = gameqa_data_dir() / CDP_HTTP_FILE
        endpoint_file_write(cdp_http)
        await _emit_line(
            emit,
            f"[gameqa][browser] wrote CDP endpoint {cdp_http!r} → {str(out_file)!r}",
        )
        _vp = gameqa_playwright_viewport()
        await _emit_line(
            emit,
            f"[gameqa][browser] new_context viewport={_vp['width']}x{_vp['height']} …",
        )
        self._context = await self._browser.new_context(
            viewport=_vp,
            ignore_https_errors=True,
        )
        self._page = await self._context.new_page()
        await _page_debug_line(emit, self._page, "new page BEFORE shadow/goto")
        if shadow:
            await _emit_line(emit, "[gameqa][browser] shadow: expose_binding + click sniffer (before goto) …")
            await self._install_shadow_bridge(on_shadow_click)
            await self._inject_click_sniffer()
        await _emit_line(
            emit,
            f"[gameqa][browser] page.goto domcontentloaded url={url!r}",
        )
        await self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await _page_debug_line(emit, self._page, "page AFTER goto")
        await _emit_line(
            emit,
            "[gameqa][browser] launch NEW Chromium complete owns_process=True",
        )
        return (
            f"launched url={url!r} headless={headless} shadow={shadow} "
            f"cdp={cdp_http} (written to {out_file})"
        )

    async def launch(
        self,
        url: str,
        *,
        headless: bool,
        shadow: bool,
        on_shadow_click: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        emit: EmitFn = None,
    ) -> str:
        """启动或附着：先尝试 CDP 附着，失败再本进程 launch（带调试端口）。"""

        force_new = (os.environ.get("GAMEQA_FORCE_NEW_BROWSER") or "").strip() in (
            "1",
            "true",
            "yes",
        )

        async def _body() -> str:
            lock_path = str(gameqa_data_dir() / BROWSER_LAUNCH_LOCK)
            await _emit_line(
                emit,
                f"[gameqa][browser] ========== launch/attach 开始（文件锁生效） lock={lock_path!r} ==========",
            )
            for ln in _snapshot_relevant_env():
                await _emit_line(emit, ln)
            gd = gameqa_data_dir()
            cdp_path = gd / CDP_HTTP_FILE
            await _emit_line(
                emit,
                f"[gameqa][browser] paths data_dir={str(gd)!r} cdp_file={str(cdp_path)!r} exists={cdp_path.is_file()}",
            )
            ep_file = endpoint_file_read()
            if ep_file:
                await _emit_line(
                    emit,
                    f"[gameqa][browser] cdp_http.txt content (stripped): {ep_file!r}",
                )
            else:
                await _emit_line(emit, "[gameqa][browser] cdp_http.txt 无可用内容（缺失或为空）")
            env_ep = explicit_cdp_url()
            def_http, def_port = remote_debug_http()
            env_gameqa_only = (os.environ.get("GAMEQA_CDP_URL") or "").strip()
            env_kalaroko = (os.environ.get("KALAROKO_CDP_ENDPOINT") or "").strip()
            await _emit_line(
                emit,
                (
                    f"[gameqa][browser] 附着优先级: GAMEQA_CDP_URL={(env_gameqa_only or '(unset)')!r} "
                    f"KALAROKO_CDP_ENDPOINT={(env_kalaroko or '(unset)')!r} "
                    f"→ 合并显式 endpoint={(env_ep or '(none)')!r}；文件={(ep_file or '(none)')!r}；"
                    f"本机默认 {def_http!r} port={def_port}"
                ),
            )
            await _emit_line(
                emit,
                f"[gameqa][browser] GAMEQA_FORCE_NEW_BROWSER → skip_attach={force_new}",
            )
            await _emit_line(
                emit,
                f"[gameqa][browser] 目标 navigate_url={url!r} headless={headless} shadow={shadow}",
            )
            await _emit_line(
                emit,
                f"[gameqa][browser] 清理本进程旧会话 close(discard_shared_endpoint_file={force_new}) …",
            )
            await self.close(discard_shared_endpoint_file=force_new)

            gameqa_cdp_only = (os.environ.get("GAMEQA_CDP_URL") or "").strip()
            ep = explicit_cdp_url() or ep_file
            if not force_new and ep:
                await _emit_line(
                    emit,
                    f"[gameqa][browser] CDP 尝试 #1（环境或 cdp_http.txt） endpoint={ep!r}",
                )
                ok = await self._try_connect_over_cdp(
                    ep,
                    url,
                    shadow=shadow,
                    on_shadow_click=on_shadow_click,
                    emit=emit,
                    attempt_label="env_or_cdp_file",
                )
                if ok:
                    self._headless = headless
                    self._shadow_mode = shadow
                    await _emit_line(
                        emit,
                        "[gameqa][browser] ========== 结束：CDP 附着成功 (#1) ==========",
                    )
                    return f"attached over CDP url={url!r} endpoint={ep!r} shadow={shadow}"
                if gameqa_cdp_only:
                    await _emit_line(
                        emit,
                        "[gameqa][browser] CDP #1 失败且已显式设置 GAMEQA_CDP_URL → 不回退其它口，将进入 launch",
                    )
                elif ep_file and not env_ep:
                    def_try, _ = remote_debug_http()
                    if _normalize_cdp_http(ep_file) != _normalize_cdp_http(def_try):
                        await _emit_line(
                            emit,
                            (
                                f"[gameqa][browser] CDP #1 失败：cdp_http.txt 端口与仓库默认调试口不一致 "
                                f"（文件={ep_file!r} 默认={def_try!r}），尝试 #2 默认口以附着 launch_chrome_debug.ps1 等启动的 Chrome"
                            ),
                        )
                        ok2 = await self._try_connect_over_cdp(
                            def_try,
                            url,
                            shadow=shadow,
                            on_shadow_click=on_shadow_click,
                            emit=emit,
                            attempt_label="default_after_stale_cdp_file",
                        )
                        if ok2:
                            self._headless = headless
                            self._shadow_mode = shadow
                            await _emit_line(
                                emit,
                                "[gameqa][browser] ========== 结束：CDP 附着成功 (#2 回退默认口) ==========",
                            )
                            return (
                                f"attached over CDP url={url!r} endpoint={def_try!r} "
                                f"(after stale cdp_http.txt {ep_file!r}) shadow={shadow}"
                            )
                    await _emit_line(
                        emit,
                        "[gameqa][browser] CDP #1 失败（已与默认调试口相同）→ 将进入 launch",
                    )
                else:
                    await _emit_line(
                        emit,
                        "[gameqa][browser] "
                        "CDP #1 失败（显式 endpoint 来自 KALAROKO_CDP_ENDPOINT 或已与默认相同）→ 将进入 launch",
                    )

            if not force_new and not ep:
                cdp_http, _port = remote_debug_http()
                await _emit_line(
                    emit,
                    f"[gameqa][browser] CDP 尝试 #2（本机默认调试口，无显式 endpoint） endpoint={cdp_http!r}",
                )
                ok = await self._try_connect_over_cdp(
                    cdp_http,
                    url,
                    shadow=shadow,
                    on_shadow_click=on_shadow_click,
                    emit=emit,
                    attempt_label="default_local_debug_port",
                )
                if ok:
                    self._headless = headless
                    self._shadow_mode = shadow
                    await _emit_line(
                        emit,
                        "[gameqa][browser] ========== 结束：CDP 附着成功 (#2) ==========",
                    )
                    return f"attached over default CDP port url={url!r} endpoint={cdp_http!r} shadow={shadow}"

            await _emit_line(
                emit,
                "[gameqa][browser] CDP 均不可用或跳过 → chromium.launch + --remote-debugging-port …",
            )
            await self.close(discard_shared_endpoint_file=True)
            self._headless = headless
            self._shadow_mode = shadow
            msg = await self._launch_new_chromium(
                url,
                headless=headless,
                shadow=shadow,
                on_shadow_click=on_shadow_click,
                emit=emit,
            )
            await _emit_line(
                emit,
                "[gameqa][browser] ========== 结束：本进程新开 Chromium ==========",
            )
            return msg

        return await self._with_launch_lock(_body)

    async def attach_if_endpoint_available(
        self,
        *,
        shadow: bool = False,
        on_shadow_click: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        emit: EmitFn = None,
    ) -> bool:
        """
        无 URL 导航，仅尝试附着到已存在的共享 Chromium（供 MCP 在未 launch 时调用 semantic/execute）。
        """
        if self._page:
            await _emit_line(emit, "[gameqa][browser] attach_if: 已有活跃 page，跳过")
            return True
        ep = explicit_cdp_url() or endpoint_file_read()
        if not ep:
            cdp_http, p = remote_debug_http()
            ep = cdp_http
            await _emit_line(
                emit,
                f"[gameqa][browser] attach_if: 无显式 CDP/file，fallback 默认 {ep!r} port={p}",
            )
        else:
            src = cdp_env_source_label() or "cdp_http.txt"
            await _emit_line(
                emit,
                f"[gameqa][browser] attach_if: endpoint={ep!r} source={src!r}",
            )
        ok = await self._try_connect_over_cdp(
            ep,
            "",
            shadow=shadow,
            on_shadow_click=on_shadow_click,
            emit=emit,
            attempt_label="attach_if",
        )
        if ok:
            await _emit_line(emit, "[gameqa][browser] attach_if: 成功")
        else:
            await _emit_line(emit, "[gameqa][browser] attach_if: 失败（无共享 Chromium 或端口不可达）")
        return ok

    async def screenshot_png(self) -> bytes:
        if not self._page:
            raise RuntimeError("browser not launched")
        return await self._page.screenshot(type="png")

    async def refresh_current_page(self, url: str = "", *, emit: EmitFn = None) -> str:
        """
        在**已有** Playwright 页上刷新：**不**走 ``launch`` / launch 文件锁。

        对齐 ``scripts/test_k11_unified_platform_smoke_playwright.py``：
        - **goto**：``_robust_goto_kalaroko_home`` 式多段 ``wait_until`` + 短轮重试；
        - **同址 / 当前标签“硬刷新”**：``about:blank``（``commit``）→ 再 ``goto``，减轻 BFCache / SPA 假死（弱网段注释同款）。

        - ``url`` 非空：``robust goto``；若与当前 URL 等价且为 HTTP(S)，则 **冷导航** 再 ``goto``。
        - ``url`` 空：默认对当前 HTTP(S) 页 **冷导航**；否则 ``robust reload``。
        - ``GAMEQA_REFRESH_SOFT_RELOAD=1``：空 ``url`` 时仅 ``robust reload``，不经过 ``about:blank``。
        - ``GAMEQA_REFRESH_SETTLE_MS``：导航成功后等待毫秒数（默认 600）。
        """
        if not self._page:
            raise RuntimeError("browser not launched")
        u = (url or "").strip()
        cur = ""
        try:
            cur = (self._page.url or "").strip()
        except Exception as e:
            await _emit_line(
                emit,
                f"[gameqa][browser] refresh_current_page: cannot read page.url: {type(e).__name__}: {e!r}",
            )
        soft = (os.environ.get("GAMEQA_REFRESH_SOFT_RELOAD") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        if u:
            if _urls_same_for_bfcache(cur, u) and _url_ok_for_cold_refresh(u):
                await _emit_line(
                    emit,
                    "[gameqa][browser] refresh_current_page: same URL — cold_nav (about:blank → robust goto)",
                )
                await _cold_blank_then_goto(self._page, u, emit, tag="refresh_target_cold")
                return f"refreshed via cold_nav+robust_goto url={u!r}"
            await _emit_line(emit, f"[gameqa][browser] refresh_current_page: robust_goto url={u!r}")
            await _robust_page_goto(self._page, u, emit=emit, tag="refresh_goto")
            return f"refreshed via robust_goto url={u!r}"

        if soft:
            await _emit_line(
                emit,
                "[gameqa][browser] refresh_current_page: GAMEQA_REFRESH_SOFT_RELOAD — robust reload only",
            )
            await _robust_page_reload(self._page, emit=emit, tag="refresh_soft_reload")
            return "refreshed via robust_page.reload() (soft)"

        if _url_ok_for_cold_refresh(cur):
            await _emit_line(
                emit,
                "[gameqa][browser] refresh_current_page: cold_nav current URL (K11-style hard refresh)",
            )
            await _cold_blank_then_goto(self._page, cur, emit, tag="refresh_current_cold")
            return f"refreshed via cold_nav url={cur!r}"

        await _emit_line(
            emit,
            "[gameqa][browser] refresh_current_page: robust reload (non-http or special tab)",
        )
        await _robust_page_reload(self._page, emit=emit, tag="refresh_reload")
        return "refreshed via robust_page.reload()"

    async def click_named_viewport(self, x: float, y: float) -> str:
        if not self._page:
            raise RuntimeError("browser not launched")
        import time as _time

        try:
            from l3_client.local_mcps.gameqa_mcp.skill_cli_debug import append as _skill_line

            _url = ""
            _title = ""
            _vp = None
            try:
                _url = self._page.url
                _title = await self._page.title()
                _vp = self._page.viewport_size
            except Exception as _e:
                _url = f"<page_meta_err {_e!r}>"
            _skill_line(
                "[CLICK] 即将 await page.mouse.click | "
                f"x={x} y={y} | page.url={_url!r} | page.title={_title!r} | viewport_size={_vp!r} | "
                f"cdp_http={self.cdp_http!r} | owns_browser_process={self.owns_browser_process} | "
                "注意：Playwright 默认无超时，若此处 hangs，本条之后不会出现「click 已返回」。"
            )
        except Exception:
            pass

        _t0 = _time.monotonic()
        await self._page.mouse.click(x, y)
        _ms = (_time.monotonic() - _t0) * 1000.0
        try:
            from l3_client.local_mcps.gameqa_mcp.skill_cli_debug import append as _skill_line

            _skill_line(f"[CLICK] page.mouse.click 已返回 | elapsed_ms={_ms:.1f}")
        except Exception:
            pass
        return f"clicked viewport ({x:.1f},{y:.1f})"

    async def drain_shadow_clicks(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while True:
            try:
                out.append(self._shadow_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    async def _install_shadow_bridge(
        self,
        on_shadow_click: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        if not self._page or self._binding_installed:
            return

        async def _binding(source: Any, payload: Any) -> str:
            if not isinstance(payload, dict):
                try:
                    payload = dict(payload)
                except Exception:
                    payload = {"raw": payload}
            x = float(payload.get("x", 0.0))
            y = float(payload.get("y", 0.0))
            ts = payload.get("t")
            data = {"x": x, "y": y, "t": ts}
            await self._shadow_queue.put(data)
            if on_shadow_click:
                try:
                    await on_shadow_click(data)
                except Exception as e:
                    logger.warning("[gameqa] on_shadow_click: %s", e)
            return "ok"

        try:
            await self._page.expose_binding("gameqaShadowReport", _binding)
            self._binding_installed = True
        except Exception as e:
            logger.info("[gameqa] expose_binding skipped/failed (second client or duplicate): %s", e)
            self._binding_installed = True

    async def _inject_click_sniffer(self) -> None:
        if not self._page:
            return
        try:
            await self._page.add_init_script(
                """
(() => {
  const report = (ev) => {
    try {
      if (typeof window.gameqaShadowReport === 'function') {
        window.gameqaShadowReport({ x: ev.clientX, y: ev.clientY, t: Date.now() });
      }
    } catch (e) {}
  };
  document.addEventListener('click', report, true);
})();
"""
            )
        except Exception as e:
            logger.info("[gameqa] add_init_script skip: %s", e)
