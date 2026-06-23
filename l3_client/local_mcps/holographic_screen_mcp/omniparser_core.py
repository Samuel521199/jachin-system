"""
OmniParser-v2.0 推理核心（与 scripts/test_omniparser_local.py 对齐）。

L3 主进程若未使用 .venv-omniparser，则通过子进程调用同一模块的 worker 入口。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from base64 import b64decode
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger("holographic.omniparser")

_HANDLER: Any = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def omnioutput_dir() -> Path:
    """仓库 scripts/omnioutput，便于查看每次 OmniParser 标注图。"""
    raw = (os.environ.get("OMNIPARSER_OUTPUT_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (repo_root() / "scripts" / "omnioutput").resolve()


def publish_to_omnioutput(
    work_dir: Path,
    *,
    tag: str = "",
) -> dict[str, str]:
    """
    将 work_dir 内标注图 / JSON / 原图复制到 scripts/omnioutput（或 OMNIPARSER_OUTPUT_DIR）。
    """
    out: dict[str, str] = {}
    work_dir = work_dir.resolve()
    if not work_dir.is_dir():
        return out

    dest = omnioutput_dir()
    dest.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    prefix = f"{stamp}_{tag}" if (tag or "").strip() else stamp

    for name in ("parsed_output.jpg", "parsed_output.png"):
        src = work_dir / name
        if src.is_file():
            dst = dest / f"{prefix}_annotated{src.suffix.lower()}"
            shutil.copy2(src, dst)
            out["annotated_image"] = str(dst)
            break

    json_src = work_dir / "parsed_result.json"
    if json_src.is_file():
        dst = dest / f"{prefix}_result.json"
        shutil.copy2(json_src, dst)
        out["parsed_result"] = str(dst)

    raw_src = work_dir / "screen_raw.png"
    if raw_src.is_file():
        dst = dest / f"{prefix}_raw.png"
        shutil.copy2(raw_src, dst)
        out["screen_raw"] = str(dst)

    if out:
        logger.info("[holographic] 已写入 omnioutput: %s", out.get("annotated_image") or dest)
    return out


def resolve_model_dir() -> Path:
    raw = (os.environ.get("OMNIPARSER_MODEL_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (repo_root() / "model" / "OmniParser-v2.0").resolve()


def resolve_omniparser_python() -> Path:
    raw = (os.environ.get("OMNIPARSER_PYTHON") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    venv_py = repo_root() / ".venv-omniparser" / "Scripts" / "python.exe"
    if venv_py.is_file():
        return venv_py.resolve()
    if sys.platform != "win32":
        alt = repo_root() / ".venv-omniparser" / "bin" / "python"
        if alt.is_file():
            return alt.resolve()
    return Path(sys.executable).resolve()


def should_run_subprocess() -> bool:
    force = (os.environ.get("OMNIPARSER_FORCE_SUBPROCESS") or "").strip().lower()
    if force in ("1", "true", "yes"):
        return True
    inproc = (os.environ.get("OMNIPARSER_INPROCESS") or "").strip().lower()
    if inproc in ("1", "true", "yes"):
        return False
    venv_py = repo_root() / ".venv-omniparser" / "Scripts" / "python.exe"
    if not venv_py.is_file() and sys.platform != "win32":
        venv_py = repo_root() / ".venv-omniparser" / "bin" / "python"
    if venv_py.is_file():
        try:
            return Path(sys.executable).resolve() != venv_py.resolve()
        except OSError:
            return True
    return False


def _ensure_model_on_path(model_dir: Path) -> None:
    if not model_dir.is_dir():
        raise FileNotFoundError(f"模型目录不存在: {model_dir}")
    for sub in ("icon_detect/model.pt", "icon_caption", "handler.py"):
        if not (model_dir / sub).exists():
            raise FileNotFoundError(f"缺少必要文件: {model_dir / sub}")
    md = str(model_dir)
    if md not in sys.path:
        sys.path.insert(0, md)


def _bbox_norm_to_xyxy_px(bbox: list[float], w: int, h: int) -> list[int]:
    if len(bbox) != 4:
        return []
    x1, y1, x2, y2 = bbox
    return [
        int(round(float(x1) * w)),
        int(round(float(y1) * h)),
        int(round(float(x2) * w)),
        int(round(float(y2) * h)),
    ]


def elements_from_bboxes(
    bboxes: list[dict[str, Any]],
    image_size: tuple[int, int],
) -> list[dict[str, Any]]:
    w, h = image_size
    elements: list[dict[str, Any]] = []
    for idx, box in enumerate(bboxes):
        raw = box.get("bbox") or []
        if not isinstance(raw, (list, tuple)) or len(raw) < 4:
            continue
        norm = [float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])]
        px = _bbox_norm_to_xyxy_px(norm, w, h)
        cx = (px[0] + px[2]) / 2.0 if px else None
        cy = (px[1] + px[3]) / 2.0 if px else None
        elements.append(
            {
                "id": idx,
                "bbox_xyxy_pixels": px,
                "center_xy_pixels": [round(cx, 1), round(cy, 1)] if cx is not None else None,
                "type": box.get("type"),
                "content": box.get("content"),
                "interactivity": box.get("interactivity"),
                "source": box.get("source"),
            }
        )
    return elements


def simplify_elements_for_llm(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """供大模型使用的精简坐标表（与标注图编号一致，从 0 起）。"""
    out: list[dict[str, Any]] = []
    for el in elements:
        center = el.get("center_xy_pixels") or []
        cx = cy = None
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            cx, cy = center[0], center[1]
        row: dict[str, Any] = {
            "id": el.get("id"),
            "center_x": int(round(float(cx))) if cx is not None else None,
            "center_y": int(round(float(cy))) if cy is not None else None,
        }
        if el.get("type"):
            row["type"] = el.get("type")
        content = (el.get("content") or "").strip()
        if content:
            row["content"] = content[:160]
        out.append(row)
    return out


def _get_handler(model_dir: Path) -> Any:
    global _HANDLER
    if _HANDLER is not None:
        return _HANDLER
    _ensure_model_on_path(model_dir)
    from handler import EndpointHandler  # type: ignore  # noqa: E402

    logger.info("[holographic] 加载 EndpointHandler model_dir=%s", model_dir)
    t0 = time.perf_counter()
    _HANDLER = EndpointHandler(model_dir=str(model_dir))
    logger.info("[holographic] EndpointHandler 就绪 (%.1fs)", time.perf_counter() - t0)
    return _HANDLER


def _keypad_probe_enabled() -> bool:
    """默认关闭：网格探针坐标易偏，元素不足时改由 VLM 读原图估坐标。"""
    return (os.environ.get("CALCULATOR_KEYPAD_PROBE") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _apply_calculator_keypad_probe(
    report: dict[str, Any],
    image_path: Path,
    work_dir: Path,
) -> dict[str, Any]:
    """
    OmniParser YOLO 对 Win11 计算器扁平按键常只检出 1～4 个框；
    用 calculator_layout 实测比例补全 elements 并重绘红框标注图。
    """
    if not _keypad_probe_enabled() or not report.get("ok"):
        return report
    from .calculator_layout import (
        build_keypad_probe_elements,
        omniparser_misses_keypad,
        render_keypad_annotated_image,
    )

    els = report.get("elements_llm") or report.get("elements") or []
    try:
        min_keys = int(os.environ.get("CALCULATOR_KEYPAD_PROBE_MIN") or "10")
    except ValueError:
        min_keys = 10
    if not omniparser_misses_keypad(els, min_keys=min_keys):
        return report

    sz = report.get("image_size") or {}
    w, h = int(sz.get("w") or 0), int(sz.get("h") or 0)
    if w < 80 or h < 80:
        return report

    probe = build_keypad_probe_elements(w, h)
    if len(probe) < min_keys:
        logger.warning("[holographic] keypad_probe 生成 %d 项，仍不足 %d", len(probe), min_keys)
        return report

    ann_path = work_dir / "parsed_output.jpg"
    try:
        render_keypad_annotated_image(image_path, probe, ann_path)
    except Exception as e:
        logger.warning("[holographic] keypad_probe 标注图绘制失败: %s", e)
        return report

    simplified = simplify_elements_for_llm(probe)
    report["elements"] = probe
    report["elements_llm"] = simplified
    report["element_count"] = len(probe)
    report["keypad_probe"] = True
    report["outputs"] = dict(report.get("outputs") or {})
    report["outputs"]["annotated_image"] = str(ann_path)
    report["outputs"]["keypad_probe"] = True

    json_path = work_dir / "parsed_result.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    tag = (os.environ.get("OMNIPARSER_OUTPUT_TAG") or "").strip()
    published = publish_to_omnioutput(work_dir, tag=tag)
    if published:
        report["omnioutput"] = published

    logger.warning(
        "[holographic] YOLO 按键过少(%d)，已启用 calculator_keypad_probe → %d 项（窗口 %d×%d）",
        len(els),
        len(probe),
        w,
        h,
    )
    return report


def run_omniparser_inprocess(
    image_path: Path,
    *,
    work_dir: Path,
    bbox_threshold: float = 0.05,
    iou_threshold: float | None = 0.7,
) -> dict[str, Any]:
    from PIL import Image

    model_dir = resolve_model_dir()
    image_path = image_path.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    handler = _get_handler(model_dir)
    payload = {
        "inputs": {
            "image": str(image_path),
            "image_size": {"w": w, "h": h},
            "bbox_threshold": bbox_threshold,
            "iou_threshold": iou_threshold,
        }
    }
    t0 = time.perf_counter()
    result = handler(payload)
    logger.info("[holographic] 推理完成 %.1fs", time.perf_counter() - t0)

    bboxes = result.get("bboxes") or []
    elements = elements_from_bboxes(bboxes, (w, h))
    simplified = simplify_elements_for_llm(elements)

    json_path = work_dir / "parsed_result.json"
    ann_path = work_dir / "parsed_output.jpg"
    report: dict[str, Any] = {
        "ok": True,
        "model_dir": str(model_dir),
        "image_size": {"w": w, "h": h},
        "element_count": len(elements),
        "elements": elements,
        "elements_llm": simplified,
        "work_dir": str(work_dir),
        "outputs": {"json": str(json_path), "annotated_image": str(ann_path)},
    }

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    encoded = result.get("image") or ""
    if isinstance(encoded, str) and encoded.strip():
        raw = b64decode(encoded)
        try:
            Image.open(BytesIO(raw)).convert("RGB").save(ann_path, format="JPEG", quality=92)
            report["annotated_image_bytes_path"] = str(ann_path)
        except Exception:
            ann_path = work_dir / "parsed_output.png"
            ann_path.write_bytes(raw)
            report["outputs"]["annotated_image"] = str(ann_path)
    else:
        report["ok"] = False
        report["error"] = "handler 未返回 image base64"

    if report.get("ok"):
        report = _apply_calculator_keypad_probe(report, image_path, work_dir)
        tag = (os.environ.get("OMNIPARSER_OUTPUT_TAG") or "").strip()
        published = publish_to_omnioutput(work_dir, tag=tag)
        if published:
            report["omnioutput"] = published
            if published.get("annotated_image"):
                report["outputs"]["omnioutput_annotated"] = published["annotated_image"]

    return report


def run_omniparser_subprocess(
    image_path: Path,
    *,
    work_dir: Path,
    bbox_threshold: float = 0.05,
    iou_threshold: float | None = 0.7,
    timeout_sec: float | None = None,
) -> dict[str, Any]:
    py = resolve_omniparser_python()
    root = repo_root()
    work_dir.mkdir(parents=True, exist_ok=True)
    if timeout_sec is None:
        try:
            timeout_sec = float(os.environ.get("OMNIPARSER_TIMEOUT_SEC") or "900")
        except ValueError:
            timeout_sec = 900.0

    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [
        str(py),
        "-m",
        "l3_client.local_mcps.holographic_screen_mcp.worker",
        "--image",
        str(image_path.resolve()),
        "--work-dir",
        str(work_dir.resolve()),
        "--bbox-threshold",
        str(bbox_threshold),
        "--iou-threshold",
        str(iou_threshold if iou_threshold is not None else 0.7),
    ]
    logger.info("[holographic] 子进程推理: %s", " ".join(cmd[:6]))
    proc = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:2000]
        return {
            "ok": False,
            "error": f"omniparser_subprocess_exit_{proc.returncode}",
            "detail": err,
            "python": str(py),
        }
    line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    if not line:
        return {"ok": False, "error": "omniparser_subprocess_empty_stdout", "detail": proc.stderr}
    try:
        summary = json.loads(line)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"omniparser_subprocess_bad_json:{e}", "detail": line[:500]}
    if summary.get("ok") and (work_dir / "parsed_result.json").is_file():
        try:
            full = json.loads((work_dir / "parsed_result.json").read_text(encoding="utf-8"))
            summary.setdefault("elements", full.get("elements"))
            summary.setdefault("elements_llm", full.get("elements_llm"))
            summary.setdefault("outputs", full.get("outputs"))
        except Exception:
            pass
    if summary.get("ok"):
        summary = _apply_calculator_keypad_probe(summary, image_path, work_dir)
        tag = (os.environ.get("OMNIPARSER_OUTPUT_TAG") or "").strip()
        published = publish_to_omnioutput(work_dir, tag=tag)
        if published:
            summary["omnioutput"] = published
    return summary


def run_omniparser(
    image_path: Path,
    *,
    work_dir: Path,
    bbox_threshold: float = 0.05,
    iou_threshold: float | None = 0.7,
) -> dict[str, Any]:
    if should_run_subprocess():
        return run_omniparser_subprocess(
            image_path,
            work_dir=work_dir,
            bbox_threshold=bbox_threshold,
            iou_threshold=iou_threshold,
        )
    return run_omniparser_inprocess(
        image_path,
        work_dir=work_dir,
        bbox_threshold=bbox_threshold,
        iou_threshold=iou_threshold,
    )
