from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from dimension_planner.capability_registry import (
    DimensionCapabilityManifest,
    current_registry,
)
from dimension_planner.plan_store import DimensionPlanStore
from dimension_planner.planning_models import (
    DimensionPlan,
    DimensionPlanningRequest,
    DimensionPlanningResult,
    dimension_plan_from_mapping,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
DIMENSION_KINDS = (
    "linear",
    "aligned",
    "diameter",
    "radius",
    "angular",
    "reference",
    "hole_diameter",
    "hole_depth",
    "hole_quantity",
    "hole_spacing",
    "hole_group_location",
    "overall",
    "step",
    "boss",
    "slot",
    "chamfer",
    "fillet",
    "symmetric",
)


def _artifact(tmp_path: Path, name: str, marker: str) -> dict:
    return {"path": str((tmp_path / name).resolve()), "sha256": marker * 64}


def _dimension(kind: str, index: int = 0) -> dict:
    if kind == "reference":
        source = {
            "source_tier": "reference_geometry_measurement",
            "measurement_ids": [f"RM-{index}"],
            "manufacturing_requirement": False,
        }
        value_mode = "measured_reference"
        hierarchy = "reference"
    else:
        source = {
            "source_tier": "model_or_pmi",
            "handoff_collection": "model_driven_dimensions",
            "source_ids": [f"MD-{index}"],
        }
        value_mode = "model_driven"
        hierarchy = "functional"
    quantity = "angle" if kind == "angular" else (
        "count" if kind == "hole_quantity" else "length"
    )
    nominal = 3.0 if quantity == "count" else (1.570796327 if quantity == "angle" else 0.01)
    unit = "count" if quantity == "count" else (
        "degree" if quantity == "angle" else "mm"
    )
    return {
        "dimension_id": f"D-{index}-{kind}",
        "kind": kind,
        "source": source,
        "target_view_id": "front",
        "attachments": [
            {
                "attachment_id": f"A-{index}",
                "entity_id": f"GE-{index}",
                "model_persistent_reference": "AQIDBA==",
                "persistent_reference_kind": "entity",
                "role": "first",
            }
        ],
        "feature_ids": [f"MF-{index}"],
        "value": {
            "value_mode": value_mode,
            "quantity_kind": quantity,
            "nominal_si": nominal,
        },
        "tolerance": None,
        "display_format": {
            "unit": unit,
            "precision": 3,
            "prefix": "",
            "suffix": "",
            "show_parentheses": kind == "reference",
            "show_units": False,
            "dual_units": False,
        },
        "dimension_zone_id": "DZ-front-top",
        "hierarchy": {
            "level": hierarchy,
            "priority": index,
            "chain_id": None,
            "baseline_id": None,
        },
        "initial_position_sheet_m": [0.1, 0.2],
        "verification_tolerance": {
            "value_abs_si": 1e-9,
            "position_abs_m": 1e-6,
            "attachment_count_exact": True,
            "display_text_exact": False,
        },
    }


def _plan(tmp_path: Path, kinds: tuple[str, ...] = ("linear",)) -> dict:
    return {
        "$schema": "https://q3ds.local/contracts/solidworks-dimension-plan-1.0.schema.json",
        "protocol_id": "solidworks-dimension-plan",
        "schema_version": "1.0",
        "plan_id": "DP-F2-contract",
        "created_at_utc": "2026-08-13T03:00:00Z",
        "producer": {
            "name": "f2-contract-test",
            "version": "1.0.0",
            "ruleset_id": "dimension-native-v1",
            "ruleset_sha256": "1" * 64,
        },
        "execution_policy": {
            "on_integrity_mismatch": "fail",
            "on_selection_ambiguity": "fail",
            "on_unsupported_dimension": "fail",
            "on_layout_violation": "fail",
            "allow_source_model_write": False,
            "allow_upstream_drawing_overwrite": False,
            "allow_partial_commit": False,
        },
        "handoff": _artifact(tmp_path, "dimension-planning-handoff.json", "2"),
        "handoff_id": "DMH-f2-contract",
        "source_model": _artifact(tmp_path, "part.SLDPRT", "3"),
        "source_drawing": _artifact(tmp_path, "views.SLDDRW", "4"),
        "view_plan": _artifact(tmp_path, "view_plan.json", "5"),
        "verification_sidecar": _artifact(tmp_path, "views.verify.json", "6"),
        "configuration": "Default",
        "dimensions": [_dimension(kind, index) for index, kind in enumerate(kinds)],
        "assumptions": [],
        "open_questions": [],
    }


def _schema(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_f2_contracts_are_valid_draft_2020_12() -> None:
    for name in (
        "dimension-plan.schema.json",
        "dimension-planning-request.schema.json",
        "dimension-planning-result.schema.json",
        "dimension-executor-capabilities.schema.json",
    ):
        Draft202012Validator.check_schema(_schema(name))


def test_dimension_plan_covers_complete_f2_union(tmp_path: Path) -> None:
    model = dimension_plan_from_mapping(_plan(tmp_path, DIMENSION_KINDS))
    assert tuple(item.kind for item in model.dimensions) == DIMENSION_KINDS
    Draft202012Validator(
        _schema("dimension-plan.schema.json"), format_checker=FormatChecker()
    ).validate(model.execution_dict())
    assert len(model.canonical_sha256) == 64


def test_dimension_plan_is_strict_and_source_aware(tmp_path: Path) -> None:
    candidate = _plan(tmp_path)
    candidate["legacy_tool"] = "auto_dimension_drawing"
    with pytest.raises(ValidationError, match="legacy_tool"):
        dimension_plan_from_mapping(candidate)

    candidate = _plan(tmp_path, ("reference",))
    candidate["dimensions"][0]["hierarchy"]["level"] = "manufacturing"
    with pytest.raises(ValidationError, match="reference-level"):
        dimension_plan_from_mapping(candidate)

    candidate = _plan(tmp_path)
    candidate["dimensions"][0]["value"]["value_mode"] = "approved_value"
    with pytest.raises(ValidationError, match="source tier"):
        dimension_plan_from_mapping(candidate)


def test_dimension_plan_rejects_duplicates_and_untrusted_tolerance(tmp_path: Path) -> None:
    candidate = _plan(tmp_path, ("linear", "aligned"))
    candidate["dimensions"][1]["dimension_id"] = candidate["dimensions"][0]["dimension_id"]
    with pytest.raises(ValidationError, match="dimension IDs"):
        dimension_plan_from_mapping(candidate)

    candidate = _plan(tmp_path, ("reference",))
    candidate["dimensions"][0]["tolerance"] = {
        "kind": "bilateral",
        "lower_si": -0.001,
        "upper_si": 0.001,
        "fit_code": None,
    }
    with pytest.raises(ValidationError, match="cannot define manufacturing tolerance"):
        dimension_plan_from_mapping(candidate)

    candidate = _plan(tmp_path)
    candidate["dimensions"][0]["feature_ids"] = []
    with pytest.raises(ValidationError, match="feature_ids"):
        dimension_plan_from_mapping(candidate)


def test_capability_manifest_and_registry_fail_closed(tmp_path: Path) -> None:
    manifest_value = json.loads(
        (ROOT / "capabilities" / "current.json").read_text(encoding="utf-8")
    )
    manifest = DimensionCapabilityManifest.model_validate(manifest_value)
    Draft202012Validator(
        _schema("dimension-executor-capabilities.schema.json")
    ).validate(manifest_value)
    assert manifest.registry_version == "0.2.0"
    assert set(manifest.dimension_types) == set(DIMENSION_KINDS)
    assert manifest.elements["annotation_text_bounds"].status == "unsupported"

    manifest_value["elements"]["annotation_text_bounds"]["evidence_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="must match live_evidence"):
        DimensionCapabilityManifest.model_validate(manifest_value)

    plan = dimension_plan_from_mapping(_plan(tmp_path, ("linear", "hole_diameter")))
    assessment = current_registry().assess(plan.execution_dict())
    assert assessment.status == "capability_blocked"
    assert "dimension_type.linear" in assessment.unsupported_capabilities
    assert "element.annotation_text_bounds" not in assessment.unsupported_capabilities


def test_plan_store_publishes_atomically_and_never_overwrites(tmp_path: Path) -> None:
    model = dimension_plan_from_mapping(_plan(tmp_path))
    store = DimensionPlanStore()
    published = store.publish(model, str(tmp_path))
    target = Path(published.path)
    payload = target.read_bytes()
    assert target.name == "dimension_plan.json"
    assert published.sha256 == hashlib.sha256(payload).hexdigest()
    assert json.loads(payload)["protocol_id"] == "solidworks-dimension-plan"
    assert not list(tmp_path.glob(".dimension_plan.*.tmp"))
    with pytest.raises(FileExistsError, match="overwrite"):
        store.publish(model, str(tmp_path))


def test_plan_store_has_single_winner_under_race(tmp_path: Path) -> None:
    model = dimension_plan_from_mapping(_plan(tmp_path))

    def publish() -> str:
        try:
            return DimensionPlanStore().publish(model, str(tmp_path)).sha256
        except FileExistsError:
            return "lost"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert results.count("lost") == 1
    assert sum(value != "lost" for value in results) == 1
    assert not list(tmp_path.glob(".dimension_plan.*.tmp"))


def test_planning_request_and_result_models_are_immutable(tmp_path: Path) -> None:
    request = DimensionPlanningRequest(
        handoff_path=str((tmp_path / "dimension-planning-handoff.json").resolve()),
        handoff_sha256="a" * 64,
        publication_directory=str(tmp_path.resolve()),
        user_requirements={"inspection": "complete"},
    )
    assert request.planner_profile == "production"
    with pytest.raises(ValidationError):
        request.planner_profile = "debug"  # type: ignore[misc]

    result_value = {
        "schema_version": "1.0",
        "status": "published",
        "execution_readiness": "capability_blocked",
        "validation": {
            "integrity": "pass",
            "schema_check": "pass",
            "source": "pass",
            "attachment": "pass",
            "semantics": "pass",
            "coverage": "pass",
            "redundancy": "pass",
            "layout": "pass",
            "capability": "fail",
            "issues": [
                {
                    "code": "DP-CAPABILITY-BLOCKED",
                    "gate": "capability",
                    "message": "native execution is not implemented",
                    "json_pointer": None,
                }
            ],
        },
        "plan": {
            "plan_id": "DP-F2-contract",
            "path": str((tmp_path / "dimension_plan.json").resolve()),
            "sha256": "b" * 64,
        },
        "audit": {
            "request_sha256": "c" * 64,
            "candidate_sha256": "d" * 64,
            "capability_manifest_version": "0.2.0",
        },
        "unsupported_capabilities": ["dimension_type.linear"],
    }
    result = DimensionPlanningResult.model_validate(result_value)
    assert result.validation.engineering_passed is True
    Draft202012Validator(_schema("dimension-planning-result.schema.json")).validate(
        result.model_dump(mode="json")
    )
