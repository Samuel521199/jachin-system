"""
Playwright：自治（无头）/ 影子（有头）+ CDP 共享。

- 首轮 launch 时使用 ``--remote-debugging-port``，并将 ``http://127.0.0.1:<port>`` 写入
  ``$GAMEQA_DATA_DIR/cdp_http.txt``，便于 **L3 HTTP** 与 **MCP stdio** 共用同一 Chromium。
- 第二轮（另一进程）：优先 ``connect_over_cdp``（或 ``GAMEQA_CDP_URL``）附着，不重开隐身实例。
"""
from __future__ import annotations

import asyncio
import logging
import os
import traceback
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from filelock import FileLock

logger = logging.getLogger("gameqa.browser_engine")

EmitFn = Optional[Callable[[str], Awaitable[None]]]


async def _emit_line(emit: EmitFn, line: str) -> None:
    """同时写 Python logging（L3 stderr）与可选 SSE 行。"""
    logger.info("%s", line)
    if emit:
        await emit(line)


def _snapshot_relevant_env() -> list[str]:
    keys = (
        "GAMEQA_DATA_DIR",
        "GAMEQA_CDP_URL",
        "GAMEQA_FORCE_NEW_BROWSER",
        "GAMEQA_REMOTE_DEBUG_HOST",
        "GAMEQA_REMOTE_DEBUG_PORT",
        "GAMEQA_YOLO_MODEL",
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


def remote_debug_http() -> tuple[str, int]:
    """当前使用的 CDP HTTP 基底，默认本机 GAMEQA_REMOTE_DEBUG_PORT（默认 9238）。"""
    port = int(os.environ.get("GAMEQA_REMOTE_DEBUG_PORT", "9238").strip())
    host = os.environ.get("GAMEQA_REMOTE_DEBUG_HOST", "127.0.0.1").strip() or "127.0.0.1"
    return (f"http://{host}:{port}", port)


def explicit_cdp_url() -> str:
    """直接指定附着地址；优先于磁盘文件与本机端口推断。"""
    return (os.environ.get("GAMEQA_CDP_URL") or "").strip()


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
        lock = FileLock(lock_path, timeout=-1)

        def _acquire() -> None:
            lock.acquire(timeout=90)

        def _release() -> None:
            try:
                lock.release(force=True)
            except Exception:
                pass

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
                await _emit_line(
                    emit,
                    "[gameqa][browser] no existing context → new_context viewport=1280x800",
                )
                self._context = await self._browser.new_context(
                    viewport={"width": 1280, "height": 800},
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
        await _emit_line(emit, "[gameqa][browser] new_context viewport=1280x800 …")
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
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
            await _emit_line(
                emit,
                (
                    f"[gameqa][browser] 附着优先级: GAMEQA_CDP_URL={(env_ep or '(unset)')!r} "
                    f"∨ 文件endpoint={(ep_file or '(none)')!r} "
                    f"∨ 本机默认 {def_http!r} port={def_port}"
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
                await _emit_line(
                    emit,
                    "[gameqa][browser] CDP #1 失败：曾有过显式 endpoint，策略上不再探测本机默认调试口 → 将进入 launch",
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
            src = "GAMEQA_CDP_URL" if explicit_cdp_url() else "cdp_http.txt"
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

    async def click_named_viewport(self, x: float, y: float) -> str:
        if not self._page:
            raise RuntimeError("browser not launched")
        await self._page.mouse.click(x, y)
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
