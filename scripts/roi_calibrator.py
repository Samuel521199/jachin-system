#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双引擎手牌 ROI 自动校准：本地缓存 → OmniParser 锚点 → VLM 零样本画框 → 安全回退。

持久化：scripts/roi_config.json（可用 TONGITS_ROI_CONFIG_PATH 覆盖）

环境变量：
  TONGITS_ROI_CONFIG_PATH       缓存文件路径
  TONGITS_ROI_FORCE_RECALIBRATE 1 忽略缓存强制重校
  TONGITS_ROI_ANCHOR_Y_GAP      Dump/Group 底边下手牌起点偏移，默认 10
  TONGITS_ROI_ANCHOR_HEIGHT     手牌带高度（像素），默认 200
  TONGITS_ROI_ANCHOR_X_MIN_RATIO 默认 0.15
  TONGITS_ROI_ANCHOR_X_MAX_RATIO 默认 0.85
  TONGITS_ROI_FALLBACK          最终回退 x1,y1,x2,y2，默认 550,750,1370,980

离线测试：
  python scripts/roi_calibrator.py --image scripts/omnioutput/xxx_raw.png --result parsed.json
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("roi_calibrator")

_RED = "\033[31m"
_RESET = "\033[0m"

DEFAULT_FALLBACK_ROI: tuple[int, int, int, int] = (550, 750, 1370, 980)

ROI_VLM_PROMPT = (
    "这是一张扑克牌游戏的完整截图。请找到画面正下方【玩家自己手牌】所在的整体长方形区域。"
    "请严格以 JSON 格式输出该区域在全图中的百分比坐标："
    '{"ymin": 0.70, "xmin": 0.20, "ymax": 0.95, "xmax": 0.80}。'
    "绝不要输出其他废话。"
)

# 解析 VLM 百分比 JSON（键名顺序/大小写容错）
_VLM_FRAC_JSON_RE = re.compile(
    r"\{[^{}]*?(?:\"|')?y\s*min(?:\"|')?\s*:\s*([\d.]+)[^{}]*?"
    r"(?:\"|')?x\s*min(?:\"|')?\s*:\s*([\d.]+)[^{}]*?"
    r"(?:\"|')?y\s*max(?:\"|')?\s*:\s*([\d.]+)[^{}]*?"
    r"(?:\"|')?x\s*max(?:\"|')?\s*:\s*([\d.]+)[^{}]*?\}",
    re.I | re.S,
)

_ANCHOR_ACTIONS = ("dump", "group")


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent


def _config_path() -> Path:
    raw = (os.environ.get("TONGITS_ROI_CONFIG_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _scripts_dir() / "roi_config.json"


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _fallback_roi() -> tuple[int, int, int, int]:
    raw = (os.environ.get("TONGITS_ROI_FALLBACK") or "").strip()
    if raw:
        try:
            parts = [int(x.strip()) for x in raw.replace(" ", "").split(",") if x.strip()]
            if len(parts) == 4 and parts[2] > parts[0] and parts[3] > parts[1]:
                return tuple(parts)  # type: ignore[return-value]
        except ValueError:
            pass
    return DEFAULT_FALLBACK_ROI


def _validate_roi(
    roi: tuple[int, int, int, int],
    screen_width: int,
    screen_height: int,
) -> bool:
    try:
        x1, y1, x2, y2 = (int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3]))
    except (TypeError, ValueError):
        return False
    if x2 <= x1 or y2 <= y1:
        return False
    if x1 < 0 or y1 < 0 or x2 > screen_width or y2 > screen_height:
        return False
    if (x2 - x1) < 80 or (y2 - y1) < 40:
        return False
    return True


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


def get_cached_roi(
    screen_width: int | None = None,
    screen_height: int | None = None,
) -> tuple[int, int, int, int] | None:
    """
    读取 roi_config.json。若提供分辨率则校验与缓存一致（±2px 容差）。
    """
    path = _config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[roi][cache] 读取失败 %s: %s", path, e)
        return None

    roi_raw = data.get("roi")
    if not isinstance(roi_raw, (list, tuple)) or len(roi_raw) != 4:
        return None
    try:
        roi = tuple(int(v) for v in roi_raw)  # type: ignore[assignment]
    except (TypeError, ValueError):
        return None

    cw = data.get("screen_width")
    ch = data.get("screen_height")
    if screen_width and screen_height and cw and ch:
        try:
            if abs(int(cw) - screen_width) > 2 or abs(int(ch) - screen_height) > 2:
                logger.info(
                    "[roi][cache] 分辨率变化 %sx%s → %sx%s，忽略缓存",
                    cw,
                    ch,
                    screen_width,
                    screen_height,
                )
                return None
        except (TypeError, ValueError):
            pass

    if screen_width and screen_height and not _validate_roi(roi, screen_width, screen_height):
        logger.warning("[roi][cache] 缓存 ROI 无效: %s", roi)
        return None

    logger.info(
        "[roi][cache] 命中 %s → %s (source=%s)",
        path,
        roi,
        data.get("source", "?"),
    )
    return roi


