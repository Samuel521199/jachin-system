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
        "GAMEQA_VISIBLE_CLICK_MARKER",
        "GAMEQA_VISIBLE_CLICK_MARKER_DELAY_MS",
        "GAMEQA_ATTACH_SCRIPT_CHROME_FIRST",
        "GAMEQA_LAUNCH_TEST_HEADLESS",
        "GAMEQA_PLAYWRIGHT_USE_PROXY",
        "GAMEQA_PROXY_URL",
        "GAMEQA_PROXY_HOST",
        "GAMEQA_PROXY_PORT",
        "GAMEQA_PROXY_BYPASS",
        "HTTP_PROXY",
        "HTTPS_PROXY",
    )
    out: list[str] = []
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if k in ("HTTP_PROXY", "HTTPS_PROXY", "GAMEQA_PROXY_URL") and v:
            try:
                p = urlparse(v)
                netloc = p.hostname or ""
                if p.port:
                    netloc = f"{netloc}:{p.port}"
                safe = urlunparse((p.scheme, netloc, p.path or "", "", "", ""))
                out.append(f"[gameqa][env] {k}={safe!r} (redacted)")
            except Exception:
                out.append(f"[gameqa][env] {k}=(set, parse failed)")
        else:
            out.append(f"[gameqa][env] {k}={v!r}" if v else f"[gameqa][env] {k}=(unset)")
    user = (os.environ.get("GAMEQA_PROXY_USERNAME") or os.environ.get("GAMEQA_PROXY_USER") or "").strip()
    out.append(
        "[gameqa][env] GAMEQA_PROXY_USERNAME=(set)"
        if user
        else "[gameqa][env] GAMEQA_PROXY_USERNAME=(unset)"
    )
    out.append(
        "[gameqa][env] GAMEQA_PROXY_PASSWORD=(set)"
        if (os.environ.get("GAMEQA_PROXY_PASSWORD") or "").strip()
        else "[gameqa][env] GAMEQA_PROXY_PASSWORD=(unset)"
    )
    return out


