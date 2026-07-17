"""Recovery playbook registry loaded from capability metadata.

MCPs and skills can declare a `recovery_playbook` block in plugin.json.  The
kernel reads those declarations and chooses one recovery step at a time using
the latest verification failure plus the full attempt history.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from .contracts import DecisionContract, VerificationReport, WorkOrder
from .recovery_playbook_schema import validate_recovery_playbook_manifest


@dataclass(slots=True)
class RecoveryCandidate:
    capability_id: str
    target_id: str
    strategy: str
    tool: str
    work_order_input: str
    rationale: str
    priority: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "target_id": self.target_id,
            "strategy": self.strategy,
            "tool": self.tool,
            "work_order_input": self.work_order_input,
            "rationale": self.rationale,
            "priority": self.priority,
            "metadata": self.metadata,
        }


class CapabilityRecoveryRegistry:
    def __init__(self, manifests: list[dict[str, Any]] | None = None) -> None:
        raw_manifests = manifests if manifests is not None else load_recovery_manifests()
        self._manifests = [
            manifest
            for manifest in raw_manifests
            if not validate_recovery_playbook_manifest(manifest)
        ]

    def max_attempts_for(self, *, role_agent: str, tool: str, default: int) -> int:
        found: list[int] = []
        for manifest in self._manifests:
            for target in _iter_targets(manifest):
                if _target_matches(target, role_agent=role_agent, tool=tool):
                    try:
                        found.append(int(target.get("max_attempts") or 0))
                    except Exception:
                        pass
        if not found:
            return default
        return max(1, min(8, max(found)))

    def select_next(
        self,
        *,
        contract: DecisionContract,
        failed_work_order: WorkOrder,
        verification: VerificationReport,
        attempt_records: list[Any],
    ) -> RecoveryCandidate | None:
        ranked = self.ranked_candidates(
            contract=contract,
            failed_work_order=failed_work_order,
            verification=verification,
            attempt_records=attempt_records,
        )
        eligible = [row for row in ranked if row.get("eligible")]
        if not eligible:
            return None
        return eligible[0]["candidate"]

    def candidate_snapshot(
        self,
        *,
        contract: DecisionContract,
        failed_work_order: WorkOrder,
        verification: VerificationReport,
        attempt_records: list[Any],
    ) -> list[dict[str, Any]]:
        return [
            _public_ranked_candidate(row)
            for row in self.ranked_candidates(
                contract=contract,
                failed_work_order=failed_work_order,
                verification=verification,
                attempt_records=attempt_records,
            )
        ]

    def ranked_candidates(
        self,
        *,
        contract: DecisionContract,
        failed_work_order: WorkOrder,
        verification: VerificationReport,
        attempt_records: list[Any],
    ) -> list[dict[str, Any]]:
        role_agent = failed_work_order.role_agent
        tool = str(failed_work_order.inputs.get("tool") or "")
        tried = {_fingerprint(getattr(r, "tool", ""), getattr(r, "strategy", "")) for r in attempt_records}
        rows: list[dict[str, Any]] = []
        for candidate in self._candidates(
            contract=contract,
            failed_work_order=failed_work_order,
            verification=verification,
            attempt_records=attempt_records,
        ):
            candidate.metadata = dict(candidate.metadata)
            scorecard = _candidate_scorecard(
                candidate,
                verification,
                attempt_records,
                role_agent=role_agent,
                tool=tool,
            )
            fingerprint = _fingerprint(candidate.tool, candidate.strategy)
            eligible = fingerprint not in tried and int(scorecard.get("score") or 0) > -100
            reject_reason = ""
            if fingerprint in tried:
                reject_reason = "same_tool_and_strategy_already_failed"
            elif int(scorecard.get("score") or 0) <= -100:
                reject_reason = "score_below_recovery_threshold"
            scorecard["eligible"] = eligible
            scorecard["reject_reason"] = reject_reason
            candidate.metadata["adaptive_scorecard"] = scorecard
            rows.append(
                {
                    "candidate": candidate,
                    "eligible": eligible,
                    "reject_reason": reject_reason,
                    "score": int(scorecard.get("score") or 0),
                    "priority": int(candidate.priority),
                }
            )
        rows.sort(key=lambda row: (1 if row["eligible"] else 0, int(row["score"]), -int(row["priority"])), reverse=True)
        return rows

    def _candidates(
        self,
        *,
        contract: DecisionContract,
        failed_work_order: WorkOrder,
        verification: VerificationReport,
        attempt_records: list[Any],
    ) -> list[RecoveryCandidate]:
        role_agent = failed_work_order.role_agent
        tool = str(failed_work_order.inputs.get("tool") or "")
        out: list[RecoveryCandidate] = []
        for manifest in self._manifests:
            capability_id = str(manifest.get("id") or manifest.get("capability_id") or manifest.get("name") or "unknown")
            for target in _iter_targets(manifest):
                if not _target_matches(target, role_agent=role_agent, tool=tool):
                    continue
                target_id = str(target.get("id") or f"{capability_id}:{role_agent}")
                for step in target.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    if not _when_matches(step.get("when") or {}, verification, attempt_records, tool=tool):
                        continue
                    candidate = _candidate_from_step(
                        capability_id=capability_id,
                        target_id=target_id,
                        step=step,
                        work_order=failed_work_order,
                        verification=verification,
                        contract=contract,
                        attempt_records=attempt_records,
                    )
                    if candidate:
                        out.append(candidate)
        return _dedupe_candidates(out)


def load_recovery_manifests() -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    builtin = Path(__file__).with_name("recovery_playbooks.builtin.json")
    _append_manifest(manifests, builtin)
    for root in _manifest_roots():
        if not root.exists():
            continue
        for path in root.rglob("plugin.json"):
            _append_manifest(manifests, path)
    return manifests


def _append_manifest(manifests: list[dict[str, Any]], path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return
    if isinstance(data, dict) and isinstance(data.get("recovery_playbook"), dict):
        if validate_recovery_playbook_manifest(data):
            return
        data = dict(data)
        data["_manifest_path"] = str(path)
        manifests.append(data)


def _manifest_roots() -> list[Path]:
    raw = os.getenv("JACHIN_RECOVERY_MANIFEST_ROOTS", "").strip()
    if raw:
        return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]
    repo = Path(__file__).resolve().parents[2]
    home = Path(os.getenv("JACHIN_HOME") or Path.home() / ".jachin")
    return [
        repo / "skills_repo",
        repo / "l3_client" / "local_mcps",
        home / "capabilities",
        home / "skills",
        home / "mcps",
    ]


def _iter_targets(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    playbook = manifest.get("recovery_playbook") or {}
    targets = playbook.get("targets") if isinstance(playbook, dict) else []
    return [x for x in targets if isinstance(x, dict)]


def _target_matches(target: dict[str, Any], *, role_agent: str, tool: str) -> bool:
    target_role = str(target.get("role_agent") or target.get("role") or "")
    if target_role and target_role != role_agent:
        return False
    patterns = target.get("tools") or target.get("tool_patterns") or []
    if isinstance(patterns, str):
        patterns = [patterns]
    if not patterns:
        return True
    return any(fnmatchcase(tool, str(pattern)) for pattern in patterns)


def _when_matches(
    when: dict[str, Any],
    verification: VerificationReport,
    attempt_records: list[Any],
    *,
    tool: str,
) -> bool:
    reason = (verification.failure_reason or "").lower()
    history = " ".join((x.failure_reason or "").lower() for x in attempt_records)
    failure_any = [str(x).lower() for x in (when.get("failure_any") or [])]
    if failure_any and not any(x in reason or x in history for x in failure_any):
        return False
    failure_all = [str(x).lower() for x in (when.get("failure_all") or [])]
    if failure_all and not all(x in reason or x in history for x in failure_all):
        return False
    tool_not_contains = [str(x).lower() for x in (when.get("tool_not_contains") or [])]
    if any(x in tool.lower() for x in tool_not_contains):
        return False
    after_attempt = when.get("after_attempt")
    if after_attempt is not None:
        try:
            if len(attempt_records) < int(after_attempt):
                return False
        except Exception:
            pass
    return True


def _candidate_from_step(
    *,
    capability_id: str,
    target_id: str,
    step: dict[str, Any],
    work_order: WorkOrder,
    verification: VerificationReport,
    contract: DecisionContract,
    attempt_records: list[Any],
) -> RecoveryCandidate | None:
    strategy = str(step.get("strategy") or "").strip()
    if not strategy:
        return None
    current_tool = str(work_order.inputs.get("tool") or "")
    tool = str(step.get("tool") or "$same")
    if tool == "$same":
        tool = current_tool
    base_action = str(work_order.inputs.get("work_order_input") or "")
    if isinstance(step.get("action_template"), dict):
        work_order_input = json.dumps(
            _render_template_obj(step["action_template"], work_order, contract),
            ensure_ascii=False,
        )
    else:
        patch = step.get("action_patch") if isinstance(step.get("action_patch"), dict) else {}
        work_order_input = _patch_json(base_action, _render_template_obj(patch, work_order, contract))
    return RecoveryCandidate(
        capability_id=capability_id,
        target_id=target_id,
        strategy=strategy,
        tool=tool,
        work_order_input=work_order_input,
        rationale=str(step.get("rationale") or verification.failure_reason or "recovery step from capability metadata"),
        priority=int(step.get("priority") or 100),
        metadata={
            "manifest_path": step.get("_manifest_path"),
            "failure_reason": verification.failure_reason,
            "history_len": len(attempt_records),
            "when": step.get("when") if isinstance(step.get("when"), dict) else {},
            "governance_policy": work_order.inputs.get("governance_policy") if isinstance(work_order.inputs.get("governance_policy"), dict) else {},
        },
    )


def _render_template_obj(obj: dict[str, Any], work_order: WorkOrder, contract: DecisionContract) -> dict[str, Any]:
    return {str(k): _render_value(v, work_order, contract) for k, v in obj.items()}


def _render_value(value: Any, work_order: WorkOrder, contract: DecisionContract) -> Any:
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    data = _json_obj(str(work_order.inputs.get("work_order_input") or ""))
    if value == "$same":
        return str(work_order.inputs.get("tool") or "")
    if value == "$app_name":
        return _first_present(data, ["app_name", "app", "name", "target", "keywords"]) or _target_name(work_order) or contract.goal
    if value == "$window_hint":
        return _first_present(data, ["window_title", "keywords", "title", "app_name", "app", "name"]) or _target_name(work_order) or contract.goal
    if value == "$path":
        return _first_present(data, ["path", "file_path", "target_path", "source"])
    key = value[1:]
    return data.get(key, value)


def _target_name(work_order: WorkOrder) -> str:
    target = work_order.inputs.get("target")
    if isinstance(target, dict):
        return str(target.get("name") or target.get("app_name") or target.get("title") or "").strip()
    return ""


def _score_candidate(
    candidate: RecoveryCandidate,
    verification: VerificationReport,
    attempt_records: list[RecoveryAttemptRecord],
    *,
    role_agent: str,
    tool: str,
) -> int:
    return int(
        _candidate_scorecard(
            candidate,
            verification,
            attempt_records,
            role_agent=role_agent,
            tool=tool,
        )["score"]
    )


def _candidate_scorecard(
    candidate: RecoveryCandidate,
    verification: VerificationReport,
    attempt_records: list[Any],
    *,
    role_agent: str,
    tool: str,
) -> dict[str, Any]:
    reason = (verification.failure_reason or "").lower()
    history_reasons = " ".join((x.failure_reason or "").lower() for x in attempt_records)
    score = max(0, 100 - int(candidate.priority))
    strategy = candidate.strategy.lower()
    reasons = [f"base_priority_score={score}"]
    current_class = _failure_signature(reason)
    history_classes = [_failure_signature(str(x.failure_reason or "").lower()) for x in attempt_records]
    used_strategies = {str(getattr(x, "strategy", "") or "").lower() for x in attempt_records}
    used_tools = {str(getattr(x, "tool", "") or "").lower() for x in attempt_records}
    failed_fingerprints = {_fingerprint(getattr(x, "tool", ""), getattr(x, "strategy", "")) for x in attempt_records}
    last_attempt = attempt_records[-1] if attempt_records else None
    last_tool = str(getattr(last_attempt, "tool", "") or "").lower() if last_attempt else ""
    last_strategy = str(getattr(last_attempt, "strategy", "") or "").lower() if last_attempt else ""
    last_class = history_classes[-1] if history_classes else ""
    candidate_tool = str(candidate.tool or "").lower()
    recovery_attempt_count = sum(1 for item in attempt_records if str(getattr(item, "strategy", "") or "").lower() != "initial")
    candidate_when = candidate.metadata.get("when") if isinstance(candidate.metadata, dict) else {}
    if isinstance(candidate_when, dict):
        history_needles = [str(x).lower() for x in (candidate_when.get("failure_any") or [])]
        history_hits = [needle for needle in history_needles if needle and (needle in reason or needle in history_reasons)]
        if history_hits:
            bonus = min(45, 15 * len(set(history_hits)))
            score += bonus
            reasons.append(f"history_failure_match_bonus={bonus}")
    if strategy == "retry_same_path" and len(attempt_records) == 1:
        score += 40
        reasons.append("first_retry_same_path_bonus=40")
    if strategy == "retry_same_path" and not attempt_records:
        score += 75
        reasons.append("first_controlled_retry_bonus=75")
    if strategy == "retry_same_path" and attempt_records:
        first_strategy = str(getattr(attempt_records[0], "strategy", "") or "").lower()
        if first_strategy == "initial":
            score += 15
            reasons.append("one_controlled_retry_after_initial_bonus=15")
    if "window" in reason and "switch" in strategy:
        score += 60
        reasons.append("window_failure_switch_bonus=60")
    if "focus" in reason and "switch" in strategy:
        score += 55
        reasons.append("focus_failure_switch_bonus=55")
    if "not found" in reason and "open" in strategy:
        score += 45
        reasons.append("not_found_open_bonus=45")
    if "path" in reason and "normalize" in strategy:
        score += 60
        reasons.append("path_normalize_bonus=60")
    if "permission" in reason and "reveal" in strategy:
        score += 25
        reasons.append("permission_reveal_bonus=25")
    if current_class == "tool_quality":
        if tool == "mcp:fetch" and candidate.tool == "mcp:fetch":
            score += 80
            reasons.append("same_fetch_tool_quality_refetch_bonus=80")
        if "fetch_access_or_bot_wall" in reason and any(token in strategy for token in ("fetch", "source", "refetch")):
            score += 95
            reasons.append("fetch_bot_wall_refetch_bonus=95")
        if "fetch_access_or_bot_wall" in reason and any(token in strategy for token in ("regenerate", "summarize", "summary")):
            score -= 90
            reasons.append("fetch_bot_wall_regenerate_penalty=-90")
        if any(token in strategy for token in ("regenerate", "clean", "summarize", "quality")):
            score += 85
            reasons.append("tool_quality_regenerate_or_clean_bonus=85")
        if any(token in strategy for token in ("fetch", "source", "refetch")):
            score += 55
            reasons.append("tool_quality_source_refresh_bonus=55")
    if any(token in reason for token in ("missing_source", "source_urls", "fetch_readable", "search_results")):
        if any(token in strategy for token in ("fetch", "source", "search")):
            score += 70
            reasons.append("missing_source_search_or_fetch_bonus=70")
    if any(token in reason for token in ("placeholder", "ellipsis", "truncation", "web_noise", "incomplete_sentence")):
        if any(token in strategy for token in ("regenerate", "clean", "summary", "summarize")):
            score += 75
            reasons.append("bad_summary_regenerate_bonus=75")
    if len(attempt_records) >= 2 and "retry_same_path" in strategy:
        score -= 80
        reasons.append("late_same_path_penalty=-80")
    if strategy in used_strategies:
        score -= 100
        reasons.append("strategy_already_failed_penalty=-100")
    if _fingerprint(candidate.tool, candidate.strategy) in failed_fingerprints:
        score -= 250
        reasons.append("same_tool_and_strategy_already_failed_penalty=-250")
    if recovery_attempt_count >= 1 and last_class and current_class == last_class and candidate_tool == last_tool and "retry" in strategy:
        score -= 70
        reasons.append("same_failure_same_tool_retry_penalty=-70")
    if len(attempt_records) >= 2 and candidate_tool and candidate_tool in used_tools and "switch" not in strategy:
        score -= 35
        reasons.append("same_tool_after_repeated_failures_penalty=-35")
    if len(attempt_records) >= 2 and candidate_tool and candidate_tool not in used_tools:
        score += 35
        reasons.append("new_tool_after_repeated_failures_bonus=35")
    if len(attempt_records) >= 1 and candidate_tool and candidate_tool != last_tool:
        if any(token in strategy for token in ("switch", "open", "fetch", "search", "regenerate", "normalize", "reveal")):
            score += 20
            reasons.append("changed_path_after_last_failure_bonus=20")
    if last_strategy and last_strategy != "initial" and strategy != last_strategy:
        score += 15
        reasons.append("strategy_shift_after_failure_bonus=15")
    if history_reasons and any(x in history_reasons for x in ("timeout", "busy", "connection")) and "backoff" in strategy:
        score += 30
        reasons.append("history_transport_backoff_bonus=30")
    repeated_current_class_count = history_classes.count(current_class) + (1 if current_class != "unknown" else 0)
    if recovery_attempt_count >= 1 and repeated_current_class_count >= 2 and current_class != "unknown":
        if current_class == "tool_quality" and any(token in strategy for token in ("regenerate", "quality", "clean")):
            score += 30
            reasons.append("repeated_tool_quality_escalation_bonus=30")
        elif current_class == "tool_quality" and any(token in strategy for token in ("source", "fetch", "search")):
            score += 20
            reasons.append("repeated_tool_quality_source_refresh_bonus=20")
        elif "retry_same_path" in strategy:
            score -= 60
            reasons.append("repeated_same_failure_retry_penalty=-60")
    if len(attempt_records) >= 3 and any(token in strategy for token in ("ask", "confirm", "manual", "review")):
        score += 65
        reasons.append("exhausted_auto_paths_manual_review_bonus=65")
    if len(attempt_records) >= 4 and not any(token in strategy for token in ("ask", "confirm", "manual", "review", "abort")):
        score -= 45
        reasons.append("near_attempt_limit_auto_path_penalty=-45")
    governance = candidate.metadata.get("governance_policy") if isinstance(candidate.metadata, dict) else {}
    if isinstance(governance, dict):
        governance_mode = str(governance.get("execution_mode") or "").lower()
        governance_score = _safe_int(governance.get("score"), 100)
        if governance_mode in {"degraded_auto", "manual_review"} or governance_score < 70:
            if any(token in strategy for token in ("switch", "regenerate", "clean", "normalize", "degrade", "fallback", "source", "fetch")):
                score += 35
                reasons.append("governance_low_health_alternate_path_bonus=35")
            if "retry_same_path" in strategy:
                score -= 120
                reasons.append("governance_low_health_same_path_penalty=-120")
        if governance_mode == "manual_review" or governance_score < 50:
            if any(token in strategy for token in ("ask", "confirm", "manual", "review")):
                score += 80
                reasons.append("governance_critical_manual_review_bonus=80")
    score = max(-500, score)
    return {
        "score": score,
        "current_failure_class": current_class,
        "history_failure_classes": history_classes,
        "last_failure_class": last_class,
        "current_failure_reason": verification.failure_reason,
        "history_failure_reasons": [str(getattr(x, "failure_reason", "") or "") for x in attempt_records],
        "role_agent": role_agent,
        "failed_tool": tool,
        "last_tool": last_tool,
        "last_strategy": last_strategy,
        "candidate_tool": candidate.tool,
        "candidate_strategy": candidate.strategy,
        "governance_policy": governance if isinstance(governance, dict) else {},
        "rationale": reasons,
    }


def _public_ranked_candidate(row: dict[str, Any]) -> dict[str, Any]:
    candidate = row.get("candidate")
    if not isinstance(candidate, RecoveryCandidate):
        return {}
    payload = candidate.to_dict()
    payload["eligible"] = bool(row.get("eligible"))
    payload["reject_reason"] = str(row.get("reject_reason") or "")
    payload["rank_score"] = int(row.get("score") or 0)
    return payload


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _failure_signature(reason: str) -> str:
    text = str(reason or "").lower()
    if "tool_quality" in text or any(
        token in text
        for token in (
            "summary_placeholder_text",
            "summary_contains_web_noise",
            "summary_has_ellipsis_truncation",
            "summary_incomplete_sentence",
            "summary_missing_source_urls",
            "fetch_readable_content_missing",
            "fetch_access_or_bot_wall",
            "search_results_missing",
        )
    ):
        return "tool_quality"
    if any(token in text for token in ("timeout", "connection", "busy", "temporarily")):
        return "transport"
    if any(token in text for token in ("focus", "foreground", "window")):
        return "window_state"
    if any(token in text for token in ("not found", "missing", "recipient", "target")):
        return "target_resolution"
    if any(token in text for token in ("permission", "not allowed", "unauthorized", "401", "403")):
        return "permission"
    if any(token in text for token in ("path", "file")):
        return "file_path"
    return "unknown"


def _dedupe_candidates(candidates: list[RecoveryCandidate]) -> list[RecoveryCandidate]:
    seen: set[str] = set()
    out: list[RecoveryCandidate] = []
    for c in candidates:
        key = _fingerprint(c.tool, c.strategy)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _fingerprint(tool: str, strategy: str) -> str:
    return f"{tool}::{strategy}"


def _patch_json(work_order_input: str, patch: dict[str, Any]) -> str:
    obj = _json_obj(work_order_input)
    obj.update(patch)
    return json.dumps(obj, ensure_ascii=False)


def _json_obj(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _first_present(obj: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
