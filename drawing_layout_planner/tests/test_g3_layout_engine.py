from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from drawing_planner.planning_models import canonical_json_sha256

from drawing_layout_planner.capability_registry import (
    LAYOUT_OPERATION_IDS,
    LAYOUT_SAFETY_IDS,
    DrawingLayoutCapabilityRegistry,
)
from drawing_layout_planner.engine_models import LayoutPlanningRequest
from drawing_layout_planner.handoff import dimension_invariant_sha256
from drawing_layout_planner.layout_solver import RepositoryLayoutSolver, load_ruleset
from drawing_layout_planner.planner_engine import DrawingLayoutPlannerEngine


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REQUEST_SCHEMA_PATH = (
    PACKAGE_ROOT / "contracts" / "drawing-layout-planning-request.schema.json"
)
RESULT_SCHEMA_PATH = (
    PACKAGE_ROOT / "contracts" / "drawing-layout-planning-result.schema.json"
)
PLAN_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "drawing-layout-plan.schema.json"
RULESET_PATH = PACKAGE_ROOT / "rulesets" / "deterministic-layout-v1.json"
G0_IDS = (
    "view_outline_bounds",
    "dimension_display_bounds",
    "note_text_bounds",
    "leader_bounds",
    "view_label_bounds",
    "section_symbol_bounds",
    "center_element_bounds",
    "sheet_border_bounds",
    "title_block_bounds",
    "rebuild_drift",
    "save_reopen_drift",
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _supported_registry(tmp_path: Path) -> tuple[DrawingLayoutCapabilityRegistry, Path]:
    boundary_path = tmp_path / "g0-current.json"
    _write_json(
        boundary_path,
        {
            "protocol_id": "solidworks-drawing-layout-executor-capabilities",
            "schema_version": "1.0",
            "registry_version": "1.0.0",
            "verification": "live_complete",
            "capabilities": [
                {"id": item, "status": "supported"} for item in G0_IDS
            ],
            "live_evidence": {"fixture": "contract-only"},
        },
    )
    evidence = "e" * 64
    entry = {
        "status": "supported",
        "reason": "Bound contract fixture represents completed live evidence.",
        "verification": "live",
        "evidence_sha256": evidence,
    }
    manifest_path = tmp_path / "g3-capabilities.json"
    _write_json(
        manifest_path,
        {
            "protocol_id": "solidworks-drawing-layout-plan-capabilities",
            "schema_version": "1.0",
            "registry_version": "1.0.0",
            "executor": "g3-contract-fixture",
            "executor_version": "1.0.0",
            "plan_protocol_id": "solidworks-drawing-layout-plan",
            "plan_schema_version": "1.0",
            "solidworks_target": "2025 SP5",
            "solidworks_revision": "33.5.0",
            "promotion_policy": "Contract fixture only; production registry remains unchanged.",
            "boundary_registry": {
                "protocol_id": "solidworks-drawing-layout-executor-capabilities",
                "registry_version": "1.0.0",
                "manifest_sha256": _sha(boundary_path),
            },
            "operations": {name: copy.deepcopy(entry) for name in LAYOUT_OPERATION_IDS},
            "safety_elements": {
                name: copy.deepcopy(entry) for name in LAYOUT_SAFETY_IDS
            },
        },
    )
    return (
        DrawingLayoutCapabilityRegistry.from_paths(manifest_path, boundary_path),
        boundary_path,
    )


def _fixture(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], DrawingLayoutCapabilityRegistry]:
    registry, boundary_path = _supported_registry(tmp_path)
    dimension_plan = tmp_path / "dimension_plan.json"
    _write_json(
        dimension_plan,
        {
            "protocol_id": "solidworks-dimension-plan",
            "schema_version": "1.0",
            "plan_id": "DP-G3",
            "configuration": "Default",
        },
    )
    dimension_handoff = tmp_path / "dimension-planning-handoff.json"
    _write_json(dimension_handoff, {"fixture": "immutable-dimension-handoff"})
    drawing = tmp_path / "dimensioned.SLDDRW"
    drawing.write_bytes(b"frozen-drawing")
    sidecar = tmp_path / "dimension-verification.json"
    _write_json(sidecar, {"verified": True})
    qualification = tmp_path / "qualification.json"
    _write_json(qualification, {"qualification_id": "G0-G3-FIXTURE"})
    dimensions = [
        {
            "dimension_id": "D-1",
            "value_si": 0.05,
            "model_persistent_references": ["persistent-ref"],
        }
    ]
    upstream = []
    for role, path in (
        ("dimension_plan", dimension_plan),
        ("dimensioned_drawing", drawing),
        ("dimension_verification_sidecar", sidecar),
        ("boundary_capability_manifest", boundary_path),
        ("boundary_qualification", qualification),
    ):
        upstream.append(
            {
                "role": role,
                "path": str(path.resolve()),
                "sha256_before": _sha(path),
                "sha256_after": _sha(path),
            }
        )
    handoff = {
        "protocol_id": "solidworks-drawing-layout-handoff",
        "schema_version": "1.0",
        "handoff_id": "DLH-G3",
        "created_at_utc": "2026-08-15T10:00:00+08:00",
        "status": "ready",
        "source_request_sha256": "a" * 64,
        "upstream_artifacts": upstream,
        "dimension_semantics": {
            "plan_id": "DP-G3",
            "planned_count": 1,
            "verified_actual_count": 1,
            "invariant_sha256": dimension_invariant_sha256(dimensions),
            "dimensions": dimensions,
        },
        "solidworks": {"revision": "33.5.0", "execution_mode": "live_read_only"},
        "sheet": {
            "name": "Sheet1",
            "bounds_m": [0.0, 0.0, 0.3, 0.2],
            "safe_bounds_m": [0.01, 0.01, 0.29, 0.19],
            "scale_numerator": 1,
            "scale_denominator": 1,
        },
        "objects": [
            {
                "id": "sheet-border",
                "category": "sheet_border_bounds",
                "view": "Sheet",
                "bounds": [0.0, 0.0, 0.3, 0.2],
                "source_api": "ISheet.GetSize",
                "exact": True,
                "collision_usable": True,
            },
            {
                "id": "view-front",
                "category": "view_outline_bounds",
                "view": "Front",
                "bounds": [0.04, 0.05, 0.09, 0.09],
                "source_api": "IView.GetOutline",
                "exact": True,
                "collision_usable": True,
            },
            {
                "id": "view-right",
                "category": "view_outline_bounds",
                "view": "Right",
                "bounds": [0.14, 0.05, 0.19, 0.09],
                "source_api": "IView.GetOutline",
                "exact": True,
                "collision_usable": True,
            },
            {
                "id": "view-aux",
                "category": "view_outline_bounds",
                "view": "Aux",
                "bounds": [0.21, 0.08, 0.25, 0.11],
                "source_api": "IView.GetOutline",
                "exact": True,
                "collision_usable": True,
            },
            {
                "id": "dimension-object",
                "category": "dimension_display_bounds",
                "view": "Front",
                "bounds": [0.04, 0.115, 0.065, 0.12],
                "source_api": "IAnnotation.GetDisplayData",
                "exact": True,
                "collision_usable": True,
                "dimension_id": "D-1",
                "attachment_point_sheet_m": [0.06, 0.09],
                "text_height_m": 0.0025,
            },
            {
                "id": "note-object",
                "category": "note_text_bounds",
                "view": "Right",
                "bounds": [0.14, 0.115, 0.16, 0.12],
                "source_api": "INote.GetExtent",
                "exact": True,
                "collision_usable": True,
                "text_height_m": 0.0025,
                "arrow_size_m": 0.0015,
            },
        ],
        "constraints": {
            "locked_zones": [
                {
                    "zone_id": "sheet-frame-left",
                    "kind": "sheet_frame",
                    "bounds_m": [0.0, 0.0, 0.005, 0.2],
                },
                {
                    "zone_id": "sheet-frame-right",
                    "kind": "sheet_frame",
                    "bounds_m": [0.295, 0.0, 0.3, 0.2],
                },
                {
                    "zone_id": "sheet-frame-bottom",
                    "kind": "sheet_frame",
                    "bounds_m": [0.005, 0.0, 0.295, 0.005],
                },
                {
                    "zone_id": "sheet-frame-top",
                    "kind": "sheet_frame",
                    "bounds_m": [0.005, 0.195, 0.295, 0.2],
                },
                {
                    "zone_id": "title-block",
                    "kind": "title_block",
                    "bounds_m": [0.22, 0.01, 0.29, 0.045],
                },
            ],
            "frozen_objects": ["sheet-border"],
            "view_constraints": [
                {
                    "view": "Front",
                    "position_sheet_m": [0.065, 0.07],
                    "position_locked": True,
                    "scale_numerator": 1,
                    "scale_denominator": 1,
                    "uses_sheet_scale": False,
                },
                {
                    "view": "Right",
                    "position_sheet_m": [0.165, 0.07],
                    "position_locked": True,
                    "scale_numerator": 1,
                    "scale_denominator": 1,
                    "uses_sheet_scale": True,
                    "sheet_scale_numerator": 1,
                    "sheet_scale_denominator": 1,
                },
                {
                    "view": "Aux",
                    "position_sheet_m": [0.23, 0.095],
                    "position_locked": False,
                    "scale_numerator": 1,
                    "scale_denominator": 1,
                    "uses_sheet_scale": False,
                },
            ],
            "view_parentage": [],
            "projection_alignments": [],
        },
        "minimum_spacing_m": {
            "object_to_object": 0.002,
            "object_to_frame": 0.005,
            "text_to_geometry": 0.001,
        },
        "boundary_capabilities": {
            "registry_version": "1.0.0",
            "verification": "live_complete",
            "qualification_id": "G0-G3-FIXTURE",
            "required": [
                "dimension_display_bounds",
                "note_text_bounds",
                "sheet_border_bounds",
                "view_outline_bounds",
            ],
            "unsupported": [],
        },
        "snapshots": {
            "before_rebuild_sha256": "b" * 64,
            "after_rebuild_sha256": "c" * 64,
            "readonly_reopen_sha256": "d" * 64,
            "object_identity_stable": True,
        },
        "source_immutability": {
            "drawing_opened_read_only": True,
            "drawing_saved": False,
            "hashes_unchanged": True,
            "dimension_count_unchanged": True,
            "dimension_values_unchanged": True,
            "dimension_attachments_unchanged": True,
        },
        "blockers": [],
    }
    handoff_path = tmp_path / "drawing-layout-handoff.json"
    _write_json(handoff_path, handoff)
    publication = tmp_path / "publication"
    publication.mkdir()
    request = {
        "protocol_id": "solidworks-drawing-layout-planning-request",
        "schema_version": "1.0",
        "request_id": "DLPR-G3",
        "plan_id": "DLP-G3",
        "created_at_utc": "2026-08-15T11:00:00+08:00",
        "source_dimension_request": {
            "schema_version": "1.0",
            "handoff_path": str(dimension_handoff.resolve()),
            "handoff_sha256": _sha(dimension_handoff),
            "planner_profile": "production",
            "publication_directory": str(tmp_path.resolve()),
            "user_requirements": {"source_drawing_read_only": True},
        },
        "handoff": {"path": str(handoff_path.resolve()), "sha256": _sha(handoff_path)},
        "publication_directory": str(publication.resolve()),
        "authorization": {
            "movable_view_names": ["Aux"],
            "scalable_view_names": ["Front"],
            "allow_sheet_scale_change": True,
            "allowed_sheet_formats": [
                {
                    "authorization_id": "approve-a3",
                    "format_id": "ISO-A3-landscape",
                    "width_m": 0.42,
                    "height_m": 0.297,
                    "approved_by": "drawing-owner",
                    "approved_at_utc": "2026-08-15T10:30:00+08:00",
                    "approval_reference": "G3 contract fixture",
                }
            ],
        },
        "intents": {
            "dimensions": [
                {
                    "dimension_id": "D-1",
                    "object_id": "dimension-object",
                    "preferred_position_sheet_m": [0.08, 0.13],
                    "tier": "outer",
                    "stack_index": 0,
                    "priority": 100,
                }
            ],
            "annotations": [
                {
                    "object_id": "note-object",
                    "preferred_position_sheet_m": [0.17, 0.13],
                    "priority": 90,
                }
            ],
            "leaders": [
                {
                    "object_id": "note-object",
                    "attachment_point_sheet_m": [0.15, 0.115],
                    "preferred_end_sheet_m": [0.17, 0.13],
                    "priority": 80,
                }
            ],
            "views": [
                {
                    "view_name": "Aux",
                    "preferred_position_sheet_m": [0.23, 0.14],
                    "priority": 70,
                }
            ],
            "view_scales": [
                {
                    "view_name": "Front",
                    "candidates": [{"numerator": 1, "denominator": 2}],
                    "priority": 60,
                }
            ],
            "sheet_scale": {
                "candidates": [{"numerator": 1, "denominator": 2}],
                "priority": 50,
            },
            "sheet_format": {"authorization_ids": ["approve-a3"], "priority": 40},
        },
        "assumptions": ["Only layout may change."],
    }
    return request, handoff, registry


