from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _ensure_sample_project(root: Path) -> Path:
    project = root / "sample_project"
    project.mkdir(parents=True, exist_ok=True)
    if not (project / ".git").is_dir():
        _git(project, "init")
        _git(project, "config", "user.email", "work-ledger-smoke@example.local")
        _git(project, "config", "user.name", "Work Ledger Smoke")
    readme = project / "README.md"
    if not readme.exists():
        readme.write_text("# Work Ledger smoke project\n", encoding="utf-8")
    feature = project / "feature_work_ledger_smoke.py"
    feature.write_text(
        "# TODO: verify Work Ledger daily live smoke\n"
        "def run():\n"
        "    return 'work-ledger-live-smoke'\n",
        encoding="utf-8",
    )
    return project


def _sample_trace() -> str:
    return "\n".join(
        [
            "Codex task: implement Work Ledger daily usage loop.",
            "Goal: make start, note, trace import, report generation and Lark brief usable every day.",
            "Changed: added baseline lark_brief output and a safe output read API.",
            "Failed: first UI copy path had no stable output when LLM was disabled.",
            "Decision: keep deterministic baseline output, then let Qwen enhance only when quality gate passes.",
            "Next: run live smoke and make Codex/Cursor trace import easier from clipboard.",
        ]
    )


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    out_root = Path(args.output_dir or ROOT / "output" / "work_ledger_live_smoke").resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    ledger_home = Path(args.ledger_home or out_root / "ledger").resolve()
    os.environ["JACHIN_WORK_LEDGER_HOME"] = str(ledger_home)
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(out_root / "kernel")
    if args.no_llm:
        os.environ["JACHIN_WORK_LEDGER_LLM_ENABLED"] = "0"

    project = Path(args.project_path).resolve() if args.project_path else _ensure_sample_project(out_root)

    from l3_node.work_ledger import (
        add_manual_note,
        adopt_work_output,
        build_end_day_preview,
        end_session,
        finalize_end_day_package,
        generate_multi_day_weekly_report,
        load_evidence,
        read_output_text,
        recall_work_ledger,
        start_session,
        write_work_ledger_recall_index,
    )

    started = time.perf_counter()
    detail = start_session(
        title=args.title or time.strftime("%Y-%m-%d Work Ledger live smoke"),
        project_path=str(project),
        user_goal=args.goal or "Validate Work Ledger daily loop end to end.",
        created_from="script:work_ledger_daily_live_smoke",
        auto_collect=True,
    )
    sid = str(detail["session"]["session_id"])
    add_manual_note(sid, args.note or "用户确认：本轮 smoke 验证日常工作记录闭环。")
    trace_text = Path(args.trace_file).read_text(encoding="utf-8") if args.trace_file else _sample_trace()
    end_day_preview = build_end_day_preview(sid, process_text=trace_text)
    finalized = finalize_end_day_package(sid, process_text=trace_text, close_session=False)
    imported = finalized.get("imported") or {}
    trace_ev = imported.get("evidence") or {}
    outputs = finalized.get("outputs") or {}
    lark = read_output_text(sid, "lark_brief", max_chars=1600)
    adoption_ev = adopt_work_output(sid, "team_lark_brief", note="live smoke adopted team Lark brief")
    recall_index = write_work_ledger_recall_index(30)
    recall = recall_work_ledger("Work Ledger daily loop", days=30, limit=5)
    weekly = generate_multi_day_weekly_report(30)
    closed = end_session(sid, generate_outputs=True)
    evidence = load_evidence(sid, limit=1000)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    result = {
        "ok": bool(
            outputs.get("daily_report")
            and outputs.get("context_pack")
            and outputs.get("codex_continuation_prompt")
            and outputs.get("lark_brief")
        ),
        "session_id": sid,
        "project_path": str(project),
        "ledger_home": str(ledger_home),
        "outputs": outputs,
        "lark_brief_preview": str(lark.get("text") or "").strip()[:800],
        "adopted_evidence_id": adoption_ev.get("evidence_id"),
        "recall_index_path": recall_index.get("path"),
        "recall_hit_count": recall.get("hit_count"),
        "recall_hits": recall.get("hits", [])[:3],
        "weekly_report_path": weekly.get("path"),
        "enhanced_weekly_report_path": weekly.get("enhanced_path"),
        "weekly_quality_report_path": weekly.get("quality_report_path"),
        "weekly_session_count": weekly.get("session_count"),
        "trace_analysis": (trace_ev.get("payload") or {}).get("analysis"),
        "end_day_preview": end_day_preview.get("preview"),
        "process_import": imported.get("import"),
        "evidence_sources": sorted({str(ev.get("source") or "") for ev in evidence}),
        "evidence_count": len(evidence),
        "closed_status": (closed.get("session") or {}).get("status"),
        "elapsed_ms": elapsed_ms,
    }
    result_path = out_root / f"work_ledger_live_smoke_{int(time.time())}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    result["result_path"] = str(result_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Work Ledger daily-loop live smoke.")
    parser.add_argument("--project-path", default="", help="Project path to scan. Defaults to an isolated sample project.")
    parser.add_argument("--output-dir", default="", help="Directory for smoke artifacts.")
    parser.add_argument("--ledger-home", default="", help="Override Work Ledger home. Defaults inside output-dir.")
    parser.add_argument("--title", default="", help="Work session title.")
    parser.add_argument("--goal", default="", help="Work session goal.")
    parser.add_argument("--note", default="", help="Manual note to add.")
    parser.add_argument("--trace-file", default="", help="Codex/Cursor trace text file to import.")
    parser.add_argument("--tool-name", default="Codex", help="Trace source tool name.")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM refinement for deterministic smoke.")
    args = parser.parse_args()
    result = run_smoke(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
