#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 协议结算后台服务（CDP 独立线程，供 main_bot_loop 集成）。

- 默认随主 bot 启动：daemon 线程连接 Chrome DevTools，监听 3016 结算帧。
- 每局结束在后台记账，不阻塞主线程看牌/打牌。
- 写入 omnioutput/coin_delta.csv + coin_delta.log（与主 bot 同一格式）。
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from tongits_cdp_capture import (
    CDPClient,
    _build_console_handler,
    _find_chrome,
    _launch_chrome,
    _list_targets,
    _pick_target,
    inject_proto_bridge_hooks,
)
from tongits_result_monitor import ResultMonitor

logger = logging.getLogger(__name__)


class ProtoSettlementService:
    """Chrome CDP 协议结算监听（daemon 线程）。"""

    def __init__(
        self,
        *,
        my_name: str = "victor",
        out_dir: Path | None = None,
        on_settlement: Callable[[dict[str, Any]], None] | None = None,
        host: str = "127.0.0.1",
        port: int = 9222,
        url_filter: str = "herontest",
        launch_chrome: bool = True,
        game_url: str | None = None,
        profile_dir: Path | None = None,
        discover: bool = False,
    ) -> None:
        self.my_name = my_name
        self.out_dir = out_dir or (Path(__file__).resolve().parent / "omnioutput")
        self.on_settlement = on_settlement
        self.host = host
        self.port = port
        self.url_filter = url_filter
        self.launch_chrome = launch_chrome
        self.game_url = game_url or os.environ.get("TONGITS_CDP_GAME_URL") or "https://www.herontest.xin/"
        self.profile_dir = profile_dir or (
            Path(__file__).resolve().parent / "omnioutput" / "cdp_chrome_profile"
        )
        self.discover = discover
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._chrome_proc: subprocess.Popen | None = None
        self._monitor: ResultMonitor | None = None

    def start_daemon(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="proto-settlement-cdp",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._chrome_proc is not None:
            try:
                self._chrome_proc.terminate()
            except Exception:
                pass
            self._chrome_proc = None

    def _ensure_chrome(self) -> bool:
        if not self.launch_chrome:
            return True
        try:
            if _list_targets(self.host, self.port):
                logger.info("[proto] 复用已运行的调试 Chrome（端口 %s）", self.port)
                return True
        except Exception:
            pass
        chrome = _find_chrome(os.environ.get("TONGITS_CDP_CHROME"))
        if not chrome:
            logger.warning("[proto] 未找到 Chrome，CDP 结算不可用")
            return False
        self._chrome_proc = _launch_chrome(chrome, self.port, self.profile_dir, self.game_url)
        return self._chrome_proc is not None

    def _wait_target(self, *, timeout_sec: float) -> dict[str, Any] | None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline and not self._stop.is_set():
            try:
                target = _pick_target(_list_targets(self.host, self.port), self.url_filter)
                if target:
                    return target
            except Exception:
                pass
            time.sleep(1.0)
        return None

    def _run_cdp_session(self, target: dict[str, Any]) -> None:
        assert self._monitor is not None
        handler = _build_console_handler(self._monitor)
        bridge_auto = os.environ.get("TONGITS_BRIDGE_AUTO_INJECT", "1").strip().lower() in (
            "1", "true", "yes", "on",
        )
        bridge_port = int(os.environ.get("TONGITS_PROTO_BRIDGE_PORT") or "17888")
        cdp = CDPClient(
            target["webSocketDebuggerUrl"],
            handler,
            bridge_auto_inject=bridge_auto,
            bridge_port=bridge_port,
        )
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        cdp.call(
            "Target.setAutoAttach",
            {"autoAttach": True, "flatten": True, "waitForDebuggerOnStart": False},
        )
        if bridge_auto:
            inject_proto_bridge_hooks(
                cdp, None,
                url=str(target.get("url") or ""),
                port=bridge_port,
            )
            logger.info("[proto] CDP 将自动注入 proto-bridge（port=%s），无需 F12 粘贴", bridge_port)
        logger.info(
            "[proto] CDP 已附着：%s | %s",
            str(target.get("title", ""))[:40],
            str(target.get("url", ""))[:80],
        )
        while not self._stop.is_set():
            try:
                cdp.ws.settimeout(1.0)
                cdp._pump_one()
            except Exception as exc:
                if self._stop.is_set():
                    break
                if "timed out" in str(exc).lower():
                    continue
                logger.warning("[proto] CDP 连接中断：%s", exc)
                break

    def _run_loop(self) -> None:
        try:
            if self.launch_chrome and not self._ensure_chrome():
                return
            wait = 40.0 if self.launch_chrome else 8.0
            target = self._wait_target(timeout_sec=wait)
            if not target:
                logger.warning(
                    "[proto] 未找到游戏页（filter=%s port=%s），CDP 结算线程退出",
                    self.url_filter,
                    self.port,
                )
                return

            self._monitor = ResultMonitor(
                self.out_dir,
                self.my_name,
                self.discover,
                on_settlement=self.on_settlement,
            )
            logger.info(
                "[proto] 结算后台线程就绪 my=%s discover=%s log=%s",
                self.my_name,
                self.discover,
                self._monitor.settlement_log_path,
            )
            while not self._stop.is_set():
                if target is None:
                    target = self._wait_target(timeout_sec=15.0)
                    if target is None:
                        time.sleep(3.0)
                        continue
                self._run_cdp_session(target)
                target = None
                if not self._stop.is_set():
                    logger.info("[proto] CDP 会话结束，3s 后重连…")
                    time.sleep(3.0)
        except Exception as exc:
            logger.warning("[proto] 结算后台线程异常退出：%s", exc)
