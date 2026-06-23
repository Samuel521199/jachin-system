#!/usr/bin/env python3
"""OmniParser 子进程 worker（供 L3 主进程通过 .venv-omniparser 调用）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--work-dir", type=Path, required=True)
    ap.add_argument("--bbox-threshold", type=float, default=0.05)
    ap.add_argument("--iou-threshold", type=float, default=0.7)
    args = ap.parse_args()

    from .omniparser_core import run_omniparser_inprocess

    try:
        report = run_omniparser_inprocess(
            args.image,
            work_dir=args.work_dir,
            bbox_threshold=args.bbox_threshold,
            iou_threshold=args.iou_threshold,
        )
        summary = {
            "ok": bool(report.get("ok")),
            "element_count": report.get("element_count", 0),
            "elements_llm": report.get("elements_llm") or [],
            "work_dir": report.get("work_dir"),
            "outputs": report.get("outputs"),
            "image_size": report.get("image_size"),
            "error": report.get("error"),
        }
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if summary["ok"] else 1
    except Exception as e:
        print(
            json.dumps({"ok": False, "error": repr(e)}, ensure_ascii=False),
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
