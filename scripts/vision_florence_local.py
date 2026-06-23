#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L2 感知层 — 本地 Florence-2 OCR + OpenCV HSV 花色/明暗探针。

五战区裁切 → <OCR_WITH_REGION> → 数字/字母 + 局部框 → HSV 花色与亮牌判定 → 全局坐标。
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger("vision_florence_local")

SCRIPTS_DIR = Path(__file__).resolve().parent
DEFAULT_FLORENCE_MODEL_DIR = SCRIPTS_DIR / "model" / "Florence"

TURN_SCOUT_ZONE_ORDER: tuple[str, ...] = (
    "player_hand",
    "my_melds",
    "opponent_left",
    "opponent_right",
    "center_discard",
)

LABEL_ONLY_ZONES: frozenset[str] = frozenset(
    {"my_melds", "opponent_left", "opponent_right", "center_discard"}
)

OCR_TASK_PROMPT = "<OCR_WITH_REGION>"

_VALID_RANKS = frozenset(
    {"A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"}
)
_OCR_NOISE_WORDS = (
    "drop",
    "fight",
    "group",
    "dump",
    "draw",
    "point",
    "special",
    "autosort",
    "sort",
    "victor",
    "microsoft",
    "lanco",
)
_SUIT_TEMPLATES: dict[str, np.ndarray] | None = None


def _florence_model_dir() -> Path:
    raw = (os.environ.get("TONGITS_FLORENCE_MODEL_DIR") or "").strip()
    return Path(raw) if raw else DEFAULT_FLORENCE_MODEL_DIR


def _brightness_threshold() -> int:
    try:
        return int(os.environ.get("TONGITS_FLORENCE_BRIGHT_V_MIN", "120"))
    except ValueError:
        return 120


def _brightness_threshold_for_zone(zone_key: str) -> int:
    """
    手牌区阈值可更低，减少深色牌面/阴影导致的误过滤。
    """
    base = _brightness_threshold()
    if zone_key == "player_hand":
        try:
            return int(os.environ.get("TONGITS_FLORENCE_HAND_BRIGHT_V_MIN", "72"))
        except ValueError:
            return min(base, 72)
    return base


def _florence_max_new_tokens() -> int:
    try:
        return max(96, int(os.environ.get("TONGITS_FLORENCE_MAX_NEW_TOKENS", "256")))
    except ValueError:
        return 256


def _florence_num_beams() -> int:
    try:
        return max(1, int(os.environ.get("TONGITS_FLORENCE_NUM_BEAMS", "1")))
    except ValueError:
        return 1


def _turn_zone_keys() -> tuple[str, ...]:
    """
    Florence 回合侦察战区策略：
    - full（默认）：五路全扫
    - fast：手牌 + 我方明牌 + 弃牌堆顶（兼顾时延与可出牌信息）
    - hand：仅手牌
    """
    preset = (os.environ.get("TONGITS_FLORENCE_ZONE_PRESET") or "full").strip().lower()
    if preset == "full":
        return TURN_SCOUT_ZONE_ORDER
    if preset == "hand":
        return ("player_hand",)
    return ("player_hand", "my_melds", "center_discard")


