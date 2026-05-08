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
from typing import Any, ClassVar

logger = logging.getLogger("gameqa.vision_engine")

# 语义 key：仅保留字母数字与下划线
_SAFE_KEY_RE = re.compile(r"[^\w\-]+")


@dataclass
class VisionResult:
    """单帧解析结果：元素名 -> 视口中心 (cx, cy) CSS 像素。"""

    elements: dict[str, tuple[float, float]]
    raw_notes: str

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

    ``GAMEQA_YOLO_MODEL`` 未设置或加载失败时，使用与原 Mock 一致的行为，便于无 GPU/无权重联调。
    """

    _mock_fallback: ClassVar[dict[str, tuple[float, float]]] = {
        "Btn_Fold": (180.0, 720.0),
        "Btn_Call": (360.0, 720.0),
        "Btn_Raise": (520.0, 720.0),
        "Pot_Label": (640.0, 200.0),
    }

    def __init__(self, *, mock_elements: dict[str, tuple[float, float]] | None = None) -> None:
        self._mock = dict(mock_elements or self._mock_fallback)

    async def analyze_async(self, screenshot_png: bytes) -> VisionResult:
        """异步入口：YOLO/mock 均在 worker 线程执行。"""
        return await asyncio.to_thread(self.analyze_sync, screenshot_png)

    def analyze_sync(self, screenshot_png: bytes) -> VisionResult:
        if not screenshot_png or len(screenshot_png) < 32:
            return VisionResult(elements={}, raw_notes="empty_or_invalid_png")

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
        return VisionResult(
            elements=dict(self._mock),
            raw_notes=note,
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
            return VisionResult(
                elements={},
                raw_notes=(
                    f"yolo_ok boxes=0 latency_ms={elapsed_ms:.2f}; "
                    f"model={_state.model_path} conf={conf}"
                ),
            )

        per_label_index: dict[str, int] = {}
        xyxy_tensor = boxes.xyxy
        cls_tensor = boxes.cls
        n = len(boxes)
        device_str = getattr(xyxy_tensor, "device", None)
        xyxy_np = xyxy_tensor.cpu().numpy() if hasattr(xyxy_tensor, "cpu") else xyxy_tensor.numpy()
        cls_np = cls_tensor.cpu().numpy() if hasattr(cls_tensor, "cpu") else cls_tensor.numpy()

        for i in range(n):
            x1, y1, x2, y2 = (float(xyxy_np[i][j]) for j in range(4))
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            cls_id = int(cls_np[i])
            raw_name = _state.names.get(cls_id, f"class_{cls_id}")
            base = _sanitize_label_key(raw_name)
            idx = per_label_index.get(base, 0)
            per_label_index[base] = idx + 1
            key = base if idx == 0 else f"{base}_{idx}"
            if key in elements:
                key = f"{base}_{idx}_{i}"

            elements[key] = (cx, cy)

        note = (
            f"yolo_ultralytics latency_ms={elapsed_ms:.2f} boxes={n} "
            f"device={device_str or _state.device or 'auto'} "
            f"model={Path(_state.model_path).name} conf={conf}"
        )
        return VisionResult(elements=elements, raw_notes=note)


def _sanitize_label_key(raw: str) -> str:
    s = raw.strip().replace(" ", "_")
    s = _SAFE_KEY_RE.sub("_", s).strip("_")
    return s or "obj"
