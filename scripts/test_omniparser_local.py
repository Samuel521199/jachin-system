#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地验证微软 OmniParser-v2.0（model/OmniParser-v2.0/handler.py）。

用法（仓库根目录）::

  python scripts/test_omniparser_local.py
  python scripts/test_omniparser_local.py --image D:\\path\\to\\test_screen.jpg
  python scripts/test_omniparser_local.py --bbox-threshold 0.05 --iou-threshold 0.7

输出（默认与脚本同目录，可用 --out-dir 覆盖）::
  - parsed_result.json   UI 元素 id / bbox / 类型 / 文案
  - parsed_output.png    带数字编号标注图（handler 内置 BoxAnnotator）
  - 同时复制到 scripts/omnioutput/ 便于查看（见该目录 README）

依赖见脚本末尾 INSTALL_NOTES 与 model/OmniParser-v2.0/requirements.txt。
首次运行会下载 EasyOCR 与 HuggingFace ``microsoft/Florence-2-base`` 处理器权重（caption 本体在 icon_caption/）。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any

# ── 硬编码模型根目录（与任务说明一致）────────────────────────────────────────
MODEL_DIR = Path(r"D:\Projects\jachi\jachin-system-main\model\OmniParser-v2.0")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEST_IMAGE = Path(__file__).resolve().parent / "test_screen.jpg"
VENV_PYTHON = REPO_ROOT / ".venv-omniparser" / "Scripts" / "python.exe"


def _print_torch_dll_fix() -> None:
    print(
        """
[omniparser-test] PyTorch 无法加载 c10.dll（WinError 1114）— 常见于 Anaconda base 里安装了过新的 torch。

建议（任选其一）：

  A) 使用仓库内独立 venv（推荐，已在本机验证 torch 2.2.2+cpu 可 import）::
     .\\scripts\\setup_omniparser_venv.ps1
     .\\.venv-omniparser\\Scripts\\python.exe scripts\\test_omniparser_local.py

  B) 修复当前 base 环境::
     pip uninstall -y torch torchvision torchaudio
     pip install torch==2.2.2+cpu torchvision==0.17.2+cpu --index-url https://download.pytorch.org/whl/cpu
     pip install numpy==1.26.4

  C) 系统层::
     安装 VC++ 2015-2022 x64: https://aka.ms/vs/17/release/vc_redist.x64.exe
     更新显卡驱动后重启

说明: 最新版 torch（2.5+）在部分 Windows 10/11 上会在 import 阶段触发 1114，与 OmniParser 无直接关系。
""",
        file=sys.stderr,
        flush=True,
    )


def preflight_torch() -> None:
    """在 import handler（→ easyocr → torch）之前自检。"""
    try:
        import os

        if hasattr(os, "add_dll_directory"):
            try:
                import importlib.util

                spec = importlib.util.find_spec("torch")
                if spec and spec.submodule_search_locations:
                    lib = Path(list(spec.submodule_search_locations)[0]) / "lib"
                    if lib.is_dir():
                        os.add_dll_directory(str(lib))
            except Exception:
                pass
        import torch  # noqa: F401

        print(
            f"[omniparser-test] PyTorch OK: {torch.__version__} "
            f"cuda={torch.cuda.is_available()} python={sys.executable}",
            flush=True,
        )
    except OSError as e:
        if getattr(e, "winerror", None) == 1114 or "c10.dll" in str(e).lower():
            _print_torch_dll_fix()
        raise SystemExit(2) from e
    except Exception as e:
        print(f"[omniparser-test] PyTorch 预检失败: {e}", file=sys.stderr, flush=True)
        raise SystemExit(2) from e


def _suggest_venv_if_broken_base() -> None:
    """若当前是 Anaconda base 且存在已配置好的 venv，提示切换解释器。"""
    exe = Path(sys.executable).resolve()
    if "anaconda" in str(exe).lower() or "conda" in str(exe).lower():
        if VENV_PYTHON.is_file():
            print(
                f"[omniparser-test] 提示: 当前解释器为 {exe}；"
                f"若遇 DLL 错误请改用 {VENV_PYTHON}",
                flush=True,
            )


