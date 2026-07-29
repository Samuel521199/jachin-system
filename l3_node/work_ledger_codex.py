"""Convention-based Codex collaboration for Work Ledger briefings.

Each report-worthy project owns one Codex conversation named ``工作计划``.
Work Ledger consults that conversation only when local evidence proves that
work changed but does not explain the work well enough for a human report.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from l3_node.work_ledger_codex_claims import build_codex_claim_fusion
from l3_node.work_ledger_codex_context import build_codex_context_pack


DEFAULT_WORK_PLAN_CONVERSATION = "工作计划"

CODEX_WORK_CHAIN_SCENARIOS: dict[str, dict[str, Any]] = {
    "task_alignment": {
        "label": "任务目标与执行计划",
        "phases": {"task_start"},
        "purpose": "把用户目标、既有上下文和代码现状整理成可执行计划",
        "output_use": "写入任务 Context Pack 和工作计划",
        "priority": 70,
    },
    "decision_support": {
        "label": "方案权衡与决策依据",
        "phases": {"task_start", "checkpoint"},
        "purpose": "比较候选方案、说明取舍依据并标记待验证假设",
        "output_use": "沉淀决策记录，避免后续重复争论",
        "priority": 75,
    },
    "progress_explanation": {
        "label": "改动含义与工作进展",
        "phases": {"checkpoint", "briefing"},
        "purpose": "把 Git 和文件变化解释成能被人理解的具体进展",
        "output_use": "补充日报和工作简报",
        "priority": 80,
    },
    "failure_diagnosis": {
        "label": "失败诊断与恢复建议",
        "phases": {"checkpoint", "end_day"},
        "purpose": "根据失败证据定位原因、排除已失败路径并给出恢复顺序",
        "output_use": "写入风险、阻塞和下一次恢复计划",
        "priority": 95,
    },
    "completion_review": {
        "label": "完成边界与验收核对",
        "phases": {"end_day", "briefing"},
        "purpose": "区分已完成、已验证、进行中和仅有文件变化的事项",
        "output_use": "防止日报夸大完成状态",
        "priority": 90,
    },
    "continuation_handoff": {
        "label": "下一轮续作任务书",
        "phases": {"end_day", "continuation"},
        "purpose": "生成下一轮 Codex/Cursor 可以直接续上的任务边界和步骤",
        "output_use": "更新 continuation prompt 和次日计划",
        "priority": 85,
    },
}


def work_plan_conversation_name() -> str:
    return (
        os.environ.get("JACHIN_WORK_LEDGER_CODEX_CONVERSATION")
        or DEFAULT_WORK_PLAN_CONVERSATION
    ).strip() or DEFAULT_WORK_PLAN_CONVERSATION


def _new_codex_invocation_id() -> str:
    return f"jcx-{uuid.uuid4().hex[:16]}"


def detect_brief_evidence_gaps(index: dict[str, Any]) -> list[dict[str, Any]]:
    """Find projects that changed but lack report-quality semantic evidence."""

    outcomes_by_session: dict[str, list[dict[str, Any]]] = {}
    for item in (index.get("verified_outcomes") or []) + (
        index.get("valued_outcomes") or []
    ):
        if not isinstance(item, dict):
            continue
        session_id = str(item.get("session_id") or "").strip()
        if session_id:
            outcomes_by_session.setdefault(session_id, []).append(item)

    gaps: list[dict[str, Any]] = []
    for row in index.get("session_evidence_digests") or []:
        if not isinstance(row, dict):
            continue
        session_id = str(row.get("session_id") or "").strip()
        project_name = str(row.get("project_name") or "").strip()
        project_path = str(row.get("project_path") or "").strip()
        git = row.get("git") if isinstance(row.get("git"), dict) else {}
        changed_files = [
            item
            for item in (git.get("changed_files") or [])
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        diff_stat = str(git.get("diff_stat") or "").strip()
        diff_patch = str(git.get("diff_patch") or "").strip()
        cached_diff_patch = str(git.get("cached_diff_patch") or "").strip()
        snippets = [
            item for item in (row.get("file_snippets") or []) if isinstance(item, dict)
        ]
        has_change_evidence = bool(
            changed_files or diff_stat or diff_patch or cached_diff_patch or snippets
        )
        if not has_change_evidence or not project_name or not project_path or not session_id:
            continue

        manual_notes = [
            str(item or "").strip()
            for item in (row.get("manual_notes") or [])
            if str(item or "").strip()
        ]
        traces = [
            item for item in (row.get("ai_work_traces") or []) if isinstance(item, dict)
        ]
        trace_buckets: dict[str, list[str]] = {
            "actions": [],
            "decisions": [],
            "failures": [],
            "next_steps": [],
        }
        for trace in traces:
            buckets = (
                trace.get("buckets")
                if isinstance(trace.get("buckets"), dict)
                else {}
            )
            for key in trace_buckets:
                trace_buckets[key].extend(
                    str(value or "").strip()
                    for value in (buckets.get(key) or [])
                    if str(value or "").strip()
                )

        session_outcomes = outcomes_by_session.get(session_id, [])
        gap_keys: list[str] = []
        if not (
            session_outcomes
            or manual_notes
            or trace_buckets["actions"]
            or trace_buckets["decisions"]
        ):
            gap_keys.append("accomplishment_meaning")
        if not (row.get("risk_candidates") or trace_buckets["failures"]):
            gap_keys.append("risk_and_unfinished")
        if not trace_buckets["next_steps"]:
            gap_keys.append("next_steps")
        if not session_outcomes:
            gap_keys.append("verification_and_completion_boundary")
        if not gap_keys:
            continue
        gaps.append(
            {
                "session_id": session_id,
                "project_name": project_name,
                "project_path": project_path,
                "title": row.get("title"),
                "user_goal": row.get("user_goal"),
                "gap_keys": gap_keys,
                "changed_files": changed_files[:300],
                "diff_stat": diff_stat[:3000],
                "diff_patch": diff_patch[:120000],
                "cached_diff_patch": cached_diff_patch[:60000],
                "file_snippets": snippets[:40],
                "existing_manual_notes": manual_notes[-12:],
                "existing_trace_buckets": trace_buckets,
            }
        )
    return gaps


def build_codex_work_plan_prompt(gap: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    project_name = str(gap.get("project_name") or "").strip()
    project_path = str(gap.get("project_path") or "").strip()
    gap_keys = [str(item) for item in gap.get("gap_keys") or []]
    changed_files = [
        {
            "path": str(item.get("path") or ""),
            "status": str(item.get("status") or "modified"),
        }
        for item in gap.get("changed_files") or []
        if isinstance(item, dict)
    ]
    gap_labels = {
        "accomplishment_meaning": "这些改动具体解决了什么问题、形成了什么可汇报进展",
        "risk_and_unfinished": "当前风险、阻塞和仍未完成的边界",
        "next_steps": "基于当前状态下一步应该做什么",
        "verification_and_completion_boundary": "哪些已经验证，哪些只能算正在开发或待验证",
    }
    questions = [
        gap_labels[key] for key in gap_keys if key in gap_labels
    ] or ["这些改动的真实工作含义和下一步"]
    context_pack = build_codex_context_pack(
        project_name=project_name,
        project_path=project_path,
        task_title=gap.get("title"),
        user_goal=gap.get("user_goal"),
        purpose="补齐工作简报缺失的事实解释、完成边界、风险和下一步",
        phase="briefing",
        trigger_reason="；".join(questions),
        evidence_gaps=gap_keys,
        changed_files=changed_files,
        diff_stat=gap.get("diff_stat"),
        diff_patch=gap.get("diff_patch"),
        cached_diff_patch=gap.get("cached_diff_patch"),
        file_snippets=[
            item
            for item in (gap.get("file_snippets") or [])
            if isinstance(item, dict)
        ],
    )
    evidence_outline = context_pack["context"]
    prompt = (
        f"请作为项目“{project_name}”的工作计划协作者，帮助 Jachin 补齐本次工作简报缺失的信息。\n"
        f"项目路径：{project_path}\n"
        "这是动态证据补全任务，不是让你泛泛总结整个项目。请结合本会话已有上下文，并实际读取本地 Git 状态、diff 和相关文件核对。\n\n"
        "Jachin 当前缺少以下信息：\n"
        + "\n".join(f"{index}. {question}" for index, question in enumerate(questions, 1))
        + "\n\nJachin 已掌握的证据摘要：\n"
        + str(context_pack["serialized"])
        + "\n\n请输出以下五部分，每部分逐条编号：\n"
        "1. 本次具体完成或推进了什么，以及它解决的问题或价值\n"
        "2. 涉及的关键模块和文件，并说明各自作用\n"
        "3. 已执行的验证、尚未验证的部分和完成边界\n"
        "4. 当前风险、阻塞或失败尝试\n"
        "5. 下一步可执行计划\n\n"
        "要求：只写能被本地文件、Git、测试输出或本会话事实支持的内容；把事实、推断和建议明确区分；"
        "证据不足就直接说明；不要输出空泛的“持续优化”“继续推进”。"
    )
    prompt_hash = hashlib.sha256(
        json.dumps(
            {
                "project_name": project_name,
                "project_path": project_path,
                "gap_keys": gap_keys,
                "context_digest": context_pack["digest"],
                "conversation": work_plan_conversation_name(),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return prompt, {
        "prompt_hash": prompt_hash,
        "gap_keys": gap_keys,
        "question_count": len(questions),
        "changed_file_count": len(changed_files),
        "conversation_name": work_plan_conversation_name(),
        "context_pack": {
            "digest": context_pack["digest"],
            "stats": context_pack["stats"],
        },
    }


def _evidence_signals(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    notes: list[str] = []
    actions: list[str] = []
    decisions: list[str] = []
    failures: list[str] = []
    next_steps: list[str] = []
    risk_candidates: list[dict[str, Any]] = []
    changed_files: list[dict[str, Any]] = []
    latest_checkpoint: dict[str, Any] = {}
    continuation_hit = False
    verified_markers = 0
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "")
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if source == "manual_note":
            text = str(payload.get("text") or item.get("summary") or "").strip()
            if text:
                notes.append(text)
        elif source == "ai_work_trace":
            analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
            buckets = analysis.get("buckets") if isinstance(analysis.get("buckets"), dict) else {}
            for key, target in (
                ("actions", actions),
                ("decisions", decisions),
                ("failures", failures),
                ("next_steps", next_steps),
            ):
                target.extend(
                    str(value or "").strip()
                    for value in (buckets.get(key) or [])
                    if str(value or "").strip()
                )
        elif source in {"work_checkpoint", "git_snapshot"}:
            latest_checkpoint = payload
            changed_files = [
                row
                for row in (payload.get("changed_files") or [])
                if isinstance(row, dict)
            ]
            risk_candidates = [
                row
                for row in (payload.get("risk_candidates") or [])
                if isinstance(row, dict)
            ]
        elif source == "file_content_snippets":
            risk_candidates.extend(
                row
                for row in (payload.get("risk_candidates") or [])
                if isinstance(row, dict)
            )
        elif source == "work_continuation_context":
            continuation_hit = continuation_hit or bool(payload.get("hit"))
        elif source in {
            "work_outcome",
            "work_outcome_verification",
            "work_value_event",
        }:
            verified_markers += 1
    return {
        "notes": notes,
        "actions": actions,
        "decisions": decisions,
        "failures": failures,
        "next_steps": next_steps,
        "risk_candidates": risk_candidates,
        "changed_files": changed_files,
        "latest_checkpoint": latest_checkpoint,
        "continuation_hit": continuation_hit,
        "verified_markers": verified_markers,
    }


def plan_codex_work_chain(
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    phase: str,
) -> dict[str, Any]:
    """Plan high-value Codex collaboration without blindly querying Codex."""

    phase_key = str(phase or "").strip().lower()
    signals = _evidence_signals(evidence)
    goal = str(session.get("user_goal") or "").strip()
    combined = " ".join(
        [goal, *signals["notes"][-12:], *signals["failures"][-8:]]
    ).lower()
    requests: list[dict[str, Any]] = []

    def add(scenario_id: str, reason: str, evidence_refs: list[str]) -> None:
        profile = CODEX_WORK_CHAIN_SCENARIOS[scenario_id]
        if phase_key not in profile["phases"]:
            return
        fingerprint_source = {
            "session_id": session.get("session_id"),
            "phase": phase_key,
            "scenario_id": scenario_id,
            "reason": reason,
            "goal": goal,
            "changed_files": signals["changed_files"][:80],
            "failure_tail": signals["failures"][-8:],
            "next_step_tail": signals["next_steps"][-8:],
            "checkpoint_fingerprint": signals["latest_checkpoint"].get("fingerprint"),
        }
        request_key = hashlib.sha256(
            json.dumps(
                fingerprint_source,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        requests.append(
            {
                "scenario_id": scenario_id,
                "label": profile["label"],
                "purpose": profile["purpose"],
                "output_use": profile["output_use"],
                "priority": profile["priority"],
                "phase": phase_key,
                "reason": reason,
                "evidence_refs": evidence_refs,
                "request_key": request_key,
                "status": "pending",
            }
        )

    if (
        phase_key == "task_start"
        and len(goal) < 24
        and not signals["continuation_hit"]
    ):
        add(
            "task_alignment",
            "任务已开始，但缺少足够具体的执行步骤或可直接续接的上次上下文",
            ["work_session", "work_continuation_context"],
        )
    decision_words = ("方案", "选择", "权衡", "架构", "应该", "是否", "对比")
    if any(word in combined for word in decision_words) and not signals["decisions"]:
        add(
            "decision_support",
            "目标包含方案或架构取舍，但当前没有明确决策依据",
            ["work_session", "manual_note", "ai_work_trace"],
        )
    if phase_key in {"checkpoint", "briefing"} and signals["changed_files"] and not (
        signals["actions"] or signals["notes"]
    ):
        add(
            "progress_explanation",
            "已经出现代码或文件变化，但缺少可以用于汇报的改动含义",
            ["work_checkpoint", "git_snapshot", "file_content_snippets"],
        )
    if phase_key in {"checkpoint", "end_day"} and (
        signals["failures"] or signals["risk_candidates"]
    ):
        add(
            "failure_diagnosis",
            "检测到失败记录或风险候选，需要形成原因、已排除路径和恢复顺序",
            ["ai_work_trace", "file_content_snippets", "work_checkpoint"],
        )
    if phase_key in {"end_day", "briefing"} and signals["changed_files"] and not signals[
        "verified_markers"
    ]:
        add(
            "completion_review",
            "存在改动证据，但缺少足够验收证据来判断完成边界",
            ["work_checkpoint", "work_outcome_verification"],
        )
    if phase_key in {"end_day", "continuation"} and not signals["next_steps"]:
        add(
            "continuation_handoff",
            "当前记录没有明确、可执行的下一步任务书",
            ["ai_work_trace", "manual_note", "work_checkpoint"],
        )

    requests.sort(key=lambda item: int(item.get("priority") or 0), reverse=True)
    return {
        "schema_version": 1,
        "phase": phase_key,
        "session_id": session.get("session_id"),
        "project_name": session.get("project_name"),
        "project_path": session.get("project_path"),
        "conversation_name": work_plan_conversation_name(),
        "request_count": len(requests),
        "requests": requests,
        "signals": {
            "changed_file_count": len(signals["changed_files"]),
            "failure_count": len(signals["failures"]),
            "risk_candidate_count": len(signals["risk_candidates"]),
            "next_step_count": len(signals["next_steps"]),
            "verified_marker_count": signals["verified_markers"],
            "continuation_hit": signals["continuation_hit"],
        },
    }


def record_codex_work_chain_plan(session_id: str, *, phase: str) -> dict[str, Any]:
    from l3_node.work_ledger import append_evidence, load_evidence
    from l3_node.work_ledger import _load_session as load_session

    session = load_session(session_id)
    evidence = load_evidence(session_id, 2000)
    plan = plan_codex_work_chain(session, evidence, phase=phase)
    existing_keys: set[str] = set()
    for item in evidence:
        if item.get("source") != "codex_work_chain_plan":
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        for request in payload.get("requests") or []:
            if isinstance(request, dict) and request.get("request_key"):
                existing_keys.add(str(request["request_key"]))
    new_requests = [
        request
        for request in plan["requests"]
        if str(request.get("request_key") or "") not in existing_keys
    ]
    plan["new_request_count"] = len(new_requests)
    plan["requests"] = new_requests
    if new_requests:
        appended = append_evidence(
            session_id,
            source="codex_work_chain_plan",
            summary=f"Codex 工作链新增 {len(new_requests)} 个协作建议",
            payload=plan,
            trust_level="system_observed",
        )
        plan["evidence_id"] = appended.get("evidence_id")
    return plan


def get_codex_work_chain_state(session_id: str) -> dict[str, Any]:
    from l3_node.work_ledger import load_evidence

    evidence = load_evidence(session_id, 2000)
    requests: dict[str, dict[str, Any]] = {}
    completed_keys: set[str] = set()
    for item in evidence:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item.get("source") == "codex_work_chain_plan":
            for request in payload.get("requests") or []:
                if not isinstance(request, dict):
                    continue
                key = str(request.get("request_key") or "")
                if key:
                    requests[key] = {
                        **request,
                        "_planned_at_ms": int(item.get("collected_at_ms") or 0),
                    }
        elif item.get("source") == "codex_work_plan_consultation":
            key = str(payload.get("request_key") or "")
            if key and payload.get("ok"):
                completed_keys.add(key)
            if payload.get("ok"):
                completed_keys.update(
                    str(value)
                    for value in (payload.get("request_keys") or [])
                    if str(value or "").strip()
                )
    for key, request in requests.items():
        request["status"] = "completed" if key in completed_keys else "pending"
    latest_by_scenario: dict[str, dict[str, Any]] = {}
    for request in requests.values():
        scenario_key = str(
            request.get("scenario_id") or request.get("label") or ""
        )
        previous = latest_by_scenario.get(scenario_key)
        if previous is None or int(request.get("_planned_at_ms") or 0) >= int(
            previous.get("_planned_at_ms") or 0
        ):
            latest_by_scenario[scenario_key] = request
    ordered = sorted(
        latest_by_scenario.values(),
        key=lambda item: (
            item.get("status") != "pending",
            -int(item.get("priority") or 0),
        ),
    )
    for request in ordered:
        request.pop("_planned_at_ms", None)
    return {
        "schema_version": 1,
        "session_id": session_id,
        "conversation_name": work_plan_conversation_name(),
        "request_count": len(ordered),
        "pending_count": sum(1 for item in ordered if item["status"] == "pending"),
        "completed_count": sum(
            1 for item in ordered if item["status"] == "completed"
        ),
        "requests": ordered,
    }


def _build_scenario_prompt_with_meta(
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
    request: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    signals = _evidence_signals(evidence)
    scenario_id = str(request.get("scenario_id") or "")
    profile = CODEX_WORK_CHAIN_SCENARIOS.get(scenario_id) or {}
    commands = (
        signals["latest_checkpoint"].get("commands")
        if isinstance(signals["latest_checkpoint"].get("commands"), dict)
        else {}
    )

    def command_stdout(name: str) -> str:
        row = commands.get(name)
        if isinstance(row, dict):
            return str(row.get("stdout") or "")
        return str(row or "")

    context_pack = build_codex_context_pack(
        project_name=str(session.get("project_name") or ""),
        project_path=str(session.get("project_path") or ""),
        task_title=session.get("title"),
        user_goal=session.get("user_goal"),
        purpose=profile.get("purpose") or request.get("purpose"),
        phase=request.get("phase"),
        trigger_reason=request.get("reason"),
        changed_files=signals["changed_files"],
        diff_stat=command_stdout("diff_stat"),
        diff_patch=command_stdout("diff_patch"),
        cached_diff_patch=command_stdout("cached_diff_patch"),
        failures=signals["failures"][-12:],
        risks=signals["risk_candidates"][:16],
        existing_decisions=signals["decisions"][-10:],
        existing_next_steps=signals["next_steps"][-10:],
    )
    prompt = (
        f"请在项目“{session.get('project_name')}”的工作计划上下文中完成一次“{profile.get('label') or scenario_id}”分析。\n"
        f"项目路径：{session.get('project_path')}\n"
        f"本次目的：{profile.get('purpose') or request.get('purpose')}\n"
        f"结果用途：{profile.get('output_use') or request.get('output_use')}\n\n"
        "当前工作 Context Pack：\n"
        + str(context_pack["serialized"])
        + "\n\n请读取真实 Git 状态、diff、相关文件和本会话上下文后回答。"
        "输出必须逐条列出：结论、证据、仍不确定的地方、建议动作。"
        "失败诊断必须写明已失败路径和下一条路径为什么不同；"
        "完成核对必须区分已修改、已验证、已完成、已交付；禁止编造。"
    )
    prompt_hash = hashlib.sha256(
        json.dumps(
            {
                "request_key": request.get("request_key"),
                "context_digest": context_pack["digest"],
                "conversation": work_plan_conversation_name(),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return prompt, prompt_hash, {
        "digest": context_pack["digest"],
        "stats": context_pack["stats"],
    }


def build_scenario_prompt(
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
    request: dict[str, Any],
) -> tuple[str, str]:
    prompt, prompt_hash, _meta = _build_scenario_prompt_with_meta(
        session,
        evidence,
        request,
    )
    return prompt, prompt_hash


def consult_codex_for_scenario(
    session_id: str,
    request_key: str,
    *,
    wait_seconds: int = 120,
    automation_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    from l3_node.work_ledger import append_evidence, load_evidence
    from l3_node.work_ledger import _load_session as load_session

    state = get_codex_work_chain_state(session_id)
    request = next(
        (
            item
            for item in state.get("requests") or []
            if str(item.get("request_key") or "") == str(request_key or "")
        ),
        None,
    )
    if not request:
        raise ValueError("codex_work_chain_request_not_found")
    if request.get("status") == "completed":
        return {
            "ok": True,
            "deduplicated": True,
            "request": request,
            "state": state,
        }
    session = load_session(session_id)
    evidence = load_evidence(session_id, 2000)
    prompt, prompt_hash, context_pack_meta = _build_scenario_prompt_with_meta(
        session,
        evidence,
        request,
    )
    invocation_id = _new_codex_invocation_id()
    if automation_factory is None:
        from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

        automation_factory = WindowsOSAutomation
    run = automation_factory().codex_work_plan_query(
        project_name=str(session.get("project_name") or ""),
        project_path=str(session.get("project_path") or ""),
        prompt=prompt,
        conversation_name=work_plan_conversation_name(),
        wait_seconds=wait_seconds,
        prompt_hash=prompt_hash,
        invocation_id=invocation_id,
        session_id=session_id,
        request_key=str(request.get("request_key") or ""),
        context_digest=str(context_pack_meta.get("digest") or ""),
        context_stats=(
            context_pack_meta.get("stats")
            if isinstance(context_pack_meta.get("stats"), dict)
            else {}
        ),
    )
    run_payload = asdict(run) if is_dataclass(run) else dict(run or {})
    run_evidence = (
        run_payload.get("evidence")
        if isinstance(run_payload.get("evidence"), dict)
        else {}
    )
    answer = str(run_evidence.get("answer") or "").strip()
    invocation_match = (
        run_evidence.get("invocation_match")
        if isinstance(run_evidence.get("invocation_match"), dict)
        else {}
    )
    ok = bool(
        run_payload.get("ok")
        and answer
        and invocation_match.get("ok")
        and str(invocation_match.get("invocation_id") or "") == invocation_id
    )
    claim_fusion = (
        build_codex_claim_fusion(
            answer,
            evidence,
            invocation_id=invocation_id,
            prompt_hash=prompt_hash,
        )
        if ok
        else {}
    )
    payload = {
        "ok": ok,
        "scenario_id": request.get("scenario_id"),
        "request_key": request.get("request_key"),
        "prompt_hash": prompt_hash,
        "context_pack": context_pack_meta,
        "invocation_id": invocation_id,
        "invocation_match": invocation_match,
        "prompt": prompt,
        "answer": answer,
        "answer_source": run_evidence.get("answer_source"),
        "answer_validation": run_evidence.get("answer_validation"),
        "claim_fusion": claim_fusion,
        "recovery": run_evidence.get("recovery") or {},
        "recovery_terminal": run_evidence.get("recovery_terminal") or {},
        "recovery_pending_user_confirmation": (
            run_evidence.get("recovery_pending_user_confirmation") or {}
        ),
        "project_name": session.get("project_name"),
        "project_path": session.get("project_path"),
        "conversation_name": work_plan_conversation_name(),
        "tool_detail": run_payload.get("detail"),
        "tool_evidence_path": run_evidence.get("evidence_path"),
        "evidence_panel_path": run_evidence.get("evidence_panel_path"),
        "report_path": run_evidence.get("report_path"),
    }
    appended = append_evidence(
        session_id,
        source="codex_work_plan_consultation",
        summary=(
            f"Codex 已完成“{request.get('label')}”协作"
            if ok
            else f"Codex“{request.get('label')}”协作未通过验证"
        ),
        payload=payload,
        trust_level="system_observed",
        source_refs=[
            {
                "type": "codex_work_chain_request",
                "request_key": request.get("request_key"),
            }
        ],
    )
    return {
        **payload,
        "work_ledger_evidence_id": appended.get("evidence_id"),
        "state": get_codex_work_chain_state(session_id),
    }


def _successful_prompt_hashes(session_id: str) -> set[str]:
    from l3_node.work_ledger import load_evidence

    hashes: set[str] = set()
    for evidence in load_evidence(session_id, 2000):
        if evidence.get("source") != "codex_work_plan_consultation":
            continue
        payload = (
            evidence.get("payload")
            if isinstance(evidence.get("payload"), dict)
            else {}
        )
        if payload.get("ok") and str(payload.get("prompt_hash") or "").strip():
            hashes.add(str(payload["prompt_hash"]))
    return hashes


def consult_codex_for_brief(
    index: dict[str, Any],
    *,
    max_projects: int = 3,
    wait_seconds: int = 120,
    automation_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Consult Codex only for unresolved evidence gaps and persist the result."""

    from l3_node.work_ledger import append_evidence, load_evidence

    gaps = detect_brief_evidence_gaps(index)
    results: list[dict[str, Any]] = []
    if not gaps:
        return {
            "ok": True,
            "consulted": False,
            "reason": "no_report_evidence_gap",
            "gap_count": 0,
            "results": results,
        }

    if automation_factory is None:
        from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation

        automation_factory = WindowsOSAutomation
    automation = automation_factory()
    for gap in gaps[: max(1, min(int(max_projects or 3), 5))]:
        session_id = str(gap["session_id"])
        record_codex_work_chain_plan(session_id, phase="briefing")
        chain_state = get_codex_work_chain_state(session_id)
        related_scenarios: set[str] = set()
        if "accomplishment_meaning" in gap.get("gap_keys", []):
            related_scenarios.add("progress_explanation")
        if "verification_and_completion_boundary" in gap.get("gap_keys", []):
            related_scenarios.add("completion_review")
        request_keys = [
            str(item.get("request_key") or "")
            for item in chain_state.get("requests") or []
            if item.get("status") == "pending"
            and str(item.get("scenario_id") or "") in related_scenarios
            and str(item.get("request_key") or "").strip()
        ]
        prompt, prompt_meta = build_codex_work_plan_prompt(gap)
        prompt_hash = str(prompt_meta["prompt_hash"])
        if prompt_hash in _successful_prompt_hashes(session_id):
            results.append(
                {
                    "ok": True,
                    "deduplicated": True,
                    "session_id": session_id,
                    "project_name": gap.get("project_name"),
                    "prompt_hash": prompt_hash,
                    "conversation_name": prompt_meta["conversation_name"],
                }
            )
            continue

        invocation_id = _new_codex_invocation_id()
        run = automation.codex_work_plan_query(
            project_name=str(gap.get("project_name") or ""),
            project_path=str(gap.get("project_path") or ""),
            prompt=prompt,
            conversation_name=str(prompt_meta["conversation_name"]),
            wait_seconds=wait_seconds,
            prompt_hash=prompt_hash,
            invocation_id=invocation_id,
            session_id=session_id,
            request_key=",".join(request_keys),
            context_digest=str(
                (prompt_meta.get("context_pack") or {}).get("digest") or ""
            ),
            context_stats=(
                (prompt_meta.get("context_pack") or {}).get("stats")
                if isinstance(
                    (prompt_meta.get("context_pack") or {}).get("stats"),
                    dict,
                )
                else {}
            ),
        )
        run_payload = asdict(run) if is_dataclass(run) else dict(run or {})
        run_evidence = (
            run_payload.get("evidence")
            if isinstance(run_payload.get("evidence"), dict)
            else {}
        )
        answer = str(run_evidence.get("answer") or "").strip()
        invocation_match = (
            run_evidence.get("invocation_match")
            if isinstance(run_evidence.get("invocation_match"), dict)
            else {}
        )
        ok = bool(
            run_payload.get("ok")
            and answer
            and invocation_match.get("ok")
            and str(invocation_match.get("invocation_id") or "")
            == invocation_id
        )
        claim_fusion = (
            build_codex_claim_fusion(
                answer,
                load_evidence(session_id, 2000),
                invocation_id=invocation_id,
                prompt_hash=prompt_hash,
            )
            if ok
            else {}
        )
        payload = {
            "ok": ok,
            "prompt_hash": prompt_hash,
            "invocation_id": invocation_id,
            "invocation_match": invocation_match,
            "request_keys": request_keys,
            "prompt": prompt,
            "prompt_meta": prompt_meta,
            "project_name": gap.get("project_name"),
            "project_path": gap.get("project_path"),
            "conversation_name": prompt_meta["conversation_name"],
            "answer": answer,
            "answer_source": run_evidence.get("answer_source"),
            "answer_validation": run_evidence.get("answer_validation"),
            "claim_fusion": claim_fusion,
            "recovery": run_evidence.get("recovery") or {},
            "recovery_terminal": run_evidence.get("recovery_terminal") or {},
            "recovery_pending_user_confirmation": (
                run_evidence.get("recovery_pending_user_confirmation") or {}
            ),
            "tool_detail": run_payload.get("detail"),
            "completion_state": run_evidence.get("completion_state") or {},
            "answer_length": len(answer),
            "tool_evidence_path": run_evidence.get("evidence_path"),
            "evidence_panel_path": run_evidence.get("evidence_panel_path"),
            "report_path": run_evidence.get("report_path"),
            "screenshots": run_evidence.get("screenshots") or {},
        }
        appended = append_evidence(
            session_id,
            source="codex_work_plan_consultation",
            summary=(
                f"已从 Codex“{prompt_meta['conversation_name']}”补充工作解释"
                if ok
                else f"Codex“{prompt_meta['conversation_name']}”协作未通过验证"
            ),
            payload=payload,
            trust_level="system_observed",
            source_refs=[
                {
                    "type": "codex_conversation",
                    "project_name": gap.get("project_name"),
                    "conversation_name": prompt_meta["conversation_name"],
                    "evidence_path": run_evidence.get("evidence_path"),
                }
            ],
        )
        results.append(
            {
                **payload,
                "session_id": session_id,
                "deduplicated": False,
                "work_ledger_evidence_id": appended.get("evidence_id"),
            }
        )
    successful = [item for item in results if item.get("ok")]
    reused_count = sum(1 for item in results if item.get("deduplicated"))
    effective_count = len(successful) + reused_count
    return {
        "ok": bool(effective_count),
        "consulted": any(not item.get("deduplicated") for item in results),
        "reason": (
            "codex_consultation_completed"
            if effective_count
            else "codex_consultation_no_verified_answer"
        ),
        "gap_count": len(gaps),
        "success_count": len(successful),
        "reused_count": reused_count,
        "effective_count": effective_count,
        "results": results,
    }
