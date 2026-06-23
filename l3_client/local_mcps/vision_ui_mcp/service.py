"""
Vision UI 会话：解析全屏 + PyAutoGUI 点击/输入。

工具面（L3 进程内 MCP）：
  - get_parsed_screen
  - click_element
  - type_text
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.mcp_multimodal_result import build_multimodal_observation_payload, encode_image_bytes_as_data_url

from .screen_parser import ParsedElement, ParsedScreen, capture_screen_png, parse_screen_from_png

logger = logging.getLogger("vision_ui.service")


@dataclass
class VisionUIService:
    """跨 ReAct 轮次保留最近一次解析结果。"""

    last_parse: ParsedScreen | None = None
    audit: list[dict[str, Any]] = field(default_factory=list)

    def _audit(self, event: str, detail: dict[str, Any]) -> None:
        self.audit.append({"ts": time.time(), "event": event, **detail})
        if len(self.audit) > 200:
            self.audit = self.audit[-120:]

    def get_parsed_screen(self) -> str:
        """
        截全屏 → OCR/YOLO 编号 → 返回多模态 Observation 信封（标注图 + JSON 映射）。
        """
        png, w, h, err = capture_screen_png()
        if err:
            return json.dumps({"ok": False, "error": err}, ensure_ascii=False)

        parsed = parse_screen_from_png(png, screen_wh=(w, h))
        self.last_parse = parsed
        if not parsed.ok:
            self._audit("parse_fail", {"error": parsed.error, "notes": parsed.notes})
            return json.dumps(
                {
                    "ok": False,
                    "error": parsed.error,
                    "notes": parsed.notes,
                    "screen_width": parsed.screen_width,
                    "screen_height": parsed.screen_height,
                },
                ensure_ascii=False,
            )

        summary = {
            "ok": True,
            "screen_width": parsed.screen_width,
            "screen_height": parsed.screen_height,
            "element_count": len(parsed.elements),
            "elements": parsed.elements,
            "annotated_image_path": parsed.annotated_path,
            "raw_screenshot_path": parsed.raw_png_path,
            "notes": parsed.notes,
            "hint": (
                "图中红色 [N] 与 elements 的键 N 一一对应。"
                "下一步用 mcp:click_element（element_id=N）点击；"
                "用 mcp:type_text 在点击后输入（可传 element_id 先聚焦）。"
                "打开桌面程序（如记事本）通常需 double_click=true。"
            ),
        }
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        self._audit("parse_ok", {"count": len(parsed.elements), "path": parsed.annotated_path})

        if parsed.annotated_png:
            du = encode_image_bytes_as_data_url(parsed.annotated_png, "image/png")
            return build_multimodal_observation_payload(
                text_parts=[text],
                image_data_urls=[du],
            )
        return text

    def _resolve_element(self, element_id: str) -> tuple[ParsedElement | None, str]:
        eid = str(element_id or "").strip()
        if not eid:
            return None, "element_id required"
        ps = self.last_parse
        if not ps or not ps.ok:
            return None, "请先调用 mcp:get_parsed_screen 获取当前屏幕元素表"
        for el in ps.element_list:
            if el.element_id == eid:
                return el, ""
        if eid in ps.elements:
            row = ps.elements[eid]
            return ParsedElement(
                element_id=eid,
                text=str(row.get("text") or ""),
                x=float(row["x"]),
                y=float(row["y"]),
                score=float(row.get("score") or 1.0),
                source=str(row.get("source") or "ocr"),
            ), ""
        return None, f"未知 element_id={eid!r}，请先 get_parsed_screen 或检查编号"

    def click_element(
        self,
        *,
        element_id: str,
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
        x, y = int(round(el.x)), int(round(el.y))
        try:
            pyautogui.moveTo(x, y, duration=0.12)
            if double_click:
                pyautogui.doubleClick(x=x, y=y, button=button)
            else:
                pyautogui.click(x=x, y=y, button=button)
        except Exception as e:
            return json.dumps(
                {"ok": False, "error": f"click_failed:{e!r}", "x": x, "y": y},
                ensure_ascii=False,
            )
        self._audit(
            "click",
            {"id": el.element_id, "text": el.text, "x": x, "y": y, "double": double_click},
        )
        return json.dumps(
            {
                "ok": True,
                "element_id": el.element_id,
                "text": el.text,
                "x": x,
                "y": y,
                "double_click": double_click,
                "button": button,
            },
            ensure_ascii=False,
        )

    def type_text(
        self,
        *,
        text: str,
        element_id: str = "",
        press_enter: bool = False,
        interval: float = 0.02,
    ) -> str:
        txt = str(text or "")
        if not txt:
            return json.dumps({"ok": False, "error": "text required"}, ensure_ascii=False)

        eid = str(element_id or "").strip()
        if eid:
            click_res = json.loads(
                self.click_element(element_id=eid, double_click=False)
            )
            if not click_res.get("ok"):
                return json.dumps(
                    {"ok": False, "error": "prefocus_click_failed", "detail": click_res},
                    ensure_ascii=False,
                )
            time.sleep(0.15)

        try:
            import pyautogui
        except ImportError as e:
            return json.dumps({"ok": False, "error": f"pyautogui_not_installed:{e}"}, ensure_ascii=False)

        try:
            if _needs_clipboard_paste(txt):
                import pyperclip

                pyperclip.copy(txt)
                pyautogui.hotkey("ctrl", "v")
            else:
                pyautogui.write(txt, interval=interval)
            if press_enter:
                pyautogui.press("enter")
        except Exception as e:
            return json.dumps({"ok": False, "error": f"type_failed:{e!r}"}, ensure_ascii=False)

        self._audit("type_text", {"len": len(txt), "element_id": eid or None, "enter": press_enter})
        return json.dumps(
            {
                "ok": True,
                "typed_len": len(txt),
                "element_id": eid or None,
                "press_enter": press_enter,
                "method": "clipboard" if _needs_clipboard_paste(txt) else "write",
            },
            ensure_ascii=False,
        )


def _needs_clipboard_paste(text: str) -> bool:
    """含 CJK 等非 ASCII 时用剪贴板粘贴（Windows 记事本等）。"""
    for ch in text:
        if ord(ch) > 127:
            return True
    return False


_service: VisionUIService | None = None


def get_vision_ui_service() -> VisionUIService:
    global _service
    if _service is None:
        _service = VisionUIService()
    return _service
