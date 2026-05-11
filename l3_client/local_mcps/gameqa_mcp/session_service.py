"""
GameQA 会话：MCP 工具与 L3 HTTP 共用实现逻辑。

CDP：`BrowserEngine` 首轮 launch 写入 ``cdp_http.txt``，其它进程可通过 ``connect_over_cdp``
附着同一 Chromium（参见 ``GAMEQA_CDP_URL`` / ``GAMEQA_REMOTE_DEBUG_PORT``）。

影子模式：拦截点击后 **异步截屏→视觉→对齐**，无需玩家事先「刷新语义状态」。
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from .core.browser_engine import BrowserEngine, CDP_HTTP_FILE, gameqa_data_dir
from .core.shadow_logger import ShadowLogger, resolve_click_to_semantic
from .core.ocr_engine import ocr_png_bytes_for_state
from .core.vision_engine import VisionEngine

logger = logging.getLogger("gameqa.session_service")


def _data_dir() -> Path:
    import os

    raw = os.environ.get("GAMEQA_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".gameqa_mcp").resolve()


def _knowledge_allowed(path: Path) -> tuple[bool, str]:
    import os

    root = os.environ.get("GAMEQA_KNOWLEDGE_ROOT", "").strip()
    if not root:
        return True, ""
    try:
        path.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False, f"path must be under GAMEQA_KNOWLEDGE_ROOT={root!r}"
    return True, ""


class GameQAService:
    """
    **单进程单例**（L3 或 MCP 进程内各一份）；跨进程通过 **CDP 附着** 共用浏览器。
    """

    __slots__ = (
        "browser",
        "vision",
        "logger",
        "semantic_map",
        "last_public_state",
        "mode",
        "run_id",
        "_op_lock",
        "_log_queues",
    )

    def __init__(self) -> None:
        self.browser = BrowserEngine()
        self.vision = VisionEngine()
        self.logger = ShadowLogger(_data_dir())
        self.semantic_map: dict[str, tuple[float, float]] = {}
        self.last_public_state: dict[str, object] = {}
        self.mode: str = "idle"
        self.run_id: str = str(uuid.uuid4())
        self._op_lock = asyncio.Lock()
        self._log_queues: list[asyncio.Queue[str]] = []

    def subscribe_logs(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=800)
        self._log_queues.append(q)
        return q

    def unsubscribe_logs(self, q: asyncio.Queue[str]) -> None:
        try:
            self._log_queues.remove(q)
        except ValueError:
            pass

    async def emit_log(self, line: str) -> None:
        for q in list(self._log_queues):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(line)
                except asyncio.QueueFull:
                    pass

    async def _skill_debug_log_active_page(self, tag: str) -> None:
        """
        将当前 Playwright 页的 url/title/viewport 写入 ``JACHIN_GAMEQA_SKILL_DEBUG_LOG`` 指向的文件。
        仅 ``run_gameqa_skill.py`` 等设置该环境变量时生效；**不**经 emit_log / stdout。
        """
        try:
            from l3_client.local_mcps.gameqa_mcp.skill_cli_debug import append, log_file_path

            if not log_file_path():
                return
            pg = self.browser.page
            if not pg:
                append(
                    f"[{tag}] 当前 Playwright 操作页 | page=None | run_id={self.run_id} | mode={self.mode!r}"
                )
                return
            url_s = ""
            title_s = ""
            try:
                url_s = pg.url
            except Exception as e:
                url_s = f"<url_err {e!r}>"
            try:
                title_s = await pg.title()
            except Exception as e:
                title_s = f"<title_err {e!r}>"
            vp = None
            try:
                vp = pg.viewport_size
            except Exception:
                pass
            append(
                f"[{tag}] 当前 Playwright 操作页（语义检测/截图以此页为准）| url={url_s!r} | title={title_s!r} | "
                f"viewport_css={vp!r} | run_id={self.run_id} | mode={self.mode!r} | "
                f"cdp_http={self.browser.cdp_http!r} | owns_browser_process={self.browser.owns_browser_process}"
            )
        except Exception:
            pass

    def _write_perception_skill_log(
        self,
        banner: str,
        vr: Any,
        ocr_block: dict[str, Any] | None,
        *,
        ocr_mode: str = "full",
    ) -> None:
        """
        将 YOLO（``vr.yolo_debug``）与 OCR 全文摘要写入 ``gameqa_skill_debug.log``。
        ``ocr_mode=skipped_lazy``：影子对齐路径不跑 OCR，仅注明原因。
        """
        try:
            from l3_client.local_mcps.gameqa_mcp.skill_cli_debug import append_perception_debug

            lines: list[str] = [banner]
            yd = (getattr(vr, "yolo_debug", None) or "").strip()
            if yd:
                lines.extend(yd.split("\n"))
            else:
                lines.append(f"  vision_notes={vr.raw_notes!r}")
                lines.append("  elements (merged map):")
                for k, (x, y) in sorted(vr.elements.items()):
                    lines.append(f"    {k!r}: ({x:.1f}, {y:.1f})")

            lines.append("---")
            if ocr_mode == "skipped_lazy":
                lines.extend(
                    [
                        "[OCR]",
                        "  (skipped: shadow lazy_align — 仅 YOLO；完整 OCR 请调用 get_semantic_state)",
                    ]
                )
            else:
                ob = ocr_block or {}
                lines.append("[OCR]")
                lines.append(
                    f"  ocr_enabled={ob.get('ocr_enabled')} "
                    f"ocr_backend={ob.get('ocr_backend')!r}"
                )
                lines.append(f"  ocr_notes={ob.get('ocr_notes')!r}")
                text = str(ob.get("ocr_text") or "")
                try:
                    lim = int((os.environ.get("GAMEQA_SKILL_DEBUG_OCR_LOG_CHARS") or "8000").strip())
                except ValueError:
                    lim = 8000
                lim = max(0, min(lim, 200_000))
                if not text:
                    lines.append("  ocr_text=(empty)")
                elif lim == 0:
                    lines.append(
                        f"  ocr_text total_len={len(text)} "
                        "(GAMEQA_SKILL_DEBUG_OCR_LOG_CHARS=0, content omitted)"
                    )
                else:
                    chunk = text[:lim]
                    tail_note = (
                        f" ...[log truncated, state has {len(text)} chars]"
                        if len(text) > lim
                        else ""
                    )
                    lines.append(f"  ocr_text ({len(text)} chars in state){tail_note}:")
                    for sub in chunk.splitlines() or [chunk]:
                        lines.append(f"    {sub}")

            append_perception_debug(lines)
        except Exception:
            pass

    async def _ensure_live_page(self, *, for_shadow_attach: bool = False) -> tuple[bool, str]:
        if self.browser.page:
            return True, ""
        ok = await self.browser.attach_if_endpoint_available(
            shadow=for_shadow_attach,
            on_shadow_click=self._on_shadow_click_lazy if for_shadow_attach else None,
            emit=self.emit_log,
        )
        if ok:
            await self.emit_log("[gameqa] 已通过 CDP 附着到共享 Chromium（与另一入口同屏）。")
        return (
            ok,
            "browser not live: start from 游戏测试 / launch-test / launch-shadow first, or set GAMEQA_CDP_URL",
        )

    async def stop(self) -> dict[str, Any]:
        async with self._op_lock:
            await self.browser.close()
            self.semantic_map.clear()
            self.last_public_state.clear()
            self.mode = "idle"
            await self.emit_log("[gameqa] 已停止：Playwright 已断开（若本进程拥有 Chromium 则进程已结束）。")
            return {"ok": True, "message": "stopped"}

    async def launch_test(self, url: str) -> dict[str, Any]:
        async with self._op_lock:
            self.run_id = str(uuid.uuid4())
            self.logger.reset_cycle_logs()
            self.semantic_map.clear()
            self.last_public_state.clear()
            self.mode = "test"
            await self.emit_log(
                f"[gameqa] 自治测试：run_id={self.run_id} · 将按序尝试 CDP 附着（GAMEQA_CDP_URL / "
                f"cdp_http.txt / 本机默认口），失败则本进程 launch Chromium 并写入 cdp_http.txt。"
                f"目标 URL={url!r}"
            )
            try:
                msg = await self.browser.launch(
                    url,
                    headless=True,
                    shadow=False,
                    on_shadow_click=None,
                    emit=self.emit_log,
                )
            except Exception as e:
                logger.exception("launch_test")
                await self.emit_log(f"[gameqa][ERROR] {e!r}")
                return {"ok": False, "error": repr(e)}
            await self._skill_debug_log_active_page("launch_test·会话就绪（对比 Agent 目标 URL 是否一致）")
            self.logger.append_audit(
                {"event": "launch_test_mode", "run_id": self.run_id, "url": url, "detail": msg}
            )
            await self.emit_log(f"[gameqa] {msg}")
            await self.emit_log(
                f"[gameqa] 其它入口可共用 CDP：读 {gameqa_data_dir() / CDP_HTTP_FILE!s} "
                "或设置环境变量 GAMEQA_CDP_URL。"
            )
            return {"ok": True, "run_id": self.run_id, "mode": "test", "message": msg}

    async def refresh_view(self, url: str = "") -> dict[str, Any]:
        """
        当前标签页 reload 或 goto；**不**走 ``launch()``，避免重复抢占 ``gameqa_browser.launch.lock`` 导致超时。
        """
        async with self._op_lock:
            ok, err = await self._ensure_live_page(for_shadow_attach=False)
            if not ok:
                return {"ok": False, "error": err}
            self.semantic_map.clear()
            self.last_public_state.clear()
            try:
                msg = await self.browser.refresh_current_page(url, emit=self.emit_log)
            except Exception as e:
                logger.exception("refresh_view")
                await self.emit_log(f"[gameqa][ERROR] refresh_view: {e!r}")
                return {"ok": False, "error": repr(e)}
            self.logger.append_audit(
                {
                    "event": "refresh_view",
                    "run_id": self.run_id,
                    "url": (url or "").strip() or "(reload)",
                    "detail": msg,
                }
            )
            await self.emit_log(f"[gameqa] {msg}")
            return {"ok": True, "run_id": self.run_id, "message": msg}

    async def _on_shadow_click_lazy(self, data: dict[str, object]) -> None:
        """绑定必须快返回；对齐在后台任务里完成（截屏 + 视觉）。"""
        x = float(data.get("x", 0.0))
        y = float(data.get("y", 0.0))
        ts = data.get("t")
        try:
            asyncio.create_task(self._lazy_align_shadow_sample(x, y, ts))
        except RuntimeError:
            await self._lazy_align_shadow_sample(x, y, ts)

    async def _lazy_align_shadow_sample(self, x: float, y: float, ts: object = None) -> None:
        """滞后对齐：用 **点击后当前帧** 的视觉结果匹配坐标，并 merge 进语义表。"""
        if not self.browser.page:
            return
        png: bytes | None = None
        try:
            png = await self.browser.screenshot_png()
        except Exception as e:
            logger.warning("[gameqa] lazy_align screenshot: %s", e)
        if not png:
            self.logger.append_training(
                structured_state=dict(self.last_public_state),
                semantic_action=None,
                client_xy={"x": x, "y": y},
                meta={"alignment": "lazy_post_click", "error": "screenshot_failed", "run_id": self.run_id},
            )
            await self.emit_log(f"[gameqa][shadow] lazy_align 截图失败 @({x:.0f},{y:.0f})")
            return

        vr = await self.vision.analyze_async(png)
        fresh_map = dict(vr.elements)
        name, dist = resolve_click_to_semantic(fresh_map, x, y)
        structured: dict[str, Any] = {
            "run_id": self.run_id,
            "mode": self.mode,
            "elements": vr.to_public_dict()["elements"],
            "vision_notes": vr.raw_notes,
            "alignment": "lazy_post_click",
            "click_ts": ts,
            "click_xy": {"x": x, "y": y},
        }
        async with self._op_lock:
            self.semantic_map.update(fresh_map)
            self.last_public_state = structured

        self.logger.append_training(
            structured_state=structured,
            semantic_action=name,
            client_xy={"x": x, "y": y},
            meta={
                "dist": float(dist),
                "run_id": self.run_id,
                "mode": "shadow",
                "alignment": "lazy_post_click",
            },
        )
        label = name or "?"
        await self.emit_log(
            f"[gameqa][shadow] lazy_align ({x:.0f},{y:.0f}) → {label!r} dist={dist:.1f} keys={list(fresh_map.keys())}"
        )
        self._write_perception_skill_log(
            f"=== perception · shadow_lazy_align run_id={self.run_id} "
            f"click_xy=({x:.1f},{y:.1f}) matched_semantic={label!r} dist={dist:.1f} ===",
            vr,
            None,
            ocr_mode="skipped_lazy",
        )

    async def launch_shadow(self, url: str) -> dict[str, Any]:
        async with self._op_lock:
            self.run_id = str(uuid.uuid4())
            self.logger.reset_cycle_logs()
            self.semantic_map.clear()
            self.last_public_state.clear()
            self.mode = "shadow"
            await self.emit_log(
                f"[gameqa] 影子训练：run_id={self.run_id} · CDP 优先，否则新开有头 Chromium；目标 URL={url!r}"
            )
            try:
                msg = await self.browser.launch(
                    url,
                    headless=False,
                    shadow=True,
                    on_shadow_click=self._on_shadow_click_lazy,
                    emit=self.emit_log,
                )
            except Exception as e:
                logger.exception("launch_shadow")
                await self.emit_log(f"[gameqa][ERROR] {e!r}")
                return {"ok": False, "error": repr(e)}
            await self._skill_debug_log_active_page("launch_shadow·会话就绪（对比 Agent 目标 URL 是否一致）")
            self.logger.append_audit(
                {"event": "launch_shadow_mode", "run_id": self.run_id, "url": url, "detail": msg}
            )
            await self.emit_log(f"[gameqa] {msg}")
            await self.emit_log(
                "[gameqa] 影子：点击后立即后台截屏对齐语义；Agent 侧请用同一 CDP attach，勿再隐式 launch。"
            )
            return {"ok": True, "run_id": self.run_id, "mode": "shadow", "message": msg}

    async def get_semantic_state(self) -> dict[str, Any]:
        async with self._op_lock:
            ok, err = await self._ensure_live_page(for_shadow_attach=False)
            if not ok:
                return {"ok": False, "error": err}
            await self._skill_debug_log_active_page("semantic_state·截图与语义检测前")
            try:
                png = await self.browser.screenshot_png()
                vr, ocr_block = await asyncio.gather(
                    self.vision.analyze_async(png),
                    asyncio.to_thread(ocr_png_bytes_for_state, png),
                )
                self.semantic_map = dict(vr.elements)
                extra_clicks = await self.browser.drain_shadow_clicks()
                public: dict[str, Any] = {
                    "run_id": self.run_id,
                    "mode": self.mode,
                    "elements": vr.to_public_dict()["elements"],
                    "vision_notes": vr.raw_notes,
                    "ocr_text": ocr_block.get("ocr_text", ""),
                    "ocr_notes": ocr_block.get("ocr_notes", ""),
                    "ocr_enabled": ocr_block.get("ocr_enabled", False),
                    "ocr_backend": ocr_block.get("ocr_backend", "none"),
                    "pending_shadow_events": extra_clicks,
                    "cdp_http": self.browser.cdp_http,
                }
                self.last_public_state = public
                self.logger.append_audit(
                    {
                        "event": "semantic_state",
                        "run_id": self.run_id,
                        "element_keys": list(self.semantic_map.keys()),
                    }
                )
                await self.emit_log(
                    f"[gameqa] 语义状态已更新 keys={list(self.semantic_map.keys())} "
                    f"ocr_backend={public.get('ocr_backend')!r} "
                    f"ocr_chars={len(str(public.get('ocr_text') or ''))}"
                )
                self._write_perception_skill_log(
                    f"=== perception · get_semantic_state run_id={self.run_id} mode={self.mode!r} ===",
                    vr,
                    ocr_block,
                )
                return {"ok": True, "state": public}
            except Exception as e:
                logger.exception("get_semantic_state")
                return {"ok": False, "error": repr(e)}

    async def execute_action(self, element_name: str) -> dict[str, Any]:
        async with self._op_lock:
            ok, err = await self._ensure_live_page(for_shadow_attach=False)
            if not ok:
                return {"ok": False, "error": err}
            if not element_name.strip():
                return {"ok": False, "error": "empty element_name"}
            key = element_name.strip()
            pos = self.semantic_map.get(key)
            if not pos:
                return {
                    "ok": False,
                    "error": f"unknown element {key!r}; run semantic-state or wait for shadow lazy_align",
                    "known": list(self.semantic_map.keys()),
                }
            x, y = pos
            try:
                try:
                    from l3_client.local_mcps.gameqa_mcp.skill_cli_debug import append, log_file_path

                    if log_file_path():
                        await self._skill_debug_log_active_page("execute_action·点击前")
                        append(
                            f"[execute_action·点击前] 目标语义元素 element_name={key!r} viewport_xy=({x},{y})"
                        )
                except Exception:
                    pass
                click_msg = await self.browser.click_named_viewport(x, y)
            except Exception as e:
                return {"ok": False, "error": repr(e)}
            rec = {
                "event": "execute_action",
                "run_id": self.run_id,
                "element": key,
                "viewport": [x, y],
                "detail": click_msg,
            }
            self.logger.append_audit(rec)
            await self.emit_log(f"[gameqa] 执行动作 {key!r} @ ({x:.0f},{y:.0f})")
            return {"ok": True, "executed": rec}

    def read_knowledge(self, file_path: str) -> dict[str, Any]:
        try:
            p = Path(file_path).expanduser().resolve()
            ok, err = _knowledge_allowed(p)
            if not ok:
                return {"ok": False, "error": err}
            if not p.is_file():
                return {"ok": False, "error": "not a file"}
            if p.suffix.lower() not in {".md", ".markdown"}:
                return {"ok": False, "error": "only .md allowed for knowledge files"}
            text = p.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "path": str(p), "content": text}
        except Exception as e:
            logger.exception("read_knowledge")
            return {"ok": False, "error": repr(e)}

    def get_audit_log(self) -> dict[str, Any]:
        try:
            text = self.logger.read_audit_text()
            return {"ok": True, "audit_trail_jsonl": text}
        except Exception as e:
            return {"ok": False, "error": repr(e)}

    def get_training_tail(self, max_lines: int = 30) -> dict[str, Any]:
        try:
            p = self.logger.training_path
            if not p.is_file():
                return {"ok": True, "lines": [], "path": str(p)}
            lines = p.read_text(encoding="utf-8", errors="replace").strip().split("\n")
            tail = [ln for ln in lines[-max_lines:] if ln.strip()]
            return {"ok": True, "lines": tail, "path": str(p)}
        except Exception as e:
            return {"ok": False, "error": repr(e)}

    def status_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": self.mode,
            "run_id": self.run_id,
            "browser_active": self.browser.page is not None,
            "semantic_keys": list(self.semantic_map.keys()),
            "data_dir": str(self.logger.data_dir),
            "audit_path": str(self.logger.audit_path),
            "training_path": str(self.logger.training_path),
            "cdp_http": self.browser.cdp_http,
            "owns_browser_process": self.browser.owns_browser_process,
            "gameqa_shared_browser_hint": (
                "Attach other clients with GAMEQA_CDP_URL or read cdp_http.txt; avoid GAMEQA_FORCE_NEW_BROWSER."
            ),
        }


_service: GameQAService | None = None


def get_gameqa_service() -> GameQAService:
    global _service
    if _service is None:
        _service = GameQAService()
    return _service
