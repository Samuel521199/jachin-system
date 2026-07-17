import json
from pathlib import Path

from l3_node.cognitive_kernel.capability_recovery_registry import CapabilityRecoveryRegistry
from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, VerificationReport, WorkOrder
from l3_node.cognitive_kernel.recovery_playbook_schema import validate_recovery_playbook_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]

CRITICAL_CAPABILITY_MANIFESTS = [
    "l3_client/local_mcps/local_translate_mcp/plugin.json",
    "l3_client/local_mcps/english_tutor_mcp/plugin.json",
    "l3_client/local_mcps/english_example_generator_mcp/plugin.json",
    "skills_repo/com.jachin.skill.english-learning-assistant/plugin.json",
    "skills_repo/pmo-copilot/plugin.json",
    "skills_repo/l1_upload_stubs/com.jachin.mcp.pmo-runtime/plugin.json",
    "skills_repo/l1_upload_stubs/com.jachin.mcp.l3.atom-lark-notifier/plugin.json",
    "skills_repo/l1_upload_stubs/com.jachin.mcp.stub.windows.uia/plugin.json",
    "skills_repo/l1_upload_stubs/com.jachin.mcp.stub.tavily.search/plugin.json",
    "skills_repo/l1_upload_stubs/com.jachin.mcp.stub.official.fetch/plugin.json",
    "skills_repo/plugin/com.jachin.mcp.tavily.search/plugin.json",
    "skills_repo/plugin/com.jachin.mcp.officialfetch/plugin.json",
]


def _load_manifest(rel_path: str) -> dict:
    return json.loads((REPO_ROOT / rel_path).read_text(encoding="utf-8-sig"))


def test_critical_skill_and_mcp_manifests_declare_valid_recovery_playbooks():
    missing = []
    invalid = {}
    for rel_path in CRITICAL_CAPABILITY_MANIFESTS:
        manifest = _load_manifest(rel_path)
        if not isinstance(manifest.get("recovery_playbook"), dict):
            missing.append(rel_path)
            continue
        errors = validate_recovery_playbook_manifest(manifest)
        if errors:
            invalid[rel_path] = errors

    assert missing == []
    assert invalid == {}


def test_web_research_manifest_paths_are_consumable_by_recovery_registry():
    manifest = _load_manifest("skills_repo/l1_upload_stubs/com.jachin.mcp.stub.official.fetch/plugin.json")
    registry = CapabilityRecoveryRegistry(manifests=[manifest])
    contract = DecisionContract(
        decision_id="decision-fetch-recovery-coverage",
        turn_id="turn-fetch-recovery-coverage",
        task_type="web_research_delivery",
        goal="fetch readable AI news sources",
        selected_roles=["BrowserExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:fetch", "mcp:tavily_search"]),
        execution_allowed=True,
    )
    work_order = WorkOrder(
        work_order_id="work-fetch-recovery-coverage",
        decision_id=contract.decision_id,
        role_agent="BrowserExecutorAgent",
        task="web_research_delivery",
        inputs={"tool": "mcp:fetch", "work_order_input": '{"url":"https://example.com/blocked"}'},
        tool_policy=contract.tool_policy,
    )
    verification = VerificationReport(
        verification_id="verify-fetch-recovery-coverage",
        work_order_id=work_order.work_order_id,
        ok=False,
        failure_reason="fetch_access_or_bot_wall",
    )

    candidate = registry.select_next(
        contract=contract,
        failed_work_order=work_order,
        verification=verification,
        attempt_records=[],
    )

    assert candidate is not None
    assert candidate.strategy == "mark_source_blocked_and_search_alternative"
    assert candidate.tool == "mcp:tavily_search"


def test_english_manifest_prefers_asset_status_before_model_retry():
    manifest = _load_manifest("skills_repo/com.jachin.skill.english-learning-assistant/plugin.json")
    registry = CapabilityRecoveryRegistry(manifests=[manifest])
    contract = DecisionContract(
        decision_id="decision-english-recovery-coverage",
        turn_id="turn-english-recovery-coverage",
        task_type="english_learning",
        goal="show a polished word card",
        selected_roles=["ToolExecutionAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["english_example_pack_status", "local_translate_warmup", "english_generate_example_card"]),
        execution_allowed=True,
    )
    work_order = WorkOrder(
        work_order_id="work-english-recovery-coverage",
        decision_id=contract.decision_id,
        role_agent="ToolExecutionAgent",
        task="english_learning",
        inputs={"tool": "english_generate_example_card", "work_order_input": '{"word":"office"}'},
        tool_policy=contract.tool_policy,
    )
    verification = VerificationReport(
        verification_id="verify-english-recovery-coverage",
        work_order_id=work_order.work_order_id,
        ok=False,
        failure_reason="example_missing",
    )

    candidate = registry.select_next(
        contract=contract,
        failed_work_order=work_order,
        verification=verification,
        attempt_records=[],
    )

    assert candidate is not None
    assert candidate.strategy == "check_cached_vocab_assets"
    assert candidate.tool == "english_example_pack_status"