def _visible_click_marker_enabled() -> bool:
    """
    实验：点击前在页面**顶部**显示高对比 HUD（当前视口点击坐标），并在目标点画**放大环状靶心**，
    便于确认 Playwright 控制的 tab / Canvas 层。

    - 默认开启（unset 等价于 ``1``）；置 ``GAMEQA_VISIBLE_CLICK_MARKER=0`` / ``false`` / ``no`` / ``off`` 可关闭。
    """
    raw = (os.environ.get("GAMEQA_VISIBLE_CLICK_MARKER") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _visible_click_marker_delay_ms() -> int:
    try:
        return max(
            0,
            int((os.environ.get("GAMEQA_VISIBLE_CLICK_MARKER_DELAY_MS") or "150").strip()),
        )
    except ValueError:
        return 150


async def _inject_visible_click_marker(page: Any, x: float, y: float) -> tuple[bool, str]:
    """
    调试：点击前在**页面最顶**叠一层高对比 HUD（当前视口点击坐标），并在 (x,y) 叠大尺寸环状靶心。
    与 ``mouse.click`` 同为 **viewport CSS 像素**；``pointer-events:none`` 不挡真实点击。
    """
    if not page:
        return False, "no page"
    try:
        detail = await page.evaluate(
            """([cx, cy]) => {
              const mid = "gameqa-visible-click-marker";
              const hid = "gameqa-click-hud";
              const sid = "gameqa-click-marker-style";
              const p1 = document.getElementById(mid);
              if (p1) p1.remove();
              const p2 = document.getElementById(hid);
              if (p2) p2.remove();
              const root = document.body || document.documentElement;
              if (!root) return { ok: false, reason: "no body" };
              if (!document.getElementById(sid)) {
                const st = document.createElement("style");
                st.id = sid;
                st.textContent =
                  "@keyframes gameqaHudPulse{0%,100%{opacity:1}" +
                  "50%{opacity:.88}}" +
                  "@keyframes gameqaTargetPulse{0%,100%{transform:translate(-50%,-50%) scale(1)}" +
                  "50%{transform:translate(-50%,-50%) scale(1.12)}}";
                (document.documentElement || root).appendChild(st);
              }
              const xs = (typeof cx === "number" ? cx : parseFloat(cx)).toFixed(1);
              const ys = (typeof cy === "number" ? cy : parseFloat(cy)).toFixed(1);
              const hud = document.createElement("div");
              hud.id = hid;
              hud.setAttribute("role", "presentation");
              hud.style.cssText =
                "position:fixed;top:0;left:0;right:0;min-height:56px;padding:10px 14px 12px;" +
                "background:linear-gradient(180deg,#c62828 0%,#8e0000 100%);color:#fff;" +
                "font-family:system-ui,-apple-system,Segoe UI,sans-serif;z-index:2147483647;" +
                "pointer-events:none;box-sizing:border-box;box-shadow:0 4px 16px rgba(0,0,0,.45);" +
                "border-bottom:3px solid #ffeb3b;display:flex;flex-direction:column;justify-content:center;" +
                "animation:gameqaHudPulse 1.2s ease-in-out infinite;";
              const line1 = document.createElement("div");
              line1.style.cssText =
                "font-size:13px;font-weight:600;letter-spacing:.04em;opacity:.95;text-transform:uppercase;";
              line1.textContent = "GameQA Agent · 即将点击（视口坐标）";
              const line2 = document.createElement("div");
              line2.style.cssText =
                "font-size:22px;font-weight:800;font-variant-numeric:tabular-nums;margin-top:4px;" +
                "text-shadow:0 1px 2px rgba(0,0,0,.4);";
              line2.textContent = "x = " + xs + " px   ·   y = " + ys + " px";
              hud.appendChild(line1);
              hud.appendChild(line2);
              root.appendChild(hud);
              const m = document.createElement("div");
              m.id = mid;
              m.setAttribute("role", "presentation");
              m.style.cssText =
                "position:fixed;left:" + cx + "px;top:" + cy + "px;" +
                "width:56px;height:56px;transform:translate(-50%,-50%);" +
                "border-radius:50%;background:rgba(255,23,68,.3);border:5px solid #ff1744;" +
                "box-shadow:0 0 0 4px #fff,0 0 28px 8px rgba(255,23,68,.85);" +
                "z-index:2147483646;pointer-events:none;box-sizing:border-box;" +
                "animation:gameqaTargetPulse 0.85s ease-in-out infinite;";
              root.appendChild(m);
              return {
                ok: true,
                reason: "hud+target",
                left: cx,
                top: cy,
                xs: xs,
                ys: ys,
              };
            }""",
            [float(x), float(y)],
        )
        ok = isinstance(detail, dict) and bool(detail.get("ok"))
        reason = repr(detail) if detail is not None else "None"
        return ok, reason
    except Exception as e:
        return False, f"{type(e).__name__}: {e!r}"


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


def gameqa_playwright_proxy_config() -> dict[str, str] | None:
    """
    供 ``chromium.launch(proxy=…)`` 使用。本机手动开的 Chrome 若走了 Clash/VPN，Playwright **新起的 Chromium 默认仍不经代理**，
    需在此配置与系统/浏览器一致的 HTTP(S) 或 SOCKS 入口。

    - ``GAMEQA_PLAYWRIGHT_USE_PROXY=0``：显式关闭（本函数返回 ``None``）。
    - **优先级**：``GAMEQA_PROXY_URL`` 完整 URL（``http(s)://`` 或 ``socks5://``）
      → ``GAMEQA_PROXY_HOST`` + ``GAMEQA_PROXY_PORT``（仅端口时默认主机 ``127.0.0.1``）
      → ``HTTPS_PROXY`` / ``HTTP_PROXY``
      → ``GAMEQA_PLAYWRIGHT_USE_PROXY=1`` 且以上皆空时，默认 ``http://127.0.0.1:8800``。
    - 可选：``GAMEQA_PROXY_BYPASS``（Playwright 的逗号分隔 bypass 列表）；
      ``GAMEQA_PROXY_USERNAME`` + ``GAMEQA_PROXY_PASSWORD``（需认证的代理）。
    """
    off = (os.environ.get("GAMEQA_PLAYWRIGHT_USE_PROXY") or "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
        "none",
    )
    if off:
        return None

    server = (os.environ.get("GAMEQA_PROXY_URL") or "").strip()
    if not server:
        port = (os.environ.get("GAMEQA_PROXY_PORT") or "").strip()
        if port.isdigit():
            host = (os.environ.get("GAMEQA_PROXY_HOST") or "127.0.0.1").strip() or "127.0.0.1"
            server = f"http://{host}:{port}"
    if not server:
        server = (os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or "").strip()
    if not server:
        use = (os.environ.get("GAMEQA_PLAYWRIGHT_USE_PROXY") or "").strip().lower()
        if use in ("1", "true", "yes", "on"):
            server = "http://127.0.0.1:8800"
    if not server:
        return None
    if "://" not in server:
        server = "http://" + server.lstrip("/")

    out: dict[str, str] = {"server": server}
    bypass = (os.environ.get("GAMEQA_PROXY_BYPASS") or "").strip()
    if bypass:
        out["bypass"] = bypass
    user = (os.environ.get("GAMEQA_PROXY_USERNAME") or os.environ.get("GAMEQA_PROXY_USER") or "").strip()
    pw = (os.environ.get("GAMEQA_PROXY_PASSWORD") or "").strip()
    if user:
        out["username"] = user
    if pw:
        out["password"] = pw
    return out


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


def prefer_attach_script_debug_chrome_first() -> bool:
    """
    未显式设置 ``GAMEQA_CDP_URL`` / ``KALAROKO_CDP_ENDPOINT`` 时，优先尝试默认远程调试口
    （``GAMEQA_REMOTE_DEBUG_HOST`` / ``PORT``；默认 **9222**，与 ``scripts/launch_chrome_debug.ps1`` 一致），
    然后再读 ``cdp_http.txt``。

    - 默认开启（避免陈旧 ``cdp_http.txt`` 指向上一次 Playwright 实例而绕过桌面 Chrome）。
    - ``GAMEQA_ATTACH_SCRIPT_CHROME_FIRST=0`` / ``false`` / ``no`` / ``off`` 恢复「先文件后默认」。
    """
    raw = (os.environ.get("GAMEQA_ATTACH_SCRIPT_CHROME_FIRST") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _cdp_attach_attempt_order(*, pinned: str, ep_file: str, def_http: str) -> list[tuple[str, str]]:
    """
    生成 CDP attach 序列 ``(attempt_label, endpoint)``（规范化 URL 去重）。

    ``pinned``：非空时只附着该 endpoint（来自 ``explicit_cdp_url()``）。
    """
    out: list[tuple[str, str]] = []
    seen_norm: set[str] = set()
    prefer_first = prefer_attach_script_debug_chrome_first()

    def _add(label: str, endpoint: str) -> None:
        n = _normalize_cdp_http(endpoint)
        if not n or n in seen_norm:
            return
        seen_norm.add(n)
        out.append((label, endpoint))

    pinned_s = (pinned or "").strip()
    ep_f = (ep_file or "").strip()

    if pinned_s:
        _add("explicit_env", pinned_s)
        return out

    if prefer_first:
        _add("prefer_script_debug_default", def_http)
        if ep_f and _normalize_cdp_http(ep_f) != _normalize_cdp_http(def_http):
            _add("cdp_http_txt", ep_f)
    else:
        if ep_f:
            _add("cdp_http_txt", ep_f)
        if not ep_f or _normalize_cdp_http(ep_f) != _normalize_cdp_http(def_http):
            _add("legacy_default_debug", def_http)

    if not out:
        _add("fallback_default_debug_only", def_http)
    return out


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
        proxy_cfg = gameqa_playwright_proxy_config()
        launch_kw: dict[str, Any] = {"headless": headless, "args": launch_args}
        if proxy_cfg:
            launch_kw["proxy"] = proxy_cfg
            srv = proxy_cfg.get("server", "")
            bits = [f"server={srv!r}"]
            if proxy_cfg.get("bypass"):
                bits.append(f"bypass={proxy_cfg['bypass']!r}")
            if proxy_cfg.get("username"):
                bits.append("proxy_auth=user+password")
            await _emit_line(
                emit,
                "[gameqa][browser] chromium.launch proxy: " + " ".join(bits),
            )
        else:
            await _emit_line(emit, "[gameqa][browser] chromium.launch proxy=(none)")
        await _emit_line(emit, "[gameqa][browser] playwright async_playwright().start() …")
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(**launch_kw)
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
        skip_cdp_attach: bool = False,
    ) -> str:
        """启动或附着：默认可先 CDP 附着，失败再本进程 launch（带调试端口）。

        ``skip_cdp_attach=True``：不尝试 ``connect_over_cdp``，直接本进程 ``chromium.launch``（自治测试与外部
        Chrome 坐标易错位时使用）。
        """

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
                f"[gameqa][browser] skip_cdp_attach={skip_cdp_attach} "
                f"（True=不附着外部 Chrome，仅本进程 launch Playwright Chromium）",
            )
            await _emit_line(
                emit,
                f"[gameqa][browser] 目标 navigate_url={url!r} headless={headless} shadow={shadow}",
            )
            await _emit_line(
                emit,
                f"[gameqa][browser] 清理本进程旧会话 close(discard_shared_endpoint_file={bool(force_new or skip_cdp_attach)}) …",
            )
            await self.close(discard_shared_endpoint_file=bool(force_new or skip_cdp_attach))

            pinned_s = explicit_cdp_url().strip()
            if skip_cdp_attach:
                await _emit_line(
                    emit,
                    "[gameqa][browser] 已跳过 CDP 附着序列，不尝试 connect_over_cdp",
                )
                attempts = []
            else:
                attempts = _cdp_attach_attempt_order(
                    pinned=pinned_s,
                    ep_file=ep_file,
                    def_http=def_http,
                )
            await _emit_line(
                emit,
                (
                    f"[gameqa][browser] CDP attach 顺序（去重）: "
                    + (
                        "（已跳过 — 仅 launch）"
                        if skip_cdp_attach
                        else (
                            ", ".join(f"{lab}={ep!r}" for lab, ep in attempts)
                            if attempts
                            else "(empty — 将直接 launch)"
                        )
                    )
                    + f" | prefer_script_first={prefer_attach_script_debug_chrome_first()!r}"
                ),
            )

            gameqa_cdp_only = (os.environ.get("GAMEQA_CDP_URL") or "").strip()
            if not force_new and not skip_cdp_attach:
                for idx, (attempt_label, endpoint) in enumerate(attempts, start=1):
                    await _emit_line(
                        emit,
                        f"[gameqa][browser] CDP 尝试 #{idx} ({attempt_label}) endpoint={endpoint!r}",
                    )
                    ok = await self._try_connect_over_cdp(
                        endpoint,
                        url,
                        shadow=shadow,
                        on_shadow_click=on_shadow_click,
                        emit=emit,
                        attempt_label=attempt_label,
                    )
                    if ok:
                        self._headless = headless
                        self._shadow_mode = shadow
                        await _emit_line(
                            emit,
                            f"[gameqa][browser] ========== 结束：CDP 附着成功 ({attempt_label}) ==========",
                        )
                        hint = ""
                        if attempt_label == "prefer_script_debug_default":
                            hint = " [visible Chrome: launch_chrome_debug.ps1 / GAMEQA_REMOTE_DEBUG_PORT]"
                        elif attempt_label == "cdp_http_txt":
                            hint = " [cdp_http.txt]"
                        elif attempt_label == "explicit_env" and cdp_env_source_label():
                            hint = f" [{cdp_env_source_label()}]"
                        return (
                            f"attached over CDP url={url!r} endpoint={endpoint!r} shadow={shadow}{hint}"
                        )
                    if gameqa_cdp_only:
                        await _emit_line(
                            emit,
                            "[gameqa][browser] GAMEQA_CDP_URL 已设置且附着失败 → 不回退其它 endpoint，将进入 launch",
                        )
                        break

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
        pinned_s = explicit_cdp_url().strip()
        ef = endpoint_file_read()
        def_http, p = remote_debug_http()
        attempts = _cdp_attach_attempt_order(
            pinned=pinned_s,
            ep_file=ef,
            def_http=def_http,
        )
        await _emit_line(
            emit,
            (
                "[gameqa][browser] attach_if: CDP attach 序列 "
                f"prefer_script_first={prefer_attach_script_debug_chrome_first()!r} "
                f"def_port={p} → "
                + (", ".join(f"{lb}={ep!r}" for lb, ep in attempts) or "(none)")
            ),
        )
        ok = False
        gameqa_cdp_only_af = (os.environ.get("GAMEQA_CDP_URL") or "").strip()
        for idx, (attempt_label, endpoint) in enumerate(attempts, start=1):
            await _emit_line(
                emit,
                f"[gameqa][browser] attach_if: 尝试 #{idx} ({attempt_label}) endpoint={endpoint!r}",
            )
            ok = await self._try_connect_over_cdp(
                endpoint,
                "",
                shadow=shadow,
                on_shadow_click=on_shadow_click,
                emit=emit,
                attempt_label=f"attach_if.{attempt_label}",
            )
            if ok:
                await _emit_line(emit, f"[gameqa][browser] attach_if: 成功 ({attempt_label})")
                return True
            if gameqa_cdp_only_af:
                await _emit_line(
                    emit,
                    "[gameqa][browser] attach_if: GAMEQA_CDP_URL 已锁定且失败 → 不再回退其它 endpoint",
                )
                break
        await _emit_line(emit, "[gameqa][browser] attach_if: 失败（无共享 Chromium 或端口不可达）")
        return False

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

        if _visible_click_marker_enabled():
            _ok_m, _detail_m = await _inject_visible_click_marker(self._page, x, y)
            try:
                from l3_client.local_mcps.gameqa_mcp.skill_cli_debug import append as _skill_line

                _skill_line(
                    f"[CLICK][marker] HUD(顶栏)+靶心 ring@({x:.1f},{y:.1f})px "
                    f"ok={_ok_m} detail={_detail_m} "
                    f"(GAMEQA_VISIBLE_CLICK_MARKER_DELAY_MS={_visible_click_marker_delay_ms()})"
                )
            except Exception:
                pass
            _delay_s = _visible_click_marker_delay_ms() / 1000.0
            if _delay_s > 0:
                await asyncio.sleep(_delay_s)

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
