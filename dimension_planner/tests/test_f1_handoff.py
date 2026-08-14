from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dimension_planner.handoff import (
    PACKAGE_ROOT,
    DimensionPlanningHandoffError,
    build_handoff_request,
    file_sha256,
    validate_dimension_planning_handoff,
    validate_handoff_request,
)
from drawing_planner.planning_models import canonical_json_sha256


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _upstream(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    model = tmp_path / "part.SLDPRT"
    drawing = tmp_path / "part.SLDDRW"
    plan_path = tmp_path / "view-plan.json"
    sidecar = tmp_path / "part.SLDDRW.verification.json"
    model.write_bytes(b"source-model")
    drawing.write_bytes(b"verified-drawing")
    plan = {
        "protocol_id": "solidworks-view-plan",
        "schema_version": "1.4",
        "model_path": str(model.resolve()),
        "model_sha256": file_sha256(model),
        "configuration": "Default",
        "projection_method": "third_angle",
        "sheet_scale": {"numerator": 1, "denominator": 1},
        "dimension_zones": [],
        "views": [{"id": "front"}],
    }
    _write_json(plan_path, plan)
    _write_json(
        sidecar,
        {
            "verified": True,
            "output_path": str(drawing.resolve()),
            "artifact_sha256": file_sha256(drawing),
            "plan_canonical_sha256": canonical_json_sha256(plan, "ViewPlan"),
        },
    )
    return plan_path, drawing, sidecar, model


def _approved_input() -> dict:
    return {
        "input_id": "approved-length-1",
        "source_tier": "user_confirmed_input",
        "approved_by": "test-user",
        "approved_at_utc": datetime.now(timezone.utc).isoformat(),
        "approval_reference": "test approval",
        "target_feature_ids": ["Boss-Extrude1"],
        "value": {"kind": "quantity", "quantity_kind": "length", "value_si": 0.01},
    }


def test_build_request_binds_verified_upstream(tmp_path: Path) -> None:
    plan, drawing, sidecar, _ = _upstream(tmp_path)
    request = build_handoff_request(
        plan,
        drawing,
        sidecar,
        tmp_path / "publication",
        approved_user_inputs=[_approved_input()],
    )

    assert request["protocol_id"] == "solidworks-dimension-planning-handoff-request"
    assert request["source"]["verified_drawing"]["sha256"] == file_sha256(drawing)
    assert request["approved_user_inputs"][0]["source_tier"] == "user_confirmed_input"


def test_request_rejects_unknown_and_unapproved_input() -> None:
    candidate = {
        "protocol_id": "solidworks-dimension-planning-handoff-request",
        "schema_version": "1.0",
        "source": {
            name: {"path": f"C:\\f1\\{name}.json", "sha256": "a" * 64}
            for name in ("view_plan", "verified_drawing", "verification_sidecar")
        },
        "publication_directory": "C:\\f1\\output",
        "approved_user_inputs": [_approved_input()],
        "legacy_tool": "auto_dimension_drawing",
    }
    with pytest.raises(DimensionPlanningHandoffError, match="legacy_tool"):
        validate_handoff_request(candidate)

    candidate.pop("legacy_tool")
    candidate["approved_user_inputs"] = [
        {**_approved_input(), "source_tier": "reference_geometry_measurement"}
    ]
    with pytest.raises(DimensionPlanningHandoffError, match="source_tier"):
        validate_handoff_request(candidate)


def test_build_request_rejects_hash_binding_drift(tmp_path: Path) -> None:
    plan, drawing, sidecar, _ = _upstream(tmp_path)
    sidecar_value = json.loads(sidecar.read_text(encoding="utf-8"))
    sidecar_value["artifact_sha256"] = "0" * 64
    _write_json(sidecar, sidecar_value)

    with pytest.raises(DimensionPlanningHandoffError, match="artifact_sha256"):
        build_handoff_request(plan, drawing, sidecar, tmp_path / "publication")


def test_build_request_keeps_validation_tree_read_only(tmp_path: Path) -> None:
    plan, drawing, sidecar, _ = _upstream(tmp_path)
    with pytest.raises(DimensionPlanningHandoffError, match="validation"):
        build_handoff_request(
            plan,
            drawing,
            sidecar,
            PACKAGE_ROOT.parent / "validation" / "f1-forbidden-output",
        )


def test_published_handoff_requires_complete_unchanged_ledger(tmp_path: Path) -> None:
    _, drawing, _, model = _upstream(tmp_path)
    drawing_hash = file_sha256(drawing)
    model_hash = file_sha256(model)
    rows = [
        {
            "role": role,
            "path": str((tmp_path / name).resolve()),
            "sha256_before": digest,
            "sha256_after": digest,
        }
        for role, name, digest in (
            ("view_plan", "view-plan.json", file_sha256(tmp_path / "view-plan.json")),
            ("verified_drawing", "part.SLDDRW", drawing_hash),
            (
                "verification_sidecar",
                "part.SLDDRW.verification.json",
                file_sha256(tmp_path / "part.SLDDRW.verification.json"),
            ),
            ("source_model", "part.SLDPRT", model_hash),
        )
    ]
    handoff = {
        "protocol_id": "solidworks-dimension-planning-handoff",
        "schema_version": "1.0",
        "handoff_id": "DMH-0123456789abcdef",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "ready",
        "source_request_sha256": "f" * 64,
        "upstream_artifacts": rows,
        "source_model": {
            "path": str(model.resolve()),
            "sha256": model_hash,
            "configuration": "Default",
            "save_flag": False,
            "persistent_reference_domain": "source_model",
        },
        "drawing_context": {
            "path": str(drawing.resolve()),
            "sheet_name": "Sheet1",
            "sheet_bounds_m": [0, 0, 0.42, 0.297],
            "projection_method": "third_angle",
            "sheet_scale": 1.0,
        },
        "views": [
            {
                "view_id": "front",
                "solidworks_name": "Q3DS_VP_front",
                "bounds_sheet_m": [0.1, 0.1, 0.2, 0.2],
                "referenced_model": str(model.resolve()),
                "configuration": "Default",
                "projected_geometry": [],
                "existing_annotations": [],
            }
        ],
        "model_driven_dimensions": [],
        "pmi_annotations": [],
        "manufacturing_features": [],
        "approved_user_inputs": [],
        "reference_measurements": [],
        "dimension_zones": [],
        "limitations": {
            "annotation_text_bounds": "unsupported_exact",
            "reference_measurements_are_manufacturing_requirements": False,
        },
        "source_immutability": {
            "drawing_opened_read_only": True,
            "source_documents_clean": True,
            "hashes_unchanged": True,
        },
    }

    assert validate_dimension_planning_handoff(handoff)["status"] == "ready"
    handoff["upstream_artifacts"][1]["sha256_after"] = "e" * 64
    with pytest.raises(DimensionPlanningHandoffError, match="changed"):
        validate_dimension_planning_handoff(handoff)
