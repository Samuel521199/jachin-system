#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 胜负结算监控（纯协议 / API，不依赖视觉）。

设计目标（与 main_bot_loop 完全解耦）：
- 只在“一局结束（胜/负/平）”时记录：我方盈亏、对手盈亏、结果。
- 数据来源 = 浏览器协议帧（WebSocket/HTTP），由 tongits_result_monitor_snippet.js 转发。
- 输出：
    * 控制台实时打印「[结算] 第N局 胜/负 我方 +1500 | 对手 ...」
    * scripts/omnioutput/result_log.csv          （结构化，便于统计）
    * scripts/omnioutput/result_monitor.jsonl     （逐事件原始留痕，便于复盘）
- discover 模式：把每个“疑似结算 / 含金币”的原始帧体落盘到 result_discover.jsonl，
  首局即可锁定确切 msgType 与字段，之后无需再猜。

用法：
    python scripts/tongits_result_monitor.py
    python scripts/tongits_result_monitor.py --my-name victor --discover
浏览器侧把 tongits_result_monitor_snippet.js 贴进 DevTools 控制台即可。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 结构识别用关键词（宽松，靠“玩家列表 + 数值增减”双条件兜底，不死磕单一 msgType）
# ---------------------------------------------------------------------------
_SETTLE_KEYWORDS = re.compile(
    r"(settle|settlement|reward|round_?end|game_?end|gameover|liquidat)",
    re.I,
)
# Agora/WebRTC 语音 SDK 遥测特征键：命中即视为噪声，与牌局胜负无关，直接丢弃。
_RTC_NOISE_KEYS = (
    "_id", "_message", "_result", "_type", "ortc", "ap_response", "rejoin_token",
    "peer_delay", "B_acd", "B_dnq", "B_unq", "B_palr4", "B_pvlr4", "p2p_id",
    "sdk_version", "channel_key", "channel_name", "iceParameters", "rtpCapabilities",
    "dtlsParameters", "rtpParameters",
)
_COIN_KEYWORDS = re.compile(r"(coin|gold|chip|balance|wallet|score|delta|profit|win|lose)", re.I)

_LIST_KEYS = (
    "players", "playerList", "users", "userList", "results", "resultList",
    "scores", "scoreList", "settle", "settleList", "list", "seats", "seatList",
    "rank", "rankList", "members", "memberList",
)
# 结算帧分工（实机已确认）：
# - 3016 sumWinBonus = 本局盈亏（测试服/旧正式服记账 SSOT）
# - 3017 coinChanged = 同局稍后到的汇总/累计字段，数值常≠单局，默认不记账
# - 3021 正式服可能承载结算展示/结果同步；若能解析出三人零和，可作为 fallback
_SETTLEMENT_OBSERVE_TYPES = frozenset({"3016", "3017", "3021"})
_SETTLEMENT_RECORD_TYPES = frozenset({"3016"})  # 只认 3016 写入 CSV
# 进房/座位/玩家状态广播：含 coin/coinChanged 但是「入座快照」，不是本局打完的盈亏。
_ROOM_SYNC_MSG_TYPES = frozenset({"101", "103", "152", "320", "1"})
_MSGTYPE_HINTS: dict[str, str] = {
    "101": "玩家进房",
    "103": "玩家入座/换座广播",
    "152": "座位/准备状态",
    "320": "麦克风/座位权限",
    "3002": "房间全量同步(余额快照，非本局盈亏)",
    "3015": "对局状态快照",
    "3016": "★本局结算明细(sumWinBonus) → 记账 SSOT",
    "3017": "同局补充帧(coinChanged 常为累计，默认不记账)",
    "3021": "正式服结算/结果同步候选（仅零和玩家列表可 fallback 记账）",
    "3028": "胜者/摊牌展示(非最终记账)",
    "C2W_GAME_STATUS": "外层 UI 桥(无金币)",
}

