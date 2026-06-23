#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉具身智能集成测试：计算器（眼-脑-手解耦架构）。

  Observe → OmniParser（bbox=0.03, iou=0.1）
  Plan    → **元素≥10 且非 keypad_probe**：VLM 只出 ID + OmniParser 坐标
            → **否则**：VLM 读**原图**自估坐标（恢复旧方案）
  Act     → PyAutoGUI
  Verify  → VLM 读原图读结果

用法（仓库根目录）::

  # 默认算式 125×4
  .\\.venv-omniparser\\Scripts\\python.exe scripts\\test_calculator_agent.py

  # 自定义算式
  .\\.venv-omniparser\\Scripts\\python.exe scripts\\test_calculator_agent.py --expr "99×8+15" --expect 807

  # 内置进阶套件（5 题）
  .\\.venv-omniparser\\Scripts\\python.exe scripts\\test_calculator_agent.py --suite

  .\\.venv-omniparser\\Scripts\\python.exe scripts\\test_calculator_agent.py --list-cases
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

logger = logging.getLogger("calculator_agent")

MIN_CLICK_STEPS = 3
MAX_PLAN_ATTEMPTS = 3
# 侦察兵默认阈值（可用环境变量覆盖）
OMNI_BOX_THRESHOLD = 0.03
OMNI_IOU_THRESHOLD = 0.1
MIN_OMNI_ELEMENTS = 10


@dataclass(frozen=True)
class CalculatorTask:
    """单道测试题：表达式由视觉模型读图后自行点键完成（非脚本写死坐标）。"""

    case_id: str
    expression: str
    expect: str
    note: str = ""


# Windows 标准计算器按输入顺序求值（非 PEMDAS）
CASE_SUITE: tuple[CalculatorTask, ...] = (
    CalculatorTask("basic", "125×4", "500", "基础乘法"),
    CalculatorTask("mul_add", "99×8+15", "807", "先乘后加"),
    CalculatorTask("sub", "1000-357", "643", "减法"),
    CalculatorTask("decimal", "12.5+3.5", "16", "含小数点"),
    CalculatorTask("chain", "48÷6×7", "56", "连续除再乘"),
)


def _default_task() -> CalculatorTask:
    return CASE_SUITE[0]


def _normalize_expr_display(expr: str) -> str:
    return (
        (expr or "")
        .strip()
        .replace("*", "×")
        .replace("/", "÷")
        .replace(" ", "")
    )


def _normalize_plan_key(key: str) -> str:
    k = (key or "").strip()
    if k in ("*", "x", "X", "×"):
        return "×"
    if k in ("/", "÷"):
        return "÷"
    if k in ("−", "–", "—"):
        return "-"
    if k in ("加", "+"):
        return "+"
    if k in ("=", "＝", "等号"):
        return "="
    if k in (".", "．", "decimal", "小数点"):
        return "."
    return k


def _expr_to_expected_keys(expr: str) -> list[str]:
    """算式展开为逐键序列（用于 Plan 步数/按键校验）。"""
    e = _normalize_expr_display(expr)
    keys: list[str] = []
    i = 0
    while i < len(e):
        if e[i].isdigit() or e[i] == ".":
            j = i
            while j < len(e) and (e[j].isdigit() or e[j] == "."):
                j += 1
            keys.extend(list(e[i:j]))
            i = j
        elif e[i] in "×÷+-":
            keys.append(e[i])
            i += 1
        else:
            i += 1
    keys.append("=")
    return keys


def _literal_digit_hint(literal: str) -> str:
    """多字符/含小数点的字面量 → 逐键说明。"""
    if "." in literal:
        chars = list(literal)
        inner = "、".join(
            "底行小数点 ." if c == "." else ("底行数字 0" if c == "0" else f"数字 {c}")
            for c in chars
        )
        return f"输入「{literal}」依次按：{inner}"
    if len(literal) > 1:
        inner = "、".join("底行数字 0" if c == "0" else f"数字 {c}" for c in literal)
        return f"输入「{literal}」依次按：{inner}（每个 0 必须点最底行 0 键，勿与 2 混淆）"
    if literal == "0":
        return "底行数字 0（勿点在 1/2/3 那一行）"
    return f"数字 {literal}"


def _expr_key_sequence_hint(expr: str) -> str:
    """给 VL 的「按键序列」文字描述（非坐标）。"""
    e = _normalize_expr_display(expr)
    parts: list[str] = []
    i = 0
    while i < len(e):
        ch = e[i]
        if ch.isdigit() or ch == ".":
            j = i
            while j < len(e) and (e[j].isdigit() or e[j] == "."):
                j += 1
            parts.append(_literal_digit_hint(e[i:j]))
            i = j
            continue
        sym = {"×": "乘号(×)", "÷": "除号(÷)", "+": "加号(+)", "-": "减号(-)"}.get(ch, ch)
        parts.append(sym)
        i += 1
    parts.append("等号(=)")
    return " → ".join(parts)


def _build_elements_dict(
    coordinates: dict[int, tuple[int, int]],
) -> dict[int, dict[str, int]]:
    return {
        int(eid): {"center_x": int(xy[0]), "center_y": int(xy[1])}
        for eid, xy in coordinates.items()
    }


def _resolve_task(expr: str | None, expect: str | None, case_id: str | None) -> CalculatorTask:
    if case_id:
        for t in CASE_SUITE:
            if t.case_id == case_id:
                return t
        raise ValueError(
            f"未知 case_id={case_id!r}，可用: {[t.case_id for t in CASE_SUITE]}"
        )
    ex = _normalize_expr_display(expr or CASE_SUITE[0].expression)
    exp = (expect or "").strip()
    if not exp:
        exp = _eval_standard_calc_sequential(ex)
    return CalculatorTask("custom", ex, exp, "自定义")


def _eval_standard_calc_sequential(expr: str) -> str:
    """
    模拟 Win11 标准计算器从左到右输入（无括号优先级）。
    仅支持 + - × ÷ 与小数点。
    """
    e = _normalize_expr_display(expr)
    tokens: list[str] = []
    i = 0
    while i < len(e):
        if e[i].isdigit() or e[i] == ".":
            j = i
            while j < len(e) and (e[j].isdigit() or e[j] == "."):
                j += 1
            tokens.append(e[i:j])
            i = j
        elif e[i] in "×÷+-":
            tokens.append(e[i])
            i += 1
        else:
            i += 1
    if not tokens:
        raise ValueError(f"无法解析表达式: {expr}")

    def _to_float(s: str) -> float:
        return float(s)

    acc = _to_float(tokens[0])
    idx = 1
    while idx < len(tokens):
        op = tokens[idx]
        rhs = _to_float(tokens[idx + 1])
        if op == "+":
            acc += rhs
        elif op == "-":
            acc -= rhs
        elif op == "×":
            acc *= rhs
        elif op == "÷":
            acc /= rhs
        idx += 2
    if abs(acc - round(acc)) < 1e-9:
        return str(int(round(acc)))
    return str(acc)


