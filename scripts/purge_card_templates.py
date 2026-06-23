#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 card_templates 中误采集的非牌面小图（按文件名 + 图像启发式）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from card_crop_filter import is_valid_label_stem, looks_like_playing_card_crop


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir",
        default=str(SCRIPTS / "card_templates"),
        help="模板目录",
    )
    ap.add_argument("--dry-run", action="store_true", help="只打印不删除")
    args = ap.parse_args()

    import cv2

    root = Path(args.dir)
    if not root.is_dir():
        print(f"目录不存在: {root}")
        return 1

    removed = 0
    kept = 0
    for p in sorted(root.glob("*.png")):
        stem = p.stem.upper()
        reasons: list[str] = []
        if not is_valid_label_stem(stem):
            reasons.append("非法文件名")
        img = cv2.imread(str(p))
        if img is None:
            reasons.append("无法读取")
        else:
            ok, why = looks_like_playing_card_crop(img)
            if not ok:
                reasons.append(why)
        if p.stat().st_size < 900 and "尺寸" not in "".join(reasons):
            reasons.append(f"文件过小 {p.stat().st_size}B")

        if reasons:
            print(f"删除 {p.name}: {', '.join(reasons)}")
            if not args.dry_run:
                p.unlink()
            removed += 1
        else:
            kept += 1

    print(f"完成: 保留 {kept} 删除 {removed} (dry_run={args.dry_run})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
