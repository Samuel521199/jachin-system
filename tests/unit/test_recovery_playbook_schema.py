from l3_node.cognitive_kernel.capability_recovery_registry import CapabilityRecoveryRegistry
from l3_node.cognitive_kernel.recovery_playbook_schema import validate_recovery_playbook_manifest


def test_valid_recovery_playbook_schema_passes():
    manifest = {
        "id": "com.example.skill",
        "recovery_playbook": {
            "targets": [
                {
                    "role_agent": "AppControlExecutorAgent",
                    "tools": ["mcp:windows_open_app"],
                    "max_attempts": 5,
                    "steps": [
                        {
                            "strategy": "retry_same_path",
                            "tool": "$same",
                            "when": {"failure_any": ["timeout"], "after_attempt": 1},
                            "action_patch": {"timeout": 12},
                            "priority": 20,
                            "rationale": "retry transient desktop failure",
                        }
                    ],
                }
            ]
        },
    }

    assert validate_recovery_playbook_manifest(manifest) == []


def test_invalid_recovery_playbook_schema_reports_precise_errors():
    manifest = {
        "id": "com.example.bad-skill",
        "recovery_playbook": {
            "targets": [
                {
                    "role_agent": "",
                    "tools": "mcp:windows_open_app",
                    "max_attempts": 99,
                    "steps": [
                        {
                            "strategy": "",
                            "when": {"failure_any": "timeout", "after_attempt": 0},
                            "action_patch": {},
                            "action_template": {},
                        }
                    ],
                }
            ]
        },
    }

    errors = validate_recovery_playbook_manifest(manifest)

    assert "recovery_playbook.targets[0].role_agent must be a non-empty string" in errors
    assert "recovery_playbook.targets[0].tools must be a non-empty string array when provided" in errors
    assert "recovery_playbook.targets[0].max_attempts must be an integer from 1 to 8" in errors
    assert (
        "recovery_playbook.targets[0].steps[0].tool must be a non-empty string, use '$same' to retry the same tool"
        in errors
    )
    assert "recovery_playbook.targets[0].steps[0] cannot define both action_patch and action_template" in errors


def test_registry_ignores_invalid_recovery_playbook(tmp_path, monkeypatch):
    package_dir = tmp_path / "bad"
    package_dir.mkdir()
    (package_dir / "plugin.json").write_text(
        """
{
  "id": "bad",
  "recovery_playbook": {
    "targets": [
      {
        "role_agent": "BadOnlyAgent",
        "tools": "mcp:bad_tool",
        "max_attempts": 7,
        "steps": [{"strategy": "retry_same_path"}]
      }
    ]
  }
}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("JACHIN_RECOVERY_MANIFEST_ROOTS", str(tmp_path))
    registry = CapabilityRecoveryRegistry()

    assert registry.max_attempts_for(
        role_agent="BadOnlyAgent",
        tool="mcp:bad_tool",
        default=3,
    ) == 3
