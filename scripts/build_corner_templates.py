#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从整牌小图批量裁切左上角角点模板 → card_corner_templates/

用法：
  python scripts/build_corner_templates.py
  python scripts/build_corner_templates.py --src scripts/card_templates --frac-w 0.38 --frac-h 0.42
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import cv2

_SCRIPTS = Path(__file__).resolve().parent
_STEM_RE = re.compile(r"^([SHCD])(A|[2-9]|10|J|Q|K)$", re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=_SCRIPTS / "card_templates")
    ap.add_argument("--dst", type=Path, default=_SCRIPTS / "card_corner_templates")
    ap.add_argument("--frac-w", type=float, default=0.38, help="角点宽占整牌比例")
    ap.add_argument("--frac-h", type=float, default=0.42, help="角点高占整牌比例")
    args = ap.parse_args()

    args.dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(args.src.glob("*")):
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp"):
            continue
        if not _STEM_RE.match(p.stem):
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        cw = max(8, int(w * args.frac_w))
        ch = max(8, int(h * args.frac_h))
        corner = img[0:ch, 0:cw]
        out = args.dst / f"{p.stem.upper()}.png"
        cv2.imwrite(str(out), corner)
        n += 1
    print(f"已写入 {n} 个角点模板 → {args.dst}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
