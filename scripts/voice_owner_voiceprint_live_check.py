from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from l3_node.voice_false_trigger_learning import (  # noqa: E402
    latest_voice_learning_summary,
    record_voice_owner_validation_result,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect live owner voiceprint evidence for Jachin voice mode.")
    parser.add_argument("--base-url", default=os.environ.get("JACHIN_JVS_BASE_URL", "http://127.0.0.1:18990"))
    parser.add_argument("--lookback-lines", type=int, default=800)
    parser.add_argument("--expect-owner-pass", action="store_true")
    parser.add_argument("--expect-non-owner-block", action="store_true")
    parser.add_argument("--report", default=str(ROOT / "docs" / "17_voice_owner_voiceprint_live_check.md"))
    args = parser.parse_args()

    profile = _profile_status()
    jvs = _http_health(args.base_url)
    logs = _scan_voice_logs(args.lookback_lines)
    status = _overall_status(profile=profile, jvs=jvs, logs=logs, args=args)

    record_voice_owner_validation_result(
        result_type=status["result_type"],
        accepted=status["accepted"],
        reason=status["reason"],
        evidence={"profile": profile, "jvs": jvs, "logs": logs, "expectations": _expectations(args)},
    )

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(profile=profile, jvs=jvs, logs=logs, status=status), encoding="utf-8")
    print(json.dumps({"ok": status["ok"], "status": status, "report": str(report_path)}, ensure_ascii=False, indent=2))
    return 0 if status["ok"] else 1


def _profile_status() -> dict[str, object]:
    path = Path.home() / ".jachin" / "voice" / "owner_voiceprint.json"
    out: dict[str, object] = {"path": str(path), "exists": path.exists(), "centroid_len": 0, "valid": False}
    if not path.exists():
        out["reason"] = "owner_voiceprint_missing"
        return out
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        centroid = payload.get("centroid")
        if isinstance(centroid, list):
            out["centroid_len"] = len(centroid)
        out["valid"] = int(out["centroid_len"]) > 0
        out["reason"] = "ok" if out["valid"] else "centroid_empty"
    except Exception as exc:
        out["reason"] = f"profile_parse_failed:{exc}"
    return out


def _http_health(base_url: str) -> dict[str, object]:
    url = base_url.rstrip("/") + "/health"
    started = time.perf_counter()
    try:
        req = Request(url, method="GET")
        with urlopen(req, timeout=2.5) as resp:
            body = resp.read(256).decode("utf-8", errors="ignore")
        return {
            "base_url": base_url,
            "ok": True,
            "status": getattr(resp, "status", None),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "body_preview": body[:120],
        }
    except Exception as exc:
        return {
            "base_url": base_url,
            "ok": False,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": _stable_error(exc),
        }


def _scan_voice_logs(lookback_lines: int) -> dict[str, object]:
    debug_dir = Path.home() / ".jachin" / "jachin_debug"
    files = [debug_dir / "voice_companion.log", debug_dir / "voice_chat.log"]
    counters = {
        "owner_accept": 0,
        "owner_reject": 0,
        "owner_drop_utterance": 0,
        "wake_accept": 0,
        "wake_reject": 0,
        "ptt_owner_track": 0,
        "ptt_fast_bypass": 0,
        "profile_missing": 0,
        "jvs_fail": 0,
    }
    recent: list[str] = []
    for path in files:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-lookback_lines:]
        except Exception:
            continue
        for line in lines:
            lowered = line.lower()
            if "sv.owner_accept" in lowered or "sv_owner_track_ok" in lowered:
                counters["owner_accept"] += 1
                recent.append(_short_line(path, line))
            if "sv.owner_reject" in lowered or "sv_reject" in lowered:
                counters["owner_reject"] += 1
                recent.append(_short_line(path, line))
            if "sv.owner_drop_utterance" in lowered:
                counters["owner_drop_utterance"] += 1
                recent.append(_short_line(path, line))
            if "sv.wake_accept" in lowered:
                counters["wake_accept"] += 1
                recent.append(_short_line(path, line))
            if "sv.wake_reject" in lowered:
                counters["wake_reject"] += 1
                recent.append(_short_line(path, line))
            if "sv.owner_track_ptt_fast_bypass" in lowered:
                counters["ptt_fast_bypass"] += 1
                recent.append(_short_line(path, line))
            elif "sv.owner_track_ptt" in lowered:
                counters["ptt_owner_track"] += 1
                recent.append(_short_line(path, line))
            if "profile_missing" in lowered or "owner_profile_missing" in lowered:
                counters["profile_missing"] += 1
            if "jvs_fail" in lowered or "owner_filter_fail" in lowered or "wake_jvs_fail" in lowered:
                counters["jvs_fail"] += 1
    return {
        "debug_dir": str(debug_dir),
        "files": [str(p) for p in files],
        "lookback_lines": lookback_lines,
        "counters": counters,
        "recent_evidence": recent[-20:],
    }