def _answer_matches_expect(answer: str, expect: str) -> bool:
    raw = (answer or "").strip()
    if expect in raw:
        return True
    # 抽取数字（含小数）
    nums = re.findall(r"-?\d+(?:\.\d+)?", raw)
    if not nums:
        return False
    try:
        got = float(nums[-1])
        want = float(expect)
        return abs(got - want) < max(0.01, abs(want) * 1e-6)
    except ValueError:
        return expect in re.sub(r"[^\d.]", "", raw)


@dataclass
class ScreenSnapshot:
    """一次全息屏解析结果。"""

    ok: bool
    elements_text: str
    coordinates: dict[int, tuple[int, int]]
    elements_dict: dict[int, dict[str, int]] = field(default_factory=dict)
    elements: list[dict[str, Any]] = field(default_factory=list)
    annotated_image_path: str | None = None
    image_data_url: str | None = None
    work_dir: str | None = None
    raw_image_path: str | None = None
    window_region: tuple[int, int, int, int] | None = None
    capture_note: str = ""
    raw_observation: str = ""
    error: str = ""


@dataclass
class PlannedClick:
    """Act 阶段：OmniParser 坐标 + VLM 选的 element_id。"""

    element_id: int
    x: int
    y: int
    key: str = ""
    source: str = "omni+vl-id"


@dataclass
class ClearDecision:
    """清屏：ID 模式用 element_id；坐标模式用 x/y（窗口内，Act 前转屏幕坐标）。"""

    need_clear: bool
    element_id: int = 0
    x: int = 0
    y: int = 0
    reason: str = ""
    by_id: bool = True


def _hydrate_openai_env() -> None:
    """将 DashScope 百炼凭证映射为 OpenAI SDK 可用的 OPENAI_* 变量。"""
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
        key = (os.environ.get("QWEN_API_KEY") or os.environ.get("QWEN_AI_API_KEY") or "").strip()
    if key:
        os.environ["OPENAI_API_KEY"] = key
        if base and not (os.environ.get("OPENAI_BASE_URL") or "").strip():
            os.environ["OPENAI_BASE_URL"] = base.rstrip("/")
        logger.info("[env] 已从 DashScope 映射 OPENAI_API_KEY / OPENAI_BASE_URL")


def _strip_litellm_prefix(model_id: str) -> str:
    """OpenAI 兼容接口使用裸模型名（如 qwen-vl-max），不含 dashscope/ 前缀。"""
    m = (model_id or "").strip()
    if m.lower().startswith("dashscope/"):
        return m.split("/", 1)[1].strip() or m
    return m


def _default_vision_model() -> str:
    """
    与 L3 多模态路由对齐：.env 中 INTENT_GATEWAY_MULTIMODAL_MODEL / CALCULATOR_AGENT_MODEL。
    """
    for key in (
        "CALCULATOR_AGENT_MODEL",
        "INTENT_GATEWAY_MULTIMODAL_MODEL",
        "LLM_VISION_MODEL",
    ):
        v = (os.environ.get(key) or "").strip()
        if v:
            return _strip_litellm_prefix(v)
    try:
        from l3_node.intent_gateway.model_resolve import get_multimodal_model_litellm_id

        return _strip_litellm_prefix(get_multimodal_model_litellm_id())
    except Exception:
        pass
    base = (os.environ.get("OPENAI_BASE_URL") or "").lower()
    if "dashscope" in base or (os.environ.get("DASHSCOPE_API_KEY") or "").strip():
        return "qwen-vl-max"
    return "gpt-4o"


def _load_elements_from_work_dir(work_dir: str | None) -> list[dict[str, Any]]:
    if not work_dir:
        return []
    p = Path(work_dir) / "parsed_result.json"
    if not p.is_file():
        return []
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj.get("elements_llm") or obj.get("elements") or []
    except Exception as e:
        logger.warning("[eye] 读取 %s 失败: %s", p, e)
        return []


def _coordinates_from_elements(elements: list[dict[str, Any]]) -> dict[int, tuple[int, int]]:
    out: dict[int, tuple[int, int]] = {}
    for row in elements:
        try:
            eid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        cx, cy = row.get("center_x"), row.get("center_y")
        if cx is None or cy is None:
            continue
        out[eid] = (int(cx), int(cy))
    return out


def _parse_holographic_observation(raw: str) -> ScreenSnapshot:
    from core.mcp_multimodal_result import parse_multimodal_observation_payload

    text, urls = parse_multimodal_observation_payload(raw)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return ScreenSnapshot(
            ok=False,
            elements_text=text[:2000],
            coordinates={},
            raw_observation=raw,
            error="observation_not_json",
        )
    if not obj.get("ok"):
        return ScreenSnapshot(
            ok=False,
            elements_text=text,
            coordinates={},
            raw_observation=raw,
            error=str(obj.get("error") or "parse_failed"),
        )
    elements = obj.get("elements") or []
    work_dir = obj.get("work_dir")
    if len(elements) < MIN_CLICK_STEPS and work_dir:
        disk_els = _load_elements_from_work_dir(str(work_dir))
        if len(disk_els) > len(elements):
            elements = disk_els
    coords = _coordinates_from_elements(elements)
    elements_dict = _build_elements_dict(coords)
    raw_ed = obj.get("elements_dict") or {}
    if isinstance(raw_ed, dict) and raw_ed:
        merged: dict[int, dict[str, int]] = {}
        for k, v in raw_ed.items():
            try:
                eid = int(k)
                merged[eid] = {
                    "center_x": int(v["center_x"]),
                    "center_y": int(v["center_y"]),
                }
            except (TypeError, ValueError, KeyError):
                continue
        if merged:
            elements_dict = merged
            coords = {eid: (d["center_x"], d["center_y"]) for eid, d in merged.items()}
    wd = str(work_dir) if work_dir else None
    raw_path = str(Path(wd) / "screen_raw.png") if wd and (Path(wd) / "screen_raw.png").is_file() else None
    ann = obj.get("annotated_image_path")
    if wd and not ann:
        for name in ("parsed_output.jpg", "annotated.jpg"):
            p = Path(wd) / name
            if p.is_file():
                ann = str(p)
                break
    region = None
    try:
        from l3_client.local_mcps.holographic_screen_mcp.calculator_layout import (
            parse_window_region_from_note,
        )

        region = parse_window_region_from_note(str(obj.get("capture_note") or ""))
    except Exception:
        pass
    return ScreenSnapshot(
        ok=True,
        elements_text=json.dumps(elements, ensure_ascii=False, indent=2),
        coordinates=coords,
        elements_dict=elements_dict,
        elements=elements,
        annotated_image_path=str(ann) if ann else obj.get("annotated_image_path"),
        image_data_url=urls[0] if urls else None,
        work_dir=wd,
        raw_image_path=raw_path,
        window_region=region,
        capture_note=str(obj.get("capture_note") or ""),
        raw_observation=raw,
    )


