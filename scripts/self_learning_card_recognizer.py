#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自生长视觉记忆库 — OpenCV 极速命中 + VLM 认知入库。

目录：scripts/card_memory/（可通过 TONGITS_CARD_MEMORY_DIR 覆盖）
  - 每张牌一个模板，如 H9.png、S7.png

环境变量：
  TONGITS_MEMORY_MATCH_THRESHOLD  OpenCV 命中阈值，默认 0.85
  TONGITS_VLM_ON_MISS             未命中时是否调 VLM，默认 1
  TONGITS_COLOR_LOG               彩色日志，默认 1
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fast_card_recognizer import (
    CardTemplateMatcher,
    _element_bbox,
    _env_float,
    _env_int,
    _resolve_screenshot_path,
    cards_to_engine_json,
    filter_hand_card_ids,
)
from vision_proxy_qwen import (
    analyze_single_card_with_qwen,
    default_vlm_model,
)

logger = logging.getLogger("self_learning_cards")

_LABEL_FILE_RE = re.compile(
    r"^([SHCD])(A|[2-9]|10|J|Q|K)$",
    re.IGNORECASE,
)

_RECOGNIZER: "SelfLearningCardRecognizer | None" = None


def _memory_dir() -> Path:
    raw = (os.environ.get("TONGITS_CARD_MEMORY_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent / "card_memory"


def _color_log() -> bool:
    return (os.environ.get("TONGITS_COLOR_LOG") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _log_hit(msg: str) -> None:
    if _color_log():
        logger.info("\033[32m%s\033[0m", msg)
    else:
        logger.info(msg)


def _log_learn(msg: str) -> None:
    if _color_log():
        logger.info("\033[33m%s\033[0m", msg)
    else:
        logger.info(msg)


def _vlm_on_miss() -> bool:
    return (os.environ.get("TONGITS_VLM_ON_MISS") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _parse_label_from_stem(stem: str) -> tuple[str, str, str] | None:
    m = _LABEL_FILE_RE.match(stem.strip().upper())
    if not m:
        return None
    suit, rank = m.group(1).upper(), m.group(2).upper()
    return f"{suit}{rank}", suit, rank


@dataclass
class SelfLearningCardRecognizer:
    """
    自学习识牌引擎：内存 OpenCV 模板 + VLM Fallback 自动入库。
    """

    memory_dir: Path
    match_threshold: float = 0.85
    vlm_model: str = "qwen-vl-max"
    scales: tuple[float, ...] = (0.85, 1.0, 1.15)
    _matcher: CardTemplateMatcher = field(init=False, repr=False)
    stats: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._reload_memory()

    def _reload_memory(self) -> None:
        self._matcher = CardTemplateMatcher(
            templates_dir=self.memory_dir,
            threshold=self.match_threshold,
            scales=self.scales,
        )
        logger.info(
            "[memory] 已加载 %d 张记忆模板 from %s",
            self._matcher.template_count,
            self.memory_dir,
        )

    @property
    def template_count(self) -> int:
        return self._matcher.template_count

    def _atomic_save_png(self, crop_bgr: Any, label: str) -> Path:
        """原子写入 card_memory/{label}.png，避免半文件。"""
        import cv2

        dest = self.memory_dir / f"{label.upper()}.png"
        if dest.is_file() and dest.stat().st_size > 100:
            return dest

        fd, tmp = tempfile.mkstemp(suffix=".png", dir=str(self.memory_dir))
        os.close(fd)
        tmp_path = Path(tmp)
        try:
            if not cv2.imwrite(str(tmp_path), crop_bgr):
                raise OSError(f"cv2.imwrite 失败: {tmp_path}")
            tmp_path.replace(dest)
        except Exception:
            if tmp_path.is_file():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise
        return dest

    def _register_template_file(self, png_path: Path, label: str) -> None:
        """将新 PNG 注册进内存 matcher（无需重启进程）。"""
        import cv2

        img = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            logger.warning("[memory] 无法读取入库文件: %s", png_path)
            return
        stem = label.upper()
        self._matcher._templates[stem] = img
        parsed = _parse_label_from_stem(stem)
        if parsed:
            self._matcher._label_index[(parsed[1], parsed[2])] = stem

    def match_crop_cached(self, crop_bgr: Any) -> tuple[str, str, str, float] | None:
        """OpenCV 记忆库检索。返回 (label, suit, rank, score) 或 None。"""
        hit = self._matcher.match_crop(crop_bgr)
        if hit is None:
            return None
        suit, rank, score, stem, _scale = hit
        label = f"{suit}{rank}"
        return label, suit, rank, score

    def learn_from_crop(
        self,
        crop_bgr: Any,
        *,
        element_id: int | None = None,
    ) -> tuple[str, str, str] | None:
        """
        VLM 认知单张 crop 并入库。失败返回 None，不抛异常。
        """
        import cv2

        if not _vlm_on_miss():
            logger.warning("[memory] VLM_ON_MISS=0，跳过认知 id=%s", element_id)
            return None

        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        tmp_path = Path(tmp)
        try:
            if not cv2.imwrite(str(tmp_path), crop_bgr):
                return None
            parsed = analyze_single_card_with_qwen(
                str(tmp_path),
                model=self.vlm_model,
            )
            if not parsed:
                return None
            label, suit, rank = parsed
            saved = self._atomic_save_png(crop_bgr, label)
            self._register_template_file(saved, label)
            self.stats["learned"] = self.stats.get("learned", 0) + 1
            _log_learn(f"[认知学习] 发现新牌，VLM 识别并入库为: {label}.png")
            return label, suit, rank
        except Exception as e:
            logger.error(
                "[memory] VLM 认知失败 id=%s: %s（流水线继续）",
                element_id,
                e,
            )
            return None
        finally:
            if tmp_path.is_file():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def recognize_one(
        self,
        crop_bgr: Any,
        *,
        element_id: int,
    ) -> dict[str, Any] | None:
        """单张手牌：Cache Hit → 返回；Miss → VLM 学习。"""
        hit = self.match_crop_cached(crop_bgr)
        if hit is not None:
            label, suit, rank, score = hit
            self.stats["hit"] = self.stats.get("hit", 0) + 1
            _log_hit(f"[极速命中] OpenCV 识别出 {label} (id={element_id}, score={score:.3f})")
            return {
                "id": element_id,
                "suit": suit,
                "rank": rank,
                "score": round(score, 4),
                "source": "memory",
                "label": label,
            }

        self.stats["miss"] = self.stats.get("miss", 0) + 1
        logger.info("[memory] Cache Miss id=%s，启动 VLM 认知 …", element_id)
        learned = self.learn_from_crop(crop_bgr, element_id=element_id)
        if learned is None:
            return None
        label, suit, rank = learned
        return {
            "id": element_id,
            "suit": suit,
            "rank": rank,
            "score": 0.0,
            "source": "vlm_learned",
            "label": label,
        }

    def recognize_cards(
        self,
        screenshot_path: str,
        elements_dict: dict[int, dict[str, int]],
        *,
        elements: list[dict[str, Any]] | None = None,
        hand_card_ids: list[int] | None = None,
        exclude_ids: set[int] | None = None,
        screen_height: int = 1080,
    ) -> list[dict[str, Any]]:
        """
        与 fast_card_recognizer.recognize_cards 相同接口，供 Tongits 流水线调用。
        """
        import cv2

        t0 = time.perf_counter()
        img_path = _resolve_screenshot_path(screenshot_path)
        screen = cv2.imread(str(img_path))
        if screen is None:
            raise FileNotFoundError(f"无法读取截图: {img_path}")

        sh, sw = screen.shape[:2]
        pad_w = _env_int("TONGITS_CROP_PAD_W", 28)
        pad_h = _env_int("TONGITS_CROP_PAD_H", 40)

        ids = hand_card_ids or filter_hand_card_ids(
            elements_dict,
            screen_height=screen_height or sh,
            elements=elements,
            exclude_ids=exclude_ids,
        )
        logger.info("[memory] 手牌候选 ID=%s，记忆库 %d 张", ids, self.template_count)

        out: list[dict[str, Any]] = []
        for eid in ids:
            bbox = _element_bbox(eid, elements_dict, elements, pad_w=pad_w, pad_h=pad_h)
            if not bbox:
                continue
            x1, y1, x2, y2 = bbox
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(sw, max(x1 + 1, x2)), min(sh, max(y1 + 1, y2))
            crop = screen[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            row = self.recognize_one(crop, element_id=eid)
            if row:
                out.append(row)

        ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "[memory] 完成 %d/%d 张 | hit=%s miss=%s learned=%s (%.1f ms)",
            len(out),
            len(ids),
            self.stats.get("hit", 0),
            self.stats.get("miss", 0),
            self.stats.get("learned", 0),
            ms,
        )
        return out


def get_self_learning_recognizer(
    memory_dir: str | Path | None = None,
    *,
    threshold: float | None = None,
) -> SelfLearningCardRecognizer:
    global _RECOGNIZER
    if _RECOGNIZER is None:
        scales_raw = (os.environ.get("TONGITS_MATCH_SCALES") or "0.85,1.0,1.15").strip()
        scales = tuple(float(s.strip()) for s in scales_raw.split(",") if s.strip())
        _RECOGNIZER = SelfLearningCardRecognizer(
            memory_dir=Path(memory_dir) if memory_dir else _memory_dir(),
            match_threshold=threshold
            if threshold is not None
            else _env_float("TONGITS_MEMORY_MATCH_THRESHOLD", 0.85),
            vlm_model=default_vlm_model(),
            scales=scales or (0.85, 1.0, 1.15),
        )
    return _RECOGNIZER


def recognize_cards(
    screenshot_path: str,
    elements_dict: dict[int, dict[str, int]],
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """模块级入口，与 fast_card_recognizer.recognize_cards 对齐。"""
    return get_self_learning_recognizer().recognize_cards(
        screenshot_path,
        elements_dict,
        **kwargs,
    )