def _force_black_on_unknown_suit() -> bool:
    raw = (os.environ.get("TONGITS_FLORENCE_FORCE_BLACK_ON_UNKNOWN_SUIT") or "0").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _florence_preload_on_startup() -> bool:
    raw = (os.environ.get("TONGITS_FLORENCE_PRELOAD_ON_STARTUP") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _hand_crop_top_ratio() -> float:
    try:
        return min(0.85, max(0.0, float(os.environ.get("TONGITS_FLORENCE_HAND_CROP_TOP_RATIO", "0.40"))))
    except ValueError:
        return 0.40


def _hand_crop_bottom_ratio() -> float:
    try:
        return min(1.0, max(0.2, float(os.environ.get("TONGITS_FLORENCE_HAND_CROP_BOTTOM_RATIO", "0.90"))))
    except ValueError:
        return 0.90


def _hand_crop_side_pad_ratio() -> float:
    try:
        return min(0.20, max(0.0, float(os.environ.get("TONGITS_FLORENCE_HAND_CROP_SIDE_PAD_RATIO", "0.02"))))
    except ValueError:
        return 0.02


def _hand_ocr_tile_count() -> int:
    try:
        return min(5, max(1, int(os.environ.get("TONGITS_FLORENCE_HAND_TILES", "3"))))
    except ValueError:
        return 3


def _hand_ocr_tile_overlap_ratio() -> float:
    try:
        return min(0.45, max(0.0, float(os.environ.get("TONGITS_FLORENCE_HAND_TILE_OVERLAP_RATIO", "0.18"))))
    except ValueError:
        return 0.18


@dataclass(frozen=True)
class FlorenceOCRItem:
    rank: str
    quad: list[float]
    ocr_text: str
    confidence: float = 0.85


class FlorenceLocalEngine:
    """Florence-2 单例（OCR_WITH_REGION）。"""

    _instance: FlorenceLocalEngine | None = None

    def __init__(self) -> None:
        self._model: Any = None
        self._processor: Any = None
        self._device: str = "cpu"
        self._dtype: Any = None
        self._loaded = False

    @classmethod
    def get(cls) -> FlorenceLocalEngine:
        if cls._instance is None:
            cls._instance = FlorenceLocalEngine()
        return cls._instance

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        model_dir = _florence_model_dir()
        if not model_dir.is_dir():
            raise FileNotFoundError(
                f"Florence 模型目录不存在: {model_dir}\n"
                "请将模型放到 scripts/model/Florence 或设置 TONGITS_FLORENCE_MODEL_DIR"
            )

        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._dtype = torch.float16 if self._device == "cuda" else torch.float32

        logger.info(
            "正在加载 Florence-2（本地）→ %s | device=%s dtype=%s",
            model_dir.resolve(),
            self._device,
            self._dtype,
        )
        self._processor = AutoProcessor.from_pretrained(
            str(model_dir),
            trust_remote_code=True,
        )
        if getattr(self._processor, "tokenizer", None) is not None:
            # 避免 transformers 对 clean_up_tokenization_spaces 的未来行为告警
            self._processor.tokenizer.clean_up_tokenization_spaces = False
        self._model = AutoModelForCausalLM.from_pretrained(
            str(model_dir),
            torch_dtype=self._dtype,
            trust_remote_code=True,
            attn_implementation="sdpa" if self._device == "cuda" else "eager",
        ).to(self._device)
        self._model.eval()
        self._loaded = True
        logger.info("Florence-2 本地 OCR 就绪")

    def ocr_region(self, crop_bgr: np.ndarray) -> tuple[list[FlorenceOCRItem], float]:
        """对裁切图跑 OCR_WITH_REGION，返回扑克 rank 候选与耗时 ms。"""
        self.ensure_loaded()
        if crop_bgr is None or crop_bgr.size == 0:
            return [], 0.0

        from PIL import Image
        import torch

        pil = Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB))
        t0 = time.perf_counter()

        inputs = self._processor(
            text=OCR_TASK_PROMPT,
            images=pil,
            return_tensors="pt",
        )
        input_ids = inputs["input_ids"].to(self._device)
        pixel_values = inputs["pixel_values"].to(self._device, dtype=self._dtype)

        with torch.inference_mode():
            generated_ids = self._model.generate(
                input_ids=input_ids,
                pixel_values=pixel_values,
                max_new_tokens=_florence_max_new_tokens(),
                num_beams=_florence_num_beams(),
                do_sample=False,
            )

        generated_text = self._processor.batch_decode(
            generated_ids,
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )[0]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return self._parse_ocr_items(
            generated_text=generated_text,
            image_size=(pil.width, pil.height),
        ), elapsed_ms

    def ocr_regions(self, crops_bgr: list[np.ndarray]) -> tuple[list[list[FlorenceOCRItem]], float]:
        """
        批量 OCR：一次 generate 同时处理多战区（等价五路并行），降低总 wall time。
        """
        self.ensure_loaded()
        valid = [c for c in crops_bgr if c is not None and c.size > 0]
        if not valid:
            return [[] for _ in crops_bgr], 0.0

        from PIL import Image
        import torch

        pil_images = [Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)) for crop in crops_bgr]
        texts = [OCR_TASK_PROMPT] * len(pil_images)
        image_sizes = [(img.width, img.height) for img in pil_images]
        t0 = time.perf_counter()

        inputs = self._processor(
            text=texts,
            images=pil_images,
            return_tensors="pt",
            padding=True,
        )
        input_ids = inputs["input_ids"].to(self._device)
        pixel_values = inputs["pixel_values"].to(self._device, dtype=self._dtype)

        with torch.inference_mode():
            generated_ids = self._model.generate(
                input_ids=input_ids,
                pixel_values=pixel_values,
                max_new_tokens=_florence_max_new_tokens(),
                num_beams=_florence_num_beams(),
                do_sample=False,
            )

        generated_texts = self._processor.batch_decode(
            generated_ids,
            skip_special_tokens=False,
            clean_up_tokenization_spaces=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        out: list[list[FlorenceOCRItem]] = []
        for text, size in zip(generated_texts, image_sizes):
            out.append(self._parse_ocr_items(generated_text=text, image_size=size))
        return out, elapsed_ms

    def _parse_ocr_items(
        self,
        *,
        generated_text: str,
        image_size: tuple[int, int],
    ) -> list[FlorenceOCRItem]:
        parsed = self._processor.post_process_generation(
            generated_text,
            task=OCR_TASK_PROMPT,
            image_size=image_size,
        )
        block = parsed.get(OCR_TASK_PROMPT) or parsed.get("OCR_WITH_REGION") or {}
        quads = block.get("quad_boxes") or block.get("box_2d") or []
        labels = block.get("labels") or []

        items: list[FlorenceOCRItem] = []
        for quad, label in zip(quads, labels):
            label_text = str(label).strip()
            ranks = _parse_ranks_from_ocr(label_text)
            if not ranks:
                continue
            if not isinstance(quad, (list, tuple)) or len(quad) < 8:
                continue
            for rank, sub_quad in zip(ranks, _split_quad_for_ranks([float(x) for x in quad[:8]], len(ranks))):
                items.append(
                    FlorenceOCRItem(
                        rank=rank,
                        quad=sub_quad,
                        ocr_text=label_text,
                    )
                )
        return items


def _parse_ranks_from_ocr(text: str) -> list[str]:
    """从 OCR 文本提取点数列表；支持 '7 7 7 7' 这类连牌串。"""
    raw = (text or "").strip()
    if not raw:
        return []
    lower = raw.lower()
    if any(w in lower for w in _OCR_NOISE_WORDS):
        return []
    upper = raw.upper().replace("</S>", " ").replace("<S>", " ")
    upper = upper.replace("1O", "10").replace("IO", "10").replace("LO", "10")
    tokens = re.findall(r"(?<![A-Z0-9])(10|[2-9]|A|J|Q|K)(?![A-Z0-9])", upper)
    out = [t for t in tokens if t in _VALID_RANKS]
    return out


def _split_quad_for_ranks(quad: list[float], n: int) -> list[list[float]]:
    if n <= 1:
        return [quad]
    x1, y1, x2, y2 = _quad_bbox(quad)
    if x2 - x1 < 2:
        return [quad] * n
    seg = (x2 - x1) / float(n)
    out: list[list[float]] = []
    for i in range(n):
        lx = x1 + i * seg
        rx = x1 + (i + 1) * seg
        out.append([lx, y1, rx, y1, rx, y2, lx, y2])
    return out


def _quad_bbox(quad: list[float]) -> tuple[float, float, float, float]:
    xs = [quad[0], quad[2], quad[4], quad[6]]
    ys = [quad[1], quad[3], quad[5], quad[7]]
    return min(xs), min(ys), max(xs), max(ys)


def _global_probe_xy(
    quad: list[float],
    offset_x: int,
    offset_y: int,
    *,
    lower_bias: float = 0.65,
) -> tuple[int, int, int, int]:
    """返回全屏探针点 (gx, gy) 与框中心 (cx, cy)。"""
    x1, y1, x2, y2 = _quad_bbox(quad)
    cx = (x1 + x2) / 2.0
    cy = y1 + lower_bias * (y2 - y1)
    gx = int(round(offset_x + cx))
    gy = int(round(offset_y + cy))
    gcx = int(round(offset_x + (x1 + x2) / 2.0))
    gcy = int(round(offset_y + (y1 + y2) / 2.0))
    return gx, gy, gcx, gcy


def _sample_hsv_at(frame_bgr: np.ndarray, x: int, y: int) -> tuple[float, float, float]:
    sh, sw = frame_bgr.shape[:2]
    x = max(0, min(sw - 1, x))
    y = max(0, min(sh - 1, y))
    half = 2
    x1, x2 = max(0, x - half), min(sw, x + half + 1)
    y1, y2 = max(0, y - half), min(sh, y + half + 1)
    patch = frame_bgr[y1:y2, x1:x2]
    if patch.size == 0:
        return 0.0, 0.0, 0.0
    mean_bgr = patch.mean(axis=(0, 1))
    pixel = np.uint8([[mean_bgr]])
    hsv = cv2.cvtColor(pixel, cv2.COLOR_BGR2HSV)[0, 0]
    return float(hsv[0]), float(hsv[1]), float(hsv[2])


def _classify_suit_hsv(h: float, s: float, v: float) -> str | None:
    """Hue → 花色：绿=C 蓝=D 红=H 黑/低饱和=S。"""
    if v < 35:
        return "S"
    if s < 35:
        return "S"
    if (h <= 12 or h >= 168) and s >= 45:
        return "H"
    if 90 <= h <= 135 and s >= 35:
        return "D"
    if 35 <= h <= 90 and s >= 35:
        return "C"
    if v < 90:
        return "S"
    return None


def _is_bright_card(v: float, zone_key: str) -> bool:
    return v >= _brightness_threshold_for_zone(zone_key)


def _build_card_label(suit: str, rank: str) -> str:
    from vision_proxy_qwen import canonical_card_label

    return canonical_card_label(f"{suit}{rank}") or f"{suit}{rank}"


def _classify_suit_from_quad_patch(
    frame_bgr: np.ndarray,
    quad: list[float],
    offset_x: int,
    offset_y: int,
) -> str | None:
    """
    兜底花色判定：在牌角区域找有色/深色像素，避免探针点落白底导致整张牌被过滤。
    """
    x1f, y1f, x2f, y2f = _quad_bbox(quad)
    x1 = max(0, int(round(offset_x + x1f)))
    y1 = max(0, int(round(offset_y + y1f)))
    x2 = min(frame_bgr.shape[1], int(round(offset_x + x2f)))
    y2 = min(frame_bgr.shape[0], int(round(offset_y + y2f)))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None

    patch = frame_bgr[y1 : y1 + max(8, int((y2 - y1) * 0.6)), x1 : x1 + max(8, int((x2 - x1) * 0.45))]
    if patch.size == 0:
        return None
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0].astype(np.float32)
    s = hsv[:, :, 1].astype(np.float32)
    v = hsv[:, :, 2].astype(np.float32)

    colorful = (s >= 45) & (v >= 45)
    if np.any(colorful):
        hs = h[colorful]
        red_ratio = float(np.mean((hs <= 12) | (hs >= 168)))
        green_ratio = float(np.mean((hs >= 35) & (hs <= 90)))
        blue_ratio = float(np.mean((hs >= 90) & (hs <= 135)))
        if red_ratio >= max(green_ratio, blue_ratio):
            return "H"
        if green_ratio >= blue_ratio:
            return "C"
        return "D"

    dark = (v <= 90) & (s <= 90)
    if float(np.mean(dark)) >= 0.06:
        return "S"
    return None


