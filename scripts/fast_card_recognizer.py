#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tongits 手牌极速识别 — 固定 ROI + 角点模板多目标扫掠（不依赖 OmniParser 切牌）。

架构：
  1. 从全屏图裁切 PLAYER_HAND_ROI → hand_zone_img
  2. 对 52 张「左上角角点」小图做 cv2.matchTemplate + np.where 多峰
  3. 距离 NMS 去重 → 按 x 排序 → 输出 suit/rank + 全屏 center_x/center_y

角点模板目录（默认 scripts/card_corner_templates，可与整牌目录分开）：
  命名 {S|H|C|D}{A|2-10|J|Q|K}.png，例如 H9.png、DK.png

环境变量：
  TONGITS_CORNER_TEMPLATES_DIR   角点模板目录
  TONGITS_PLAYER_HAND_ROI        物理 ROI：x1,y1,x2,y2（优先于下方常量）
  TONGITS_HAND_ROI_X_MIN_RATIO   无 ROI 时用比例（默认 0.27）
  TONGITS_HAND_ROI_X_MAX_RATIO   默认 0.73
  TONGITS_HAND_ROI_Y_MIN_RATIO   默认 0.74
  TONGITS_HAND_ROI_Y_MAX_RATIO   默认 0.88
  TONGITS_CORNER_MATCH_THRESHOLD 匹配阈值，默认 0.94（严控误报）
  TONGITS_CORNER_NMS_MIN_DIST    NMS 最小中心距（像素），默认 15
  TONGITS_CORNER_MAX_CARDS       手牌区最多保留，默认 14
  TONGITS_ROI_PLAYER_HAND         手牌区 x1,y1,x2,y2（覆盖自动标定）
  TONGITS_ROI_CENTER_DISCARD      中央弃牌区
  TONGITS_ROI_OPPONENT_LEFT       左侧对手明牌区
  TONGITS_ROI_OPPONENT_RIGHT      右侧对手明牌区
  TONGITS_CORNER_MATCH_SCALES    多尺度，默认 1.0（可 0.9,1.0,1.1）

离线测试：
  python scripts/fast_card_recognizer.py --image scripts/omnioutput/xxx_raw.png
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("fast_card_recognizer")

VALID_SUITS = frozenset({"S", "H", "C", "D"})
VALID_RANKS = frozenset(
    {"A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"}
)

