#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run Jachin Windows OS automation smoke scenarios.

Scenarios:
  1. Windows open/save file dialogs
  2. Calculator keys and result reading
  3. Notepad edit/save flow
  4. Browser address/download/prompt flow
  5. Generic popup confirm/cancel/close
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from l3_client.local_mcps.windows_uia_mcp.os_tasks import run_tasks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jachin Windows OS automation smoke runner")
    parser.add_argument(
        "--task",
        choices=("all", "file_dialogs", "calculator", "notepad", "browser", "popup"),
        default="all",
        help="Scenario to run.",
    )
    parser.add_argument("--out-dir", default=str(ROOT / "output" / "os_vision"))
    parser.add_argument("--notepad-file", default="")
    parser.add_argument("--text", default="")
    parser.add_argument("--expr", default="99*8+15")
    parser.add_argument("--expect", default="")
    parser.add_argument("--browser-url", default="")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    report = run_tasks(
        task=args.task,
        out_dir=args.out_dir,
        notepad_text=args.text,
        notepad_file=args.notepad_file or None,
        expression=args.expr,
        expected=args.expect,
        browser_url=args.browser_url,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