def _rewrite_handoff(request: dict[str, Any], handoff: dict[str, Any]) -> None:
    path = Path(request["handoff"]["path"])
    _write_json(path, handoff)
    request["handoff"]["sha256"] = _sha(path)


def _keep_dimension_intent_only(request: dict[str, Any]) -> None:
    request["intents"]["annotations"] = []
    request["intents"]["leaders"] = []
    request["intents"]["views"] = []
    request["intents"]["view_scales"] = []
    request["intents"]["sheet_scale"] = None
    request["intents"]["sheet_format"] = None


def test_g3_request_schema_and_ruleset_are_frozen(tmp_path: Path) -> None:
    request, _, _ = _fixture(tmp_path)
    schema = json.loads(REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(
        json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(request)) == []
    model = LayoutPlanningRequest.model_validate(request)
    assert model.intents.dimensions[0].priority == 100
    ruleset, digest = load_ruleset()
    assert ruleset["phase_order"] == [
        "dimension_text_and_hierarchy",
        "leaders_and_labels",
        "movable_views",
        "local_scale",
        "sheet_scale",
        "authorized_sheet_format",
    ]
    assert digest == hashlib.sha256(RULESET_PATH.read_bytes()).hexdigest()
    tampered = copy.deepcopy(ruleset)
    tampered["phase_order"][0], tampered["phase_order"][1] = (
        tampered["phase_order"][1],
        tampered["phase_order"][0],
    )
    tampered_path = tmp_path / "tampered-ruleset.json"
    _write_json(tampered_path, tampered)
    with pytest.raises(ValueError, match="frozen phase order"):
        load_ruleset(tampered_path)


def test_g3_engine_publishes_all_six_phases_and_valid_plan(tmp_path: Path) -> None:
    request, _, registry = _fixture(tmp_path)
    result = DrawingLayoutPlannerEngine(capabilities=registry).plan(request)
    assert result.status == "published"
    assert result.execution_readiness == "supported"
    assert result.validation.passed
    assert result.plan is not None
    result_schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert list(
        Draft202012Validator(result_schema).iter_errors(
            result.model_dump(mode="json")
        )
    ) == []
    plan = json.loads(Path(result.plan.path).read_text(encoding="utf-8"))
    schema = json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(plan)) == []
    kinds = [operation["kind"] for operation in plan["operations"]]
    assert kinds == [
        "set_dimension_hierarchy",
        "move_dimension",
        "move_annotation",
        "route_leader",
        "move_view",
        "set_view_scale",
        "set_sheet_scale",
        "set_sheet_format",
    ]
    assert [row["sequence"] for row in plan["operations"]] == list(range(8))
    assert plan["producer"]["ruleset_sha256"] == hashlib.sha256(
        RULESET_PATH.read_bytes()
    ).hexdigest()
    assert plan["execution_policy"]["allow_delete_objects"] is False


