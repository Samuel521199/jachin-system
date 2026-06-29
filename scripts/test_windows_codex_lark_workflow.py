#!/usr/bin/env python3
"""Smoke-test the Windows Codex -> Lark multi-app workflow.

Default mode is safe and does not touch the desktop UI:
- remembers project name/path
- generates the non-Codex project briefing fallback
- builds the Codex prompt
- validates a representative Codex response

Use --live-ui to actually open/focus Codex, paste the prompt, wait, copy output,
and optionally send to Lark with --send-lark.

For multi-target validation, pass --recipients-json '["Vivian","测试备注冒烟草稿"]'
or repeat --recipient. Mixed single chats and group chats are supported by the
underlying Windows UIA MCP workflow.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from l3_client.local_mcps.windows_uia_mcp.os_tasks import (  # noqa: E402
    WindowsOSAutomation,
    _build_codex_project_prompt,
    _codex_response_valid,
)


def _json_safe_result(result) -> dict:
    data = {
        "task": result.task,
        "ok": result.ok,
        "detail": result.detail,
        "evidence": result.evidence,
    }
    return data


def _delivery_summary(send_result: dict | None) -> list[dict]:
    if not isinstance(send_result, dict):
        return []
    evidence = send_result.get("evidence") if isinstance(send_result.get("evidence"), dict) else {}
    rows = []
    for row in evidence.get("deliveries") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "recipient": row.get("recipient"),
                "ok": row.get("ok"),
                "recipient_visible": row.get("recipient_visible"),
                "message_visible": row.get("message_visible"),
                "preview_verified": row.get("preview_verified"),
                "failure_stage": row.get("failure_stage"),
            }
        )
    return rows


def _loads_jsonish_list(raw: str) -> list:
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
    return parsed if isinstance(parsed, list) else []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-name", default="Jachin")
    parser.add_argument("--project-path", default=str(REPO_ROOT))
    parser.add_argument("--feature-query", default="OS assistant Codex Lark workflow")
    parser.add_argument("--user-input", default="")
    parser.add_argument("--recipients-json", default='["Vivian"]')
    parser.add_argument("--recipient", action="append", default=[])
    parser.add_argument("--since-days", type=int, default=3)
    parser.add_argument("--wait-seconds", type=int, default=90)
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "output" / "codex_lark_workflow_smoke"))
    parser.add_argument("--live-ui", action="store_true")
    parser.add_argument("--send-lark", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    auto = WindowsOSAutomation(out_dir=out_dir)

    recipients = _loads_jsonish_list(args.recipients_json)
    deduped_recipients: list[str] = []
    seen_recipients: set[str] = set()
    for raw in [*recipients, *args.recipient]:
        name = str(raw).strip()
        key = name.lower()
        if not name or key in seen_recipients:
            continue
        seen_recipients.add(key)
        deduped_recipients.append(name)
    recipients = deduped_recipients

    prompt = _build_codex_project_prompt(
        args.project_name,
        str(Path(args.project_path).expanduser().resolve()),
        feature_query=args.feature_query,
        since_days=args.since_days,
        original_user_input=args.user_input,
    )
    representative_codex_text = (
        f"{args.project_name} 项目最新进展：围绕 {args.feature_query} 完成了 "
        "windows_codex_project_briefing_to_lark 多 App 工作流。关键文件包括 "
        "l3_client/local_mcps/windows_uia_mcp/os_tasks.py、server.py 和 registry.py。"
        "总结：Codex 作为代码分析 App，Lark 作为交付 App，Jachin 负责 OS 级调度。"
        "风险：真实 UI 复制和发送仍需要桌面截图证据。下一步建议：运行 live-ui 烟测。"
    )

    results: dict[str, object] = {
        "mode": "live-ui" if args.live_ui else "safe",
        "project_name": args.project_name,
        "project_path": str(Path(args.project_path).expanduser().resolve()),
        "feature_query": args.feature_query,
        "recipients": recipients,
        "out_dir": str(out_dir),
        "prompt_len": len(prompt),
        "prompt_preview": prompt[:800],
        "validation": _codex_response_valid(representative_codex_text, args.project_name, args.feature_query),
    }

    remembered = auto.project_remember(args.project_name, args.project_path)
    results["remember"] = _json_safe_result(remembered)

    fallback_briefing = auto.project_latest_briefing(
        project_name=args.project_name,
        project_path="",
        feature_query=args.feature_query,
        recipients=recipients,
        since_days=args.since_days,
        send_summary=False,
        open_report=False,
        use_qwen=False,
        remember=True,
        max_files=30,
    )
    results["project_latest_briefing_no_qwen"] = {
        "task": fallback_briefing.task,
        "ok": fallback_briefing.ok,
        "detail": fallback_briefing.detail,
        "report_path": fallback_briefing.evidence.get("report_path"),
        "evidence_path": fallback_briefing.evidence.get("evidence_path"),
        "evidence_panel_path": fallback_briefing.evidence.get("evidence_panel_path"),
        "recent_count": fallback_briefing.evidence.get("recent_count"),
        "feature_match_count": fallback_briefing.evidence.get("feature_match_count"),
        "qwen": fallback_briefing.evidence.get("qwen"),
    }

    if args.live_ui:
        live = auto.codex_project_briefing_to_lark(
            project_name=args.project_name,
            project_path="",
            feature_query=args.feature_query,
            original_user_input=args.user_input,
            recipients=recipients,
            since_days=args.since_days,
            wait_seconds=args.wait_seconds,
            send_summary=args.send_lark,
            remember=True,
        )
        results["codex_project_briefing_to_lark_live"] = {
            "task": live.task,
            "ok": live.ok,
            "detail": live.detail,
            "report_path": live.evidence.get("report_path"),
            "evidence_path": live.evidence.get("evidence_path"),
            "evidence_panel_path": live.evidence.get("evidence_panel_path"),
            "validation": live.evidence.get("validation"),
            "send_result": live.evidence.get("send_result"),
            "delivery_summary": _delivery_summary(live.evidence.get("send_result")),
            "timeline_steps": len(live.evidence.get("timeline") or []),
            "screenshots": live.evidence.get("screenshots"),
        }
    else:
        results["codex_project_briefing_to_lark_live"] = {
            "skipped": True,
            "reason": "Pass --live-ui to open Codex and interact with the desktop. Pass --send-lark to send after validation.",
        }

    output_path = out_dir / "codex_lark_workflow_smoke_result.json"
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "result_path": str(output_path), "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
