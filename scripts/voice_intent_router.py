"""
陪伴态语音路由 — 调用桌面端 SSOT（voiceIntentRouter.ts），与 chat.tsx dispatchVoiceUtterance 对齐。

SSOT: clients/desktop/src/voice/voiceIntentRouter.ts
CLI: clients/desktop/scripts/voice-intent-router-cli.ts
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DESKTOP_DIR = _REPO_ROOT / "clients" / "desktop"
_ROUTER_CLI = _DESKTOP_DIR / "scripts" / "voice-intent-router-cli.ts"
_BOM = b"\xef\xbb\xbf"


def _strip_json_bom_if_needed(path: Path) -> None:
    """tsx 无法解析带 BOM 的 package.json；与 ensure-json-no-bom.mjs 行为一致。"""
    if not path.is_file():
        return
    raw = path.read_bytes()
    if len(raw) >= 3 and raw[:3] == _BOM:
        path.write_bytes(raw[3:])


def _ensure_desktop_json_ready() -> None:
    node = _DESKTOP_DIR / "node_modules"
    ensure_script = _DESKTOP_DIR / "scripts" / "ensure-json-no-bom.mjs"
    if ensure_script.is_file():
        subprocess.run(
            ["node", str(ensure_script)],
            cwd=str(_DESKTOP_DIR),
            capture_output=True,
            timeout=15,
            check=False,
        )
    else:
        _strip_json_bom_if_needed(_DESKTOP_DIR / "package.json")


def _run_voice_router_cli(payload_json: str) -> str:
    _ensure_desktop_json_ready()
    cli_arg = "scripts/voice-intent-router-cli.ts"
    if sys.platform == "win32":
        proc = subprocess.run(
            f"npx tsx {cli_arg}",
            cwd=str(_DESKTOP_DIR),
            input=payload_json,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            shell=True,
        )
    else:
        proc = subprocess.run(
            ["npx", "tsx", cli_arg],
            cwd=str(_DESKTOP_DIR),
            input=payload_json,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"dispatch_voice_intent failed (code={proc.returncode}): {err}")
    line = (proc.stdout or "").strip()
    if not line:
        raise RuntimeError("dispatch_voice_intent: empty stdout")
    return line


def _default_ctx() -> dict[str, Any]:
    return {"activeTasks": []}


def dispatch_voice_intent(text: str, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """与 dispatchVoiceIntent(rawText, ctx) 等价；失败时抛出 RuntimeError。"""
    if not _ROUTER_CLI.is_file():
        raise RuntimeError(f"voice intent router CLI missing: {_ROUTER_CLI}")
    payload = json.dumps({"text": text or "", "ctx": ctx if ctx is not None else _default_ctx()}, ensure_ascii=False)
    line = _run_voice_router_cli(payload)
    return json.loads(line)


def build_light_task_context(
    decision: dict[str, Any],
    active_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """对齐 chat.tsx dispatchVoiceUtterance 的 light_task_context 构造。"""
    hints = decision.get("router_hints") or {}
    if not hints.get("inject_light_task_context"):
        return None
    tasks = active_tasks or []
    if not tasks:
        ids = decision.get("active_task_ids") or []
        tasks = [{"id": tid, "title": ""} for tid in ids if tid]
    if not tasks:
        return None
    target_id = decision.get("target_task_id")
    lead = next((t for t in tasks if t.get("id") == target_id), tasks[0])
    title = (lead.get("title") or "").strip()
    summary = (
        f"{title}（另有{len(tasks) - 1}个任务）"
        if title and len(tasks) > 1
        else title
    )
    focused = target_id or (tasks[0].get("id") if tasks else None)
    return {
        "active_tasks": [{"id": t.get("id", ""), "title": t.get("title") or ""} for t in tasks[:3]],
        "focused_task_id": focused,
        "summary": summary or None,
        "source": "voice_intent_router",
    }


def build_companion_implicit_signals(
    *,
    raw_stt_text: str,
    decision: dict[str, Any],
    source: str = "voice_latency_bench",
    active_tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    对齐 chat.tsx doActualSend + dispatchVoiceUtterance 写入 L3 的 implicit_signals。
    """
    hints = decision.get("router_hints") or {}
    routed_text = (decision.get("normalized_text") or raw_stt_text).strip() or raw_stt_text
    tasks = active_tasks
    if tasks is None:
        ids = decision.get("active_task_ids") or []
        tasks = [{"id": tid, "title": decision.get("task_title") or ""} for tid in ids if tid]
    lead = next(
        (t for t in tasks if t.get("id") == decision.get("target_task_id")),
        tasks[0] if tasks else None,
    )
    task_summary = ""
    if lead and (lead.get("title") or "").strip():
        task_summary = lead["title"]
        if len(tasks) > 1:
            task_summary += f"（另有{len(tasks) - 1}个任务）"
    light_ctx = build_light_task_context(decision, tasks)

    signals: dict[str, Any] = {
        "desktop_companion": True,
        "source": "desktop_voice_companion",
        "voice_raw_stt_text": raw_stt_text,
        "voice_routed_text": routed_text,
        "voice_dispatcher_decision": decision,
        "voice_decision_id": decision.get("decision_id"),
        "voice_dispatch_tier": decision.get("tier"),
        "voice_intent_class": decision.get("intent_class"),
        "voice_dispatch_lane": decision.get("execution_lane"),
        "voice_interrupt_verdict": decision.get("interrupt_verdict"),
        "voice_route_source": decision.get("route_source"),
        "voice_route_notes": decision.get("route_notes"),
        "voice_confidence": decision.get("confidence"),
        "voice_task_title": decision.get("task_title"),
        "voice_active_task_ids": decision.get("active_task_ids"),
        "voice_fast_lane": hints.get("fast_lane"),
        "skip_context_retrieval": hints.get("skip_context_retrieval"),
        "skip_context_sniffer": hints.get("skip_context_sniffer"),
        "skip_experience_rag": hints.get("skip_experience_rag"),
        "skip_gateway_enrich": hints.get("skip_gateway_enrich"),
        "prefer_direct_llm": hints.get("prefer_direct_llm"),
        "force_background": hints.get("force_background"),
        "acceptance_round": hints.get("acceptance_round"),
        "inject_task_context": hints.get("inject_task_context"),
        "inject_light_task_context": hints.get("inject_light_task_context"),
        "max_foreground_tool_sec": hints.get("max_foreground_tool_sec", 5),
        "awaiting_confirmation": hints.get("awaiting_confirmation"),
        "clarification_pending": hints.get("clarification_pending"),
        "target_task_id": decision.get("target_task_id"),
        "task_context_summary": task_summary or None,
        "bench_route_source": source,
    }
    if light_ctx is not None:
        signals["light_task_context"] = light_ctx
    return signals


if __name__ == "__main__":
    text = sys.argv[1] if len(sys.argv) > 1 else ""
    ctx = json.loads(sys.argv[2]) if len(sys.argv) > 2 else _default_ctx()
    print(json.dumps(dispatch_voice_intent(text, ctx), ensure_ascii=False, indent=2))
