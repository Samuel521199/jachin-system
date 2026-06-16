#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Florence-2 能力验证（独立脚本，不经过 GameQA / YOLO）

验证三项能力：
  1. 视觉语义理解 — <DETAILED_CAPTION>
  2. 短语接地坐标 — <CAPTION_TO_PHRASE_GROUNDING> → [x1,y1,x2,y2]
  3. 推理耗时 — 每张图、每个短语打印 latency_ms

用法（仓库根，jachin-dev 环境）::

  python scripts/test_florence2_phrase_grounding.py --image D:\\screenshots\\game.png
  python scripts/test_florence2_phrase_grounding.py --image game.png --phrases "Spin button" "Continue with Guest"
  python scripts/test_florence2_phrase_grounding.py --image game.png --model microsoft/Florence-2-large --device cuda

产出：
  - 终端：caption、每个短语的 bbox / 中心点 (cx,cy)、耗时
  - 图片：data/florence2_test_out/<stem>_annotated.png（框 + 标签）

依赖（jachin-dev）::

  pip install "transformers==4.46.3" "huggingface-hub>=0.34.0,<1.0" timm einops accelerate pillow torch modelscope
  # transformers 5.x 与 Florence-2 自带代码不兼容；务必用 4.46.x
  # huggingface-hub>=1.0 与 transformers 4.46 冲突（报 required <1.0 but found 1.x）

国内下载模型（hf-mirror 连不上时）::

  Remove-Item Env:HF_ENDPOINT   # 或 $env:HF_ENDPOINT=''
  python scripts/test_florence2_phrase_grounding.py --download-modelscope --image <你的图>
  # 模型落到 data/models/Florence-2-base，之后可重复 --model data/models/Florence-2-base
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_MODEL = ROOT / "data" / "models" / "Florence-2-base"
_FORCED_BOS_OLD = (
    "if self.forced_bos_token_id is None and kwargs.get(\"force_bos_token_to_be_generated\", False):"
)
_FORCED_BOS_NEW = (
    "if getattr(self, \"forced_bos_token_id\", None) is None and kwargs.get("
    "\"force_bos_token_to_be_generated\", False):"
)


def _patch_florence2_transformers5_compat(model_dir: Path) -> None:
    """Patch bundled configuration_florence2.py for transformers 5.x (forced_bos_token_id)."""
    targets: list[Path] = []
    cfg = model_dir / "configuration_florence2.py"
    if cfg.is_file():
        targets.append(cfg)
    hf_home = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface"))
    modules_root = hf_home / "modules" / "transformers_modules"
    if modules_root.is_dir():
        targets.extend(modules_root.rglob("configuration_florence2.py"))

    seen: set[str] = set()
    for path in targets:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        if _FORCED_BOS_OLD not in text:
            continue
        path.write_text(text.replace(_FORCED_BOS_OLD, _FORCED_BOS_NEW), encoding="utf-8")
        print(f"[patch] transformers 5.x compat -> {path}", flush=True)


def _strip_bom_from_model_py(model_dir: Path) -> None:
    for path in model_dir.glob("*.py"):
        raw = path.read_bytes()
        if len(raw) >= 3 and raw[:3] == b"\xef\xbb\xbf":
            path.write_bytes(raw[3:])
            print(f"[patch] stripped UTF-8 BOM -> {path.name}", flush=True)


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Florence-2 短语接地 + 视觉描述 + 耗时")
    ap.add_argument(
        "--image",
        required=True,
        help="本地截图路径（你自己截的游戏/大厅图）",
    )
    ap.add_argument(
        "--model",
        default=str(DEFAULT_LOCAL_MODEL)
        if DEFAULT_LOCAL_MODEL.is_dir()
        else "microsoft/Florence-2-base",
        help="本地模型目录或 HF id；国内推荐先 ModelScope 下到 data/models/Florence-2-base",
    )
    ap.add_argument(
        "--download-modelscope",
        action="store_true",
        help="先从魔搭 AI-ModelScope/Florence-2-base 下载到 data/models/Florence-2-base 再测",
    )
    ap.add_argument(
        "--device",
        default="auto",
        choices=("auto", "cpu", "cuda"),
        help="推理设备；auto = 有 CUDA 则用 GPU",
    )
    ap.add_argument(
        "--phrases",
        nargs="*",
        default=[
            "Continue with Guest",
            "the Spin button",
            "a green confirm button",
            "the small red close icon on the top right",
            "Play Now button",
        ],
        help="要定位的自然语言短语（可多个）",
    )
    ap.add_argument(
        "--out-dir",
        default=str(ROOT / "data" / "florence2_test_out"),
        help="标注图输出目录",
    )
    ap.add_argument(
        "--skip-caption",
        action="store_true",
        help="跳过 <DETAILED_CAPTION>（只测坐标与耗时）",
    )
    return ap.parse_args()


