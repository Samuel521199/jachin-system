#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
牌面模板匹配识别：从 data/templates/ 加载 rank 小图，对切片做 cv2.matchTemplate。

模板命名即牌面标签，如 4.png、10.png、K.png；缺啥补啥，随用随加。
同色多款式可用下划线别名，如 K_green.png → 识别为 K。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_TEMPLATES_DIR = ROOT / "data" / "templates"


def _canonical_label(stem: str) -> str:
    """文件名 stem 转牌面标签；K_green → K，10 保持 10。"""
    if "_" in stem:
        return stem.split("_", 1)[0]
    return stem


@dataclass
class MatchResult:
    """单张牌的识别结果（含全屏点击坐标）。"""

    card_index: int
    label: str
    score: float
    screen_cx: int
    screen_cy: int
    roi_bbox: tuple[int, int, int, int]  # ROI 内 rank 区域 (x1,y1,x2,y2)

    def format_line(self) -> str:
        return (
            f"[牌面: {self.label}] -> 屏幕绝对点击坐标: ({self.screen_cx}, {self.screen_cy}) "
            f"-> 匹配度: {self.score:.2f}"
        )


class CardRecognizer:
    """OpenCV 模板匹配牌面识别器。"""

    def __init__(
        self,
        templates_dir: Path | str = DEFAULT_TEMPLATES_DIR,
        *,
        match_threshold: float = 0.75,
    ) -> None:
        self.templates_dir = Path(templates_dir).expanduser().resolve()
        self.match_threshold = match_threshold
        self._templates: list[tuple[str, np.ndarray]] = []
        self.reload_templates()

    def reload_templates(self) -> int:
        """读取 templates_dir 下所有 .png；同 rank 可多张（K.png + K_green.png）。"""
        self._templates.clear()
        if not self.templates_dir.is_dir():
            return 0
        for path in sorted(self.templates_dir.glob("*.png")):
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if img is None or img.size == 0:
                continue
            self._templates.append((_canonical_label(path.stem), img))
        return len(self._templates)

    @property
    def template_labels(self) -> list[str]:
        return sorted({label for label, _ in self._templates})

    def _prepare_template(
        self,
        template: np.ndarray,
        crop_h: int,
        crop_w: int,
    ) -> np.ndarray | None:
        """将模板缩放到不超过 crop 尺寸，便于 matchTemplate。"""
        th, tw = template.shape[:2]
        if th <= 0 or tw <= 0 or crop_h <= 0 or crop_w <= 0:
            return None
        scale = min(crop_w / tw, crop_h / th, 1.0)
        if scale < 1.0:
            nw = max(1, int(tw * scale))
            nh = max(1, int(th * scale))
            return cv2.resize(template, (nw, nh), interpolation=cv2.INTER_AREA)
        return template

    def match_crop(self, crop_bgr: np.ndarray) -> tuple[str, float]:
        """
        对单张 rank 切片做模板匹配，返回 (label, score)。
        score < match_threshold 时 label 为 Unknown。
        """
        if crop_bgr.size == 0 or not self._templates:
            return "Unknown", 0.0

        crop_gray = (
            crop_bgr
            if crop_bgr.ndim == 2
            else cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        )
        ch, cw = crop_gray.shape[:2]

        best_label = "Unknown"
        best_score = 0.0

        for label, template in self._templates:
            tpl = self._prepare_template(template, ch, cw)
            if tpl is None:
                continue
            th, tw = tpl.shape[:2]
            if th > ch or tw > cw:
                continue
            res = cv2.matchTemplate(crop_gray, tpl, cv2.TM_CCOEFF_NORMED)
            score = float(res.max())
            if score > best_score:
                best_score = score
                best_label = label

        if best_score < self.match_threshold:
            return "Unknown", best_score
        return best_label, best_score

    def recognize_slices(
        self,
        slices: list[tuple[int, int, int, int, int, np.ndarray]],
        hand_bbox: tuple[int, int, int, int],
    ) -> list[MatchResult]:
        """
        识别 build_slices 产出的列表。
        slices 元素: (index, ax1, ax2, ay1, ay2, crop_bgr)，坐标相对 ROI。
        hand_bbox: 全图 (x1, y1, x2, y2)。
        """
        hx1, hy1, _, _ = hand_bbox
        results: list[MatchResult] = []
        for idx, ax1, ax2, ay1, ay2, crop in slices:
            label, score = self.match_crop(crop)
            screen_cx = hx1 + (ax1 + ax2) // 2
            screen_cy = hy1 + (ay1 + ay2) // 2
            results.append(
                MatchResult(
                    card_index=idx,
                    label=label,
                    score=score,
                    screen_cx=screen_cx,
                    screen_cy=screen_cy,
                    roi_bbox=(ax1, ay1, ax2, ay2),
                )
            )
        return results

    def print_report(self, results: list[MatchResult], *, prefix: str = "") -> None:
        if not self._templates:
            print(
                f"{prefix}[recognize] 模板库为空: {self.templates_dir} "
                f"(请放入 4.png、K.png 等 rank 小图)",
                file=sys.stderr,
            )
            return
        print(f"\n{prefix}=== 模板匹配识别 (templates={len(self._templates)}) ===")
        for r in results:
            print(f"{prefix}  card_{r.card_index:2d}: {r.format_line()}")
