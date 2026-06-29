#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Browser protocol bridge:
- Accepts browser-reported API/WS events over localhost HTTP
- Aggregates protocol signals (duel/settlement/coin)
- Writes scripts/omnioutput/proto_status.json for main_bot_loop [proto] logger
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_DUEL_RE = re.compile(r"(duel|fight|challenge|fold|showdown)", re.I)
_SETTLEMENT_RE = re.compile(r"(settle|settlement|result|reward|round_end|game_end)", re.I)
_COIN_RE = re.compile(r"(coin|gold|balance|wallet|chip|chips|delta|win|lose|profit)", re.I)
_INT_RE = re.compile(r"[+-]?\d+")
_DUEL_MSG_TYPES = {"3018"}
_SETTLEMENT_MSG_TYPES = {"3016", "3017", "3021"}
_COIN_MSG_TYPES = {"3016"}  # SSOT：仅 3016 sumWinBonus；3021/3024 不记账

_output_path: Path | None = None
_settlement_monitor: Any = None

_state_lock = threading.Lock()
_state: dict[str, Any] = {}


def _now_utc8_text() -> str:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _to_text_blob(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(payload)


def _payload_kind(payload: dict[str, Any]) -> str:
    kind = str(payload.get("kind") or payload.get("channel") or "").strip().lower()
    if kind in ("api", "http", "xhr", "fetch"):
        return "api"
    if kind in ("ws", "websocket"):
        return "ws"
    if str(payload.get("url") or "").startswith("ws"):
        return "ws"
    if payload.get("msgType") is not None or payload.get("msg_type") is not None:
        return "ws"
    return "api"


def _extract_msg_type(payload: dict[str, Any]) -> str:
    raw = payload.get("msgType", payload.get("msg_type", payload.get("type", "")))
    if raw in ("", None):
        text = str(payload.get("text") or "")
        m = re.search(r"\b(30\d{2})\b", text)
        if m:
            raw = m.group(1)
    return str(raw) if raw is not None else ""


def _looks_like_rtc_noise(payload: dict[str, Any], text_blob: str | None = None) -> bool:
    """Agora/WebRTC telemetry can contain words like result, but it is not game settlement."""
    url = str(payload.get("url") or "").lower()
    if "sd-rtn.com" in url or "agora" in url or ".edge." in url and ":471" in url:
        return True
    if text_blob is None:
        text_blob = _to_text_blob(payload)
    low = text_blob.lower()
    rtc_tokens = (
        "agora",
        "sd-rtn",
        "rejoin_token",
        "rtc",
        "webrtc",
        "iceparameters",
        "rtpcapabilities",
        "dtlsparameters",
    )
    return any(token in low for token in rtc_tokens)


def _mark_signal_by_msg_type(msg_type: str) -> tuple[str, str, str]:
    """
    显式 msgType 映射（优先级最高）：
    - 3018: duel
    - 3021: settlement + coin
    - 3024: coin
    """
    duel = f"seen@{msg_type}" if msg_type in _DUEL_MSG_TYPES else "-"
    settlement = f"seen@{msg_type}" if msg_type in _SETTLEMENT_MSG_TYPES else "-"
    coin = f"seen@{msg_type}" if msg_type in _COIN_MSG_TYPES else "-"
    return duel, settlement, coin


def _mark_signal(payload: dict[str, Any], text_blob: str) -> tuple[str, str, str]:
    duel = "-"
    settlement = "-"
    coin = "-"

    msg_type = _extract_msg_type(payload)
    map_duel, map_settlement, map_coin = _mark_signal_by_msg_type(msg_type)
    if map_duel != "-" or map_settlement != "-" or map_coin != "-":
        return map_duel, map_settlement, map_coin
    if not msg_type and _looks_like_rtc_noise(payload, text_blob):
        return "-", "-", "-"

    compact = f"{msg_type} {text_blob}"
    if _DUEL_RE.search(compact):
        duel = f"seen@{msg_type}" if msg_type else "seen"
    if _SETTLEMENT_RE.search(compact):
        settlement = f"seen@{msg_type}" if msg_type else "seen"
    if _COIN_RE.search(compact):
        coin = f"seen@{msg_type}" if msg_type else "seen"

    body = payload.get("body")
    if isinstance(body, dict):
        body_keys = " ".join(body.keys())
        if duel == "-" and _DUEL_RE.search(body_keys):
            duel = f"seen@{msg_type}" if msg_type else "seen"
        if settlement == "-" and _SETTLEMENT_RE.search(body_keys):
            settlement = f"seen@{msg_type}" if msg_type else "seen"
        if coin == "-" and _COIN_RE.search(body_keys):
            coin = f"seen@{msg_type}" if msg_type else "seen"

    return duel, settlement, coin


def _first_int_from_any(v: Any, *, depth: int = 0) -> int | None:
    if depth > 4:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    if isinstance(v, str):
        m = _INT_RE.search(v.replace(",", ""))
        if m:
            try:
                return int(m.group(0))
            except ValueError:
                return None
        return None
    if isinstance(v, dict):
        preferred = (
            "coinDelta",
            "coin_delta",
            "goldDelta",
            "gold_delta",
            "delta",
            "change",
            "changeGold",
            "change_gold",
            "winGold",
            "loseGold",
            "scoreDelta",
            "myDelta",
            "my_delta",
        )
        for k in preferred:
            if k in v:
                got = _first_int_from_any(v.get(k), depth=depth + 1)
                if got is not None:
                    return got
        for vv in v.values():
            got = _first_int_from_any(vv, depth=depth + 1)
            if got is not None:
                return got
        return None
    if isinstance(v, list):
        for item in v:
            got = _first_int_from_any(item, depth=depth + 1)
            if got is not None:
                return got
    return None


def _extract_coin_delta(payload: dict[str, Any], *, msg_type: str) -> int | None:
    """3021/3024 的 coin 字段为累计/同步，不是本局盈亏；仅 bridge 状态机保留，不写入 coin_delta。"""
    if msg_type != "3016":
        return None
    for key in ("coinDelta", "coin_delta", "delta", "goldDelta", "gold_delta"):
        if key in payload:
            got = _first_int_from_any(payload.get(key))
            if got is not None:
                return got
    body = payload.get("body")
    if isinstance(body, dict):
        got = _first_int_from_any(body)
        if got is not None:
            return got
    data = payload.get("data")
    if data is not None:
        got = _first_int_from_any(data)
        if got is not None:
            return got
    text = str(payload.get("text") or "")
    m = re.search(r"([+-]\d{2,})", text.replace(",", ""))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _normalize_bridge_payload(payload: dict[str, Any]) -> dict[str, Any]:
    msg_type = _extract_msg_type(payload)
    body = payload.get("body")
    if body is None:
        body = payload.get("data")
    return {
        "msgType": msg_type,
        "direction": str(payload.get("direction") or payload.get("dir") or "in"),
        "body": body,
        "text": str(payload.get("text") or "")[:4000],
    }


def _get_settlement_monitor(out_dir: Path) -> Any:
    global _settlement_monitor
    if _settlement_monitor is not None:
        return _settlement_monitor
    import os
    import sys

    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from tongits_result_monitor import ResultMonitor

    my_name = str(os.environ.get("TONGITS_MY_NAME") or "victor").strip()

    def _on_settlement(data: dict[str, Any]) -> None:
        line = str(data.get("line") or "")
        if _state:
            _state["settlement_record_line"] = line
            _state["settlement_record_at"] = str(data.get("at") or _now_utc_iso())
            _state["settlement_record_game_no"] = data.get("game_no")
            _state["settlement_record_msg_type"] = str(data.get("msg_type") or "3016")
        if line:
            print(f"[proto-bridge] {line}", flush=True)

    _settlement_monitor = ResultMonitor(
        out_dir,
        my_name,
        discover=False,
        on_settlement=_on_settlement,
    )
    return _settlement_monitor


def _feed_settlement_monitor(payload: dict[str, Any], out_dir: Path) -> None:
    msg_type = _extract_msg_type(payload)
    if msg_type not in ("3016", "3017", "3021"):
        return
    try:
        mon = _get_settlement_monitor(out_dir)
        mon.handle(_normalize_bridge_payload(payload))
    except Exception as exc:
        print(f"[proto-bridge] {msg_type} settlement error: {exc}", flush=True)


def _init_state() -> dict[str, Any]:
    ts_iso = _now_utc_iso()
    ts_txt = _now_utc8_text()
    return {
        "source": "browser_proto_bridge",
        "updated_at": ts_iso,
        "updated_at_utc8": ts_txt,
        "api": {"state": "off", "last_url": "", "last_status": ""},
        "ws": {"state": "off", "last_msg_type": "", "last_direction": ""},
        "duel": "-",
        "duel_at": "",
        "settlement": "-",
        "settlement_at": "",
        "coin": "-",
        "coin_at": "",
        "coin_delta": None,
        "coin_delta_at": "",
        "settlement_record_line": "",
        "settlement_record_at": "",
        "settlement_record_game_no": None,
        "settlement_record_msg_type": "",
        "counters": {"events": 0},
        "msg_type_map": {
            "duel": sorted(_DUEL_MSG_TYPES),
            "settlement": sorted(_SETTLEMENT_MSG_TYPES),
            "coin": sorted(_COIN_MSG_TYPES),
        },
        "last_event": {},
    }


def _update_state(payload: dict[str, Any], *, out_dir: Path) -> dict[str, Any]:
    global _state
    if not _state:
        _state = _init_state()
    text_blob = _to_text_blob(payload)
    kind = _payload_kind(payload)
    msg_type = _extract_msg_type(payload)
    direction = str(payload.get("direction") or payload.get("dir") or "")

    _feed_settlement_monitor(payload, out_dir)
    url = str(payload.get("url") or "")
    status = payload.get("status")

    if kind == "api":
        _state["api"] = {
            "state": "on",
            "last_url": url[:256],
            "last_status": str(status)[:32],
            "last_at": _now_utc_iso(),
        }
    else:
        _state["ws"] = {
            "state": "on",
            "last_msg_type": msg_type[:64],
            "last_direction": direction[:32],
            "last_at": _now_utc_iso(),
        }

    duel, settlement, coin = _mark_signal(payload, text_blob)
    if duel != "-":
        _state["duel"] = duel
        _state["duel_at"] = _now_utc_iso()
    if settlement != "-":
        _state["settlement"] = settlement
        _state["settlement_at"] = _now_utc_iso()
    if coin != "-":
        _state["coin"] = coin
        _state["coin_at"] = _now_utc_iso()
    coin_delta = _extract_coin_delta(payload, msg_type=msg_type)
    if coin_delta is not None:
        _state["coin_delta"] = int(coin_delta)
        _state["coin_delta_at"] = _now_utc_iso()

    _state["updated_at"] = _now_utc_iso()
    _state["updated_at_utc8"] = _now_utc8_text()
    _state["counters"]["events"] = int(_state["counters"].get("events", 0)) + 1
    _state["last_event"] = {
        "kind": kind,
        "direction": direction[:32],
        "msgType": msg_type[:64],
        "url": url[:256],
        "status": str(status)[:32] if status is not None else "",
        "at": _state["updated_at"],
    }
    return _state


def _build_handler(output_path: Path):
    out_dir = output_path.parent

    class Handler(BaseHTTPRequestHandler):
        def _write_json(self, status: int, obj: dict[str, Any]) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path not in ("/health", "/proto/status"):
                self._write_json(404, {"ok": False, "error": "not_found"})
                return
            with _state_lock:
                if not _state:
                    _state.update(_init_state())
                snap = dict(_state)
            self._write_json(200, {"ok": True, "state": snap})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/proto/update":
                self._write_json(404, {"ok": False, "error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._write_json(400, {"ok": False, "error": "empty_body"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._write_json(400, {"ok": False, "error": "invalid_json"})
                return
            if not isinstance(payload, dict):
                self._write_json(400, {"ok": False, "error": "json_object_required"})
                return
            with _state_lock:
                snap = _update_state(payload, out_dir=out_dir)
                _atomic_write_json(output_path, snap)
            self._write_json(200, {"ok": True, "state": snap})

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="Tongits browser protocol bridge")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=17888)
    ap.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / "omnioutput" / "proto_status.json"),
    )
    args = ap.parse_args()

    output_path = Path(args.output)
    with _state_lock:
        if not _state:
            _state.update(_init_state())
        _atomic_write_json(output_path, _state)
    server = ThreadingHTTPServer((args.host, args.port), _build_handler(output_path))
    print(
        f"[proto-bridge] listening http://{args.host}:{args.port} "
        f"-> {output_path}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