def get_holographic_screen(*, use_mock: bool = False) -> ScreenSnapshot:
    """
    MCP 动作：截屏 → OmniParser → 标注图 + {id: (center_x, center_y)}。
    """
    if use_mock:
        return _mock_holographic_screen()

    from l3_client.local_mcps.holographic_screen_mcp.session_service import (
        get_holographic_screen_service,
    )

    try:
        bt = float(os.environ.get("CALCULATOR_BBOX_THRESHOLD") or str(OMNI_BOX_THRESHOLD))
    except ValueError:
        bt = OMNI_BOX_THRESHOLD
    try:
        iou = float(os.environ.get("CALCULATOR_IOU_THRESHOLD") or str(OMNI_IOU_THRESHOLD))
    except ValueError:
        iou = OMNI_IOU_THRESHOLD

    logger.info(
        "[eye] 调用 get_holographic_screen（计算器窗口 + OmniParser，可能需数十秒） "
        "bbox_threshold=%.2f iou=%.2f …",
        bt,
        iou,
    )
    t0 = time.perf_counter()
    raw = get_holographic_screen_service().get_holographic_screen(
        capture_window=True,
        bbox_threshold=bt,
        iou_threshold=iou,
    )
    logger.info("[eye] 完成 (%.1fs)", time.perf_counter() - t0)
    snap = _parse_holographic_observation(raw)
    logger.info(
        "[eye] ok=%s elements=%d capture=%s raw=%s annotated=%s",
        snap.ok,
        len(snap.coordinates),
        snap.capture_note or "?",
        snap.raw_image_path or "(无)",
        snap.annotated_image_path or "(data_url)",
    )
    try:
        from core.mcp_multimodal_result import parse_multimodal_observation_payload

        summary_text, _ = parse_multimodal_observation_payload(raw)
        omni = (json.loads(summary_text).get("omnioutput") or {}) if summary_text.startswith("{") else {}
        if omni.get("annotated_image"):
            logger.info("[eye] omnioutput 标注图: %s", omni["annotated_image"])
    except Exception:
        pass
    n = len(snap.elements_dict)
    natural = _snapshot_natural_omni_count(snap)
    logger.info(
        "[eye] elements_dict %d（自然检出 %d）；Plan 模式见 Observe 后日志",
        n,
        natural,
    )
    if natural < MIN_OMNI_ELEMENTS:
        logger.warning(
            "[eye] OmniParser 自然检出 <%d → 将用 VLM 读原图估坐标（可设 CALCULATOR_KEYPAD_PROBE=1 启用网格探针）",
            MIN_OMNI_ELEMENTS,
        )
    return snap


def _mock_holographic_screen() -> ScreenSnapshot:
    """Mock：合成图 + 伪 OmniParser elements_dict（供 ID 闭环联调）。"""
    logger.warning("[eye] MOCK：合成标注图 + elements_dict，需 --mock-click-skip")
    labels = ["7", "8", "9", "÷", "4", "5", "6", "×", "1", "2", "3", "-", "0", ".", "=", "+", "C"]
    elements: list[dict[str, Any]] = []
    coords: dict[int, tuple[int, int]] = {}
    out_dir = ROOT / "scripts" / "_calculator_mock"
    out_dir.mkdir(parents=True, exist_ok=True)
    img_path = out_dir / "mock_annotated.png"
    try:
        from PIL import Image, ImageDraw

        im = Image.new("RGB", (400, 520), (240, 240, 240))
        dr = ImageDraw.Draw(im)
        dr.rectangle([20, 20, 380, 80], fill=(30, 30, 30))
        dr.text((30, 35), "0", fill=(200, 255, 200))
        for i, label in enumerate(labels):
            x, y = 30 + (i % 4) * 90, 100 + (i // 4) * 90
            cx, cy = x + 35, y + 35
            dr.rectangle([x, y, x + 70, y + 70], outline=(255, 0, 0), width=2)
            dr.text((x + 4, y + 4), str(i), fill=(255, 0, 0))
            dr.text((x + 22, y + 26), label, fill=(0, 0, 0))
            elements.append(
                {"id": i, "center_x": cx, "center_y": cy, "content": label, "type": "button"}
            )
            coords[i] = (cx, cy)
        im.save(img_path)
        du = f"data:image/png;base64,{base64.b64encode(img_path.read_bytes()).decode('ascii')}"
    except Exception as e:
        logger.warning("[eye] mock 图像生成失败: %s", e)
        du = None
        img_path = None
        elements_dict: dict[int, dict[str, int]] = {}
    else:
        elements_dict = _build_elements_dict(coords)

    return ScreenSnapshot(
        ok=True,
        elements_text=json.dumps(elements, ensure_ascii=False, indent=2),
        coordinates=coords,
        elements_dict=elements_dict,
        elements=elements,
        annotated_image_path=str(img_path) if img_path else None,
        image_data_url=du,
        capture_note="mock",
        window_region=(0, 0, 400, 520),
    )


def _openai_client():
    _hydrate_openai_env()
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "未配置 OPENAI_API_KEY 或 DASHSCOPE_API_KEY；请在 .env 中设置后重试。"
        )
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("请安装 openai: pip install openai") from e

    base = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
    return OpenAI(api_key=api_key, base_url=base)


