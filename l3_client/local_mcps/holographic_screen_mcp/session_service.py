"""
全息屏幕会话：OmniParser 解析 + PyAutoGUI 物理点击。

L3 进程内工具（registry）与 stdio MCP server 共用本模块。
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.mcp_multimodal_result import build_multimodal_observation_payload, encode_image_bytes_as_data_url

from .omniparser_core import repo_root, run_omniparser, simplify_elements_for_llm
from .window_capture import capture_window_png

logger = logging.getLogger("holographic.service")


def _data_dir() -> Path:
    raw = (os.environ.get("HOLOGRAPHIC_SCREEN_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    try:
        from l3_node.jachin_config import get_jachin_root

        return (get_jachin_root() / "holographic_screen").resolve()
    except ImportError:
        return (Path.home() / ".jachin" / "holographic_screen").resolve()


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def capture_screen_png() -> tuple[bytes, int, int, str]:
    try:
        import pyautogui
    except ImportError as e:
        return b"", 0, 0, f"pyautogui_not_installed:{e}"
    try:
        from io import BytesIO

        img = pyautogui.screenshot()
        w, h = img.size
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), int(w), int(h), ""
    except Exception as e:
        return b"", 0, 0, f"screenshot_failed:{e!r}"


@dataclass
class HolographicElement:
    element_id: int
    center_x: int
    center_y: int
    type: str = ""
    content: str = ""


@dataclass
class HolographicScreenService:
    """跨 ReAct 轮次保留最近一次 OmniParser 元素表。"""

    last_elements: dict[int, HolographicElement] = field(default_factory=dict)
    last_work_dir: str = ""
    screen_width: int = 0
    screen_height: int = 0
    audit: list[dict[str, Any]] = field(default_factory=list)

    def _audit(self, event: str, detail: dict[str, Any]) -> None:
        self.audit.append({"ts": time.time(), "event": event, **detail})
        if len(self.audit) > 200:
            self.audit = self.audit[-120:]

    def _index_elements(self, elements_llm: list[dict[str, Any]]) -> dict[int, HolographicElement]:
        out: dict[int, HolographicElement] = {}
        for row in elements_llm:
            try:
                eid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            cx = row.get("center_x")
            cy = row.get("center_y")
            if cx is None or cy is None:
                continue
            out[eid] = HolographicElement(
                element_id=eid,
                center_x=int(cx),
                center_y=int(cy),
                type=str(row.get("type") or ""),
                content=str(row.get("content") or ""),
            )
        return out

    def get_holographic_screen(
        self,
        *,
        bbox_threshold: float | None = None,
        iou_threshold: float | None = None,
        capture_window: bool = False,
        window_title_keywords: tuple[str, ...] | None = None,
    ) -> str:
        capture_region_note = ""
        if capture_window or (os.environ.get("HOLOGRAPHIC_CAPTURE_WINDOW") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "calculator",
        ):
            png, w, h, err, region = capture_window_png(window_title_keywords)
            if err or not png:
                logger.warning(
                    "[holographic] 窗口截取失败(%s)，回退全屏",
                    err or "empty",
                )
                png, w, h, err = capture_screen_png()
                capture_region_note = f"window_capture_fallback:{err}"
            else:
                capture_region_note = f"window_region={region}"
        else:
            png, w, h, err = capture_screen_png()
        if err:
            return json.dumps({"ok": False, "error": err}, ensure_ascii=False)

        self.screen_width = w
        self.screen_height = h

        dd = _data_dir()
        dd.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        work_dir = dd / f"parse_{ts}"
        raw_path = work_dir / "screen_raw.png"
        work_dir.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(png)

        bt = bbox_threshold if bbox_threshold is not None else _env_float("OMNIPARSER_BBOX_THRESHOLD", 0.05)
        iou = iou_threshold if iou_threshold is not None else _env_float("OMNIPARSER_IOU_THRESHOLD", 0.7)

        try:
            report = run_omniparser(
                raw_path,
                work_dir=work_dir,
                bbox_threshold=bt,
                iou_threshold=iou,
            )
        except Exception as e:
            logger.exception("[holographic] parse failed")
            return json.dumps({"ok": False, "error": f"omniparser_failed:{e!r}"}, ensure_ascii=False)

        if not report.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "error": report.get("error") or "omniparser_failed",
                    "detail": report.get("detail"),
                },
                ensure_ascii=False,
            )

        elements_llm = report.get("elements_llm") or []
        if not elements_llm and report.get("elements"):
            elements_llm = simplify_elements_for_llm(report["elements"])

        self.last_elements = self._index_elements(elements_llm)
        self.last_work_dir = str(work_dir)

        outs = report.get("outputs") or {}
        ann_path = Path(str(outs.get("annotated_image") or ""))
        annotated_bytes = b""
        if ann_path.is_file():
            annotated_bytes = ann_path.read_bytes()

        elements_dict = {
            str(eid): {"center_x": el.center_x, "center_y": el.center_y}
            for eid, el in self.last_elements.items()
        }
        summary = {
            "ok": True,
            "screen_width": w,
            "screen_height": h,
            "element_count": len(elements_llm),
            "elements": elements_llm,
            "elements_dict": elements_dict,
            "annotated_image_path": str(ann_path) if ann_path else None,
            "omnioutput": report.get("omnioutput") or {},
            "work_dir": str(work_dir),
            "capture_note": capture_region_note or "fullscreen",
            "model_dir": report.get("model_dir") or str(repo_root() / "model" / "OmniParser-v2.0"),
            "hint": (
                "标注图上红色数字 id 与 elements[].id 一致（从 0 起）。"
                "下一步用 mcp:physical_click，Action Input 示例 {\"element_id\": 12}。"
                "界面变化后须重新调用 mcp:get_holographic_screen。"
            ),
        }
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        self._audit("parse_ok", {"count": len(elements_llm), "work_dir": str(work_dir)})

        if annotated_bytes:
            mime = "image/jpeg" if ann_path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            du = encode_image_bytes_as_data_url(annotated_bytes, mime)
            return build_multimodal_observation_payload(
                text_parts=[text],
                image_data_urls=[du],
            )
        return text

    def _resolve_element(self, element_id: str | int) -> tuple[HolographicElement | None, str]:
        if not self.last_elements:
            return None, "请先调用 mcp:get_holographic_screen 获取当前屏幕元素表"
        try:
            eid = int(str(element_id).strip())
        except (TypeError, ValueError):
            return None, f"element_id 须为整数编号，收到: {element_id!r}"
        el = self.last_elements.get(eid)
        if el is None:
            return None, f"未知 element_id={eid}，请先 get_holographic_screen 或检查标注图编号"
        return el, ""

    def physical_click(
        self,
        *,
        element_id: str | int,
        double_click: bool = False,
        button: str = "left",
    ) -> str:
        el, err = self._resolve_element(element_id)
        if el is None:
            return json.dumps({"ok": False, "error": err}, ensure_ascii=False)
        try:
            import pyautogui
        except ImportError as e:
            return json.dumps({"ok": False, "error": f"pyautogui_not_installed:{e}"}, ensure_ascii=False)

        pyautogui.FAILSAFE = True
        x, y = el.center_x, el.center_y
        try:
            pyautogui.moveTo(x, y, duration=0.12)
            if double_click:
                pyautogui.doubleClick(x=x, y=y, button=button)
            else:
                pyautogui.click(x=x, y=y, button=button)
        except Exception as e:
            return json.dumps(
                {"ok": False, "error": f"click_failed:{e!r}", "center_x": x, "center_y": y},
                ensure_ascii=False,
            )
        self._audit(
            "physical_click",
            {"id": el.element_id, "x": x, "y": y, "double": double_click},
        )
        return json.dumps(
            {
                "ok": True,
                "element_id": el.element_id,
                "center_x": x,
                "center_y": y,
                "type": el.type,
                "content": el.content,
                "double_click": double_click,
                "button": button,
            },
            ensure_ascii=False,
        )


_service: HolographicScreenService | None = None


def get_holographic_screen_service() -> HolographicScreenService:
    global _service
    if _service is None:
        _service = HolographicScreenService()
    return _service
