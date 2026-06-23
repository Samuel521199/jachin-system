#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉过滤侦察 — 几何过滤后在原图上画绿框。

【推荐】实时画面（不读已有图/JSON）：
  cd D:\\Projects\\jachi\\jachin-system-main
  python scripts/test_vision_filter.py --live

离线回放（指定历史截图 + JSON）：
  python scripts/test_vision_filter.py \\
    --image scripts/omnioutput/xxx_raw.png \\
    --json  scripts/omnioutput/xxx_result.json \\
    -o scripts/omnioutput/filtered_visual_result.jpg
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("test_vision_filter")

_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
for p in (_SCRIPTS, _REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from vision_filter import (  # noqa: E402
    clean_omniparser_output,
    normalize_elements_dict,
    parse_elements_from_json,
)


@dataclass
class LiveObservation:
    raw_screenshot_path: str
    elements: list[dict[str, Any]]
    screen_width: int
    screen_height: int
    work_dir: str = ""


def observe_live_screen(
    *,
    capture_window: bool = False,
    bbox_threshold: float = 0.03,
    iou_threshold: float = 0.1,
) -> LiveObservation:
    """
    截当前屏幕 → OmniParser → 返回原图路径与 elements（不依赖 omnioutput 里旧文件）。
    """
    from core.mcp_multimodal_result import parse_multimodal_observation_payload
    from l3_client.local_mcps.holographic_screen_mcp.session_service import (
        get_holographic_screen_service,
    )

    logger.info(
        "[scout][live] 截屏 + OmniParser（capture_window=%s）…",
        capture_window,
    )
    t0 = time.perf_counter()
    raw = get_holographic_screen_service().get_holographic_screen(
        capture_window=capture_window,
        bbox_threshold=bbox_threshold,
        iou_threshold=iou_threshold,
    )
    logger.info("[scout][live] OmniParser 完成 (%.1fs)", time.perf_counter() - t0)

    text, _urls = parse_multimodal_observation_payload(raw)
    obj = json.loads(text)
    if not obj.get("ok"):
        raise RuntimeError(f"OmniParser 失败: {obj.get('error')}")

    sw = int(obj.get("screen_width") or 1920)
    sh = int(obj.get("screen_height") or 1080)
    work_dir = str(obj.get("work_dir") or "")

    elements: list[dict[str, Any]] = []
    if work_dir:
        parsed = Path(work_dir) / "parsed_result.json"
        if parsed.is_file():
            try:
                full = json.loads(parsed.read_text(encoding="utf-8"))
                elements = list(full.get("elements") or [])
            except Exception as e:
                logger.warning("[scout][live] 读取 parsed_result.json 失败: %s", e)
    if not elements:
        elements = list(obj.get("elements") or [])

    raw_path = ""
    if work_dir:
        cand = Path(work_dir) / "screen_raw.png"
        if cand.is_file():
            raw_path = str(cand)
    if not raw_path:
        raw_path = str(obj.get("raw_image_path") or obj.get("annotated_image_path") or "")
    if not raw_path or not Path(raw_path).is_file():
        raise RuntimeError(
            "Observe 未返回可用原图路径（screen_raw.png）；work_dir=%s" % work_dir
        )

    logger.info(
        "[scout][live] 原图=%s elements=%d 屏=%dx%d work_dir=%s",
        raw_path,
        len(elements),
        sw,
        sh,
        work_dir or "(无)",
    )
    return LiveObservation(
        raw_screenshot_path=raw_path,
        elements=elements,
        screen_width=sw,
        screen_height=sh,
        work_dir=work_dir,
    )


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


def _default_live_output() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = _SCRIPTS / "omnioutput"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"filtered_live_{ts}.jpg"


def _log_file_written(path: Path) -> None:
    try:
        st = path.stat()
        logger.info(
            "[scout] 写入确认 size=%d bytes mtime=%s",
            st.st_size,
            datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        )
    except OSError as e:
        logger.warning("[scout] 无法 stat 输出文件: %s", e)


def draw_filtered_boxes(
    image_bgr: Any,
    cleaned: dict[int, list[int] | dict],
    *,
    thickness: int = 3,
) -> Any:
    """在图上画绿色粗框 + 红色 ID。"""
    import cv2

    out = image_bgr.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX

    for eid, value in sorted(cleaned.items(), key=lambda kv: int(kv[0])):
        if isinstance(value, dict):
            bbox = value.get("bbox") or value.get("bbox_xyxy_pixels")
        else:
            bbox = value
        if not bbox or len(bbox) < 4:
            continue
        x1, y1, x2, y2 = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), thickness)
        label = str(eid)
        kind = value.get("kind") if isinstance(value, dict) else None
        if kind:
            label = f"{eid}:{str(kind)[:1]}"
        (_tw, th), _ = cv2.getTextSize(label, font, 0.7, 2)
        ty = max(th + 4, y1 - 6)
        cv2.putText(
            out,
            label,
            (x1, ty),
            font,
            0.7,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
    return out


def run_filter_visual_from_elements(
    image_path: str | Path,
    elements: list[dict[str, Any]] | dict[Any, Any],
    screen_width: int,
    screen_height: int,
    *,
    output_path: str | Path,
    include_meta: bool = False,
) -> tuple[Path, dict[int, Any]]:
    """对任意 elements + 原图做过滤与绘图。返回 (输出路径, cleaned_dict)。"""
    import cv2

    img_p = _resolve_image_path(Path(image_path))
    screen = cv2.imread(str(img_p))
    if screen is None:
        raise RuntimeError(f"OpenCV 无法读取图片: {img_p}")

    sh, sw = screen.shape[:2]
    if sw != screen_width or sh != screen_height:
        logger.warning(
            "[scout] 声明尺寸 %dx%d 与图片实际 %dx%d 不一致，以图片为准",
            screen_width,
            screen_height,
            sw,
            sh,
        )

    raw_dict = normalize_elements_dict(elements)
    logger.info("[scout] 元素数=%d", len(raw_dict))

    cleaned = clean_omniparser_output(
        raw_dict,
        sw,
        sh,
        include_meta=include_meta,
    )

    annotated = draw_filtered_boxes(screen, cleaned)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_p), annotated):
        raise RuntimeError(f"写入失败: {out_p}")

    logger.info("[scout] 已保存可视化 → %s (保留 %d 框)", out_p.resolve(), len(cleaned))
    _log_file_written(out_p)
    return out_p.resolve(), cleaned