def _resolve_annotated_image_path(snapshot: ScreenSnapshot) -> Path:
    """VLM 只允许看 OmniParser 红框标注图，禁止 screen_raw。"""
    if snapshot.annotated_image_path:
        p = Path(snapshot.annotated_image_path)
        if p.is_file():
            return p
    if snapshot.work_dir:
        for name in ("parsed_output.jpg", "annotated.jpg"):
            p = Path(snapshot.work_dir) / name
            if p.is_file():
                return p
    raise RuntimeError(
        "无 OmniParser 标注图（parsed_output.jpg）；请确认 Observe 成功且 bbox/iou 阈值已调低"
    )


def _image_message_part_annotated(snapshot: ScreenSnapshot) -> dict[str, Any]:
    """红框+ID 标注图（ID 模式 Plan/Clear）。"""
    if snapshot.image_data_url and "mock" in (snapshot.capture_note or ""):
        return {"type": "image_url", "image_url": {"url": snapshot.image_data_url}}
    p = _resolve_annotated_image_path(snapshot)
    mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def _image_message_part_raw(snapshot: ScreenSnapshot) -> dict[str, Any]:
    """计算器窗口原图（VL 坐标模式 Plan/Clear/Verify）。"""
    if snapshot.raw_image_path and Path(snapshot.raw_image_path).is_file():
        p = Path(snapshot.raw_image_path)
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    if snapshot.image_data_url:
        return {"type": "image_url", "image_url": {"url": snapshot.image_data_url}}
    return _image_message_part_annotated(snapshot)


def _snapshot_has_keypad_probe(snapshot: ScreenSnapshot) -> bool:
    return any(
        str((r or {}).get("source") or "") == "calculator_keypad_probe"
        for r in snapshot.elements
    )


def _snapshot_natural_omni_count(snapshot: ScreenSnapshot) -> int:
    """不含 keypad_probe 补全的 OmniParser 检出数。"""
    n = 0
    for r in snapshot.elements:
        if str((r or {}).get("source") or "") == "calculator_keypad_probe":
            continue
        n += 1
    return n


