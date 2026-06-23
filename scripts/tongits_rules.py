#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 结构化规则（菲律宾拉米）— 决策引擎 SSOT，无 LLM。

要点摘要
--------
- 3 人，52 张；庄家 13 张、闲家 12 张，牌堆 15 张。
- 胜利：Tongits（手牌清空）/ Fight 点数最低 / 牌堆抓完点数最低。
- 散牌点数：A=1，2–10 牌面，J/Q/K=10。
- 合法牌组：刻子 3–4 张同点；同花顺 ≥3 张（A 仅接 2，不接 K）。
- 回合：Draw（摸暗牌堆 / 吃明牌弃牌顶且须立刻成组）→ 可选 Drop/Sapaw → Dump 一张。
- Fight：本局须至少 Drop 过一组，且自上次 Drop 后未被 Sapaw。
- Burned：整局未 Drop 任何组则自动判负。

本模块提供：点数、刻子/顺子判定、能否吃牌、选 Dump 牌等纯函数。
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Iterable

from vision_proxy_qwen import _RANK_VALUE, _SCATTER_POINTS, parse_card_label

RANK_ORDER = list(_RANK_VALUE.keys())  # A,2,...,K
MIN_MELD_LEN = 3
MAX_SET_SIZE = 4


@dataclass(frozen=True)
class HandCard:
    label: str
    suit: str
    rank: str
    center_x: int = 0
    center_y: int = 0

    @property
    def scatter(self) -> int:
        return _SCATTER_POINTS.get(self.rank.upper(), 10)

    @property
    def rank_value(self) -> int:
        return _RANK_VALUE.get(self.rank.upper(), 99)


def label_to_hand_card(
    label: str,
    *,
    center_x: int = 0,
    center_y: int = 0,
) -> HandCard | None:
    parsed = parse_card_label(label)
    if not parsed:
        return None
    canon, suit, rank = parsed
    return HandCard(
        label=canon,
        suit=suit,
        rank=rank,
        center_x=center_x,
        center_y=center_y,
    )


def scatter_points(cards: Iterable[HandCard]) -> int:
    return sum(c.scatter for c in cards)


