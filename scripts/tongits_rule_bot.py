#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits（菲律宾拉米）规则挂机 Bot — 决策无 LLM。

架构：
  Observe  → OmniParser 标注图 + elements_dict + 按钮 OCR 映射
  Perceive → 默认自生长记忆库（OpenCV 命中 + VLM _miss 入库）
             可选 --opencv 纯模板 / --vlm 全屏 VLM
  Decide   → TongitsDecisionEngine 纯 if-else
  Act      → physical_click

用法（仓库根目录）::

  python scripts/tongits_rule_bot.py --scenario normal_turn
  python scripts/tongits_rule_bot.py --live
  python scripts/tongits_rule_bot.py --live --opencv
  python scripts/tongits_rule_bot.py --live --vlm
  python scripts/tongits_rule_bot.py --live --board
  python scripts/vlm_board_analyzer.py --image scripts/omnioutput/xxx_raw.png
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    for _p in (ROOT / ".env", ROOT / "core" / ".env", Path.home() / ".jachin" / ".env"):
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
except ImportError:
    pass

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from tongits_button_resolver import (
    LEGACY_REFERENCE_IDS,
    log_legacy_id_trap,
    resolve_action_buttons,
    resolve_action_element_id,
)
from fast_card_recognizer import (
    cards_to_engine_json,
    filter_hand_card_ids,
    get_card_matcher,
    recognize_cards as opencv_recognize_cards,
)
from self_learning_card_recognizer import (
    get_self_learning_recognizer,
    recognize_cards as memory_recognize_cards,
)
from vision_proxy_qwen import (
    analyze_buttons_with_qwen,
    analyze_cards_with_qwen,
    analyze_game_phase_with_qwen,
    cards_to_cv_state_dict,
    default_vlm_model,
)
from vlm_board_analyzer import (
    analyze_board_image,
    board_snapshot_to_cv_state_dict,
    board_state_to_chinese,
    hand_cards_for_engine,
    print_board_state,
    resolve_screenshot_for_board,
)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logger = logging.getLogger("tongits_bot")

# 语义动作名（真正点击的 element_id 每帧由 resolver / VLM 解析，见 LEGACY_REFERENCE_IDS）
ACTION_NAMES = ("drop", "fight", "group", "dump", "deck", "special")


class TurnPhase(str, Enum):
    """专有 CV 识别的回合阶段（示意，非完整游戏状态机）。"""

    FIGHT_OFFER = "fight_offer"  # 回合开始，可发起 Fight
    DRAW = "draw"  # 须摸牌或吃牌
    MELD = "meld"  # 已摸牌，可 Group / Drop
    DUMP = "dump"  # 须弃一张散牌结束回合
    IDLE = "idle"


@dataclass
class VisionState:
    """
    单帧融合感知：OmniParser 坐标表 + CV 回合语义。
    """

    turn_phase: TurnPhase
    elements_dict: dict[int, dict[str, int]]
    can_fight: bool = False
    should_fight: bool = False
    can_chow: bool = False
    can_group: bool = False
    can_drop: bool = False
    scatter_points: int = 99
    hand_cards: list[dict[str, Any]] | None = None
    action_id_map: dict[str, int] = field(default_factory=dict)
    elements: list[dict[str, Any]] = field(default_factory=list)
    screen_width: int = 1920
    screen_height: int = 1080
    annotated_image_path: str = ""
    note: str = ""


@dataclass
class ObserveBundle:
    annotated_path: str
    elements_dict: dict[int, dict[str, int]]
    elements: list[dict[str, Any]]
    action_id_map: dict[str, int]
    screen_width: int = 1920
    screen_height: int = 1080
    raw_screenshot_path: str = ""
    board_snapshot_cn: dict[str, list[str]] | None = None


def _resolve_raw_screenshot(annotated_path: str, work_dir: str = "") -> str:
    """认牌用无红框原图，避免标注干扰模板匹配。"""
    if work_dir:
        raw = Path(work_dir) / "screen_raw.png"
        if raw.is_file():
            return str(raw)
    ann = Path(annotated_path)
    if ann.is_file():
        for cand in (
            ann.parent / ann.name.replace("_annotated", "_raw").replace("annotated", "raw"),
            ann.parent / "screen_raw.png",
            ann.with_name("screen_raw.png"),
        ):
            if cand.is_file():
                return str(cand)
    return annotated_path


def cv_state_dict_to_vision_state(
    cv: dict[str, Any],
    elements_dict: dict[int, dict[str, int]],
    *,
    annotated_image_path: str = "",
    action_id_map: dict[str, int] | None = None,
    elements: list[dict[str, Any]] | None = None,
    screen_width: int = 1920,
    screen_height: int = 1080,
) -> VisionState:
    """将 VLM+本地推导的 cv_state_dict 转为规则引擎输入。"""
    phase_raw = str(cv.get("turn_phase") or "draw").lower()
    try:
        phase = TurnPhase(phase_raw)
    except ValueError:
        phase = TurnPhase.DRAW
    return VisionState(
        turn_phase=phase,
        elements_dict=elements_dict,
        can_fight=bool(cv.get("can_fight")),
        should_fight=bool(cv.get("should_fight")),
        can_chow=bool(cv.get("can_chow")),
        can_group=bool(cv.get("can_group")),
        can_drop=bool(cv.get("can_drop")),
        scatter_points=int(cv.get("scatter_points") or 99),
        hand_cards=list(cv.get("hand_cards") or []),
        action_id_map=dict(action_id_map or {}),
        elements=list(elements or []),
        screen_width=screen_width,
        screen_height=screen_height,
        annotated_image_path=annotated_image_path,
        note=f"cv_state: phase={phase.value} cards={len(cv.get('hand_cards') or [])}",
    )


@dataclass(frozen=True)
class Decision:
    """规则引擎输出：语义动作名（element_id 由 Observe 解析，禁止写死）。"""

    action: str
    reason: str


# ---------------------------------------------------------------------------
# Mock 视觉输入
# ---------------------------------------------------------------------------

def _mock_elements_full() -> list[dict[str, Any]]:
    """Mock 元素表（含 OCR 文案，供 resolver 测试）。"""
    specs = [
        ("drop", 27, 518, 726, "Drop"),
        ("fight", 28, 813, 722, "Fight"),
        ("group", 29, 1104, 724, "Group"),
        ("dump", 30, 1399, 724, "Dump"),
        ("deck", 20, 859, 412, "(15"),
        ("special", 24, 466, 570, "Special"),
    ]
    out: list[dict[str, Any]] = []
    for _name, eid, cx, cy, content in specs:
        out.append(
            {
                "id": eid,
                "center_x": cx,
                "center_y": cy,
                "content": content,
                "bbox_xyxy_pixels": [cx - 40, cy - 18, cx + 40, cy + 18],
            }
        )
    return out


def _mock_elements_dict() -> dict[int, dict[str, int]]:
    return {
        int(r["id"]): {"center_x": int(r["center_x"]), "center_y": int(r["center_y"])}
        for r in _mock_elements_full()
    }


def get_vision_state(scenario: str = "normal_turn") -> VisionState:
    """
    模拟一帧 Observe + CV 状态。

    scenario:
      - fight_win: 回合开始且散牌极少，应 Fight
      - chow_draw: 可吃牌，应 Special
      - normal_turn: 标准摸牌 → 组牌 → 亮牌 → 弃牌
      - dump_only: 已组完牌，只剩 Dump
    """
    elements_list = _mock_elements_full()
    elements = _mock_elements_dict()
    action_map = resolve_action_buttons(elements_list, screen_height=1080)
    if scenario == "fight_win":
        return VisionState(
            turn_phase=TurnPhase.FIGHT_OFFER,
            elements_dict=elements,
            action_id_map=action_map,
            elements=elements_list,
            can_fight=True,
            should_fight=True,
            scatter_points=3,
            note="CV: Can Fight, scatter=3",
        )
    if scenario == "chow_draw":
        return VisionState(
            turn_phase=TurnPhase.DRAW,
            elements_dict=elements,
            action_id_map=action_map,
            elements=elements_list,
            can_chow=True,
            note="CV: Turn to Draw, chow available",
        )
    if scenario == "dump_only":
        return VisionState(
            turn_phase=TurnPhase.DUMP,
            elements_dict=elements,
            action_id_map=action_map,
            elements=elements_list,
            note="CV: Turn to Dump",
        )
    return VisionState(
        turn_phase=TurnPhase.DRAW,
        elements_dict=elements,
        action_id_map=action_map,
        elements=elements_list,
        can_chow=False,
        can_group=True,
        can_drop=True,
        scatter_points=18,
        note="CV: Turn to Draw (deck)",
    )


def _carry_state(
    state: VisionState,
    *,
    turn_phase: TurnPhase,
    **kwargs: Any,
) -> VisionState:
    """推进 Mock 阶段时保留 action_id_map / 手牌等字段。"""
    base = {
        "turn_phase": turn_phase,
        "elements_dict": state.elements_dict,
        "action_id_map": state.action_id_map,
        "elements": state.elements,
        "screen_width": state.screen_width,
        "screen_height": state.screen_height,
        "hand_cards": state.hand_cards,
        "can_fight": state.can_fight,
        "should_fight": state.should_fight,
        "can_chow": state.can_chow,
        "can_group": state.can_group,
        "can_drop": state.can_drop,
        "scatter_points": state.scatter_points,
        "annotated_image_path": state.annotated_image_path,
    }
    base.update(kwargs)
    return VisionState(**base)


def advance_mock_state(state: VisionState, decision: Decision) -> VisionState:
    """模拟点击后游戏阶段推进（仅用于 dry-run 演示）。"""
    action = decision.action

    if action == "fight":
        return _carry_state(state, turn_phase=TurnPhase.IDLE, note="Mock: Fight resolved")
    if action == "special":
        return _carry_state(
            state,
            turn_phase=TurnPhase.MELD,
            note="Mock: Chow done → meld phase",
        )
    if action == "deck":
        return _carry_state(
            state,
            turn_phase=TurnPhase.MELD,
            can_group=True,
            can_drop=True,
            note="Mock: Drew from deck → meld phase",
        )
    if action == "group":
        return _carry_state(
            state,
            turn_phase=TurnPhase.MELD,
            can_group=False,
            can_drop=True,
            scatter_points=max(0, state.scatter_points - 8),
            note="Mock: Group formed, can Drop",
        )
    if action == "drop":
        return _carry_state(
            state,
            turn_phase=TurnPhase.DUMP,
            can_drop=False,
            scatter_points=max(0, state.scatter_points - 6),
            note="Mock: Meld dropped → must Dump",
        )
    if action == "dump":
        return _carry_state(state, turn_phase=TurnPhase.IDLE, note="Mock: Dump done")
    return state


# ---------------------------------------------------------------------------
# 规则决策引擎（无 LLM）
# ---------------------------------------------------------------------------


class TongitsDecisionEngine:
    """
    轻量级规则大脑：根据 CV 回合阶段与标志位选择按钮 ID。

    决策优先级（示意）：
      1. Fight 邀约且散牌足够小 → Fight
      2. 摸牌阶段：能 Chow → Special，否则 → Deck
      3. 组牌阶段：能 Group → Group；能 Drop → Drop
      4. 弃牌阶段 → Dump
    """

    def __init__(self, *, fight_point_threshold: int = 5) -> None:
        self.fight_point_threshold = fight_point_threshold

    def decide_action(self, state: VisionState) -> Decision | None:
        phase = state.turn_phase
        if state.hand_cards:
            logger.info(
                "[brain] 手牌(%d): %s | scatter=%s can_group=%s",
                len(state.hand_cards),
                state.hand_cards,
                state.scatter_points,
                state.can_group,
            )

        if phase == TurnPhase.IDLE:
            logger.info("[brain] 回合已结束，无动作")
            return None

        if phase == TurnPhase.FIGHT_OFFER:
            if state.can_fight and (
                state.should_fight or state.scatter_points <= self.fight_point_threshold
            ):
                return Decision(
                    "fight",
                    f"散牌点数={state.scatter_points} ≤ 阈值 {self.fight_point_threshold}",
                )
            return Decision(
                "deck",
                "不 Fight，进入正常摸牌（由后续帧切换到 DRAW）",
            )

        if phase == TurnPhase.DRAW:
            if state.can_chow:
                return Decision(
                    "special",
                    "上家出牌可吃，优先 Chow/Special",
                )
            return Decision("deck", "无吃牌，从牌堆摸牌")

        if phase == TurnPhase.MELD:
            if state.can_group:
                return Decision(
                    "group",
                    "手牌可组成顺子/刻子，先 Group",
                )
            if state.can_drop:
                return Decision(
                    "drop",
                    "组合已完成，亮出 Drop 减散牌",
                )
            return Decision(
                "dump",
                "无牌可组，直接弃牌结束回合",
            )

        if phase == TurnPhase.DUMP:
            return Decision("dump", "结束回合，打出一张散牌")

        logger.warning("[brain] 未处理阶段: %s", phase)
        return None

    def plan_full_turn(self, state: VisionState, *, max_steps: int = 12) -> list[Decision]:
        """干跑：在 Mock 状态推进下生成一整回合动作序列。"""
        plan: list[Decision] = []
        cur = state
        for _ in range(max_steps):
            dec = self.decide_action(cur)
            if dec is None:
                break
            plan.append(dec)
            cur = advance_mock_state(cur, dec)
            if cur.turn_phase == TurnPhase.IDLE:
                break
        return plan


# ---------------------------------------------------------------------------
# 执行层
# ---------------------------------------------------------------------------