def should_plan_by_element_ids(snapshot: ScreenSnapshot) -> bool:
    """
    仅当 OmniParser 自然检出足够按键时用 ID 模式；
    否则回退 VLM 读原图输出坐标。
    """
    if (os.environ.get("CALCULATOR_FORCE_VL_COORDS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    if (os.environ.get("CALCULATOR_FORCE_OMNI_IDS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return len(snapshot.elements_dict) >= MIN_OMNI_ELEMENTS
    if _snapshot_has_keypad_probe(snapshot):
        return False
    natural = _snapshot_natural_omni_count(snapshot)
    return natural >= MIN_OMNI_ELEMENTS and len(snapshot.elements_dict) >= MIN_OMNI_ELEMENTS


def _region_hint_for_vl(snapshot: ScreenSnapshot) -> str:
    if not snapshot.window_region:
        return ""
    _left, _top, w, h = snapshot.window_region
    return (
        f"\n本图为计算器窗口裁剪，约 {w}×{h} 像素；"
        "坐标 (x,y) 原点为图左上角 (0,0)，输出按钮中心在本图内的像素位置。"
    )


def _snapshot_image_height(snapshot: ScreenSnapshot) -> int:
    if snapshot.window_region:
        return int(snapshot.window_region[3])
    if snapshot.raw_image_path and Path(snapshot.raw_image_path).is_file():
        try:
            from PIL import Image

            with Image.open(snapshot.raw_image_path) as im:
                return int(im.height)
        except Exception:
            pass
    return 810


_CALC_LAYOUT_SEMANTICS = """
【Win11 标准计算器 — 标坐标时核对行距】
- 数字 0、小数点 . 在最底行，y 明显大于 1/2/3 那一行；. 在 0 右侧，不是 3 正上方。
- 运算符 + − × ÷ 在最右列，与对应数字行对齐。
"""


def _strip_llm_json_text(text: str) -> str:
    raw = (text or "").strip()
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if m:
            raw = m.group(1).strip()
    return raw


def _extract_element_ids_json(text: str) -> list[int]:
    """解析 VLM 返回的纯 ID 数组（禁止 x,y）。"""
    raw = _strip_llm_json_text(text)
    if not raw:
        raise ValueError("LLM 返回为空")
    obj = json.loads(raw)
    ids: list[int] = []
    if not isinstance(obj, list):
        raise ValueError("Plan 须为 JSON 数组，例如 [15, 23, 8, 4, 12, 9]")
    for item in obj:
        if isinstance(item, dict):
            if "element_id" in item:
                ids.append(int(item["element_id"]))
            elif "id" in item:
                ids.append(int(item["id"]))
            elif "x" in item or "y" in item:
                raise ValueError("禁止输出坐标 (x,y)，只输出 element ID 数组")
        else:
            ids.append(int(item))
    if len(ids) < MIN_CLICK_STEPS:
        raise ValueError(f"解析到 {len(ids)} 个 ID，至少需要 {MIN_CLICK_STEPS}")
    return ids


def _omni_window_to_screen_xy(
    center_x: int,
    center_y: int,
    window_region: tuple[int, int, int, int] | None,
) -> tuple[int, int]:
    """OmniParser 中心点为窗口内坐标，转为屏幕绝对坐标。"""
    if not window_region:
        return center_x, center_y
    left, top, _w, _h = window_region
    return left + center_x, top + center_y


def map_element_ids_to_clicks(
    element_ids: list[int],
    elements_dict: dict[int, dict[str, int]],
    window_region: tuple[int, int, int, int] | None,
) -> list[PlannedClick]:
    """狙击手：VLM ID → OmniParser elements_dict → 屏幕像素。"""
    clicks: list[PlannedClick] = []
    for eid in element_ids:
        if eid not in elements_dict:
            logger.error(
                "[hand] VLM 幻觉 ID=%s 不在 elements_dict（共 %d 项），跳过",
                eid,
                len(elements_dict),
            )
            continue
        row = elements_dict[eid]
        cx, cy = int(row["center_x"]), int(row["center_y"])
        sx, sy = _omni_window_to_screen_xy(cx, cy, window_region)
        clicks.append(
            PlannedClick(element_id=eid, x=sx, y=sy, source="omni+vl-id")
        )
    if len(clicks) < MIN_CLICK_STEPS:
        raise ValueError(
            f"有效映射 {len(clicks)}/{len(element_ids)} 个 ID；"
            f"IDs={element_ids}，可用={sorted(elements_dict.keys())[:30]}…"
        )
    return clicks


def audit_element_ids(
    element_ids: list[int],
    elements_dict: dict[int, dict[str, int]],
) -> list[str]:
    issues: list[str] = []
    if len(elements_dict) < MIN_OMNI_ELEMENTS:
        issues.append(
            f"OmniParser 仅标定 {len(elements_dict)} 个元素（<{MIN_OMNI_ELEMENTS}），"
            "请降低 bbox/iou 或确认计算器在前台"
        )
    for i, eid in enumerate(element_ids):
        if eid not in elements_dict:
            issues.append(
                f"第 {i + 1} 步 element_id={eid} 不在 elements_dict；"
                f"合法 ID 示例: {sorted(elements_dict.keys())[:20]}"
            )
    return list(dict.fromkeys(issues))


def _extract_planned_clicks_json(text: str) -> list[PlannedClick]:
    """解析 VLM 返回的带坐标点击序列（VL 坐标模式）。"""
    raw = _strip_llm_json_text(text)
    if not raw:
        raise ValueError("LLM 返回为空")
    obj = json.loads(raw)
    clicks: list[PlannedClick] = []
    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                key = str(item.get("key") or item.get("button") or "")
                x, y = int(item["x"]), int(item["y"])
                clicks.append(
                    PlannedClick(
                        element_id=-1, key=key, x=x, y=y, source="vl-coords"
                    )
                )
    if len(clicks) < MIN_CLICK_STEPS:
        raise ValueError(f"解析到 {len(clicks)} 次点击，至少需要 {MIN_CLICK_STEPS}")
    return clicks


def _vl_clicks_to_screen_absolute(
    clicks: list[PlannedClick],
    region: tuple[int, int, int, int] | None,
) -> list[PlannedClick]:
    if not region or not clicks:
        return clicks
    left, top, w, h = region
    max_x = max(c.x for c in clicks)
    max_y = max(c.y for c in clicks)
    if max_x <= w + 30 and max_y <= h + 30:
        logger.info(
            "[brain] VL 坐标转屏幕绝对 +(%d,%d) (max=%d,%d vs %d×%d)",
            left,
            top,
            max_x,
            max_y,
            w,
            h,
        )
        for c in clicks:
            c.x += left
            c.y += top
    return clicks


def audit_plan_clicks(
    clicks: list[PlannedClick],
    task: CalculatorTask,
    image_h: int,
) -> list[str]:
    issues: list[str] = []
    expected = _expr_to_expected_keys(task.expression)
    if len(clicks) != len(expected):
        issues.append(
            f"点击步数 {len(clicks)} 与算式逐键数 {len(expected)} 不一致；期望：{' '.join(expected)}"
        )
    y_123 = [c.y for c in clicks if _normalize_plan_key(c.key) in "123"]
    row_123 = max(y_123) if y_123 else None
    margin = max(20, int(image_h * 0.035))
    for c in clicks:
        k = _normalize_plan_key(c.key)
        if k == "0" and row_123 is not None and c.y <= row_123 + margin:
            issues.append(f"按键 0 的 y={c.y} 与 1/2/3 行重合，须点在更靠下的底行 0")
        if k == "." and row_123 is not None and c.y <= row_123 + margin:
            issues.append(f"小数点 . 的 y={c.y} 与 1/2/3 行重合，须与 0 同底行")
    return list(dict.fromkeys(issues))


def _build_clear_prompt_id() -> str:
    return (
        "这是一张计算器截图，所有可点击按钮已用红色框和数字 ID 标出。\n"
        "在输入新算式前，判断是否需要先清空上一次计算残留。\n"
        "**最高指令：绝对不能输出任何坐标 (x,y)！**\n"
        "逻辑：主屏若仍是上次结果（如 500、807）或未完成算式 → need_clear=true，"
        "找到标有 C 或 CE 的红色框 ID；若已是待机 0 → need_clear=false。\n"
        "只输出一个 JSON 对象（不要 markdown）：\n"
        '  {"need_clear":true,"element_id":12,"reason":"显示屏为500"}\n'
        '  {"need_clear":false,"reason":"显示屏为0"}\n'
    )


def _parse_clear_decision_id_json(text: str) -> ClearDecision:
    raw = _strip_llm_json_text(text)
    obj = json.loads(raw)
    need = bool(obj.get("need_clear"))
    reason = str(obj.get("reason") or "")
    if not need:
        return ClearDecision(need_clear=False, reason=reason or "无需清屏", by_id=True)
    eid = obj.get("element_id", obj.get("id"))
    if eid is None:
        raise ValueError(f"need_clear=true 但缺少 element_id: {obj}")
    return ClearDecision(
        need_clear=True, element_id=int(eid), reason=reason, by_id=True
    )


def _build_clear_prompt_coords(region_hint: str) -> str:
    return (
        "这是 Windows 计算器窗口原图。输入新算式前，判断是否需点 C/CE 清空。\n"
        f"{region_hint}\n"
        "若主屏仍是上次结果 → need_clear=true，给出 C/CE 按钮在本图内的 (x,y)。\n"
        "若已是待机 0 → need_clear=false。\n"
        "只输出 JSON：\n"
        '  {"need_clear":true,"x":120,"y":200,"reason":"…"}\n'
        '  {"need_clear":false,"reason":"…"}\n'
    )


def _parse_clear_decision_coords_json(text: str) -> ClearDecision:
    raw = _strip_llm_json_text(text)
    obj = json.loads(raw)
    need = bool(obj.get("need_clear"))
    reason = str(obj.get("reason") or "")
    if not need:
        return ClearDecision(need_clear=False, reason=reason, by_id=False)
    if "x" not in obj or "y" not in obj:
        raise ValueError(f"need_clear=true 但缺少 x/y: {obj}")
    return ClearDecision(
        need_clear=True,
        x=int(obj["x"]),
        y=int(obj["y"]),
        reason=reason,
        by_id=False,
    )


def llm_decide_clear_calculator(
    snapshot: ScreenSnapshot, *, model: str, by_id: bool
) -> ClearDecision:
    client = _openai_client()
    if by_id:
        prompt, img = _build_clear_prompt_id(), _image_message_part_annotated(snapshot)
        tag = "Clear(VL-ID)"
    else:
        prompt = _build_clear_prompt_coords(_region_hint_for_vl(snapshot))
        img = _image_message_part_raw(snapshot)
        tag = "Clear(VL-坐标)"
    logger.info("[brain] %s model=%s …", tag, model)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, img]}],
        temperature=0.0,
        max_tokens=256,
    )
    content = (resp.choices[0].message.content or "").strip()
    logger.info("[brain] %s 原始回复: %s", tag, content[:800])
    if by_id:
        return _parse_clear_decision_id_json(content)
    return _parse_clear_decision_coords_json(content)


