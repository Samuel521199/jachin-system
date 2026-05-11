"""
视觉：YOLO(Ultralytics) 截图推理 → {语义名: (cx, cy)}；未配置模型或未装依赖时回退 Mock。

推理在 ``async_analyze`` 内经 ``asyncio.to_thread`` 执行，避免阻塞 Playwright/asyncio loop。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger("gameqa.vision_engine")

# 语义 key：仅保留字母数字与下划线
_SAFE_KEY_RE = re.compile(r"[^\w\-]+")


def _read_gameqa_viewport_wh() -> tuple[int, int]:
    """与 ``browser_engine.gameqa_playwright_viewport`` 默认值一致（360×760），便于 mock 坐标与视口对齐。"""
    try:
        w = int((os.environ.get("GAMEQA_VIEWPORT_WIDTH") or "360").strip())
    except ValueError:
        w = 360
    try:
        h = int((os.environ.get("GAMEQA_VIEWPORT_HEIGHT") or "760").strip())
    except ValueError:
        h = 760
    return max(16, w), max(16, h)


def _default_mock_elements() -> dict[str, tuple[float, float]]:
    """无 YOLO 时的兜底语义坐标：按当前视口比例放在底栏与顶部中间（竖屏 H5）。"""
    w, h = _read_gameqa_viewport_wh()
    return {
        "Btn_Fold": (w * 0.18, h * 0.93),
        "Btn_Call": (w * 0.50, h * 0.93),
        "Btn_Raise": (w * 0.82, h * 0.93),
        "Pot_Label": (w * 0.50, h * 0.26),
    }


@dataclass
class VisionResult:
    """单帧解析结果：元素名 -> 视口中心 (cx, cy) CSS 像素。"""

    elements: dict[str, tuple[float, float]]
    raw_notes: str
    #: 多行可读摘要（供 ``gameqa_skill_debug.log``）；含每框 class/conf/bbox/映射后的 key。
    yolo_debug: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "elements": {k: [round(x, 2), round(y, 2)] for k, (x, y) in self.elements.items()},
            "raw_notes": self.raw_notes,
        }


@dataclass
class _YOLOSingleton:
    """跨 VisionEngine 实例共享一份模型权重。"""

    lock: threading.Lock = field(default_factory=threading.Lock)
    loaded: bool = False
    model: Any = None
    names: dict[int, str] = field(default_factory=dict)
    backend: str = "mock"  # ultralytics | mock
    model_path: str = ""
    device: str = ""
    load_error: str = ""


_state = _YOLOSingleton()


class VisionEngine:
    """
    优先使用 Ultralytics YOLO（YOLO11 等）；环境变量::

        GAMEQA_YOLO_MODEL   必选（启用真实推理）：``.pt`` 权重路径或官方 hub 名如 ``yolo11n.pt``
        GAMEQA_YOLO_CONF    置信度阈值，默认 0.25
        GAMEQA_YOLO_DEVICE  可选 ``cpu`` / ``0`` / ``cuda:0``；不设则交由 ultralytics 自动选
        GAMEQA_YOLO_IMG_SIZE   可选 int，默认不强制（沿用模型预设）

    ``GAMEQA_YOLO_MODEL`` 未设置或加载失败时，使用与当前 ``GAMEQA_VIEWPORT_WIDTH/HEIGHT``（默认 360×760）
    对齐的兜底坐标，便于无 GPU/无权重联调。
    """

    def __init__(self, *, mock_elements: dict[str, tuple[float, float]] | None = None) -> None:
        self._mock = dict(mock_elements or _default_mock_elements())

    async def analyze_async(self, screenshot_png: bytes) -> VisionResult:
        """异步入口：YOLO/mock 均在 worker 线程执行。"""
        return await asyncio.to_thread(self.analyze_sync, screenshot_png)

    def analyze_sync(self, screenshot_png: bytes) -> VisionResult:
        if not screenshot_png or len(screenshot_png) < 32:
            return VisionResult(
                elements={},
                raw_notes="empty_or_invalid_png",
                yolo_debug="[YOLO]\n  status: empty_or_invalid_png (screenshot too small or missing)",
            )

        mp = (os.environ.get("GAMEQA_YOLO_MODEL") or "").strip()
        if not mp:
            return self._mock_result()

        err = self._ensure_yolo_loaded(mp)
        if err:
            logger.warning("[gameqa][vision] YOLO 不可用，回退 mock: %s", err)
            return self._mock_result(raw_suffix=f"fallback:{err}")

        return self._yolo_inference(screenshot_png)

    def _mock_result(self, raw_suffix: str = "") -> VisionResult:
        note = "mock_vision_fallback"
        if raw_suffix:
            note = f"{note}:{raw_suffix}"
        el = dict(self._mock)
        dbg_lines = [
            "[YOLO · mock_fallback]",
            f"  summary: {note}",
            "  synthetic elements (not from real inference):",
        ]
        for k, (x, y) in sorted(el.items()):
            dbg_lines.append(f"    {k!r} → center=({x:.1f}, {y:.1f})")
        return VisionResult(
            elements=el,
            raw_notes=note,
            yolo_debug="\n".join(dbg_lines),
        )

    def _ensure_yolo_loaded(self, model_spec: str) -> str:
        """返回空串表示就绪；否则为错误简述（已缓存失败则短路）。"""
        global _state
        with _state.lock:
            if _state.backend == "ultralytics" and _state.model is not None:
                return ""
            if _state.loaded and _state.load_error:
                return _state.load_error

            try:
                from ultralytics import YOLO  # type: ignore[import-untyped]
            except ImportError as e:
                _state.loaded = True
                _state.backend = "mock"
                _state.load_error = repr(e)
                return _state.load_error

            try:
                path = Path(model_spec)
                spec = str(path) if path.is_file() else model_spec
                model = YOLO(spec)
                _state.model = model
                names = getattr(model, "names", None) or {}
                if isinstance(names, dict):
                    _state.names = {int(k): str(v) for k, v in names.items()}
                else:
                    _state.names = {i: str(n) for i, n in enumerate(names)}
                _state.model_path = spec
                _state.device = (os.environ.get("GAMEQA_YOLO_DEVICE") or "").strip()
                _state.backend = "ultralytics"
                _state.loaded = True
                _state.load_error = ""
                logger.info(
                    "[gameqa][vision] YOLO 已加载 spec=%s device=%s classes=%s",
                    spec,
                    _state.device or "auto",
                    len(_state.names),
                )
            except Exception as e:
                logger.exception("[gameqa][vision] YOLO load failed")
                _state.loaded = True
                _state.backend = "mock"
                _state.model = None
                _state.load_error = repr(e)
                return _state.load_error
        return ""

    def _yolo_inference(self, png: bytes) -> VisionResult:
        global _state
        t0 = time.perf_counter()
        conf = float(os.environ.get("GAMEQA_YOLO_CONF", "0.25"))
        imgsz_env = (os.environ.get("GAMEQA_YOLO_IMG_SIZE") or "").strip()
        imgsz: int | None = None
        if imgsz_env:
            try:
                v = int(imgsz_env)
                imgsz = v if v > 0 else None
            except ValueError:
                imgsz = None

        try:
            from PIL import Image
        except ImportError:
            return self._mock_result(raw_suffix="PIL_missing")

        img = Image.open(BytesIO(png)).convert("RGB")
        model = _state.model
        kw: dict[str, Any] = {
            "source": img,
            "conf": conf,
            "verbose": False,
        }
        if imgsz:
            kw["imgsz"] = imgsz
        if _state.device:
            kw["device"] = _state.device

        results = model.predict(**kw)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if elapsed_ms > 100.0:
            logger.warning(
                "[gameqa][vision] YOLO inference slow: %.1f ms (threshold 100 ms)",
                elapsed_ms,
            )
        else:
            logger.debug("[gameqa][vision] YOLO inference %.2f ms", elapsed_ms)

        r0 = results[0]
        boxes = getattr(r0, "boxes", None)
        elements: dict[str, tuple[float, float]] = {}
        if boxes is None or len(boxes) == 0:
            rn = (
                f"yolo_ok boxes=0 latency_ms={elapsed_ms:.2f}; "
                f"model={_state.model_path} conf={conf}"
            )
            ydbg = "\n".join(
                [
                    "[YOLO · ultralytics]",
                    f"  summary: {rn}",
                    "  detections: (none — 当前 conf/threshold 下无框)",
                ]
            )
            return VisionResult(elements={}, raw_notes=rn, yolo_debug=ydbg)

        per_label_index: dict[str, int] = {}
        xyxy_tensor = boxes.xyxy
        cls_tensor = boxes.cls
        n = len(boxes)
        device_str = getattr(xyxy_tensor, "device", None)
        xyxy_np = xyxy_tensor.cpu().numpy() if hasattr(xyxy_tensor, "cpu") else xyxy_tensor.numpy()
        cls_np = cls_tensor.cpu().numpy() if hasattr(cls_tensor, "cpu") else cls_tensor.numpy()
        conf_np = None
        if getattr(boxes, "conf", None) is not None:
            ct = boxes.conf
            conf_np = ct.cpu().numpy() if hasattr(ct, "cpu") else ct.numpy()

        dbg_lines: list[str] = []

        for i in range(n):
            x1, y1, x2, y2 = (float(xyxy_np[i][j]) for j in range(4))
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            cls_id = int(cls_np[i])
            raw_name = _state.names.get(cls_id, f"class_{cls_id}")
            cf = float(conf_np[i]) if conf_np is not None and int(conf_np.shape[0]) > i else -1.0
            base = _sanitize_label_key(raw_name)
            idx = per_label_index.get(base, 0)
            per_label_index[base] = idx + 1
            key = base if idx == 0 else f"{base}_{idx}"
            if key in elements:
                key = f"{base}_{idx}_{i}"

            elements[key] = (cx, cy)
            dbg_lines.append(
                f"  [{i}] semantic_key={key!r} cls_id={cls_id} class_name={raw_name!r} "
                f"conf={cf:.4f} xyxy=({x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f}) "
                f"center=({cx:.1f},{cy:.1f})"
            )

        note = (
            f"yolo_ultralytics latency_ms={elapsed_ms:.2f} boxes={n} "
            f"device={device_str or _state.device or 'auto'} "
            f"model={Path(_state.model_path).name} conf={conf}"
        )
        ydbg = "\n".join(
            [
                "[YOLO · ultralytics]",
                f"  summary: {note}",
                f"  merged_element_keys: {list(elements.keys())!r}",
                "  per_detection:",
                *dbg_lines,
            ]
        )
        return VisionResult(elements=elements, raw_notes=note, yolo_debug=ydbg)


def _sanitize_label_key(raw: str) -> str:
    s = raw.strip().replace(" ", "_")
    s = _SAFE_KEY_RE.sub("_", s).strip("_")
    return s or "obj"
