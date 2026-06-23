#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全图景视觉融合可视化：OmniParser UI 过滤（蓝框）+ OpenCV 角点认牌（红点瞄准）。

用法（离线回放）：
  python scripts/test_full_vision_pipeline.py \\
    --image scripts/omnioutput/xxx_raw.png \\
    --json  scripts/omnioutput/xxx_result.json \\
    -o scripts/omnioutput/full_pipeline_vision.jpg

用法（实时当前屏幕 — 蓝框 UI + 红点手牌）：
  python scripts/test_full_vision_pipeline.py --live
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("full_vision_pipeline")

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
for p in (_SCRIPTS, _REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fast_card_recognizer import (  # noqa: E402
    ZONE_KEYS,
    flatten_table_snapshot,
    recognize_table_snapshot,
    reset_hand_roi_session,
    resolve_multi_zone_rois,
)
from test_vision_filter import observe_live_screen  # noqa: E402
from vision_filter import (  # noqa: E402
    clean_omniparser_output,
    normalize_elements_dict,
    parse_elements_from_json,
)


def _default_live_output() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = _SCRIPTS / "omnioutput"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"full_pipeline_live_{ts}.jpg"


def _resolve_image_path(image_path: Path) -> Path:
    if image_path.is_file():
        return image_path
    if "annotated" in image_path.stem.lower():
        for cand in (
            image_path.parent
            / image_path.name.replace("annotated", "raw").replace("_annotated", "_raw"),
            image_path.parent / "screen_raw.png",
        ):
            if cand.is_file():
                return cand
    raise FileNotFoundError(f"找不到截图: {image_path}")


def load_omni_bundle(
    json_path: Path,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, int]], int, int]:
    """读取 OmniParser JSON → elements 列表 + elements_dict（含 content）。"""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    elements = list(data.get("elements") or [])
    sw, sh = 1920, 1080
    size = data.get("image_size") or {}
    try:
        if size.get("w"):
            sw = int(size["w"])
        if size.get("h"):
            sh = int(size["h"])
    except (TypeError, ValueError):
        pass

    elements_dict: dict[int, dict[str, int]] = {}
    for row in elements:
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        cen = row.get("center_xy_pixels") or [
            row.get("center_x"),
            row.get("center_y"),
        ]
        try:
            elements_dict[eid] = {
                "center_x": int(cen[0]),
                "center_y": int(cen[1]),
                "content": str(row.get("content") or ""),
            }
        except (TypeError, ValueError, IndexError):
            continue
    return elements, elements_dict, sw, sh


def draw_ui_buttons(
    canvas: Any,
    cleaned: dict[int, dict[str, Any] | list[int]],
    *,
    thickness: int = 4,
) -> int:
    """蓝色粗框 + ID（仅 kind=button）。"""
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    count = 0
    for eid, value in sorted(cleaned.items(), key=lambda kv: int(kv[0])):
        if isinstance(value, dict):
            if value.get("kind") != "button":
                continue
            bbox = value.get("bbox") or value.get("bbox_xyxy_pixels")
        else:
            bbox = value
        if not bbox or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (255, 0, 0), thickness)
        label = str(eid)
        cv2.putText(
            canvas,
            label,
            (x1, max(24, y1 - 8)),
            font,
            0.75,
            (255, 0, 0),
            2,
            cv2.LINE_AA,
        )
        count += 1
    return count


_ZONE_ROI_COLORS: dict[str, tuple[int, int, int]] = {
    "player_hand": (0, 220, 255),
    "center_discard": (255, 200, 0),
    "opponent_left": (0, 255, 128),
    "opponent_right": (255, 128, 255),
}


def draw_zone_rois(
    canvas: Any,
    zone_rois: dict[str, tuple[int, int, int, int]],
) -> None:
    """四战区 ROI 细框。"""
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    for zone, (x1, y1, x2, y2) in zone_rois.items():
        color = _ZONE_ROI_COLORS.get(zone, (200, 200, 200))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            canvas,
            zone,
            (x1 + 4, max(y1 - 6, 16)),
            font,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )


def draw_card_crosshairs(
    canvas: Any,
    cards: list[dict[str, Any]],
    *,
    dot_radius: int = 10,
) -> int:
    """红点瞄准星 + 牌面文字（如 H9）。"""
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    for c in cards:
        cx = int(c.get("center_x") or 0)
        cy = int(c.get("center_y") or 0)
        suit = str(c.get("suit") or "?").upper()
        rank = str(c.get("rank") or "?").upper()
        zone = str(c.get("zone") or "")[:1]
        label = f"{suit}{rank}"
        if zone:
            label = f"{zone}:{label}"
        cv2.circle(canvas, (cx, cy), dot_radius, (0, 0, 255), -1, lineType=cv2.LINE_AA)
        cv2.circle(canvas, (cx, cy), dot_radius + 2, (255, 255, 255), 2, lineType=cv2.LINE_AA)
        cv2.putText(
            canvas,
            label,
            (cx + dot_radius + 6, cy + 6),
            font,
            0.85,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    return len(cards)


def run_full_vision_pipeline(
    image_path: str | Path,
    json_path: str | Path | None = None,
    *,
    elements: list[dict[str, Any]] | None = None,
    elements_dict: dict[int, dict[str, int]] | None = None,
    output_path: str | Path | None = None,
    force_roi: bool = False,
    draw_roi: bool = True,
) -> Path:
    import cv2

    img_p = _resolve_image_path(Path(image_path))
    screen = cv2.imread(str(img_p))
    if screen is None:
        raise RuntimeError(f"OpenCV 无法读取: {img_p}")

    sh, sw = screen.shape[:2]
    jw, jh = sw, sh
    if elements is None:
        if not json_path:
            raise ValueError("离线模式需要 json_path，或传入 elements（--live）")
        json_p = Path(json_path)
        if not json_p.is_file():
            raise FileNotFoundError(f"找不到 JSON: {json_p}")
        elements, elements_dict, jw, jh = load_omni_bundle(json_p)
    elif elements_dict is None:
        elements_dict = {}
        for row in elements:
            try:
                eid = int(row["id"])
                cen = row.get("center_xy_pixels") or [
                    row.get("center_x"),
                    row.get("center_y"),
                ]
                elements_dict[eid] = {
                    "center_x": int(cen[0]),
                    "center_y": int(cen[1]),
                    "content": str(row.get("content") or ""),
                }
            except (TypeError, ValueError, KeyError, IndexError):
                continue
    if jw and jh and (jw != sw or jh != sh):
        logger.warning(
            "[pipeline] JSON 尺寸 %dx%d ≠ 图片 %dx%d，以图片为准",
            jw,
            jh,
            sw,
            sh,
        )

    canvas = screen.copy()

    # --- Step 1: OmniParser + vision_filter（仅 UI 按钮画蓝框）---
    raw_bbox = normalize_elements_dict(elements)
    cleaned = clean_omniparser_output(
        raw_bbox,
        sw,
        sh,
        include_meta=True,
    )
    ui_count = draw_ui_buttons(canvas, cleaned)  # type: ignore[arg-type]
    logger.info("[pipeline][1] UI 按钮蓝框=%d", ui_count)

    # --- Step 2: 四战区 ROI + 全景角点扫掠 ---
    if force_roi:
        reset_hand_roi_session()

    zone_rois = resolve_multi_zone_rois(
        sw,
        sh,
        screenshot_path=str(img_p),
        elements_dict=elements_dict,
        elements=elements,
        force_recalibrate=force_roi,
    )
    if draw_roi:
        draw_zone_rois(canvas, zone_rois)

    try:
        snapshot = recognize_table_snapshot(
            str(img_p),
            elements_dict,
            elements=elements,
            force_recalibrate=False,
        )
    except RuntimeError as e:
        logger.warning("[pipeline][2] 角点认牌跳过: %s", e)
        snapshot = {z: [] for z in ZONE_KEYS}

    all_cards = flatten_table_snapshot(snapshot)
    card_count = draw_card_crosshairs(canvas, all_cards)
    logger.info(
        "[pipeline][2] 红点合计=%d | hand=%d discard=%d L=%d R=%d",
        card_count,
        len(snapshot.get("player_hand") or []),
        len(snapshot.get("center_discard") or []),
        len(snapshot.get("opponent_left") or []),
        len(snapshot.get("opponent_right") or []),
    )

    out_p = Path(output_path) if output_path else Path.cwd() / "full_pipeline_vision.jpg"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_p), canvas):
        raise RuntimeError(f"写入失败: {out_p}")

    logger.info(
        "[pipeline] 融合图已保存 → %s (UI=%d 牌=%d)",
        out_p.resolve(),
        ui_count,
        card_count,
    )
    return out_p.resolve()


def run_full_vision_pipeline_live(
    *,
    output_path: str | Path | None = None,
    force_roi: bool = False,
    draw_roi: bool = True,
    capture_window: bool = False,
    bbox_threshold: float = 0.03,
    iou_threshold: float = 0.1,
) -> Path:
    """截当前屏幕 → OmniParser → 融合标注（不读已有 image/json）。"""
    logger.info("[pipeline][live] 请先将游戏窗口置于前台 …")
    obs = observe_live_screen(
        capture_window=capture_window,
        bbox_threshold=bbox_threshold,
        iou_threshold=iou_threshold,
    )
    out_p = Path(output_path) if output_path else _default_live_output()
    return run_full_vision_pipeline(
        obs.raw_screenshot_path,
        elements=obs.elements,
        output_path=out_p,
        force_roi=force_roi,
        draw_roi=draw_roi,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="OmniParser UI + OpenCV 角点认牌 融合可视化")
    ap.add_argument(
        "--live",
        action="store_true",
        help="截当前屏幕并跑 OmniParser（不用 --image/--json）",
    )
    ap.add_argument("--image", help="离线：游戏原图 raw.png")
    ap.add_argument("--json", help="离线：OmniParser parsed_result.json")
    ap.add_argument(
        "--output",
        "-o",
        help="输出路径；--live 默认 scripts/omnioutput/full_pipeline_live_时间戳.jpg",
    )
    ap.add_argument(
        "--force-roi",
        action="store_true",
        help="忽略 roi_config 缓存，重新校准手牌区",
    )
    ap.add_argument(
        "--no-roi-box",
        action="store_true",
        help="不画手牌 ROI 黄框",
    )
    ap.add_argument(
        "--capture-window",
        action="store_true",
        help="--live 时只截前台窗口",
    )
    args = ap.parse_args()

    try:
        if args.live:
            path = run_full_vision_pipeline_live(
                output_path=args.output,
                force_roi=args.force_roi,
                draw_roi=not args.no_roi_box,
                capture_window=args.capture_window,
            )
        else:
            if not args.image or not args.json:
                ap.error("离线模式需要 --image 与 --json；实时屏幕请用 --live")
            path = run_full_vision_pipeline(
                args.image,
                args.json,
                output_path=args.output or "full_pipeline_vision.jpg",
                force_roi=args.force_roi,
                draw_roi=not args.no_roi_box,
            )
        print(str(path))
        return 0
    except Exception as e:
        logger.exception("[pipeline] 失败: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