def test_g6_revalidation_recomputes_unique_plan_and_rejects_drift(tmp_path: Path) -> None:
    request, _, registry = _fixture(tmp_path)
    engine = DrawingLayoutPlannerEngine(capabilities=registry)
    result = engine.plan(request)
    assert result.plan is not None
    plan = json.loads(Path(result.plan.path).read_text(encoding="utf-8"))

    normalized, rebound_request, validation, assessment, audit = engine.validate_plan(
        plan, request
    )
    assert validation.passed
    assert assessment is not None and assessment.status == "supported"
    assert audit.request_sha256 == canonical_json_sha256(
        rebound_request.model_dump(mode="json"), "layout planning request"
    )
    assert normalized.execution_dict() == plan

    drifted = copy.deepcopy(plan)
    move = next(row for row in drifted["operations"] if row["kind"] == "move_dimension")
    move["target_position_sheet_m"][0] += 0.001
    _, _, rejected, rejected_assessment, _ = engine.validate_plan(drifted, request)
    assert rejected.integrity == "fail"
    assert rejected_assessment is None
    assert rejected.issues[0].code == "published-plan-determinism-mismatch"


def test_solver_is_byte_deterministic_for_same_handoff_and_intent(tmp_path: Path) -> None:
    request, handoff, _ = _fixture(tmp_path)
    model = LayoutPlanningRequest.model_validate(request)
    solver = RepositoryLayoutSolver()
    first = solver.solve(handoff, model.intents, model.authorization)
    second = solver.solve(handoff, model.intents, model.authorization)
    assert first.operations == second.operations
    assert json.dumps(first.operations, sort_keys=True, separators=(",", ":")) == json.dumps(
        second.operations, sort_keys=True, separators=(",", ":")
    )
    assert first.issues == second.issues == ()