def run_filter_visual(
    image_path: str | Path,
    json_path: str | Path,
    *,
    output_path: str | Path | None = None,
    include_meta: bool = False,
) -> Path:
    """离线：从已有 JSON + 截图过滤绘图。"""
    json_p = Path(json_path)
    if not json_p.is_file():
        raise FileNotFoundError(f"找不到 JSON: {json_p}")

    raw, jw, jh = parse_elements_from_json(json_p)
    import cv2

    img_p = _resolve_image_path(Path(image_path))
    screen = cv2.imread(str(img_p))
    if screen is None:
        raise RuntimeError(f"OpenCV 无法读取图片: {img_p}")
    sh, sw = screen.shape[:2]
    if jw > 0 and jh > 0 and (jw != sw or jh != sh):
        logger.warning(
            "[scout] JSON 尺寸 %dx%d 与图片 %dx%d 不一致",
            jw,
            jh,
            sw,
            sh,
        )

    out_p = Path(output_path) if output_path else Path.cwd() / "filtered_visual_result.jpg"
    path, _cleaned = run_filter_visual_from_elements(
        img_p,
        raw,
        sw,
        sh,
        output_path=out_p,
        include_meta=include_meta,
    )
    return path


def run_filter_visual_live(
    *,
    output_path: str | Path | None = None,
    include_meta: bool = False,
    capture_window: bool = False,
    bbox_threshold: float = 0.03,
    iou_threshold: float = 0.1,
) -> tuple[Path, dict[int, Any]]:
    """实时：截屏 → OmniParser → 过滤 → 保存标注图（新文件名，不覆盖旧结果）。"""
    obs = observe_live_screen(
        capture_window=capture_window,
        bbox_threshold=bbox_threshold,
        iou_threshold=iou_threshold,
    )
    out_p = Path(output_path) if output_path else _default_live_output()
    path, cleaned = run_filter_visual_from_elements(
        obs.raw_screenshot_path,
        obs.elements,
        obs.screen_width,
        obs.screen_height,
        output_path=out_p,
        include_meta=include_meta,
    )
    return path, cleaned


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ap = argparse.ArgumentParser(
        description="OmniParser 几何过滤可视化（--live=当前屏幕，否则离线回放）"
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="截当前屏幕并跑 OmniParser（不使用已有 image/json）",
    )
    ap.add_argument(
        "--image",
        help="离线：原始全屏截图",
    )
    ap.add_argument(
        "--json",
        help="离线：OmniParser JSON",
    )
    ap.add_argument(
        "--output",
        "-o",
        help="输出路径；--live 默认 scripts/omnioutput/filtered_live_时间戳.jpg",
    )
    ap.add_argument(
        "--meta",
        action="store_true",
        help="标签显示 card/button 类型",
    )
    ap.add_argument(
        "--dump-json",
        help="另存 cleaned_dict JSON",
    )
    ap.add_argument(
        "--capture-window",
        action="store_true",
        help="--live 时只截前台窗口",
    )
    ap.add_argument(
        "--bbox-threshold",
        type=float,
        default=0.03,
        help="OmniParser bbox_threshold",
    )
    ap.add_argument(
        "--iou-threshold",
        type=float,
        default=0.1,
        help="OmniParser iou_threshold",
    )
    args = ap.parse_args()

    try:
        cleaned_cache: dict[int, Any] | None = None
        if args.live:
            out_path, cleaned_cache = run_filter_visual_live(
                output_path=args.output,
                include_meta=args.meta,
                capture_window=args.capture_window,
                bbox_threshold=args.bbox_threshold,
                iou_threshold=args.iou_threshold,
            )
        else:
            if not args.image or not args.json:
                ap.error("离线模式需要 --image 与 --json；实时请用 --live")
            out_path = run_filter_visual(
                args.image,
                args.json,
                output_path=args.output or "filtered_visual_result.jpg",
                include_meta=args.meta,
            )

        if args.dump_json:
            if cleaned_cache is not None:
                cleaned = cleaned_cache
            else:
                import cv2

                elements, sw, sh = parse_elements_from_json(args.json)
                img = cv2.imread(str(_resolve_image_path(Path(args.image))))
                if img is not None:
                    sh, sw = img.shape[:2]
                cleaned = clean_omniparser_output(
                    normalize_elements_dict(elements),
                    sw,
                    sh,
                    include_meta=args.meta,
                )
            Path(args.dump_json).write_text(
                json.dumps(cleaned, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("[scout] cleaned JSON → %s", args.dump_json)

        print(str(out_path))
        return 0
    except Exception as e:
        logger.exception("[scout] 失败: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
