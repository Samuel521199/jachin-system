#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 牌局状态提取器 — 截屏 + Qwen-VL-Max 端到端（无 OpenCV / OmniParser）。

用法（仓库根目录）::

  python scripts/vlm_board_analyzer.py
    # 倒计时截屏后默认保存到 scripts/omnioutput/{时间戳}_board_raw.jpg
  python scripts/vlm_board_analyzer.py --image scripts/omnioutput/xxx_board_raw.jpg
  python scripts/vlm_board_analyzer.py --countdown 5 --save scripts/omnioutput/my_capture.jpg

环境变量：
  TONGITS_VLM_PROVIDER=gemini|qwen
  GEMINI_API_KEY + TONGITS_VLM_MODEL（默认 gemini-2.0-flash）
  或 DASHSCOPE_API_KEY + TONGITS_VLM_MODEL（默认 qwen3.5-flash）
  TONGITS_VLM_TIMEOUT（默认 60，牌局 JSON 较复杂可适当加大）
  TONGITS_BOARD_REFINE_OPPONENT（smart|always|off，默认 smart：仅三同点可疑时裁切精检）
  TONGITS_BOARD_REFINE_MY_MELDS（always|empty|off，默认 empty：主扫 my_melds=[] 时裁切精检）
  TONGITS_BOARD_INFER_MISSING_SUIT（已废弃，勿开启：会把合法明刻臆造成明杠）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ALL_SUITS = frozenset({"S", "H", "C", "D"})

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

try:
    from dotenv import load_dotenv

    for _p in (ROOT / ".env", ROOT / "core" / ".env", Path.home() / ".jachin" / ".env"):
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
except ImportError:
    pass

from vision_proxy_qwen import (  # noqa: E402
    default_vlm_model,
    strip_markdown_json,
    vlm_provider,
    _vlm_chat,
)

logger = logging.getLogger("vlm_board_analyzer")

OMNI_OUTPUT_DIR = SCRIPTS / "omnioutput"


def board_screenshot_save_path(
    *,
    save_dir: Path | None = None,
    explicit: str | Path | None = None,
) -> Path:
    """实时截屏默认落盘路径（与 omnioutput 其它流水线时间戳风格一致）。"""
    if explicit:
        out = Path(explicit)
        out.parent.mkdir(parents=True, exist_ok=True)
        return out
    d = save_dir or OMNI_OUTPUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    return d / f"{ts}_board_raw.jpg"

BOARD_STATE_PROMPT = """你是 Tongits（菲律宾拉米）牌局状态分析 AI。请观察截图，识别所有可见扑克牌。

【牌面编码】每张牌用「花色字母+点数」：H红桃、S黑桃、C梅花、D方块；点数 A/2-10/J/Q/K。例：H9、SA、D10、CQ。

【画面分区 — 严禁串区】
- 屏幕最底部：我的手牌（my_hand）。
- **my_melds（极易漏识 — 必查）**：在四色大按钮（Drop/Fight/Group/Dump）**正上方**的**灰色/半透明横条槽位**内，是我方已打出（Drop）的明牌。
  这些牌常比手牌**发灰、略透明**，但仍是完整牌面，须逐张读取；常见为水平一排的同花顺（如方块A-2-3-4-5-6）或明刻。
  **禁止**因发灰就输出 my_melds:[]；只要槽位内可见牌角/点数，就必须写入 my_melds。
- **left_opponent**：屏幕**左上方**对手头像旁（如 Beason），与 my_melds 相距很远；不要把对手牌写进 my_melds。
- **right_opponent**：屏幕**右上方**对手头像旁。
- 画面中央：右侧弃牌顶牌；左侧摸牌堆（牌背+剩余张数）。

【点数易混淆 — 务必逐字辨认】
- **A 与 K**：A 为尖顶+中间横杠三角；K 为竖干+两条斜腿。二者严禁互换。
- **一副牌约束**：同一 rank（如 A）在桌面可见区（手牌+三方明牌+弃牌顶）合计**最多 4 张**。若 my_melds 与 left_opponent 各出现 3 张 A，必定有一处把 **K 误读成 A** 或串区，须重新辨认 my_melds 区。

【对手明牌叠放 — Tongits 规则：3 张与 4 张都合法】
左右上方明牌水平叠放，每张仅左侧圆角露出点数+花色。
- **明刻 set**：恰好 3 张同点异花（Tongits 常见亮牌方式）
- **明杠 quad**：恰好 4 张同点异花（四张同点一次亮出）
必须先数清物理叠放「层数」，再填 cards；**层数=3 则 kind=set 且 cards 长度=3，禁止臆造第 4 张**；层数=4 则 kind=quad 且 cards 长度=4。
漏数（4 层只写 3 张）与凑数（3 层写 4 张）都是严重错误。

【melds 组类型 kind】
- quad：恰好四张同点异花
- set：恰好三张同点异花
- sequence：同花顺（若可见）
- special：手牌里 UI 标记 Special 的成组

请只输出一个 JSON 对象（不要 markdown、不要解释），结构如下：

{
  "my_hand": {
    "special_groups": [["D8","C8","H8","S8"]],
    "loose": ["D2","H3","C4","H7","DK"]
  },
  "my_melds": [
    {"kind": "sequence", "cards": ["DA","D2","D3","D4","D5","D6"]}
  ],
  "left_opponent": {
    "player_name": "Edwolo",
    "melds": [{"kind": "quad", "cards": ["S5","D5","C5","H5"]}]
  },
  "right_opponent": {
    "melds": [{"kind": "set", "cards": ["S9","H9","D9"]}]
  },
  "center": {
    "discard_top": "SK",
    "draw_pile_remaining": 8
  }
}

规则：无牌用 [] 或 null；discard_top 仅一张或 null；draw_pile_remaining 为整数或 null（看不清则 null）。
同点异花：3 张→set，4 张→quad（不要用 sequence 表示四张同点）。"""

OPPONENT_REFINE_PROMPT = """这是 Tongits 截图中【{side_label}】的裁切放大图，仅含该对手已亮出的明牌组。

【Tongits 规则 — 先数层数，再读牌】
明牌水平叠放，每张仅左侧圆角露点数+花色（H/S/C/D + A-10/J/Q/K）。
**合法形态只有两种**：① 恰好 3 层 → 明刻 set；② 恰好 4 层 → 明杠 quad。不存在「3 张牌却凑成 4 张」。
步骤：先数 visible_card_count（肉眼可见叠放层数，整数 3 或 4），再逐层读牌。
- visible_card_count=3 → kind=set，cards 长度必须=3
- visible_card_count=4 → kind=quad，cards 长度必须=4（检查最左/最右是否还有被挡的角标）
被挡的牌可能只剩白边，仅当 visible_card_count=4 时才计入第 4 张。

若该对手有**多组**明牌（如一组刻子+一组顺子），melds 数组须**全部列出**，不可只输出其中一组。
只输出 JSON（无 markdown）：
{{"visible_card_count":6,"melds":[{{"kind":"set","cards":["HA","CA","DA"]}},{{"kind":"sequence","cards":["H5","H6","H7"]}}]}}
单组叠放时 visible_card_count 填该组层数(3或4)；多组时填可见牌总张数。
无明牌：{{"visible_card_count":0,"melds":[]}}"""

MY_MELDS_REFINE_PROMPT = """这是 Tongits 截图裁切：仅含【我方已打出明牌】区域 — 在 Drop/Fight/Group/Dump 四色按钮正上方的灰色/半透明横条槽位。
**禁止**读取：最底部手牌、四色按钮文字、画面上方对手明牌、中央牌堆。

【最高优先级 — 灰色槽位内的牌】
已打出明牌常**发灰、半透明**，看起来像 UI 背景，但每张仍有花色与点数，必须全部识别。
常见形态：
- 同花顺 sequence：水平一排，如方块 A23456 → ["DA","D2","D3","D4","D5","D6"]
- 明刻 set：三同点异花
- 明杠 quad：四同点异花
若灰色槽位内有多张牌，cards 长度须与可见张数一致（顺子可 3 张以上）。

【易错】A 与 K 字形不同；不要把对手区的牌抄进来。

只输出 JSON（无 markdown）：
{{"melds":[{{"kind":"sequence","cards":["DA","D2","D3","D4","D5","D6"]}}]}}
槽位内确无牌面则 {{"melds":[]}}。"""

OPPONENT_COUNT_ONLY_PROMPT = """这是 Tongits 对手明牌区的裁切图。请只数水平叠放明牌的物理层数（数左侧露出的牌角/层数，不是猜点数）。

Tongits 中一层就是一张牌；合法结果为 0、3 或 4（3=明刻，4=明杠）。
只输出 JSON：{{"visible_card_count": 3}} 或 {{"visible_card_count": 4}} 或 {{"visible_card_count": 0}}"""