def _ensure_model_on_path() -> None:
    md = MODEL_DIR.resolve()
    if not md.is_dir():
        raise FileNotFoundError(f"模型目录不存在: {md}")
    for sub in ("icon_detect/model.pt", "icon_caption", "handler.py"):
        if not (md / sub).exists():
            raise FileNotFoundError(f"缺少必要文件: {md / sub}")
    if str(md) not in sys.path:
        sys.path.insert(0, str(md))


def _resolve_test_image(path: Path | None) -> Path:
    if path and path.is_file():
        return path.resolve()
    if DEFAULT_TEST_IMAGE.is_file():
        return DEFAULT_TEST_IMAGE.resolve()
    # 兜底：截一张桌面图便于冒烟
    try:
        import pyautogui

        out = DEFAULT_TEST_IMAGE
        out.parent.mkdir(parents=True, exist_ok=True)
        pyautogui.screenshot().save(out)
        print(f"[omniparser-test] 未找到 test_screen.jpg，已截屏保存: {out}", flush=True)
        return out.resolve()
    except Exception as e:
        raise FileNotFoundError(
            f"请提供 --image 或将测试图放到 {DEFAULT_TEST_IMAGE}；截屏兜底失败: {e}"
        ) from e


def _bbox_norm_to_xyxy_px(bbox: list[float], w: int, h: int) -> list[int]:
    """handler 返回的 bbox 为归一化 xyxy（0~1）。"""
    if len(bbox) != 4:
        return []
    x1, y1, x2, y2 = bbox
    return [
        int(round(x1 * w)),
        int(round(y1 * h)),
        int(round(x2 * w)),
        int(round(y2 * h)),
    ]


def _elements_from_bboxes(
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
                "label_on_image": str(idx),
                "bbox_xyxy_normalized": norm,
                "bbox_xyxy_pixels": px,
                "center_xy_pixels": [round(cx, 1), round(cy, 1)] if cx is not None else None,
                "type": box.get("type"),
                "content": box.get("content"),
                "interactivity": box.get("interactivity"),
                "source": box.get("source"),
                "confidence": None,
                "confidence_note": (
                    "handler.py 未将 YOLO conf 写入 bboxes；"
                    "可调 --bbox-threshold 控制检测阈值。"
                ),
            }
        )
    return elements


def run_omniparser(
    image_path: Path,
    *,
    bbox_threshold: float = 0.05,
    iou_threshold: float | None = 0.7,
    out_dir: Path,
) -> dict[str, Any]:
    _ensure_model_on_path()

    # 延迟导入：handler 在模块级会初始化 easyocr.Reader（较慢）
    from handler import EndpointHandler  # type: ignore  # noqa: E402

    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    print(f"[omniparser-test] 加载 EndpointHandler model_dir={MODEL_DIR}", flush=True)
    t0 = time.perf_counter()
    handler = EndpointHandler(model_dir=str(MODEL_DIR))
    print(f"[omniparser-test] 初始化完成 ({time.perf_counter() - t0:.1f}s)", flush=True)

    payload = {
        "inputs": {
            "image": str(image_path),
            "image_size": {"w": w, "h": h},
            "bbox_threshold": bbox_threshold,
            "iou_threshold": iou_threshold,
        }
    }
    print(f"[omniparser-test] 推理中 image={image_path} size={w}x{h}", flush=True)
    t1 = time.perf_counter()
    result = handler(payload)
    print(f"[omniparser-test] 推理完成 ({time.perf_counter() - t1:.1f}s)", flush=True)

    bboxes = result.get("bboxes") or []
    elements = _elements_from_bboxes(bboxes, (w, h))

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "parsed_result.json"
    img_path = out_dir / "parsed_output.jpg"

    report: dict[str, Any] = {
        "ok": True,
        "model_dir": str(MODEL_DIR),
        "input_image": str(image_path),
        "image_size": {"w": w, "h": h},
        "element_count": len(elements),
        "bbox_threshold": bbox_threshold,
        "iou_threshold": iou_threshold,
        "elements": elements,
        "outputs": {
            "json": str(json_path),
            "annotated_image": str(img_path),
        },
    }

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    encoded = result.get("image") or ""
    if isinstance(encoded, str) and encoded.strip():
        raw = base64.b64decode(encoded)
        try:
            from PIL import Image

            Image.open(BytesIO(raw)).convert("RGB").save(img_path, format="JPEG", quality=92)
        except Exception:
            img_path = out_dir / "parsed_output.png"
            img_path.write_bytes(raw)
            report["outputs"]["annotated_image"] = str(img_path)
    else:
        report["ok"] = False
        report["error"] = "handler 未返回 image base64"

    if report.get("ok"):
        try:
            from l3_client.local_mcps.holographic_screen_mcp.omniparser_core import (
                publish_to_omnioutput,
            )

            published = publish_to_omnioutput(out_dir, tag="local_test")
            if published:
                report["omnioutput"] = published
                print(f"[omniparser-test] omnioutput: {published.get('annotated_image')}", flush=True)
        except Exception as e:
            print(f"[omniparser-test] omnioutput 复制跳过: {e}", flush=True)

    return report


