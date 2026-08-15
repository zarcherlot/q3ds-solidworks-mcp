"""Create verified DimensionPlan sources for G7 section/detail/auxiliary cases."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "adapters" / "claude"))

from drawing_planner.planning_models import canonical_json_sha256  # noqa: E402
from dimension_planner.planning_models import DimensionPlanningRequest  # noqa: E402
from semantic_models import DimensionPlan  # noqa: E402
from server import (  # noqa: E402
    initialize_part_drawing_dimension_handoff,
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


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.resolve(strict=True).read_bytes()).hexdigest()


def _publish(path: Path, value: dict[str, Any]) -> Path:
    target = path.resolve()
    if target.exists():
        raise FileExistsError(f"refusing to overwrite immutable artifact: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _semantic(payload: str, stage: str, scenario: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError(f"{scenario} {stage} failed: {payload}")
    return value


def _bound(handoff: dict[str, Any], role: str) -> dict[str, str]:
    rows = [row for row in handoff["upstream_artifacts"] if row["role"] == role]
    if len(rows) != 1:
        raise ValueError(f"handoff requires one {role} artifact")
    row = rows[0]
    return {"path": row["path"], "sha256": row["sha256_after"]}


def _build_plan(
    template: dict[str, Any], scenario: str, handoff_path: Path,
    handoff_hash: str, planning_request: dict[str, Any]
) -> dict[str, Any]:
    handoff = _load(handoff_path)
    zones = {row["view_id"]: row for row in handoff["dimension_zones"]}
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for source in handoff["model_driven_dimensions"]:
        if source.get("manufacturing_requirement") is not True:
            continue
        candidates = [
            item for item in source.get("import_candidates", [])
            if item["view_id"] in zones
        ]
        if not candidates:
            raise ValueError(
                f"{scenario} manufacturing dimension {source['dimension_id']} "
                "has no import candidate bound to a dimension zone"
            )
        selected.extend((source, candidate) for candidate in candidates)
    if not selected:
        raise ValueError(f"{scenario} has no required import candidate")
    plan = copy.deepcopy(template)
    token = scenario.replace("_view", "").upper()
    plan["plan_id"] = f"DP-G7-{token}-20260815"
    plan["created_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    plan["handoff"] = {"path": str(handoff_path.resolve()), "sha256": handoff_hash}
    plan["handoff_id"] = handoff["handoff_id"]
    plan["source_model"] = _bound(handoff, "source_model")
    plan["source_drawing"] = _bound(handoff, "verified_drawing")
    plan["verification_sidecar"] = _bound(handoff, "verification_sidecar")
    plan["view_plan"] = _bound(handoff, "view_plan")
    plan["assumptions"] = [
        "G7 imports trusted marked-for-drawing model dimensions; no tolerance or fit is inferred.",
        "A trusted model dimension may be imported into more than one explicitly planned view when each view has a frozen native import candidate.",
    ]
    counts: dict[str, int] = {}
    totals: dict[str, int] = {}
    for _source, candidate in selected:
        totals[candidate["view_id"]] = totals.get(candidate["view_id"], 0) + 1
    feature_ids = [row["feature_id"] for row in handoff["manufacturing_features"]]
    dimensions: list[dict[str, Any]] = []
    roles = ("first", "second")
    for ordinal, (source, candidate) in enumerate(selected, start=1):
        view_id = candidate["view_id"]
        index = counts.get(view_id, 0)
        counts[view_id] = index + 1
        zone = zones[view_id]
        bounds = zone["bounds_sheet_m"]
        fraction = (index + 1) / (totals[view_id] + 1)
        position = [
            (bounds["x_min_m"] + bounds["x_max_m"]) / 2,
            bounds["y_min_m"] + fraction * (bounds["y_max_m"] - bounds["y_min_m"]),
        ]
        dimension = copy.deepcopy(plan["dimensions"][0])
        dimension["dimension_id"] = f"D-G7-{token}-IMPORT-{ordinal:03d}"
        dimension["dimension_zone_id"] = zone["id"]
        dimension["target_view_id"] = view_id
        dimension["initial_position_sheet_m"] = position
        dimension["source"]["source_ids"] = [source["dimension_id"]]
        dimension["value"]["nominal_si"] = source["value_si"]
        dimension["feature_ids"] = feature_ids
        dimension["hierarchy"]["priority"] = 100 - ordinal
        dimension["attachments"] = [
            {
                "attachment_id": f"AT-G7-{token}-{ordinal:03d}-{roles[attachment_index].upper()}",
                "role": roles[attachment_index],
                "entity_id": entity_id,
                "model_persistent_reference": candidate[
                    "model_persistent_references"
                ][attachment_index],
                "persistent_reference_kind": "entity",
            }
            for attachment_index, entity_id in enumerate(
                candidate["attachment_entity_ids"]
            )
        ]
        dimensions.append(dimension)
    plan["dimensions"] = dimensions
    expected_plan = Path(planning_request["publication_directory"]) / "dimension_plan.json"
    if expected_plan.resolve() != handoff_path.resolve().parent / "dimension_plan.json":
        raise ValueError("dimension planning request publication is not beside its handoff")
    return plan


def run(
    view_result_path: Path, template_plan_path: Path, base_f7_request_path: Path,
    output_root: Path, result_path: Path
) -> dict[str, Any]:
    view_result = _load(view_result_path)
    template = _load(template_plan_path)
    root = output_root.resolve()
    if root.exists():
        raise FileExistsError(f"output root must be new: {root}")
    prepared: list[dict[str, Any]] = []
    for row in view_result["cases"]:
        scenario = row["scenario"]
        publication = root / "dimension-plans" / scenario
        handoff_result = _semantic(
            initialize_part_drawing_dimension_handoff(
                view_plan_path=row["view_plan_path"],
                verified_drawing_path=row["output_path"],
                verification_sidecar_path=row["verification_sidecar_path"],
                publication_directory=str(publication),
            ),
            "initialize", scenario,
        )
        request_value = handoff_result["planning_request"]
        request = DimensionPlanningRequest.model_validate(request_value)
        handoff_path = Path(handoff_result["handoff_path"])
        plan_value = _build_plan(
            template, scenario, handoff_path, handoff_result["handoff_sha256"],
            request_value,
        )
        plan = DimensionPlan(root=plan_value)
        publish = _semantic(
            publish_validated_part_drawing_dimension_plan(plan=plan, request=request),
            "publish", scenario,
        )
        plan_path = publication / "dimension_plan.json"
        prepared.append({
            "scenario": scenario,
            "plan_path": str(plan_path.resolve(strict=True)),
            "planning_request": request.model_dump(mode="json"),
            "planning_request_sha256": canonical_json_sha256(
                request.model_dump(mode="json"), "dimension planning request"
            ),
            "plan_file_sha256": _file_hash(plan_path),
            "plan_canonical_sha256": canonical_json_sha256(plan_value, "DimensionPlan"),
            "output_path": str((root / "dimension-drawings" / f"g7-{scenario}.SLDDRW").resolve()),
            "evidence_path": str((root / "f7-evidence" / f"g7-{scenario}.json").resolve()),
            "stages": {"initialize": handoff_result, "publish": publish},
        })

    base = _load(base_f7_request_path)
    special_categories = {
        "section_view": "plate",
        "auxiliary_view": "bracket",
        "detail_view": "threaded",
    }
    matrix_cases: list[dict[str, Any]] = []
    for row in prepared:
        matrix_cases.append({
            "case_id": "F7-G7-" + row["scenario"].replace("_", "-").upper(),
            "category": special_categories[row["scenario"]],
            **{key: row[key] for key in (
                "plan_path", "plan_file_sha256", "plan_canonical_sha256",
                "planning_request", "planning_request_sha256", "output_path",
                "evidence_path",
            )},
        })
    (root / "dimension-drawings").mkdir(parents=True, exist_ok=True)
    (root / "f7-evidence").mkdir(parents=True, exist_ok=True)
    (root / "fillers").mkdir(parents=True, exist_ok=True)
    filler_categories = ("shaft_sleeve", "flange", "slot_cavity")
    for category in filler_categories:
        source = next(case for case in base["cases"] if case["category"] == category)
        filler = copy.deepcopy(source)
        filler["case_id"] = "F7-G7-FILLER-" + category.replace("_", "-").upper()
        filler["output_path"] = str((root / "fillers" / f"{category}.SLDDRW").resolve())
        filler["evidence_path"] = str((root / "fillers" / f"{category}.evidence.json").resolve())
        matrix_cases.append(filler)
    matrix = {
        "protocol_id": "solidworks-dimension-f7-matrix-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "f0_evidence": copy.deepcopy(base["f0_evidence"]),
        "cases": matrix_cases,
    }
    matrix_path = _publish(root / "dimension-f7-g7-sources-request.json", matrix)
    matrix_hash = _file_hash(matrix_path)

    for row in prepared:
        case_id = "F7-G7-" + row["scenario"].replace("_", "-").upper()
        plan_value = _load(Path(row["plan_path"]))
        plan = DimensionPlan(root=plan_value)
        request = DimensionPlanningRequest.model_validate(row["planning_request"])
        validate = _semantic(
            validate_part_drawing_dimension_plan(
                plan=plan, request=request, output_path=row["output_path"]
            ),
            "validate", row["scenario"],
        )
        create = _semantic(
            qualify_dimensioned_part_drawing(
                plan=plan, request=request, output_path=row["output_path"],
                matrix_request_path=str(matrix_path),
                matrix_request_sha256=matrix_hash, case_id=case_id,
            ),
            "qualify", row["scenario"],
        )
        verify = _semantic(
            verify_qualified_dimensioned_part_drawing(
                plan=plan, request=request, output_path=row["output_path"],
                matrix_request_path=str(matrix_path),
                matrix_request_sha256=matrix_hash, case_id=case_id,
            ),
            "verify", row["scenario"],
        )
        row["verification_sidecar_path"] = row["output_path"] + ".dimension-verification.json"
        row["stages"].update({"validate": validate, "create": create, "verify": verify})

    report = {
        "status": "complete",
        "matrix_request_path": str(matrix_path),
        "matrix_request_sha256": matrix_hash,
        "cases": prepared,
    }
    result = _publish(result_path, report)
    return {"result_path": str(result), "case_count": len(prepared)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view-result", type=Path, required=True)
    parser.add_argument("--template-plan", type=Path, required=True)
    parser.add_argument("--base-f7-request", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.view_result, args.template_plan, args.base_f7_request,
                         args.output_root, args.result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