ZONE_PLAYER_HAND = "my_hand"
ZONE_CENTER_DISCARD = "center_discard"
ZONE_OPPONENT_LEFT = "left_opponent_melds"
ZONE_OPPONENT_RIGHT = "right_opponent_melds"

ZONE_KEYS: tuple[str, ...] = (
    ZONE_OPPONENT_LEFT,
    ZONE_OPPONENT_RIGHT,
    ZONE_CENTER_DISCARD,
    "my_melds",
    ZONE_PLAYER_HAND,
)

ZONE_LABELS: dict[str, str] = {
    "left_opponent_melds": "左侧对手明牌",
    "right_opponent_melds": "右侧对手明牌",
    "center_discard": "中央弃牌顶牌",
    "my_melds": "我方已打出明牌",
    "my_hand": "我的手牌",
}

_CARD_LABEL_RE = re.compile(
    r"^([SHCD])(A|[2-9]|10|J|Q|K)$",
    re.IGNORECASE,
)

# 内部规范码 S/H/C/D → 中文花色（对外展示与 JSON 导出）
SUIT_TO_CN: dict[str, str] = {
    "S": "黑桃",
    "H": "红桃",
    "C": "梅花",
    "D": "方块",
}

MELD_KIND_LABELS: dict[str, str] = {
    "quad": "一组四张（明杠）",
    "set": "一组三张（明刻）",
    "triplet": "一组三张（明刻）",
    "sequence": "顺子",
    "special": "Special",
    "group": "一组牌",
    "unknown": "明牌组",
}

BoardState = dict[str, Any]

CN_SUIT_TO_CODE: dict[str, str] = {cn: code for code, cn in SUIT_TO_CN.items()}

# 兼容 VLM 偶发「花色在后」如 QH、10D
_CARD_LABEL_ALT_RE = re.compile(
    r"^([2-9]|10|J|Q|K|A)([SHCD])$",
    re.IGNORECASE,
)


def _normalize_card_token(item: Any) -> str | None:
    """将 VLM 单项转为标准标签如 H9、HA（花色在前）。"""
    if item is None:
        return None
    if isinstance(item, str):
        from vision_proxy_qwen import canonical_card_label

        return canonical_card_label(item)
    if isinstance(item, dict):
        suit = str(item.get("suit") or "").strip().upper()
        rank = str(item.get("rank") or "").strip().upper()
        if suit and rank:
            return _normalize_card_token(f"{suit}{rank}")
    return None


