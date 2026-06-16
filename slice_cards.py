#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手牌区切片：手牌 ROI 定位 + OpenCV 白牌边缘检测 + 轮廓紧裁 rank 角标

支持：
  - 手牌张数自动检测（默认不写死 13；可用 --num-cards 强制）
  - 多候选框时按「屏幕下半部」过滤（玩家手牌通常在底部）
  - 无 bbox 时用 OpenCV 在画面底部自动找手牌横条（--auto-hand）

用法::

  # 旧方式：手动 bbox
  python slice_cards.py --image data/my_game_screenshot.png --bbox 396 729 1517 928 --ocr

  # 自动找底部手牌区 + 自动数张数
  python slice_cards.py --image data/hard_example.jpg --auto-hand --ocr

  # Florence 返回多个框：传入候选，脚本按 Y 过滤后取最宽的一条
  python slice_cards.py --image shot.jpg \\
    --candidate-bbox 120 400 1800 900 --candidate-bbox 396 729 1517 928 --ocr

  # 批量：切割 florence2_test_out/图片 下所有截图（自动读 *_report.json）
  python slice_cards.py --input-dir data/florence2_test_out/图片 --auto-hand --ocr
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
DEFAULT_IMG = ROOT / "data" / "my_game_screenshot.png"
DEFAULT_OUT = ROOT / "data" / "florence2_test_out" / "sliced_cards"
DEFAULT_INPUT_DIR = ROOT / "data" / "florence2_test_out" / "图片"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class SliceOptions:
    bbox: tuple[int, int, int, int] | None = None
    candidate_bbox: list[tuple[int, int, int, int]] = field(default_factory=list)
    auto_hand: bool = False
    y_min_ratio: float = 0.55
    y_min: int = 0
    num_cards: int = 0
    min_gap: int = 72
    bottom_scan_ratio: float = 0.42
    ocr: bool = False

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> SliceOptions:
        return cls(
            bbox=tuple(args.bbox) if args.bbox is not None else None,
            candidate_bbox=[tuple(b) for b in (args.candidate_bbox or [])],
            auto_hand=args.auto_hand,
            y_min_ratio=args.y_min_ratio,
            y_min=args.y_min,
            num_cards=args.num_cards,
            min_gap=args.min_gap,
            bottom_scan_ratio=args.bottom_scan_ratio,
            ocr=args.ocr,
        )


@dataclass
class BatchTask:
    name: str
    image_path: Path
    candidate_bbox: list[tuple[int, int, int, int]] = field(default_factory=list)
    report_path: Path | None = None


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="手牌 ROI：Y 过滤 / 自动定位 + 边缘切牌")
    ap.add_argument("--image", type=Path, default=None, help="单张原图（与 --input-dir 二选一）")
    ap.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help=f"批量目录，默认示例: {DEFAULT_INPUT_DIR.relative_to(ROOT)}",
    )
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument(
        "--bbox",
        type=int,
        nargs=4,
        metavar=("X1", "Y1", "X2", "Y2"),
        default=None,
        help="单一手牌 ROI（与 --auto-hand / --candidate-bbox 互斥时优先）",
    )
    ap.add_argument(
        "--candidate-bbox",
        type=int,
        nargs=4,
        action="append",
        metavar=("X1", "Y1", "X2", "Y2"),
        default=None,
        help="可重复；Florence 等多个候选框，按 Y 过滤后选最宽",
    )
    ap.add_argument(
        "--auto-hand",
        action="store_true",
        help="不用 bbox：在画面底部 OpenCV 自动检测手牌横条",
    )
    ap.add_argument(
        "--y-min-ratio",
        type=float,
        default=0.55,
        help="候选框中心 cy 须 >= 屏高×该比例（默认 0.55≈下半屏）",
    )
    ap.add_argument(
        "--y-min",
        type=int,
        default=0,
        help="绝对像素 cy 下限；>0 时覆盖 --y-min-ratio",
    )
    ap.add_argument(
        "--num-cards",
        type=int,
        default=0,
        help="强制张数；0=边缘检测自动数牌（默认）",
    )
    ap.add_argument("--min-gap", type=int, default=72, help="相邻牌左缘最小间距(px)")
    ap.add_argument(
        "--bottom-scan-ratio",
        type=float,
        default=0.42,
        help="--auto-hand 时在画面底部该比例高度内扫描",
    )
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument(
        "--no-from-report",
        action="store_true",
        help="批量时忽略同目录 *_report.json 中的原图路径与候选框",
    )
    return ap.parse_args()


