from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from drawing_layout_planner.capability_registry import (
    LAYOUT_OPERATION_IDS,
    LAYOUT_SAFETY_IDS,
    DrawingLayoutCapabilityManifest,
    DrawingLayoutCapabilityRegistry,
    DrawingLayoutExecutionCapabilityError,
    current_registry,
)
from drawing_layout_planner.plan_store import PlanStore
from drawing_layout_planner.planning_models import (
    DrawingLayoutPlan,
    drawing_layout_plan_from_mapping,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PLAN_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "drawing-layout-plan.schema.json"
CAPABILITY_SCHEMA_PATH = (
    PACKAGE_ROOT / "contracts" / "drawing-layout-plan-capabilities.schema.json"
)
CAPABILITY_PATH = PACKAGE_ROOT / "capabilities" / "plan-current.json"
BOUNDARY_PATH = PACKAGE_ROOT / "capabilities" / "current.json"
SHA = "1" * 64


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": SHA}


def _plan(tmp_path: Path) -> dict[str, object]:
    return {
        "$schema": "https://q3ds.local/contracts/solidworks-drawing-layout-plan-1.0.schema.json",
        "protocol_id": "solidworks-drawing-layout-plan",
        "schema_version": "1.0",
        "plan_id": "layout-plan-g2",
        "created_at_utc": "2026-08-15T10:00:00+08:00",
        "producer": {
            "name": "repository-layout-planner",
            "version": "0.1.0",
            "ruleset_id": "layout-rules-v1",
            "ruleset_sha256": "2" * 64,
        },
        "execution_policy": {
            "on_integrity_mismatch": "fail",
            "on_layout_violation": "fail",
            "on_unsupported_operation": "fail",
            "preserve_dimension_count": True,
            "preserve_dimension_values": True,
            "preserve_dimension_attachments": True,
            "preserve_configuration": True,
            "preserve_display_state": True,
            "preserve_projection_method": True,
            "preserve_section_definitions": True,
            "preserve_model_associativity": True,
            "preserve_frozen_geometry": True,
            "allow_delete_objects": False,
            "allow_new_manufacturing_annotations": False,
            "allow_source_model_write": False,
            "allow_upstream_drawing_overwrite": False,
            "allow_partial_commit": False,
        },
        "handoff": _artifact(tmp_path / "drawing-layout-handoff.json"),
        "handoff_id": "layout-handoff-g1",
        "source_dimension_plan": _artifact(tmp_path / "dimension_plan.json"),
        "source_drawing": _artifact(tmp_path / "dimensioned.SLDDRW"),
        "dimension_verification_sidecar": _artifact(
            tmp_path / "dimension-verification.json"
        ),
        "configuration": "Default",
        "source_invariants": {
            "dimension_semantics_sha256": "3" * 64,
            "dimension_ids": ["D-1"],
            "object_snapshot_sha256": "4" * 64,
            "object_ids": ["dimension-object", "note-object", "frame-object"],
            "view_names": ["Front"],
            "locked_object_ids": ["frame-object"],
            "required_boundary_capabilities": [
                "view_outline_bounds",
                "dimension_display_bounds",
                "sheet_border_bounds",
            ],
        },
        "authorization": {
            "movable_view_names": ["Front"],
            "scalable_view_names": ["Front"],
            "allow_sheet_scale_change": True,
            "allowed_sheet_formats": [
                {
                    "authorization_id": "sheet-approval-a3",
                    "format_id": "ISO-A3-landscape",
                    "width_m": 0.42,
                    "height_m": 0.297,
                    "approved_by": "drawing-owner",
                    "approved_at_utc": "2026-08-15T09:30:00+08:00",
                    "approval_reference": "user-approved-G2-fixture",
                }
            ],
        },
        "operations": [
            {
                "operation_id": "op-0",
                "kind": "move_dimension",
                "sequence": 0,
                "object_id": "dimension-object",
                "dimension_id": "D-1",
                "target_position_sheet_m": [0.12, 0.15],
                "preserve_attachment": True,
            },
            {
                "operation_id": "op-1",
                "kind": "move_annotation",
                "sequence": 1,
                "object_id": "note-object",
                "target_position_sheet_m": [0.18, 0.16],
            },
            {
                "operation_id": "op-2",
                "kind": "route_leader",
                "sequence": 2,
                "object_id": "note-object",
                "points_sheet_m": [[0.18, 0.16], [0.2, 0.17]],
                "preserve_attachment": True,
            },
            {
                "operation_id": "op-3",
                "kind": "move_view",
                "sequence": 3,
                "view_name": "Front",
                "target_position_sheet_m": [0.15, 0.11],
                "preserve_alignment": True,
            },
            {
                "operation_id": "op-4",
                "kind": "set_dimension_hierarchy",
                "sequence": 4,
                "dimension_id": "D-1",
                "tier": "outer",
                "stack_index": 0,
            },
            {
                "operation_id": "op-5",
                "kind": "set_view_scale",
                "sequence": 5,
                "view_name": "Front",
                "numerator": 2,
                "denominator": 1,
            },
            {
                "operation_id": "op-6",
                "kind": "set_sheet_scale",
                "sequence": 6,
                "numerator": 1,
                "denominator": 2,
            },
            {
                "operation_id": "op-7",
                "kind": "set_sheet_format",
                "sequence": 7,
                "authorization_id": "sheet-approval-a3",
                "format_id": "ISO-A3-landscape",
                "width_m": 0.42,
                "height_m": 0.297,
            },
        ],
        "assumptions": ["G1 handoff hashes were independently verified."],
        "open_questions": [],
    }


def _schema(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_g2_schemas_are_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(_schema(PLAN_SCHEMA_PATH))
    Draft202012Validator.check_schema(_schema(CAPABILITY_SCHEMA_PATH))


def test_plan_is_valid_under_schema_and_domain_model(tmp_path: Path) -> None:
    candidate = _plan(tmp_path)
    validator = Draft202012Validator(
        _schema(PLAN_SCHEMA_PATH), format_checker=FormatChecker()
    )
    assert list(validator.iter_errors(candidate)) == []
    plan = drawing_layout_plan_from_mapping(candidate)
    assert isinstance(plan, DrawingLayoutPlan)
    assert tuple(operation.kind for operation in plan.operations) == LAYOUT_OPERATION_IDS
    assert plan.execution_dict() == candidate
    assert len(plan.canonical_sha256) == 64


def test_plan_is_deeply_immutable_and_rejects_extra_or_delete_operations(
    tmp_path: Path,
) -> None:
    plan = drawing_layout_plan_from_mapping(_plan(tmp_path))
    with pytest.raises(ValidationError):
        plan.configuration = "Changed"  # type: ignore[misc]

    extra = _plan(tmp_path)
    extra["unexpected"] = True
    with pytest.raises(ValidationError):
        drawing_layout_plan_from_mapping(extra)

    deletion = _plan(tmp_path)
    deletion["operations"][0]["kind"] = "delete_dimension"  # type: ignore[index]
    with pytest.raises(ValidationError):
        drawing_layout_plan_from_mapping(deletion)


@pytest.mark.parametrize(
    ("field", "weakened"),
    [
        ("preserve_dimension_values", False),
        ("preserve_dimension_attachments", False),
        ("preserve_configuration", False),
        ("preserve_projection_method", False),
        ("preserve_section_definitions", False),
        ("preserve_model_associativity", False),
        ("preserve_frozen_geometry", False),
        ("allow_delete_objects", True),
        ("allow_new_manufacturing_annotations", True),
        ("allow_source_model_write", True),
        ("allow_upstream_drawing_overwrite", True),
        ("allow_partial_commit", True),
    ],
)
def test_execution_policy_cannot_be_weakened(
    tmp_path: Path, field: str, weakened: bool
) -> None:
    candidate = _plan(tmp_path)
    candidate["execution_policy"][field] = weakened  # type: ignore[index]
    with pytest.raises(ValidationError):
        drawing_layout_plan_from_mapping(candidate)


def test_references_and_authorizations_are_enforced(tmp_path: Path) -> None:
    unknown = _plan(tmp_path)
    unknown["operations"][0]["object_id"] = "missing"  # type: ignore[index]
    with pytest.raises(ValidationError, match="unknown object"):
        drawing_layout_plan_from_mapping(unknown)

    locked = _plan(tmp_path)
    locked["operations"][1]["object_id"] = "frame-object"  # type: ignore[index]
    with pytest.raises(ValidationError, match="locked object"):
        drawing_layout_plan_from_mapping(locked)

    unauthorized_view = _plan(tmp_path)
    unauthorized_view["authorization"]["movable_view_names"] = []  # type: ignore[index]
    with pytest.raises(ValidationError, match="explicit view authorization"):
        drawing_layout_plan_from_mapping(unauthorized_view)

    unauthorized_scale = _plan(tmp_path)
    unauthorized_scale["authorization"]["allow_sheet_scale_change"] = False  # type: ignore[index]
    with pytest.raises(ValidationError, match="explicit authorization"):
        drawing_layout_plan_from_mapping(unauthorized_scale)

    mismatched_format = _plan(tmp_path)
    mismatched_format["operations"][7]["width_m"] = 0.5  # type: ignore[index]
    with pytest.raises(ValidationError, match="exactly match"):
        drawing_layout_plan_from_mapping(mismatched_format)


def test_operation_sequence_and_reduced_scales_are_canonical(tmp_path: Path) -> None:
    sequence_gap = _plan(tmp_path)
    sequence_gap["operations"][7]["sequence"] = 8  # type: ignore[index]
    with pytest.raises(ValidationError, match="contiguous"):
        drawing_layout_plan_from_mapping(sequence_gap)

    unordered = _plan(tmp_path)
    unordered["operations"][0], unordered["operations"][1] = (  # type: ignore[index]
        unordered["operations"][1],  # type: ignore[index]
        unordered["operations"][0],  # type: ignore[index]
    )
    with pytest.raises(ValidationError, match="sequence order"):
        drawing_layout_plan_from_mapping(unordered)

    scale = _plan(tmp_path)
    scale["operations"][5]["numerator"] = 4  # type: ignore[index]
    scale["operations"][5]["denominator"] = 2  # type: ignore[index]
    with pytest.raises(ValidationError, match="reduced ratio"):
        drawing_layout_plan_from_mapping(scale)


def test_plan_store_publishes_canonical_payload_once(tmp_path: Path) -> None:
    plan = drawing_layout_plan_from_mapping(_plan(tmp_path))
    published = PlanStore().publish(plan, str(tmp_path))
    target = tmp_path / "drawing_layout_plan.json"
    assert published.path == str(target.resolve())
    assert published.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert json.loads(target.read_text(encoding="utf-8")) == plan.execution_dict()
    assert list(tmp_path.glob(".drawing_layout_plan.*.tmp")) == []
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        PlanStore().publish(plan, str(tmp_path))


def test_plan_store_rejects_validation_tree(tmp_path: Path) -> None:
    validation_root = Path(__file__).resolve().parents[2] / "validation"
    assert validation_root.is_dir()
    with pytest.raises(ValueError, match="must not be validation"):
        PlanStore().publish(_plan(tmp_path), str(validation_root))


def test_capability_manifest_schema_catalog_and_g0_binding() -> None:
    payload = _schema(CAPABILITY_PATH)
    validator = Draft202012Validator(_schema(CAPABILITY_SCHEMA_PATH))
    assert list(validator.iter_errors(payload)) == []
    manifest = DrawingLayoutCapabilityManifest.model_validate(payload)
    assert tuple(manifest.operations) == LAYOUT_OPERATION_IDS
    assert tuple(manifest.safety_elements) == LAYOUT_SAFETY_IDS
    assert manifest.boundary_registry.manifest_sha256 == hashlib.sha256(
        BOUNDARY_PATH.read_bytes()
    ).hexdigest()


def test_current_capability_registry_accepts_g7_proven_execution(tmp_path: Path) -> None:
    registry = current_registry()
    assessment = registry.assess(_plan(tmp_path))
    assert assessment.status == "supported"
    assert assessment.unsupported_capabilities == ()
    registry.require_supported(_plan(tmp_path))


def test_registry_rejects_tampered_g0_manifest(tmp_path: Path) -> None:
    tampered = tmp_path / "current.json"
    tampered.write_bytes(BOUNDARY_PATH.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="SHA-256 binding"):
        DrawingLayoutCapabilityRegistry.from_paths(CAPABILITY_PATH, tampered)


def test_mapping_input_is_defensively_copied(tmp_path: Path) -> None:
    candidate = _plan(tmp_path)
    original = copy.deepcopy(candidate)
    plan = drawing_layout_plan_from_mapping(candidate)
    candidate["configuration"] = "Mutated"
    candidate["operations"][0]["target_position_sheet_m"][0] = 99.0  # type: ignore[index]
    assert plan.configuration == original["configuration"]
    assert plan.operations[0].target_position_sheet_m[0] == 0.12  # type: ignore[union-attr]
