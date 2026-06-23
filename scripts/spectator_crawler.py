#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
观战模式素材预热爬虫 — 感知 → pHash 去重 → VLM 标注 → card_templates 入库。

在观战大厅 / 新手教程等场景静默运行，每 2 秒截屏一次，只对新牌面调用 VLM。

用法（仓库根目录）::

  pip install imagehash Pillow
  python scripts/spectator_crawler.py
  python scripts/spectator_crawler.py --interval 2 --target 52

环境变量：
  SPECTATOR_TEMPLATES_DIR     模板目录，默认 scripts/card_templates
  SPECTATOR_PHASH_MAX_DIST    汉明距离阈值，默认 5
  SPECTATOR_LOOP_INTERVAL     循环间隔秒，默认 2
  SPECTATOR_TARGET_COUNT      目标张数，默认 52
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

from card_crop_filter import (
    filter_playing_card_candidates,
    is_valid_label_stem,
    looks_like_playing_card_crop,
)
from fast_card_recognizer import (
    _element_bbox,
    _env_int,
    _resolve_screenshot_path,
)
from vision_proxy_qwen import analyze_single_card_with_qwen, default_vlm_model

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logger = logging.getLogger("spectator_crawler")

def _templates_dir() -> Path:
    raw = (os.environ.get("SPECTATOR_TEMPLATES_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return SCRIPTS / "card_templates"


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _phash_max_dist() -> int:
    return _env_int("SPECTATOR_PHASH_MAX_DIST", 5)


# ---------------------------------------------------------------------------
# pHash 记忆索引
# ---------------------------------------------------------------------------


class TemplateLibrary:
    """扫描 card_templates/，维护已知 pHash 与已收录标签。"""

    def __init__(self, templates_dir: Path) -> None:
        self.templates_dir = templates_dir
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.known_hashes: list[Any] = []
        self.known_labels: set[str] = set()
        self._load_existing()

    def _load_existing(self) -> None:
        try:
            import imagehash
            from PIL import Image
        except ImportError as e:
            raise RuntimeError(
                "请安装依赖: pip install imagehash Pillow"
            ) from e

        for p in sorted(self.templates_dir.glob("*.png")):
            stem = p.stem.upper()
            if not is_valid_label_stem(stem):
                continue
            try:
                im = Image.open(p).convert("RGB")
                h = imagehash.phash(im)
                self.known_hashes.append(h)
                if is_valid_label_stem(stem):
                    self.known_labels.add(stem)
            except Exception as e:
                logger.warning("[library] 跳过损坏文件 %s: %s", p.name, e)

        logger.info(
            "[library] 已索引 %d 个 pHash，有效标签 %d 张 → %s",
            len(self.known_hashes),
            len(self.known_labels),
            self.templates_dir,
        )

    def is_duplicate_crop(self, crop_bgr: Any, *, max_dist: int | None = None) -> bool:
        """裁剪图与库内 pHash 汉明距离 <= max_dist 视为已采集。"""
        import cv2
        from PIL import Image

        try:
            import imagehash
        except ImportError:
            return False

        max_dist = max_dist if max_dist is not None else _phash_max_dist()
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        h = imagehash.phash(pil)
        for known in self.known_hashes:
            if h - known <= max_dist:
                return True
        return False

    def register_saved(self, png_path: Path, crop_bgr: Any | None = None) -> None:
        """新图落盘后更新 pHash 与标签集合。"""
        from PIL import Image
        import imagehash
        import cv2

        stem = png_path.stem.upper()
        if crop_bgr is not None:
            rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
            im = Image.fromarray(rgb)
        else:
            im = Image.open(png_path).convert("RGB")
        self.known_hashes.append(imagehash.phash(im))
        if is_valid_label_stem(stem):
            self.known_labels.add(stem)

    def unique_label_count(self) -> int:
        return len(self.known_labels)

    def has_label_file(self, label: str) -> bool:
        return (self.templates_dir / f"{label.upper()}.png").is_file()

    def all_standard_collected(self, target: int) -> bool:
        return self.unique_label_count() >= target


# ---------------------------------------------------------------------------
# Observe（OmniParser 接入点）
# ---------------------------------------------------------------------------


@dataclass
class ScreenObservation:
    raw_screenshot_path: str
    annotated_path: str
    elements_dict: dict[int, dict[str, int]]
    elements: list[dict[str, Any]]
    screen_width: int = 1920
    screen_height: int = 1080


def observe_screen(
    *,
    bbox_threshold: float = 0.03,
    iou_threshold: float = 0.1,
) -> ScreenObservation:
    """
    【接入点】调用 OmniParser 获取全屏截图与元素表。

    默认走项目内 holographic_screen_mcp；可替换为自定义 Observe 实现。
    """
    from core.mcp_multimodal_result import parse_multimodal_observation_payload
    from l3_client.local_mcps.holographic_screen_mcp.session_service import (
        get_holographic_screen_service,
    )

    logger.debug("[observe] OmniParser …")
    raw = get_holographic_screen_service().get_holographic_screen(
        capture_window=False,
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

    elements: list[dict[str, Any]] = []
    if work_dir:
        p = Path(work_dir) / "parsed_result.json"
        if p.is_file():
            try:
                full = json.loads(p.read_text(encoding="utf-8"))
                elements = full.get("elements") or []
            except Exception:
                pass
    if not elements:
        elements = list(obj.get("elements") or [])

    elements_dict: dict[int, dict[str, int]] = {}
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
            }
        except (TypeError, ValueError, KeyError, IndexError):
            continue

    ann = str(obj.get("annotated_image_path") or "")
    if work_dir and not ann:
        for name in ("parsed_output.jpg", "annotated.jpg"):
            c = Path(work_dir) / name
            if c.is_file():
                ann = str(c)
                break

    raw_path = str(Path(work_dir) / "screen_raw.png") if work_dir else ann
    if work_dir and not Path(raw_path).is_file():
        raw_path = ann

    return ScreenObservation(
        raw_screenshot_path=raw_path,
        annotated_path=ann,
        elements_dict=elements_dict,
        elements=elements,
        screen_width=sw,
        screen_height=sh,
    )


def crop_card_regions(
    screenshot_path: str,
    elements_dict: dict[int, dict[str, int]],
    elements: list[dict[str, Any]],
    *,
    screen_height: int,
    hand_card_ids: list[int] | None = None,
) -> list[tuple[int, Any]]:
    """
    从全屏图裁切手牌区域。返回 [(element_id, crop_bgr), ...]
    """
    import cv2

    path = _resolve_screenshot_path(screenshot_path)
    screen = cv2.imread(str(path))
    if screen is None:
        raise FileNotFoundError(f"无法读取截图: {path}")

    sh, sw = screen.shape[:2]
    pad_w = _env_int("TONGITS_CROP_PAD_W", 28)
    pad_h = _env_int("TONGITS_CROP_PAD_H", 40)

    ids = hand_card_ids
    if ids is None:
        ids = filter_playing_card_candidates(
            elements_dict,
            elements,
            screen_width=sw,
            screen_height=screen_height or sh,
        )

    crops: list[tuple[int, Any]] = []
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
        ok, why = looks_like_playing_card_crop(crop)
        if not ok:
            logger.debug("[crop] 丢弃 id=%s: %s", eid, why)
            continue
        crops.append((eid, crop))
    return crops


# ---------------------------------------------------------------------------
# VLM 标注 + 落盘
# ---------------------------------------------------------------------------


def label_and_save_crop(
    crop_bgr: Any,
    library: TemplateLibrary,
    *,
    model: str,
    max_vlm_retries: int = 3,
) -> str | None:
    """
    新牌：VLM 认知 → 保存 {label}.png。已存在同名文件则跳过写入。
    返回 label 或 None（失败不抛异常）。
    """
    import cv2

    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    tmp_path = Path(tmp)
    last_err = ""
    try:
        if not cv2.imwrite(str(tmp_path), crop_bgr):
            return None

        parsed = None
        for attempt in range(1, max_vlm_retries + 1):
            try:
                parsed = analyze_single_card_with_qwen(str(tmp_path), model=model)
                if parsed:
                    break
                last_err = "VLM 返回无法解析"
            except Exception as e:
                last_err = repr(e)
                logger.warning(
                    "[vlm] 标注失败 %d/%d: %s",
                    attempt,
                    max_vlm_retries,
                    e,
                )
                time.sleep(0.5 * attempt)

        if not parsed:
            logger.error("[vlm] 放弃本张: %s", last_err)
            return None

        label, suit, rank = parsed
        label = label.upper()
        if not is_valid_label_stem(label):
            logger.warning("[vlm] 非法标签 %r，不入库", label)
            return None

        ok, why = looks_like_playing_card_crop(crop_bgr)
        if not ok:
            logger.warning("[vlm] 图像不像扑克牌 (%s)，丢弃标签 %s", why, label)
            return None

        dest = library.templates_dir / f"{label}.png"

        if library.has_label_file(label):
            logger.info("[save] 已有 %s，仅更新 pHash 索引", dest.name)
            library.register_saved(dest)
            return label

        if not cv2.imwrite(str(dest), crop_bgr):
            logger.error("[save] 写入失败: %s", dest)
            return None

        library.register_saved(dest, crop_bgr)
        logger.info(
            "\033[33m[入库] 新牌 %s → %s (当前 %d/%d)\033[0m",
            label,
            dest,
            library.unique_label_count(),
            _env_int("SPECTATOR_TARGET_COUNT", 52),
        )
        return label
    finally:
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------


def run_crawler_loop(
    *,
    interval_sec: float = 2.0,
    target_count: int = 52,
    templates_dir: Path | None = None,
    model: str | None = None,
) -> int:
    """
    观战采集主循环。返回退出码 0=大满贯，130=用户中断，1=错误。
    """
    library = TemplateLibrary(templates_dir or _templates_dir())
    model = model or default_vlm_model()
    max_dist = _phash_max_dist()

    logger.info("=" * 60)
    logger.info(
        "[crawler] 观战预热启动 目标=%d 间隔=%.1fs pHash<=%d 目录=%s",
        target_count,
        interval_sec,
        max_dist,
        library.templates_dir,
    )
    logger.info("=" * 60)

    if library.all_standard_collected(target_count):
        _log_grand_slam(target_count)
        return 0

    round_no = 0
    try:
        while True:
            round_no += 1
            t0 = time.perf_counter()
            logger.info("--- 轮次 %d ---", round_no)

            try:
                obs = observe_screen()
            except Exception as e:
                logger.error("[observe] 失败: %s（%ds 后重试）", e, interval_sec)
                time.sleep(interval_sec)
                continue

            try:
                crops = crop_card_regions(
                    obs.raw_screenshot_path,
                    obs.elements_dict,
                    obs.elements,
                    screen_height=obs.screen_height,
                )
            except Exception as e:
                logger.error("[crop] 失败: %s", e)
                time.sleep(interval_sec)
                continue

            logger.info(
                "[crop] 通过严格过滤的牌面候选 %d 块（非牌 OCR/筹码已剔除）",
                len(crops),
            )
            if not crops:
                logger.warning(
                    "[crop] 本帧无合格牌面：请在对局/教程中露出清晰手牌，"
                    "或放宽 SPECTATOR_CARD_*_RATIO 环境变量"
                )
            new_this_round = 0

            for eid, crop in crops:
                if library.is_duplicate_crop(crop, max_dist=max_dist):
                    logger.debug("[dedup] id=%s pHash 命中，跳过 API", eid)
                    continue

                logger.info("[dedup] id=%s 新外观，调用 VLM …", eid)
                label = label_and_save_crop(
                    crop,
                    library,
                    model=model,
                )
                if label:
                    new_this_round += 1

                if library.all_standard_collected(target_count):
                    _log_grand_slam(target_count)
                    return 0

            elapsed = time.perf_counter() - t0
            logger.info(
                "[round] 本轮新入库 %d 张，累计 %d/%d (%.1fs)",
                new_this_round,
                library.unique_label_count(),
                target_count,
                elapsed,
            )

            sleep_left = max(0.0, interval_sec - elapsed)
            if sleep_left > 0:
                time.sleep(sleep_left)

    except KeyboardInterrupt:
        logger.info(
            "[crawler] 用户中断，已采集 %d/%d",
            library.unique_label_count(),
            target_count,
        )
        return 130


def _log_grand_slam(target: int) -> None:
    msg = (
        f"\n{'=' * 60}\n"
        f"[大满贯] {target} 张扑克牌已全部采集完毕，记忆库构建完成！\n"
        f"目录: {_templates_dir()}\n"
        f"{'=' * 60}\n"
    )
    logger.info("\033[32m%s\033[0m", msg)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(description="观战模式扑克牌模板预热爬虫")
    ap.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("SPECTATOR_LOOP_INTERVAL") or "2"),
        help="循环间隔（秒）",
    )
    ap.add_argument(
        "--target",
        type=int,
        default=_env_int("SPECTATOR_TARGET_COUNT", 52),
        help="目标采集张数（标准 52，可设 54 含大小王）",
    )
    ap.add_argument(
        "--templates-dir",
        default=None,
        help="模板输出目录，默认 scripts/card_templates",
    )
    ap.add_argument("--model", default=None, help="VLM 模型，默认 qwen-vl-max")
    args = ap.parse_args()

    td = Path(args.templates_dir).resolve() if args.templates_dir else None
    return run_crawler_loop(
        interval_sec=args.interval,
        target_count=args.target,
        templates_dir=td,
        model=args.model,
    )


if __name__ == "__main__":
    raise SystemExit(main())