def test_grid_solver_moves_away_from_collision_deterministically(tmp_path: Path) -> None:
    request, handoff, _ = _fixture(tmp_path)
    request["intents"] = {
        "dimensions": [
            {
                "dimension_id": "D-1",
                "object_id": "dimension-object",
                "preferred_position_sheet_m": [0.065, 0.07],
                "tier": "outer",
                "stack_index": 0,
                "priority": 100,
            }
        ],
        "annotations": [],
        "leaders": [],
        "views": [],
        "view_scales": [],
        "sheet_scale": None,
        "sheet_format": None,
    }
    model = LayoutPlanningRequest.model_validate(request)
    outcome = RepositoryLayoutSolver().solve(
        handoff, model.intents, model.authorization
    )
    move = next(row for row in outcome.operations if row["kind"] == "move_dimension")
    assert move["target_position_sheet_m"] != [0.065, 0.07]
    assert all(value * 1000 == round(value * 1000) for value in move["target_position_sheet_m"])


def test_priority_controls_deterministic_conflict_resolution(tmp_path: Path) -> None:
    request, handoff, _ = _fixture(tmp_path)
    handoff["objects"].append(
        {
            "id": "note-object-low",
            "category": "note_text_bounds",
            "view": "Right",
            "bounds": [0.19, 0.115, 0.21, 0.12],
            "source_api": "INote.GetExtent",
            "exact": True,
            "collision_usable": True,
            "text_height_m": 0.0025,
        }
    )
    request["intents"] = {
        "dimensions": [],
        "annotations": [
            {
                "object_id": "note-object-low",
                "preferred_position_sheet_m": [0.17, 0.13],
                "priority": 10,
            },
            {
                "object_id": "note-object",
                "preferred_position_sheet_m": [0.17, 0.13],
                "priority": 100,
            },
        ],
        "leaders": [],
        "views": [],
        "view_scales": [],
        "sheet_scale": None,
        "sheet_format": None,
    }
    model = LayoutPlanningRequest.model_validate(request)
    outcome = RepositoryLayoutSolver().solve(
        handoff, model.intents, model.authorization
    )
    moves = [row for row in outcome.operations if row["kind"] == "move_annotation"]
    assert [row["object_id"] for row in moves] == ["note-object", "note-object-low"]
    assert moves[0]["target_position_sheet_m"] == [0.17, 0.13]
    assert moves[1]["target_position_sheet_m"] != [0.17, 0.13]