def _bbox_center_y(b: tuple[int, int, int, int]) -> float:
    return (b[1] + b[3]) / 2.0


def _bbox_width(b: tuple[int, int, int, int]) -> int:
    return max(0, b[2] - b[0])


def _bbox_area(b: tuple[int, int, int, int]) -> int:
    return _bbox_width(b) * max(0, b[3] - b[1])


def filter_bboxes_bottom(
    boxes: list[tuple[int, int, int, int]],
    img_h: int,
    *,
    y_min_ratio: float,
    y_min_abs: int,
) -> list[tuple[int, int, int, int]]:
    """空间规则：玩家手牌在屏幕下方，丢弃桌心/上方候选框。"""
    threshold = float(y_min_abs) if y_min_abs > 0 else img_h * y_min_ratio
    kept = [b for b in boxes if _bbox_center_y(b) >= threshold]
    return kept


def reject_fullscreen_boxes(
    boxes: list[tuple[int, int, int, int]],
    img_w: int,
    img_h: int,
) -> list[tuple[int, int, int, int]]:
    """去掉接近整屏的 Florence 假框。"""
    out: list[tuple[int, int, int, int]] = []
    for b in boxes:
        if _bbox_width(b) > img_w * 0.92 and (b[3] - b[1]) > img_h * 0.85:
            continue
        out.append(b)
    return out


def merge_bboxes(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes)
    y2 = max(b[3] for b in boxes)
    return (x1, y1, x2, y2)


def pick_hand_bbox(
    candidates: list[tuple[int, int, int, int]],
    *,
    img_w: int = 0,
) -> tuple[int, int, int, int] | None:
    """下半部候选里：优先最宽；多个窄框则合并为整条手牌带。"""
    if not candidates:
        return None
    widest = max(_bbox_width(b) for b in candidates)
    if len(candidates) > 1 and img_w > 0 and widest < img_w * 0.65:
        return merge_bboxes(candidates)
    return max(
        candidates,
        key=lambda b: (_bbox_width(b), _bbox_center_y(b)),
    )


def detect_hand_roi_auto(
    img_bgr: np.ndarray,
    *,
    bottom_scan_ratio: float = 0.42,
) -> tuple[int, int, int, int] | None:
    """
    无 Florence 时：在画面底部扫描白色牌面横条，返回全图坐标 bbox。
    """
    img_h, img_w = img_bgr.shape[:2]
    y0 = max(0, int(img_h * (1.0 - bottom_scan_ratio)))
    strip = img_bgr[y0:img_h, :]
    if strip.size == 0:
        return None

    sh, sw = strip.shape[:2]
    gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 200, 255, cv2.THRESH_BINARY)

    row_white = (mask > 0).mean(axis=1)
    ys = np.where(row_white > 0.25)[0]
    if ys.size == 0:
        return None
    y1s, y2s = int(ys[0]), int(ys[-1]) + 1

    band = mask[y1s:y2s, :]
    col_white = (band > 0).mean(axis=0)
    xs = np.where(col_white > 0.15)[0]
    if xs.size == 0:
        return None
    x1s, x2s = int(xs[0]), int(xs[-1]) + 1

    pad = 4
    x1 = max(0, x1s - pad)
    x2 = min(sw, x2s + pad)
    y1 = max(0, y0 + y1s - pad)
    y2 = min(img_h, y0 + y2s + pad)
    if x2 - x1 < 80 or y2 - y1 < 20:
        return None
    return (x1, y1, x2, y2)


