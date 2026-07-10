"""Schema checks for capability recovery playbooks.

The recovery planner is manifest-driven, so malformed playbooks must be
rejected before a package reaches runtime. Keep this validator dependency-free
so it can be used by installers, tests, and registry defensive loading.
"""

from __future__ import annotations

from typing import Any


def validate_recovery_playbook_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return schema errors for the manifest's optional recovery_playbook."""

    playbook = manifest.get("recovery_playbook")
    if playbook is None:
        return []
    errors: list[str] = []
    if not isinstance(playbook, dict):
        return ["recovery_playbook must be an object"]

    targets = playbook.get("targets")
    if not isinstance(targets, list) or not targets:
        return ["recovery_playbook.targets must be a non-empty array"]

    for target_index, target in enumerate(targets):
        prefix = f"recovery_playbook.targets[{target_index}]"
        if not isinstance(target, dict):
            errors.append(f"{prefix} must be an object")
            continue

        role_agent = target.get("role_agent") or target.get("role")
        if not _non_empty_string(role_agent):
            errors.append(f"{prefix}.role_agent must be a non-empty string")

        tools = target.get("tools", target.get("tool_patterns"))
        if tools is not None:
            if not isinstance(tools, list) or not tools:
                errors.append(f"{prefix}.tools must be a non-empty string array when provided")
            else:
                for tool_index, tool in enumerate(tools):
                    if not _non_empty_string(tool):
                        errors.append(f"{prefix}.tools[{tool_index}] must be a non-empty string")

        max_attempts = target.get("max_attempts")
        if max_attempts is not None and not _int_in_range(max_attempts, 1, 8):
            errors.append(f"{prefix}.max_attempts must be an integer from 1 to 8")

        steps = target.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append(f"{prefix}.steps must be a non-empty array")
            continue

        for step_index, step in enumerate(steps):
            step_prefix = f"{prefix}.steps[{step_index}]"
            if not isinstance(step, dict):
                errors.append(f"{step_prefix} must be an object")
                continue
            if not _non_empty_string(step.get("strategy")):
                errors.append(f"{step_prefix}.strategy must be a non-empty string")
            if not _non_empty_string(step.get("tool")):
                errors.append(f"{step_prefix}.tool must be a non-empty string, use '$same' to retry the same tool")
            priority = step.get("priority")
            if priority is not None and not _int_in_range(priority, 0, 1000):
                errors.append(f"{step_prefix}.priority must be an integer from 0 to 1000")
            rationale = step.get("rationale")
            if rationale is not None and not isinstance(rationale, str):
                errors.append(f"{step_prefix}.rationale must be a string when provided")
            if "action_patch" in step and "action_template" in step:
                errors.append(f"{step_prefix} cannot define both action_patch and action_template")
            for key in ("action_patch", "action_template"):
                if key in step and not isinstance(step.get(key), dict):
                    errors.append(f"{step_prefix}.{key} must be an object when provided")
            _validate_when(step_prefix, step.get("when"), errors)

    return errors


def _validate_when(step_prefix: str, when: Any, errors: list[str]) -> None:
    if when is None:
        return
    if not isinstance(when, dict):
        errors.append(f"{step_prefix}.when must be an object when provided")
        return
    for key in ("failure_any", "failure_all", "tool_not_contains"):
        value = when.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(f"{step_prefix}.when.{key} must be a string array")
            continue
        for index, item in enumerate(value):
            if not _non_empty_string(item):
                errors.append(f"{step_prefix}.when.{key}[{index}] must be a non-empty string")
    after_attempt = when.get("after_attempt")
    if after_attempt is not None and not _int_in_range(after_attempt, 1, 8):
        errors.append(f"{step_prefix}.when.after_attempt must be an integer from 1 to 8")


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _int_in_range(value: Any, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum
