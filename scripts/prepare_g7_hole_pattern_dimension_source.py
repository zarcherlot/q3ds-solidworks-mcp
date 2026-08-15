"""Publish and independently verify the native hole-pattern DimensionPlan for G7."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adapters" / "claude"))

from dimension_planner.planning_models import DimensionPlanningRequest  # noqa: E402
from drawing_planner.planning_models import canonical_json_sha256  # noqa: E402
from semantic_models import DimensionPlan  # noqa: E402
from server import (  # noqa: E402
    publish_validated_part_drawing_dimension_plan,
    qualify_dimensioned_part_drawing,
    validate_part_drawing_dimension_plan,
    verify_qualified_dimensioned_part_drawing,
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.resolve(strict=True).read_bytes()).hexdigest()


def _publish(path: Path, value: dict[str, Any]) -> Path:
    target = path.resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite immutable artifact: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _require(payload: str, stage: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError(f"hole-pattern {stage} failed: {payload}")
    return value


def run(bracket_path: Path, flange_path: Path, current_handoff_path: Path,
        base_matrix_path: Path, output_root: Path,
        result_path: Path) -> dict[str, Any]:
    bracket = _load(bracket_path)
    flange = _load(flange_path)
    root = output_root.resolve()
    if root.exists():
        raise FileExistsError(f"output root must be new: {root}")
    source_handoff_path = current_handoff_path.resolve(strict=True)
    if flange["handoff"]["path"] != bracket["handoff"]["path"]:
        raise ValueError("hole plan candidates must bind the same handoff")
    publication = root / "dimension-plan"
    publication.mkdir(parents=True, exist_ok=False)
    handoff_path = publication / "dimension-planning-handoff.json"
    shutil.copy2(source_handoff_path, handoff_path)
    request_value = {
        "schema_version": "1.0",
        "handoff_path": str(handoff_path),
        "handoff_sha256": _sha(handoff_path),
        "planner_profile": "production",
        "publication_directory": str(publication.resolve()),
        "user_requirements": {"source_drawing_read_only": True},
    }
    request = DimensionPlanningRequest.model_validate(request_value)
    handoff = _load(handoff_path)
    plan = copy.deepcopy(bracket)
    plan["plan_id"] = "DP-G7-HOLE-PATTERN-20260815"
    plan["created_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    plan["handoff"] = {"path": str(handoff_path), "sha256": _sha(handoff_path)}
    artifacts = {row["role"]: row for row in handoff["upstream_artifacts"]}
    plan["handoff_id"] = handoff["handoff_id"]
    plan["source_model"] = {
        "path": artifacts["source_model"]["path"],
        "sha256": artifacts["source_model"]["sha256_after"],
    }
    plan["source_drawing"] = {
        "path": artifacts["verified_drawing"]["path"],
        "sha256": artifacts["verified_drawing"]["sha256_after"],
    }
    plan["verification_sidecar"] = {
        "path": artifacts["verification_sidecar"]["path"],
        "sha256": artifacts["verification_sidecar"]["sha256_after"],
    }
    plan["view_plan"] = {
        "path": artifacts["view_plan"]["path"],
        "sha256": artifacts["view_plan"]["sha256_after"],
    }
    plan["configuration"] = handoff["source_model"]["configuration"]
    template_dimension = copy.deepcopy(bracket["dimensions"][0])
    feature_ids = [row["feature_id"] for row in handoff["manufacturing_features"]]
    dimensions: list[dict[str, Any]] = []
    for source in handoff["model_driven_dimensions"]:
        if source.get("manufacturing_requirement") is not True:
            continue
        candidate = source["import_candidates"][0]
        native_type = candidate["native_type"]
        attachment_count = len(candidate["attachment_entity_ids"])
        kind = "angular" if native_type == 3 else (
            "diameter" if native_type == 6 else (
                "linear"
            )
        )
        roles = ("arc",) if kind == "diameter" else (
            ("first",) if attachment_count == 1 else ("first", "second")
        )
        dimension = copy.deepcopy(template_dimension)
        dimension["dimension_id"] = f"D-G7-HOLE-MODEL-{len(dimensions) + 1:03d}"
        dimension["kind"] = kind
        dimension["source"] = {
            "source_tier": "model_or_pmi",
            "handoff_collection": "model_driven_dimensions",
            "source_ids": [source["dimension_id"]],
        }
        dimension["target_view_id"] = candidate["view_id"]
        dimension["dimension_zone_id"] = next(
            row["id"] for row in handoff["dimension_zones"]
            if row["view_id"] == candidate["view_id"]
        )
        dimension["attachments"] = [
            {
                "attachment_id": f"AT-G7-HOLE-MODEL-{len(dimensions) + 1:03d}-{index + 1}",
                "entity_id": entity_id,
                "model_persistent_reference": candidate["model_persistent_references"][index],
                "persistent_reference_kind": next(
                    entity["persistent_reference_kind"]
                    for view in handoff["views"] if view["view_id"] == candidate["view_id"]
                    for entity in view["projected_geometry"] if entity["entity_id"] == entity_id
                ),
                "role": roles[index],
            }
            for index, entity_id in enumerate(candidate["attachment_entity_ids"])
        ]
        dimension["feature_ids"] = feature_ids
        dimension["value"] = {
            "value_mode": "model_driven",
            "quantity_kind": "angle" if kind == "angular" else "length",
            "nominal_si": source["value_si"],
        }
        dimension["display_format"]["unit"] = "degree" if kind == "angular" else "mm"
        dimensions.append(dimension)
    spacing = next(row for row in dimensions if row["kind"] == "linear")
    spacing["kind"] = "hole_spacing"
    circle_view = next(
        view for view in handoff["views"]
        if any(entity["entity_kind"] == "circle" for entity in view["projected_geometry"])
    )
    circle = next(
        entity for entity in circle_view["projected_geometry"]
        if entity["entity_kind"] == "circle" and entity["persistent_reference_kind"] == "entity"
    )
    count_source = next(
        row for row in handoff["model_driven_dimensions"]
        if row["value_si"] == 3.0 and "阵列" in row["full_name"]
    )
    quantity = copy.deepcopy(template_dimension)
    quantity.update({
        "dimension_id": "D-G7-HOLE-QUANTITY-3X",
        "kind": "hole_quantity",
        "source": {
            "source_tier": "model_or_pmi",
            "handoff_collection": "model_driven_dimensions",
            "source_ids": [count_source["dimension_id"]],
        },
        "target_view_id": circle_view["view_id"],
        "dimension_zone_id": next(
            row["id"] for row in handoff["dimension_zones"]
            if row["view_id"] == circle_view["view_id"]
        ),
        "attachments": [{
            "attachment_id": "AT-G7-HOLE-QUANTITY-ARC",
            "entity_id": circle["entity_id"],
            "model_persistent_reference": circle["model_persistent_reference"],
            "persistent_reference_kind": "entity",
            "role": "arc",
        }],
        "feature_ids": feature_ids,
        "value": {
            "value_mode": "model_driven",
            "quantity_kind": "count",
            "nominal_si": 3.0,
        },
        "display_format": {
            "unit": "count", "precision": 0, "prefix": "3X ", "suffix": "",
            "show_parentheses": False, "show_units": False, "dual_units": False,
        },
    })
    dimensions.append(quantity)
    plan["dimensions"] = dimensions
    for ordinal, dimension in enumerate(plan["dimensions"]):
        dimension["dimension_id"] = f"D-G7-HOLE-{ordinal + 1:03d}-{dimension['kind'].upper()}"
        dimension["hierarchy"]["priority"] = 100 - ordinal
    by_view: dict[str, list[dict[str, Any]]] = {}
    for dimension in plan["dimensions"]:
        by_view.setdefault(dimension["target_view_id"], []).append(dimension)
    zones = {
        row["view_id"]: row["bounds_sheet_m"]
        for row in _load(handoff_path)["dimension_zones"]
    }
    for view_id, dimensions in by_view.items():
        bounds = zones[view_id]
        for index, dimension in enumerate(dimensions, start=1):
            dimension["initial_position_sheet_m"] = [
                (bounds["x_min_m"] + bounds["x_max_m"]) / 2,
                bounds["y_min_m"]
                + index / (len(dimensions) + 1)
                * (bounds["y_max_m"] - bounds["y_min_m"]),
            ]
    plan["assumptions"] = [
        "Every hole-pattern value and attachment is copied from the frozen repository draft bound to this handoff."
    ]
    semantic_plan = DimensionPlan(root=plan)
    publish = _require(
        publish_validated_part_drawing_dimension_plan(plan=semantic_plan, request=request),
        "publish",
    )
    plan_path = publication / "dimension_plan.json"
    output_path = root / "dimension-drawing" / "g7-hole-pattern.SLDDRW"
    evidence_path = root / "f7-evidence" / "g7-hole-pattern.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    case = {
        "case_id": "F7-G7-HOLE-PATTERN",
        "category": "flange",
        "plan_path": str(plan_path.resolve(strict=True)),
        "plan_file_sha256": _sha(plan_path),
        "plan_canonical_sha256": canonical_json_sha256(plan, "DimensionPlan"),
        "planning_request": request.model_dump(mode="json"),
        "planning_request_sha256": canonical_json_sha256(
            request.model_dump(mode="json"), "dimension planning request"
        ),
        "output_path": str(output_path.resolve()),
        "evidence_path": str(evidence_path.resolve()),
    }
    base = _load(base_matrix_path)
    matrix_cases = [case]
    for category in ("plate", "bracket", "shaft_sleeve", "slot_cavity", "threaded"):
        filler = copy.deepcopy(next(row for row in base["cases"] if row["category"] == category))
        filler["case_id"] = "F7-G7-HOLE-FILLER-" + category.replace("_", "-").upper()
        filler["output_path"] = str((root / "fillers" / f"{category}.SLDDRW").resolve())
        filler["evidence_path"] = str((root / "fillers" / f"{category}.json").resolve())
        matrix_cases.append(filler)
    matrix = {
        "protocol_id": "solidworks-dimension-f7-matrix-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "f0_evidence": copy.deepcopy(base["f0_evidence"]),
        "cases": matrix_cases,
    }
    matrix_path = _publish(root / "dimension-f7-hole-pattern-request.json", matrix)
    matrix_hash = _sha(matrix_path)
    validate = _require(validate_part_drawing_dimension_plan(
        plan=semantic_plan, request=request, output_path=str(output_path.resolve())), "validate")
    create = _require(qualify_dimensioned_part_drawing(
        plan=semantic_plan, request=request, output_path=str(output_path.resolve()),
        matrix_request_path=str(matrix_path), matrix_request_sha256=matrix_hash,
        case_id=case["case_id"]), "qualify")
    verify = _require(verify_qualified_dimensioned_part_drawing(
        plan=semantic_plan, request=request, output_path=str(output_path.resolve()),
        matrix_request_path=str(matrix_path), matrix_request_sha256=matrix_hash,
        case_id=case["case_id"]), "verify")
    report = {
        "status": "complete",
        "plan_path": str(plan_path),
        "planning_request": request.model_dump(mode="json"),
        "output_path": str(output_path.resolve(strict=True)),
        "verification_sidecar_path": str(Path(str(output_path.resolve()) + ".dimension-verification.json").resolve(strict=True)),
        "matrix_request_path": str(matrix_path),
        "matrix_request_sha256": matrix_hash,
        "stages": {"publish": publish, "validate": validate, "create": create, "verify": verify},
    }
    result = _publish(result_path, report)
    return {"result_path": str(result), "dimension_count": len(plan["dimensions"])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bracket-candidate", type=Path, required=True)
    parser.add_argument("--flange-candidate", type=Path, required=True)
    parser.add_argument("--current-handoff", type=Path, required=True)
    parser.add_argument("--base-f7-request", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.bracket_candidate, args.flange_candidate,
                         args.current_handoff, args.base_f7_request,
                         args.output_root, args.result),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