def _overall_status(*, profile: dict[str, object], jvs: dict[str, object], logs: dict[str, object], args: argparse.Namespace) -> dict[str, object]:
    counters = logs.get("counters") if isinstance(logs.get("counters"), dict) else {}
    owner_pass = int(counters.get("owner_accept") or 0) + int(counters.get("wake_accept") or 0) + int(counters.get("ptt_owner_track") or 0)
    non_owner_block = int(counters.get("owner_reject") or 0) + int(counters.get("wake_reject") or 0) + int(counters.get("owner_drop_utterance") or 0)
    missing: list[str] = []
    if not profile.get("valid"):
        missing.append("owner_voiceprint_profile")
    if not jvs.get("ok"):
        missing.append("jvs_health")
    if args.expect_owner_pass and owner_pass <= 0:
        missing.append("owner_pass_evidence")
    if args.expect_non_owner_block and non_owner_block <= 0:
        missing.append("non_owner_block_evidence")
    ok = not missing
    if not ok:
        result_type = "not_ready" if "owner_voiceprint_profile" in missing or "jvs_health" in missing else "insufficient_evidence"
    elif non_owner_block > 0 and owner_pass > 0:
        result_type = "pass_owner_and_block_non_owner"
    elif owner_pass > 0:
        result_type = "pass_owner_only"
    else:
        result_type = "ready_no_live_sample"
    return {
        "ok": ok,
        "accepted": ok,
        "result_type": result_type,
        "reason": ",".join(missing) if missing else result_type,
        "owner_pass_count": owner_pass,
        "non_owner_block_count": non_owner_block,
        "missing": missing,
    }


def _render_report(*, profile: dict[str, object], jvs: dict[str, object], logs: dict[str, object], status: dict[str, object]) -> str:
    learning = latest_voice_learning_summary()
    lines = [
        "# Voice Owner Voiceprint Live Check",
        "",
        f"- Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Overall: {'PASS' if status.get('ok') else 'NEEDS_ACTION'}",
        f"- Reason: {status.get('reason')}",
        "",
        "## Owner Profile",
        f"- Path: `{profile.get('path')}`",
        f"- Exists: {profile.get('exists')}",
        f"- Valid: {profile.get('valid')}",
        f"- Centroid length: {profile.get('centroid_len')}",
        "",
        "## JVS Health",
        f"- Base URL: `{jvs.get('base_url')}`",
        f"- OK: {jvs.get('ok')}",
        f"- Elapsed: {jvs.get('elapsed_ms')} ms",
        f"- Detail: {jvs.get('body_preview') or jvs.get('error') or ''}",
        "",
        "## Recent Live Evidence",
        f"- Counters: `{json.dumps(logs.get('counters') or {}, ensure_ascii=False)}`",
        "",
    ]
    recent = logs.get("recent_evidence") if isinstance(logs.get("recent_evidence"), list) else []
    if recent:
        lines.append("Recent lines:")
        lines.extend(f"- `{line}`" for line in recent)
    else:
        lines.append("- No recent SV evidence found. Speak once in always-on/PTT mode, then rerun this script.")
    lines.extend(
        [
            "",
            "## Adaptive Learning",
            f"- Learning samples: {learning.get('sample_count')}",
            f"- Thresholds: `{json.dumps(learning.get('thresholds') or {}, ensure_ascii=False)}`",
            "",
            "## Next Action",
            "- If profile is missing: open Jachin Console -> Wake Mode -> enroll 3 owner samples.",
            "- If owner pass evidence is missing: say one safe command in always-on mode and rerun with `--expect-owner-pass`.",
            "- If non-owner block evidence is missing: let a non-owner/noise source speak near the mic and rerun with `--expect-non-owner-block`.",
        ]
    )
    return "\n".join(lines) + "\n"


def _expectations(args: argparse.Namespace) -> dict[str, bool]:
    return {
        "expect_owner_pass": bool(args.expect_owner_pass),
        "expect_non_owner_block": bool(args.expect_non_owner_block),
    }


def _short_line(path: Path, line: str) -> str:
    line = re.sub(r"\s+", " ", line).strip()
    return f"{path.name}: {line[:240]}"


def _stable_error(exc: BaseException) -> str:
    text = str(exc)
    if "10061" in text or "Connection refused" in text or "actively refused" in text:
        return "connection_refused"
    if "timed out" in text or "timeout" in text.lower():
        return "timeout"
    return exc.__class__.__name__


if __name__ == "__main__":
    raise SystemExit(main())