def resolve_hand_bbox(
    img_bgr: np.ndarray,
    opts: SliceOptions,
) -> tuple[tuple[int, int, int, int], str]:
    """返回 ((x1,y1,x2,y2), 来源说明)。"""
    img_h, img_w = img_bgr.shape[:2]
    candidates: list[tuple[int, int, int, int]] = []

    if opts.bbox is not None:
        candidates = [opts.bbox]
        source = "manual --bbox"
    elif opts.candidate_bbox:
        candidates = list(opts.candidate_bbox)
        source = "candidate-bbox"
    elif opts.auto_hand:
        auto = detect_hand_roi_auto(img_bgr, bottom_scan_ratio=opts.bottom_scan_ratio)
        if auto is None:
            raise RuntimeError("--auto-hand 未在画面底部找到手牌白区")
        return auto, "opencv auto-hand"
    else:
        auto = detect_hand_roi_auto(img_bgr, bottom_scan_ratio=opts.bottom_scan_ratio)
        if auto is not None:
            return auto, "opencv auto-hand (default)"
        raise RuntimeError(
            "请指定 --bbox、--candidate-bbox 或 --auto-hand 之一以定位手牌区"
        )

    candidates = reject_fullscreen_boxes(candidates, img_w, img_h)
    filtered = filter_bboxes_bottom(
        candidates,
        img_h,
        y_min_ratio=opts.y_min_ratio,
        y_min_abs=opts.y_min,
    )
    if not filtered:
        raise RuntimeError(
            f"候选框经 Y 过滤后为空（cy 须 >= {opts.y_min or img_h * opts.y_min_ratio:.0f}）"
        )
    picked = pick_hand_bbox(filtered, img_w=img_w)
    if picked is None:
        raise RuntimeError("未能从候选框中选手牌 ROI")
    if len(filtered) > 1 and picked == merge_bboxes(filtered):
        source = f"{source}+merge"
    return picked, source


def _bbox_to_int_tuple(raw: list[float] | tuple[float, ...]) -> tuple[int, int, int, int]:
    return tuple(int(round(v)) for v in raw[:4])  # type: ignore[return-value]


def _resolve_image_path(raw: str, *, base_dir: Path) -> Path | None:
    p = Path(raw)
    if p.is_file():
        return p.resolve()
    for candidate in (base_dir / p.name, ROOT / p, base_dir / p):
        if candidate.is_file():
            return candidate.resolve()
    return None


def bboxes_from_florence_report(report_path: Path) -> tuple[Path | None, list[tuple[int, int, int, int]]]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    img_raw = str(data.get("image") or "")
    img_path = _resolve_image_path(img_raw, base_dir=report_path.parent) if img_raw else None

    seen: set[tuple[int, int, int, int]] = set()
    boxes: list[tuple[int, int, int, int]] = []
    for entry in data.get("phrase_grounding") or []:
        for hit in entry.get("hits") or []:
            bbox = hit.get("bbox")
            if not bbox or len(bbox) < 4:
                continue
            ib = _bbox_to_int_tuple(bbox)
            if ib in seen:
                continue
            seen.add(ib)
            boxes.append(ib)
    return img_path, boxes


def _should_skip_batch_image(path: Path) -> bool:
    name = path.name.lower()
    stem = path.stem.lower()
    if stem.endswith("_annotated") or "_annotated." in name:
        return True
    if stem.startswith("card_") or name.startswith("_"):
        return True
    return False


def collect_batch_tasks(input_dir: Path, *, use_report: bool) -> list[BatchTask]:
    input_dir = input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"输入目录不存在: {input_dir}")

    tasks: list[BatchTask] = []
    seen_images: set[Path] = set()

    if use_report:
        for report_path in sorted(input_dir.glob("*_report.json")):
            img_path, boxes = bboxes_from_florence_report(report_path)
            if img_path is None or not img_path.is_file():
                print(f"[WARN] 跳过 report（找不到原图）: {report_path.name}", file=sys.stderr)
                continue
            name = report_path.stem[: -len("_report")] if report_path.stem.endswith("_report") else report_path.stem
            tasks.append(
                BatchTask(
                    name=name,
                    image_path=img_path,
                    candidate_bbox=boxes,
                    report_path=report_path,
                )
            )
            seen_images.add(img_path.resolve())

    for img_path in sorted(input_dir.iterdir()):
        if img_path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if _should_skip_batch_image(img_path):
            continue
        resolved = img_path.resolve()
        if resolved in seen_images:
            continue
        tasks.append(BatchTask(name=img_path.stem, image_path=resolved))

    return tasks


