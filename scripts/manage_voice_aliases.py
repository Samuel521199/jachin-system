#!/usr/bin/env python3
"""Manage voice alias lexicon entries for Phase 12/13."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from l3_node.voice_entity_correction import bulk_import_aliases, deactivate_alias, list_user_aliases, teach_alias


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage voice aliases")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add")
    add.add_argument("kind", choices=["app", "contact", "project"])
    add.add_argument("canonical")
    add.add_argument("alias")

    deact = sub.add_parser("deactivate")
    deact.add_argument("kind", choices=["app", "contact", "project"])
    deact.add_argument("canonical")
    deact.add_argument("alias")

    imp = sub.add_parser("import-json")
    imp.add_argument("path", type=Path)

    sub.add_parser("list")
    args = parser.parse_args()

    if args.cmd == "add":
        path = teach_alias(args.kind, args.canonical, args.alias)
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False))
        return 0
    if args.cmd == "deactivate":
        path = deactivate_alias(args.kind, args.canonical, args.alias)
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False))
        return 0
    if args.cmd == "import-json":
        items = json.loads(args.path.read_text(encoding="utf-8"))
        path = bulk_import_aliases(items if isinstance(items, list) else [], source=str(args.path))
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False))
        return 0
    print(json.dumps(list_user_aliases(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