def test_engine_rejects_capability_blocked_handoff_before_solver(tmp_path: Path) -> None:
    request, handoff, registry = _fixture(tmp_path)
    handoff["status"] = "capability_blocked"
    handoff["boundary_capabilities"]["unsupported"] = [
        "dimension_display_bounds"
    ]
    handoff["blockers"] = ["dimension boundary unavailable"]
    _rewrite_handoff(request, handoff)
    result = DrawingLayoutPlannerEngine(capabilities=registry).plan(request)
    assert result.status == "rejected"
    assert result.validation.integrity == "fail"
    assert result.validation.solver == "not_run"
    assert any(
        issue.code == "handoff-capability-blocked"
        for issue in result.validation.issues
    )


def test_engine_rejects_upstream_hash_drift(tmp_path: Path) -> None:
    request, handoff, registry = _fixture(tmp_path)
    drawing = Path(
        next(
            row["path"]
            for row in handoff["upstream_artifacts"]
            if row["role"] == "dimensioned_drawing"
        )
    )
    drawing.write_bytes(b"tampered")
    result = DrawingLayoutPlannerEngine(capabilities=registry).plan(request)
    assert result.status == "rejected"
    assert any(
        issue.code == "upstream-sha256-mismatch"
        for issue in result.validation.issues
    )