def _normalize_zone_cards(raw: Any) -> list[str]:
    """区域牌列表规范化。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        one = _normalize_card_token(raw)
        return [one] if one else []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        label = _normalize_card_token(item)
        if label and label not in out:
            out.append(label)
    return out


def _card_rank(label: str) -> str | None:
    code = _normalize_card_token(label)
    if not code:
        return None
    m = _CARD_LABEL_RE.match(code)
    return m.group(2).upper() if m else None


def _card_suit(label: str) -> str | None:
    code = _normalize_card_token(label)
    if not code:
        return None
    m = _CARD_LABEL_RE.match(code)
    return m.group(1).upper() if m else None


def _infer_kind_from_cards(cards: list[str]) -> str:
    """根据牌面点数/花色推断 meld 类型（修正 VLM 把四张 10 标成顺子等）。"""
    if not cards:
        return "unknown"
    ranks = [_card_rank(c) for c in cards]
    suits = [_card_suit(c) for c in cards]
    ranks_ok = [r for r in ranks if r]
    suits_ok = [s for s in suits if s]
    if len(ranks_ok) == len(cards) and len(set(ranks_ok)) == 1:
        if len(cards) >= 4:
            return "quad"
        if len(cards) == 3:
            return "set"
    if len(cards) >= 3 and len(set(suits_ok)) == 1 and len(suits_ok) == len(cards):
        rank_val = {"A": 1, "J": 11, "Q": 12, "K": 13}
        vals = [rank_val.get(r, int(r) if r.isdigit() else -1) for r in ranks_ok]
        if all(v > 0 for v in vals) and max(vals) - min(vals) == len(cards) - 1:
            return "sequence"
    return "unknown"


def _finalize_meld(meld: dict[str, Any]) -> dict[str, Any]:
    cards = _normalize_card_group(meld)
    if not cards:
        return meld
    inferred = _infer_kind_from_cards(cards)
    kind = str(meld.get("kind") or "").lower()
    if inferred in ("quad", "set", "sequence"):
        if kind != inferred:
            logger.debug(
                "[board] meld kind 修正 %s → %s cards=%s",
                kind or "?",
                inferred,
                cards,
            )
        kind = inferred
    else:
        kind = _normalize_meld_kind(kind, card_count=len(cards))
    return {"kind": kind, "cards": cards}


def _normalize_meld_kind(raw: Any, *, card_count: int = 0) -> str:
    text = str(raw or "").strip().lower()
    if text in MELD_KIND_LABELS:
        return text
    if text in ("four", "4", "kong", "杠", "明杠"):
        return "quad"
    if text in ("three", "3", "pong", "刻", "明刻", "triplet"):
        return "set"
    if text in ("run", "顺", "straight"):
        return "sequence"
    if "special" in text:
        return "special"
    return "unknown"


def _normalize_card_group(raw: Any) -> list[str]:
    if isinstance(raw, dict):
        return _normalize_zone_cards(raw.get("cards"))
    return _normalize_zone_cards(raw)


def _normalize_meld_groups(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        cards = _normalize_zone_cards(raw)
        if not cards:
            return []
        return [_finalize_meld({"kind": None, "cards": cards})]
    groups: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            cards = _normalize_zone_cards(item.get("cards"))
            if not cards:
                continue
            groups.append(_finalize_meld({"kind": item.get("kind"), "cards": cards}))
        else:
            cards = _normalize_card_group(item)
            if cards:
                groups.append(_finalize_meld({"kind": None, "cards": cards}))
    return groups


def _normalize_hand_section(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        special: list[list[str]] = []
        for g in raw.get("special_groups") or raw.get("special") or []:
            cards = _normalize_card_group(g)
            if cards:
                special.append(cards)
        loose = _normalize_zone_cards(
            raw.get("loose") or raw.get("loose_cards") or raw.get("ungrouped")
        )
        return {"special_groups": special, "loose": loose}
    return {"special_groups": [], "loose": _normalize_zone_cards(raw)}


def _legacy_flat_to_rich(obj: dict[str, Any]) -> BoardState:
    """旧版五区平铺 JSON → 结构化 BoardState。"""
    my_melds_raw = _normalize_zone_cards(obj.get("my_melds"))
    left_raw = _normalize_zone_cards(obj.get("left_opponent_melds"))
    right_raw = _normalize_zone_cards(obj.get("right_opponent_melds"))
    center_raw = _normalize_zone_cards(obj.get("center_discard"))

    def _blob_to_melds(cards: list[str]) -> list[dict[str, Any]]:
        if not cards:
            return []
        return [
            {
                "kind": _normalize_meld_kind(None, card_count=len(cards)),
                "cards": cards,
            }
        ]

    discard_top = center_raw[0] if center_raw else None
    return {
        "my_hand": _normalize_hand_section(obj.get("my_hand")),
        "my_melds": _blob_to_melds(my_melds_raw),
        "left_opponent": {
            "player_name": str(obj.get("left_opponent_name") or "").strip(),
            "melds": _blob_to_melds(left_raw),
        },
        "right_opponent": {"melds": _blob_to_melds(right_raw)},
        "center": {
            "discard_top": discard_top,
            "draw_pile_remaining": obj.get("draw_pile_remaining"),
        },
    }


def _is_rich_board_payload(obj: dict[str, Any]) -> bool:
    if "center" in obj or "left_opponent" in obj or "right_opponent" in obj:
        return True
    mh = obj.get("my_hand")
    return isinstance(mh, dict)


def _parse_rich_board_obj(obj: dict[str, Any]) -> BoardState:
    left = obj.get("left_opponent") if isinstance(obj.get("left_opponent"), dict) else {}
    right = obj.get("right_opponent") if isinstance(obj.get("right_opponent"), dict) else {}
    center = obj.get("center") if isinstance(obj.get("center"), dict) else {}

    discard_raw = center.get("discard_top")
    discard_top: str | None = None
    if discard_raw is not None:
        if isinstance(discard_raw, list):
            discards = _normalize_zone_cards(discard_raw)
            discard_top = discards[0] if discards else None
        else:
            discard_top = _normalize_card_token(discard_raw)

    draw_rem = center.get("draw_pile_remaining")
    if draw_rem is not None:
        try:
            draw_rem = int(draw_rem)
        except (TypeError, ValueError):
            draw_rem = None

    return {
        "my_hand": _normalize_hand_section(obj.get("my_hand")),
        "my_melds": _normalize_meld_groups(obj.get("my_melds")),
        "left_opponent": {
            "player_name": str(left.get("player_name") or "").strip(),
            "melds": _normalize_meld_groups(left.get("melds")),
        },
        "right_opponent": {
            "melds": _normalize_meld_groups(right.get("melds")),
        },
        "center": {
            "discard_top": discard_top,
            "draw_pile_remaining": draw_rem,
        },
    }


def is_rich_board_state(state: dict[str, Any]) -> bool:
    return _is_rich_board_payload(state)


def parse_board_state_json(text: str) -> BoardState:
    """解析 VLM 回复为结构化牌局快照；解析失败抛 ValueError。"""
    cleaned = strip_markdown_json(text)
    last_err = ""
    for candidate in (cleaned, text):
        try:
            obj = json.loads(candidate)
            if not isinstance(obj, dict):
                last_err = "根节点不是 JSON 对象"
                continue
            if _is_rich_board_payload(obj):
                return _parse_rich_board_obj(obj)
            if any(k in obj for k in ZONE_KEYS):
                return _legacy_flat_to_rich(obj)
            last_err = "缺少可识别的牌局字段"
        except json.JSONDecodeError as e:
            last_err = str(e)

    m = re.search(r"\{[\s\S]*\}", cleaned or text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                if _is_rich_board_payload(obj):
                    return _parse_rich_board_obj(obj)
                return _legacy_flat_to_rich(obj)
        except json.JSONDecodeError as e:
            last_err = str(e)

    raise ValueError(f"无法解析牌局 JSON: {last_err}")


def board_state_to_legacy_zones(state: BoardState) -> dict[str, list[str]]:
    """结构化快照 → 旧版五区平铺（供兼容逻辑使用）。"""
    if not is_rich_board_state(state):
        return {key: _normalize_zone_cards(state.get(key)) for key in ZONE_KEYS}

    hand = state.get("my_hand") or {}
    my_hand_cards: list[str] = []
    for g in hand.get("special_groups") or []:
        my_hand_cards.extend(_normalize_card_group(g))
    my_hand_cards.extend(_normalize_zone_cards(hand.get("loose")))

    def _flatten_melds(side: dict[str, Any] | None) -> list[str]:
        out: list[str] = []
        for m in (side or {}).get("melds") or []:
            out.extend(_normalize_card_group(m))
        return out

    center = state.get("center") or {}
    discard = center.get("discard_top")
    center_list = [discard] if discard else []

    my_melds_flat: list[str] = []
    for m in state.get("my_melds") or []:
        my_melds_flat.extend(_normalize_card_group(m))

    return {
        ZONE_OPPONENT_LEFT: _flatten_melds(state.get("left_opponent")),
        ZONE_OPPONENT_RIGHT: _flatten_melds(state.get("right_opponent")),
        ZONE_CENTER_DISCARD: center_list,
        "my_melds": my_melds_flat,
        ZONE_PLAYER_HAND: my_hand_cards,
    }


def iter_hand_card_labels(state: BoardState | dict[str, list[str]]) -> list[str]:
    """收集手牌区全部牌面（Special 组 + 散牌）。"""
    if is_rich_board_state(state):
        hand = state.get("my_hand") or {}
        labels: list[str] = []
        for g in hand.get("special_groups") or []:
            labels.extend(_normalize_card_group(g))
        labels.extend(_normalize_zone_cards(hand.get("loose")))
        return labels
    return _normalize_zone_cards(state.get(ZONE_PLAYER_HAND))


def _refine_opponent_mode() -> str:
    return os.environ.get("TONGITS_BOARD_REFINE_OPPONENT", "smart").strip().lower()


def _normalized_card_tokens(cards: list[str]) -> list[str]:
    out: list[str] = []
    for c in cards:
        tok = _normalize_card_token(c)
        if tok and tok not in out:
            out.append(tok)
    return out


def _is_tongits_rank_set_meld(cards: list[str]) -> bool:
    """Tongits 明刻：恰好 3 张同点异花。"""
    norm = _normalized_card_tokens(cards)
    if len(norm) != 3:
        return False
    ranks = [_card_rank(c) for c in norm]
    suits = [_card_suit(c) for c in norm]
    return (
        len(set(ranks)) == 1
        and ranks[0] is not None
        and len(set(suits)) == 3
        and all(s in ALL_SUITS for s in suits)
    )


def _is_tongits_rank_quad_meld(cards: list[str]) -> bool:
    """Tongits 明杠：恰好 4 张同点异花（四门花色各一）。"""
    norm = _normalized_card_tokens(cards)
    if len(norm) != 4:
        return False
    ranks = [_card_rank(c) for c in norm]
    suits = [_card_suit(c) for c in norm]
    return (
        len(set(ranks)) == 1
        and ranks[0] is not None
        and len(set(suits)) == 4
        and all(s in ALL_SUITS for s in suits)
    )


def _primary_cards_subset_of(primary: list[str], refined: list[str]) -> bool:
    p = set(_normalized_card_tokens(primary))
    r = set(_normalized_card_tokens(refined))
    return bool(p) and p.issubset(r)


def _melds_total_cards(melds: list[dict[str, Any]]) -> int:
    return sum(len(m.get("cards") or []) for m in melds)


def _opponent_meld_suspect_undercount(melds: list[dict[str, Any]]) -> bool:
    """
    主扫「三同点明刻」可能是漏数第 4 张，值得裁切精检。
    已是 4 张或非同点组不触发。
    """
    for m in melds or []:
        cards = m.get("cards") or []
        if _is_tongits_rank_set_meld(cards):
            return True
    return False


def _parse_visible_card_count(obj: dict[str, Any]) -> int | None:
    raw = obj.get("visible_card_count")
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _parse_count_only_json(text: str) -> int | None:
    cleaned = strip_markdown_json(text)
    obj = json.loads(cleaned)
    if isinstance(obj, dict):
        return _parse_visible_card_count(obj)
    return None


_MELD_OBJ_RE = re.compile(
    r'\{\s*"kind"\s*:\s*"[^"]+"\s*,\s*"cards"\s*:\s*\[[^\]]*\]\s*\}',
    re.IGNORECASE,
)


def _extract_first_json_object(raw: str) -> str | None:
    """从残片中截取第一个完整 {...} 对象。"""
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(raw)):
        ch = raw[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def _salvage_melds_response(text: str) -> tuple[list[dict[str, Any]], int | None]:
    """
    解析 VLM 精检 JSON；兼容残片如 {"kind":"set","cards":["H5","H6","H7"]}]}。
    """
    raw = (text or "").strip()
    # 精检残片须优先用 raw：strip_markdown_json 可能把 cards 内 [...] 误提为根节点
    cleaned = strip_markdown_json(raw)
    visible: int | None = None
    melds_raw: Any = None
    last_err = ""

    for candidate in (raw, cleaned):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                visible = _parse_visible_card_count(obj)
                melds_raw = obj.get("melds")
                if melds_raw is not None:
                    return _normalize_meld_groups(melds_raw), visible
                if "kind" in obj and "cards" in obj:
                    return _normalize_meld_groups([obj]), visible
            if isinstance(obj, list):
                return _normalize_meld_groups(obj), None
        except json.JSONDecodeError as e:
            last_err = str(e)

    m_vis = re.search(r'"visible_card_count"\s*:\s*(\d+)', raw)
    if m_vis:
        visible = int(m_vis.group(1))

    m_arr = re.search(r'"melds"\s*:\s*(\[[\s\S]*?\])', raw)
    if m_arr:
        try:
            return _normalize_meld_groups(json.loads(m_arr.group(1))), visible
        except json.JSONDecodeError as e:
            last_err = str(e)

    found = _MELD_OBJ_RE.findall(raw)
    if found:
        groups: list[dict[str, Any]] = []
        for chunk in found:
            try:
                groups.extend(_normalize_meld_groups([json.loads(chunk)]))
            except json.JSONDecodeError:
                continue
        if groups:
            logger.warning(
                "[board][refine] 从残片 JSON 恢复 %d 组 melds（原解析失败: %s）",
                len(groups),
                last_err,
            )
            return groups, visible

    single = _extract_first_json_object(raw)
    if single:
        try:
            obj = json.loads(single)
            if isinstance(obj, dict) and "kind" in obj and "cards" in obj:
                logger.warning("[board][refine] 从首个 JSON 对象恢复单组 meld")
                return _normalize_meld_groups([obj]), visible
        except json.JSONDecodeError as e:
            last_err = str(e)

    raise ValueError(f"无法解析精检 JSON: {last_err}")


def _parse_opponent_refine_json(text: str) -> tuple[list[dict[str, Any]], int | None]:
    melds, visible = _salvage_melds_response(text)
    if visible is not None and len(melds) == 1:
        cards = melds[0].get("cards") or []
        if visible in (3, 4) and len(cards) != visible:
            logger.warning(
                "[board][refine] visible_card_count=%d 与 cards 长度 %d 不一致，以层数为准截断",
                visible,
                len(cards),
            )
            melds = [_finalize_meld({"kind": melds[0].get("kind"), "cards": cards[:visible]})]
    return melds, visible


def _collect_table_cards_by_rank(state: BoardState) -> dict[str, list[tuple[str, str]]]:
    """rank → [(zone_label, card_token), ...] 含手牌/明牌/弃牌顶。"""
    hist: dict[str, list[tuple[str, str]]] = {}

    def _add(zone: str, token: str) -> None:
        rank = _card_rank(token)
        if not rank:
            return
        hist.setdefault(rank, []).append((zone, token))

    hand = state.get("my_hand") or {}
    for g in hand.get("special_groups") or []:
        for c in _normalize_card_group(g):
            _add("my_hand", c)
    for c in _normalize_zone_cards(hand.get("loose")):
        _add("my_hand", c)

    for i, m in enumerate(state.get("my_melds") or []):
        for c in m.get("cards") or []:
            _add(f"my_melds[{i}]", c)

    left = state.get("left_opponent") or {}
    for i, m in enumerate(left.get("melds") or []):
        for c in m.get("cards") or []:
            _add(f"left[{i}]", c)

    right = state.get("right_opponent") or {}
    for i, m in enumerate(right.get("melds") or []):
        for c in m.get("cards") or []:
            _add(f"right[{i}]", c)

    center = state.get("center") or {}
    top = center.get("discard_top")
    if top:
        _add("center_discard", str(top))

    return hist


def find_deck_constraint_violations(state: BoardState) -> list[dict[str, Any]]:
    """
    一副牌约束：同一 rank 在桌面可见区不得超过 4 张；
    两个独立「三同点」同 rank 明刻亦不可能（如两处各 3 张 A）。
    """
    hist = _collect_table_cards_by_rank(state)
    violations: list[dict[str, Any]] = []
    for rank, entries in hist.items():
        if len(entries) > 4:
            violations.append(
                {
                    "rank": rank,
                    "reason": "rank_count_exceeds_4",
                    "count": len(entries),
                    "entries": entries,
                }
            )
        set_zones = _count_rank_sets_of_three(state, rank)
        if len(set_zones) >= 2:
            violations.append(
                {
                    "rank": rank,
                    "reason": "duplicate_triplet_sets",
                    "zones": set_zones,
                    "count": len(entries),
                    "entries": entries,
                }
            )
    return violations


def _my_melds_roi_box(sw: int, sh: int) -> tuple[int, int, int, int]:
    """按钮正上方灰色明牌槽（排除底部手牌与中央牌堆）。"""
    y1 = int(sh * float(os.environ.get("TONGITS_ROI_MY_MELD_Y1_RATIO", "0.46")))
    y2 = int(sh * float(os.environ.get("TONGITS_ROI_MY_MELD_Y2_RATIO", "0.66")))
    x1 = int(sw * float(os.environ.get("TONGITS_ROI_MY_MELD_X1_RATIO", "0.08")))
    x2 = int(sw * float(os.environ.get("TONGITS_ROI_MY_MELD_X2_RATIO", "0.58")))
    return (max(0, x1), max(0, y1), min(sw, x2), min(sh, y2))


def _my_melds_refine_mode() -> str:
    return os.environ.get("TONGITS_BOARD_REFINE_MY_MELDS", "empty").strip().lower()


def _should_refine_my_melds(state: BoardState) -> bool:
    mode = _my_melds_refine_mode()
    if mode in ("0", "off", "false", "no"):
        return False
    if mode in ("always", "1", "true", "yes", "on"):
        return True
    # empty：主扫未识别我方明牌时补裁切精检（默认）
    return not (state.get("my_melds") or [])


def refine_my_melds_if_missing(
    state: BoardState,
    image_path: str | Path,
    *,
    model: str | None = None,
) -> BoardState:
    """主扫漏掉我方明牌（灰色槽位）时，裁切按钮上方区域二次 VLM。"""
    if not is_rich_board_state(state) or not _should_refine_my_melds(state):
        return state

    model = model or default_vlm_model()
    timeout = float(os.environ.get("TONGITS_VLM_TIMEOUT") or "60")
    primary = state.get("my_melds") or []

    try:
        refined = _refine_my_melds(image_path, model=model, timeout=timeout)
    except Exception as e:
        logger.warning("[board][my_melds] 精检失败，保留主扫: %s", e)
        return state

    if not refined:
        logger.info("[board][my_melds] 精检未发现明牌（槽位可能确实为空）")
        return state

    if not primary:
        out = dict(state)
        out["my_melds"] = refined
        logger.info(
            "[board][my_melds] 主扫为空 → 采用精检 %d 组: %s",
            len(refined),
            refined,
        )
        return out

    if _melds_total_cards(refined) > _melds_total_cards(primary):
        out = dict(state)
        out["my_melds"] = refined
        logger.info("[board][my_melds] 精检牌数更多，采用精检结果")
        return out

    return state


def _crop_my_melds_zone(image_path: str | Path) -> Path:
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("裁切 my_melds ROI 需要 Pillow") from e

    path = Path(image_path)
    img = Image.open(path)
    sw, sh = img.size
    box = _my_melds_roi_box(sw, sh)
    crop = img.crop(box)
    fd, tmp = tempfile.mkstemp(suffix="_my_melds_crop.jpg", prefix="board_")
    os.close(fd)
    out = Path(tmp)
    crop.save(str(out), format="JPEG", quality=95)
    logger.info("[board][my_melds] 裁切 ROI %s → %s", box, out)
    return out


def _refine_my_melds(
    image_path: str | Path,
    *,
    model: str,
    timeout: float,
) -> list[dict[str, Any]]:
    crop_path: Path | None = None
    try:
        crop_path = _crop_my_melds_zone(image_path)
        raw = _vlm_chat(
            MY_MELDS_REFINE_PROMPT,
            str(crop_path),
            model=model,
            max_tokens=768,
            timeout_sec=timeout,
        )
        melds, _ = _salvage_melds_response(raw)
        return melds
    finally:
        if crop_path and crop_path.is_file():
            try:
                crop_path.unlink()
            except OSError:
                pass


def resolve_deck_constraint_violations(
    state: BoardState,
    image_path: str | Path,
    *,
    model: str | None = None,
) -> BoardState:
    """违反一副牌约束时，优先重读 my_melds 区（常见 K↔A 误读/串区）。"""
    model = model or default_vlm_model()
    timeout = float(os.environ.get("TONGITS_VLM_TIMEOUT") or "60")
    out: BoardState = dict(state)

    for _ in range(2):
        violations = find_deck_constraint_violations(out)
        if not violations:
            return out

        ranks = sorted({str(v["rank"]) for v in violations})
        logger.warning(
            "[board][deck] 一副牌约束冲突 rank=%s violations=%s",
            ranks,
            [v["reason"] for v in violations],
        )

        needs_my_melds = False
        for v in violations:
            zones = v.get("zones") or []
            entries = v.get("entries") or []
            if "my_melds" in zones or any(z.startswith("my_melds") for z, _ in entries):
                needs_my_melds = True
                break
            if v.get("reason") == "rank_count_exceeds_4" and any(
                z.startswith("my_melds") for z, _ in entries
            ):
                needs_my_melds = True
                break

        if not needs_my_melds:
            logger.warning("[board][deck] 冲突不在 my_melds，保留主扫结果")
            return out

        try:
            new_melds = _refine_my_melds(image_path, model=model, timeout=timeout)
            if new_melds:
                out["my_melds"] = new_melds
                logger.info(
                    "[board][my_melds] 约束修复后重读: %s",
                    new_melds,
                )
        except Exception as e:
            logger.warning("[board][my_melds] 约束修复精检失败: %s", e)
            return out

    remaining = find_deck_constraint_violations(out)
    if remaining:
        logger.warning(
            "[board][deck] 修复后仍冲突（请人工核对）: %s",
            remaining,
        )
    return out


def _opponent_roi_box(side: str, sw: int, sh: int) -> tuple[int, int, int, int]:
    meld_y1 = int(sh * float(os.environ.get("TONGITS_ROI_MELD_Y1_RATIO", "0.14")))
    meld_y2 = int(sh * float(os.environ.get("TONGITS_ROI_MELD_Y2_RATIO", "0.50")))
    if side == "left":
        x1 = int(sw * float(os.environ.get("TONGITS_ROI_LEFT_X_MIN_RATIO", "0.02")))
        x2 = int(sw * float(os.environ.get("TONGITS_ROI_LEFT_X_MAX_RATIO", "0.40")))
    else:
        x1 = int(sw * float(os.environ.get("TONGITS_ROI_RIGHT_X_MIN_RATIO", "0.60")))
        x2 = int(sw * float(os.environ.get("TONGITS_ROI_RIGHT_X_MAX_RATIO", "0.98")))
    return (max(0, x1), max(0, meld_y1), min(sw, x2), min(sh, meld_y2))


def _crop_opponent_zone(image_path: str | Path, side: str) -> Path:
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("裁切对手 ROI 需要 Pillow") from e

    path = Path(image_path)
    img = Image.open(path)
    sw, sh = img.size
    box = _opponent_roi_box(side, sw, sh)
    crop = img.crop(box)
    fd, tmp = tempfile.mkstemp(suffix=f"_{side}_opponent_crop.jpg", prefix="board_")
    os.close(fd)
    out = Path(tmp)
    crop.save(str(out), format="JPEG", quality=95)
    logger.info("[board][refine] %s 裁切 ROI %s → %s", side, box, out)
    return out


def _side_melds_list(state: BoardState, side_key: str) -> list[dict[str, Any]]:
    if side_key == "my_melds":
        return list(state.get("my_melds") or [])
    return list((state.get(side_key) or {}).get("melds") or [])


def _count_rank_sets_of_three(state: BoardState, rank: str) -> list[str]:
    """含有「三同点」明刻的 side 名（my_melds / left_opponent / right_opponent）。"""
    zones: list[str] = []
    for key in ("my_melds", "left_opponent", "right_opponent"):
        for m in _side_melds_list(state, key):
            cards = m.get("cards") or []
            if _is_tongits_rank_set_meld(cards) and _card_rank(cards[0]) == rank:
                zones.append(key)
                break
    return zones


def _merge_multi_meld_groups(
    primary: list[dict[str, Any]],
    refined: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    """多组明牌：按牌面重叠逐组合并，禁止用更少组数覆盖主扫。"""
    if len(refined) < len(primary):
        return primary, "primary_multi_meld_keep"

    updated: list[dict[str, Any]] = []
    used: set[int] = set()
    for pm in primary:
        pc = set(_normalized_card_tokens(pm.get("cards") or []))
        best_j = -1
        best_overlap = 0
        for j, rm in enumerate(refined):
            if j in used:
                continue
            rc = set(_normalized_card_tokens(rm.get("cards") or []))
            overlap = len(pc & rc)
            if overlap > best_overlap:
                best_overlap = overlap
                best_j = j
        if best_j >= 0 and best_overlap > 0:
            used.add(best_j)
            updated.append(refined[best_j])
        else:
            updated.append(pm)

    if len(updated) != len(primary):
        return primary, "multi_merge_incomplete"
    return updated, "multi_group_merge"


def _merge_opponent_melds_conservative(
    primary: list[dict[str, Any]],
    refined: list[dict[str, Any]],
    *,
    visible_card_count: int | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """
    保守合并：Tongits 明刻(3)与明杠(4)均合法，禁止无证据 3→4 升级。
    返回 (采用结果, 决策原因)。
    """
    if not refined:
        return primary, "refined_empty"

    if len(primary) > 1:
        return _merge_multi_meld_groups(primary, refined)

    if len(primary) == 1 and len(refined) == 1:
        pc = primary[0].get("cards") or []
        rc = refined[0].get("cards") or []
        pn, rn = len(pc), len(rc)

        if rn == pn:
            if _is_tongits_rank_quad_meld(rc) and _is_tongits_rank_set_meld(pc):
                pass  # 同张数不应出现
            return primary, "same_count_keep_primary"

        if rn < pn:
            return primary, "refined_fewer_keep_primary"

        # 潜在 3→4 升级：须满足 Tongits 明杠结构 + 层数证据
        if pn == 3 and rn == 4:
            if visible_card_count == 3:
                logger.info(
                    "[board][refine] 拒绝 3→4：精检层数=3（合法明刻），保留主扫 %s",
                    pc,
                )
                return [_finalize_meld({"kind": "set", "cards": pc})], "layers_3_keep_set"

            if not _is_tongits_rank_quad_meld(rc):
                logger.info(
                    "[board][refine] 拒绝 3→4：精检非四门同点 %s",
                    rc,
                )
                return [_finalize_meld({"kind": "set", "cards": pc})], "invalid_quad_pattern"

            if not _primary_cards_subset_of(pc, rc):
                logger.info(
                    "[board][refine] 拒绝 3→4：主扫 %s 不是精检 %s 的子集",
                    pc,
                    rc,
                )
                return [_finalize_meld({"kind": "set", "cards": pc})], "primary_not_subset"

            if visible_card_count == 4:
                logger.info(
                    "[board][refine] 接受 3→4：层数=4 且四门同点 %s",
                    rc,
                )
                return refined, "layers_4_upgrade_quad"

            return primary, "needs_count_confirmation"

        return primary, "unhandled_single_meld_shape"

    p_n = _melds_total_cards(primary)
    r_n = _melds_total_cards(refined)
    if r_n > p_n:
        logger.info(
            "[board][refine] 拒绝多组盲升级 %d→%d（须单组且满足明杠校验）",
            p_n,
            r_n,
        )
    return primary, "multi_meld_keep_primary"


def _vlm_count_stack_layers(
    crop_path: Path,
    *,
    model: str,
    timeout: float,
) -> int | None:
    """仅数叠放层数，用于 3↔4 争议仲裁。"""
    try:
        raw = _vlm_chat(
            OPPONENT_COUNT_ONLY_PROMPT,
            str(crop_path),
            model=model,
            max_tokens=64,
            timeout_sec=min(timeout, 30.0),
        )
        return _parse_count_only_json(raw)
    except Exception as e:
        logger.warning("[board][refine] 层数仲裁失败: %s", e)
        return None


def _refine_opponent_side(
    image_path: str | Path,
    side: str,
    *,
    primary_melds: list[dict[str, Any]],
    model: str,
    timeout: float,
) -> list[dict[str, Any]]:
    side_label = "左侧对手（屏幕左上方）" if side == "left" else "右侧对手（屏幕右上方）"
    crop_path: Path | None = None
    try:
        crop_path = _crop_opponent_zone(image_path, side)
        prompt = OPPONENT_REFINE_PROMPT.format(side_label=side_label)
        raw = _vlm_chat(
            prompt,
            str(crop_path),
            model=model,
            max_tokens=512,
            timeout_sec=timeout,
        )
        refined, visible = _parse_opponent_refine_json(raw)
        merged, reason = _merge_opponent_melds_conservative(
            primary_melds,
            refined,
            visible_card_count=visible,
        )
        if reason == "needs_count_confirmation" and crop_path:
            layer_count = _vlm_count_stack_layers(
                crop_path, model=model, timeout=timeout
            )
            logger.info("[board][refine] 层数仲裁结果 visible_card_count=%s", layer_count)
            merged, reason = _merge_opponent_melds_conservative(
                primary_melds,
                refined,
                visible_card_count=layer_count,
            )
        if reason.startswith("layers_4_upgrade"):
            logger.info("[board][refine] %s 最终采用明杠 %d 张", side, _melds_total_cards(merged))
        elif reason in ("layers_3_keep_set", "same_count_keep_primary", "primary_not_subset"):
            logger.info(
                "[board][refine] %s 保留明刻/主扫 %d 张（%s）",
                side,
                _melds_total_cards(merged),
                reason,
            )
        return merged
    except Exception as e:
        logger.warning("[board][refine] %s 对手精检失败，保留主扫: %s", side, e)
        return primary_melds
    finally:
        if crop_path and crop_path.is_file():
            try:
                crop_path.unlink()
            except OSError:
                pass


def refine_board_opponent_melds(
    state: BoardState,
    image_path: str | Path,
    *,
    model: str | None = None,
    refine_opponent: bool = True,
) -> BoardState:
    """对手明牌裁切二次 VLM，缓解叠放漏数。"""
    if not refine_opponent or not is_rich_board_state(state):
        return state

    mode = _refine_opponent_mode()
    if mode in ("0", "off", "false", "no"):
        return state

    model = model or default_vlm_model()
    timeout = float(os.environ.get("TONGITS_VLM_TIMEOUT") or "60")
    out = dict(state)

    for side, key in (("left", "left_opponent"), ("right", "right_opponent")):
        side_obj = dict(out.get(key) or {})
        primary = side_obj.get("melds") or []
        if mode == "smart" and not _opponent_meld_suspect_undercount(primary):
            continue
        refined = _refine_opponent_side(
            image_path,
            side,
            primary_melds=primary,
            model=model,
            timeout=timeout,
        )
        side_obj["melds"] = refined
        out[key] = side_obj

    return out


def capture_screen_with_countdown(
    *,
    countdown_sec: int = 3,
    save_path: Path | None = None,
) -> Path:
    """
    倒计时后全屏截图，返回图片路径（临时文件或 save_path）。
    """
    try:
        import pyautogui
    except ImportError as e:
        raise RuntimeError("请安装 pyautogui: pip install pyautogui") from e

    if countdown_sec > 0:
        logger.info("请在 %d 秒内切换到游戏画面…", countdown_sec)
        for remain in range(countdown_sec, 0, -1):
            print(f"  {remain} …", flush=True)
            time.sleep(1.0)

    logger.info("[capture] 正在截取全屏 …")
    img = pyautogui.screenshot()
    if save_path:
        out = Path(save_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out))
        logger.info("[capture] 已保存 → %s", out.resolve())
        return out.resolve()

    out = board_screenshot_save_path()
    img.save(str(out), format="JPEG", quality=92)
    logger.info("[capture] 已保存 → %s", out.resolve())
    return out.resolve()


def capture_board_screenshot(*, save_path: Path | str | None = None) -> Path:
    """立即全屏截图（无倒计时），供回合触发等自动化场景。"""
    out = Path(save_path) if save_path else board_screenshot_save_path()
    return capture_screen_with_countdown(countdown_sec=0, save_path=out)


def vlm_config_summary() -> str:
    """当前 VLM 后端与牌局分析相关配置（供挂机循环打印）。"""
    timeout = float(os.environ.get("TONGITS_VLM_TIMEOUT") or "60")
    return (
        f"provider={vlm_provider()} model={default_vlm_model()} "
        f"timeout={timeout:.0f}s "
        f"refine_opponent={_refine_opponent_mode()} "
        f"refine_my_melds={_my_melds_refine_mode()}"
    )


def _append_step(step_log: list[str] | None, msg: str) -> None:
    if step_log is not None:
        step_log.append(msg)
    else:
        logger.info(msg)


TONGITS_UI_PHASE_PROMPT = """你是 Tongits（菲律宾拉米）UI 阶段识别 AI。根据截图判断**当前我方回合**处于哪一步操作。

