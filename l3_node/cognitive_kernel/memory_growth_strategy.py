"""Strategy policy helpers for Memory Growth artifacts.

The strategy layer converts governance effectiveness into durable metadata on
concept/playbook Markdown pages. Recall and recovery can then prefer knowledge
that has been useful and become cautious around knowledge that repeatedly led
to failed governance.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any


def load_governance_strategy_policy(root: Path) -> dict[str, Any]:
    path = root / "indexes" / "governance_effectiveness.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    policy = payload.get("strategy_policy") if isinstance(payload, dict) else None
    return policy if isinstance(policy, dict) else {}


def persist_strategy_policy_to_artifacts(root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    """Write strategy metadata into concept/playbook frontmatter."""

    if not isinstance(policy, dict) or not policy.get("action_policy"):
        return {"updated_count": 0, "skipped_count": 0, "paths": []}
    updated = 0
    skipped = 0
    paths: list[str] = []
    for path in _artifact_paths(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            skipped += 1
            continue
        frontmatter = parse_frontmatter(text)
        action = infer_governance_action(frontmatter=frontmatter, path=path, root=root)
        if not action:
            skipped += 1
            continue
        strategy = strategy_for_action(policy, action)
        if not strategy:
            skipped += 1
            continue
        next_text = text
        next_text = upsert_frontmatter_field(next_text, "governance_strategy_action", action)
        next_text = upsert_frontmatter_field(next_text, "governance_strategy_weight", f"{float(strategy.get('weight') or 1.0):.2f}")
        next_text = upsert_frontmatter_field(next_text, "governance_execution_mode", str(strategy.get("execution_mode") or "normal"))
        next_text = upsert_frontmatter_field(
            next_text,
            "governance_requires_more_evidence",
            bool(strategy.get("requires_more_evidence")),
        )
        next_text = upsert_frontmatter_field(next_text, "governance_strategy_reason", str(strategy.get("reason") or "strategy_policy"))
        next_text = upsert_frontmatter_field(next_text, "governance_strategy_updated_at", _iso_now())
        if next_text != text:
            path.write_text(next_text, encoding="utf-8")
            updated += 1
            paths.append(str(path.relative_to(root)))
        else:
            skipped += 1
    return {"updated_count": updated, "skipped_count": skipped, "paths": paths}


def record_artifact_usage(
    *,
    root: Path,
    memory_context_refs: list[dict[str, Any]],
    turn_id: str,
    verification_status: str,
    failure_reason: str = "",
) -> dict[str, Any]:
    """Update artifact-level learning counters from a completed turn."""

    ok = str(verification_status or "").lower() == "passed"
    failed = str(verification_status or "").lower() == "failed"
    updated_paths: list[str] = []
    skipped = 0
    for ref in memory_context_refs:
        if not isinstance(ref, dict) or not str(ref.get("source") or "").startswith("Memory Growth"):
            skipped += 1
            continue
        path = resolve_artifact_path(root=root, ref=ref)
        if path is None or not path.exists() or path.name == "README.md":
            skipped += 1
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        frontmatter = parse_frontmatter(text)
        usage_count = int(_float(frontmatter.get("memory_use_count"), 0)) + 1
        success_count = int(_float(frontmatter.get("memory_success_count"), 0)) + (1 if ok else 0)
        failure_count = int(_float(frontmatter.get("memory_failure_count"), 0)) + (1 if failed else 0)
        success_rate = round(success_count / max(1, success_count + failure_count), 3)
        next_text = text
        next_text = upsert_frontmatter_field(next_text, "memory_use_count", usage_count)
        next_text = upsert_frontmatter_field(next_text, "memory_success_count", success_count)
        next_text = upsert_frontmatter_field(next_text, "memory_failure_count", failure_count)
        next_text = upsert_frontmatter_field(next_text, "memory_success_rate", success_rate)
        next_text = upsert_frontmatter_field(next_text, "memory_last_used_at", _iso_now())
        next_text = upsert_frontmatter_field(next_text, "memory_last_turn_id", turn_id)
        if failed:
            next_text = upsert_frontmatter_field(next_text, "memory_last_failure_reason", failure_reason or "verification_failed")
        path.write_text(next_text, encoding="utf-8")
        updated_paths.append(str(path.relative_to(root)))
    _write_artifact_usage_index(root)
    return {"updated_count": len(updated_paths), "skipped_count": skipped, "paths": updated_paths}


def refresh_artifact_usage_index(root: Path) -> None:
    _write_artifact_usage_index(root)


def resolve_artifact_path(*, root: Path, ref: dict[str, Any]) -> Path | None:
    raw_path = str(ref.get("artifact_path") or "").strip()
    if raw_path:
        candidate = Path(raw_path)
        if candidate.is_absolute() and candidate.exists():
            return candidate
        candidate = root / raw_path
        if candidate.exists():
            return candidate
    preview = str(ref.get("preview") or "")
    match = re.search(r"(?:concept|playbook) path=([^;]+)", preview)
    if match:
        candidate = Path(match.group(1).strip())
        if candidate.is_absolute() and candidate.exists():
            return candidate
        candidate = root / match.group(1).strip()
        if candidate.exists():
            return candidate
    memory_id = str(ref.get("memory_id") or "")
    if memory_id.startswith("memory_growth:concept:"):
        slug = memory_id.rsplit(":", 1)[-1]
        matches = list((root / "concepts").glob(f"**/{slug}.md"))
        return matches[0] if matches else None
    if memory_id.startswith("memory_growth:playbook:"):
        slug = memory_id.rsplit(":", 1)[-1]
        matches = list((root / "playbooks").glob(f"**/{slug}.md"))
        return matches[0] if matches else None
    return None


def artifact_strategy_metadata(*, root: Path, path: Path, frontmatter: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    action = str(frontmatter.get("governance_strategy_action") or "") or infer_governance_action(
        frontmatter=frontmatter,
        path=path,
        root=root,
    )
    strategy = strategy_for_action(policy, action)
    if not strategy:
        strategy = {
            "weight": _float(frontmatter.get("governance_strategy_weight"), 1.0),
            "execution_mode": str(frontmatter.get("governance_execution_mode") or "normal"),
            "requires_more_evidence": _bool(frontmatter.get("governance_requires_more_evidence")),
            "reason": str(frontmatter.get("governance_strategy_reason") or "default_strategy"),
        }
    return {
        "action": action,
        "weight": max(0.25, min(1.8, _float(strategy.get("weight"), 1.0))),
        "execution_mode": str(strategy.get("execution_mode") or "normal"),
        "requires_more_evidence": bool(strategy.get("requires_more_evidence")),
        "reason": str(strategy.get("reason") or "strategy_policy"),
        "global_mode": str(policy.get("global_mode") or "normal") if isinstance(policy, dict) else "normal",
    }


def apply_strategy_to_score(score: float, strategy: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    weight = max(0.25, min(1.8, _float(strategy.get("weight"), 1.0)))
    mode = str(strategy.get("execution_mode") or "normal")
    adjusted = score * weight
    if mode == "manual_review":
        adjusted *= 0.72
    if bool(strategy.get("requires_more_evidence")):
        adjusted *= 0.82
    if str(strategy.get("global_mode") or "") == "cautious" and mode != "batch_ok":
        adjusted *= 0.92
    detail = {
        "strategy_action": strategy.get("action") or "",
        "strategy_weight": round(weight, 3),
        "execution_mode": mode,
        "requires_more_evidence": bool(strategy.get("requires_more_evidence")),
        "strategy_reason": strategy.get("reason") or "",
        "strategy_adjusted_score": round(adjusted, 4),
    }
    return adjusted, detail


def artifact_usage_metadata(frontmatter: dict[str, Any]) -> dict[str, Any]:
    use_count = int(_float(frontmatter.get("memory_use_count"), 0))
    success_count = int(_float(frontmatter.get("memory_success_count"), 0))
    failure_count = int(_float(frontmatter.get("memory_failure_count"), 0))
    success_rate = _float(frontmatter.get("memory_success_rate"), 0.0)
    if use_count and not success_rate:
        success_rate = round(success_count / max(1, success_count + failure_count), 3)
    return {
        "use_count": use_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": success_rate,
        "last_used_at": str(frontmatter.get("memory_last_used_at") or ""),
        "last_failure_reason": str(frontmatter.get("memory_last_failure_reason") or ""),
    }


def apply_usage_to_score(score: float, usage: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    use_count = int(usage.get("use_count") or 0)
    success_rate = _float(usage.get("success_rate"), 0.0)
    failure_count = int(usage.get("failure_count") or 0)
    multiplier = 1.0
    if use_count:
        multiplier += min(0.24, use_count * 0.03)
        if success_rate >= 0.8:
            multiplier += 0.18
        elif success_rate and success_rate < 0.5:
            multiplier -= 0.18
    if failure_count >= 2:
        multiplier -= min(0.24, failure_count * 0.06)
    multiplier = max(0.45, min(1.45, multiplier))
    adjusted = score * multiplier
    return adjusted, {
        "artifact_use_count": use_count,
        "artifact_success_rate": round(success_rate, 3),
        "artifact_failure_count": failure_count,
        "artifact_usage_multiplier": round(multiplier, 3),
        "artifact_last_failure_reason": usage.get("last_failure_reason") or "",
    }


def strategy_for_action(policy: dict[str, Any], action: str) -> dict[str, Any]:
    if not action or not isinstance(policy, dict):
        return {}
    action_policy = policy.get("action_policy")
    if not isinstance(action_policy, dict):
        return {}
    strategy = action_policy.get(action)
    return strategy if isinstance(strategy, dict) else {}


def infer_governance_action(*, frontmatter: dict[str, Any], path: Path, root: Path) -> str:
    explicit = str(frontmatter.get("governance_strategy_action") or "")
    if explicit:
        return explicit
    page_type = str(frontmatter.get("type") or "").lower()
    rel = str(path.relative_to(root)).replace("\\", "/").lower() if _is_relative_to(path, root) else str(path).lower()
    if page_type in {"recovery_playbook", "failure_playbook"} or "/playbooks/recovery/" in f"/{rel}":
        return "generate_failure_playbook"
    if page_type == "confirmed" or "/concepts/confirmed/" in f"/{rel}":
        return "confirm_pending"
    if str(frontmatter.get("verification_status") or "") == "revalidated_by_governance":
        return "revalidate_stale"
    return ""


def parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    out: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        raw = raw.strip()
        try:
            out[key.strip()] = json.loads(raw)
        except Exception:
            out[key.strip()] = raw.strip('"')
    return out


def upsert_frontmatter_field(text: str, key: str, value: Any) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    frontmatter = text[:end]
    line = f"{key}: {_yaml_value(value)}"
    pattern = re.compile(rf"^{re.escape(key)}:.*$", re.MULTILINE)
    if pattern.search(frontmatter):
        frontmatter = pattern.sub(line, frontmatter)
    else:
        frontmatter += "\n" + line
    return frontmatter + text[end:]


def strategy_preview(strategy: dict[str, Any]) -> str:
    return (
        f"strategy_action={strategy.get('action') or ''}; "
        f"strategy_weight={float(strategy.get('weight') or 1.0):.2f}; "
        f"governance_execution_mode={strategy.get('execution_mode') or 'normal'}; "
        f"requires_more_evidence={str(bool(strategy.get('requires_more_evidence'))).lower()}; "
        f"strategy_reason={strategy.get('reason') or 'default_strategy'}"
    )


def usage_preview(usage: dict[str, Any]) -> str:
    return (
        f"artifact_use_count={int(usage.get('use_count') or 0)}; "
        f"artifact_success_rate={float(usage.get('success_rate') or 0.0):.3f}; "
        f"artifact_failure_count={int(usage.get('failure_count') or 0)}; "
        f"artifact_last_failure_reason={usage.get('last_failure_reason') or ''}"
    )


def _write_artifact_usage_index(root: Path) -> None:
    rows: list[dict[str, Any]] = []
    for path in _artifact_paths(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        frontmatter = parse_frontmatter(text)
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "id": str(frontmatter.get("id") or path.stem),
                "type": str(frontmatter.get("type") or path.parent.name),
                "summary": str(frontmatter.get("summary") or path.stem),
                "memory_use_count": int(_float(frontmatter.get("memory_use_count"), 0)),
                "memory_success_count": int(_float(frontmatter.get("memory_success_count"), 0)),
                "memory_failure_count": int(_float(frontmatter.get("memory_failure_count"), 0)),
                "memory_success_rate": _float(frontmatter.get("memory_success_rate"), 0.0),
                "memory_last_used_at": str(frontmatter.get("memory_last_used_at") or ""),
                "memory_last_failure_reason": str(frontmatter.get("memory_last_failure_reason") or ""),
            }
        )
    rows.sort(
        key=lambda row: (
            -int(row.get("memory_use_count") or 0),
            -float(row.get("memory_success_rate") or 0),
            str(row.get("path") or ""),
        )
    )
    index_path = root / "indexes" / "artifact_usage.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps({"schema_version": 1, "updated_at": _iso_now(), "artifacts": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _artifact_paths(root: Path) -> list[Path]:
    paths: dict[str, Path] = {}
    for pattern in ("concepts/*.md", "concepts/**/*.md", "playbooks/*.md", "playbooks/**/*.md"):
        for path in root.glob(pattern):
            if path.name != "README.md":
                paths[str(path)] = path
    return sorted(paths.values())


def _yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
