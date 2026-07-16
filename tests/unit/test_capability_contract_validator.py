from l3_node.cognitive_kernel.capability_contract_validator import validate_capability_contract


def _valid_manifest() -> dict:
    return {
        "id": "com.jachin.skill.demo",
        "version": "1.0.0",
        "item_type": "skill",
        "inputs": ["task"],
        "examples": ["demo task"],
        "metadata": {
            "verification": [{"method": "evidence"}],
            "risk": "medium",
        },
        "decomposition": {
            "nodes": [
                {
                    "id": "open",
                    "goal": "open the target app",
                    "role_agent": "AppControlExecutorAgent",
                    "tool": "windows_app_open",
                    "verification_criteria": ["foreground window title matches"],
                }
            ]
        },
        "recovery_playbook": {
            "targets": [
                {
                    "role_agent": "AppControlExecutorAgent",
                    "tools": ["windows_app_open"],
                    "max_attempts": 2,
                    "steps": [
                        {
                            "strategy": "retry_with_detected_path",
                            "tool": "$same",
                            "priority": 10,
                        }
                    ],
                }
            ]
        },
    }


def test_capability_contract_accepts_complete_manifest():
    result = validate_capability_contract(_valid_manifest())

    assert not result.errors
    assert result.quality_score >= 0.72
    assert result.production_ready is True


def test_capability_contract_rejects_incomplete_decomposition_node():
    manifest = _valid_manifest()
    manifest["decomposition"]["nodes"][0].pop("verification_criteria")
    manifest["decomposition"]["nodes"][0]["tool"] = ""

    result = validate_capability_contract(manifest)

    messages = [issue.message for issue in result.errors]
    assert any("tool must be a non-empty string" in message for message in messages)
    assert any("verification_criteria must be a non-empty array" in message for message in messages)
    assert result.production_ready is False


def test_capability_contract_rejects_missing_dependencies_when_catalog_known():
    manifest = _valid_manifest()
    manifest["required_mcps"] = ["com.jachin.mcp.missing"]
    manifest["required_models"] = ["com.jachin.model.missing"]

    result = validate_capability_contract(
        manifest,
        available_capability_ids={"com.jachin.mcp.other"},
        available_model_ids={"com.jachin.model.other"},
    )

    codes = {issue.code for issue in result.errors}
    assert "required_mcp_not_installable" in codes
    assert "required_model_not_installable" in codes


def test_capability_contract_warns_on_low_quality_profile():
    result = validate_capability_contract({"id": "com.jachin.skill.tiny", "version": "1.0.0"})

    codes = {issue.code for issue in result.warnings}
    assert "capability_profile_low_quality" in codes
    assert result.production_ready is False
