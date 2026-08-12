import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError


_HERE = os.path.dirname(os.path.abspath(__file__))
_ADAPTER_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _ADAPTER_DIR)

import server  # noqa: E402
from drawing_planner.capability_registry import CapabilityAssessment  # noqa: E402
from drawing_planner.planning_models import PlanningValidation  # noqa: E402
from drawing_planner.planning_models import (  # noqa: E402
    PlanningRequest,
    PublishedPlan,
    ValidationIssue,
    canonical_json_sha256,
)


def _passing_validation():
    return PlanningValidation(
        integrity="pass",
        schema_check="pass",
        semantics="pass",
        coverage="pass",
        layout="pass",
    )


def _supported():
    return CapabilityAssessment(
        status="supported",
        manifest_version="0.7.0",
        unsupported_capabilities=(),
    )


def _completed(state=0):
    return {
        "status": "COMPLETED",
        "verified": True,
        "stateVersion": state,
        "result_geometry": {"verified": True},
    }


def _request(tmp_path):
    return PlanningRequest(
        handoff_manifest_path=str(tmp_path / "drawing-planning-handoff.json"),
        handoff_manifest_sha256="a" * 64,
        publication_directory=str(tmp_path),
    )


def test_publish_manual_candidate_revalidates_and_publishes_supported_plan(tmp_path):
    plan = {"protocol_id": "solidworks-view-plan", "plan_id": "VP-manual-1"}
    request = _request(tmp_path)
    published = PublishedPlan(
        plan_id="VP-manual-1",
        path=str(tmp_path / "view_plan.json"),
        sha256="b" * 64,
    )
    with patch.object(
        server,
        "_validate_view_plan",
        return_value=(plan, _passing_validation(), _supported()),
    ), patch.object(server, "PlanStore") as store_type:
        store_type.return_value.publish.return_value = published
        result = json.loads(
            server.publish_validated_part_drawing_view_plan(plan, request)
        )

    store_type.return_value.publish.assert_called_once_with(plan, str(tmp_path))
    assert result["ok"] is True
    assert result["status"] == "published"
    assert result["generation_mode"] == "manual_skill"
    assert result["execution_readiness"] == "supported"
    assert result["plan"]["sha256"] == "b" * 64
    assert result["audit"] == {
        "request_sha256": canonical_json_sha256(
            request.model_dump(mode="json"), "planning request"
        ),
        "candidate_sha256": canonical_json_sha256(plan, "model plan candidate"),
        "capability_manifest_version": "0.7.0",
    }


def test_publish_manual_candidate_preserves_capability_blocked_result(tmp_path):
    plan = {"protocol_id": "solidworks-view-plan", "plan_id": "VP-manual-2"}
    request = _request(tmp_path)
    blocked = CapabilityAssessment(
        status="capability_blocked",
        manifest_version="0.7.0",
        unsupported_capabilities=("view_type.auxiliary_view.hidden_arrow",),
    )
    published = PublishedPlan(
        plan_id="VP-manual-2",
        path=str(tmp_path / "view_plan.json"),
        sha256="c" * 64,
    )
    with patch.object(
        server,
        "_validate_view_plan",
        return_value=(plan, _passing_validation(), blocked),
    ), patch.object(server, "PlanStore") as store_type:
        store_type.return_value.publish.return_value = published
        result = json.loads(
            server.publish_validated_part_drawing_view_plan(plan, request)
        )

    assert result["ok"] is True
    assert result["status"] == "published"
    assert result["execution_readiness"] == "capability_blocked"
    assert result["unsupported_capabilities"] == [
        "view_type.auxiliary_view.hidden_arrow"
    ]
    store_type.return_value.publish.assert_called_once()