# 本局盈亏字段（优先级从高到低）；不含 coin/gold/chip——那是账户总余额。
_ROUND_DELTA_KEYS = (
    "sumWinBonus", "sum_win_bonus",
    "coinChanged", "coin_changed",
    "coinDelta", "coin_delta",
    "goldDelta", "gold_delta",
    "coinChange", "coin_change",
    "changeGold", "change_gold",
    "winGold", "win_gold",
    "profit", "change", "amount",
)
# 通用 discover 扫描仍用（含 balance 字样便于观察），但结算记账不用。
_DELTA_KEYS = _ROUND_DELTA_KEYS
_NAME_KEYS = ("name", "nickname", "nickName", "userName", "user_name", "playerName", "player_name")
_UID_KEYS = ("uid", "userId", "userID", "user_id", "playerId", "player_id", "id", "accountId")
_SEAT_KEYS = ("seat", "seatId", "seat_id", "pos", "position", "chairId", "chair_id", "index")
_RESULT_KEYS = ("result", "isWin", "is_win", "win", "winFlag", "win_flag", "outcome", "rank")

_state_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).isoformat()


def _now_hms() -> str:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    if isinstance(v, str):
        m = re.search(r"[+-]?\d+", v.replace(",", ""))
        if m:
            try:
                return int(m.group(0))
            except ValueError:
                return None
    return None


def _get_first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    # 大小写无关兜底
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] not in (None, ""):
            return lower[k.lower()]
    return None


def _looks_like_player(entry: Any) -> dict[str, Any] | None:
    """一个 dict 同时具备“身份(name/uid/seat)”+“数值增减”才算玩家结算条目。"""
    if not isinstance(entry, dict):
        return None
    delta_raw = _get_first(entry, _DELTA_KEYS)
    delta = _to_int(delta_raw)
    if delta is None:
        return None
    name = _get_first(entry, _NAME_KEYS)
    uid = _get_first(entry, _UID_KEYS)
    seat = _get_first(entry, _SEAT_KEYS)
    if name is None and uid is None and seat is None:
        return None
    result = _get_first(entry, _RESULT_KEYS)
    return {
        "name": str(name) if name is not None else "",
        "uid": str(uid) if uid is not None else "",
        "seat": seat,
        "delta": delta,
        "result": result,
    }


def _schema(node: Any, *, depth: int = 0) -> str:
    """把任意 JSON 节点压成可读结构串；小整数内联其值，便于一眼看到 coinDelta:int(1500)。"""
    if depth > 4:
        return "…"
    if isinstance(node, dict):
        items = list(node.items())[:14]
        inner = ",".join(f"{k}:{_schema(v, depth=depth + 1)}" for k, v in items)
        more = "…" if len(node) > 14 else ""
        return "{" + inner + more + "}"
    if isinstance(node, list):
        if not node:
            return "[]"
        head = _schema(node[0], depth=depth + 1)
        return "[" + head + (f"×{len(node)}" if len(node) > 1 else "") + "]"
    if isinstance(node, bool):
        return f"bool({node})"
    if isinstance(node, int):
        return f"int({node})" if abs(node) < 1_000_000 else "int(big)"
    if isinstance(node, float):
        return f"f({node:.0f})"
    if isinstance(node, str):
        return f'"{node[:14]}"' if len(node) <= 14 else "str"
    if node is None:
        return "null"
    return type(node).__name__


def _coin_like_leaves(node: Any, *, prefix: str = "", depth: int = 0,
                      out: list[tuple[str, int]] | None = None) -> list[tuple[str, int]]:
    """收集“可疑金币”数值：key 命中金币词、或带符号且量级 >=100 的整数，附带路径。"""
    if out is None:
        out = []
    if depth > 6 or len(out) > 40:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                iv = int(round(v))
                if _COIN_KEYWORDS.search(str(k)) or abs(iv) >= 100:
                    out.append((path, iv))
            else:
                _coin_like_leaves(v, prefix=path, depth=depth + 1, out=out)
    elif isinstance(node, list):
        for i, item in enumerate(node[:12]):
            _coin_like_leaves(item, prefix=f"{prefix}[{i}]", depth=depth + 1, out=out)
    return out


def _unwrap_player_info(entry: dict[str, Any]) -> dict[str, Any]:
    """Tongits 常双层嵌套 playerInfo.playerInfo。"""
    pi = entry.get("playerInfo")
    if isinstance(pi, dict):
        inner = pi.get("playerInfo")
        if isinstance(inner, dict):
            return inner
        return pi
    return entry


def _player_row(info: dict[str, Any], delta: int, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "name": str(_get_first(info, _NAME_KEYS) or ""),
        "uid": str(_get_first(info, _UID_KEYS) or ""),
        "seat": _get_first(info, _SEAT_KEYS),
        "delta": int(delta),
        "result": _get_first(info, _RESULT_KEYS),
    }
    if extra:
        row.update(extra)
    return row


