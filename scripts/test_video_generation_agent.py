#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 视频生成网页 — 视觉具身端到端集成测试。

架构（严格解耦）：
  Observe  → OmniParser 产出红框标注图 + elements_dict（物理坐标）
  Plan     → VLM 只读标注图，只输出 element_id（禁止 x,y）
  Act      → physical_click / type_text 查表点击

四阶段串行：打开素材库 → 弹窗选图确认 → 输入提示词生成 → 结果复核。

用法（仓库根目录）::

  .\\.venv-omniparser\\Scripts\\python.exe scripts\\test_video_generation_agent.py
  .\\.venv-omniparser\\Scripts\\python.exe scripts\\test_video_generation_agent.py --mock
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
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

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logger = logging.getLogger("video_gen_agent")

DEFAULT_PROMPT = (
    "Cyberpunk style city at night, neon lights, cinematic panning"
)

OMNI_BOX_THRESHOLD = 0.03
OMNI_IOU_THRESHOLD = 0.1
MIN_OMNI_ELEMENTS = 3
MAX_VL_RETRIES = 3

# Mock 阶段计数（--mock 时按 Phase 返回不同 elements_dict）
_mock_phase: int = 0


@dataclass
class HolographicFrame:
    """单次 Observe 结果。"""

    ok: bool
    annotated_image_path: str
    elements_dict: dict[int, dict[str, int]]
    window_region: tuple[int, int, int, int] | None = None
    elements: list[dict[str, Any]] = field(default_factory=list)
    image_data_url: str | None = None
    work_dir: str | None = None
    error: str = ""


# ---------------------------------------------------------------------------
# JSON / VLM 工具
# ---------------------------------------------------------------------------


def strip_markdown_json(text: str) -> str:
    raw = (text or "").strip()
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if m:
            return m.group(1).strip()
    # 抽取首个 { ... } 或 [ ... ]
    for pat in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
        m = re.search(pat, raw)
        if m:
            return m.group(0).strip()
    return raw


def parse_json_object(text: str, *, required_keys: tuple[str, ...] | None = None) -> dict[str, Any]:
    raw = strip_markdown_json(text)
    if not raw:
        raise ValueError("VLM 返回为空")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError(f"须为 JSON 对象，收到: {type(obj).__name__}")
    if required_keys:
        missing = [k for k in required_keys if k not in obj]
        if missing:
            raise ValueError(f"缺少字段: {missing}；原始: {obj}")
    return obj


def _hydrate_openai_env() -> None:
    if (os.environ.get("OPENAI_API_KEY") or "").strip():
        return
    cn = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    sea = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    region = (os.environ.get("JACHIN_ACTIVE_REGION") or "CN").strip().upper()
    if region == "SEA":
        key = (os.environ.get("DASHSCOPE_API_KEY_SEA") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
        base = (os.environ.get("DASHSCOPE_API_BASE_SEA") or os.environ.get("DASHSCOPE_API_BASE") or sea).strip()
    else:
        key = (os.environ.get("DASHSCOPE_API_KEY_CN") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
        base = (os.environ.get("DASHSCOPE_API_BASE_CN") or os.environ.get("DASHSCOPE_API_BASE") or cn).strip()
    if not key:
        key = (os.environ.get("QWEN_API_KEY") or "").strip()
    if key:
        os.environ["OPENAI_API_KEY"] = key
        if base and not (os.environ.get("OPENAI_BASE_URL") or "").strip():
            os.environ["OPENAI_BASE_URL"] = base.rstrip("/")


def _default_vision_model() -> str:
    for key in ("VIDEO_GEN_AGENT_MODEL", "CALCULATOR_AGENT_MODEL", "INTENT_GATEWAY_MULTIMODAL_MODEL"):
        v = (os.environ.get(key) or "").strip()
        if v:
            m = v.strip()
            if m.lower().startswith("dashscope/"):
                m = m.split("/", 1)[1].strip()
            return m
    return "qwen-vl-max"


def _openai_client():
    _hydrate_openai_env()
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY / DASHSCOPE_API_KEY")
    from openai import OpenAI

    base = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
    return OpenAI(api_key=api_key, base_url=base)


def _annotated_image_part(frame: HolographicFrame) -> dict[str, Any]:
    if frame.image_data_url and (
        frame.annotated_image_path.startswith("mock:")
        or frame.annotated_image_path == "data_url"
    ):
        return {"type": "image_url", "image_url": {"url": frame.image_data_url}}
    p = Path(frame.annotated_image_path)
    if not p.is_file():
        raise RuntimeError(f"标注图不存在: {frame.annotated_image_path}")
    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def vl_plan_json(
    prompt: str,
    frame: HolographicFrame,
    *,
    model: str,
    required_keys: tuple[str, ...],
    phase_tag: str,
) -> dict[str, Any]:
    """VLM 读标注图，解析为 JSON 对象；禁止坐标字段。"""
    client = _openai_client()
    last_err = ""
    for attempt in range(1, MAX_VL_RETRIES + 1):
        extra = ""
        if last_err:
            extra = f"\n\n【上次解析失败】{last_err}\n请只输出要求的 JSON 对象，不要 markdown，不要 (x,y)。"
        logger.info("[%s] VLM Plan 请求 model=%s 尝试 %d/%d", phase_tag, model, attempt, MAX_VL_RETRIES)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt + extra},
                        _annotated_image_part(frame),
                    ],
                }
            ],
            temperature=0.05 if attempt > 1 else 0.1,
            max_tokens=512,
        )
        content = (resp.choices[0].message.content or "").strip()
        logger.info("[%s] VLM 原始回复: %s", phase_tag, content[:1500])
        if re.search(r'"x"\s*:|"y"\s*:|center_x|center_y', content, re.I):
            last_err = "禁止输出坐标；只输出 element_id 整数字段"
            continue
        try:
            obj = parse_json_object(content, required_keys=required_keys)
            for k, v in obj.items():
                if k.endswith("_id") or k == "element_id":
                    int(v)
            return obj
        except (ValueError, json.JSONDecodeError, TypeError) as e:
            last_err = str(e)
            logger.warning("[%s] JSON 解析失败: %s", phase_tag, e)
    raise RuntimeError(f"[{phase_tag}] VLM Plan 失败: {last_err}")


