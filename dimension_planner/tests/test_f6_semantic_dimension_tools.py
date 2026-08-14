from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROMPT_PACK = ROOT / "dimension_planner/prompt_packs/native-v1"
EXECUTION_ROOT = ROOT / "solidworks-execution/SolidworksExecution"


def test_native_prompt_pack_has_a_reproducible_frozen_producer_ruleset():
    manifest = json.loads((PROMPT_PACK / "manifest.json").read_text(encoding="utf-8"))
    payload = (
        (PROMPT_PACK / "system.md").read_bytes()
        + b"\n---\n"
        + (PROMPT_PACK / "task.md").read_bytes()
    )
    assert manifest["pack_id"] == "dimension-native-v1"
    assert manifest["protocol_id"] == "solidworks-dimension-plan"
    assert manifest["schema_version"] == "1.0"
    assert manifest["producer"] == {
        "name": "q3ds-dimension-skill",
        "version": "1.0.0",
        "ruleset_id": "dimension-native-v1",
        "ruleset_sha256": hashlib.sha256(payload).hexdigest(),
    }


def test_f6_server_wraps_only_the_three_private_dimension_executor_stages():
    source = (ROOT / "adapters/claude/server.py").read_text(encoding="utf-8")
    assert '"validate_frozen_part_drawing_dimension_plan"' in source
    assert '"execute_part_drawing_dimension_plan"' in source
    assert '"verify_committed_part_drawing_dimension_plan"' in source
    assert "_dimension_plan_binding(normalized, request)" in source
    assert '"dimension_plan.json"' in source
    assert '".dimension-verification.json"' in source


def test_f6_independent_verifier_is_read_only_and_rechecks_frozen_evidence():
    preflight = (
        EXECUTION_ROOT / "Contracts/DimensionPlanVerificationPreflight.cs"
    ).read_text(encoding="utf-8-sig")
    verifier = (
        EXECUTION_ROOT / "Services/DimensionPlanDrawingVerifier.cs"
    ).read_text(encoding="utf-8-sig")
    project = (EXECUTION_ROOT / "SolidworksExecution.csproj").read_text(
        encoding="utf-8-sig"
    )
    service = (
        EXECUTION_ROOT / "Services/SolidWorksService.DimensionPlanExecution.cs"
    ).read_text(encoding="utf-8-sig")

    assert "DimensionPlanTransactionPreflight().TryValidate" in preflight
    assert 'report["frozen_inputs"]' in preflight
    assert 'report["dimension_handles"]' in preflight
    assert 'reopen["dimensions"]' in preflight
    assert "DimensionPlanContractValidator.FileSha256(outputPath)" in preflight
    assert "DIMENSION_OUTPUT_ALIASES_INPUT" in preflight
    assert 'row.Value<string>("selection_name") != handle' in preflight
    assert "swOpenDocOptions_ReadOnly" in verifier
    assert "TryVerifyPersisted" in verifier
    assert "DIMENSION_OUTPUT_CHANGED_DURING_VERIFICATION" in verifier
    assert ".Save3(" not in verifier
    assert ".SaveAs" not in verifier
    assert "DimensionPlanVerificationPreflight.cs" in project
    assert "DimensionPlanDrawingVerifier.cs" in project
    assert "VerifyCommittedPartDrawingDimensionPlan" in service
    assert "GetCurrentStateVersion() + 1" not in service[
        service.index("VerifyCommittedPartDrawingDimensionPlan") :
    ]


def test_f6_skill_forbids_plan_repair_and_preserves_one_candidate_request_chain():
    skill = (
        ROOT / ".codex/skills/solidworks-dimension-drawing/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "恰好一个完整 DimensionPlan 1.0 候选" in skill
    assert "后续每一步原样复用，不能重新构造" in skill
    assert "发布后禁止修改候选、请求或生产者信息" in skill
    assert "capability_blocked" in skill
    assert "不要调用私有 executor 动词" in skill
    assert "raw HTTP" not in skill
    assert "原始 HTTP" in skill


def test_f7_qualification_is_matrix_bound_without_weakening_production_gate():
    server = (ROOT / "adapters/claude/server.py").read_text(encoding="utf-8")
    service = (
        EXECUTION_ROOT / "Services/SolidWorksService.DimensionPlanExecution.cs"
    ).read_text(encoding="utf-8-sig")
    capability = (
        EXECUTION_ROOT / "Contracts/DimensionPlanCapabilityPreflight.cs"
    ).read_text(encoding="utf-8-sig")
    qualification = (
        EXECUTION_ROOT / "Contracts/DimensionPlanQualificationPreflight.cs"
    ).read_text(encoding="utf-8-sig")

    assert '"qualify_part_drawing_dimension_plan"' in server
    assert '"verify_qualified_part_drawing_dimension_plan"' in server
    assert "validate_f7_matrix_request" in server
    assert "DIMENSION_F7_CASE_BINDING_MISMATCH" in server
    assert "TryValidateQualification" in service
    assert "TryValidate(plan" in service
    assert 'status == "unsupported"' in capability
    assert 'status != "supported"' in capability
    assert "capability_registry_promoted" in service
    assert "matrix_request_sha256" in qualification
    assert "DIMENSION_F7_MATRIX_HASH_MISMATCH" in qualification
