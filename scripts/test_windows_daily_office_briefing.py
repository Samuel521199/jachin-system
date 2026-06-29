#!/usr/bin/env python3
"""Smoke-test the Windows daily office briefing workflow.

Default mode is safe: it scans windows/system/recent files, writes a Markdown
report, evidence JSON, and an HTML evidence panel. Pass --send-lark to deliver
the generated summary to one or more Lark users/groups.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation  # noqa: E402


def _parse_list(raw: str, extra: list[str]) -> list[str]:
    text = str(raw or "[]").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    try:
        parsed = json.loads(text or "[]")
    except Exception:
        if text.startswith("[") and text.endswith("]"):
            parsed = [item.strip().strip("'\"") for item in text[1:-1].split(",") if item.strip()]
        elif text:
            parsed = [text.strip().strip("'\"")]
        else:
            parsed = []
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list):
        parsed = []
    out: list[str] = []
    seen: set[str] = set()
    for item in [*parsed, *extra]:
        value = str(item).strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipients-json", default="[]")
    parser.add_argument("--recipient", action="append", default=[])
    parser.add_argument("--paths-json", default="")
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--since-days", type=int, default=1)
    parser.add_argument("--max-files", type=int, default=80)
    parser.add_argument("--send-lark", action="store_true")
    parser.add_argument("--open-report", action="store_true")
    parser.add_argument("--no-reveal-key-file", action="store_true")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "output" / "daily_office_briefing_smoke"))
    args = parser.parse_args()

    recipients = _parse_list(args.recipients_json, args.recipient)
    paths = _parse_list(args.paths_json, args.path)
    paths_json = json.dumps(paths, ensure_ascii=False) if paths else ""
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    auto = WindowsOSAutomation(out_dir=out_dir)
    result = auto.daily_office_briefing(
        recipients=recipients,
        paths_json=paths_json,
        since_days=args.since_days,
        send_summary=args.send_lark,
        open_report=args.open_report,
        reveal_key_file=not args.no_reveal_key_file,
        max_files=args.max_files,
    )

    output = {
        "ok": result.ok,
        "task": result.task,
        "detail": result.detail,
        "recipients": recipients,
        "paths": paths,
        "report_path": result.evidence.get("report_path"),
        "evidence_path": result.evidence.get("evidence_path"),
        "evidence_panel_path": result.evidence.get("evidence_panel_path"),
        "recent_count": (result.evidence.get("recent") or {}).get("count"),
        "send_result": result.evidence.get("send_result"),
    }
    output_path = out_dir / "daily_office_briefing_smoke_result.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": result.ok, "result_path": str(output_path), "results": output}, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
