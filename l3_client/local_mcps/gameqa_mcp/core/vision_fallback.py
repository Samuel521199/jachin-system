"""
YOLO 无框时的感知降级：OCR 外接矩形中心 → 语义键；可选 DashScope VL 打点。

与环境变量契约（节选）::

    GAMEQA_OCR_ANCHOR_ENABLED      默认 1；设为 0/false/off 关闭 OCR 锚点
    GAMEQA_OCR_ANCHOR_MAP         可选追加 ``短语=语义键``，`|` 分隔，如 ``claim now=Play_Now_Btn``
    GAMEQA_VL_FALLBACK             1/true/on 时在 OCR 锚点仍全空且无 mock 时再调一次 VL
    GAMEQA_VL_MODEL                默认 qwen-vl-max（DashScope compatible-mode）
    GAMEQA_VL_API_BASE             覆盖默认 compatible-mode URL（与海 / 中国区密钥一致）
    DASHSCOPE_API_KEY              或通过 GAMEQA_VL_API_KEY（仅 VL 打点）
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Any

from .ocr_engine import ocr_line_boxes_from_png

logger = logging.getLogger("gameqa.vision_fallback")

_JSON_OBJ_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*}[^{}]*)*\}", re.DOTALL)


def _env_bool(name: str, default: bool = True) -> bool:
    v = (os.environ.get(name) or "").strip().lower()
    if not v:
        return default
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return default


def _is_mock_vision(raw_notes: str) -> bool:
    s = (raw_notes or "").lower()
    return "mock_vision_fallback" in s


def _default_phrase_key_table() -> list[tuple[str, str]]:
    """(小写短语需被 OCR 行包含, 与 YOLO 类名对齐的语义键) — 长短语优先在外层排序。"""
    return [
        ("continue with guest", "Guest_Access_Play_Btn"),
        ("continue as guest", "Guest_Access_Play_Btn"),
        ("play as guest", "Guest_Access_Play_Btn"),
        ("guest access", "Guest_Access_Play_Btn"),
        ("play now", "Play_Now_Btn"),
        ("claim now", "Play_Now_Btn"),
        ("tongits king", "Tongits_King_Entry"),
        ("drop", "Tongits_Action_Drop_Btn"),
        ("fight", "Tongits_Action_Fight_Btn"),
        ("group", "Tongits_Action_Group_Btn"),
        ("dump", "Tongits_Action_Dump_Btn"),
        ("game rules", "Game_Rules_Btn"),
    ]


def _parse_anchor_map_env() -> list[tuple[str, str]]:
    raw = (os.environ.get("GAMEQA_OCR_ANCHOR_MAP") or "").strip()
    if not raw:
        return []
    pairs: list[tuple[str, str]] = []
    for part in raw.split("|"):
        p = part.strip()
        if "=" not in p:
            continue
        a, b = p.split("=", 1)
        phrase = a.strip().lower()
        key = b.strip().replace(" ", "_")
        if phrase and key:
            pairs.append((phrase, key))
    return pairs


def _allocate_element_key(preferred_full_key: str, taken: set[str]) -> str:
    k = preferred_full_key.strip() or "OcrAnchor_Unknown"
    if k not in taken:
        return k
    n = 1
    while f"{k}_{n}" in taken:
        n += 1
    return f"{k}_{n}"


def _ocr_anchor_elements_from_lines(
    lines: list[dict[str, Any]],
    taken_keys: set[str],
) -> dict[str, tuple[float, float]]:
    phrases = sorted(
        list(_parse_anchor_map_env()) + _default_phrase_key_table(),
        key=lambda x: len(x[0]),
        reverse=True,
    )
    merged: dict[str, tuple[float, float]] = {}
    consumed_line_indices: set[int] = set()

    for i, row in enumerate(lines):
        if i in consumed_line_indices:
            continue
        t = str(row.get("text") or "").strip().lower()
        if not t:
            continue
        for phrase, klass in phrases:
            if phrase in t:
                base_key = klass
                nk = _allocate_element_key(f"OcrAnchor_{base_key}", taken_keys | set(merged.keys()))
                merged[nk] = (float(row["cx"]), float(row["cy"]))
                consumed_line_indices.add(i)
                logger.info(
                    "[gameqa][ocr_anchor] %r → %s @ (%.1f,%.1f)",
                    phrase,
                    nk,
                    row["cx"],
                    row["cy"],
                )
                break
    return merged


def build_ocr_anchor_fallback(
    png: bytes,
    base_elements: dict[str, tuple[float, float]],
    raw_notes: str,
) -> tuple[dict[str, tuple[float, float]], str]:
    if not _env_bool("GAMEQA_OCR_ANCHOR_ENABLED", True):
        return {}, "ocr_anchor_skip:GAMEQA_OCR_ANCHOR_ENABLED=0"
    if _is_mock_vision(raw_notes):
        return {}, "ocr_anchor_skip:mock_vision"
    if base_elements:
        return {}, "ocr_anchor_skip:yolo_nonempty"
    lines, ocr_note = ocr_line_boxes_from_png(png)
    if not lines:
        return {}, f"ocr_anchor_skip:no_lines ({ocr_note})"
    added = _ocr_anchor_elements_from_lines(lines, set())
    if not added:
        return {}, f"ocr_anchor_skip:no_keyword_match ({ocr_note})"
    note = f"ocr_anchor_fallback keys={list(added.keys())!r} ({ocr_note})"
    return added, note


def _extract_json_obj(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for m in _JSON_OBJ_RE.finditer(s):
        frag = m.group(0)
        try:
            return json.loads(frag)
        except json.JSONDecodeError:
            continue
    return None


def _dashscope_vl_sync(
    png: bytes,
    *,
    viewport_w: int,
    viewport_h: int,
) -> tuple[dict[str, tuple[float, float]], str]:
    if (os.environ.get("GAMEQA_VL_FALLBACK") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {}, "vl_skip:GAMEQA_VL_FALLBACK off"

    api_key = (
        (os.environ.get("GAMEQA_VL_API_KEY") or "").strip()
        or (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
        or (os.environ.get("QWEN_API_KEY") or "").strip()
    )
    if not api_key:
        return {}, "vl_skip:no_api_key"

    model = (os.environ.get("GAMEQA_VL_MODEL") or "qwen-vl-max").strip()
    api_base = (
        (os.environ.get("GAMEQA_VL_API_BASE") or "").strip().rstrip("/")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    url = f"{api_base}/chat/completions"

    b64 = base64.standard_b64encode(png).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"
    vw, vh = max(16, int(viewport_w)), max(16, int(viewport_h))
    user_text = (
        f"Screenshot is a mobile game/web viewport {vw}x{vh} CSS pixels (origin top-left). "
        "Find the primary clickable buttons the user asked about: Continue with Guest / guest login, "
        "Tongits entry, Play Now. Reply ONLY compact JSON:\n"
        '{"targets":[{"label":"snake_identifier","cx":<number>,"cy":<number>}],'
        '"notes":""}'
        "\nCoordinates must be viewport pixels, integers or floats. Omit targets you cannot ground."
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": user_text},
                ],
            }
        ],
        "max_tokens": 512,
    }
    timeout_s = 45.0
    try:
        v = os.environ.get("GAMEQA_VL_TIMEOUT_SEC")
        if v:
            timeout_s = max(10.0, min(120.0, float(v)))
    except ValueError:
        pass

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {}, f"vl_http_error:{getattr(e, 'code', '?')}:{err_body!r}"
    except Exception as e:
        logger.warning("[gameqa][vl_fallback] request failed: %s", e)
        return {}, f"vl_request_error:{e!r}"

    try:
        top = json.loads(raw)
        content = (
            (((top.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or ""
        )
    except Exception as e:
        return {}, f"vl_parse_top:{e!r}"

    parsed = _extract_json_obj(str(content))
    if not parsed or not isinstance(parsed, dict):
        return {}, f"vl_parse_json_fail content_head={str(content)[:200]!r}"

    targets = parsed.get("targets")
    if not isinstance(targets, list):
        return {}, "vl_no_targets_list"

    out: dict[str, tuple[float, float]] = {}
    safe = re.compile(r"[^\w\-]+")
    taken: set[str] = set()

    for i, t in enumerate(targets):
        if not isinstance(t, dict):
            continue
        lab = str(t.get("label") or f"target_{i}").strip()
        lab = safe.sub("_", lab).strip("_") or f"target_{i}"
        try:
            cx = float(t.get("cx"))
            cy = float(t.get("cy"))
        except (TypeError, ValueError):
            continue
        cx = max(0.0, min(float(vw), cx))
        cy = max(0.0, min(float(vh), cy))
        k = _allocate_element_key(f"VlAnchor_{lab}", taken)
        taken.add(k)
        out[k] = (cx, cy)

    if not out:
        return {}, "vl_empty_targets_after_parse"

    vn = parsed.get("notes")
    tail = ""
    if isinstance(vn, str) and vn.strip():
        tail = f" model_notes={vn.strip()[:200]}"
    return out, f"vl_fallback keys={list(out.keys())!r}{tail}"


def apply_perception_fallbacks(
    png: bytes,
    yolo_elements: dict[str, tuple[float, float]],
    *,
    raw_notes: str,
    viewport_wh: tuple[int, int],
) -> tuple[dict[str, tuple[float, float]], dict[str, str], str]:
    """
    合并 YOLO 结果与 OCR/VL 降级；返回 (merged, element_sources, extra_vision_notes 片段)。
    element_sources：键 → yolo | ocr_anchor | vl
    """
    merged = dict(yolo_elements)
    sources: dict[str, str] = {k: "yolo" for k in yolo_elements}
    blobs: list[str] = []

    ocr_added, ocr_blob = build_ocr_anchor_fallback(png, merged, raw_notes)
    if ocr_added:
        for k, v in ocr_added.items():
            if k not in merged:
                merged[k] = v
                sources[k] = "ocr_anchor"
        blobs.append(ocr_blob)

    # VL 仅在 YOLO+OCR 仍全无锚点时触发（单次 HTTP；失败则仅记 vision_notes）
    need_vl = not merged and not _is_mock_vision(raw_notes)
    vl_added: dict[str, tuple[float, float]] = {}
    vl_blob = ""
    if need_vl:
        vl_added, vl_blob = _dashscope_vl_sync(
            png, viewport_w=viewport_wh[0], viewport_h=viewport_wh[1]
        )
        if vl_added:
            for k, v in vl_added.items():
                merged[k] = v
                sources[k] = "vl"
            blobs.append(vl_blob)
        elif vl_blob:
            blobs.append(vl_blob)

    extra = "; ".join(blobs) if blobs else ""
    return merged, sources, extra