def test_solver_rejects_locked_or_unauthorized_targets(tmp_path: Path) -> None:
    request, handoff, _ = _fixture(tmp_path)
    handoff["constraints"]["frozen_objects"].append("dimension-object")
    model = LayoutPlanningRequest.model_validate(request)
    outcome = RepositoryLayoutSolver().solve(
        handoff, model.intents, model.authorization
    )
    assert any(issue.code == "dimension-object-frozen" for issue in outcome.issues)

    handoff["constraints"]["frozen_objects"].remove("dimension-object")
    authorization = model.authorization.model_copy(
        update={"movable_view_names": ()}
    )
    outcome = RepositoryLayoutSolver().solve(handoff, model.intents, authorization)
    assert any(issue.code == "view-move-unauthorized" for issue in outcome.issues)


def test_dimension_crossing_metadata_is_mandatory(tmp_path: Path) -> None:
    request, handoff, _ = _fixture(tmp_path)
    dimension = next(row for row in handoff["objects"] if row["id"] == "dimension-object")
    dimension.pop("attachment_point_sheet_m")
    _keep_dimension_intent_only(request)
    model = LayoutPlanningRequest.model_validate(request)
    outcome = RepositoryLayoutSolver().solve(
        handoff, model.intents, model.authorization
    )
    assert any(issue.code == "dimension-attachment-missing" for issue in outcome.issues)
    assert not any(row["kind"] == "move_dimension" for row in outcome.operations)


def test_readability_gate_rejects_small_text_and_arrow(tmp_path: Path) -> None:
    request, handoff, registry = _fixture(tmp_path)
    dimension = next(row for row in handoff["objects"] if row["id"] == "dimension-object")
    note = next(row for row in handoff["objects"] if row["id"] == "note-object")
    dimension["text_height_m"] = 0.0005
    note["arrow_size_m"] = 0.0005
    _rewrite_handoff(request, handoff)
    result = DrawingLayoutPlannerEngine(capabilities=registry).plan(request)
    assert result.status == "rejected"
    assert result.validation.readability == "fail"
    codes = {issue.code for issue in result.validation.issues}
    assert {"text-height-unreadable", "arrow-size-unreadable"}.issubset(codes)


def test_projection_alignment_gate_rejects_drift(tmp_path: Path) -> None:
    request, handoff, registry = _fixture(tmp_path)
    handoff["constraints"]["projection_alignments"] = [
        {
            "view": "Aux",
            "parent_view": "Front",
            "axis": "horizontal",
            "offset_m": 0.0,
        }
    ]
    request["intents"]["views"] = []
    _rewrite_handoff(request, handoff)
    result = DrawingLayoutPlannerEngine(capabilities=registry).plan(request)
    assert result.status == "rejected"
    assert result.validation.projection_alignment == "fail"
    assert any(
        issue.code == "projection-alignment-drift"
        for issue in result.validation.issues
    )