_FILENAME_RE = re.compile(
    r"^([SHCD])(A|[2-9]|10|J|Q|K)$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 手牌 ROI — 由 roi_calibrator.auto_calibrate_roi 双引擎标定（见 roi_config.json）
# 显式覆盖仍可设 TONGITS_PLAYER_HAND_ROI 或 recognize_cards(roi=...)
# ---------------------------------------------------------------------------
PLAYER_HAND_ROI: tuple[int, int, int, int] = (0, 0, 0, 0)

# 进程内已解析 ROI（避免同帧重复读盘）
_SESSION_HAND_ROI: tuple[int, int, int, int] | None = None
_SESSION_ROI_ENGINE: str = ""

# 四战区（全景感知）
ZONE_PLAYER_HAND = "player_hand"
ZONE_CENTER_DISCARD = "center_discard"
ZONE_OPPONENT_LEFT = "opponent_left"
ZONE_OPPONENT_RIGHT = "opponent_right"
ZONE_KEYS: tuple[str, ...] = (
    ZONE_PLAYER_HAND,
    ZONE_CENTER_DISCARD,
    ZONE_OPPONENT_LEFT,
    ZONE_OPPONENT_RIGHT,
)

# 合成 id 起点（按战区分段，避免冲突）
_ZONE_ID_BASE: dict[str, int] = {
    ZONE_PLAYER_HAND: 9001,
    ZONE_CENTER_DISCARD: 9100,
    ZONE_OPPONENT_LEFT: 9200,
    ZONE_OPPONENT_RIGHT: 9300,
}

_ZONE_MAX_CARDS: dict[str, int] = {
    ZONE_PLAYER_HAND: 14,
    ZONE_CENTER_DISCARD: 4,
    ZONE_OPPONENT_LEFT: 24,
    ZONE_OPPONENT_RIGHT: 24,
}

_ZONE_ENV_ROI: dict[str, str] = {
    ZONE_PLAYER_HAND: "TONGITS_ROI_PLAYER_HAND",
    ZONE_CENTER_DISCARD: "TONGITS_ROI_CENTER_DISCARD",
    ZONE_OPPONENT_LEFT: "TONGITS_ROI_OPPONENT_LEFT",
    ZONE_OPPONENT_RIGHT: "TONGITS_ROI_OPPONENT_RIGHT",
}

# 兼容旧名
ROI_PLAYER_HAND = ZONE_PLAYER_HAND

_MATCHER: "CornerTemplateMatcher | None" = None
_SESSION_ZONE_ROIS: dict[str, tuple[int, int, int, int]] | None = None


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _default_corner_templates_dir() -> Path:
    raw = (os.environ.get("TONGITS_CORNER_TEMPLATES_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    corner = Path(__file__).resolve().parent / "card_corner_templates"
    if corner.is_dir() and any(corner.glob("*.png")):
        return corner
    return Path(__file__).resolve().parent / "card_templates"


def _parse_template_name(stem: str) -> tuple[str, str] | None:
    m = _FILENAME_RE.match(stem.strip().upper())
    if not m:
        return None
    return m.group(1).upper(), m.group(2).upper()


def reset_hand_roi_session() -> None:
    """强制下一帧重新走 auto_calibrate_roi（配合 TONGITS_ROI_FORCE_RECALIBRATE）。"""
    global _SESSION_HAND_ROI, _SESSION_ROI_ENGINE, PLAYER_HAND_ROI, _SESSION_ZONE_ROIS
    _SESSION_HAND_ROI = None
    _SESSION_ROI_ENGINE = ""
    _SESSION_ZONE_ROIS = None
    PLAYER_HAND_ROI = (0, 0, 0, 0)


def _parse_roi_env(env_name: str) -> tuple[int, int, int, int] | None:
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return None
    try:
        parts = [int(x.strip()) for x in raw.replace(" ", "").split(",") if x.strip()]
        if len(parts) == 4 and parts[2] > parts[0] and parts[3] > parts[1]:
            return tuple(parts)  # type: ignore[return-value]
    except ValueError:
        pass
    return None


def _clamp_roi(
    roi: tuple[int, int, int, int],
    screen_width: int,
    screen_height: int,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = roi
    x1 = max(0, min(x1, screen_width - 2))
    y1 = max(0, min(y1, screen_height - 2))
    x2 = max(x1 + 1, min(x2, screen_width))
    y2 = max(y1 + 1, min(y2, screen_height))
    return x1, y1, x2, y2


def _find_action_bar_bottom_y(
    elements: list[dict[str, Any]] | None,
    elements_dict: dict[int, dict[str, int]] | None,
) -> int | None:
    """Dump/Group 按钮底边 y2，用于多战区推算。"""
    import re

    scan: list[dict[str, Any]] = list(elements or [])
    if not scan and elements_dict:
        for eid, row in sorted(elements_dict.items()):
            scan.append(
                {
                    "id": eid,
                    "content": row.get("content", ""),
                    "center_y": row.get("center_y"),
                    "bbox_xyxy_pixels": row.get("bbox_xyxy_pixels"),
                }
            )
    bottoms: list[int] = []
    for row in scan:
        text = re.sub(r"\s+", "", str(row.get("content") or "").lower())
        if text not in ("dump", "group"):
            continue
        b = row.get("bbox_xyxy_pixels") or []
        if isinstance(b, (list, tuple)) and len(b) >= 4:
            bottoms.append(int(b[3]))
        else:
            cy = row.get("center_y")
            if cy is not None:
                bottoms.append(int(cy) + 18)
    return max(bottoms) if bottoms else None


def resolve_multi_zone_rois(
    screen_width: int,
    screen_height: int,
    *,
    screenshot_path: str | None = None,
    elements_dict: dict[int, dict[str, int]] | None = None,
    elements: list[dict[str, Any]] | None = None,
    force_recalibrate: bool = False,
    hand_roi_override: tuple[int, int, int, int] | None = None,
) -> dict[str, tuple[int, int, int, int]]:
    """
    解析四战区 ROI。player_hand 走校准器；其余由锚点+比例推算，环境变量可覆盖。
    """
    global _SESSION_ZONE_ROIS

    if (
        not force_recalibrate
        and _SESSION_ZONE_ROIS
        and ZONE_PLAYER_HAND in _SESSION_ZONE_ROIS
    ):
        return dict(_SESSION_ZONE_ROIS)

    sw, sh = screen_width, screen_height
    anchor_y2 = _find_action_bar_bottom_y(elements, elements_dict)

    hand = resolve_player_hand_roi(
        sw,
        sh,
        roi_override=hand_roi_override,
        screenshot_path=screenshot_path,
        elements_dict=elements_dict,
        elements=elements,
        force_recalibrate=force_recalibrate,
    )

    gap = _env_int("TONGITS_ROI_ANCHOR_Y_GAP", 10)
    hand_h = _env_int("TONGITS_ROI_ANCHOR_HEIGHT", 200)
    if anchor_y2 is not None:
        cd_above = _env_int("TONGITS_ROI_DISCARD_ABOVE_ANCHOR", 90)
        cd_h = _env_int("TONGITS_ROI_DISCARD_HEIGHT", 240)
        cd_y2 = max(0, anchor_y2 - cd_above)
        cd_y1 = max(0, cd_y2 - cd_h)
        center_discard = _clamp_roi(
            (
                int(sw * _env_float("TONGITS_ROI_DISCARD_X_MIN_RATIO", 0.36)),
                cd_y1,
                int(sw * _env_float("TONGITS_ROI_DISCARD_X_MAX_RATIO", 0.64)),
                cd_y2,
            ),
            sw,
            sh,
        )
        meld_h = _env_int("TONGITS_ROI_MELD_HEIGHT", 320)
        meld_y2 = max(0, anchor_y2 - _env_int("TONGITS_ROI_MELD_ABOVE_ANCHOR", 120))
        meld_y1 = max(0, meld_y2 - meld_h)
    else:
        center_discard = _clamp_roi(
            (
                int(sw * 0.36),
                int(sh * 0.30),
                int(sw * 0.64),
                int(sh * 0.56),
            ),
            sw,
            sh,
        )
        meld_y1 = int(sh * 0.14)
        meld_y2 = int(sh * 0.50)

    opponent_left = _clamp_roi(
        (
            int(sw * _env_float("TONGITS_ROI_LEFT_X_MIN_RATIO", 0.02)),
            meld_y1,
            int(sw * _env_float("TONGITS_ROI_LEFT_X_MAX_RATIO", 0.36)),
            meld_y2,
        ),
        sw,
        sh,
    )
    opponent_right = _clamp_roi(
        (
            int(sw * _env_float("TONGITS_ROI_RIGHT_X_MIN_RATIO", 0.64)),
            meld_y1,
            int(sw * _env_float("TONGITS_ROI_RIGHT_X_MAX_RATIO", 0.98)),
            meld_y2,
        ),
        sw,
        sh,
    )

    rois: dict[str, tuple[int, int, int, int]] = {
        ZONE_PLAYER_HAND: _clamp_roi(hand, sw, sh),
        ZONE_CENTER_DISCARD: center_discard,
        ZONE_OPPONENT_LEFT: opponent_left,
        ZONE_OPPONENT_RIGHT: opponent_right,
    }

    for zone, env_key in _ZONE_ENV_ROI.items():
        custom = _parse_roi_env(env_key)
        if custom:
            rois[zone] = _clamp_roi(custom, sw, sh)
            logger.info("[corner] 战区 %s 环境覆盖 → %s", zone, rois[zone])

    if anchor_y2:
        logger.info(
            "[corner] 多战区锚点 action_bar_y2=%s hand=%s discard=%s left=%s right=%s",
            anchor_y2,
            rois[ZONE_PLAYER_HAND],
            rois[ZONE_CENTER_DISCARD],
            rois[ZONE_OPPONENT_LEFT],
            rois[ZONE_OPPONENT_RIGHT],
        )

    _SESSION_ZONE_ROIS = dict(rois)
    return rois


def resolve_player_hand_roi(
    screen_width: int,
    screen_height: int,
    *,
    roi_override: tuple[int, int, int, int] | None = None,
    screenshot_path: str | None = None,
    elements_dict: dict[int, dict[str, int]] | None = None,
    elements: list[dict[str, Any]] | None = None,
    force_recalibrate: bool = False,
) -> tuple[int, int, int, int]:
    """
    解析手牌裁切区 (x1, y1, x2, y2)。优先级：
      roi_override → auto_calibrate_roi（cache/anchor/vlm/fallback）→ 比例兜底。
    """
    global _SESSION_HAND_ROI, _SESSION_ROI_ENGINE, PLAYER_HAND_ROI

    if roi_override and roi_override[2] > roi_override[0] and roi_override[3] > roi_override[1]:
        return roi_override

    if (
        not force_recalibrate
        and _SESSION_HAND_ROI
        and _SESSION_HAND_ROI[2] > _SESSION_HAND_ROI[0]
    ):
        return _SESSION_HAND_ROI

    if screenshot_path:
        try:
            from roi_calibrator import auto_calibrate_roi

            roi, engine = auto_calibrate_roi(
                screenshot_path,
                elements_dict,
                elements,
                screen_width,
                screen_height,
                force_recalibrate=force_recalibrate,
            )
            _SESSION_HAND_ROI = roi
            _SESSION_ROI_ENGINE = engine
            PLAYER_HAND_ROI = roi
            logger.info(
                "[corner] 手牌 ROI 来自校准引擎=%s → %s",
                engine,
                roi,
            )
            return roi
        except Exception as e:
            logger.warning("[corner] auto_calibrate_roi 失败，使用比例兜底: %s", e)

    x1 = int(screen_width * _env_float("TONGITS_HAND_ROI_X_MIN_RATIO", 0.27))
    x2 = int(screen_width * _env_float("TONGITS_HAND_ROI_X_MAX_RATIO", 0.73))
    y1 = int(screen_height * _env_float("TONGITS_HAND_ROI_Y_MIN_RATIO", 0.74))
    y2 = int(screen_height * _env_float("TONGITS_HAND_ROI_Y_MAX_RATIO", 0.88))
    return x1, y1, x2, y2


def crop_hand_zone(
    screen_bgr: Any,
    *,
    roi: tuple[int, int, int, int] | None = None,
    screenshot_path: str | None = None,
    elements_dict: dict[int, dict[str, int]] | None = None,
    elements: list[dict[str, Any]] | None = None,
    force_recalibrate: bool = False,
) -> tuple[Any, tuple[int, int, int, int]]:
    """裁切 hand_zone，返回 (hand_zone_bgr, roi_xyxy)。"""
    sh, sw = screen_bgr.shape[:2]
    x1, y1, x2, y2 = resolve_player_hand_roi(
        sw,
        sh,
        roi_override=roi,
        screenshot_path=screenshot_path,
        elements_dict=elements_dict,
        elements=elements,
        force_recalibrate=force_recalibrate,
    )
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(sw, max(x1 + 1, x2)), min(sh, max(y1 + 1, y2))
    zone = screen_bgr[y1:y2, x1:x2]
    if zone.size == 0:
        raise ValueError(f"手牌 ROI 无效: ({x1},{y1},{x2},{y2}) 屏 {sw}x{sh}")
    return zone, (x1, y1, x2, y2)


@dataclass
class CornerHit:
    """单次角点匹配峰。"""

    suit: str
    rank: str
    template: str
    score: float
    loc_x: int  # ROI 内左上角
    loc_y: int
    tw: int
    th: int
    scale: float = 1.0

    @property
    def center_x_roi(self) -> float:
        return self.loc_x + self.tw / 2.0

    @property
    def center_y_roi(self) -> float:
        return self.loc_y + self.th / 2.0


def _dist_xy(a: CornerHit, b: CornerHit) -> float:
    dx = a.center_x_roi - b.center_x_roi
    dy = a.center_y_roi - b.center_y_roi
    return (dx * dx + dy * dy) ** 0.5


def nms_corner_hits(
    hits: list[CornerHit],
    *,
    min_dist: float | None = None,
) -> list[CornerHit]:
    """
    硬核 NMS：15px 内多模板冲突只保留最高分（消灭同角点 H4/S4 重复标）。
    """
    if not hits:
        return []
    md = min_dist if min_dist is not None else _env_float("TONGITS_CORNER_NMS_MIN_DIST", 15.0)
    ordered = sorted(hits, key=lambda h: (-h.score, h.center_x_roi, h.center_y_roi))
    kept: list[CornerHit] = []
    for h in ordered:
        suppress = False
        for k in kept:
            if _dist_xy(h, k) < md:
                suppress = True
                break
        if not suppress:
            kept.append(h)
    return sorted(kept, key=lambda h: h.center_x_roi)


@dataclass
class CornerTemplateMatcher:
    """加载角点灰度模板库。"""

    templates_dir: Path
    threshold: float = 0.94
    scales: tuple[float, ...] = (1.0,)
    _templates: dict[str, Any] = field(default_factory=dict, repr=False)
    _meta: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._load_templates()

    def _load_templates(self) -> None:
        try:
            import cv2
        except ImportError as e:
            raise RuntimeError(
                "请安装 opencv-python: pip install opencv-python-headless"
            ) from e

        self.templates_dir.mkdir(parents=True, exist_ok=True)
        paths = sorted(
            p
            for p in self.templates_dir.iterdir()
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp")
        )
        if not paths:
            logger.warning(
                "[corner] 模板目录为空: %s — 请放入角点小图 H9.png 等",
                self.templates_dir,
            )
            return

        for p in paths:
            parsed = _parse_template_name(p.stem)
            if not parsed:
                logger.warning("[corner] 跳过无法解析: %s", p.name)
                continue
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None or img.size == 0:
                logger.warning("[corner] 无法读取: %s", p)
                continue
            stem = p.stem.upper()
            self._templates[stem] = img
            self._meta[stem] = parsed

        logger.info(
            "[corner] 已加载 %d 个角点模板 ← %s",
            len(self._templates),
            self.templates_dir,
        )
        self._log_template_quality_hints()

    def _log_template_quality_hints(self) -> None:
        if not self._templates:
            return
        widths = [t.shape[1] for t in self._templates.values()]
        heights = [t.shape[0] for t in self._templates.values()]
        max_w, max_h = max(widths), max(heights)
        corner_dir = Path(__file__).resolve().parent / "card_corner_templates"
        if max_w > 36 or max_h > 36:
            logger.warning(
                "[corner] 模板尺寸偏大 (%dx%d)，疑似整牌/误采图而非左上角角点。"
                "请: python scripts/build_corner_templates.py "
                "并设置 TONGITS_CORNER_TEMPLATES_DIR=%s",
                max_w,
                max_h,
                corner_dir,
            )
        if len(self._templates) < 52:
            logger.warning(
                "[corner] 仅 %d/52 张模板，未覆盖的牌型不会被识别",
                len(self._templates),
            )
        if self.templates_dir.name == "card_templates" and (
            corner_dir.is_dir() and any(corner_dir.glob("*.png"))
        ):
            logger.warning(
                "[corner] 当前使用 card_templates；建议改用角点目录: %s",
                corner_dir,
            )

    @property
    def template_count(self) -> int:
        return len(self._templates)

    def _match_one_template(
        self,
        hand_gray: Any,
        stem: str,
        tmpl_gray: Any,
        suit: str,
        rank: str,
    ) -> list[CornerHit]:
        import cv2
        import numpy as np

        zh, zw = hand_gray.shape[:2]
        th0, tw0 = tmpl_gray.shape[:2]
        out: list[CornerHit] = []

        for scale in self.scales:
            tw = max(6, int(round(tw0 * scale)))
            th = max(6, int(round(th0 * scale)))
            if tw > zw or th > zh:
                continue
            tmpl = cv2.resize(
                tmpl_gray,
                (tw, th),
                interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
            )
            res = cv2.matchTemplate(hand_gray, tmpl, cv2.TM_CCOEFF_NORMED)
            peaks = np.where(res >= self.threshold)
            for ly, lx in zip(peaks[0], peaks[1]):
                score = float(res[ly, lx])
                out.append(
                    CornerHit(
                        suit=suit,
                        rank=rank,
                        template=stem,
                        score=score,
                        loc_x=int(lx),
                        loc_y=int(ly),
                        tw=tw,
                        th=th,
                        scale=scale,
                    )
                )
        return out

    def sweep_zone(
        self,
        zone_bgr: Any,
        *,
        max_cards: int | None = None,
        zone_name: str = "",
    ) -> list[CornerHit]:
        """在任意战区裁切图内扫掠角点模板 + 硬核 NMS。"""
        import cv2

        if not self._templates:
            return []

        if len(zone_bgr.shape) == 3:
            zone_gray = cv2.cvtColor(zone_bgr, cv2.COLOR_BGR2GRAY)
        else:
            zone_gray = zone_bgr

        all_hits: list[CornerHit] = []
        for stem, tmpl in self._templates.items():
            meta = self._meta.get(stem)
            if not meta:
                continue
            suit, rank = meta
            all_hits.extend(
                self._match_one_template(zone_gray, stem, tmpl, suit, rank)
            )

        if not all_hits:
            return []

        merged = nms_corner_hits(all_hits)
        cap = max_cards if max_cards is not None else _env_int("TONGITS_CORNER_MAX_CARDS", 14)
        if len(merged) > cap:
            merged = sorted(merged, key=lambda h: -h.score)[:cap]
            merged = sorted(merged, key=lambda h: h.center_x_roi)
        if zone_name and merged:
            logger.debug(
                "[corner][%s] 扫掠 %d 峰 → NMS 后 %d (thr=%.2f)",
                zone_name,
                len(all_hits),
                len(merged),
                self.threshold,
            )
        return merged

    def sweep_hand_zone(self, hand_zone_bgr: Any) -> list[CornerHit]:
        """兼容旧名：等同 sweep_zone（手牌区）。"""
        return self.sweep_zone(
            hand_zone_bgr,
            max_cards=_env_int("TONGITS_CORNER_MAX_CARDS", 14),
            zone_name=ZONE_PLAYER_HAND,
        )

    def match_crop(self, crop_bgr_or_gray: Any) -> tuple[str, str, float, str, float] | None:
        """[遗留] 单张裁剪区最佳角点/整牌模板（供 self_learning 等）。"""
        return match_crop_legacy(crop_bgr_or_gray, self)


def scan_corners_in_roi(
    hand_zone_img: Any,
    templates_dir: str | Path,
    *,
    threshold: float | None = None,
    scales: tuple[float, ...] | None = None,
) -> list[dict[str, Any]]:
    """
    在 hand_zone 内扫掠角点模板，返回排序后的 JSON 友好列表（ROI 相对 + 全屏坐标需外加 roi 原点）。

    若只需 ROI 内坐标，见返回字段 loc_x / loc_y；调用方加 roi_x1/roi_y1 得 center_x/center_y。
    """
    scales_raw = (os.environ.get("TONGITS_CORNER_MATCH_SCALES") or "1.0").strip()
    default_scales = tuple(float(s.strip()) for s in scales_raw.split(",") if s.strip()) or (1.0,)
    matcher = CornerTemplateMatcher(
        templates_dir=Path(templates_dir),
        threshold=threshold
        if threshold is not None
        else _env_float("TONGITS_CORNER_MATCH_THRESHOLD", 0.94),
        scales=scales or default_scales,
    )
    hits = matcher.sweep_hand_zone(hand_zone_img)
    return [
        {
            "suit": h.suit,
            "rank": h.rank,
            "score": round(h.score, 4),
            "template": h.template,
            "scale": h.scale,
            "loc_x": h.loc_x,
            "loc_y": h.loc_y,
            "tw": h.tw,
            "th": h.th,
            "center_x_roi": round(h.center_x_roi, 1),
            "center_y_roi": round(h.center_y_roi, 1),
        }
        for h in hits
    ]


def hits_to_card_rows(
    hits: list[CornerHit],
    roi: tuple[int, int, int, int],
    *,
    zone: str = ZONE_PLAYER_HAND,
    id_base: int | None = None,
) -> list[dict[str, Any]]:
    """CornerHit → 带 zone 与全屏坐标的牌行。"""
    rx1, ry1, _, _ = roi
    base = id_base if id_base is not None else _ZONE_ID_BASE.get(zone, 9001)
    rows: list[dict[str, Any]] = []
    for i, h in enumerate(hits):
        cx = int(round(rx1 + h.center_x_roi))
        cy = int(round(ry1 + h.center_y_roi))
        rows.append(
            {
                "id": base + i,
                "zone": zone,
                "suit": h.suit,
                "rank": h.rank,
                "score": round(h.score, 4),
                "template": h.template,
                "scale": h.scale,
                "loc_x": h.loc_x,
                "loc_y": h.loc_y,
                "center_x": cx,
                "center_y": cy,
                "roi": list(roi),
            }
        )
    return rows


def flatten_table_snapshot(
    snapshot: dict[str, list[dict[str, Any]]],
    *,
    zones: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """将分区快照压平为列表（保留 zone 字段）。"""
    out: list[dict[str, Any]] = []
    keys = zones or ZONE_KEYS
    for z in keys:
        for c in snapshot.get(z) or []:
            row = dict(c)
            row["zone"] = z
            out.append(row)
    return out


def empty_table_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {z: [] for z in ZONE_KEYS}


def get_corner_matcher(
    templates_dir: str | Path | None = None,
    *,
    threshold: float | None = None,
) -> CornerTemplateMatcher:
    global _MATCHER
    if _MATCHER is None:
        scales_raw = (os.environ.get("TONGITS_CORNER_MATCH_SCALES") or "1.0").strip()
        scales = tuple(float(s.strip()) for s in scales_raw.split(",") if s.strip())
        _MATCHER = CornerTemplateMatcher(
            templates_dir=Path(templates_dir)
            if templates_dir
            else _default_corner_templates_dir(),
            threshold=threshold
            if threshold is not None
            else _env_float("TONGITS_CORNER_MATCH_THRESHOLD", 0.94),
            scales=scales or (1.0,),
        )
    return _MATCHER


# 兼容旧名
CardTemplateMatcher = CornerTemplateMatcher
get_card_matcher = get_corner_matcher


def _resolve_screenshot_path(screenshot_path: str) -> Path:
    p = Path(screenshot_path)
    if p.is_file():
        return p
    if "annotated" in p.stem.lower():
        for cand in (
            p.parent / p.name.replace("annotated", "raw").replace("_annotated", "_raw"),
            p.parent / "screen_raw.png",
        ):
            if cand.is_file():
                return cand
    raise FileNotFoundError(f"截图不存在: {screenshot_path}")


def recognize_table_snapshot(
    screenshot_path: str,
    elements_dict: dict[int, dict[str, int]] | None = None,
    *,
    elements: list[dict[str, Any]] | None = None,
    matcher: CornerTemplateMatcher | None = None,
    hand_roi_override: tuple[int, int, int, int] | None = None,
    force_recalibrate: bool = False,
    zones: tuple[str, ...] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    四战区角点扫掠，返回带 zone 的分区快照（决策层全景输入）。
    """
    import cv2

    t0 = time.perf_counter()
    m = matcher or get_corner_matcher()
    if m.template_count == 0:
        raise RuntimeError(
            f"无角点模板，请将左上角小图放入 {m.templates_dir}（如 H9.png、DK.png）"
        )

    img_path = _resolve_screenshot_path(screenshot_path)
    screen = cv2.imread(str(img_path))
    if screen is None:
        raise FileNotFoundError(f"OpenCV 无法读取截图: {img_path}")

    sh, sw = screen.shape[:2]
    zone_rois = resolve_multi_zone_rois(
        sw,
        sh,
        screenshot_path=str(img_path),
        elements_dict=elements_dict,
        elements=elements,
        force_recalibrate=force_recalibrate,
        hand_roi_override=hand_roi_override,
    )

    scan_zones = zones or ZONE_KEYS
    snapshot: dict[str, list[dict[str, Any]]] = empty_table_snapshot()

    for zone in scan_zones:
        roi = zone_rois.get(zone)
        if not roi:
            continue
        x1, y1, x2, y2 = roi
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(sw, max(x1 + 1, x2)), min(sh, max(y1 + 1, y2))
        patch = screen[y1:y2, x1:x2]
        if patch.size == 0:
            logger.warning("[corner][%s] ROI 无效 %s", zone, roi)
            continue

        max_c = _ZONE_MAX_CARDS.get(zone, 14)
        env_cap = os.environ.get(f"TONGITS_CORNER_MAX_{zone.upper()}")
        if env_cap:
            try:
                max_c = int(env_cap)
            except ValueError:
                pass

        hits = m.sweep_zone(patch, max_cards=max_c, zone_name=zone)
        rows = hits_to_card_rows(hits, roi, zone=zone)
        snapshot[zone] = rows

        if rows:
            brief = ", ".join(
                f"{c['suit']}{c['rank']}@({c['center_x']},{c['center_y']})={c['score']:.2f}"
                for c in rows
            )
            logger.info("[corner][%s] %d 张: %s", zone, len(rows), brief)
        else:
            logger.info("[corner][%s] 无命中 (thr=%.2f)", zone, m.threshold)

    total = sum(len(snapshot[z]) for z in scan_zones)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "[corner] 全景快照完成 共 %d 张 | hand=%d discard=%d L=%d R=%d (%.1f ms)",
        total,
        len(snapshot[ZONE_PLAYER_HAND]),
        len(snapshot[ZONE_CENTER_DISCARD]),
        len(snapshot[ZONE_OPPONENT_LEFT]),
        len(snapshot[ZONE_OPPONENT_RIGHT]),
        elapsed_ms,
    )
    return snapshot


def recognize_cards(
    screenshot_path: str,
    elements_dict: dict[int, dict[str, int]] | None = None,
    *,
    elements: list[dict[str, Any]] | None = None,
    hand_card_ids: list[int] | None = None,
    exclude_ids: set[int] | None = None,
    screen_height: int = 1080,
    matcher: CornerTemplateMatcher | None = None,
    roi: tuple[int, int, int, int] | None = None,
    force_recalibrate: bool = False,
    flat: bool = False,
    zones: tuple[str, ...] | None = None,
) -> dict[str, list[dict[str, Any]]] | list[dict[str, Any]]:
    """
    全景多战区认牌。默认返回分区快照 dict；`flat=True` 时返回压平列表（兼容旧流水线）。

    elements_dict / elements 用于锚点推导 ROI；hand_card_ids 已忽略。
    """
    _ = hand_card_ids, exclude_ids, screen_height

    snapshot = recognize_table_snapshot(
        screenshot_path,
        elements_dict,
        elements=elements,
        matcher=matcher,
        hand_roi_override=roi,
        force_recalibrate=force_recalibrate,
        zones=zones,
    )
    if flat:
        return flatten_table_snapshot(snapshot)
    return snapshot


def cards_to_engine_json(
    cards_or_snapshot: list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
    *,
    zone: str = ZONE_PLAYER_HAND,
) -> list[dict[str, Any]]:
    """
    供 TongitsDecisionEngine：默认只导出 player_hand；传入 flat 列表则原样导出。
    """
    if isinstance(cards_or_snapshot, dict):
        cards = list(cards_or_snapshot.get(zone) or [])
    else:
        cards = cards_or_snapshot

    out: list[dict[str, Any]] = []
    for c in cards:
        row: dict[str, Any] = {
            "id": c["id"],
            "suit": c["suit"],
            "rank": c["rank"],
        }
        if c.get("zone"):
            row["zone"] = c["zone"]
        if "center_x" in c and "center_y" in c:
            row["center_x"] = int(c["center_x"])
            row["center_y"] = int(c["center_y"])
        if "score" in c:
            row["score"] = c["score"]
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 遗留：OmniParser 按 element 裁切（self_learning / spectator 仍可能引用）
# ---------------------------------------------------------------------------


def _element_bbox(
    eid: int,
    elements_dict: dict[int, dict[str, int]],
    elements: list[dict[str, Any]] | None,
    *,
    pad_w: int,
    pad_h: int,
) -> tuple[int, int, int, int] | None:
    if elements:
        for row in elements:
            try:
                if int(row["id"]) != eid:
                    continue
            except (TypeError, ValueError):
                continue
            b = row.get("bbox_xyxy_pixels") or []
            if isinstance(b, (list, tuple)) and len(b) >= 4:
                return int(b[0]), int(b[1]), int(b[2]), int(b[3])

    row = elements_dict.get(eid)
    if not row:
        return None
    cx = int(row.get("center_x") or 0)
    cy = int(row.get("center_y") or 0)
    return cx - pad_w, cy - pad_h, cx + pad_w, cy + pad_h


def filter_hand_card_ids(
    elements_dict: dict[int, dict[str, int]],
    *,
    screen_height: int = 1080,
    elements: list[dict[str, Any]] | None = None,
    exclude_ids: set[int] | None = None,
    min_y_ratio: float | None = None,
) -> list[int]:
    """[遗留] OmniParser 手牌 ID 过滤 — OpenCV 主路径已不用。"""
    ratio = min_y_ratio if min_y_ratio is not None else _env_float(
        "TONGITS_HAND_MIN_Y_RATIO", 0.72
    )
    min_y = int(screen_height * ratio)
    skip = exclude_ids or set()
    ids: list[int] = []
    for eid, row in sorted(elements_dict.items()):
        if eid in skip:
            continue
        if int(row.get("center_y") or 0) >= min_y:
            ids.append(eid)
    if not ids and elements:
        for row in elements:
            try:
                eid = int(row["id"])
            except (TypeError, ValueError):
                continue
            if eid in skip:
                continue
            b = row.get("bbox_xyxy_pixels") or []
            if isinstance(b, (list, tuple)) and len(b) >= 4:
                cy = (int(b[1]) + int(b[3])) // 2
            else:
                cy = int(row.get("center_y") or 0)
            if cy >= min_y:
                ids.append(eid)
    return sorted(set(ids))


def match_crop_legacy(
    crop_bgr_or_gray: Any,
    matcher: CornerTemplateMatcher | None = None,
) -> tuple[str, str, float, str, float] | None:
    """[遗留] 单裁剪区最佳模板（整牌小图）。"""
    import cv2

    m = matcher or get_corner_matcher()
    if not m._templates:
        return None
    if len(crop_bgr_or_gray.shape) == 3:
        gray = cv2.cvtColor(crop_bgr_or_gray, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop_bgr_or_gray
    h, w = gray.shape[:2]
    if h < 8 or w < 8:
        return None
    best_score = -1.0
    best: tuple[str, str, str, float] | None = None
    for stem, tmpl in m._templates.items():
        label = _parse_template_name(stem)
        if not label:
            continue
        th0, tw0 = tmpl.shape[:2]
        for scale in m.scales:
            tw = max(8, int(round(tw0 * scale)))
            th = max(8, int(round(th0 * scale)))
            tmpl_s = cv2.resize(
                tmpl, (tw, th), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
            )
            if tw <= w and th <= h:
                res = cv2.matchTemplate(gray, tmpl_s, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
            else:
                crop_n = cv2.resize(gray, (tw, th), interpolation=cv2.INTER_AREA)
                res = cv2.matchTemplate(crop_n, tmpl_s, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)
            if max_val > best_score:
                best_score = float(max_val)
                best = (label[0], label[1], stem, scale)
    if best is None or best_score < m.threshold:
        return None
    return best[0], best[1], best_score, best[2], best[3]


def main() -> int:
    import argparse
    import json as _json

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(
        description="固定 ROI + 角点模板扫掠（无需 OmniParser JSON）"
    )
    ap.add_argument("--image", required=True, help="全屏截图 *_raw.png")
    ap.add_argument("--result", help="parsed_result.json（供 ROI 锚点校准）")
    ap.add_argument(
        "--roi",
        help="覆盖 ROI: x1,y1,x2,y2",
    )
    ap.add_argument("--force-roi", action="store_true", help="忽略 roi_config 缓存")
    ap.add_argument(
        "--templates-dir",
        help="角点模板目录",
    )
    ap.add_argument("--threshold", type=float, help="匹配阈值")
    args = ap.parse_args()

    roi = None
    if args.roi:
        parts = [int(x.strip()) for x in args.roi.split(",")]
        if len(parts) == 4:
            roi = tuple(parts)  # type: ignore[assignment]

    elements_dict: dict[int, dict[str, int]] = {}
    elements: list[dict[str, Any]] = []
    if args.result:
        data = _json.loads(Path(args.result).read_text(encoding="utf-8"))
        elements = data.get("elements") or []
        for row in elements:
            try:
                eid = int(row["id"])
                c = row.get("center_xy_pixels") or [
                    row.get("center_x"),
                    row.get("center_y"),
                ]
                elements_dict[eid] = {
                    "center_x": int(c[0]),
                    "center_y": int(c[1]),
                    "content": row.get("content", ""),
                }
            except (TypeError, ValueError, KeyError, IndexError):
                continue
    if args.force_roi:
        reset_hand_roi_session()

    matcher = None
    if args.templates_dir or args.threshold is not None:
        matcher = CornerTemplateMatcher(
            templates_dir=Path(args.templates_dir)
            if args.templates_dir
            else _default_corner_templates_dir(),
            threshold=args.threshold
            if args.threshold is not None
            else _env_float("TONGITS_CORNER_MATCH_THRESHOLD", 0.94),
        )

    snapshot = recognize_table_snapshot(
        args.image,
        elements_dict or None,
        elements=elements or None,
        matcher=matcher,
        hand_roi_override=roi,
        force_recalibrate=args.force_roi,
    )
    print(_json.dumps(snapshot, ensure_ascii=False, indent=2))
    total = sum(len(snapshot[z]) for z in ZONE_KEYS)
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