def act_auto_clear_if_needed(
    snapshot: ScreenSnapshot,
    *,
    model: str,
    use_mock: bool = False,
    skip_clicks: bool = False,
    enabled: bool = True,
) -> ScreenSnapshot:
    """清屏：VLM 选 ID → OmniParser 坐标点击 → 重新 Observe 刷新标注。"""
    if not enabled:
        logger.info("[step 2/5] Clear — 已禁用 (--no-auto-clear)")
        return snapshot
    by_id = should_plan_by_element_ids(snapshot)
    logger.info(
        "[step 2/5] Clear — %s",
        "VLM+Omni ID" if by_id else "VLM 读原图估 C/CE 坐标",
    )
    try:
        decision = llm_decide_clear_calculator(snapshot, model=model, by_id=by_id)
    except Exception:
        logger.exception("[warn] Clear 阶段解析失败，继续 Plan")
        return snapshot
    if not decision.need_clear:
        logger.info("[brain] Clear 跳过: %s", decision.reason)
        return snapshot
    if decision.by_id:
        try:
            clicks = map_element_ids_to_clicks(
                [decision.element_id],
                snapshot.elements_dict,
                snapshot.window_region,
            )
            cx, cy = clicks[0].x, clicks[0].y
            label = f"C(id={decision.element_id})"
        except ValueError as e:
            logger.error("[warn] 清屏 ID 映射失败: %s", e)
            return snapshot
    else:
        pt = _vl_clicks_to_screen_absolute(
            [
                PlannedClick(
                    element_id=-1,
                    x=decision.x,
                    y=decision.y,
                    key="C",
                    source="vl-clear",
                )
            ],
            snapshot.window_region,
        )[0]
        cx, cy, label = pt.x, pt.y, "C(vl-coords)"
    res = physical_click_xy(cx, cy, label=label, skip_real_click=skip_clicks)
    if not res.get("ok"):
        logger.error("[warn] 清屏点击失败: %s", res)
        return snapshot
    time.sleep(0.6)
    logger.info("[eye] 清屏后重新 Observe（OmniParser 全量刷新）")
    return get_holographic_screen(use_mock=use_mock)


def _elements_id_catalog(snapshot: ScreenSnapshot) -> str:
    """标注图 ID 与按键文案对照（辅助 VLM 选对 element_id）。"""
    by_id: dict[int, dict[str, Any]] = {}
    for row in snapshot.elements:
        try:
            by_id[int(row["id"])] = row
        except (TypeError, ValueError, KeyError):
            continue
    parts: list[str] = []
    for eid in sorted(snapshot.elements_dict.keys()):
        content = str((by_id.get(eid) or {}).get("content") or "?")
        parts.append(f"{eid}={content}")
    if not parts:
        return ""
    return "红框 ID 与按键文案：" + "，".join(parts) + "。\n"


def _build_plan_prompt_ids(
    task: CalculatorTask,
    snapshot: ScreenSnapshot,
    *,
    retry_feedback: str = "",
) -> str:
    expr = _normalize_expr_display(task.expression)
    seq = _expr_key_sequence_hint(expr)
    catalog = _elements_id_catalog(snapshot)
    body = (
        "这是一张计算器截图，所有可点击按钮都已经用红色框和数字 ID 标出。\n"
        f"你的任务是完成 `{expr}` 的计算操作（期望结果约 {task.expect}）。\n"
        "**最高指令：你绝对不能输出任何坐标 (x,y)！**\n"
        "你必须仔细观察红色标注图，按顺序找到每个需要按下的键"
        "（数字、运算符、等号）所对应的红色方框 ID。\n"
        f"操作顺序提示：{seq}\n"
        f"{catalog}"
        "请且仅请以严格的 JSON 数组格式输出你需要依次按下的元素 ID 列表。\n"
        "例如：`[15, 23, 8, 4, 12, 9]`。不要输出任何解释，只输出 JSON 数组。\n"
        "清屏(C/CE)已由系统处理，不要包含 C/CE 的 ID。"
    )
    if retry_feedback:
        body += f"\n\n【上次 ID 计划有误，请修正】\n{retry_feedback}"
    return body


def llm_plan_element_ids(
    snapshot: ScreenSnapshot,
    task: CalculatorTask,
    *,
    model: str,
    max_attempts: int = MAX_PLAN_ATTEMPTS,
) -> list[int]:
    """Plan：VLM 读标注图，仅输出 element_id 列表。"""
    client = _openai_client()
    retry_feedback = ""
    element_ids: list[int] = []
    logger.info(
        "[brain] Plan(VL-ID) expr=%s expect=%s model=%s elements=%d …",
        task.expression,
        task.expect,
        model,
        len(snapshot.elements_dict),
    )
    for attempt in range(1, max(1, max_attempts) + 1):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _build_plan_prompt_ids(
                                task, snapshot, retry_feedback=retry_feedback
                            ),
                        },
                        _image_message_part_annotated(snapshot),
                    ],
                }
            ],
            temperature=0.05 if attempt > 1 else 0.1,
            max_tokens=512,
        )
        content = (resp.choices[0].message.content or "").strip()
        logger.info(
            "[brain] Plan(VL-ID) 尝试 %d/%d: %s",
            attempt,
            max_attempts,
            content[:1200],
        )
        try:
            element_ids = _extract_element_ids_json(content)
        except (ValueError, json.JSONDecodeError) as e:
            issues = [str(e)]
        else:
            issues = audit_element_ids(element_ids, snapshot.elements_dict)
        if not issues:
            logger.info("[brain] Plan(VL-ID) 通过: %s", element_ids)
            return element_ids
        logger.warning(
            "[brain] Plan ID 校验未通过 (%d/%d):\n  %s",
            attempt,
            max_attempts,
            "\n  ".join(issues),
        )
        retry_feedback = "\n".join(f"- {x}" for x in issues)
        if attempt >= max_attempts:
            logger.error("[brain] Plan 达最大重试，使用末次 ID 列表")
            break
    return element_ids


