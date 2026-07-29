"""Manifest-driven staged recovery for Codex desktop collaboration."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any


CODEX_TOOL_ID = "mcp:windows_codex_work_plan_query"
BLOCKED_FAILURE_MARKERS = (
    "permission_required",
    "requires_confirmation",
    "not allowed",
    "cancelled",
)


@dataclass(slots=True)
class CodexRecoveryAttempt:
    attempt_no: int
    stage: str
    strategy: str
    failure_reason: str
    evidence: dict[str, Any]
    elapsed_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CodexRecoveryDecision:
    attempt_no: int
    stage: str
    strategy: str
    rationale: str
    action_patch: dict[str, Any]
    capability_id: str
    target_id: str
    history_reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CodexStageRecoveryPlanner:
    """Choose one new recovery path after each observed stage failure."""

    def __init__(
        self,
        *,
        manifests: list[dict[str, Any]] | None = None,
        journal_path: str | Path | None = None,
        max_attempts: int = 5,
    ) -> None:
        disabled = str(
            os.environ.get("JACHIN_CODEX_RECOVERY_DISABLED") or ""
        ).strip().casefold() in {"1", "true", "yes", "on"}
        self._manifests = (
            []
            if disabled
            else (
                manifests
                if manifests is not None
                else load_codex_recovery_manifests()
            )
        )
        self._targets = _matching_targets(self._manifests)
        declared = [
            int(target.get("max_attempts") or 0)
            for _, target in self._targets
            if str(target.get("max_attempts") or "").isdigit()
        ]
        self.max_attempts = max(
            1,
            min(8, max(declared or [int(max_attempts or 5)])),
        )
        self.attempts: list[CodexRecoveryAttempt] = []
        self.decisions: list[CodexRecoveryDecision] = []
        self.journal_path = Path(journal_path) if journal_path else None

    def observe_failure(
        self,
        *,
        stage: str,
        failure_reason: str,
        attempted_strategy: str,
        evidence: dict[str, Any] | None = None,
        elapsed_ms: float | None = None,
    ) -> CodexRecoveryDecision | None:
        clean_stage = normalize_codex_stage(stage)
        attempt = CodexRecoveryAttempt(
            attempt_no=len(self.attempts) + 1,
            stage=clean_stage,
            strategy=str(attempted_strategy or "initial"),
            failure_reason=str(failure_reason or "unknown_failure"),
            evidence=dict(evidence or {}),
            elapsed_ms=elapsed_ms,
        )
        self.attempts.append(attempt)
        if len(self.decisions) >= self.max_attempts:
            return None
        lowered = attempt.failure_reason.casefold()
        if any(marker in lowered for marker in BLOCKED_FAILURE_MARKERS):
            return None

        ranked = self._ranked_candidates(clean_stage, attempt.failure_reason)
        if not ranked:
            return None
        selected = ranked[0]
        decision = CodexRecoveryDecision(
            attempt_no=len(self.decisions) + 1,
            stage=clean_stage,
            strategy=selected["strategy"],
            rationale=selected["rationale"],
            action_patch=selected["action_patch"],
            capability_id=selected["capability_id"],
            target_id=selected["target_id"],
            history_reasons=[row.failure_reason for row in self.attempts],
        )
        self.decisions.append(decision)
        return decision

    def record_success(
        self,
        *,
        stage: str,
        strategy: str,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "event": "codex_recovery_succeeded",
            "stage": normalize_codex_stage(stage),
            "strategy": str(strategy or ""),
            "attempt_count": len(self.attempts),
            "failure_history": [row.to_dict() for row in self.attempts],
            "decision_history": [row.to_dict() for row in self.decisions],
            "success_evidence": dict(evidence or {}),
        }
        _append_jsonl(self.journal_path, payload)
        return payload

    def record_terminal_failure(self, *, final_reason: str) -> dict[str, Any]:
        payload = {
            "event": "codex_recovery_exhausted",
            "final_reason": str(final_reason or "unknown_failure"),
            "attempt_count": len(self.attempts),
            "max_attempts": self.max_attempts,
            "failure_history": [row.to_dict() for row in self.attempts],
            "decision_history": [row.to_dict() for row in self.decisions],
            "recommended_next_steps": recommend_codex_recovery_actions(
                self.attempts,
                final_reason,
            ),
        }
        _append_jsonl(self.journal_path, payload)
        return payload

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "selection_mode": "one_path_after_each_failure",
            "manifest_target_count": len(self._targets),
            "max_attempts": self.max_attempts,
            "attempts": [row.to_dict() for row in self.attempts],
            "decisions": [row.to_dict() for row in self.decisions],
        }

    def _ranked_candidates(
        self,
        stage: str,
        failure_reason: str,
    ) -> list[dict[str, Any]]:
        used = {row.strategy for row in self.attempts} | {
            row.strategy for row in self.decisions
        }
        history = " ".join(row.failure_reason.casefold() for row in self.attempts)
        current = str(failure_reason or "").casefold()
        rows: list[dict[str, Any]] = []
        for manifest, target in self._targets:
            capability_id = str(
                manifest.get("id")
                or manifest.get("capability_id")
                or "unknown_capability"
            )
            target_id = str(target.get("id") or f"{capability_id}:codex")
            for step in target.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                strategy = str(step.get("strategy") or "").strip()
                patch = (
                    dict(step.get("action_patch"))
                    if isinstance(step.get("action_patch"), dict)
                    else {}
                )
                recovery_stage = normalize_codex_stage(
                    str(patch.get("recovery_stage") or "")
                )
                if recovery_stage and recovery_stage != stage:
                    continue
                if not _when_matches(
                    step.get("when"),
                    current=current,
                    history=history,
                    attempt_count=len(self.attempts),
                ):
                    continue
                if not strategy or strategy in used:
                    continue
                failure_hits = sum(
                    1
                    for marker in (step.get("when") or {}).get("failure_any", [])
                    if str(marker).casefold() in current
                    or str(marker).casefold() in history
                )
                priority = int(step.get("priority") or 100)
                rows.append(
                    {
                        "capability_id": capability_id,
                        "target_id": target_id,
                        "strategy": strategy,
                        "rationale": str(
                            step.get("rationale")
                            or "recovery path declared by capability metadata"
                        ),
                        "action_patch": patch,
                        "score": (1000 - priority) + failure_hits * 100,
                    }
                )
        rows.sort(key=lambda row: int(row["score"]), reverse=True)
        return rows


def normalize_codex_stage(stage: str) -> str:
    value = str(stage or "").strip().casefold().replace("-", "_")
    if not value:
        return ""
    prefix = value.split(".", 1)[0]
    aliases = {
        "open_codex": "open",
        "navigate_conversation": "navigate",
        "verify_context": "verify",
        "locate_composer": "input",
        "paste_prompt": "input",
        "verify_context_before_submit": "submit",
        "wait_reply": "wait",
        "extract_reply": "extract",
        "reply_validated": "fuse",
    }
    return aliases.get(prefix, prefix)


def load_codex_recovery_manifests() -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for root in _manifest_roots():
        if root.is_file():
            _append_manifest(manifests, root)
            continue
        if not root.exists():
            continue
        for path in root.rglob("plugin.json"):
            _append_manifest(manifests, path)
    return manifests


def recommend_codex_recovery_actions(
    attempts: list[CodexRecoveryAttempt],
    final_reason: str,
) -> list[str]:
    text = " ".join(
        [str(final_reason or ""), *(row.failure_reason for row in attempts)]
    ).casefold()
    actions: list[str] = []
    if "permission" in text:
        actions.append("在 Codex 中处理权限请求后恢复同一个 invocation。")
    if any(marker in text for marker in ("focus", "window", "foreground")):
        actions.append("检查 Codex 窗口是否被最小化或被其他窗口遮挡。")
    if any(marker in text for marker in ("timeout", "generation", "network")):
        actions.append("检查 Codex 生成状态和网络，再从未完成阶段恢复。")
    if any(marker in text for marker in ("extract", "copy", "reply", "marker")):
        actions.append("保留当前回复，重新执行原生复制与视觉/OCR 交叉提取。")
    if not actions:
        actions.append("查看 Evidence 中最后一次失败阶段并人工确认目标上下文。")
    return actions


def _matching_targets(
    manifests: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for manifest in manifests:
        playbook = manifest.get("recovery_playbook")
        if not isinstance(playbook, dict):
            continue
        for target in playbook.get("targets") or []:
            if not isinstance(target, dict):
                continue
            patterns = target.get("tools") or target.get("tool_patterns") or []
            if isinstance(patterns, str):
                patterns = [patterns]
            if any(fnmatchcase(CODEX_TOOL_ID, str(pattern)) for pattern in patterns):
                out.append((manifest, target))
    return out


def _when_matches(
    when: Any,
    *,
    current: str,
    history: str,
    attempt_count: int,
) -> bool:
    if not isinstance(when, dict):
        return True
    failure_any = [
        str(item).casefold() for item in when.get("failure_any") or []
    ]
    if failure_any and not any(
        marker in current or marker in history for marker in failure_any
    ):
        return False
    failure_all = [
        str(item).casefold() for item in when.get("failure_all") or []
    ]
    if failure_all and not all(
        marker in current or marker in history for marker in failure_all
    ):
        return False
    after_attempt = when.get("after_attempt")
    if after_attempt is not None:
        try:
            if attempt_count < int(after_attempt):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _manifest_roots() -> list[Path]:
    raw = os.environ.get("JACHIN_RECOVERY_MANIFEST_ROOTS", "").strip()
    if raw:
        return [
            Path(item).expanduser()
            for item in raw.split(os.pathsep)
            if item.strip()
        ]
    repo = Path(__file__).resolve().parents[3]
    home = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin")
    return [
        repo
        / "skills_repo"
        / "l1_upload_stubs"
        / "com.jachin.mcp.stub.windows.uia"
        / "plugin.json",
        home / "capabilities",
        home / "mcps",
        home / "skills",
    ]


def _append_manifest(manifests: list[dict[str, Any]], path: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return
    if isinstance(value, dict) and isinstance(value.get("recovery_playbook"), dict):
        manifests.append(value)


def _append_jsonl(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except OSError:
        return