def _screen_click_xy(
    cx: int,
    cy: int,
    *,
    screen_width: int,
    screen_height: int,
) -> tuple[int, int]:
    """截图像素 → PyAutoGUI 屏幕坐标（处理缩放不一致）。"""
    if (os.environ.get("TONGITS_SCALE_COORDS") or "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return cx, cy
    try:
        import pyautogui

        pw, ph = pyautogui.size()
        if pw > 0 and ph > 0 and (pw, ph) != (screen_width, screen_height):
            sx = int(round(cx * pw / screen_width))
            sy = int(round(cy * ph / screen_height))
            logger.debug(
                "[hand] 坐标缩放 (%d,%d)→(%d,%d) shot=%dx%d screen=%dx%d",
                cx,
                cy,
                sx,
                sy,
                screen_width,
                screen_height,
                pw,
                ph,
            )
            return sx, sy
    except Exception:
        pass
    return cx, cy


def physical_click(
    element_id: int,
    elements_dict: dict[int, dict[str, int]],
    *,
    skip_real: bool = False,
    label: str = "",
    screen_width: int = 1920,
    screen_height: int = 1080,
) -> dict[str, Any]:
    """查 elements_dict 坐标并点击。"""
    eid = int(element_id)
    if eid not in elements_dict:
        msg = f"element_id={eid} 不在 elements_dict，可用={sorted(elements_dict.keys())}"
        logger.error("[hand] %s", msg)
        return {"ok": False, "error": msg, "element_id": eid}

    row = elements_dict[eid]
    cx = int(row["center_x"])
    cy = int(row["center_y"])
    sx, sy = _screen_click_xy(
        cx, cy, screen_width=screen_width, screen_height=screen_height
    )
    tag = label or f"id={eid}"
    logger.info(
        "[hand] physical_click %s → 标注(%d,%d) 屏幕(%d,%d)",
        tag,
        cx,
        cy,
        sx,
        sy,
    )

    if skip_real:
        return {"ok": True, "element_id": eid, "x": sx, "y": sy, "skipped": True}

    try:
        import pyautogui
    except ImportError as e:
        return {"ok": False, "error": f"pyautogui_not_installed:{e}"}

    pyautogui.FAILSAFE = True
    try:
        pyautogui.moveTo(sx, sy, duration=0.12)
        pyautogui.click()
    except Exception as e:
        return {"ok": False, "error": repr(e), "x": sx, "y": sy}
    return {"ok": True, "element_id": eid, "x": sx, "y": sy}


def physical_click_xy(
    cx: int,
    cy: int,
    *,
    skip_real: bool = False,
    label: str = "",
    screen_width: int = 1920,
    screen_height: int = 1080,
) -> dict[str, Any]:
    """按截图像素坐标点击（认牌结果 center_x/y 或固定按钮 ROI）。"""
    sx, sy = _screen_click_xy(
        int(cx), int(cy), screen_width=screen_width, screen_height=screen_height
    )
    tag = label or f"({cx},{cy})"
    logger.info(
        "[hand] physical_click_xy %s → 标注(%d,%d) 屏幕(%d,%d)",
        tag,
        cx,
        cy,
        sx,
        sy,
    )
    if skip_real:
        return {"ok": True, "x": sx, "y": sy, "skipped": True, "label": tag}

    try:
        import pyautogui
    except ImportError as e:
        return {"ok": False, "error": f"pyautogui_not_installed:{e}"}

    pyautogui.FAILSAFE = True
    try:
        pyautogui.moveTo(sx, sy, duration=0.12)
        pyautogui.click()
    except Exception as e:
        return {"ok": False, "error": repr(e), "x": sx, "y": sy}
    return {"ok": True, "x": sx, "y": sy, "label": tag}


def execute_decision(
    decision: Decision,
    state: VisionState,
    *,
    dry_run: bool = True,
    click_delay: float = 0.35,
) -> dict[str, Any]:
    """执行单步决策并打日志。"""
    legacy = LEGACY_REFERENCE_IDS.get(decision.action)
    if legacy is not None and state.elements:
        log_legacy_id_trap(decision.action, legacy, state.elements)

    try:
        eid = resolve_action_element_id(
            decision.action,
            state.action_id_map,
            elements_dict=state.elements_dict,
        )
    except RuntimeError as e:
        logger.error("[act] %s", e)
        return {"ok": False, "error": str(e)}

    row = state.elements_dict.get(eid, {})
    logger.info(
        "[act] 决策: action=%s → 解析 id=%s @(%s,%s) | %s",
        decision.action,
        eid,
        row.get("center_x"),
        row.get("center_y"),
        decision.reason,
    )
    res = physical_click(
        eid,
        state.elements_dict,
        skip_real=dry_run,
        label=decision.action,
        screen_width=state.screen_width,
        screen_height=state.screen_height,
    )
    if not res.get("ok"):
        logger.error("[act] 点击失败: %s", res.get("error"))
    elif click_delay > 0 and not dry_run:
        time.sleep(click_delay)
    return res


# ---------------------------------------------------------------------------
# OmniParser Observe
# ---------------------------------------------------------------------------


def _load_parsed_elements(work_dir: str | None) -> list[dict[str, Any]]:
    if not work_dir:
        return []
    p = Path(work_dir) / "parsed_result.json"
    if not p.is_file():
        return []
    try:
        full = json.loads(p.read_text(encoding="utf-8"))
        rich = full.get("elements") or []
        return rich if isinstance(rich, list) else []
    except Exception:
        return []


def _elements_to_dict(elements: list[dict[str, Any]]) -> dict[int, dict[str, int]]:
    from tongits_button_resolver import _element_center

    out: dict[int, dict[str, int]] = {}
    for row in elements:
        try:
            eid = int(row["id"])
            cx, cy = _element_center(row)
            out[eid] = {"center_x": cx, "center_y": cy}
        except (TypeError, ValueError, KeyError):
            continue
    return out


def observe_omniparser(
    *,
    bbox_threshold: float = 0.03,
    iou_threshold: float = 0.1,
    capture_window: bool = False,
    hand_card_ids: set[int] | None = None,
) -> ObserveBundle:
    """全屏/窗口截屏 + OmniParser → 标注图、坐标表、动作按钮映射。"""
    from core.mcp_multimodal_result import parse_multimodal_observation_payload

    from l3_client.local_mcps.holographic_screen_mcp.session_service import (
        get_holographic_screen_service,
    )

    logger.info("[eye] OmniParser Observe …")
    raw = get_holographic_screen_service().get_holographic_screen(
        capture_window=capture_window,
        bbox_threshold=bbox_threshold,
        iou_threshold=iou_threshold,
    )
    text, _urls = parse_multimodal_observation_payload(raw)
    obj = json.loads(text)
    if not obj.get("ok"):
        raise RuntimeError(f"OmniParser 失败: {obj.get('error')}")

    sw = int(obj.get("screen_width") or 1920)
    sh = int(obj.get("screen_height") or 1080)
    work_dir = str(obj.get("work_dir") or "")
    elements = _load_parsed_elements(work_dir or None)
    if not elements:
        elements = list(obj.get("elements") or [])

    elements_dict = _elements_to_dict(elements)
    if not elements_dict:
        raw_ed = obj.get("elements_dict") or {}
        if isinstance(raw_ed, dict):
            for k, v in raw_ed.items():
                try:
                    elements_dict[int(k)] = {
                        "center_x": int(v["center_x"]),
                        "center_y": int(v["center_y"]),
                    }
                except (TypeError, ValueError, KeyError):
                    continue

    ann = obj.get("annotated_image_path") or ""
    if work_dir and not ann:
        for name in ("parsed_output.jpg", "annotated.jpg"):
            cand = Path(work_dir) / name
            if cand.is_file():
                ann = str(cand)
                break
    if not ann:
        raise RuntimeError("Observe 未返回 annotated_image_path")

    action_id_map = resolve_action_buttons(
        elements,
        screen_height=sh,
        hand_card_ids=hand_card_ids,
    )

    logger.info(
        "[eye] 标注图=%s elements=%d 动作映射=%s",
        ann,
        len(elements_dict),
        action_id_map,
    )
    for action, legacy_id in LEGACY_REFERENCE_IDS.items():
        resolved = action_id_map.get(action)
        if resolved is not None and resolved != legacy_id:
            logger.info(
                "[eye] 动态 ID: %s 文档参考=%s 本帧实际=%s",
                action,
                legacy_id,
                resolved,
            )

    raw_path = _resolve_raw_screenshot(str(ann), work_dir)

    return ObserveBundle(
        annotated_path=str(ann),
        elements_dict=elements_dict,
        elements=elements,
        action_id_map=action_id_map,
        screen_width=sw,
        screen_height=sh,
        raw_screenshot_path=raw_path,
    )


def _perceive_hand_cards(
    obs: ObserveBundle,
    *,
    card_backend: str,
    model: str | None,
    use_vlm_buttons: bool,
) -> tuple[list[dict[str, Any]], ObserveBundle]:
    """
    认牌 + 刷新动作按钮映射（排除手牌 ID）。
    返回 (cards_for_engine, updated_obs)。
    """
    exclude_pre = set(obs.action_id_map.values())

    if card_backend == "vlm":
        cards = analyze_cards_with_qwen(
            obs.annotated_path, obs.elements_dict, model=model
        )
        logger.info("[perceive] VLM 全屏认牌: %s", json.dumps(cards, ensure_ascii=False))
    elif card_backend == "board":
        shot = obs.raw_screenshot_path or obs.annotated_path
        raw_state = analyze_board_image(shot, model=model)
        cn_state = board_state_to_chinese(raw_state)
        print_board_state(raw_state, use_chinese=True)
        cards = hand_cards_for_engine(cn_state)
        logger.info(
            "[perceive] 牌局快照(中文): %s",
            json.dumps(cn_state, ensure_ascii=False),
        )
        logger.info(
            "[perceive] 决策用手牌: %s",
            json.dumps(
                [{"id": c["id"], "suit": c["suit"], "rank": c["rank"], "cn": c.get("label_cn")} for c in cards],
                ensure_ascii=False,
            ),
        )
        obs = ObserveBundle(
            annotated_path=obs.annotated_path,
            elements_dict=obs.elements_dict,
            elements=obs.elements,
            action_id_map=obs.action_id_map,
            screen_width=obs.screen_width,
            screen_height=obs.screen_height,
            raw_screenshot_path=obs.raw_screenshot_path,
            board_snapshot_cn=cn_state,
        )
    else:
        shot = obs.raw_screenshot_path or obs.annotated_path
        if card_backend == "opencv":
            # 固定 ROI（双引擎校准）+ 角点扫掠；elements 供锚点推导 ROI
            raw_snapshot = opencv_recognize_cards(
                shot,
                obs.elements_dict,
                elements=obs.elements,
                screen_height=obs.screen_height,
            )
            if isinstance(raw_snapshot, dict):
                logger.info(
                    "[perceive] OpenCV 全景快照: %s",
                    json.dumps(raw_snapshot, ensure_ascii=False),
                )
                raw_cards = raw_snapshot
            else:
                raw_cards = raw_snapshot
        else:
            hand_ids = filter_hand_card_ids(
                obs.elements_dict,
                screen_height=obs.screen_height,
                elements=obs.elements,
                exclude_ids=exclude_pre,
            )
            raw_cards = memory_recognize_cards(
                shot,
                obs.elements_dict,
                elements=obs.elements,
                hand_card_ids=hand_ids,
                exclude_ids=exclude_pre,
                screen_height=obs.screen_height,
            )
        cards = cards_to_engine_json(raw_cards)
        logger.info(
            "[perceive] 认牌(%s) 决策用手牌: %s",
            card_backend,
            json.dumps(cards, ensure_ascii=False),
        )

    hand_ids = {int(c["id"]) for c in cards}
    action_map = resolve_action_buttons(
        obs.elements,
        screen_height=obs.screen_height,
        hand_card_ids=hand_ids,
    )
    missing = [a for a in ACTION_NAMES if a not in action_map]
    if missing and use_vlm_buttons and card_backend != "vlm":
        try:
            vlm_btns = analyze_buttons_with_qwen(obs.annotated_path, model=model)
            action_map = {**vlm_btns, **action_map}
        except Exception as e:
            logger.warning("[perceive] VLM 按钮回退失败: %s", e)

    obs2 = ObserveBundle(
        annotated_path=obs.annotated_path,
        elements_dict=obs.elements_dict,
        elements=obs.elements,
        action_id_map=action_map,
        screen_width=obs.screen_width,
        screen_height=obs.screen_height,
        raw_screenshot_path=obs.raw_screenshot_path,
    )
    logger.info("[perceive] 动作按钮映射: %s", action_map)
    return cards, obs2


def advance_cv_state(cv: dict[str, Any], decision: Decision) -> dict[str, Any]:
    """根据已执行动作推进 cv_state_dict（无二次 VLM 时的阶段模拟）。"""
    cv = dict(cv)
    action = decision.action
    if action == "fight":
        cv["turn_phase"] = "idle"
    elif action == "special":
        cv["turn_phase"] = "meld"
    elif action == "deck":
        cv["turn_phase"] = "meld"
        cv["can_group"] = cv.get("can_group", True)
        cv["can_drop"] = True
    elif action == "group":
        cv["turn_phase"] = "meld"
        cv["can_group"] = False
        cv["can_drop"] = True
        cv["scatter_points"] = max(0, int(cv.get("scatter_points") or 0) - 8)
    elif action == "drop":
        cv["turn_phase"] = "dump"
        cv["can_drop"] = False
        cv["scatter_points"] = max(0, int(cv.get("scatter_points") or 0) - 6)
    elif action == "dump":
        cv["turn_phase"] = "idle"
    return cv


def run_live_pipeline_turn(
    *,
    image_path: str | None = None,
    elements_dict: dict[int, dict[str, int]] | None = None,
    dry_run: bool = True,
    max_steps: int = 12,
    model: str | None = None,
    card_backend: str = "memory",
    use_phase_vlm: bool = False,
    use_vlm_buttons: bool = True,
    reobserve_each_step: bool = False,
) -> int:
    """
    全链路：OmniParser → 认牌(opencv|vlm) → cv_state_dict → 规则引擎 → physical_click。

    默认 card_backend=memory（OpenCV 命中 + VLM 未命中时自动入库）。
    """
    engine = TongitsDecisionEngine()
    model = model or default_vlm_model()
    cv: dict[str, Any] | None = None
    obs: ObserveBundle | None = None

    if card_backend == "opencv":
        try:
            mc = get_card_matcher().template_count
            if mc == 0:
                logger.error(
                    "OpenCV 模板为空，请将牌面放入 scripts/card_templates/ "
                    "或改用默认自生长记忆库（去掉 --opencv）"
                )
                return 1
            logger.info("[pipeline] 认牌后端=OpenCV 模板数=%d", mc)
        except Exception as e:
            logger.error("[pipeline] OpenCV 初始化失败: %s", e)
            return 1
    elif card_backend == "vlm":
        logger.info("[pipeline] 认牌后端=VLM 全屏 model=%s", model)
    elif card_backend == "board":
        logger.info(
            "[pipeline] 认牌后端=牌局快照 VLM（无 OmniParser/OpenCV）model=%s",
            model,
        )
    else:
        try:
            mem = get_self_learning_recognizer()
            logger.info(
                "[pipeline] 认牌后端=自生长记忆库 已有模板=%d 目录=%s",
                mem.template_count,
                mem.memory_dir,
            )
        except Exception as e:
            logger.error("[pipeline] 记忆库初始化失败: %s", e)
            return 1

    logger.info("=" * 60)
    logger.info(
        "[pipeline] 感知(%s) + 规则决策  dry_run=%s",
        card_backend,
        dry_run,
    )
    logger.info("=" * 60)

    step = 0
    while step < max_steps:
        step += 1
        logger.info("--- Pipeline Step %d ---", step)

        if reobserve_each_step or step == 1:
            if card_backend == "board":
                import cv2

                img_path = resolve_screenshot_for_board(
                    image_path if step == 1 and not reobserve_each_step else None,
                    countdown_sec=3 if not image_path or reobserve_each_step else 0,
                    save_dir=_SCRIPTS_DIR / "omnioutput",
                )
                screen = cv2.imread(str(img_path))
                sh, sw = (screen.shape[:2] if screen is not None else (1080, 1920))
                action_map: dict[str, int] = {}
                try:
                    action_map = analyze_buttons_with_qwen(str(img_path), model=model)
                except Exception as e:
                    logger.warning("[pipeline] VLM 按钮映射失败: %s", e)
                obs = ObserveBundle(
                    annotated_path=str(img_path),
                    elements_dict={},
                    elements=[],
                    action_id_map=action_map,
                    screen_width=sw,
                    screen_height=sh,
                    raw_screenshot_path=str(img_path),
                )
                logger.info(
                    "[pipeline][1] Board Observe 截图=%s 按钮=%s",
                    img_path,
                    action_map,
                )
            elif not image_path or reobserve_each_step:
                obs = observe_omniparser()
            elif step == 1 and image_path:
                elements_full = _mock_elements_full()
                obs = ObserveBundle(
                    annotated_path=image_path,
                    elements_dict=elements_dict or _elements_to_dict(elements_full),
                    elements=elements_full,
                    action_id_map=resolve_action_buttons(elements_full),
                )
            assert obs is not None
            logger.info(
                "[pipeline][1] Observe 完成 raw=%s",
                obs.raw_screenshot_path or obs.annotated_path,
            )

            cards, obs = _perceive_hand_cards(
                obs,
                card_backend=card_backend,
                model=model,
                use_vlm_buttons=use_vlm_buttons,
            )
            logger.info("[pipeline][2] 认牌: %s", json.dumps(cards, ensure_ascii=False))

            phase_overlay = None
            if use_phase_vlm:
                phase_overlay = analyze_game_phase_with_qwen(obs.annotated_path, model=model)
                logger.info("[pipeline][2b] VLM UI 阶段: %s", phase_overlay)

            if card_backend == "board" and obs.board_snapshot_cn:
                cv = board_snapshot_to_cv_state_dict(
                    obs.board_snapshot_cn,
                    phase_overlay=phase_overlay,
                )
            else:
                cv = cards_to_cv_state_dict(cards, phase_overlay=phase_overlay)
            logger.info(
                "[pipeline][3] cv_state_dict: %s",
                json.dumps(
                    {k: v for k, v in cv.items() if k not in ("hand_cards", "table_snapshot")},
                    ensure_ascii=False,
                ),
            )
            if cv.get("table_snapshot"):
                logger.info(
                    "[pipeline][3] table_snapshot(中文): %s",
                    json.dumps(cv["table_snapshot"], ensure_ascii=False),
                )
        assert cv is not None and obs is not None

        state = cv_state_dict_to_vision_state(
            cv,
            obs.elements_dict,
            annotated_image_path=obs.annotated_path,
            action_id_map=obs.action_id_map,
            elements=obs.elements,
            screen_width=obs.screen_width,
            screen_height=obs.screen_height,
        )
        logger.info("[pipeline][4] VisionState phase=%s", state.turn_phase.value)

        if step == 1:
            preview = engine.plan_full_turn(state, max_steps=max_steps)
            logger.info(
                "[pipeline][4] 规则预演整回合: %s",
                " → ".join(
                    f"{d.action}[{state.action_id_map.get(d.action, '?')}]" for d in preview
                )
                or "(空)",
            )

        decision = engine.decide_action(state)
        if decision is None:
            logger.info("[pipeline] 无更多决策，结束")
            break

        resolved_id = state.action_id_map.get(decision.action)
        logger.info(
            "[pipeline][4] 本步决策: %s → 解析 id=%s | %s",
            decision.action,
            resolved_id,
            decision.reason,
        )

        res = execute_decision(decision, state, dry_run=dry_run)
        if not res.get("ok"):
            return 1
        logger.info(
            "[pipeline][5] 点击坐标: (%s, %s) action=%s",
            res.get("x"),
            res.get("y"),
            decision.action,
        )

        cv = advance_cv_state(cv, decision)
        if cv.get("turn_phase") == "idle":
            logger.info("[pipeline] 回合结束 (idle)")
            break
        if not reobserve_each_step:
            logger.info("[pipeline] 阶段推进 → %s", cv.get("turn_phase"))

    logger.info("=" * 60)
    logger.info("[pipeline] 结束，共 %d 步", step)
    logger.info("=" * 60)
    return 0


def run_vlm_pipeline_turn(**kwargs: Any) -> int:
    """兼容旧入口：强制 VLM 认牌。"""
    kwargs["card_backend"] = "vlm"
    return run_live_pipeline_turn(**kwargs)


# ---------------------------------------------------------------------------
# 主循环（Mock）
# ---------------------------------------------------------------------------


def run_simulated_turn(
    *,
    scenario: str = "normal_turn",
    dry_run: bool = True,
    max_steps: int = 12,
) -> int:
    """
    模拟一整回合：Observe → Decide → Act 循环，直到 IDLE 或步数上限。
    """
    engine = TongitsDecisionEngine()
    state = get_vision_state(scenario)

    logger.info("=" * 60)
    logger.info(
        "[turn] 开始 scenario=%s phase=%s | %s",
        scenario,
        state.turn_phase.value,
        state.note,
    )
    logger.info("[turn] 动作按钮映射: %s", state.action_id_map)
    logger.info("=" * 60)

    step = 0
    while step < max_steps and state.turn_phase != TurnPhase.IDLE:
        step += 1
        logger.info("--- Step %d | phase=%s ---", step, state.turn_phase.value)

        decision = engine.decide_action(state)
        if decision is None:
            logger.warning("[turn] 无决策，退出")
            break

        res = execute_decision(decision, state, dry_run=dry_run)
        if not res.get("ok"):
            return 1

        state = advance_mock_state(state, decision)
        logger.info("[turn] 推进后 phase=%s | %s", state.turn_phase.value, state.note)

    logger.info("=" * 60)
    logger.info("[turn] 结束，共 %d 步，最终 phase=%s", step, state.turn_phase.value)
    logger.info("=" * 60)
    return 0


def run_all_scenarios(*, dry_run: bool = True) -> int:
    """跑多种 Mock 场景，验证规则分支。"""
    scenarios = ("fight_win", "chow_draw", "normal_turn", "dump_only")
    rc = 0
    engine = TongitsDecisionEngine()
    for sc in scenarios:
        logger.info("\n######## 场景: %s ########\n", sc)
        if run_simulated_turn(scenario=sc, dry_run=dry_run) != 0:
            rc = 1
        st = get_vision_state(sc)
        plan = engine.plan_full_turn(st)
        logger.info(
            "[plan] %s 预演序列: %s",
            sc,
            " → ".join(
                f"{d.action}[{st.action_id_map.get(d.action, '?')}]" for d in plan
            ),
        )
    return rc


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(
        description="Tongits 规则 Bot（VLM 只认牌，决策无 LLM）",
    )
    ap.add_argument(
        "--scenario",
        default="normal_turn",
        choices=("fight_win", "chow_draw", "normal_turn", "dump_only", "all"),
        help="Mock CV 场景（非 --vlm 时）",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="实机全链路：OmniParser + 自生长记忆库认牌 + 规则 + 点击",
    )
    ap.add_argument(
        "--vlm",
        action="store_true",
        help="认牌改用百炼全屏 VLM（慢，易超时）",
    )
    ap.add_argument(
        "--board",
        action="store_true",
        help="MVP：截屏+Qwen 五区牌局快照（中文），不用 OmniParser/OpenCV",
    )
    ap.add_argument(
        "--opencv",
        action="store_true",
        help="仅用静态 OpenCV 模板（card_templates/，无 VLM 入库）",
    )
    ap.add_argument(
        "--memory",
        action="store_true",
        help="显式使用自生长记忆库（--live 默认）",
    )
    ap.add_argument(
        "--image",
        default=None,
        help="已有 OmniParser 标注图路径（--vlm 时可跳过首次 Observe）",
    )
    ap.add_argument("--model", default=None, help="VLM 模型，默认 qwen-vl-max")
    ap.add_argument(
        "--phase-vlm",
        action="store_true",
        help="额外调用 VLM 识别 turn_phase（仍不参与出牌决策）",
    )
    ap.add_argument(
        "--no-vlm-buttons",
        action="store_true",
        help="OCR 未命中按钮时不回退 VLM 按钮映射",
    )
    ap.add_argument(
        "--reobserve",
        action="store_true",
        help="每步重新 Observe + 认牌（慢但更准）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只打日志，不移动鼠标（与 --live 互斥）",
    )
    ap.add_argument("--max-steps", type=int, default=12)
    args = ap.parse_args()

    dry_run = args.dry_run or not args.live
    if args.board:
        card_backend = "board"
    elif args.vlm:
        card_backend = "vlm"
    elif args.opencv:
        card_backend = "opencv"
    else:
        card_backend = "memory"

    if args.live or args.vlm or args.board or args.image:
        return run_live_pipeline_turn(
            image_path=args.image,
            dry_run=dry_run,
            max_steps=args.max_steps,
            model=args.model,
            card_backend=card_backend,
            use_phase_vlm=args.phase_vlm,
            use_vlm_buttons=not args.no_vlm_buttons,
            reobserve_each_step=args.reobserve,
        )
    if args.scenario == "all":
        return run_all_scenarios(dry_run=dry_run)
    return run_simulated_turn(
        scenario=args.scenario,
        dry_run=dry_run,
        max_steps=args.max_steps,
    )


if __name__ == "__main__":
    raise SystemExit(main())