def _unwrap_settlement_body(body: Any) -> Any:
    """CDP/console 有时把整包 {msgType, body} 当作 body 传入，需剥一层。"""
    if not isinstance(body, dict):
        return body
    if "playerResults" in body or "players" in body:
        return body
    inner = body.get("body")
    if isinstance(inner, dict):
        return inner
    return body


def _coerce_msg_type(payload: dict[str, Any], text: str) -> str:
    raw = (
        payload.get("msgType")
        or payload.get("msg_type")
        or payload.get("type")
        or ""
    )
    if raw not in ("", None):
        return str(raw).strip()
    m = re.search(r"收到消息[：:]\s*(\d{4})", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(301[0-9]|302[0-9])\b", text)
    if m:
        return m.group(1)
    body = _unwrap_settlement_body(payload.get("body"))
    if isinstance(body, dict) and body.get("playerResults"):
        return "3016"
    if isinstance(body, dict) and body.get("players"):
        return "3017"
    return ""


def _valid_settlement_players(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(players) < 2:
        return []
    if not any(int(p["delta"]) != 0 for p in players):
        return []
    return players


def _generic_settlement_players(body: Any) -> list[dict[str, Any]]:
    return _valid_settlement_players(_find_player_list(body))


def _parse_tongits_settlement(msg_type: str, body: Any) -> list[dict[str, Any]]:
    """Extract settlement players from known prod/test settlement messages."""
    body = _unwrap_settlement_body(body)
    if not isinstance(body, dict) or msg_type not in _SETTLEMENT_OBSERVE_TYPES:
        return []

    players: list[dict[str, Any]] = []

    if msg_type == "3016":
        results = body.get("playerResults")
        if isinstance(results, list):
            for pr in results:
                if not isinstance(pr, dict):
                    continue
                info = _unwrap_player_info(pr)
                delta = _to_int(pr.get("sumWinBonus"))
                if delta is None:
                    delta = _to_int(_get_first(info, ("coinChanged", "coin_changed")))
                if delta is None:
                    continue
                uid = _get_first(info, _UID_KEYS)
                name = _get_first(info, _NAME_KEYS)
                if uid is None and name is None:
                    continue
                players.append(_player_row(info, delta, extra={
                    "normalWinBonus": _to_int(pr.get("normalWinBonus")),
                    "bonusBonus": _to_int(pr.get("bonusBonus")),
                }))
        if not players:
            players = _find_player_list(body)

    elif msg_type == "3017":
        raw_players = body.get("players")
        if isinstance(raw_players, list):
            for entry in raw_players:
                if not isinstance(entry, dict):
                    continue
                info = _unwrap_player_info(entry)
                delta = _to_int(_get_first(info, ("coinChanged", "coin_changed")))
                if delta is None:
                    continue
                uid = _get_first(info, _UID_KEYS)
                name = _get_first(info, _NAME_KEYS)
                if uid is None and name is None:
                    continue
                players.append(_player_row(info, delta))
        if not players:
            players = _find_player_list(body)

    elif msg_type == "3021":
        players = _find_player_list(body)

    return _valid_settlement_players(players)


def _find_player_list(node: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """深度搜索：返回最大的一组“玩家结算条目”列表。"""
    best: list[dict[str, Any]] = []
    if depth > 6:
        return best
    if isinstance(node, list):
        players = [p for p in (_looks_like_player(x) for x in node) if p]
        if len(players) >= 2:
            best = players
        for item in node:
            cand = _find_player_list(item, depth=depth + 1)
            if len(cand) > len(best):
                best = cand
        return best
    if isinstance(node, dict):
        for key in _LIST_KEYS:
            if key in node and isinstance(node[key], list):
                players = [p for p in (_looks_like_player(x) for x in node[key]) if p]
                if len(players) > len(best):
                    best = players
        for v in node.values():
            cand = _find_player_list(v, depth=depth + 1)
            if len(cand) > len(best):
                best = cand
    return best


class ResultMonitor:
    def __init__(self, out_dir: Path, my_name: str, discover: bool,
                 *, on_settlement: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = out_dir / "result_log.csv"
        self.jsonl_path = out_dir / "result_monitor.jsonl"
        self.discover_path = out_dir / "result_discover.jsonl"
        self.my_name = my_name.strip().lower()
        self.my_uid: str = ""
        self.discover = discover
        self.game_no = 0
        self.wins = 0
        self.losses = 0
        self.draws = 0
        self.net = 0
        self._recent_sig: dict[str, float] = {}
        self._events = 0
        self.type_counts: dict[str, int] = {}
        self.verbose_per_type = 2  # 每种 msgType 详细打印前 N 帧，之后收敛成计数
        self._noise_dropped = 0
        self._started_at = time.time()
        self._warmup_sec = float(os.environ.get("TONGITS_SETTLE_WARMUP_SEC") or ("15" if on_settlement else "5"))
        self._last_primary_settle_at: float = 0.0
        self._on_settlement: Callable[[dict[str, Any]], None] | None = on_settlement
        self._quiet_console = on_settlement is not None
        self._quiet_observe = self._quiet_console
        self.settlement_log_path = out_dir / "settlement.log"
        self._debounce_sec = float(os.environ.get("TONGITS_SETTLE_DEBOUNCE_SEC") or "2.5")
        self._debounce_timer: threading.Timer | None = None
        self._pending_debounced: tuple[str, list[dict[str, Any]], dict[str, Any]] | None = None
        self._last_3016_hint_at: float = 0.0
        if not self.csv_path.exists() and not self._quiet_console:
            with self.csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(
                    ["time", "game_no", "outcome", "my_delta", "net_total",
                     "wins", "losses", "draws", "opponents", "msg_type"]
                )

    # --- 自我识别：从 joinRoom / 我方发出的 C2W_* 帧学习我的 uid/座位 ---
    def _maybe_learn_self(self, payload: dict[str, Any], text: str) -> None:
        if self.my_uid:
            return
        direction = str(payload.get("direction") or payload.get("dir") or "")
        mtype = str(payload.get("msgType") or payload.get("type") or "")
        # 客户端自身的桥接/状态帧（C2W_/C2N_ GAME_STATUS、发出帧）携带的 userId 即为本人，最可靠。
        if direction == "out" or "C2W_" in text or "C2N_" in text or mtype.startswith(("C2W_", "C2N_")):
            uid = self._deep_find_uid(payload.get("body"))
            if uid:
                self.my_uid = str(uid)
                print(f"[monitor] 已识别我方 uid={self.my_uid}（来自客户端状态/发出帧 {mtype or 'C2*'}）", flush=True)
                return
        body = payload.get("body")
        if not isinstance(body, (dict, list)):
            return
        for entry in (body if isinstance(body, list) else [body]):
            if not isinstance(entry, dict):
                continue
            name = _get_first(entry, _NAME_KEYS)
            uid = _get_first(entry, _UID_KEYS)
            is_self = _get_first(entry, ("isSelf", "is_self", "self", "isMe", "is_me", "mine"))
            if name and self.my_name and str(name).strip().lower() == self.my_name and uid:
                self.my_uid = str(uid)
                print(f"[monitor] 已识别我方 uid={self.my_uid}（name={name}）", flush=True)
                return
            if is_self in (True, 1, "1", "true") and uid:
                self.my_uid = str(uid)
                print(f"[monitor] 已识别我方 uid={self.my_uid}（isSelf）", flush=True)
                return

    @staticmethod
    def _deep_find_uid(node: Any, *, depth: int = 0) -> str | None:
        if depth > 5 or node is None:
            return None
        if isinstance(node, dict):
            for k in _UID_KEYS:
                if k in node and node[k] not in (None, ""):
                    return str(node[k])
            for v in node.values():
                got = ResultMonitor._deep_find_uid(v, depth=depth + 1)
                if got:
                    return got
        elif isinstance(node, list):
            for item in node:
                got = ResultMonitor._deep_find_uid(item, depth=depth + 1)
                if got:
                    return got
        return None

    @staticmethod
    def _is_rtc_noise(body: Any) -> bool:
        """Agora/WebRTC 语音 SDK 遥测识别：命中特征键即噪声（仅在无游戏 msgType 时调用）。"""
        if isinstance(body, dict):
            return any(k in body for k in _RTC_NOISE_KEYS)
        return False

    def _pick_me(self, players: list[dict[str, Any]]) -> dict[str, Any] | None:
        for p in players:
            if self.my_uid and p["uid"] == self.my_uid:
                return p
            if self.my_name and p["name"].strip().lower() == self.my_name:
                return p
        return None

    def _signature(self, msg_type: str, players: list[dict[str, Any]]) -> str:
        items = sorted(f"{p['name']}:{p['seat']}:{p['delta']}" for p in players)
        return f"{msg_type}|" + "|".join(items)

    @staticmethod
    def _zero_sum_ok(players: list[dict[str, Any]]) -> bool:
        """三人零和：所有 sumWinBonus 之和应为 0。"""
        return sum(int(p["delta"]) for p in players) == 0

    def _schedule_debounced_record(
        self, msg_type: str, players: list[dict[str, Any]], payload: dict[str, Any],
    ) -> None:
        """同一局可能连发多帧 3016：去抖后只记最后一帧（更准确）。"""
        with _state_lock:
            self._pending_debounced = (msg_type, players, payload)
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                self._debounce_sec, self._flush_debounced_record,
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _flush_debounced_record(self) -> None:
        with _state_lock:
            pending = self._pending_debounced
            self._pending_debounced = None
            self._debounce_timer = None
        if not pending:
            return
        msg_type, players, payload = pending
        if not self._zero_sum_ok(players):
            return
        sig = self._signature(msg_type, players)
        now_ts = time.time()
        with _state_lock:
            self._recent_sig = {k: v for k, v in self._recent_sig.items() if now_ts - v < 120}
            if sig in self._recent_sig:
                return
            self._recent_sig[sig] = now_ts
        self._record(msg_type, players, payload)

    def _emit_settle_note(self, msg: str) -> None:
        """集成模式也输出关键结算行（写入 settlement.log 前后各打一行）。"""
        print(f"{_now_hms()} {msg}", flush=True)

    def handle(self, payload: dict[str, Any]) -> None:
        with _state_lock:
            self._events += 1
            text = str(payload.get("text") or "")
            msg_type = _coerce_msg_type(payload, text)
            if msg_type and not payload.get("msgType"):
                payload = dict(payload)
                payload["msgType"] = msg_type
            direction = str(payload.get("direction") or payload.get("dir") or "?")

            # 候选体：优先解析对象 body，否则尝试解析 text
            candidates: list[Any] = []
            if payload.get("body") is not None:
                candidates.append(payload.get("body"))
            if payload.get("data") is not None:
                candidates.append(payload.get("data"))
            if text:
                try:
                    candidates.append(json.loads(text))
                except Exception:
                    pass
            candidates.append(payload)

            players: list[dict[str, Any]] = []
            best_body: Any = candidates[0] if candidates else payload
            for c in candidates:
                pl = _find_player_list(c)
                if pl:
                    players = pl
                    best_body = c
                    break

            # 真·结算：只认 3016/3017 + coinChanged/sumWinBonus（不认 3002 等的 coin 总余额）
            settlement_players: list[dict[str, Any]] = []
            if msg_type in _SETTLEMENT_OBSERVE_TYPES:
                for c in candidates:
                    if isinstance(c, dict):
                        sp = _parse_tongits_settlement(msg_type, c)
                        if sp:
                            settlement_players = sp
                            best_body = c
                            break

            # 白名单门：只处理“游戏帧”=有 msgType / C2N_ 类型，或本身就是 ≥2 玩家的结算表。
            is_game = bool(msg_type) or len(players) >= 2 or len(settlement_players) >= 2
            if not is_game:
                self._noise_dropped += 1
                if self._noise_dropped % 200 == 0:
                    print(f"{_now_hms()} [噪声] 已丢弃 {self._noise_dropped} 帧非游戏遥测（与胜负无关）", flush=True)
                return

            # 自我识别只在游戏帧上做，避免学到 Agora 语音 uid。
            self._maybe_learn_self(payload, text)

            # 结算候选（仅用于日志高亮）：真结算帧 3016/3017 且解析出 ≥2 人盈亏
            settle_like = msg_type in _SETTLEMENT_OBSERVE_TYPES and len(settlement_players) >= 2

            # ---- 自学习日志：把关键信息一步步打出来，逐渐看清真实协议 ----
            mt = msg_type or "(none)"
            # 计数键：有 msgType 用之；否则用原文前缀签名，保证不同形态封包各有详打额度。
            if msg_type:
                dkey = msg_type
            else:
                sig_src = re.sub(r"\d+", "#", text[:40]) if text else "(empty)"
                dkey = f"raw:{sig_src}"
            self.type_counts[dkey] = self.type_counts.get(dkey, 0) + 1
            cnt = self.type_counts[dkey]
            had_body = payload.get("body") is not None or any(
                isinstance(c, (dict, list)) and c is not payload for c in candidates[:-1]
            )
            self._observe_log(mt, direction, cnt, best_body, settlement_players or players, settle_like,
                              text=text, had_body=had_body)

            # discover：集成模式不写；独立 discover 模式才落盘
            blob = json.dumps(payload, ensure_ascii=False)[:4000]
            if self.discover and not self._quiet_console and (settle_like or _COIN_KEYWORDS.search(blob)):
                with self.discover_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(
                        {"at": _now_iso(), "msg_type": msg_type,
                         "n_players": len(settlement_players or players), "payload": payload},
                        ensure_ascii=False) + "\n")

            if len(settlement_players) < 2:
                if msg_type == "3016" and self._on_settlement:
                    now_hint = time.time()
                    if now_hint - self._last_3016_hint_at >= 2.0:
                        self._last_3016_hint_at = now_hint
                        self._emit_settle_note(
                            f"[结算] 3016 帧未就绪（players={len(settlement_players)}，"
                            "等待含 sumWinBonus 的完整帧）"
                        )
                return  # 不是可记账的结算帧

            # 3017/3021 are fallback settlement candidates. 3016 remains the preferred SSOT.
            if msg_type == "3017":
                dedup_sec = float(os.environ.get("TONGITS_SETTLE_DEDUP_SEC") or "120")
                if self._last_primary_settle_at and (time.time() - self._last_primary_settle_at) < dedup_sec:
                    if not self._quiet_console:
                        me = self._pick_me(settlement_players)
                        my_d = me["delta"] if me else "?"
                        print(f"{_now_hms()} [结算] 跳过 3017（{dedup_sec:.0f}s 内已有 3016 记账，"
                              f"coinChanged={my_d} 非单局盈亏）", flush=True)
                    return
                if os.environ.get("TONGITS_SETTLE_ALLOW_3017", "").strip().lower() not in ("1", "true", "yes"):
                    if not self._quiet_console:
                        me = self._pick_me(settlement_players)
                        my_d = me["delta"] if me else "?"
                        print(f"{_now_hms()} [结算] 跳过 3017（默认不记账，以 3016 sumWinBonus 为准；"
                              f"coinChanged={my_d}）", flush=True)
                    return
            elif msg_type == "3021":
                dedup_sec = float(os.environ.get("TONGITS_SETTLE_DEDUP_SEC") or "120")
                if self._last_primary_settle_at and (time.time() - self._last_primary_settle_at) < dedup_sec:
                    if not self._quiet_console:
                        print(
                            f"{_now_hms()} [结算] 跳过 3021（{dedup_sec:.0f}s 内已有 3016 记账）",
                            flush=True,
                        )
                    return
                if not _env_bool("TONGITS_SETTLE_ALLOW_3021", True):
                    if not self._quiet_console:
                        me = self._pick_me(settlement_players)
                        my_d = me["delta"] if me else "?"
                        print(f"{_now_hms()} [结算] 跳过 3021 fallback（TONGITS_SETTLE_ALLOW_3021=0，my={my_d}）", flush=True)
                    return
                if self._on_settlement:
                    me = self._pick_me(settlement_players)
                    my_d = me["delta"] if me else "?"
                    self._emit_settle_note(
                        f"[结算] 使用 3021 正式服 fallback 记账候选 我方 {my_d}"
                    )
            elif msg_type not in _SETTLEMENT_RECORD_TYPES:
                return

            if not self._zero_sum_ok(settlement_players):
                if msg_type in _SETTLEMENT_OBSERVE_TYPES and self._on_settlement:
                    total = sum(int(p["delta"]) for p in settlement_players)
                    self._emit_settle_note(
                        f"[结算] {msg_type} 零和校验失败 sum={total}，跳过本帧"
                    )
                return

            # 启动后短 warmup：CDP 附着时会回放历史帧，避免把上一局结算误记为本局
            if (time.time() - self._started_at) < self._warmup_sec:
                me = self._pick_me(settlement_players)
                my_d = me["delta"] if me else "?"
                self._emit_settle_note(
                    f"[结算] 跳过（启动 warmup {self._warmup_sec:.0f}s 内，疑为历史回放）"
                    f" msgType={msg_type} 我方 {my_d}"
                )
                return

            # 去抖：连发多帧时只记最后一帧（约 2.5s 内无新帧才落盘）
            if msg_type in ("3016", "3021") and self._on_settlement:
                me = self._pick_me(settlement_players)
                my_d = me["delta"] if me else "?"
                self._emit_settle_note(
                    f"[结算] {msg_type} 已收帧 我方 {my_d}，{self._debounce_sec:.1f}s 去抖后落盘…"
                )
            self._schedule_debounced_record(msg_type, settlement_players, payload)

    def _observe_log(self, mt: str, direction: str, cnt: int, body: Any,
                     players: list[dict[str, Any]], settle_like: bool,
                     *, text: str = "", had_body: bool = True) -> None:
        """渐进式打印：新类型高亮全打，老类型前 N 帧详打、之后只计数；疑似结算永远详打。"""
        if self._quiet_observe:
            return
        new_type = cnt == 1
        if not verbose:
            # 收敛：每出现 25 次提示一次，避免刷屏又不完全静默
            if cnt % 25 == 0:
                print(f"{_now_hms()} [帧] msgType={mt} 已累计 {cnt} 帧（已收敛，仅计数）", flush=True)
            return

        tag = "[新类型]" if new_type else ("[疑似结算]" if settle_like else "[帧]")
        hint = _MSGTYPE_HINTS.get(mt, "")
        if mt in _ROOM_SYNC_MSG_TYPES:
            tag = "[进房同步]" if new_type else tag
        schema = _schema(body)
        if len(schema) > 600:
            schema = schema[:600] + "…"
        coins_txt = ""
        if mt == "3016":
            coins = _coin_like_leaves(body)
            if coins:
                shown = [c for c in coins if "sumWinBonus" in c[0] or "WinBonus" in c[0]][:6]
                if not shown:
                    shown = coins[:6]
                coins_txt = " 本局盈亏=" + ", ".join(f"{p}={v:+d}" for p, v in shown)
        elif mt == "3017":
            coins_txt = " （3017 coinChanged 为累计/补充，不记账；以 3016 为准）"
        elif mt in _ROOM_SYNC_MSG_TYPES:
            coins_txt = " （coin/coinChanged=入座快照，非本局结算，已忽略记账）"
        elif hint:
            coins_txt = f" ({hint})"
        plinfo = f" 玩家={len(players)}" if players else ""
        # 解析不出结构（非 JSON / 二进制 / 自定义封包）时，打印原文预览，便于看清真实线格式。
        raw_txt = ""
        if not had_body and text and not str(text).startswith("["):
            preview = text[:240].replace("\n", "\\n")
            raw_txt = f" 原文={preview!r}"
        elif not had_body and text:
            raw_txt = f" 原文(非文本)={text[:60]!r}"
        print(
            f"{_now_hms()} {tag} msgType={mt} {direction} 第{cnt}帧{plinfo} schema={schema}{coins_txt}{raw_txt}",
            flush=True,
        )

    def _record(self, msg_type: str, players: list[dict[str, Any]], payload: dict[str, Any]) -> None:
        me = self._pick_me(players)
        my_delta = me["delta"] if me else None
        if my_delta is None:
            # 无法确定我方：按“最像本人”的策略提示，仍记录但标 unknown
            outcome = "UNKNOWN"
        elif my_delta > 0:
            outcome = "WIN"
        elif my_delta < 0:
            outcome = "LOSE"
        else:
            outcome = "DRAW"

        self.game_no += 1
        if outcome == "WIN":
            self.wins += 1
        elif outcome == "LOSE":
            self.losses += 1
        elif outcome == "DRAW":
            self.draws += 1
        if my_delta is not None:
            self.net += my_delta

        opp = [p for p in players if p is not me]
        opp_txt = " | ".join(f"{p['name'] or p['uid'] or p['seat']} {p['delta']:+d}" for p in opp)
        my_txt = f"{my_delta:+d}" if my_delta is not None else "未知"

        line = (
            f"[结算] 第{self.game_no}局 {self._cn(outcome)} "
            f"我方 {my_txt} | 对手 {opp_txt or '-'} "
            f"｜累计 净{self.net:+d} 胜{self.wins} 负{self.losses} 平{self.draws}"
            f"（msgType={msg_type or '-'}）"
        )
        ts_full = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S",
        )
        with self.settlement_log_path.open("a", encoding="utf-8") as f:
            f.write(f"{ts_full} {line}\n")

        if not self._quiet_console:
            print(f"{_now_hms()} {line}", flush=True)
        elif self._on_settlement:
            # 集成模式：主 bot logger + 控制台各打一行
            self._emit_settle_note(line)
        else:
            # 独立运行且未接回调：可选保留结构化留痕
            if _env_bool("TONGITS_RESULT_VERBOSE", False):
                with self.jsonl_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(
                        {"at": _now_iso(), "game_no": self.game_no, "outcome": outcome,
                         "my_delta": my_delta, "players": players, "msg_type": msg_type,
                         "raw": payload}, ensure_ascii=False) + "\n")
                with self.csv_path.open("a", newline="", encoding="utf-8-sig") as f:
                    csv.writer(f).writerow([
                        _now_iso(), self.game_no, outcome,
                        my_delta if my_delta is not None else "",
                        self.net, self.wins, self.losses, self.draws,
                        opp_txt, msg_type,
                    ])

        if msg_type == "3016":
            self._last_primary_settle_at = time.time()

        if self._on_settlement:
            try:
                self._on_settlement({
                    "game_no": self.game_no,
                    "outcome": outcome,
                    "my_delta": my_delta,
                    "opponents": opp,
                    "msg_type": msg_type,
                    "line": line,
                    "net": self.net,
                    "wins": self.wins,
                    "losses": self.losses,
                    "draws": self.draws,
                    "at": _now_iso(),
                })
            except Exception:
                pass

    @staticmethod
    def _cn(outcome: str) -> str:
        return {"WIN": "胜", "LOSE": "负", "DRAW": "平", "UNKNOWN": "未知"}.get(outcome, outcome)

    def health(self) -> dict[str, Any]:
        with _state_lock:
            return {
                "ok": True, "events": self._events, "my_uid": self.my_uid,
                "my_name": self.my_name, "games": self.game_no,
                "wins": self.wins, "losses": self.losses, "draws": self.draws,
                "net": self.net,
            }


def _build_handler(monitor: ResultMonitor):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, obj: dict[str, Any]) -> None:
            raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/health", "/result/status"):
                self._json(200, monitor.health())
            else:
                self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/proto/update":
                self._json(404, {"ok": False, "error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                self._json(400, {"ok": False, "error": "empty_body"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self._json(400, {"ok": False, "error": "invalid_json"})
                return
            if isinstance(payload, dict):
                try:
                    monitor.handle(payload)
                except Exception as exc:  # 监控不可拖死接收
                    print(f"[monitor] handle error: {exc}", flush=True)
            self._json(200, {"ok": True})

        def log_message(self, _f: str, *_a: Any) -> None:
            return

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="Tongits 胜负结算监控（纯协议）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=17889)
    ap.add_argument("--my-name", default="victor", help="我方玩家昵称（用于在结算列表里定位本人）")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parent / "omnioutput"))
    ap.add_argument("--discover", action="store_true", help="落盘疑似结算/金币原始帧，便于锁定字段")
    args = ap.parse_args()

    monitor = ResultMonitor(Path(args.out_dir), args.my_name, args.discover)
    server = ThreadingHTTPServer((args.host, args.port), _build_handler(monitor))
    print(
        f"[monitor] 胜负结算监控已启动 http://{args.host}:{args.port}  "
        f"我方={args.my_name} discover={args.discover}\n"
        f"[monitor] CSV={monitor.csv_path}\n"
        f"[monitor] 请在浏览器控制台贴入 tongits_result_monitor_snippet.js",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[monitor] 退出。", flush=True)
        if monitor.type_counts:
            hist = sorted(monitor.type_counts.items(), key=lambda kv: -kv[1])
            print("[monitor] 本次见到的 msgType 直方图（次数降序）：", flush=True)
            for mt, c in hist:
                print(f"    msgType={mt:<10} {c} 帧", flush=True)
        h = monitor.health()
        print(f"[monitor] 共 {h['games']} 局：胜{h['wins']} 负{h['losses']} 平{h['draws']} 净{h['net']:+d}", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