def _resolve_device(choice: str):
    import torch

    if choice == "cpu":
        return torch.device("cpu")
    if choice == "cuda":
        if not torch.cuda.is_available():
            print("[WARN] CUDA 不可用，回退 CPU", file=sys.stderr)
            return torch.device("cpu")
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _download_modelscope(local_dir: Path) -> Path:
    try:
        from modelscope import snapshot_download
    except ImportError as e:
        raise RuntimeError("请先安装: pip install modelscope") from e
    local_dir.parent.mkdir(parents=True, exist_ok=True)
    print("[download] ModelScope AI-ModelScope/Florence-2-base ...", flush=True)
    snapshot_download("AI-ModelScope/Florence-2-base", local_dir=str(local_dir))
    _patch_florence2_transformers5_compat(local_dir)
    return local_dir


def _load_model(model_id: str, device):
    import torch
    from transformers import AutoModelForCausalLM, AutoProcessor

    model_path = Path(model_id)
    if model_path.is_dir():
        model_path = model_path.resolve()
        _strip_bom_from_model_py(model_path)
        _patch_florence2_transformers5_compat(model_path)
        model_ref = str(model_path)
    else:
        model_ref = model_id
        if (os.environ.get("HF_ENDPOINT") or "").strip():
            print(
                f"[hint] 当前 HF_ENDPOINT={os.environ.get('HF_ENDPOINT')!r}；"
                "若连不上镜像，请: Remove-Item Env:HF_ENDPOINT 或改用 --download-modelscope",
                flush=True,
            )

    print(f"[load] model={model_ref!r} device={device}", flush=True)
    t0 = time.perf_counter()
    processor = AutoProcessor.from_pretrained(model_ref, trust_remote_code=True)
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        model_ref,
        trust_remote_code=True,
        torch_dtype=dtype,
    ).to(device)
    model.eval()
    print(f"[load] done in {(time.perf_counter() - t0) * 1000:.0f} ms", flush=True)
    return processor, model


def _run_task(processor, model, device, image, task_prompt: str, text: str = "") -> tuple[object, float]:
    import torch

    prompt = task_prompt + text
    inputs = processor(text=prompt, images=image, return_tensors="pt")
    inputs = {k: v.to(device) if hasattr(v, "to") else v for k, v in inputs.items()}

    t0 = time.perf_counter()
    with torch.no_grad():
        ids = model.generate(
            input_ids=inputs["input_ids"],
            pixel_values=inputs["pixel_values"],
            max_new_tokens=256,
            num_beams=3,
            do_sample=False,
        )
    latency_ms = (time.perf_counter() - t0) * 1000.0

    raw = processor.batch_decode(ids, skip_special_tokens=False)[0]
    parsed = processor.post_process_generation(
        raw,
        task=task_prompt,
        image_size=(image.width, image.height),
    )
    return parsed, latency_ms