def _print_summary(report: dict[str, Any]) -> None:
    print("\n=== OmniParser 解析摘要 ===", flush=True)
    print(f"元素数量: {report.get('element_count')}", flush=True)
    for el in report.get("elements") or []:
        eid = el.get("id")
        typ = el.get("type")
        content = (el.get("content") or "")[:60]
        px = el.get("bbox_xyxy_pixels")
        print(f"  [{eid}] type={typ} bbox_px={px} content={content!r}", flush=True)
    outs = report.get("outputs") or {}
    print(f"\nJSON: {outs.get('json')}", flush=True)
    print(f"标注图: {outs.get('annotated_image')}", flush=True)


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="OmniParser-v2.0 本地冒烟")
    ap.add_argument("--image", type=Path, default=None, help="测试截图路径（默认 scripts/test_screen.jpg）")
    ap.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent, help="输出目录")
    ap.add_argument("--bbox-threshold", type=float, default=0.05)
    ap.add_argument("--iou-threshold", type=float, default=0.7)
    ap.add_argument(
        "--preflight-only",
        action="store_true",
        help="仅检查 PyTorch 是否可 import，不加载 OmniParser",
    )
    args = ap.parse_args()

    _suggest_venv_if_broken_base()
    preflight_torch()
    if args.preflight_only:
        print("[omniparser-test] preflight 通过", flush=True)
        return 0

    try:
        image_path = _resolve_test_image(args.image)
        report = run_omniparser(
            image_path,
            bbox_threshold=args.bbox_threshold,
            iou_threshold=args.iou_threshold,
            out_dir=args.out_dir.resolve(),
        )
        _print_summary(report)
        return 0 if report.get("ok") else 1
    except Exception as e:
        print(f"[omniparser-test] 失败: {e}", file=sys.stderr, flush=True)
        import traceback

        traceback.print_exc()
        return 1


INSTALL_NOTES = """
=== Windows + CUDA 建议安装顺序 ===

1) 安装与显卡匹配的 PyTorch（示例 CUDA 12.1，以 https://pytorch.org 为准）::

   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

2) 模型目录 requirements + 推理常见依赖::

   pip install -r D:\\Projects\\jachi\\jachin-system-main\\model\\OmniParser-v2.0\\requirements.txt
   pip install transformers accelerate pillow numpy timm einops

3) 若 Florence-2 报缺依赖，可补::

   pip install sentencepiece protobuf

4) 冒烟脚本（仓库根）::

   python scripts/test_omniparser_local.py

说明:
- handler.EndpointHandler(model_dir) + handler({"inputs": {...}}) 为官方入口。
- 返回 bboxes 为归一化 xyxy；标注图上的数字 id 与 elements[].id 一致（从 0 起）。
- 首次 import handler 会构建 easyocr.Reader，并 from_pretrained("microsoft/Florence-2-base")，需联网。
- icon_caption/ 与 icon_detect/model.pt 必须已在 MODEL_DIR 下。
"""


if __name__ == "__main__":
    raise SystemExit(main())