def test_publish_manual_candidate_rejects_before_publication(tmp_path):
    plan = {"protocol_id": "solidworks-view-plan", "plan_id": "VP-invalid"}
    request = _request(tmp_path)
    rejected = PlanningValidation(
        integrity="pass",
        schema_check="fail",
        semantics="not_run",
        coverage="not_run",
        layout="not_run",
        issues=(
            ValidationIssue(
                code="VP-SCHEMA-TEST",
                gate="schema",
                message="test rejection",
                json_pointer="/views",
            ),
        ),
    )
    with patch.object(
        server,
        "_validate_view_plan",
        return_value=(plan, rejected, None),
    ), patch.object(server, "PlanStore") as store_type:
        result = json.loads(
            server.publish_validated_part_drawing_view_plan(plan, request)
        )

    assert result["ok"] is False
    assert result["status"] == "rejected"
    assert result["execution_readiness"] == "not_assessed"
    assert result["plan"] is None
    assert result["audit"]["candidate_sha256"] == canonical_json_sha256(
        plan, "model plan candidate"
    )
    store_type.return_value.publish.assert_not_called()


def test_publish_manual_candidate_refuses_existing_frozen_plan(tmp_path):
    plan = {"protocol_id": "solidworks-view-plan", "plan_id": "VP-existing"}
    request = _request(tmp_path)
    with patch.object(
        server,
        "_validate_view_plan",
        return_value=(plan, _passing_validation(), _supported()),
    ), patch.object(server, "PlanStore") as store_type:
        store_type.return_value.publish.side_effect = FileExistsError(
            "refusing to overwrite frozen plan"
        )
        with pytest.raises(ToolError, match="VIEW_PLAN_ALREADY_EXISTS"):
            server.publish_validated_part_drawing_view_plan(plan, request)


def test_validate_viewplan_calls_only_the_private_com_free_entry():
    plan = {"protocol_id": "solidworks-view-plan"}
    request = SimpleNamespace()
    with patch.object(
        server,
        "_validate_view_plan",
        return_value=(plan, _passing_validation(), _supported()),
    ), patch.object(server, "_execute", return_value=_completed()) as execute:
        result = json.loads(server.validate_part_drawing_view_plan(plan, request))

    assert result["ok"] is True
    assert result["execution_readiness"] == "supported"
    execute.assert_called_once_with(
        "validate_frozen_part_drawing_view_plan",
        {"plan": plan},
        mutating=False,
    )


def test_create_viewplan_uses_native_mutating_transaction(tmp_path):
    plan = {"protocol_id": "solidworks-view-plan"}
    request = SimpleNamespace()
    output = tmp_path / "created.SLDDRW"
    with patch.object(
        server,
        "_validate_view_plan",
        return_value=(plan, _passing_validation(), _supported()),
    ), patch.object(server, "_execute", return_value=_completed(state=1)) as execute:
        result = json.loads(
            server.create_part_drawing_from_view_plan(plan, request, str(output))
        )

    assert result["ok"] is True
    execute.assert_called_once_with(
        "execute_part_drawing_view_plan",
        {"plan": plan, "output_path": str(output.resolve())},
        mutating=True,
    )


def test_create_viewplan_fails_before_executor_when_capability_is_blocked(tmp_path):
    blocked = CapabilityAssessment(
        status="capability_blocked",
        manifest_version="0.7.0",
        unsupported_capabilities=("view_type.full_section",),
    )
    with patch.object(
        server,
        "_validate_view_plan",
        return_value=({}, _passing_validation(), blocked),
    ), patch.object(server, "_execute") as execute:
        with pytest.raises(ToolError, match="VIEW_PLAN_CAPABILITY_BLOCKED"):
            server.create_part_drawing_from_view_plan(
                {}, SimpleNamespace(), str(tmp_path / "blocked.SLDDRW")
            )
    execute.assert_not_called()


def test_verify_viewplan_uses_read_only_committed_entry(tmp_path):
    plan = {"protocol_id": "solidworks-view-plan"}
    output = tmp_path / "created.SLDDRW"
    output.write_bytes(b"drawing")
    (tmp_path / "created.SLDDRW.verification.json").write_text(
        "{}", encoding="utf-8"
    )
    with patch.object(
        server,
        "_validate_view_plan",
        return_value=(plan, _passing_validation(), _supported()),
    ), patch.object(server, "_execute", return_value=_completed()) as execute:
        result = json.loads(
            server.verify_part_drawing_view_plan(
                plan, SimpleNamespace(), str(output)
            )
        )

    assert result["ok"] is True
    execute.assert_called_once_with(
        "verify_committed_part_drawing_view_plan",
        {"plan": plan, "output_path": str(output.resolve())},
        mutating=False,
    )