def save_cached_roi(
    roi_tuple: tuple[int, int, int, int],
    *,
    screen_width: int,
    screen_height: int,
    source: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """写入 roi_config.json。"""
    path = _config_path()
    payload: dict[str, Any] = {
        "roi": list(roi_tuple),
        "screen_width": int(screen_width),
        "screen_height": int(screen_height),
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload.update(extra)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[roi][cache] 已保存 %s source=%s roi=%s", path, source, roi_tuple)
    except Exception as e:
        logger.error("[roi][cache] 写入失败 %s: %s", path, e)


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip().lower())


def _match_anchor_action(content: str) -> str | None:
    norm = _normalize_label(content)
    if not norm:
        return None
    if re.search(r"^dump$", norm, re.I):
        return "dump"
    if re.search(r"^group$", norm, re.I):
        return "group"
    return None


def _bbox_bottom_y(row: dict[str, Any], *, default_half_h: int = 18) -> int | None:
    b = row.get("bbox_xyxy_pixels") or []
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return int(b[3])
    cy = row.get("center_y")
    if cy is None:
        cen = row.get("center_xy_pixels")
        if isinstance(cen, (list, tuple)) and len(cen) >= 2:
            cy = cen[1]
    if cy is not None:
        return int(cy) + default_half_h
    eid = row.get("id")
    return None


def _row_from_elements_dict(
    eid: int,
    elements_dict: dict[int, dict[str, int]],
) -> dict[str, Any]:
    row = elements_dict.get(eid) or {}
    return {
        "id": eid,
        "center_x": row.get("center_x"),
        "center_y": row.get("center_y"),
        "content": row.get("content", ""),
    }


def calibrate_via_anchor(
    screen_width: int,
    screen_height: int,
    elements_dict: dict[int, dict[str, int]] | None = None,
    *,
    elements: list[dict[str, Any]] | None = None,
) -> tuple[int, int, int, int] | None:
    """
    引擎一：从 Dump / Group 按钮底边推导手牌 ROI。
    """
    try:
        gap = _env_int("TONGITS_ROI_ANCHOR_Y_GAP", 10)
        band_h = _env_int("TONGITS_ROI_ANCHOR_HEIGHT", 200)
        x_min = int(screen_width * _env_float("TONGITS_ROI_ANCHOR_X_MIN_RATIO", 0.15))
        x_max = int(screen_width * _env_float("TONGITS_ROI_ANCHOR_X_MAX_RATIO", 0.85))

        anchor_bottoms: list[tuple[str, int, int]] = []  # action, eid, y2

        scan_rows: list[dict[str, Any]] = []
        if elements:
            scan_rows.extend(elements)
        elif elements_dict:
            for eid in sorted(elements_dict.keys()):
                scan_rows.append(_row_from_elements_dict(eid, elements_dict))

        for row in scan_rows:
            try:
                eid = int(row.get("id", -1))
            except (TypeError, ValueError):
                continue
            action = _match_anchor_action(str(row.get("content") or ""))
            if action not in _ANCHOR_ACTIONS:
                continue
            y2 = _bbox_bottom_y(row)
            if y2 is None:
                continue
            anchor_bottoms.append((action, eid, y2))
            logger.info(
                "[roi][anchor] 锚点 %s id=%s bottom_y=%s text=%r",
                action,
                eid,
                y2,
                str(row.get("content") or "")[:32],
            )

        if not anchor_bottoms:
            logger.warning("[roi][anchor] 未找到 Dump/Group OCR 锚点")
            return None

        # 取最靠下的按钮底边（通常动作条同一行）
        _, best_eid, y2 = max(anchor_bottoms, key=lambda t: t[2])
        y_min = y2 + gap
        y_max = y_min + band_h
        roi = _clamp_roi((x_min, y_min, x_max, y_max), screen_width, screen_height)

        if not _validate_roi(roi, screen_width, screen_height):
            logger.warning("[roi][anchor] 推导 ROI 校验失败: %s", roi)
            return None

        logger.info(
            "[roi][anchor] 成功 anchor_id=%s y2=%s → ROI=%s",
            best_eid,
            y2,
            roi,
        )
        return roi
    except Exception as e:
        logger.exception("[roi][anchor] 异常: %s", e)
        return None


def _parse_vlm_fraction_json(text: str) -> dict[str, float] | None:
    """从 VLM 回复中提取 ymin/xmin/ymax/xmax（0~1 比例）。"""
    try:
        from vision_proxy_qwen import strip_markdown_json

        cleaned = strip_markdown_json(text)
    except Exception:
        cleaned = (text or "").strip()

    # 直接 json.loads
    for candidate in (cleaned, text or ""):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                norm: dict[str, float] = {}
                for k, v in obj.items():
                    key = re.sub(r"[\s_]", "", str(k).lower())
                    if key in ("ymin", "xmin", "ymax", "xmax"):
                        norm[key] = float(v)
                if len(norm) == 4:
                    return norm  # type: ignore[return-value]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # 正则容错
    for blob in (cleaned, text or ""):
        m = _VLM_FRAC_JSON_RE.search(blob)
        if m:
            try:
                ymin, xmin, ymax, xmax = (float(m.group(i)) for i in range(1, 5))
                return {"ymin": ymin, "xmin": xmin, "ymax": ymax, "xmax": xmax}
            except ValueError:
                continue

        m2 = re.search(r"\{[\s\S]*?\}", blob)
        if m2:
            frag = m2.group(0)
            pairs = re.findall(
                r"(y\s*min|x\s*min|y\s*max|x\s*max)\s*[:=]\s*([\d.]+)",
                frag,
                re.I,
            )
            if len(pairs) >= 4:
                acc: dict[str, float] = {}
                for k, v in pairs:
                    acc[k.replace(" ", "").lower()] = float(v)
                if all(k in acc for k in ("ymin", "xmin", "ymax", "xmax")):
                    return {
                        "ymin": acc["ymin"],
                        "xmin": acc["xmin"],
                        "ymax": acc["ymax"],
                        "xmax": acc["xmax"],
                    }
    return None


def _fractions_to_pixels(
    frac: dict[str, float],
    screen_width: int,
    screen_height: int,
) -> tuple[int, int, int, int] | None:
    try:
        ymin = float(frac["ymin"])
        xmin = float(frac["xmin"])
        ymax = float(frac["ymax"])
        xmax = float(frac["xmax"])
    except (KeyError, TypeError, ValueError):
        return None

    # 若模型返回 0~100 百分比
    if max(ymin, xmin, ymax, xmax) > 1.5:
        ymin, xmin, ymax, xmax = ymin / 100, xmin / 100, ymax / 100, xmax / 100

    if not (0 <= ymin < ymax <= 1.05 and 0 <= xmin < xmax <= 1.05):
        return None

    x1 = int(round(xmin * screen_width))
    y1 = int(round(ymin * screen_height))
    x2 = int(round(xmax * screen_width))
    y2 = int(round(ymax * screen_height))
    return x1, y1, x2, y2


def calibrate_via_vlm(
    screenshot_path: str,
    screen_width: int,
    screen_height: int,
    *,
    model: str | None = None,
    max_retries: int = 2,
) -> tuple[int, int, int, int] | None:
    """
    引擎二：Qwen-VL 零样本画框 → 像素 ROI。
    """
    try:
        from vision_proxy_qwen import _vlm_chat, default_vlm_model

        path = Path(screenshot_path)
        if not path.is_file():
            logger.error("[roi][vlm] 截图不存在: %s", screenshot_path)
            return None

        model = model or default_vlm_model()
        last_err = ""
        for attempt in range(1, max_retries + 1):
            try:
                raw = _vlm_chat(ROI_VLM_PROMPT, str(path), model=model, max_tokens=256)
                frac = _parse_vlm_fraction_json(raw)
                if frac is None:
                    last_err = f"无法解析 JSON: {raw[:200]}"
                    logger.warning(
                        "[roi][vlm] 解析失败 %d/%d: %s",
                        attempt,
                        max_retries,
                        last_err,
                    )
                    time.sleep(0.4 * attempt)
                    continue

                roi = _fractions_to_pixels(frac, screen_width, screen_height)
                if roi is None:
                    last_err = f"比例无效: {frac}"
                    continue

                roi = _clamp_roi(roi, screen_width, screen_height)
                if not _validate_roi(roi, screen_width, screen_height):
                    last_err = f"像素 ROI 无效: {roi} from {frac}"
                    logger.warning("[roi][vlm] %s", last_err)
                    continue

                logger.info(
                    "[roi][vlm] 成功 frac=%s → ROI=%s",
                    frac,
                    roi,
                )
                return roi
            except Exception as e:
                last_err = repr(e)
                logger.warning(
                    "[roi][vlm] 请求异常 %d/%d: %s",
                    attempt,
                    max_retries,
                    e,
                )
                time.sleep(0.5 * attempt)

        logger.error("[roi][vlm] 放弃: %s", last_err)
        return None
    except Exception as e:
        logger.exception("[roi][vlm] 模块异常: %s", e)
        return None


def auto_calibrate_roi(
    screenshot_path: str | None,
    elements_dict: dict[int, dict[str, int]] | None,
    elements: list[dict[str, Any]] | None,
    screen_width: int,
    screen_height: int,
    *,
    force_recalibrate: bool = False,
    vlm_model: str | None = None,
) -> tuple[tuple[int, int, int, int], str]:
    """
    统帅调度：cache → anchor → vlm → fallback。

    Returns:
        (roi_xyxy, engine_name)  engine_name: cache | anchor | vlm | fallback | env
    """
    force = force_recalibrate or (
        (os.environ.get("TONGITS_ROI_FORCE_RECALIBRATE") or "").strip() in ("1", "true", "yes")
    )

    # 显式环境变量 ROI（运维覆盖，不写缓存）
    env_roi = (os.environ.get("TONGITS_PLAYER_HAND_ROI") or "").strip()
    if env_roi:
        try:
            parts = [int(x.strip()) for x in env_roi.replace(" ", "").split(",") if x.strip()]
            if len(parts) == 4:
                roi = _clamp_roi(tuple(parts), screen_width, screen_height)  # type: ignore[arg-type]
                if _validate_roi(roi, screen_width, screen_height):
                    logger.info("[roi] 使用环境变量 TONGITS_PLAYER_HAND_ROI=%s", roi)
                    return roi, "env"
        except ValueError:
            pass

    if not force:
        cached = get_cached_roi(screen_width, screen_height)
        if cached is not None:
            return _clamp_roi(cached, screen_width, screen_height), "cache"

    # 引擎一
    roi_anchor = calibrate_via_anchor(
        screen_width,
        screen_height,
        elements_dict,
        elements=elements,
    )
    if roi_anchor is not None:
        save_cached_roi(
            roi_anchor,
            screen_width=screen_width,
            screen_height=screen_height,
            source="anchor",
        )
        logger.info("[roi] ★ 校准完成：引擎=OmniParser锚点 anchor ROI=%s", roi_anchor)
        return roi_anchor, "anchor"

    # 引擎二
    if screenshot_path:
        roi_vlm = calibrate_via_vlm(
            screenshot_path,
            screen_width,
            screen_height,
            model=vlm_model,
        )
        if roi_vlm is not None:
            save_cached_roi(
                roi_vlm,
                screen_width=screen_width,
                screen_height=screen_height,
                source="vlm",
            )
            logger.info("[roi] ★ 校准完成：引擎=VLM零样本画框 ROI=%s", roi_vlm)
            return roi_vlm, "vlm"
    else:
        logger.warning("[roi][vlm] 无截图路径，跳过 VLM 引擎")

    # 回退
    roi_fb = _clamp_roi(_fallback_roi(), screen_width, screen_height)
    logger.error(
        "%s[roi] ★ 全引擎失败，使用安全回退 ROI=%s（请手动标定或删除 roi_config.json 重试）%s",
        _RED,
        roi_fb,
        _RESET,
    )
    save_cached_roi(
        roi_fb,
        screen_width=screen_width,
        screen_height=screen_height,
        source="fallback",
        extra={"warning": "auto_calibrate_all_failed"},
    )
    return roi_fb, "fallback"


def main() -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="双引擎 ROI 校准测试")
    ap.add_argument("--image", help="screen_raw.png")
    ap.add_argument("--result", help="parsed_result.json（锚点引擎）")
    ap.add_argument("--force", action="store_true", help="忽略缓存")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    args = ap.parse_args()

    elements: list[dict[str, Any]] = []
    elements_dict: dict[int, dict[str, int]] = {}
    if args.result:
        try:
            data = json.loads(Path(args.result).read_text(encoding="utf-8"))
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
        except Exception as e:
            logger.error("解析 result 失败: %s", e)
            return 1

    sw, sh = args.width, args.height
    if args.image:
        try:
            import cv2

            img = cv2.imread(args.image)
            if img is not None:
                sh, sw = img.shape[:2]
        except Exception:
            pass

    roi, engine = auto_calibrate_roi(
        args.image,
        elements_dict or None,
        elements or None,
        sw,
        sh,
        force_recalibrate=args.force,
    )
    print(json.dumps({"roi": list(roi), "engine": engine}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
