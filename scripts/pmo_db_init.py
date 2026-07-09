#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PMO-Copilot v6：初始化 ``~/.jachin/workspace/pmo_db.sqlite`` Schema。

用法（仓库根）::

  python scripts/pmo_db_init.py
  python scripts/pmo_db_init.py --force   # 删除现有库并重建空表

Schema notes live with the PMO capability package; host startup must not
depend on deleted PMO architecture documents.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
ROOT = Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from l3_node.tools.pmo_db_tools import get_pmo_db_path, init_pmo_database  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="初始化 PMO SQLite 数据库（pmo_db.sqlite）")
    ap.add_argument(
        "--force",
        action="store_true",
        help="删除现有 pmo_db.sqlite 后重建空 Schema（会丢失全部 PMO 数据）",
    )
    args = ap.parse_args()

    if args.force:
        print(f"[pmo_db_init] --force：将删除 {get_pmo_db_path()}", file=sys.stderr)

    result = init_pmo_database(force=args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