【Tongits 回合顺序】
1. draw：须先从中央**摸牌堆**摸一张，或**吃**弃牌堆顶牌（Chow/Special）
2. meld：摸牌后可选 Group 组牌、Drop 亮牌
3. dump：须选一张手牌点 Dump 结束回合

【turn_phase 取值】
- draw：还没摸牌/吃牌（常见：中央摸牌堆可点、手牌张数偏少）
- meld：已摸牌，可组牌/亮牌，尚未必须 Dump
- dump：必须选一张牌 Dump 结束回合
- fight_offer：回合开始可发起 Fight（按钮 Fight 高亮）
- idle：非我方操作阶段

只输出 JSON（不要 markdown）：
{"turn_phase":"draw","can_chow":false,"can_group":false,"can_drop":false,"can_fight":false}
can_chow：弃牌顶牌能否吃（Special/Chow 可用）。"""


HAND_VLM_PROMPT = """你是 Tongits 手牌识别 AI。截图是屏幕底部「我的手牌」区域，牌从左到右一字排开。

【编码】花色字母+点数：H红桃、S黑桃、C梅花、D方块；点数 A/2-10/J/Q/K。例：H9、SA、D10。

【输出】只输出一个 JSON 对象（不要 markdown、不要解释）：
{
  "cards_left_to_right": ["S3","S4","S5","H9","DK"]
}