def _white_column_rising_edges(roi_bgr: np.ndarray, *, min_gap: int) -> list[int]:
    h, w = roi_bgr.shape[:2]
    band = roi_bgr[0 : max(32, int(h * 0.55)), :]
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 208, 255, cv2.THRESH_BINARY)
    col = (mask > 0).mean(axis=0)

    raw: list[int] = []
    for i in range(1, w):
        rise = (col[i - 1] < 0.38 and col[i] >= 0.55) or (
            col[i - 1] < 0.52 and col[i] >= 0.72
        )
        if not rise:
            continue
        if not raw or i - raw[-1] >= min_gap:
            raw.append(i)
    return raw


def _merge_small_gaps(left: list[int], w: int, min_gap: int) -> list[int]:
    """去掉间距过小的冗余左缘（牌内噪声）。"""
    if len(left) <= 1:
        return left
    merged = [left[0]]
    for x in left[1:]:
        if x - merged[-1] < min_gap // 2:
            continue
        merged.append(x)
    # 末段过窄则去掉最后一个左缘（避免空切）
    while len(merged) > 1 and w - merged[-1] < min_gap // 3:
        merged.pop()
    return merged


def detect_card_left_edges(
    roi_bgr: np.ndarray,
    *,
    min_gap: int,
    num_cards: int = 0,
) -> tuple[list[int], str, int]:
    """
    自动数牌：返回 (左缘列表, 方法名, 检测张数)。
    num_cards>0 时强制截断/补齐到该张数。
    """
    h, w = roi_bgr.shape[:2]
    transitions = _white_column_rising_edges(roi_bgr, min_gap=min_gap)

    left: list[int] = [0]
    for t in transitions:
        if t - left[-1] >= min_gap:
            left.append(t)

    left = _merge_small_gaps(left, w, min_gap)
    method = "column_threshold_auto"

    if num_cards > 0:
        method = "column_threshold_fixed"
        while len(left) < num_cards:
            med = int(np.median(np.diff(left))) if len(left) > 1 else max(min_gap, w // max(num_cards, 1))
            left.append(min(w - 1, left[-1] + med))
        if len(left) > num_cards:
            while len(left) > num_cards:
                gaps = [(left[i + 1] - left[i], i) for i in range(len(left) - 1)]
                _, idx = min(gaps, key=lambda t: t[0])
                left.pop(idx + 1)
        left = left[:num_cards]

    n_cards = len(left)
    return left, method, n_cards


def _rank_crop_contour(
    roi_bgr: np.ndarray,
    x1: int,
    x2: int,
    *,
    rank_h_ratio: float = 0.58,
    min_rank_w: int = 36,
    max_rank_w: int = 72,
) -> tuple[np.ndarray, int, int, int, int]:
    h, _w = roi_bgr.shape[:2]
    rank_h = max(28, int(h * rank_h_ratio))
    seg_w = max(1, x2 - x1)
    sub = roi_bgr[0:rank_h, x1:x2]
    if sub.size == 0:
        return sub, x1, x2, 0, rank_h

    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 198, 255, cv2.THRESH_BINARY)
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    if not cnts:
        rw = min(max_rank_w, seg_w)
        return sub[:, :rw], x1, x1 + rw, 0, rank_h

    c = max(cnts, key=cv2.contourArea)
    bx, by, bw, bh = cv2.boundingRect(c)
    if bw * bh < 80 or bw < min_rank_w // 2:
        rw = min(max_rank_w, max(min_rank_w, seg_w * 3 // 4))
        return sub[:, :rw], x1, x1 + rw, 0, rank_h

    pad = 3
    xa = max(0, bx - pad)
    ya = max(0, by - pad)
    xb = min(sub.shape[1], bx + bw + pad)
    yb = min(sub.shape[0], by + bh + pad)
    return sub[ya:yb, xa:xb], x1 + xa, x1 + xb, ya, yb


def build_slices(
    roi_bgr: np.ndarray,
    left_edges: list[int],
) -> list[tuple[int, int, int, int, int, np.ndarray]]:
    h, w = roi_bgr.shape[:2]
    out: list[tuple[int, int, int, int, int, np.ndarray]] = []
    for i, x1 in enumerate(left_edges):
        x2 = w if i == len(left_edges) - 1 else left_edges[i + 1]
        if x1 >= w or x2 <= x1:
            continue
        crop, ax1, ax2, ay1, ay2 = _rank_crop_contour(roi_bgr, x1, x2)
        out.append((i + 1, ax1, ax2, ay1, ay2, crop))
    return out


def _draw_full_debug(
    img_bgr: np.ndarray,
    hand_bbox: tuple[int, int, int, int],
    roi_bgr: np.ndarray,
    left_edges: list[int],
    slices,
    *,
    method: str,
    roi_source: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """全图标注 + ROI 内切分调试图。"""
    x1, y1, x2, y2 = hand_bbox
    full = img_bgr.copy()
    cv2.rectangle(full, (x1, y1), (x2 - 1, y2 - 1), (0, 255, 0), 2)
    cv2.putText(
        full,
        f"hand ROI ({roi_source})",
        (x1, max(14, y1 - 6)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (0, 255, 0),
        1,
    )
    y_thr = int(img_bgr.shape[0] * 0.55)
    cv2.line(full, (0, y_thr), (img_bgr.shape[1] - 1, y_thr), (255, 128, 0), 1)

    h, w = roi_bgr.shape[:2]
    vis = roi_bgr.copy()
    for i, x in enumerate(left_edges):
        cv2.line(vis, (x, 0), (x, h - 1), (0, 255, 0), 1)
        cv2.putText(vis, str(i + 1), (x + 2, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
    for idx, ax1, ax2, ay1, ay2, _ in slices:
        cv2.rectangle(vis, (ax1, ay1), (max(ax1, ax2 - 1), max(ay1, ay2 - 1)), (0, 180, 255), 1)
    cv2.putText(vis, f"{method} n={len(left_edges)}", (4, h - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

    band = roi_bgr[0 : max(32, int(h * 0.55)), :]
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0), 208, 255, cv2.THRESH_BINARY)
    mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    return full, vis, mask_bgr


def process_one_image(
    img_path: Path,
    out_dir: Path,
    opts: SliceOptions,
    *,
    label: str = "",
) -> int:
    img_path = img_path.expanduser().resolve()
    if not img_path.is_file():
        print(f"[ERROR] 图片不存在: {img_path}", file=sys.stderr)
        return 2

    img = cv2.imread(str(img_path))
    if img is None:
        print(f"[ERROR] cv2.imread 失败: {img_path}", file=sys.stderr)
        return 2

    img_h, img_w = img.shape[:2]
    t0 = time.perf_counter()
    prefix = f"[{label}] " if label else ""

    try:
        hand_bbox, roi_source = resolve_hand_bbox(img, opts)
    except RuntimeError as e:
        print(f"{prefix}[ERROR] {e}", file=sys.stderr)
        return 2

    x1, y1, x2, y2 = hand_bbox
    roi = img[y1:y2, x1:x2]
    if roi.size == 0:
        print(f"{prefix}[ERROR] 手牌 ROI 无效", file=sys.stderr)
        return 2

    h, w = roi.shape[:2]
    left_edges, method, n_detected = detect_card_left_edges(
        roi,
        min_gap=opts.min_gap,
        num_cards=opts.num_cards,
    )
    intervals = [
        (left_edges[i + 1] if i + 1 < len(left_edges) else w) - left_edges[i]
        for i in range(len(left_edges))
    ]
    slices = build_slices(roi, left_edges)

    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / "_hand_roi.png"), roi)

    full_dbg, slice_dbg, mask_vis = _draw_full_debug(
        img, hand_bbox, roi, left_edges, slices, method=method, roi_source=roi_source
    )
    cv2.imwrite(str(out_dir / "_full_debug.png"), full_dbg)
    cv2.imwrite(str(out_dir / "_slice_debug.png"), slice_dbg)
    cv2.imwrite(str(out_dir / "_white_mask.png"), mask_vis)

    y_thr = opts.y_min if opts.y_min > 0 else int(img_h * opts.y_min_ratio)
    print(f"{prefix}原图: {img_path.name} ({img_w}x{img_h})  手牌 ROI: {hand_bbox}  来源: {roi_source}")
    print(f"{prefix}Y 过滤线 cy>={y_thr}  ROI 内: {w}x{h}  检测: {method}  张数: {n_detected}")
    print(f"{prefix}左缘 px: {left_edges}")
    print(f"{prefix}段宽 px: {intervals}")

    for idx, ax1, ax2, ay1, ay2, crop in slices:
        path = out_dir / f"card_{idx}.png"
        cv2.imwrite(str(path), crop)
        ch, cw = (crop.shape[0], crop.shape[1]) if crop.size else (0, 0)
        seg_x1 = left_edges[idx - 1]
        seg_w = intervals[idx - 1]
        print(f"{prefix}  card_{idx:2d}: seg=[{seg_x1},{seg_x1 + seg_w}) crop={cw}x{ch} -> {path.name}")

    elapsed_ms = (time.perf_counter() - t0) * 1000
    print(f"{prefix}完成 {len(slices)} 张 -> {out_dir}  ({elapsed_ms:.1f} ms)")
    print(f"{prefix}对照: _full_debug.png / _slice_debug.png")

    if opts.ocr:
        _try_ocr(slices, prefix=prefix)
    return 0


def _try_ocr(slices, *, prefix: str = "") -> None:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print("[ocr] 跳过：pip install rapidocr-onnxruntime", file=sys.stderr)
        return
    ocr = RapidOCR()
    print(f"\n{prefix}=== RapidOCR 试读 ===")
    for idx, _, _, _, _, crop in slices:
        result, _ = ocr(crop)
        text = " | ".join(str(row[1]) for row in (result or [])) if result else "(empty)"
        print(f"{prefix}  card_{idx:2d}: {text}")


def run_batch(input_dir: Path, out_root: Path, base_opts: SliceOptions, *, use_report: bool) -> int:
    try:
        tasks = collect_batch_tasks(input_dir, use_report=use_report)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    if not tasks:
        print(f"[ERROR] 目录内没有可切割的图片: {input_dir}", file=sys.stderr)
        return 2

    print(f"批量切割: {input_dir}  共 {len(tasks)} 项  输出根目录: {out_root}")
    rc = 0
    for i, task in enumerate(tasks, 1):
        print(f"\n{'=' * 60}\n[{i}/{len(tasks)}] {task.name}")
        if task.report_path:
            print(f"  report: {task.report_path.name}  原图: {task.image_path}")
        opts = SliceOptions(
            bbox=base_opts.bbox,
            candidate_bbox=(
                list(base_opts.candidate_bbox)
                if base_opts.candidate_bbox
                else ([] if base_opts.auto_hand else list(task.candidate_bbox))
            ),
            auto_hand=base_opts.auto_hand,
            y_min_ratio=base_opts.y_min_ratio,
            y_min=base_opts.y_min,
            num_cards=base_opts.num_cards,
            min_gap=base_opts.min_gap,
            bottom_scan_ratio=base_opts.bottom_scan_ratio,
            ocr=base_opts.ocr,
        )
        if not opts.bbox and not opts.candidate_bbox and not opts.auto_hand:
            opts.auto_hand = True
        task_out = out_root / task.name
        code = process_one_image(task.image_path, task_out, opts, label=task.name)
        if code != 0:
            rc = code
    print(f"\n{'=' * 60}\n批量完成: {len(tasks)} 项 -> {out_root}")
    return rc


def main() -> int:
    args = _parse_args()
    base_opts = SliceOptions.from_args(args)

    if args.input_dir is not None:
        return run_batch(
            args.input_dir,
            args.out_dir.expanduser().resolve(),
            base_opts,
            use_report=not args.no_from_report,
        )

    img_path = args.image or DEFAULT_IMG
    return process_one_image(img_path, args.out_dir, base_opts)


if __name__ == "__main__":
    raise SystemExit(main())