def _build_suit_templates(size: int = 36) -> dict[str, np.ndarray]:
    """
    生成简化花色模板（S/H/D/C），用于二次形状确认。
    """
    global _SUIT_TEMPLATES
    if _SUIT_TEMPLATES is not None:
        return _SUIT_TEMPLATES

    def _blank() -> np.ndarray:
        return np.zeros((size, size), dtype=np.uint8)

    # Spade
    s = _blank()
    cx = size // 2
    r = max(3, size // 7)
    cv2.circle(s, (cx - r, size // 2 - r), r + 1, 255, -1)
    cv2.circle(s, (cx + r, size // 2 - r), r + 1, 255, -1)
    cv2.circle(s, (cx, size // 2 - 2 * r), r + 1, 255, -1)
    cv2.fillConvexPoly(
        s,
        np.array(
            [
                [cx - 3 * r, size // 2 - r],
                [cx + 3 * r, size // 2 - r],
                [cx, size // 2 + 2 * r],
            ],
            dtype=np.int32,
        ),
        255,
    )
    cv2.rectangle(s, (cx - r // 2, size // 2 + 2 * r - 1), (cx + r // 2, size - 4), 255, -1)

    # Heart
    h = _blank()
    cv2.circle(h, (cx - r, size // 2 - r), r + 1, 255, -1)
    cv2.circle(h, (cx + r, size // 2 - r), r + 1, 255, -1)
    cv2.fillConvexPoly(
        h,
        np.array(
            [
                [cx - 3 * r, size // 2 - r // 2],
                [cx + 3 * r, size // 2 - r // 2],
                [cx, size - 4],
            ],
            dtype=np.int32,
        ),
        255,
    )

    # Diamond
    d = _blank()
    cv2.fillConvexPoly(
        d,
        np.array(
            [
                [cx, 4],
                [size - 4, size // 2],
                [cx, size - 4],
                [4, size // 2],
            ],
            dtype=np.int32,
        ),
        255,
    )

    # Club
    c = _blank()
    cv2.circle(c, (cx, size // 2 - 2 * r), r + 1, 255, -1)
    cv2.circle(c, (cx - 2 * r, size // 2), r + 1, 255, -1)
    cv2.circle(c, (cx + 2 * r, size // 2), r + 1, 255, -1)
    cv2.rectangle(c, (cx - r // 2, size // 2), (cx + r // 2, size - 4), 255, -1)

    _SUIT_TEMPLATES = {"S": s, "H": h, "D": d, "C": c}
    return _SUIT_TEMPLATES


def _extract_suit_icon_mask(
    frame_bgr: np.ndarray,
    quad: list[float],
    offset_x: int,
    offset_y: int,
    *,
    out_size: int = 36,
) -> np.ndarray | None:
    """
    提取牌角花色图标区域并二值化，供模板匹配。
    """
    x1f, y1f, x2f, y2f = _quad_bbox(quad)
    x1 = max(0, int(round(offset_x + x1f)))
    y1 = max(0, int(round(offset_y + y1f)))
    x2 = min(frame_bgr.shape[1], int(round(offset_x + x2f)))
    y2 = min(frame_bgr.shape[0], int(round(offset_y + y2f)))
    if x2 - x1 < 10 or y2 - y1 < 10:
        return None

    w = x2 - x1
    h = y2 - y1
    # suit 图标通常位于牌角 rank 的下方区域
    sx1 = x1
    sx2 = min(frame_bgr.shape[1], x1 + int(round(w * 0.52)))
    sy1 = y1 + int(round(h * 0.22))
    sy2 = min(frame_bgr.shape[0], y1 + int(round(h * 0.92)))
    if sx2 - sx1 < 8 or sy2 - sy1 < 8:
        return None

    patch = frame_bgr[sy1:sy2, sx1:sx2]
    if patch.size == 0:
        return None

    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if float(np.mean(mask > 0)) < 0.03:
        return None
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.resize(mask, (out_size, out_size), interpolation=cv2.INTER_NEAREST)
    return mask


def _match_suit_template(mask: np.ndarray | None) -> tuple[str | None, float]:
    if mask is None:
        return None, 0.0
    templates = _build_suit_templates(mask.shape[0])
    best_label: str | None = None
    best_score = -1.0
    m = (mask > 0).astype(np.uint8)
    for label, tpl in templates.items():
        t = (tpl > 0).astype(np.uint8)
        inter = float(np.sum((m == 1) & (t == 1)))
        union = float(np.sum((m == 1) | (t == 1)))
        iou = inter / union if union > 0 else 0.0
        score = cv2.matchTemplate(mask, tpl, cv2.TM_CCOEFF_NORMED)[0, 0]
        mixed = 0.65 * float(score) + 0.35 * iou
        if mixed > best_score:
            best_score = mixed
            best_label = label
    return best_label, best_score


def _vote_suit_from_color(
    frame_bgr: np.ndarray,
    quad: list[float],
    offset_x: int,
    offset_y: int,
) -> tuple[str | None, float]:
    """
    扩大采样区域 + 多点投票（不是单点）。
    """
    x1f, y1f, x2f, y2f = _quad_bbox(quad)
    x1 = max(0, int(round(offset_x + x1f)))
    y1 = max(0, int(round(offset_y + y1f)))
    x2 = min(frame_bgr.shape[1], int(round(offset_x + x2f)))
    y2 = min(frame_bgr.shape[0], int(round(offset_y + y2f)))
    if x2 - x1 < 10 or y2 - y1 < 10:
        return None, 0.0

    w = x2 - x1
    h = y2 - y1
    rx1 = x1
    rx2 = min(frame_bgr.shape[1], x1 + int(round(w * 0.58)))
    ry1 = y1 + int(round(h * 0.10))
    ry2 = min(frame_bgr.shape[0], y1 + int(round(h * 0.92)))
    if rx2 - rx1 < 8 or ry2 - ry1 < 8:
        return None, 0.0

    roi = frame_bgr[ry1:ry2, rx1:rx2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hh = hsv[:, :, 0].astype(np.float32)
    ss = hsv[:, :, 1].astype(np.float32)
    vv = hsv[:, :, 2].astype(np.float32)

    # 粗像素分布投票
    colorful = (ss >= 42) & (vv >= 45)
    red_ratio = float(np.mean(((hh <= 12) | (hh >= 168)) & colorful))
    green_ratio = float(np.mean((hh >= 35) & (hh <= 90) & colorful))
    blue_ratio = float(np.mean((hh >= 90) & (hh <= 135) & colorful))
    dark_ratio = float(np.mean((vv <= 95) & (ss <= 95)))

    color_only_max = max(red_ratio, green_ratio, blue_ratio)
    if color_only_max >= 0.012 and color_only_max >= dark_ratio * 0.35:
        score_map = {"H": red_ratio, "C": green_ratio, "D": blue_ratio}
    else:
        score_map = {"H": red_ratio, "C": green_ratio, "D": blue_ratio, "S": dark_ratio}
    suit_px = max(score_map.items(), key=lambda kv: kv[1])[0]
    conf_px = float(score_map.get(suit_px, 0.0))

    # 网格多点分类投票
    h_roi, w_roi = roi.shape[:2]
    vote_map: dict[str, int] = {"H": 0, "C": 0, "D": 0, "S": 0}
    total_votes = 0
    for gy in (0.20, 0.38, 0.56, 0.74):
        for gx in (0.10, 0.24, 0.38, 0.52):
            px = int(round(rx1 + gx * w_roi))
            py = int(round(ry1 + gy * h_roi))
            h0, s0, v0 = _sample_hsv_at(frame_bgr, px, py)
            # 多点采样中，黑色票更容易被按钮/描边污染，收紧阈值。
            if v0 <= 80 and s0 <= 70:
                suit = "S"
            else:
                suit = _classify_suit_hsv(h0, s0, v0)
            if suit is None:
                continue
            vote_map[suit] = vote_map.get(suit, 0) + 1
            total_votes += 1
    suit_vote = max(vote_map.items(), key=lambda kv: kv[1])[0]
    conf_vote = (vote_map.get(suit_vote, 0) / total_votes) if total_votes > 0 else 0.0

    # 融合两种颜色证据
    if conf_vote >= 0.35:
        return suit_vote, max(conf_vote, conf_px)
    if conf_px >= 0.03:
        return suit_px, conf_px
    return None, max(conf_vote, conf_px)


def _vote_suit_from_rank_color(
    frame_bgr: np.ndarray,
    quad: list[float],
    offset_x: int,
    offset_y: int,
) -> tuple[str | None, float]:
    """
    用 rank 字体颜色做额外判定：红=H、绿=C、蓝=D，黑=S。
    """
    x1f, y1f, x2f, y2f = _quad_bbox(quad)
    x1 = max(0, int(round(offset_x + x1f)))
    y1 = max(0, int(round(offset_y + y1f)))
    x2 = min(frame_bgr.shape[1], int(round(offset_x + x2f)))
    y2 = min(frame_bgr.shape[0], int(round(offset_y + y2f)))
    if x2 - x1 < 10 or y2 - y1 < 10:
        return None, 0.0

    w = x2 - x1
    h = y2 - y1
    rx1 = x1
    rx2 = min(frame_bgr.shape[1], x1 + int(round(w * 0.48)))
    ry1 = y1
    ry2 = min(frame_bgr.shape[0], y1 + int(round(h * 0.55)))
    if rx2 - rx1 < 8 or ry2 - ry1 < 8:
        return None, 0.0

    patch = frame_bgr[ry1:ry2, rx1:rx2]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    hh = hsv[:, :, 0].astype(np.float32)
    ss = hsv[:, :, 1].astype(np.float32)
    vv = hsv[:, :, 2].astype(np.float32)

    colored = (ss >= 55) & (vv >= 45)
    if np.any(colored):
        red_ratio = float(np.mean(((hh <= 12) | (hh >= 168)) & colored))
        green_ratio = float(np.mean((hh >= 35) & (hh <= 90) & colored))
        blue_ratio = float(np.mean((hh >= 90) & (hh <= 140) & colored))
        color_map = {"H": red_ratio, "C": green_ratio, "D": blue_ratio}
        suit = max(color_map.items(), key=lambda kv: kv[1])[0]
        conf = float(color_map[suit])
        if conf >= 0.012:
            return suit, conf

    black_ratio = float(np.mean((vv <= 90) & (ss <= 85)))
    if black_ratio >= 0.20:
        return "S", black_ratio
    return None, black_ratio


def _classify_suit_with_two_stage(
    frame_bgr: np.ndarray,
    quad: list[float],
    offset_x: int,
    offset_y: int,
) -> str | None:
    """
    一次判定流程：
    1) 扩大区域 + 多点颜色投票
    2) 花色模板匹配二次确认
    """
    scores = _suit_score_map(frame_bgr, quad, offset_x, offset_y)

    non_spade_best = max(scores["H"], scores["C"], scores["D"])
    if non_spade_best >= 0.020:
        for suit in ("H", "C", "D"):
            if scores[suit] >= non_spade_best * 0.92:
                return suit

    best_suit = max(scores.items(), key=lambda kv: kv[1])[0]
    best_score = scores[best_suit]
    if best_score >= 0.025:
        return best_suit
    if _force_black_on_unknown_suit():
        return "S"
    return None


def _suit_score_map(
    frame_bgr: np.ndarray,
    quad: list[float],
    offset_x: int,
    offset_y: int,
) -> dict[str, float]:
    suit_color, conf_color = _vote_suit_from_color(frame_bgr, quad, offset_x, offset_y)
    suit_rank, conf_rank = _vote_suit_from_rank_color(frame_bgr, quad, offset_x, offset_y)
    mask = _extract_suit_icon_mask(frame_bgr, quad, offset_x, offset_y)
    suit_tpl, conf_tpl = _match_suit_template(mask)

    scores: dict[str, float] = {"H": 0.0, "C": 0.0, "D": 0.0, "S": 0.0}
    if suit_color:
        scores[suit_color] += max(0.0, conf_color) * 1.00
    if suit_rank:
        scores[suit_rank] += max(0.0, conf_rank) * 1.25
    if suit_tpl:
        scores[suit_tpl] += max(0.0, conf_tpl) * 0.70
    return scores


def _crop_zone_for_ocr(
    frame_bgr: np.ndarray,
    zone_key: str,
    roi: tuple[int, int, int, int],
) -> tuple[np.ndarray, tuple[int, int]]:
    from main_bot_loop import _crop_frame_roi

    crop, (ox, oy) = _crop_frame_roi(frame_bgr, roi)
    if zone_key != "player_hand" or crop is None or crop.size == 0:
        return crop, (ox, oy)

    h, w = crop.shape[:2]
    top = int(round(h * _hand_crop_top_ratio()))
    bottom = int(round(h * _hand_crop_bottom_ratio()))
    side_pad = int(round(w * _hand_crop_side_pad_ratio()))
    x1 = max(0, min(w - 1, side_pad))
    x2 = max(x1 + 1, min(w, w - side_pad))
    y1 = max(0, min(h - 1, top))
    y2 = max(y1 + 1, min(h, bottom))
    hand_crop = crop[y1:y2, x1:x2]
    return hand_crop, (ox + x1, oy + y1)


def _split_hand_crop_tiles(
    crop: np.ndarray,
    offset_x: int,
    offset_y: int,
) -> list[tuple[np.ndarray, tuple[int, int], str]]:
    """
    将手牌区横向切分为 3 片（可配置）并带重叠，降低单帧 OCR 漏检。
    """
    tiles = _hand_ocr_tile_count()
    if crop is None or crop.size == 0 or tiles <= 1:
        return [(crop, (offset_x, offset_y), "full")]
    h, w = crop.shape[:2]
    if w < 90:
        return [(crop, (offset_x, offset_y), "full")]

    overlap = int(round((w / tiles) * _hand_ocr_tile_overlap_ratio()))
    step = max(24, int(round(w / tiles)))
    out: list[tuple[np.ndarray, tuple[int, int], str]] = []
    for i in range(tiles):
        x1 = max(0, i * step - overlap)
        x2 = min(w, (i + 1) * step + overlap if i < tiles - 1 else w)
        if x2 - x1 < 24:
            continue
        part = crop[:, x1:x2]
        out.append((part, (offset_x + x1, offset_y), f"tile{i+1}"))
    return out or [(crop, (offset_x, offset_y), "full")]


def scout_zone_crop(
    frame_bgr: np.ndarray,
    zone_key: str,
    roi: tuple[int, int, int, int],
    engine: FlorenceLocalEngine,
) -> tuple[list[Any], float]:
    """
    单战区：Florence OCR + HSV → CardDetection 列表（懒加载 main_bot_loop.CardDetection）。
    """
    from main_bot_loop import CardDetection

    crop, (ox, oy) = _crop_zone_for_ocr(frame_bgr, zone_key, roi)
    items, ocr_ms = engine.ocr_region(crop)
    detections = _items_to_detections(frame_bgr, zone_key, items, ox, oy, CardDetection)
    detections.sort(key=lambda d: (d.center_y, d.center_x))
    return detections, ocr_ms


def _items_to_detections(
    frame_bgr: np.ndarray,
    zone_key: str,
    items: list[FlorenceOCRItem],
    offset_x: int,
    offset_y: int,
    card_detection_cls: Any,
) -> list[Any]:
    detections: list[Any] = []
    suit_meta: dict[int, tuple[str, dict[str, float]]] = {}
    for item in items:
        gx, gy, gcx, gcy = _global_probe_xy(item.quad, offset_x, offset_y)
        h, s, v = _sample_hsv_at(frame_bgr, gx, gy)
        if not _is_bright_card(v, zone_key):
            logger.debug(
                "[florence] 跳过暗牌 zone=%s text=%s V=%.0f @(%d,%d)",
                zone_key,
                item.ocr_text,
                v,
                gx,
                gy,
            )
            continue
        suit_scores = _suit_score_map(frame_bgr, item.quad, offset_x, offset_y)
        suit = max(suit_scores.items(), key=lambda kv: kv[1])[0]
        suit_val = suit_scores.get(suit, 0.0)
        if suit in ("H", "C", "D"):
            if suit_val < 0.020:
                suit = None
        else:
            if suit_val < 0.030:
                suit = None
        if suit is None and _force_black_on_unknown_suit():
            suit = "S"
        if suit is None:
            logger.debug(
                "[florence] 无法判定花色 zone=%s text=%s HSV=(%.0f,%.0f,%.0f)",
                zone_key,
                item.ocr_text,
                h,
                s,
                v,
            )
            continue
        label = _build_card_label(suit, item.rank)
        if zone_key in LABEL_ONLY_ZONES:
            cx, cy = 0, 0
        else:
            cx, cy = gcx, gcy
        detections.append(
            card_detection_cls(
                class_name=label,
                center_x=cx,
                center_y=cy,
                confidence=item.confidence,
                zone=zone_key,
            )
        )
        suit_meta[id(detections[-1])] = (item.rank, suit_scores)
    if zone_key == "player_hand":
        detections = _rebalance_duplicate_hand_labels(detections, suit_meta)
        detections = _dedupe_hand_nearby(detections)
    return detections


def _rebalance_duplicate_hand_labels(
    detections: list[Any],
    suit_meta: dict[int, tuple[str, dict[str, float]]],
) -> list[Any]:
    """
    同一副牌中，完全相同标签不应重复。对重复标签尝试按花色分数改写为同 rank 其他花色。
    """
    if len(detections) <= 1:
        return detections

    by_label: dict[str, list[Any]] = {}
    for d in detections:
        by_label.setdefault(str(getattr(d, "class_name", "")), []).append(d)

    used_labels: set[str] = set()
    kept: list[Any] = []
    for label, rows in by_label.items():
        rows.sort(key=lambda d: float(getattr(d, "confidence", 0.0)), reverse=True)
        keep_first = rows[0]
        kept.append(keep_first)
        used_labels.add(label)
        for extra in rows[1:]:
            rank, scores = suit_meta.get(id(extra), ("", {"H": 0.0, "C": 0.0, "D": 0.0, "S": 0.0}))
            if not rank:
                continue
            assigned = False
            for suit, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
                if score < 0.018:
                    continue
                new_label = _build_card_label(suit, rank)
                if new_label in used_labels:
                    continue
                extra = extra.__class__(
                    class_name=new_label,
                    center_x=int(getattr(extra, "center_x", 0)),
                    center_y=int(getattr(extra, "center_y", 0)),
                    confidence=float(getattr(extra, "confidence", 0.0)),
                    zone=str(getattr(extra, "zone", "player_hand")),
                )
                used_labels.add(new_label)
                kept.append(extra)
                assigned = True
                break
            if not assigned:
                # 丢弃冲突且无可信替代花色的重复项
                continue
    kept.sort(key=lambda d: (d.center_x, d.center_y))
    return kept


def _dedupe_hand_nearby(detections: list[Any]) -> list[Any]:
    """去除同一位置的重复 OCR 命中（常见于同牌文本被切成重叠框）。"""
    if len(detections) <= 1:
        return detections
    try:
        x_thr = max(4, int(os.environ.get("TONGITS_FLORENCE_HAND_DEDUP_X", "16")))
        y_thr = max(4, int(os.environ.get("TONGITS_FLORENCE_HAND_DEDUP_Y", "18")))
    except ValueError:
        x_thr, y_thr = 16, 18

    out: list[Any] = []
    for det in sorted(detections, key=lambda d: (d.center_x, d.center_y, d.class_name)):
        if any(
            abs(det.center_x - kept.center_x) <= x_thr
            and abs(det.center_y - kept.center_y) <= y_thr
            for kept in out
        ):
            # 同位置出现冲突标签时，保留置信度更高者。
            for i, kept in enumerate(out):
                if (
                    abs(det.center_x - kept.center_x) <= x_thr
                    and abs(det.center_y - kept.center_y) <= y_thr
                    and det.confidence > kept.confidence
                ):
                    out[i] = det
            continue
        out.append(det)
    return out


def _vote_merge_hand_detections(detections: list[Any]) -> list[Any]:
    """
    多分片结果按空间聚类投票：位置一致的多候选取多数标签，提升稳定性。
    """
    if len(detections) <= 1:
        return detections
    try:
        x_thr = max(8, int(os.environ.get("TONGITS_FLORENCE_HAND_VOTE_X", "24")))
        y_thr = max(8, int(os.environ.get("TONGITS_FLORENCE_HAND_VOTE_Y", "24")))
    except ValueError:
        x_thr, y_thr = 24, 24

    clusters: list[list[Any]] = []
    for det in sorted(detections, key=lambda d: (d.center_x, d.center_y)):
        placed = False
        for cluster in clusters:
            cx = int(round(sum(c.center_x for c in cluster) / len(cluster)))
            cy = int(round(sum(c.center_y for c in cluster) / len(cluster)))
            if abs(det.center_x - cx) <= x_thr and abs(det.center_y - cy) <= y_thr:
                cluster.append(det)
                placed = True
                break
        if not placed:
            clusters.append([det])

    merged: list[Any] = []
    for cluster in clusters:
        label_stat: dict[str, tuple[int, float]] = {}
        for det in cluster:
            count, score = label_stat.get(det.class_name, (0, 0.0))
            label_stat[det.class_name] = (count + 1, score + float(det.confidence))
        label = max(label_stat.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
        best = max(
            (d for d in cluster if d.class_name == label),
            key=lambda d: float(d.confidence),
        )
        cx = int(round(sum(c.center_x for c in cluster) / len(cluster)))
        cy = int(round(sum(c.center_y for c in cluster) / len(cluster)))
        merged.append(
            best.__class__(
                class_name=label,
                center_x=cx,
                center_y=cy,
                confidence=float(best.confidence),
                zone=getattr(best, "zone", "player_hand"),
            )
        )
    merged.sort(key=lambda d: (d.center_y, d.center_x))
    return _dedupe_hand_nearby(merged)


def scout_zones(
    frame_bgr: np.ndarray,
    zone_rois: dict[str, tuple[int, int, int, int]],
    zone_keys: tuple[str, ...] | list[str],
    engine: FlorenceLocalEngine | None = None,
) -> tuple[dict[str, list[Any]], float]:
    """侦察多个战区：支持批量并发推理（默认）与顺序模式。"""
    from main_bot_loop import CardDetection

    eng = engine or FlorenceLocalEngine.get()
    by_zone: dict[str, list[Any]] = {z: [] for z in TURN_SCOUT_ZONE_ORDER}
    valid: list[tuple[str, np.ndarray, tuple[int, int], str]] = []
    for zone_key in zone_keys:
        roi = zone_rois.get(zone_key)
        if roi:
            crop, offset = _crop_zone_for_ocr(frame_bgr, zone_key, roi)
            if zone_key == "player_hand":
                for part, part_offset, tag in _split_hand_crop_tiles(crop, offset[0], offset[1]):
                    valid.append((zone_key, part, part_offset, tag))
            else:
                valid.append((zone_key, crop, offset, "full"))

    if not valid:
        return by_zone, 0.0

    mode = (os.environ.get("TONGITS_FLORENCE_OCR_MODE") or "batch").strip().lower()
    if mode == "sequential" or len(valid) <= 1:
        total_ms = 0.0
        for zone_key, crop, (ox, oy), tag in valid:
            items, ms = eng.ocr_region(crop)
            dets = _items_to_detections(frame_bgr, zone_key, items, ox, oy, CardDetection)
            total_ms += ms
            by_zone[zone_key].extend(dets)
            logger.info(
                "[florence] zone=%s(%s) count=%d ocr=%.0fms labels=%s",
                zone_key,
                tag,
                len(dets),
                ms,
                [d.class_name for d in dets],
            )
        by_zone["player_hand"] = _vote_merge_hand_detections(by_zone["player_hand"])
        for z in TURN_SCOUT_ZONE_ORDER:
            by_zone[z].sort(key=lambda d: (d.center_y, d.center_x))
        return by_zone, total_ms

    crops = [item[1] for item in valid]
    zone_items, total_ms = eng.ocr_regions(crops)
    avg_ms = total_ms / max(1, len(valid))
    for (zone_key, _crop, (ox, oy), tag), items in zip(valid, zone_items):
        dets = _items_to_detections(frame_bgr, zone_key, items, ox, oy, CardDetection)
        by_zone[zone_key].extend(dets)
        logger.info(
            "[florence] zone=%s(%s) count=%d ocr~%.0fms(batch) labels=%s",
            zone_key,
            tag,
            len(dets),
            avg_ms,
            [d.class_name for d in dets],
        )
    by_zone["player_hand"] = _vote_merge_hand_detections(by_zone["player_hand"])
    for z in TURN_SCOUT_ZONE_ORDER:
        by_zone[z].sort(key=lambda d: (d.center_y, d.center_x))
    return by_zone, total_ms


def _filter_hand_outside_melds(
    hand: list[Any],
    zone_rois: dict[str, tuple[int, int, int, int]],
) -> list[Any]:
    from main_bot_loop import _filter_detections_in_roi

    melds_roi = zone_rois.get("my_melds")
    if not melds_roi or not hand:
        return hand
    kept = []
    for det in hand:
        if _filter_detections_in_roi([det], melds_roi):
            logger.info(
                "[florence] 手牌剔除明牌区框 %s @(%d,%d)",
                det.class_name,
                det.center_x,
                det.center_y,
            )
            continue
        kept.append(det)
    return kept


class FlorenceLocalScout:
    """
    L2 Scout：Florence OCR + HSV，接口与 QwenFullScreenScout 兼容。
    """

    def __init__(
        self,
        weights_path: Path | None = None,
        *,
        conf: float = 0.4,
        monitor_index: int = 1,
    ) -> None:
        from main_bot_loop import ScreenCapturer

        self.weights_path = weights_path
        self.conf = conf
        self.capturer = ScreenCapturer(monitor_index=monitor_index)
        self.capturer.warmup()
        self._engine = FlorenceLocalEngine.get()
        if _florence_preload_on_startup():
            t0 = time.perf_counter()
            self._engine.ensure_loaded()
            logger.info(
                "Florence 启动预加载完成，耗时 %.0fms",
                (time.perf_counter() - t0) * 1000.0,
            )
        logger.info(
            "FlorenceLocalScout 就绪 | conf=%.2f | 模型=%s",
            conf,
            _florence_model_dir().resolve(),
        )

    def _zone_rois(self, frame_bgr: np.ndarray, prev: Any | None = None) -> dict:
        from main_bot_loop import _load_board_zone_rois

        zone_rois = _load_board_zone_rois(frame_bgr)
        if prev and getattr(prev, "zone_rois", None):
            zone_rois = dict(prev.zone_rois)
        return zone_rois

    def warmup_model(self) -> None:
        """启动后主动热身一次，减少首回合 generate 抖动。"""
        t0 = time.perf_counter()
        self._engine.ensure_loaded()
        dummy = np.zeros((96, 256, 3), dtype=np.uint8)
        try:
            self._engine.ocr_region(dummy)
        except Exception as e:
            logger.warning("Florence 热身失败（可忽略）: %s", e)
        logger.info(
            "Florence 启动热身完成，耗时 %.0fms",
            (time.perf_counter() - t0) * 1000.0,
        )

    def _build_turn_result(
        self,
        frame_bgr: np.ndarray,
        by_zone: dict[str, list[Any]],
        zone_rois: dict,
        *,
        elapsed_ms: float,
        florence_ms: float,
        scout_mode: str,
        prev: Any | None = None,
    ) -> Any:
        from main_bot_loop import (
            TurnScoutResult,
            _validate_table_card_uniqueness,
        )

        hand = _filter_hand_outside_melds(
            by_zone.get("player_hand", []),
            zone_rois,
        )
        by_zone["player_hand"] = hand

        deck_valid, deck_issues = _validate_table_card_uniqueness(by_zone)
        if not deck_valid:
            logger.warning(
                "[florence] 一副牌约束未通过: %s",
                "; ".join(deck_issues),
            )
            new_by_zone = {z: [] for z in TURN_SCOUT_ZONE_ORDER}
            new_by_zone["player_hand"] = list(by_zone.get("player_hand", []))
            by_zone = new_by_zone
            deck_valid, deck_issues = _validate_table_card_uniqueness(by_zone)

        classified = [d for zs in by_zone.values() for d in zs]
        return TurnScoutResult(
            all_detections=classified,
            by_zone=by_zone,
            elapsed_ms=elapsed_ms,
            zone_rois=zone_rois,
            raw_detection_count=len(classified),
            yolo_ms=0.0,
            vlm_ms=florence_ms,
            scout_mode=scout_mode,
            deck_valid=deck_valid,
            deck_issues=deck_issues if deck_issues else None,
        )

    def infer_turn_frame(
        self,
        frame_bgr: np.ndarray,
        *,
        save_marked: bool | None = None,
    ) -> Any:
        from main_bot_loop import _is_round_end_win_screen, _log_win_skip_reason, TurnScoutResult

        t0 = time.perf_counter()
        zone_rois = self._zone_rois(frame_bgr)

        if _is_round_end_win_screen(frame_bgr):
            _log_win_skip_reason(frame_bgr)
            return TurnScoutResult(
                all_detections=[],
                by_zone={z: [] for z in TURN_SCOUT_ZONE_ORDER},
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                zone_rois=zone_rois,
                scout_mode="florence_local",
            )

        zone_keys = _turn_zone_keys()
        by_zone, florence_ms = scout_zones(
            frame_bgr,
            zone_rois,
            zone_keys,
            self._engine,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "[florence] OCR[%s] 合计=%.0fms wall=%.0fms 检出 %d 张",
            ",".join(zone_keys),
            florence_ms,
            elapsed_ms,
            sum(len(v) for v in by_zone.values()),
        )
        return self._build_turn_result(
            frame_bgr,
            by_zone,
            zone_rois,
            elapsed_ms=elapsed_ms,
            florence_ms=florence_ms,
            scout_mode="florence_local",
        )

    def infer_hand_only(
        self,
        frame_bgr: np.ndarray,
        prev: Any | None = None,
    ) -> Any:
        t0 = time.perf_counter()
        zone_rois = self._zone_rois(frame_bgr, prev)
        hand_roi = zone_rois.get("player_hand")
        if not hand_roi:
            return self.infer_turn_frame(frame_bgr)

        partial, florence_ms = scout_zones(
            frame_bgr,
            zone_rois,
            ("player_hand",),
            self._engine,
        )

        from main_bot_loop import TURN_SCOUT_ZONE_ORDER

        by_zone: dict[str, list[Any]] = {z: [] for z in TURN_SCOUT_ZONE_ORDER}
        if prev and getattr(prev, "by_zone", None):
            for z in TURN_SCOUT_ZONE_ORDER:
                by_zone[z] = list(prev.by_zone.get(z) or [])
        by_zone["player_hand"] = partial.get("player_hand", [])

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "[florence] 手牌快刷 OCR=%.0fms 合计=%.0fms 手牌=%d（其它区沿用上一帧）",
            florence_ms,
            elapsed_ms,
            len(by_zone["player_hand"]),
        )
        return self._build_turn_result(
            frame_bgr,
            by_zone,
            zone_rois,
            elapsed_ms=elapsed_ms,
            florence_ms=florence_ms,
            scout_mode="florence_local_hand_only",
            prev=prev,
        )

    def infer_hand_my_melds_only(
        self,
        frame_bgr: np.ndarray,
        prev: Any | None = None,
    ) -> Any:
        t0 = time.perf_counter()
        zone_rois = self._zone_rois(frame_bgr, prev)
        if not zone_rois.get("player_hand"):
            return self.infer_turn_frame(frame_bgr)

        keys: list[str] = ["player_hand"]
        if zone_rois.get("my_melds"):
            keys.append("my_melds")

        partial, florence_ms = scout_zones(
            frame_bgr,
            zone_rois,
            keys,
            self._engine,
        )

        from main_bot_loop import TURN_SCOUT_ZONE_ORDER

        by_zone: dict[str, list[Any]] = {z: [] for z in TURN_SCOUT_ZONE_ORDER}
        if prev and getattr(prev, "by_zone", None):
            for z in TURN_SCOUT_ZONE_ORDER:
                by_zone[z] = list(prev.by_zone.get(z) or [])
        by_zone["player_hand"] = partial.get("player_hand", [])
        by_zone["my_melds"] = partial.get("my_melds", [])

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "[florence] 手牌+明牌快刷 OCR=%.0fms 合计=%.0fms hand=%d my_melds=%d",
            florence_ms,
            elapsed_ms,
            len(by_zone["player_hand"]),
            len(by_zone["my_melds"]),
        )
        return self._build_turn_result(
            frame_bgr,
            by_zone,
            zone_rois,
            elapsed_ms=elapsed_ms,
            florence_ms=florence_ms,
            scout_mode="florence_local_hand_melds",
            prev=prev,
        )

    def close(self) -> None:
        self.capturer.close()