cards_left_to_right：每张手牌按**屏幕从左到右**顺序列出（含 Special 成组内的牌，仍按视觉左→右）。
若某张无法辨认，用 "?"。严禁漏牌、严禁串入对手区或明牌区。"""


def _crop_hand_zone_file(image_path: str | Path) -> tuple[Path, tuple[int, int, int, int], int, int]:
    """裁切手牌区并落盘，返回 (裁切图路径, roi_xyxy, 屏宽, 屏高)。"""
    import cv2

    from fast_card_recognizer import crop_hand_zone

    path = Path(image_path)
    screen = cv2.imread(str(path))
    if screen is None:
        raise FileNotFoundError(f"无法读取截图: {path}")
    sh, sw = screen.shape[:2]
    _zone, roi = crop_hand_zone(screen, screenshot_path=str(path))
    x1, y1, x2, y2 = roi
    crop = screen[y1:y2, x1:x2]
    max_w = int(os.environ.get("TONGITS_HAND_CROP_MAX_WIDTH") or "0")
    if max_w > 0 and crop.shape[1] > max_w:
        scale = max_w / crop.shape[1]
        crop = cv2.resize(
            crop,
            (max_w, max(1, int(crop.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    out = path.parent / f"{path.stem}_hand_crop.jpg"
    quality = int(os.environ.get("TONGITS_HAND_CROP_JPEG_QUALITY") or "82")
    cv2.imwrite(str(out), crop, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return out, roi, sw, sh


def _parse_hand_vlm_labels(text: str) -> list[str]:
    """解析手牌 VLM JSON → 从左到右的标准牌面标签列表。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("手牌 VLM 返回为空")

    obj: dict[str, Any] | None = None
    for candidate in (raw, strip_markdown_json(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                obj = parsed
                break
        except json.JSONDecodeError:
            continue
    if obj is None:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                obj = parsed
    if obj is None:
        raise ValueError(f"手牌 VLM 须为 JSON 对象: {raw[:300]}")

    seq: list[Any] = []
    if isinstance(obj.get("cards_left_to_right"), list):
        seq = obj["cards_left_to_right"]
    elif isinstance(obj.get("my_hand"), dict):
        hand = obj["my_hand"]
        for g in hand.get("special_groups") or []:
            if isinstance(g, list):
                seq.extend(g)
        seq.extend(hand.get("loose") or [])
    else:
        for g in obj.get("special_groups") or []:
            if isinstance(g, list):
                seq.extend(g)
        seq.extend(obj.get("loose") or [])

    labels: list[str] = []
    for item in seq:
        tok = _normalize_card_token(item)
        if tok and tok != "?":
            labels.append(tok)
    if not labels:
        raise ValueError(f"手牌 VLM 未解析到有效牌面: {(raw or '')[:300]}")
    return labels


def hand_labels_to_clickable_cards(
    labels: list[str],
    roi: tuple[int, int, int, int],
    *,
    id_base: int = 9001,
) -> list[dict[str, Any]]:
    """按手牌 ROI 均匀分布点击坐标（左→右与 VLM 顺序对齐）。"""
    x1, y1, x2, y2 = roi
    n = len(labels)
    cy = (y1 + y2) // 2
    span = max(1, x2 - x1)
    rows: list[dict[str, Any]] = []
    for i, label in enumerate(labels):
        parsed = parse_chinese_or_code_card(label)
        if not parsed:
            continue
        suit, rank, stem = parsed
        cx = x1 + int((i + 0.5) * span / n)
        rows.append(
            {
                "id": id_base + i,
                "suit": suit,
                "rank": rank,
                "label": stem,
                "label_cn": card_label_to_chinese(stem),
                "center_x": cx,
                "center_y": cy,
                "hand_index": i,
            }
        )
    return rows


def _parse_phase_vlm_obj(text: str) -> dict[str, Any]:
    """解析 UI 阶段 VLM JSON 对象。"""
    raw = (text or "").strip()
    obj: dict[str, Any] | None = None
    for candidate in (raw, strip_markdown_json(text)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                obj = parsed
                break
        except json.JSONDecodeError:
            continue
    if obj is None:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                obj = parsed
    if obj is None:
        raise ValueError(f"阶段 VLM 须为 JSON 对象: {raw[:300]}")
    return obj


def analyze_turn_phase_vlm(
    image_path: str | Path,
    *,
    model: str | None = None,
    max_retries: int = 2,
    step_log: list[str] | None = None,
) -> dict[str, Any]:
    """全屏截图 VLM 识别当前 Tongits UI 阶段（draw/meld/dump）。"""
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"截图不存在: {path}")

    model = model or default_vlm_model()
    timeout = float(os.environ.get("TONGITS_VLM_TIMEOUT") or "60")
    _append_step(
        step_log,
        f"阶段 VLM：全屏 UI（provider={vlm_provider()} model={model}）",
    )

    last_err = ""
    raw = ""
    for attempt in range(1, max_retries + 1):
        try:
            raw = _vlm_chat(
                TONGITS_UI_PHASE_PROMPT,
                str(path),
                model=model,
                max_tokens=256,
                timeout_sec=timeout,
            )
            obj = _parse_phase_vlm_obj(raw)
            phase = str(obj.get("turn_phase") or "draw").strip().lower()
            out = {
                "turn_phase": phase,
                "can_chow": bool(obj.get("can_chow")),
                "can_group": bool(obj.get("can_group")),
                "can_drop": bool(obj.get("can_drop")),
                "can_fight": bool(obj.get("can_fight")),
            }
            _append_step(step_log, f"阶段 VLM 结果: {out}")
            return out
        except (ValueError, json.JSONDecodeError) as e:
            last_err = str(e)
            logger.warning("[phase_vlm] 解析失败 %d/%d: %s", attempt, max_retries, e)
            time.sleep(0.8 * attempt)
        except Exception as e:
            last_err = repr(e)
            logger.warning("[phase_vlm] 请求失败 %d/%d: %s", attempt, max_retries, e)
            time.sleep(1.0 * attempt)

    raise RuntimeError(
        f"阶段 VLM 识别失败: {last_err}\n原始回复片段: {(raw or '')[:400]}"
    )


def analyze_hand_vlm(
    image_path: str | Path,
    *,
    model: str | None = None,
    max_retries: int = 2,
    step_log: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    裁切手牌区 + 单次 VLM 识别，返回带 center_x/y 的 hand_cards（供点击出牌）。
    """
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"截图不存在: {path}")

    model = model or default_vlm_model()
    timeout = float(os.environ.get("TONGITS_VLM_TIMEOUT") or "60")
    crop_path, roi, _sw, _sh = _crop_hand_zone_file(path)

    _append_step(
        step_log,
        f"手牌 VLM：裁切 ROI={roi} → {crop_path.name}（provider={vlm_provider()} model={model}）",
    )

    last_err = ""
    raw = ""
    for attempt in range(1, max_retries + 1):
        try:
            raw = _vlm_chat(
                HAND_VLM_PROMPT,
                str(crop_path),
                model=model,
                max_tokens=512,
                timeout_sec=timeout,
            )
            labels = _parse_hand_vlm_labels(raw)
            hand = hand_labels_to_clickable_cards(labels, roi)
            if not hand:
                raise ValueError("手牌列表为空")
            _append_step(
                step_log,
                f"手牌 VLM 识别 {len(hand)} 张: {', '.join(c['label'] for c in hand)}",
            )
            return hand
        except (ValueError, json.JSONDecodeError) as e:
            last_err = str(e)
            logger.warning("[hand_vlm] 解析失败 %d/%d: %s", attempt, max_retries, e)
            time.sleep(0.8 * attempt)
        except Exception as e:
            last_err = repr(e)
            logger.warning("[hand_vlm] 请求失败 %d/%d: %s", attempt, max_retries, e)
            time.sleep(1.0 * attempt)

    raise RuntimeError(
        f"手牌 VLM 识别失败: {last_err}\n原始回复片段: {(raw or '')[:400]}"
    )


def format_my_hand_cn(state: BoardState | dict[str, list[str]]) -> str:
    """仅输出手牌区中文描述（Special 组 + 散牌）。"""
    if not is_rich_board_state(state):
        state = _legacy_flat_to_rich(state)  # type: ignore[arg-type]
    hand = state.get("my_hand") or {}
    parts: list[str] = []
    for i, g in enumerate(hand.get("special_groups") or [], 1):
        cards = _cn_card_list(_normalize_card_group(g))
        if cards:
            parts.append(f"Special组{i}：{'、'.join(cards)}")
    loose = _cn_card_list(_normalize_zone_cards(hand.get("loose")))
    if loose:
        parts.append(f"散牌：{'、'.join(loose)}")
    if parts:
        return "；".join(parts)
    labels = iter_hand_card_labels(state)
    if labels:
        return "、".join(card_label_to_chinese(c) for c in labels)
    return "（未能识别手牌）"


def analyze_board_image(
    image_path: str | Path,
    *,
    model: str | None = None,
    max_retries: int = 2,
    refine_opponent: bool = True,
    step_log: list[str] | None = None,
) -> BoardState:
    """调用 VLM 分析牌局截图；默认对左右对手明牌区裁切二次精检。"""
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"截图不存在: {path}")

    model = model or default_vlm_model()
    timeout = float(os.environ.get("TONGITS_VLM_TIMEOUT") or "60")
    last_err = ""
    raw = ""

    for attempt in range(1, max_retries + 1):
        try:
            _append_step(
                step_log,
                f"主扫：全图牌局 JSON（provider={vlm_provider()} model={model}）",
            )
            raw = _vlm_chat(
                BOARD_STATE_PROMPT,
                str(path),
                model=model,
                max_tokens=2048,
                timeout_sec=timeout,
            )
            state = parse_board_state_json(raw)
            _append_step(
                step_log,
                f"精检：左右对手明牌裁切 VLM（mode={_refine_opponent_mode()}）",
            )
            state = refine_board_opponent_melds(
                state,
                path,
                model=model,
                refine_opponent=refine_opponent,
            )
            my_mode = _my_melds_refine_mode()
            will_refine_my = _should_refine_my_melds(state)
            _append_step(
                step_log,
                f"精检：我方明牌 my_melds（mode={my_mode}，"
                f"{'执行' if will_refine_my else '跳过'}）",
            )
            state = refine_my_melds_if_missing(state, path, model=model)
            _append_step(step_log, "校验：一副牌约束冲突检测与修正")
            return resolve_deck_constraint_violations(
                state, path, model=model
            )
        except (ValueError, json.JSONDecodeError) as e:
            last_err = str(e)
            logger.warning(
                "[board] JSON 解析失败 %d/%d: %s",
                attempt,
                max_retries,
                e,
            )
            time.sleep(0.8 * attempt)
        except Exception as e:
            last_err = repr(e)
            logger.warning(
                "[board] VLM 请求失败 %d/%d: %s",
                attempt,
                max_retries,
                e,
            )
            time.sleep(1.0 * attempt)

    raise RuntimeError(
        f"牌局状态提取失败: {last_err}\n原始回复片段: {(raw or '')[:500]}"
    )


def card_label_to_chinese(label: str) -> str:
    """
    标准码 → 中文牌面，如 H9 → 红桃9，SA → 黑桃A，D10 → 方块10。
    """
    code = (label or "").strip().upper()
    m = _CARD_LABEL_RE.match(code)
    if not m:
        m_alt = _CARD_LABEL_ALT_RE.match(code)
        if m_alt:
            suit, rank = m_alt.group(2).upper(), m_alt.group(1).upper()
        else:
            return label
    else:
        suit, rank = m.group(1).upper(), m.group(2).upper()
    suit_cn = SUIT_TO_CN.get(suit, suit)
    return f"{suit_cn}{rank}"


def _cn_card_list(cards: list[str]) -> list[str]:
    return [card_label_to_chinese(c) for c in cards]


def _cn_meld_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in groups or []:
        cards = _normalize_card_group(g)
        if not cards:
            continue
        out.append(
            {
                "kind": g.get("kind") or _normalize_meld_kind(None, card_count=len(cards)),
                "cards": _cn_card_list(cards),
            }
        )
    return out


def board_state_to_chinese(state: BoardState | dict[str, list[str]]) -> BoardState:
    """结构化快照 → 中文牌面（保留分组与中央牌堆信息）。"""
    if not is_rich_board_state(state):
        return {
            key: _cn_card_list(_normalize_zone_cards(state.get(key)))
            for key in ZONE_KEYS
        }

    hand = state.get("my_hand") or {}
    center = state.get("center") or {}
    left = state.get("left_opponent") or {}
    right = state.get("right_opponent") or {}
    discard = center.get("discard_top")

    return {
        "my_hand": {
            "special_groups": [
                _cn_card_list(_normalize_card_group(g))
                for g in hand.get("special_groups") or []
            ],
            "loose": _cn_card_list(_normalize_zone_cards(hand.get("loose"))),
        },
        "my_melds": _cn_meld_groups(state.get("my_melds") or []),
        "left_opponent": {
            "player_name": str(left.get("player_name") or "").strip(),
            "melds": _cn_meld_groups(left.get("melds") or []),
        },
        "right_opponent": {
            "melds": _cn_meld_groups(right.get("melds") or []),
        },
        "center": {
            "discard_top": card_label_to_chinese(discard) if discard else None,
            "draw_pile_remaining": center.get("draw_pile_remaining"),
        },
    }


def parse_chinese_or_code_card(label: str) -> tuple[str, str, str] | None:
    """
    解析中文或字母牌面 → (suit, rank, stem)。
    例：红桃9 → (H,9,H9)；H9 → (H,9,H9)。
    """
    text = (label or "").strip()
    if not text:
        return None
    for cn in sorted(CN_SUIT_TO_CODE.keys(), key=len, reverse=True):
        if text.startswith(cn):
            rank = text[len(cn) :].strip().upper()
            code = CN_SUIT_TO_CODE[cn]
            stem = f"{code}{rank}"
            if _CARD_LABEL_RE.match(stem):
                return code, rank, stem
            return None
    code = _normalize_card_token(text)
    if not code:
        return None
    m = _CARD_LABEL_RE.match(code)
    if not m:
        return None
    return m.group(1).upper(), m.group(2).upper(), code


def hand_cards_for_engine(
    state: BoardState | dict[str, list[str]],
    *,
    zone: str = ZONE_PLAYER_HAND,
    id_base: int = 9001,
) -> list[dict[str, Any]]:
    """从快照手牌区生成规则引擎用 hand_cards（suit/rank 仍为字母码）。"""
    if is_rich_board_state(state):
        labels = iter_hand_card_labels(state)
    else:
        labels = _normalize_zone_cards(state.get(zone))
    rows: list[dict[str, Any]] = []
    for i, label in enumerate(labels):
        parsed = parse_chinese_or_code_card(label)
        if not parsed:
            continue
        suit, rank, stem = parsed
        rows.append(
            {
                "id": id_base + i,
                "suit": suit,
                "rank": rank,
                "label_cn": card_label_to_chinese(stem),
            }
        )
    return rows


def board_snapshot_to_cv_state_dict(
    state: BoardState | dict[str, list[str]],
    *,
    phase_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    全景快照 → cv_state_dict（hand_cards 供引擎；table_snapshot 为中文结构化战报）。
    """
    from vision_proxy_qwen import cards_to_cv_state_dict

    hand = hand_cards_for_engine(state)
    cv = cards_to_cv_state_dict(hand, phase_overlay=phase_overlay)
    cv["table_snapshot"] = board_state_to_chinese(state)
    cv["table_snapshot_text"] = format_board_report_cn(state, use_chinese=True)
    return cv


def _display_card(label: str) -> str:
    code = _normalize_card_token(label)
    return card_label_to_chinese(code) if code else str(label)


def _join_cards_cn(cards: list[str]) -> str:
    parts = [_display_card(c) for c in cards if c]
    return "、".join(parts) if parts else "（无）"


def _meld_kind_title(kind: str) -> str:
    return MELD_KIND_LABELS.get(str(kind or "").lower(), MELD_KIND_LABELS["unknown"])


def format_board_report_cn(
    state: BoardState | dict[str, list[str]],
    *,
    use_chinese: bool = True,
) -> str:
    """
    生成与人工检阅一致的中文战报（分组、明杠/明刻、中央双堆）。
    """
    if not is_rich_board_state(state):
        state = _legacy_flat_to_rich(state)  # type: ignore[arg-type]
    if use_chinese:
        view = board_state_to_chinese(state)
    else:
        view = state

    lines: list[str] = []

    hand = view.get("my_hand") or {}
    lines.append("自己的手牌（屏幕下方底部）：")
    lines.append("")
    special_groups = hand.get("special_groups") or []
    if special_groups:
        for g in special_groups:
            lines.append(f"成组的牌（标记为 Special）： {_join_cards_cn(g)}")
            lines.append("")
    loose = hand.get("loose") or []
    lines.append(f"散牌： {_join_cards_cn(loose)}")
    lines.append("")

    my_melds = view.get("my_melds") or []
    lines.append("自己展示的牌（屏幕左下，操作按钮上方）：")
    lines.append("")
    if my_melds:
        for m in my_melds:
            title = _meld_kind_title(str(m.get("kind") or ""))
            lines.append(f"{title}： {_join_cards_cn(m.get('cards') or [])}")
            lines.append("")
    else:
        lines.append("（暂无已打出的明牌）")
        lines.append("")

    left = view.get("left_opponent") or {}
    pname = str(left.get("player_name") or "").strip()
    left_title = "左侧玩家展示的牌（屏幕左上方"
    if pname:
        left_title += f"，玩家 \"{pname}\""
    left_title += "）："
    lines.append(left_title)
    lines.append("")
    if left.get("melds"):
        for m in left["melds"]:
            title = _meld_kind_title(str(m.get("kind") or ""))
            lines.append(f"{title}： {_join_cards_cn(m.get('cards') or [])}")
            lines.append("")
    else:
        lines.append("（暂无可见明牌）")
        lines.append("")

    right = view.get("right_opponent") or {}
    lines.append("右侧玩家展示的牌（屏幕右上方）：")
    lines.append("")
    if right.get("melds"):
        for m in right["melds"]:
            title = _meld_kind_title(str(m.get("kind") or ""))
            lines.append(f"{title}： {_join_cards_cn(m.get('cards') or [])}")
            lines.append("")
    else:
        lines.append("（暂无可见明牌）")
        lines.append("")

    center = view.get("center") or {}
    lines.append("中间的牌堆：")
    lines.append("")
    discard = center.get("discard_top")
    if discard:
        disc = _display_card(discard) if not use_chinese else str(discard)
        lines.append(f"弃牌堆（右侧明牌）： {disc}")
    else:
        lines.append("弃牌堆（右侧明牌）： （未能识别顶牌）")
    lines.append("")

    draw_n = center.get("draw_pile_remaining")
    if draw_n is not None:
        lines.append(
            f"摸牌堆（左侧暗牌）： 牌背朝上，上面显示数字 {draw_n}（通常代表牌堆中还剩余{draw_n}张牌）。"
        )
    else:
        lines.append(
            "摸牌堆（左侧暗牌）： 牌背朝上（未能识别剩余张数，请查看截图左侧数字）。"
        )

    return "\n".join(lines).rstrip() + "\n"


def resolve_screenshot_for_board(
    image_path: str | Path | None,
    *,
    countdown_sec: int = 3,
    save_dir: Path | None = None,
) -> Path:
    """已有图则直接用，否则倒计时全屏截图。"""
    if image_path:
        p = Path(image_path)
        if not p.is_file() and "annotated" in p.stem.lower():
            for cand in (
                p.parent / p.name.replace("annotated", "raw").replace("_annotated", "_raw"),
                p.parent / "screen_raw.png",
            ):
                if cand.is_file():
                    return cand
        if p.is_file():
            return p.resolve()
        raise FileNotFoundError(f"截图不存在: {image_path}")
    out = board_screenshot_save_path(save_dir=save_dir) if save_dir else None
    return capture_screen_with_countdown(countdown_sec=countdown_sec, save_path=out)


def print_board_state(
    state: BoardState | dict[str, list[str]],
    *,
    use_chinese: bool = True,
) -> None:
    """打印中文分组战报（与人工检阅格式一致）。"""
    report = format_board_report_cn(state, use_chinese=use_chinese)
    print("\n" + report)

    legacy = board_state_to_legacy_zones(state) if is_rich_board_state(state) else state
    logger.info(
        "[board] 汇总 | 左=%d 右=%d 中央=%d 我方明牌=%d 手牌=%d 摸牌堆剩余=%s",
        len(legacy.get("left_opponent_melds") or []),
        len(legacy.get("right_opponent_melds") or []),
        len(legacy.get("center_discard") or []),
        len(legacy.get("my_melds") or []),
        len(legacy.get("my_hand") or []),
        (state.get("center") or {}).get("draw_pile_remaining")
        if is_rich_board_state(state)
        else "?",
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    ap = argparse.ArgumentParser(
        description="Tongits 端到端牌局状态提取（截屏 + Qwen-VL-Max）"
    )
    ap.add_argument(
        "--image",
        help="跳过截屏，直接分析已有图片",
    )
    ap.add_argument(
        "--countdown",
        type=int,
        default=3,
        help="截屏前倒计时秒数（默认 3）",
    )
    ap.add_argument(
        "--save",
        help=(
            "截屏保存路径（默认 scripts/omnioutput/{时间戳}_board_raw.jpg）"
        ),
    )
    ap.add_argument(
        "--save-dir",
        default=None,
        help="截屏目录（默认 scripts/omnioutput），与 --save 二选一",
    )
    ap.add_argument(
        "--model",
        default=None,
        help="VLM 模型，默认 qwen-vl-max",
    )
    ap.add_argument(
        "--json-out",
        help="将解析结果写入 JSON 文件",
    )
    ap.add_argument(
        "--no-print",
        action="store_true",
        help="不打印格式化战报（仅写日志/文件）",
    )
    ap.add_argument(
        "--english",
        action="store_true",
        help="战报牌面保留字母码 H9/SA（默认中文：红桃9/黑桃A）",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="额外在 stdout 打印结构化 JSON（默认仅打印中文战报）",
    )
    ap.add_argument(
        "--no-refine-opponent",
        action="store_true",
        help="跳过左右对手明牌区裁切二次 VLM（默认开启，防叠牌漏数）",
    )
    args = ap.parse_args()

    try:
        if args.image:
            img_path = Path(args.image)
        else:
            save_dir = Path(args.save_dir) if args.save_dir else OMNI_OUTPUT_DIR
            save_path = board_screenshot_save_path(
                save_dir=save_dir,
                explicit=args.save,
            )
            img_path = capture_screen_with_countdown(
                countdown_sec=max(0, args.countdown),
                save_path=save_path,
            )
            print(f"\n>> 截图已保存: {img_path.resolve()}\n", flush=True)

        state = analyze_board_image(
            img_path,
            model=args.model,
            refine_opponent=not args.no_refine_opponent,
        )
        use_cn = not args.english
        output_state = board_state_to_chinese(state) if use_cn else state
        report_text = format_board_report_cn(state, use_chinese=use_cn)

        if not args.no_print:
            print_board_state(state, use_chinese=use_cn)

        if args.json_out:
            payload = {
                "report": report_text,
                "board": output_state,
            }
            Path(args.json_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("[board] JSON 已写入 → %s", args.json_out)

        if args.json:
            print(
                json.dumps(
                    {"report": report_text, "board": output_state},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0

    except Exception as e:
        logger.exception("[board] 失败: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
