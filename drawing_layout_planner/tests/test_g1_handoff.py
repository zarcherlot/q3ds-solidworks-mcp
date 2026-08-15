from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from drawing_layout_planner.handoff import (
    DrawingLayoutHandoffError,
    build_layout_handoff_request,
    dimension_invariant_sha256,
    file_sha256,
    validate_drawing_layout_handoff,
)
from drawing_planner.planning_models import canonical_json_sha256


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _upstream(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    plan_path = tmp_path / "dimension_plan.json"
    drawing = tmp_path / "dimensioned.SLDDRW"
    sidecar = tmp_path / "dimensioned.dimension-verification.json"
    qualification = tmp_path / "g0-qualification.json"
    manifest = tmp_path / "capabilities.json"
    drawing.write_bytes(b"dimensioned-drawing")
    plan = {
        "protocol_id": "solidworks-dimension-plan",
        "schema_version": "1.0",
        "plan_id": "DP-G1",
        "dimensions": [{"dimension_id": "D-1"}],
    }
    _write(plan_path, plan)
    row = {
        "dimension_id": "D-1",
        "value_si": 0.05,
        "model_persistent_references": ["persistent-reference"],
    }
    _write(
        sidecar,
        {
            "protocol_id": "solidworks-dimension-drawing-verification",
            "schema_version": "1.0",
            "verified": True,
            "plan_id": "DP-G1",
            "plan_file_path": str(plan_path.resolve()),
            "plan_file_sha256": file_sha256(plan_path),
            "plan_canonical_sha256": canonical_json_sha256(plan, "DimensionPlan"),
            "output_path": str(drawing.resolve()),
            "artifact_sha256": file_sha256(drawing),
            "in_memory_verification": {"verified": True, "dimensions": [row]},
            "reopen_verification": {
                "verified": True,
                "actual_total_count": 1,
                "dimensions": [row],
            },
        },
    )
    _write(
        qualification,
        {
            "protocol_id": "solidworks-layout-g0-qualification",
            "schema_version": "1.0",
            "qualification_id": "G0-TEST",
            "solidworks_revision": "33.5.0",
            "overall_status": "complete",
        },
    )
    _write(
        manifest,
        {
            "protocol_id": "solidworks-drawing-layout-executor-capabilities",
            "schema_version": "1.0",
            "registry_version": "1.0.0",
            "solidworks_revision": "33.5.0",
            "verification": "live_complete",
            "capabilities": [
                {"id": "view_outline_bounds", "status": "supported"},
                {"id": "dimension_display_bounds", "status": "unsupported"},
            ],
            "live_evidence": {
                "qualification_path": str(qualification.resolve()),
                "qualification_sha256": file_sha256(qualification),
                "qualification_id": "G0-TEST",
                "solidworks_revision": "33.5.0",
            },
        },
    )
    return plan_path, drawing, sidecar, manifest


def test_request_binds_verified_f_output_and_live_g0(tmp_path: Path) -> None:
    plan, drawing, sidecar, manifest = _upstream(tmp_path)
    request = build_layout_handoff_request(
        plan,
        drawing,
        sidecar,
        tmp_path / "publication",
        capability_manifest_path=manifest,
    )
    assert request["protocol_id"] == "solidworks-drawing-layout-handoff-request"
    assert request["source"]["dimensioned_drawing"]["sha256"] == file_sha256(drawing)
    assert request["boundary_capabilities"]["qualification"]["sha256"]


def test_request_rejects_dimension_semantic_drift(tmp_path: Path) -> None:
    plan, drawing, sidecar, manifest = _upstream(tmp_path)
    value = json.loads(sidecar.read_text(encoding="utf-8"))
    value["reopen_verification"]["dimensions"][0]["dimension_id"] = "D-other"
    _write(sidecar, value)
    with pytest.raises(DrawingLayoutHandoffError, match="IDs/count"):
        build_layout_handoff_request(
            plan,
            drawing,
            sidecar,
            tmp_path / "publication",
            capability_manifest_path=manifest,
        )


def test_published_handoff_freezes_bounds_and_reports_capability_blocker(
    tmp_path: Path,
) -> None:
    plan, drawing, sidecar, manifest = _upstream(tmp_path)
    request = build_layout_handoff_request(
        plan,
        drawing,
        sidecar,
        tmp_path / "publication",
        capability_manifest_path=manifest,
    )
    qualification = Path(request["boundary_capabilities"]["qualification"]["path"])
    artifacts = []
    for role, path in (
        ("dimension_plan", plan),
        ("dimensioned_drawing", drawing),
        ("dimension_verification_sidecar", sidecar),
        ("boundary_capability_manifest", manifest),
        ("boundary_qualification", qualification),
    ):
        digest = file_sha256(path)
        artifacts.append(
            {
                "role": role,
                "path": str(path.resolve()),
                "sha256_before": digest,
                "sha256_after": digest,
            }
        )
    dimensions = [
        {
            "dimension_id": "D-1",
            "value_si": 0.05,
            "model_persistent_references": ["persistent-reference"],
        }
    ]
    handoff = {
        "protocol_id": "solidworks-drawing-layout-handoff",
        "schema_version": "1.0",
        "handoff_id": "DLH-0123456789abcdef",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "capability_blocked",
        "source_request_sha256": "a" * 64,
        "upstream_artifacts": artifacts,
        "dimension_semantics": {
            "plan_id": "DP-G1",
            "planned_count": 1,
            "verified_actual_count": 1,
            "invariant_sha256": dimension_invariant_sha256(dimensions),
            "dimensions": dimensions,
        },
        "solidworks": {"revision": "33.5.0", "execution_mode": "live_read_only"},
        "sheet": {
            "name": "Sheet1",
            "bounds_m": [0, 0, 0.42, 0.297],
            "safe_bounds_m": [0.005, 0.005, 0.415, 0.292],
        },
        "objects": [
            {
                "id": "sheet-border",
                "category": "sheet_border_bounds",
                "view": "Sheet",
                "bounds": [0, 0, 0.42, 0.297],
                "source_api": "ISheet.GetSize",
                "exact": True,
                "collision_usable": True,
            },
            {
                "id": "dimension:front:D1",
                "category": "dimension_display_bounds",
                "view": "front",
                "bounds": [0.1, 0.1, 0.2, 0.2],
                "source_api": "IAnnotation.GetDisplayData",
                "exact": False,
                "collision_usable": False,
            },
        ],
        "constraints": {
            "locked_zones": [
                {"zone_id": "sheet-frame", "kind": "sheet_frame", "bounds_m": [0, 0, 0.42, 0.297]}
            ],
            "frozen_objects": ["sheet-border"],
            "view_constraints": [
                {
                    "view": "front",
                    "position_sheet_m": [0.15, 0.15],
                    "position_locked": True,
                }
            ],
            "view_parentage": [],
            "projection_alignments": [],
        },
        "minimum_spacing_m": request["minimum_spacing_m"],
        "boundary_capabilities": {
            "registry_version": "1.0.0",
            "verification": "live_complete",
            "qualification_id": "G0-TEST",
            "required": ["dimension_display_bounds", "sheet_border_bounds"],
            "unsupported": ["dimension_display_bounds"],
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
        "blockers": ["dimension_display_bounds is unsupported for exact collision use"],
    }
    assert validate_drawing_layout_handoff(handoff)["status"] == "capability_blocked"
    handoff["objects"][1]["collision_usable"] = True
    with pytest.raises(DrawingLayoutHandoffError, match="collision usability"):
        validate_drawing_layout_handoff(handoff)