def _bbox_to_center(bbox: list[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def _extract_grounding_boxes(parsed) -> list[dict]:
    """从 post_process 结果里抽出 bboxes + labels。"""
    out: list[dict] = []
    if not isinstance(parsed, dict):
        return out
    # Florence-2 grounding 常见结构: {"<CAPTION_TO_PHRASE_GROUNDING>": {"bboxes": [...], "labels": [...]}}
    for key, val in parsed.items():
        if not isinstance(val, dict):
            continue
        bboxes = val.get("bboxes") or []
        labels = val.get("labels") or []
        for i, bbox in enumerate(bboxes):
            if not bbox or len(bbox) < 4:
                continue
            lab = labels[i] if i < len(labels) else ""
            try:
                bb = [float(x) for x in bbox[:4]]
            except (TypeError, ValueError):
                continue
            cx, cy = _bbox_to_center(bb)
            out.append({"label": str(lab), "bbox": bb, "cx": cx, "cy": cy})
    return out


def _draw_annotations(image, hits: list[dict], out_path: Path) -> None:
    from PIL import ImageDraw, ImageFont

    img = image.copy()
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    colors = ["#00FF00", "#FF4444", "#44AAFF", "#FFAA00", "#FF00FF", "#00FFFF"]
    for i, h in enumerate(hits):
        color = colors[i % len(colors)]
        x1, y1, x2, y2 = h["bbox"]
        draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
        tag = f"{h.get('phrase', h.get('label', ''))} ({h['cx']:.0f},{h['cy']:.0f})"
        draw.text((x1, max(0, y1 - 16)), tag[:48], fill=color, font=font)
        draw.ellipse([h["cx"] - 4, h["cy"] - 4, h["cx"] + 4, h["cy"] + 4], fill=color)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"[out] annotated image -> {out_path}", flush=True)


def main() -> int:
    args = _parse_args()
    if args.download_modelscope:
        args.model = str(_download_modelscope(DEFAULT_LOCAL_MODEL))

    img_path = Path(args.image).expanduser().resolve()
    if not img_path.is_file():
        print(f"[ERROR] 图片不存在: {img_path}", file=sys.stderr)
        return 2

    try:
        from PIL import Image
    except ImportError:
        print("[ERROR] 请安装 pillow: pip install pillow", file=sys.stderr)
        return 2

    image = Image.open(img_path).convert("RGB")
    print(f"[image] {img_path} size={image.width}x{image.height}", flush=True)

    device = _resolve_device(args.device)
    try:
        processor, model = _load_model(args.model, device)
    except Exception as e:
        print(f"[ERROR] 模型加载失败: {e}", file=sys.stderr)
        print(
            "  常见修复:\n"
            "  1) hf-mirror 连不上: Remove-Item Env:HF_ENDPOINT\n"
            "  2) 国内下载: python scripts/test_florence2_phrase_grounding.py --download-modelscope --image <图>\n"
            "  3) 本地模型: --model data/models/Florence-2-base\n"
            "  4) 版本组合: pip install \"transformers==4.46.3\" \"huggingface-hub>=0.34.0,<1.0\"",
            file=sys.stderr,
        )
        print(
            "  5) huggingface-hub 1.x 与 transformers 4.46 冲突: pip install \"huggingface-hub<1.0\"",
            file=sys.stderr,
        )
        return 2

    report: dict = {
        "image": str(img_path),
        "model": args.model,
        "device": str(device),
        "size": [image.width, image.height],
        "caption": None,
        "phrase_grounding": [],
    }

    if not args.skip_caption:
        print("\n=== 1) 视觉语义理解 <DETAILED_CAPTION> ===", flush=True)
        try:
            cap, cap_ms = _run_task(processor, model, device, image, "<DETAILED_CAPTION>")
            report["caption"] = {"result": cap, "latency_ms": round(cap_ms, 1)}
            print(f"latency_ms: {cap_ms:.1f}")
            print(json.dumps(cap, ensure_ascii=False, indent=2) if isinstance(cap, dict) else cap)
        except Exception as e:
            print(f"[ERROR] DETAILED_CAPTION 失败: {e}", file=sys.stderr)
            report["caption"] = {"error": repr(e)}

    print("\n=== 2) 短语接地 <CAPTION_TO_PHRASE_GROUNDING> ===", flush=True)
    print("（输入自然语言 → 输出 bbox + 中心点，可直接给 Playwright 点击）\n", flush=True)

    all_hits: list[dict] = []
    for phrase in args.phrases:
        phrase = phrase.strip()
        if not phrase:
            continue
        try:
            parsed, ms = _run_task(
                processor,
                model,
                device,
                image,
                "<CAPTION_TO_PHRASE_GROUNDING>",
                phrase,
            )
            boxes = _extract_grounding_boxes(parsed)
            entry = {
                "phrase": phrase,
                "latency_ms": round(ms, 1),
                "raw": parsed,
                "hits": boxes,
            }
            report["phrase_grounding"].append(entry)

            print(f"phrase: {phrase!r}")
            print(f"  latency_ms: {ms:.1f}")
            if boxes:
                for b in boxes:
                    print(
                        f"  bbox={b['bbox']}  center=({b['cx']:.1f}, {b['cy']:.1f})  label={b['label']!r}"
                    )
                    all_hits.append({**b, "phrase": phrase})
            else:
                print("  (no bbox — 画面上可能无匹配，或换更具体的描述再试)")
            print()
        except Exception as e:
            print(f"phrase: {phrase!r}  ERROR: {e}\n", file=sys.stderr)
            report["phrase_grounding"].append({"phrase": phrase, "error": repr(e)})

    out_dir = Path(args.out_dir)
    ann_path = out_dir / f"{img_path.stem}_annotated.png"
    if all_hits:
        _draw_annotations(image, all_hits, ann_path)
    else:
        print("[WARN] 无任何 bbox，未生成标注图", flush=True)

    json_path = out_dir / f"{img_path.stem}_report.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[out] json report -> {json_path}", flush=True)

    print("\n=== 3) 耗时小结（System 2 相对速度）===", flush=True)
    latencies = [
        x["latency_ms"]
        for x in report["phrase_grounding"]
        if isinstance(x.get("latency_ms"), (int, float))
    ]
    if latencies:
        print(f"  phrase_grounding: min={min(latencies):.0f}ms max={max(latencies):.0f}ms avg={sum(latencies)/len(latencies):.0f}ms")
        if device.type == "cuda":
            print("  参考：GPU 上通常几十～数百 ms/次；若 >2s 可检查是否误用 CPU")
        else:
            print("  参考：纯 CPU 通常 1～3s/次；有显卡请加 --device cuda")
    cap = report.get("caption") or {}
    if isinstance(cap.get("latency_ms"), (int, float)):
        print(f"  detailed_caption: {cap['latency_ms']:.0f}ms")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
