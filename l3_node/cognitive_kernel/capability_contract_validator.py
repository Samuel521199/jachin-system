"""Capability contract validation for Skill/MCP manifests.

This module is the authoring guard for capability packages.  Recovery
playbooks, decomposition nodes, dependencies, and capability intelligence
quality are checked in one place so publish, install, and startup scan paths
all judge manifests with the same contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable

from .capability_intelligence import build_capability_intelligence
from .recovery_playbook_schema import validate_recovery_playbook_manifest


QUALITY_THRESHOLD = 0.72


@dataclass(slots=True)
class CapabilityContractIssue:
    severity: str
    code: str
    message: str
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CapabilityContractValidationResult:
    capability_id: str
    quality_score: float
    production_ready: bool
    issues: list[CapabilityContractIssue]

    @property
    def errors(self) -> list[CapabilityContractIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[CapabilityContractIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "quality_score": self.quality_score,
            "production_ready": self.production_ready,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_capability_contract(
    manifest: dict[str, Any],
    *,
    available_capability_ids: Iterable[str] | None = None,
    available_model_ids: Iterable[str] | None = None,
    quality_threshold: float = QUALITY_THRESHOLD,
) -> CapabilityContractValidationResult:
    """Validate the production contract exposed by a Skill/MCP manifest."""

    issues: list[CapabilityContractIssue] = []
    capability_id = _first_non_empty_string(manifest, "id", "plugin_id", "name")
    if not capability_id:
        _issue(issues, "error", "missing_id", "manifest id/plugin_id must be a non-empty string", "id")

    version = _first_non_empty_string(manifest, "version")
    if not version:
        _issue(issues, "error", "missing_version", "manifest version must be a non-empty string", "version")
    elif not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        _issue(issues, "error", "invalid_version", f"manifest version is not semver: {version}", "version")

    _validate_decomposition(manifest, issues)
    for error in validate_recovery_playbook_manifest(manifest):
        _issue(issues, "error", "invalid_recovery_playbook", error, "recovery_playbook")

    available_capabilities = {str(v).strip() for v in (available_capability_ids or []) if str(v).strip()}
    available_models = {str(v).strip() for v in (available_model_ids or []) if str(v).strip()}
    _validate_dependencies(
        manifest,
        issues,
        available_capability_ids=available_capabilities or None,
        available_model_ids=available_models or None,
    )

    profile = build_capability_intelligence(_descriptor_from_manifest(manifest))
    if profile.quality_score < quality_threshold:
        missing = ", ".join(profile.missing_metadata) or "metadata"
        _issue(
            issues,
            "warning",
            "capability_profile_low_quality",
            f"capability profile quality {profile.quality_score:.2f} is below {quality_threshold:.2f}; missing {missing}",
            "capability_profile",
        )

    production_ready = not any(issue.severity == "error" for issue in issues) and profile.quality_score >= quality_threshold
    return CapabilityContractValidationResult(
        capability_id=capability_id,
        quality_score=profile.quality_score,
        production_ready=production_ready,
        issues=issues,
    )


def contract_error_messages(result: CapabilityContractValidationResult) -> list[str]:
    return [_format_issue(issue) for issue in result.errors]


def contract_warning_messages(result: CapabilityContractValidationResult) -> list[str]:
    return [_format_issue(issue) for issue in result.warnings]


def _validate_decomposition(manifest: dict[str, Any], issues: list[CapabilityContractIssue]) -> None:
    decomposition = manifest.get("decomposition") or _metadata(manifest).get("decomposition")
    if decomposition is None:
        return
    if not isinstance(decomposition, dict):
        _issue(issues, "error", "invalid_decomposition", "decomposition must be an object", "decomposition")
        return
    nodes = decomposition.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        _issue(issues, "error", "invalid_decomposition_nodes", "decomposition.nodes must be a non-empty array", "decomposition.nodes")
        return

    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        path = f"decomposition.nodes[{index}]"
        if not isinstance(node, dict):
            _issue(issues, "error", "invalid_decomposition_node", f"{path} must be an object", path)
            continue
        node_id = _node_id(node, index)
        node_ids.add(node_id)
        for key in ("goal", "role_agent", "tool"):
            if not _non_empty_string(node.get(key)):
                _issue(issues, "error", f"missing_decomposition_{key}", f"{path}.{key} must be a non-empty string", f"{path}.{key}")
        verification = node.get("verification_criteria") or node.get("verification") or node.get("expected_evidence")
        if not _non_empty_list(verification):
            _issue(
                issues,
                "error",
                "missing_decomposition_verification",
                f"{path}.verification_criteria must be a non-empty array",
                f"{path}.verification_criteria",
            )

    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for dep in _string_list(node.get("depends_on")):
            if dep not in node_ids:
                _issue(
                    issues,
                    "error",
                    "unknown_decomposition_dependency",
                    f"decomposition.nodes[{index}].depends_on references unknown node: {dep}",
                    f"decomposition.nodes[{index}].depends_on",
                )


def _validate_dependencies(
    manifest: dict[str, Any],
    issues: list[CapabilityContractIssue],
    *,
    available_capability_ids: set[str] | None,
    available_model_ids: set[str] | None,
) -> None:
    required_mcps = _dependency_ids(manifest, "required_mcps")
    required_models = _dependency_ids(manifest, "required_models")
    for dep in required_mcps:
        if available_capability_ids is not None and dep not in available_capability_ids:
            _issue(
                issues,
                "error",
                "required_mcp_not_installable",
                f"required MCP is not available in current catalog/source: {dep}",
                "required_mcps",
            )
    for dep in required_models:
        if available_model_ids is not None and dep not in available_model_ids:
            _issue(
                issues,
                "error",
                "required_model_not_installable",
                f"required model is not available in current catalog/source: {dep}",
                "required_models",
            )


def _descriptor_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(_metadata(manifest))
    for key in ("preconditions", "required_state", "verification", "verification_methods", "required_mcps", "required_models", "recovery_playbook", "decomposition"):
        if key in manifest and key not in metadata:
            metadata[key] = manifest[key]
    actions = manifest.get("actions") or metadata.get("actions") or []
    examples = manifest.get("examples") or metadata.get("examples") or []
    risk = manifest.get("risk") or metadata.get("risk") or ("external_effect" if _looks_side_effectful(actions) else "medium")
    return {
        "id": _first_non_empty_string(manifest, "id", "plugin_id", "name"),
        "domain": manifest.get("domain") or metadata.get("domain") or _first_non_empty_string(manifest, "item_type", "type", "kind"),
        "task_type": manifest.get("task_type") or metadata.get("task_type") or _first_non_empty_string(manifest, "id", "plugin_id", "name"),
        "risk": risk,
        "inputs": manifest.get("inputs") or metadata.get("inputs") or _infer_inputs(manifest, metadata),
        "actions": actions,
        "objects": manifest.get("objects") or metadata.get("objects") or [],
        "examples": examples,
        "evidence": manifest.get("evidence") or metadata.get("evidence") or [],
        "metadata": metadata,
        "source": "manifest",
    }


def _infer_inputs(manifest: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    inputs: list[str] = []
    for dep in _dependency_ids(manifest, "required_mcps"):
        if dep:
            inputs.append("required_mcp")
    for dep in _dependency_ids(manifest, "required_models"):
        if dep:
            inputs.append("required_model")
    for node in ((manifest.get("decomposition") or metadata.get("decomposition") or {}).get("nodes") or []):
        if isinstance(node, dict):
            inputs.extend(_string_list(node.get("inputs")))
    return sorted(set(inputs)) or ["task"]


def _looks_side_effectful(actions: Any) -> bool:
    values = {value.lower() for value in _string_list(actions)}
    return bool(values.intersection({"send_message", "notify", "write", "delete", "move", "rename", "open_app", "close_app"}))


def _dependency_ids(manifest: dict[str, Any], key: str) -> list[str]:
    values = []
    values.extend(_string_list(manifest.get(key)))
    values.extend(_string_list(_metadata(manifest).get(key)))
    return sorted({value for value in values if value})


def _metadata(manifest: dict[str, Any]) -> dict[str, Any]:
    metadata = manifest.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _node_id(node: dict[str, Any], index: int) -> str:
    return str(node.get("id") or node.get("name") or index).strip()


def _first_non_empty_string(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if _non_empty_string(value):
            return str(value).strip()
    return ""


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
            elif isinstance(item, dict):
                item_id = item.get("id") or item.get("plugin_id") or item.get("model_id") or item.get("capability_id")
                if _non_empty_string(item_id):
                    result.append(str(item_id).strip())
        return result
    return []


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def _issue(issues: list[CapabilityContractIssue], severity: str, code: str, message: str, path: str = "") -> None:
    issues.append(CapabilityContractIssue(severity=severity, code=code, message=message, path=path))


def _format_issue(issue: CapabilityContractIssue) -> str:
    return f"{issue.path}: {issue.message}" if issue.path else issue.message