def _build_plan_prompt_coords(
    task: CalculatorTask,
    region_hint: str,
    *,
    retry_feedback: str = "",
) -> str:
    expr = _normalize_expr_display(task.expression)
    seq = _expr_key_sequence_hint(expr)
    body = (
        "这是 Windows 标准计算器窗口原图（无红框 ID）。\n"
        f"任务：计算 {expr}（期望约 {task.expect}）。\n"
        f"请按顺序点击：{seq}。\n"
        f"{_CALC_LAYOUT_SEMANTICS}\n"
        "清屏已由上一步处理，不要点 C/CE。\n"
        f"{region_hint}\n"
        "只输出 JSON 数组，每项：`{{\"key\":\"1\",\"x\":120,\"y\":580}}`，最后一项为 =。\n"
        "x/y 为按钮中心在本图内的像素坐标。"
    )
    if retry_feedback:
        body += f"\n\n【上次坐标计划有误，请修正】\n{retry_feedback}"
    return body


def llm_plan_click_coordinates_vl(
    snapshot: ScreenSnapshot,
    task: CalculatorTask,
    *,
    model: str,
    max_attempts: int = MAX_PLAN_ATTEMPTS,
) -> list[PlannedClick]:
    client = _openai_client()
    region_hint = _region_hint_for_vl(snapshot)
    img_h = _snapshot_image_height(snapshot)
    retry_feedback = ""
    clicks: list[PlannedClick] = []
    logger.info(
        "[brain] Plan(VL-坐标) expr=%s model=%s（Omni 元素不足，VLM 读原图）",
        task.expression,
        model,
    )
    for attempt in range(1, max(1, max_attempts) + 1):
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _build_plan_prompt_coords(
                                task, region_hint, retry_feedback=retry_feedback
                            ),
                        },
                        _image_message_part_raw(snapshot),
                    ],
                }
            ],
            temperature=0.05 if attempt > 1 else 0.1,
            max_tokens=1024,
        )
        content = (resp.choices[0].message.content or "").strip()
        logger.info(
            "[brain] Plan(VL-坐标) 尝试 %d/%d: %s",
            attempt,
            max_attempts,
            content[:1200],
        )
        clicks = _extract_planned_clicks_json(content)
        clicks = _vl_clicks_to_screen_absolute(clicks, snapshot.window_region)
        issues = audit_plan_clicks(clicks, task, img_h)
        if not issues:
            for i, c in enumerate(clicks):
                logger.info(
                    "[brain] Plan 第%d步 key=%s → (%d,%d)",
                    i + 1,
                    c.key or "?",
                    c.x,
                    c.y,
                )
            return clicks
        logger.warning(
            "[brain] Plan 坐标校验未通过 (%d/%d): %s",
            attempt,
            max_attempts,
            "; ".join(issues),
        )
        retry_feedback = "\n".join(f"- {x}" for x in issues)
        if attempt >= max_attempts:
            break
    return clicks


def plan_clicks(
    snapshot: ScreenSnapshot,
    task: CalculatorTask,
    *,
    model: str,
) -> list[PlannedClick]:
    if should_plan_by_element_ids(snapshot):
        logger.info(
            "[step 3/5] Plan — %s + OmniParser ID 模式「%s」",
            model,
            task.expression,
        )
        ids = llm_plan_element_ids(snapshot, task, model=model)
        return map_element_ids_to_clicks(
            ids, snapshot.elements_dict, snapshot.window_region
        )
    logger.info(
        "[step 3/5] Plan — %s 读原图自估坐标「%s」（自然检出 %d 个元素）",
        model,
        task.expression,
        _snapshot_natural_omni_count(snapshot),
    )
    return llm_plan_click_coordinates_vl(snapshot, task, model=model)


def physical_click_xy(x: int, y: int, *, label: str = "", skip_real_click: bool = False) -> dict[str, Any]:
    logger.info("[hand] click %s → (%d, %d)", label or "?", x, y)
    if skip_real_click:
        return {"ok": True, "x": x, "y": y, "skipped": True}
    try:
        import pyautogui
    except ImportError as e:
        return {"ok": False, "error": f"pyautogui_not_installed:{e}"}
    pyautogui.FAILSAFE = True
    try:
        pyautogui.moveTo(x, y, duration=0.2)
        pyautogui.click()
    except Exception as e:
        return {"ok": False, "error": repr(e), "x": x, "y": y}
    return {"ok": True, "x": x, "y": y, "label": label}


def llm_read_calculator_result(
    snapshot: ScreenSnapshot,
    task: CalculatorTask,
    *,
    model: str,
) -> str:
    """Verify：视觉模型读计算器原图显示屏数字。"""
    client = _openai_client()
    expr = _normalize_expr_display(task.expression)
    prompt = (
        "这是 Windows 计算器截图。请读取**主显示屏**上当前显示的计算结果数字。\n"
        f"用户刚完成的算式是：{expr}，期望结果约为 {task.expect}。\n"
        "只回答结果数字（可极简说明），例如：807。"
    )
    logger.info("[brain] Verify(VL 原图) 请求 model=%s …", model)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    _image_message_part_raw(snapshot),
                ],
            }
        ],
        temperature=0.0,
        max_tokens=128,
    )
    answer = (resp.choices[0].message.content or "").strip()
    logger.info("[brain] Verify 回答: %s", answer)
    return answer


def run_calculator_test(
    *,
    task: CalculatorTask,
    use_mock: bool = False,
    skip_clicks: bool = False,
    click_delay_sec: float = 0.5,
    model: str | None = None,
    auto_clear: bool = True,
) -> int:
    """
    主流程：Observe → Clear(VL) → Plan → Act → Verify。
    返回进程退出码 0=成功（Verify 读数与 expect 匹配）。
    """
    model = model or _default_vision_model()
    expr = _normalize_expr_display(task.expression)
    logger.info("=" * 60)
    logger.info(
        "Calculator Agent  %s → expect=%s  model=%s mock=%s",
        expr,
        task.expect,
        model,
        use_mock,
    )
    if task.note:
        logger.info("说明: %s", task.note)
    logger.info("=" * 60)

    # ── 1. Observe ──────────────────────────────────────────────
    logger.info("[step 1/5] Observe — get_holographic_screen")
    snap_before = get_holographic_screen(use_mock=use_mock)
    if not snap_before.ok:
        logger.error("[fail] 初次截图解析失败: %s", snap_before.error)
        return 1
    id_mode = should_plan_by_element_ids(snap_before)
    logger.info(
        "[brain] Plan 模式: %s（自然检出 %d，总计 %d）",
        "OmniParser+ID" if id_mode else "VLM 原图坐标",
        _snapshot_natural_omni_count(snap_before),
        len(snap_before.elements_dict),
    )
    if not id_mode:
        if not snap_before.raw_image_path or not Path(snap_before.raw_image_path).is_file():
            logger.error("[fail] VL 坐标模式需要 screen_raw.png")
            return 1
    else:
        try:
            _resolve_annotated_image_path(snap_before)
        except RuntimeError as e:
            logger.error("[fail] ID 模式需要标注图: %s", e)
            return 1

    # ── 2. Clear（VLM 选 ID，OmniParser 坐标）────────────────────
    snap_before = act_auto_clear_if_needed(
        snap_before,
        model=model,
        use_mock=use_mock,
        skip_clicks=skip_clicks,
        enabled=auto_clear,
    )

    # ── 3. Plan（VLM 只输出 ID）──────────────────────────────────
    try:
        planned = plan_clicks(snap_before, task, model=model)
    except Exception:
        logger.exception("[fail] Plan 阶段失败")
        return 1

    # ── 4. Act（手）──────────────────────────────────────────────
    logger.info(
        "[step 4/5] Act — %d 次点击 (OmniParser 坐标 + VLM ID)",
        len(planned),
    )
    for i, click in enumerate(planned):
        res = physical_click_xy(
            click.x,
            click.y,
            label=f"id={click.element_id}({click.source})",
            skip_real_click=skip_clicks,
        )
        if not res.get("ok"):
            logger.error("[fail] 第 %d 次点击失败: %s", i + 1, res)
            return 1
        lbl = (
            f"id={click.element_id}"
            if click.element_id >= 0
            else (click.key or click.source)
        )
        logger.info(
            "[hand] 第 %d/%d 次完成 %s → (%d,%d)",
            i + 1,
            len(planned),
            lbl,
            click.x,
            click.y,
        )
        time.sleep(click_delay_sec)

    # 等号后多等一会，确保结果显示在显示屏
    time.sleep(1.2)

    # ── 5. Verify ───────────────────────────────────────────────
    logger.info("[step 5/5] Verify — 再次截图并读取结果")
    snap_after = get_holographic_screen(use_mock=use_mock)
    if not snap_after.ok:
        logger.error("[fail] 复核截图失败: %s", snap_after.error)
        return 1

    try:
        final_answer = llm_read_calculator_result(snap_after, task, model=model)
    except Exception:
        logger.exception("[fail] Verify 阶段失败")
        return 1

    logger.info("=" * 60)
    logger.info("Final Answer: %s", final_answer)
    logger.info("=" * 60)

    if _answer_matches_expect(final_answer, task.expect):
        logger.info("[pass] 检测到预期结果 %s（算式 %s）", task.expect, expr)
        return 0

    logger.error("[fail] 期望 %s，Verify 回答: %s", task.expect, final_answer)
    return 1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    ap = argparse.ArgumentParser(
        description="计算器测试：Omni 够用时 ID+坐标映射，不足时 VLM 原图坐标"
    )
    ap.add_argument(
        "--mock",
        action="store_true",
        help="Mock 全息屏（不跑 OmniParser；坐标为占位）",
    )
    ap.add_argument(
        "--mock-click-skip",
        action="store_true",
        help="不移动真实鼠标（与 --mock 联调 LLM）",
    )
    ap.add_argument(
        "--model",
        default=None,
        help="视觉模型，默认 .env 的 CALCULATOR_AGENT_MODEL / INTENT_GATEWAY_MULTIMODAL_MODEL（qwen-vl-max）",
    )
    ap.add_argument("--click-delay", type=float, default=0.5, help="每次点击间隔秒")
    ap.add_argument("--expr", default=None, help='算式，如 "99×8+15" 或 "99*8+15"')
    ap.add_argument("--expect", default=None, help="期望显示屏结果；省略则按标准计算器顺序求值推算")
    ap.add_argument(
        "--case",
        dest="case_id",
        default=None,
        help="内置用例 id：basic | mul_add | sub | decimal | chain",
    )
    ap.add_argument(
        "--suite",
        action="store_true",
        help="依次运行内置 5 道进阶题（题间间隔，Plan 提示可点 C 清屏）",
    )
    ap.add_argument("--list-cases", action="store_true", help="列出内置用例并退出")
    ap.add_argument(
        "--suite-stop-on-fail",
        action="store_true",
        help="套件模式下首题失败即停止（默认继续跑完）",
    )
    ap.add_argument(
        "--no-auto-clear",
        action="store_true",
        help="禁用开测前 VL 自动识别并点击 C/CE（默认开启，利于 --suite 连跑）",
    )
    args = ap.parse_args()

    if args.list_cases:
        for t in CASE_SUITE:
            print(f"  {t.case_id:10}  {t.expression:12}  expect={t.expect:6}  # {t.note}")
        return 0

    use_mock = args.mock or (os.environ.get("CALCULATOR_AGENT_MOCK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    skip_clicks = args.mock_click_skip or use_mock

    if not use_mock:
        logger.info(
            "提示: 请打开 Windows「计算器」并保持窗口在前台（勿最小化）；"
            "脚本将自动截取计算器窗口区域。推理使用 .venv-omniparser。"
        )

    auto_clear = not args.no_auto_clear
    common = dict(
        use_mock=use_mock,
        skip_clicks=skip_clicks,
        click_delay_sec=args.click_delay,
        model=args.model,
        auto_clear=auto_clear,
    )

    if args.suite:
        failed: list[str] = []
        for i, t in enumerate(CASE_SUITE, 1):
            logger.info("")
            logger.info("######## 套件 %d/%d  case=%s ########", i, len(CASE_SUITE), t.case_id)
            code = run_calculator_test(task=t, **common)
            if code != 0:
                failed.append(t.case_id)
                if args.suite_stop_on_fail:
                    break
            if i < len(CASE_SUITE):
                time.sleep(2.5)
        if failed:
            logger.error("[suite fail] 未通过: %s", ", ".join(failed))
            return 1
        logger.info("[suite pass] 全部 %d 题通过", len(CASE_SUITE))
        return 0

    try:
        task = _resolve_task(args.expr, args.expect, args.case_id)
    except ValueError as e:
        logger.error("%s", e)
        return 2

    return run_calculator_test(task=task, **common)


if __name__ == "__main__":
    raise SystemExit(main())