def vl_plan_text(prompt: str, frame: HolographicFrame, *, model: str, phase_tag: str) -> str:
    client = _openai_client()
    logger.info("[%s] VLM Verify 请求 model=%s", phase_tag, model)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    _annotated_image_part(frame),
                ],
            }
        ],
        temperature=0.0,
        max_tokens=512,
    )
    answer = (resp.choices[0].message.content or "").strip()
    logger.info("[%s] VLM 复核: %s", phase_tag, answer)
    return answer


# ---------------------------------------------------------------------------
# Observe / Act 基础工具（对外 API）
# ---------------------------------------------------------------------------


def _build_elements_dict(elements: list[dict[str, Any]]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for row in elements:
        try:
            eid = int(row["id"])
            cx, cy = row.get("center_x"), row.get("center_y")
            if cx is None or cy is None:
                continue
            out[eid] = {"center_x": int(cx), "center_y": int(cy)}
        except (TypeError, ValueError, KeyError):
            continue
    return out


def _load_elements_with_bbox(work_dir: str | None, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """观测 payload 常为精简表无 bbox；优先从 parsed_result.json 读全量元素。"""
    if not work_dir:
        return fallback
    p = Path(work_dir) / "parsed_result.json"
    if not p.is_file():
        return fallback
    try:
        full = json.loads(p.read_text(encoding="utf-8"))
        rich = full.get("elements") or []
        if isinstance(rich, list) and len(rich) >= MIN_OMNI_ELEMENTS:
            return rich
    except Exception:
        pass
    return fallback


def _parse_holographic_observation(raw: str) -> HolographicFrame:
    from core.mcp_multimodal_result import parse_multimodal_observation_payload

    text, urls = parse_multimodal_observation_payload(raw)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return HolographicFrame(False, "", {}, error="observation_not_json")
    if not obj.get("ok"):
        return HolographicFrame(
            False,
            "",
            {},
            error=str(obj.get("error") or "parse_failed"),
        )
    work_dir = obj.get("work_dir")
    elements = _load_elements_with_bbox(
        str(work_dir) if work_dir else None,
        obj.get("elements") or [],
    )
    if work_dir and len(elements) < MIN_OMNI_ELEMENTS:
        p = Path(work_dir) / "parsed_result.json"
        if p.is_file():
            try:
                full = json.loads(p.read_text(encoding="utf-8"))
                elements = full.get("elements_llm") or full.get("elements") or elements
            except Exception:
                pass
    elements_dict = _build_elements_dict(elements)
    raw_ed = obj.get("elements_dict") or {}
    if isinstance(raw_ed, dict) and raw_ed:
        merged: dict[int, dict[str, int]] = {}
        for k, v in raw_ed.items():
            try:
                merged[int(k)] = {
                    "center_x": int(v["center_x"]),
                    "center_y": int(v["center_y"]),
                }
            except (TypeError, ValueError, KeyError):
                continue
        if merged:
            elements_dict = merged

    ann = obj.get("annotated_image_path") or ""
    if work_dir and not ann:
        for name in ("parsed_output.jpg", "annotated.jpg"):
            cand = Path(work_dir) / name
            if cand.is_file():
                ann = str(cand)
                break

    region = None
    note = str(obj.get("capture_note") or "")
    if "window_region=" in note:
        m = re.search(r"window_region=\((\d+),\s*(\d+),\s*(\d+),\s*(\d+)\)", note)
        if m:
            region = tuple(int(m.group(i)) for i in range(1, 5))

    if not ann and not urls:
        return HolographicFrame(False, "", {}, error="no_annotated_image")

    return HolographicFrame(
        ok=True,
        annotated_image_path=str(ann) if ann else "data_url",
        elements_dict=elements_dict,
        window_region=region,
        elements=elements,
        image_data_url=urls[0] if urls else None,
        work_dir=str(work_dir) if work_dir else None,
    )


def _mock_holographic_screen() -> HolographicFrame:
    """按 Phase 返回预设 ID（联调 VLM / Act 链路，不移动真实鼠标）。"""
    global _mock_phase
    _mock_phase += 1
    phase = _mock_phase

    # 各 Phase 预设：library=5, image=34, confirm=45, input=56, generate=67
    presets: dict[int, dict[int, dict[str, int]]] = {
        1: {
            5: {"center_x": 200, "center_y": 400},
            10: {"center_x": 100, "center_y": 100},
        },
        2: {
            34: {"center_x": 300, "center_y": 350},
            45: {"center_x": 700, "center_y": 500},
        },
        3: {
            56: {"center_x": 400, "center_y": 300},
            67: {"center_x": 150, "center_y": 600},
        },
        4: {
            80: {"center_x": 500, "center_y": 200},
        },
    }
    elements_dict = presets.get(phase, presets[4])
    out_dir = ROOT / "scripts" / "_video_gen_mock"
    out_dir.mkdir(parents=True, exist_ok=True)
    img_path = out_dir / f"mock_phase{phase}.png"
    try:
        from PIL import Image, ImageDraw

        im = Image.new("RGB", (900, 700), (28, 28, 32))
        dr = ImageDraw.Draw(im)
        dr.text((20, 20), f"Mock Phase {phase}", fill=(200, 200, 200))
        for eid, pt in elements_dict.items():
            cx, cy = pt["center_x"], pt["center_y"]
            dr.rectangle([cx - 40, cy - 20, cx + 40, cy + 20], outline=(255, 0, 0), width=2)
            dr.text((cx - 35, cy - 18), str(eid), fill=(255, 0, 0))
        im.save(img_path)
        du = f"data:image/png;base64,{base64.b64encode(img_path.read_bytes()).decode('ascii')}"
    except Exception:
        du = None

    logger.warning("[eye] MOCK Phase %d elements=%s", phase, list(elements_dict.keys()))
    return HolographicFrame(
        ok=True,
        annotated_image_path=f"mock:{img_path}",
        elements_dict=elements_dict,
        image_data_url=du,
        window_region=(0, 0, 900, 700),
    )


def get_holographic_screen(*, use_mock: bool | None = None) -> tuple[str, dict[int, dict[str, int]]]:
    """
    Observe：全屏截屏 + OmniParser → 标注图路径与 elements_dict。

    Returns:
        (annotated_image_path, elements_dict)
        elements_dict: {element_id: {"center_x": int, "center_y": int}}
    """
    mock = use_mock if use_mock is not None else _env_mock()
    if mock:
        frame = _mock_holographic_screen()
    else:
        from l3_client.local_mcps.holographic_screen_mcp.session_service import (
            get_holographic_screen_service,
        )

        try:
            bt = float(os.environ.get("VIDEO_GEN_BBOX_THRESHOLD") or str(OMNI_BOX_THRESHOLD))
        except ValueError:
            bt = OMNI_BOX_THRESHOLD
        try:
            iou = float(os.environ.get("VIDEO_GEN_IOU_THRESHOLD") or str(OMNI_IOU_THRESHOLD))
        except ValueError:
            iou = OMNI_IOU_THRESHOLD

        capture_win = (os.environ.get("VIDEO_GEN_CAPTURE_WINDOW") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        title_kw = tuple(
            k.strip()
            for k in (os.environ.get("VIDEO_GEN_WINDOW_TITLE") or "Chrome,Edge,社媒,视频").split(",")
            if k.strip()
        )
        logger.info(
            "[eye] get_holographic_screen %s bbox=%.2f iou=%.2f …",
            "窗口" if capture_win else "全屏",
            bt,
            iou,
        )
        t0 = time.perf_counter()
        raw = get_holographic_screen_service().get_holographic_screen(
            capture_window=capture_win,
            window_title_keywords=title_kw if capture_win else None,
            bbox_threshold=bt,
            iou_threshold=iou,
        )
        logger.info("[eye] 完成 (%.1fs)", time.perf_counter() - t0)
        frame = _parse_holographic_observation(raw)
        if frame.image_data_url and frame.annotated_image_path == "data_url":
            # 保持 path 占位，VLM 用 data_url
            pass

    if not frame.ok:
        raise RuntimeError(f"get_holographic_screen 失败: {frame.error}")

    logger.info(
        "[eye] 标注图=%s elements_dict=%d 项 IDs=%s",
        frame.annotated_image_path,
        len(frame.elements_dict),
        sorted(frame.elements_dict.keys())[:40],
    )
    if len(frame.elements_dict) < MIN_OMNI_ELEMENTS:
        logger.warning(
            "[eye] 元素过少（%d），可调 VIDEO_GEN_BBOX_THRESHOLD / IOU_THRESHOLD",
            len(frame.elements_dict),
        )

    global _CACHED_FRAME
    _CACHED_FRAME = frame

    path = frame.annotated_image_path
    if path.startswith("mock:"):
        path = path.split(":", 1)[1]
    return path, frame.elements_dict


_CACHED_FRAME: HolographicFrame | None = None


def _get_cached_frame() -> HolographicFrame:
    if _CACHED_FRAME is None:
        raise RuntimeError("请先调用 get_holographic_screen()")
    return _CACHED_FRAME


def _to_screen_xy(
    cx: int,
    cy: int,
    window_region: tuple[int, int, int, int] | None,
) -> tuple[int, int]:
    if not window_region:
        return cx, cy
    left, top, _w, _h = window_region
    return left + cx, top + cy


def physical_click(
    element_id: int,
    elements_dict: dict[int, dict[str, int]],
    *,
    skip_real: bool = False,
    label: str = "",
) -> dict[str, Any]:
    """
    根据 element_id 查 elements_dict，PyAutoGUI 点击屏幕坐标。
    """
    eid = int(element_id)
    if eid not in elements_dict:
        msg = (
            f"element_id={eid} 不在 elements_dict；"
            f"可用={sorted(elements_dict.keys())[:30]}"
        )
        logger.error("[hand] %s", msg)
        return {"ok": False, "error": msg, "element_id": eid}

    row = elements_dict[eid]
    detail = _element_row(_CACHED_FRAME, eid)
    if detail:
        cx, cy = _element_center(detail)
    else:
        cx, cy = int(row["center_x"]), int(row["center_y"])
    frame = _CACHED_FRAME
    region = frame.window_region if frame else None
    sx, sy = _to_screen_xy(cx, cy, region)

    tag = label or f"id={eid}"
    _log_click_target(eid, tag)
    logger.info("[hand] physical_click %s → 窗口内(%d,%d) 屏幕(%d,%d)", tag, cx, cy, sx, sy)

    if skip_real:
        return {"ok": True, "element_id": eid, "x": sx, "y": sy, "skipped": True}

    try:
        import pyautogui
    except ImportError as e:
        return {"ok": False, "error": f"pyautogui_not_installed:{e}"}

    pyautogui.FAILSAFE = True
    try:
        pyautogui.moveTo(sx, sy, duration=0.15)
        pyautogui.click()
    except Exception as e:
        return {"ok": False, "error": repr(e), "x": sx, "y": sy}
    return {"ok": True, "element_id": eid, "x": sx, "y": sy}


def type_text(text: str, *, skip_real: bool = False) -> dict[str, Any]:
    """剪贴板粘贴（Win）或 pyautogui.write 输入文本。"""
    logger.info("[hand] type_text len=%d preview=%r", len(text), text[:80])
    if skip_real:
        return {"ok": True, "skipped": True, "text": text}

    try:
        import pyautogui
    except ImportError as e:
        return {"ok": False, "error": f"pyautogui_not_installed:{e}"}

    pyautogui.FAILSAFE = True
    if sys.platform == "win32":
        try:
            import pyperclip

            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.05)
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.25)
            return {"ok": True, "method": "clipboard"}
        except ImportError:
            logger.warning("[hand] pyperclip 未安装，回退 pyautogui.write")

    try:
        pyautogui.write(text, interval=0.02)
        return {"ok": True, "method": "write"}
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def _env_mock() -> bool:
    return (os.environ.get("VIDEO_GEN_AGENT_MOCK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _element_row(frame: HolographicFrame | None, eid: int) -> dict[str, Any] | None:
    if not frame:
        return None
    for row in frame.elements:
        try:
            if int(row.get("id")) == eid:
                return row
        except (TypeError, ValueError):
            continue
    return None


def _bbox_area(row: dict[str, Any]) -> int:
    b = row.get("bbox_xyxy_pixels") or []
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return abs(int(b[2]) - int(b[0])) * abs(int(b[3]) - int(b[1]))
    return 0


def _bbox_span_x(row: dict[str, Any]) -> int:
    b = row.get("bbox_xyxy_pixels") or []
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return abs(int(b[2]) - int(b[0]))
    return 0


def _element_center(row: dict[str, Any]) -> tuple[int, int]:
    b = row.get("bbox_xyxy_pixels") or []
    if isinstance(b, (list, tuple)) and len(b) >= 4:
        return (int((int(b[0]) + int(b[2])) / 2), int((int(b[1]) + int(b[3])) / 2))
    cx = row.get("center_x")
    cy = row.get("center_y")
    if cx is not None and cy is not None:
        return int(cx), int(cy)
    cen = row.get("center_xy_pixels") or [0, 0]
    return int(cen[0]), int(cen[1])


def _iter_element_rows(frame: HolographicFrame) -> list[dict[str, Any]]:
    return list(frame.elements or [])


def _material_modal_open(frame: HolographicFrame) -> bool:
    """弹窗打开时，屏幕中部偏右会出现多块素材缩略图（宽框、x>700）。"""
    thumbs = 0
    for row in _iter_element_rows(frame):
        cx, cy = _element_center(row)
        if cx < 700 or cy < 180 or cy > 720:
            continue
        if _bbox_area(row) >= 8000 or _bbox_span_x(row) >= 80:
            thumbs += 1
    return thumbs >= 3


def _heuristic_library_btn(frame: HolographicFrame) -> int | None:
    best: tuple[int, int, int] | None = None  # score, area, eid
    for row in _iter_element_rows(frame):
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        cx, cy = _element_center(row)
        if not (380 <= cx <= 720 and 660 <= cy <= 780):
            continue
        content = str(row.get("content") or "")
        area = _bbox_area(row)
        score = area
        if "Image" in content or "MedRr" in content or "素材" in content:
            score += 50000
        if _bbox_span_x(row) >= 180:
            score += 30000
        if best is None or score > best[0]:
            best = (score, area, eid)
    return best[2] if best else None


def _heuristic_confirm_btn(frame: HolographicFrame) -> int | None:
    if not _material_modal_open(frame):
        return None
    best: tuple[int, int] | None = None  # score, eid
    for row in _iter_element_rows(frame):
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        cx, cy = _element_center(row)
        span = _bbox_span_x(row)
        area = _bbox_area(row)
        content = str(row.get("content") or "")
        if cy < 820 or span < 60:
            continue
        score = area + span * 20
        if "确认" in content:
            score += 80000
        if best is None or score > best[0]:
            best = (score, eid)
    return best[1] if best else None


def _heuristic_prompt_input(frame: HolographicFrame) -> int | None:
    best: tuple[int, int] | None = None
    for row in _iter_element_rows(frame):
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        cx, cy = _element_center(row)
        span = _bbox_span_x(row)
        if not (250 <= cx <= 900 and 760 <= cy <= 860):
            continue
        if span < 200:
            continue
        score = span * 100 + _bbox_area(row)
        if best is None or score > best[0]:
            best = (score, eid)
    return best[1] if best else None


def _heuristic_generate_btn(frame: HolographicFrame) -> int | None:
    best: tuple[int, int] | None = None
    for row in _iter_element_rows(frame):
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        cx, cy = _element_center(row)
        span = _bbox_span_x(row)
        if not (320 <= cx <= 680 and 940 <= cy <= 1005):
            continue
        if span < 120:
            continue
        score = span * 100 + _bbox_area(row)
        if best is None or score > best[0]:
            best = (score, eid)
    return best[1] if best else None


def _merge_plan_id(
    vlm_id: int,
    heuristic_id: int | None,
    elements_dict: dict[int, dict[str, int]],
    *,
    role: str,
) -> int:
    if heuristic_id is None:
        return vlm_id
    if heuristic_id not in elements_dict:
        return vlm_id
    if vlm_id == heuristic_id:
        return vlm_id
    logger.warning(
        "[brain] %s: VLM id=%s 与几何启发 id=%s 不一致，采用启发式",
        role,
        vlm_id,
        heuristic_id,
    )
    _log_click_target(heuristic_id, f"{role}(heuristic)")
    return heuristic_id


def _focus_target_window() -> None:
    keys = tuple(
        k.strip()
        for k in (os.environ.get("VIDEO_GEN_WINDOW_TITLE") or "Chrome,Edge,社媒").split(",")
        if k.strip()
    )
    if sys.platform != "win32":
        return
    try:
        from l3_client.local_mcps.holographic_screen_mcp.window_capture import (
            _find_window_rect_win32,
        )

        rect = _find_window_rect_win32(keys)
        if not rect:
            logger.warning("[hand] 未找到浏览器窗口 title 含 %s，跳过聚焦", keys)
            return
        import ctypes

        user32 = ctypes.windll.user32
        matches: list[tuple[int, str]] = []

        def _cb(hwnd: int, _lp: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            title = (buf.value or "").strip().lower()
            if any(k.lower() in title for k in keys):
                matches.append((hwnd, buf.value or ""))
            return True

        from ctypes import wintypes

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        if matches:
            hwnd, title = matches[0]
            user32.SetForegroundWindow(hwnd)
            logger.info("[hand] 已聚焦窗口: %s", title)
            time.sleep(0.35)
    except Exception as e:
        logger.warning("[hand] 聚焦窗口失败: %s", e)


def _sync_elements_dict_from_rows(
    frame: HolographicFrame,
    elements_dict: dict[int, dict[str, int]],
) -> dict[int, dict[str, int]]:
    """用带 bbox 的全量元素刷新坐标（点击取框中心）。"""
    out = dict(elements_dict)
    for row in _iter_element_rows(frame):
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        cx, cy = _element_center(row)
        out[eid] = {"center_x": cx, "center_y": cy}
    return out


def _elements_catalog_for_prompt(frame: HolographicFrame, *, hints: str = "") -> str:
    """把 OmniParser OCR/框信息写入 Prompt，降低 VLM 误选顶栏小字 ID。"""
    lines: list[str] = []
    for row in sorted(frame.elements, key=lambda r: int(r.get("id", 0))):
        try:
            eid = int(row["id"])
        except (TypeError, ValueError):
            continue
        cx = row.get("center_x")
        cy = row.get("center_y")
        if cx is None or cy is None:
            cen = row.get("center_xy_pixels") or [0, 0]
            cx, cy = cen[0], cen[1]
        content = str(row.get("content") or "").strip()[:48]
        lines.append(
            f"  id={eid} 中心≈({int(cx)},{int(cy)})  框面积={_bbox_area(row)}  OCR={content!r}"
        )
    body = "\n".join(lines[:70])
    return (
        "\n【本屏元素目录 — 红框数字 ID 与下表一致；每次 Observe 后 ID 会重新编号】\n"
        f"{body}\n"
        f"{hints}\n"
    )


def _log_click_target(eid: int, label: str) -> None:
    row = _element_row(_CACHED_FRAME, eid)
    if not row:
        logger.warning("[hand] id=%s (%s) 无元素明细", eid, label)
        return
    logger.info(
        "[hand] 目标 %s → id=%s | OCR=%r | 中心=(%s,%s) | bbox=%s",
        label,
        eid,
        row.get("content"),
        row.get("center_x"),
        row.get("center_y"),
        row.get("bbox_xyxy_pixels"),
    )


def _require_ids(plan: dict[str, Any], elements_dict: dict[int, dict[str, int]], keys: tuple[str, ...]) -> dict[str, int]:
    out: dict[str, int] = {}
    for k in keys:
        eid = int(plan[k])
        if eid not in elements_dict:
            raise ValueError(f"Plan 字段 {k}={eid} 不在当前 elements_dict")
        out[k] = eid
        _log_click_target(eid, k)
    return out


# ---------------------------------------------------------------------------
# 四阶段 Phase
# ---------------------------------------------------------------------------


def _skip_image_select() -> bool:
    return (os.environ.get("VIDEO_GEN_SKIP_IMAGE_SELECT") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def phase1_open_material_library(
    *,
    model: str,
    use_mock: bool,
    skip_real: bool,
) -> dict[str, int]:
    logger.info("=" * 60)
    logger.info("[Phase 1] 打开素材库 — Observe → Plan → Act")
    logger.info("=" * 60)

    ann, elements_dict = get_holographic_screen(use_mock=use_mock)

    frame = _get_cached_frame()
    elements_dict = _sync_elements_dict_from_rows(frame, elements_dict)
    if not skip_real:
        _focus_target_window()
    catalog = _elements_catalog_for_prompt(
        frame,
        hints=(
            "【选 ID 规则】\n"
            "- 目标：左侧配置区、参考图下方的**大块按钮**，框住整颗按钮（粉/紫描边），"
            "其上可见中文『从素材库选择』。\n"
            "- 不要选：浏览器顶栏（y 通常 < 80）的 Cursor/OmniParser 小 OCR 框；"
            "不要选左侧最窄图标栏的小方块（仅图标、无『从素材库选择』字样）。\n"
            "- 优先选框面积较大的 id，且中心 y 约在 650~720、x 约在 400~550 一带（以本图为准）。"
        ),
    )
    prompt = (
        "这是一张视频生成网页的截图，红框数字为 element_id。\n"
        f"{catalog}\n"
        "请找到『从素材库选择』按钮对应的红框 ID（必须是按钮本体，不是零散 OCR 小字）。\n"
        "**禁止输出 (x,y)！** 仅 JSON：`{\"library_btn_id\": 38}`\n"
    )
    plan = vl_plan_json(
        prompt,
        frame,
        model=model,
        required_keys=("library_btn_id",),
        phase_tag="Phase1",
    )
    ids = _require_ids(plan, elements_dict, ("library_btn_id",))
    hid = _heuristic_library_btn(frame)
    ids["library_btn_id"] = _merge_plan_id(
        ids["library_btn_id"],
        hid,
        elements_dict,
        role="library_btn_id",
    )

    res = physical_click(
        ids["library_btn_id"],
        elements_dict,
        skip_real=skip_real,
        label="从素材库选择",
    )
    if not res.get("ok"):
        raise RuntimeError(f"Phase1 点击失败: {res}")

    logger.info("[Phase 1] 完成，等待弹窗 2s …")
    time.sleep(2)
    return ids


def phase2_select_image_and_confirm(
    *,
    model: str,
    use_mock: bool,
    skip_real: bool,
) -> dict[str, int]:
    logger.info("=" * 60)
    logger.info("[Phase 2] 弹窗选图并确认 — Observe → Plan → Act")
    logger.info("=" * 60)

    ann, elements_dict = get_holographic_screen(use_mock=use_mock)

    frame = _get_cached_frame()
    elements_dict = _sync_elements_dict_from_rows(frame, elements_dict)
    if not skip_real:
        _focus_target_window()

    modal = _material_modal_open(frame)
    logger.info("[Phase 2] 素材弹窗检测: %s", "已打开" if modal else "未检测到（可能 Phase1 未点开）")
    if not modal and not skip_real:
        retry_id = _heuristic_library_btn(frame)
        if retry_id is not None:
            logger.warning("[Phase 2] 弹窗未开，重试点击素材库 id=%s", retry_id)
            physical_click(retry_id, elements_dict, skip_real=False, label="从素材库选择(重试)")
            time.sleep(2.0)
            ann, elements_dict = get_holographic_screen(use_mock=use_mock)
            frame = _get_cached_frame()
            elements_dict = _sync_elements_dict_from_rows(frame, elements_dict)
            modal = _material_modal_open(frame)
            logger.info("[Phase 2] 重试后弹窗: %s", "已打开" if modal else "仍未打开")

    skip_img = _skip_image_select()
    required: tuple[str, ...] = ("confirm_btn_id",) if skip_img else ("image_id", "confirm_btn_id")
    catalog = _elements_catalog_for_prompt(
        frame,
        hints=(
            "【选 ID 规则】\n"
            + (
                "- 产品默认已选中第一张素材，**只需** confirm_btn_id（底部宽按钮『确认选择』），勿点零散 OCR。\n"
                if skip_img
                else "- image_id：弹窗内任意一张素材缩略图大框；confirm_btn_id：底部『确认选择』宽按钮。\n"
            )
            + "- 勿选 y≈760 的「1216」等小字碎片；确认按钮通常 y>850 且框较宽。"
        ),
    )
    prompt = (
        "这是素材选择弹窗截图，红框为 element_id。\n"
        f"{catalog}\n"
        + (
            "请只返回 confirm_btn_id。\n"
            if skip_img
            else "请返回 image_id 与 confirm_btn_id。\n"
        )
        + "**禁止 (x,y)。** "
        + (
            '仅 JSON：`{"confirm_btn_id": 48}`'
            if skip_img
            else '仅 JSON：`{"image_id": 34, "confirm_btn_id": 45}`'
        )
    )
    plan = vl_plan_json(
        prompt,
        frame,
        model=model,
        required_keys=required,
        phase_tag="Phase2",
    )
    ids: dict[str, int] = {}
    if "image_id" in plan:
        ids["image_id"] = int(plan["image_id"])
    ids["confirm_btn_id"] = int(plan["confirm_btn_id"])
    for k in required:
        if int(ids[k]) not in elements_dict:
            raise ValueError(f"Plan 字段 {k}={ids[k]} 不在当前 elements_dict")
        _log_click_target(int(ids[k]), k)

    if "image_id" in ids:
        ids["image_id"] = _merge_plan_id(
            ids["image_id"],
            None,
            elements_dict,
            role="image_id",
        )
    hid_c = _heuristic_confirm_btn(frame)
    ids["confirm_btn_id"] = _merge_plan_id(
        ids["confirm_btn_id"],
        hid_c,
        elements_dict,
        role="confirm_btn_id",
    )

    steps: list[tuple[str, str, str]] = []
    if "image_id" in ids and not skip_img:
        steps.append(("选图", "image", "image_id"))
    steps.append(("确认", "confirm", "confirm_btn_id"))

    for step, key, eid_key in steps:
        res = physical_click(
            ids[eid_key],
            elements_dict,
            skip_real=skip_real,
            label=key,
        )
        if not res.get("ok"):
            raise RuntimeError(f"Phase2 {step} 点击失败: {res}")
        logger.info("[Phase 2] %s 点击 element_id=%s 成功", step, ids[eid_key])
        time.sleep(0.5)

    logger.info("[Phase 2] 完成，等待弹窗关闭 2s …")
    time.sleep(2)
    return ids


def phase3_prompt_and_generate(
    *,
    model: str,
    use_mock: bool,
    skip_real: bool,
    prompt_text: str,
) -> dict[str, int]:
    logger.info("=" * 60)
    logger.info("[Phase 3] 输入提示词并生成 — Observe → Plan → Act")
    logger.info("=" * 60)

    ann, elements_dict = get_holographic_screen(use_mock=use_mock)

    frame = _get_cached_frame()
    elements_dict = _sync_elements_dict_from_rows(frame, elements_dict)
    if not skip_real:
        _focus_target_window()
    catalog = _elements_catalog_for_prompt(
        frame,
        hints=(
            "【选 ID 规则】\n"
            "- input_box_id：左侧『视频描述/运镜』下方的**多行输入框**大区域。\n"
            "- generate_btn_id：其下方绿色『立即生成』按钮大框（非顶栏、非右侧画布）。\n"
            "- 不要与 Phase1 的 ID 混淆：本屏 ID 已重新编号。"
        ),
    )
    prompt = (
        "这是视频生成主界面截图（弹窗已关闭）。\n"
        f"{catalog}\n"
        "请返回 input_box_id 与 generate_btn_id。\n"
        "**禁止 (x,y)。** 仅 JSON：`{\"input_box_id\": 45, \"generate_btn_id\": 47}`"
    )
    plan = vl_plan_json(
        prompt,
        frame,
        model=model,
        required_keys=("input_box_id", "generate_btn_id"),
        phase_tag="Phase3",
    )
    ids = _require_ids(plan, elements_dict, ("input_box_id", "generate_btn_id"))
    ids["input_box_id"] = _merge_plan_id(
        ids["input_box_id"],
        _heuristic_prompt_input(frame),
        elements_dict,
        role="input_box_id",
    )
    ids["generate_btn_id"] = _merge_plan_id(
        ids["generate_btn_id"],
        _heuristic_generate_btn(frame),
        elements_dict,
        role="generate_btn_id",
    )

    res = physical_click(
        ids["input_box_id"],
        elements_dict,
        skip_real=skip_real,
        label="输入框",
    )
    if not res.get("ok"):
        raise RuntimeError(f"Phase3 聚焦输入框失败: {res}")
    time.sleep(0.35)
    if not skip_real:
        physical_click(
            ids["input_box_id"],
            elements_dict,
            skip_real=False,
            label="输入框(双击聚焦)",
        )
    time.sleep(0.35)

    tres = type_text(prompt_text, skip_real=skip_real)
    if not tres.get("ok"):
        raise RuntimeError(f"Phase3 输入文本失败: {tres}")
    time.sleep(0.5)

    res = physical_click(
        ids["generate_btn_id"],
        elements_dict,
        skip_real=skip_real,
        label="立即生成",
    )
    if not res.get("ok"):
        raise RuntimeError(f"Phase3 点击生成失败: {res}")

    logger.info("[Phase 3] 完成，已提交提示词并点击生成")
    return ids


def phase4_verify(
    *,
    model: str,
    use_mock: bool,
    wait_sec: float = 3.0,
) -> str:
    logger.info("=" * 60)
    logger.info("[Phase 4] 结果复核 — Observe → Plan(Verify)")
    logger.info("=" * 60)

    logger.info("[Phase 4] 等待 %.1fs …", wait_sec)
    time.sleep(wait_sec)

    ann, elements_dict = get_holographic_screen(use_mock=use_mock)
    logger.info("[Phase 4] elements_dict %d 项", len(elements_dict))

    prompt = (
        "请观察当前画面（视频生成网页，带红色元素 ID 标注）。\n"
        "视频生成任务是否已成功提交并处于等待或生成中状态？\n"
        "请简要回答（是/否 + 一句依据）。"
    )
    frame = _get_cached_frame()
    return vl_plan_text(prompt, frame, model=model, phase_tag="Phase4")


def run_video_gen_test(
    *,
    use_mock: bool = False,
    skip_real_click: bool = False,
    model: str | None = None,
    prompt_text: str = DEFAULT_PROMPT,
) -> int:
    """
    端到端四阶段串行调度。返回进程退出码 0=成功。
    """
    global _mock_phase, _CACHED_FRAME
    _mock_phase = 0
    _CACHED_FRAME = None

    model = model or _default_vision_model()
    skip_real = skip_real_click or use_mock

    logger.info("Video Generation Agent E2E  model=%s mock=%s", model, use_mock)
    if not use_mock:
        logger.info(
            "提示: 请打开 AI 视频生成网页并保持在前台；默认全屏 Observe。"
            " 可设 VIDEO_GEN_CAPTURE_WINDOW=1 与 VIDEO_GEN_WINDOW_TITLE=Chrome,社媒 仅截浏览器。"
            " Phase2 默认跳过选图（首张已选），仅点确认。"
        )

    try:
        phase1_open_material_library(
            model=model, use_mock=use_mock, skip_real=skip_real
        )
        phase2_select_image_and_confirm(
            model=model, use_mock=use_mock, skip_real=skip_real
        )
        phase3_prompt_and_generate(
            model=model,
            use_mock=use_mock,
            skip_real=skip_real,
            prompt_text=prompt_text,
        )
        conclusion = phase4_verify(model=model, use_mock=use_mock)
        logger.info("=" * 60)
        logger.info("[Final] Phase4 复核结论: %s", conclusion)
        logger.info("=" * 60)
        return 0
    except Exception:
        logger.exception("[fail] 测试中止")
        return 1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(description="AI 视频生成网页视觉具身 E2E 测试")
    ap.add_argument("--mock", action="store_true", help="Mock Observe/VLM（不点真实鼠标）")
    ap.add_argument("--mock-click-skip", action="store_true", help="不移动鼠标")
    ap.add_argument("--model", default=None, help="VLM，默认 qwen-vl-max")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="Phase3 视频描述文案")
    args = ap.parse_args()

    use_mock = args.mock or _env_mock()
    skip = args.mock_click_skip or use_mock

    return run_video_gen_test(
        use_mock=use_mock,
        skip_real_click=skip,
        model=args.model,
        prompt_text=args.prompt,
    )


if __name__ == "__main__":
    raise SystemExit(main())