def test_dimension_crossing_gate_rejects_unrelated_view(tmp_path: Path) -> None:
    request, handoff, registry = _fixture(tmp_path)
    handoff["objects"].append(
        {
            "id": "view-obstacle",
            "category": "view_outline_bounds",
            "view": "Obstacle",
            "bounds": [0.105, 0.105, 0.13, 0.14],
            "source_api": "IView.GetOutline",
            "exact": True,
            "collision_usable": True,
        }
    )
    handoff["constraints"]["view_constraints"].append(
        {
            "view": "Obstacle",
            "position_sheet_m": [0.1175, 0.1225],
            "position_locked": True,
            "scale_numerator": 1,
            "scale_denominator": 1,
            "uses_sheet_scale": False,
        }
    )
    request["intents"]["dimensions"][0]["preferred_position_sheet_m"] = [0.17, 0.15]
    _keep_dimension_intent_only(request)
    _rewrite_handoff(request, handoff)
    result = DrawingLayoutPlannerEngine(capabilities=registry).plan(request)
    assert result.status == "rejected"
    assert result.validation.dimension_crossing == "fail"
    assert any(
        issue.code == "dimension-crosses-unrelated-view"
        for issue in result.validation.issues
    )


@pytest.mark.parametrize(
    ("objects", "expected_gate"),
    [
        (
            [
                {
                    "id": "outside-safe",
                    "category": "center_element_bounds",
                    "view": "Sheet",
                    "bounds": [0.292, 0.1, 0.296, 0.105],
                    "source_api": "contract-fixture",
                    "exact": True,
                    "collision_usable": True,
                }
            ],
            "safe_area",
        ),
        (
            [
                {
                    "id": "inside-title-block",
                    "category": "center_element_bounds",
                    "view": "Sheet",
                    "bounds": [0.23, 0.02, 0.24, 0.03],
                    "source_api": "contract-fixture",
                    "exact": True,
                    "collision_usable": True,
                }
            ],
            "locked_zones",
        ),
        (
            [
                {
                    "id": "collision-a",
                    "category": "center_element_bounds",
                    "view": "Sheet",
                    "bounds": [0.1, 0.16, 0.12, 0.17],
                    "source_api": "contract-fixture",
                    "exact": True,
                    "collision_usable": True,
                },
                {
                    "id": "collision-b",
                    "category": "center_element_bounds",
                    "view": "Sheet",
                    "bounds": [0.11, 0.165, 0.13, 0.175],
                    "source_api": "contract-fixture",
                    "exact": True,
                    "collision_usable": True,
                },
            ],
            "collisions",
        ),
        (
            [
                {
                    "id": "spacing-a",
                    "category": "center_element_bounds",
                    "view": "Sheet",
                    "bounds": [0.1, 0.16, 0.11, 0.17],
                    "source_api": "contract-fixture",
                    "exact": True,
                    "collision_usable": True,
                },
                {
                    "id": "spacing-b",
                    "category": "center_element_bounds",
                    "view": "Sheet",
                    "bounds": [0.111, 0.16, 0.121, 0.17],
                    "source_api": "contract-fixture",
                    "exact": True,
                    "collision_usable": True,
                },
            ],
            "minimum_spacing",
        ),
    ],
)
def test_frozen_baseline_geometry_issues_are_preserved_without_new_violations(
    tmp_path: Path, objects: list[dict[str, Any]], expected_gate: str
) -> None:
    request, handoff, registry = _fixture(tmp_path)
    handoff["objects"].extend(copy.deepcopy(objects))
    handoff["boundary_capabilities"]["required"].append("center_element_bounds")
    _keep_dimension_intent_only(request)
    _rewrite_handoff(request, handoff)
    result = DrawingLayoutPlannerEngine(capabilities=registry).plan(request)
    assert result.status == "published"
    assert getattr(result.validation, expected_gate) == "pass"


def test_request_rejects_non_reduced_scale_and_empty_intents(tmp_path: Path) -> None:
    request, _, _ = _fixture(tmp_path)
    request["intents"]["view_scales"][0]["candidates"] = [
        {"numerator": 2, "denominator": 4}
    ]
    with pytest.raises(ValidationError, match="reduced ratios"):
        LayoutPlanningRequest.model_validate(request)
    request, _, _ = _fixture(tmp_path / "empty")
    request["intents"] = {
        "dimensions": [],
        "annotations": [],
        "leaders": [],
        "views": [],
        "view_scales": [],
        "sheet_scale": None,
        "sheet_format": None,
    }
    with pytest.raises(ValidationError, match="at least one layout intent"):
        LayoutPlanningRequest.model_validate(request)