def _rank_counts(cards: Iterable[HandCard]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in cards:
        out[c.rank] = out.get(c.rank, 0) + 1
    return out


def find_set_melds(cards: list[HandCard], *, min_size: int = MIN_MELD_LEN) -> list[list[int]]:
    """刻子：返回手牌下标列表的列表（同点数，且花色互异，数量 ≥ min_size）。"""
    by_rank: dict[str, list[int]] = {}
    for i, c in enumerate(cards):
        by_rank.setdefault(c.rank, []).append(i)
    melds: list[list[int]] = []
    for idxs in by_rank.values():
        # 同点数组内去重花色：防止识别抖动把同一张牌复制成同花色同点数，
        # 误判出不存在的刻子（例如 C3,C3,S3）。
        uniq_by_suit: dict[str, int] = {}
        for i in idxs:
            suit = cards[i].suit
            if suit not in uniq_by_suit:
                uniq_by_suit[suit] = i
        uniq_idxs = sorted(uniq_by_suit.values())
        if len(uniq_idxs) >= min_size:
            melds.append(uniq_idxs[:MAX_SET_SIZE])
    return melds


def _consecutive_runs(values: list[int], *, min_len: int) -> list[list[int]]:
    if not values:
        return []
    uniq = sorted(set(values))
    runs: list[list[int]] = []
    run = [uniq[0]]
    for v in uniq[1:]:
        if v == run[-1] + 1:
            run.append(v)
        else:
            if len(run) >= min_len:
                runs.append(list(run))
            run = [v]
    if len(run) >= min_len:
        runs.append(list(run))
    return runs


def find_straight_melds(
    cards: list[HandCard],
    *,
    min_len: int = MIN_MELD_LEN,
) -> list[list[int]]:
    """同花顺：返回手牌下标列表（每段顺子）。"""
    by_suit: dict[str, list[tuple[int, int]]] = {}
    for i, c in enumerate(cards):
        by_suit.setdefault(c.suit, []).append((i, c.rank_value))

    melds: list[list[int]] = []
    for pairs in by_suit.values():
        rank_to_idx: dict[int, int] = {}
        for idx, rv in pairs:
            rank_to_idx[rv] = idx
        for run in _consecutive_runs(list(rank_to_idx.keys()), min_len=min_len):
            melds.append([rank_to_idx[rv] for rv in run])
    return melds


def indices_in_any_meld(cards: list[HandCard]) -> set[int]:
    used: set[int] = set()
    for meld in find_set_melds(cards) + find_straight_melds(cards):
        used.update(meld)
    return used


def loose_cards(cards: list[HandCard]) -> list[HandCard]:
    in_meld = indices_in_any_meld(cards)
    loose = [c for i, c in enumerate(cards) if i not in in_meld]
    return loose if loose else list(cards)


def loose_scatter_points(cards: list[HandCard]) -> int:
    """
    仅计算“未成组散牌”的点数。
    与 loose_cards 不同：当全部已成组时返回 0（而非全手牌点数）。
    """
    in_meld = indices_in_any_meld(cards)
    loose = [c for i, c in enumerate(cards) if i not in in_meld]
    return scatter_points(loose)


def pick_dump_card(cards: list[HandCard]) -> HandCard | None:
    """弃牌阶段：散牌点数最高者优先打出。"""
    pool = loose_cards(cards)
    if not pool:
        return None
    return max(pool, key=lambda c: (c.scatter, c.center_x))


def _discard_as_hand(discard_label: str) -> HandCard | None:
    return label_to_hand_card(discard_label)


def can_chow_with_discard(hand: list[HandCard], discard_label: str) -> bool:
    """
    能否吃弃牌顶：加入手牌后须能立刻组成刻子或同花顺（含该张）。
    """
    discard = _discard_as_hand(discard_label)
    if discard is None:
        return False

    # 刻子：手牌中同点数 ≥2
    same_rank = sum(1 for c in hand if c.rank == discard.rank)
    if same_rank >= 2:
        return True

    # 同花顺：同花色序列中含 discard.rank_value，且能形成 ≥3 连张
    suit_cards = [c for c in hand if c.suit == discard.suit]
    values = sorted({c.rank_value for c in suit_cards} | {discard.rank_value})
    for run in _consecutive_runs(values, min_len=MIN_MELD_LEN):
        if discard.rank_value in run:
            return True
    return False


def _chow_gain_threshold() -> int:
    try:
        return int(os.environ.get("TONGITS_CHOW_GAIN_THRESHOLD", "2"))
    except ValueError:
        return 2


def _hand_quality_score(cards: list[HandCard]) -> int:
    """
    简化手牌质量分：基于“可落地的非重叠牌组”评估，避免重叠牌组虚高增益。
    """
    plans = find_hand_melds_for_drop(cards)
    used: set[int] = set()
    meld_score = 0
    for idxs in plans:
        used.update(idxs)
        meld_score += sum(cards[i].scatter for i in idxs)

    loose = [c for i, c in enumerate(cards) if i not in used]
    loose_penalty = scatter_points(loose)
    # 覆盖张数与可落地牌组总分为正向，散牌点为负向。
    return len(used) * 6 + meld_score - loose_penalty


def _discard_immediate_meld_bonus(hand: list[HandCard], discard: HandCard) -> int:
    """
    吃牌即时收益（Tongits 语义）：
    - 弃牌顶必须参与新组
    - 若该组吸收了原本散牌，给予显著加分
    """
    before_used = indices_in_any_meld(hand)
    after_cards = list(hand) + [discard]
    d_idx = len(after_cards) - 1
    candidates = [
        idxs
        for idxs in (find_set_melds(after_cards) + find_straight_melds(after_cards))
        if d_idx in idxs
    ]
    if not candidates:
        return -999

    best = -999
    for idxs in candidates:
        # 只统计“被弃牌顶激活”的旧手牌增益
        old_newly_used = [i for i in idxs if i != d_idx and i not in before_used]
        newly_count = len(old_newly_used)
        scatter_saved = sum(after_cards[i].scatter for i in old_newly_used)
        bonus = newly_count * 5 + scatter_saved
        if len(idxs) >= 4:
            bonus += 2
        best = max(best, bonus)
    return best


def _discard_card_improves_hand(
    hand: list[HandCard],
    discard_label: str,
) -> tuple[bool, int]:
    """
    吃牌是否让当前手牌结构变好（用于 Tongits 吃/摸决策）。
    """
    discard = _discard_as_hand(discard_label)
    if discard is None:
        return False, -999
    before = _hand_quality_score(hand)
    after_cards = list(hand) + [discard]
    after = _hand_quality_score(after_cards)
    # 原始结构分 + 吃牌即时成组奖励（避免 A234 抢占导致 678 收益被低估）
    bonus = _discard_immediate_meld_bonus(hand, discard)
    gain = (after - before) + max(0, bonus)
    return gain >= _chow_gain_threshold(), gain


def decide_draw_action(
    hand: list[HandCard],
    discard_top: str | None,
    *,
    auto_chow: bool = True,
    ui_chow_available: bool = False,
) -> tuple[str, str]:
    """
    摸牌阶段决策：能立刻成组时优先吃顶牌，否则摸暗牌堆。

    Returns:
        ("deck"|"discard", reason)
    """
    top = (discard_top or "").strip()
    if auto_chow and top and can_chow_with_discard(hand, top):
        better, gain = _discard_card_improves_hand(hand, top)
        if not better:
            return "deck", f"顶牌 {top} 可成组但收益不足（gain={gain}），改摸暗牌堆"
        ui_note = "，UI 弃牌堆黄箭头" if ui_chow_available else ""
        return "discard", f"可吃顶牌 {top} 并立刻成组{ui_note}"
    if top:
        return "deck", f"顶牌 {top} 无法立刻成组，改摸暗牌堆"
    return "deck", "无弃牌顶牌，摸中央暗牌堆"


# ---------------------------------------------------------------------------
# 桌面牌组 / Sapaw / Drop 亮牌
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableMeld:
    """桌面上已亮出的一组牌（来自 VLM 标签分区解析）。"""

    zone: str
    kind: str  # set | quad | sequence
    cards: tuple[HandCard, ...]
    start_index: int = 0

    @property
    def rank_values(self) -> list[int]:
        return [c.rank_value for c in self.cards]

    @property
    def min_rank_value(self) -> int:
        return min(self.rank_values) if self.rank_values else 99

    @property
    def max_rank_value(self) -> int:
        return max(self.rank_values) if self.rank_values else 0


@dataclass(frozen=True)
class SapawMove:
    """贴牌：手牌一张 → 桌面某组牌。"""

    hand_index: int
    hand_card: HandCard
    meld: TableMeld
    attach: str  # set | low | high
    reason: str = ""


@dataclass(frozen=True)
class MeldPlan:
    """下一步亮牌/贴牌计划。"""

    action: str  # drop | sapaw | chow_drop
    score: int
    hand_indices: tuple[int, ...] = ()
    sapaw: SapawMove | None = None
    reason: str = ""


def _labels_to_hand_cards(labels: Iterable[str]) -> list[HandCard]:
    out: list[HandCard] = []
    for lb in labels:
        hc = label_to_hand_card(str(lb or "").strip())
        if hc:
            out.append(hc)
    return out


def parse_zone_labels_to_melds(zone: str, labels: Iterable[str]) -> list[TableMeld]:
    """
    将战区 VLM 扁平标签解析为刻子/同花顺（贪心：先刻子后顺子）。
    """
    indexed: list[tuple[int, HandCard]] = []
    for i, lb in enumerate(labels):
        hc = label_to_hand_card(str(lb or "").strip())
        if hc:
            indexed.append((i, hc))
    if not indexed:
        return []

    used: set[int] = set()
    melds: list[TableMeld] = []

    by_rank: dict[str, list[tuple[int, HandCard]]] = {}
    for idx, hc in indexed:
        by_rank.setdefault(hc.rank, []).append((idx, hc))

    for group in by_rank.values():
        if len(group) < MIN_MELD_LEN:
            continue
        group.sort(key=lambda x: x[0])
        take = group[:MAX_SET_SIZE]
        idxs = [g[0] for g in take]
        if any(i in used for i in idxs):
            continue
        used.update(idxs)
        kind = "quad" if len(idxs) == 4 else "set"
        melds.append(
            TableMeld(
                zone=zone,
                kind=kind,
                cards=tuple(g[1] for g in take),
                start_index=min(idxs),
            )
        )

    remaining = [(i, hc) for i, hc in indexed if i not in used]
    by_suit: dict[str, list[tuple[int, HandCard]]] = {}
    for idx, hc in remaining:
        by_suit.setdefault(hc.suit, []).append((idx, hc))

    for group in by_suit.values():
        group.sort(key=lambda x: x[1].rank_value)
        i = 0
        while i < len(group):
            run = [group[i]]
            j = i + 1
            while j < len(group) and group[j][1].rank_value == run[-1][1].rank_value + 1:
                run.append(group[j])
                j += 1
            if len(run) >= MIN_MELD_LEN:
                idxs = [r[0] for r in run]
                melds.append(
                    TableMeld(
                        zone=zone,
                        kind="sequence",
                        cards=tuple(r[1] for r in run),
                        start_index=idxs[0],
                    )
                )
                used.update(idxs)
            i = j if j > i + 1 else i + 1

    melds.sort(key=lambda m: m.start_index)
    return melds


def all_table_melds_from_zones(
    zone_labels: dict[str, list[str]],
) -> list[TableMeld]:
    """汇总 my_melds / 左右对手已亮牌组。"""
    out: list[TableMeld] = []
    for zone in ("my_melds", "opponent_left", "opponent_right"):
        labels = zone_labels.get(zone) or []
        out.extend(parse_zone_labels_to_melds(zone, labels))
    return out


def zone_labels_from_detections(by_zone: dict[str, list[Any]]) -> dict[str, list[str]]:
    """侦察 by_zone → 各战区标签列表。"""
    out: dict[str, list[str]] = {}
    for zone, dets in (by_zone or {}).items():
        labels: list[str] = []
        for d in dets or []:
            lb = str(getattr(d, "class_name", "") or "").strip()
            if lb:
                labels.append(lb)
        out[str(zone)] = labels
    return out


def find_longest_straight_melds(
    cards: list[HandCard],
    *,
    min_len: int = MIN_MELD_LEN,
) -> list[list[int]]:
    """同花顺：每花色只取最长一段（避免 456789 重复出 456/567）。"""
    by_suit: dict[str, list[tuple[int, int]]] = {}
    for i, c in enumerate(cards):
        by_suit.setdefault(c.suit, []).append((i, c.rank_value))

    melds: list[list[int]] = []
    for pairs in by_suit.values():
        pairs.sort(key=lambda x: x[1])
        if len(pairs) < min_len:
            continue
        run_start = 0
        best: list[int] = []
        for j in range(1, len(pairs) + 1):
            if j < len(pairs) and pairs[j][1] == pairs[j - 1][1] + 1:
                continue
            run = pairs[run_start:j]
            if len(run) >= min_len and len(run) > len(best):
                best = [i for i, _ in run]
            run_start = j
        if best:
            melds.append(best)
    return melds


def find_hand_melds_for_drop(cards: list[HandCard]) -> list[list[int]]:
    """
    手牌中可 Drop 亮出的牌组（非重叠，按散牌分降序）。
    """
    candidates = find_set_melds(cards) + find_longest_straight_melds(cards)
    candidates.sort(
        key=lambda idxs: (
            sum(cards[i].scatter for i in idxs),
            len(idxs),
        ),
        reverse=True,
    )
    used: set[int] = set()
    picked: list[list[int]] = []
    for idxs in candidates:
        if any(i in used for i in idxs):
            continue
        used.update(idxs)
        picked.append(sorted(idxs))
    return picked


def _card_matches_label(card: HandCard, label: str) -> bool:
    target = label_to_hand_card(label)
    if target is None:
        return False
    return card.label == target.label or (
        card.rank == target.rank and card.suit == target.suit
    )


def find_meld_in_hand_for_label(
    cards: list[HandCard],
    label: str,
) -> list[int] | None:
    """吃牌后：找包含该牌且可立刻亮出的整组下标（刻子或同花顺）。"""
    target = label_to_hand_card(label)
    if target is None:
        return None
    target_idx = next(
        (i for i, c in enumerate(cards) if _card_matches_label(c, label)),
        None,
    )
    if target_idx is None:
        return None
    for idxs in find_hand_melds_for_drop(cards):
        if target_idx in idxs:
            return idxs
    return None


def can_sapaw_card_to_meld(card: HandCard, meld: TableMeld) -> str | None:
    """
    能否 Sapaw 贴到桌面牌组。

    Returns:
        "set" | "low" | "high" 或 None
    """
    if meld.kind in ("set", "quad"):
        if len(meld.cards) >= MAX_SET_SIZE:
            return None
        if card.rank == meld.cards[0].rank:
            return "set"
        return None

    if meld.kind != "sequence" or not meld.cards:
        return None
    if card.suit != meld.cards[0].suit:
        return None
    lo, hi = meld.min_rank_value, meld.max_rank_value
    if card.rank_value == lo - 1:
        return "low"
    if card.rank_value == hi + 1:
        return "high"
    return None


def find_sapaw_moves(
    hand: list[HandCard],
    table_melds: list[TableMeld],
) -> list[SapawMove]:
    """所有合法 Sapaw（手牌下标 + 目标牌组）。"""
    moves: list[SapawMove] = []
    for i, card in enumerate(hand):
        for meld in table_melds:
            attach = can_sapaw_card_to_meld(card, meld)
            if not attach:
                continue
            zone_cn = {
                "my_melds": "我方明牌",
                "opponent_left": "左对手",
                "opponent_right": "右对手",
            }.get(meld.zone, meld.zone)
            meld_txt = ",".join(c.label for c in meld.cards)
            moves.append(
                SapawMove(
                    hand_index=i,
                    hand_card=card,
                    meld=meld,
                    attach=attach,
                    reason=f"{card.label} → {zone_cn}[{meld_txt}] ({attach})",
                )
            )
    return moves


def pick_next_meld_plan(
    hand: list[HandCard],
    table_melds: list[TableMeld],
    *,
    auto_drop: bool = True,
    auto_sapaw: bool = True,
) -> MeldPlan | None:
    """
    在 Drop 亮牌与 Sapaw 贴牌中选散牌分最高的一步。
    约束：必须至少保留 1 张手牌用于本回合 Dump。
    """
    best: MeldPlan | None = None
    min_cards_for_dump = 1

    if auto_drop:
        for idxs in find_hand_melds_for_drop(hand):
            # Tongits 常规回合：亮牌后仍需弃一张，禁止把手牌亮到 0 张。
            if len(hand) - len(idxs) < min_cards_for_dump:
                continue
            score = sum(hand[i].scatter for i in idxs)
            plan = MeldPlan(
                action="drop",
                score=score,
                hand_indices=tuple(idxs),
                reason=f"Drop 亮牌 {len(idxs)} 张（散牌分={score}）",
            )
            if best is None or score > best.score:
                best = plan

    if auto_sapaw:
        for move in find_sapaw_moves(hand, table_melds):
            # Sapaw 贴牌后同样要 Dump，至少留 1 张手牌。
            if len(hand) - 1 < min_cards_for_dump:
                continue
            score = move.hand_card.scatter
            if move.meld.zone.startswith("opponent"):
                score += 1
            plan = MeldPlan(
                action="sapaw",
                score=score,
                sapaw=move,
                reason=move.reason,
            )
            if best is None or score > best.score:
                best = plan

    return best


def meld_attach_slot_index(meld: TableMeld, attach: str, zone_card_count: int) -> int:
    """Sapaw 目标在战区扁平序列中的槽位（用于估算点击 x）。"""
    n = max(zone_card_count, len(meld.cards), 1)
    start = meld.start_index
    meld_len = len(meld.cards)
    if attach == "low":
        return max(0, start)
    if attach == "high":
        return min(n - 1, start + meld_len)
    return min(n - 1, start + meld_len // 2)
