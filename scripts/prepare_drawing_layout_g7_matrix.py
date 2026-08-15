"""Prepare the immutable ten-case DrawingLayoutPlan G7 live matrix."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from drawing_planner.planning_models import canonical_json_sha256  # noqa: E402
from drawing_layout_planner.g7_evidence import (  # noqa: E402
    validate_g7_matrix_request,
)
from drawing_layout_planner.handoff import (  # noqa: E402
    build_layout_handoff_request,
    file_sha256,
    validate_drawing_layout_handoff,
)
from drawing_layout_planner.planner_engine import DrawingLayoutPlannerEngine  # noqa: E402


SCENARIO_SOURCES = {
    "sparse_dimensions": "section_view",
    "multi_view": "auxiliary_view",
    "section_view": "section_view",
    "detail_view": "detail_view",
    "auxiliary_view": "auxiliary_view",
    "hole_pattern": "hole_pattern",
    "high_density_dimensions": "auxiliary_view",
    "scale_change": "auxiliary_view",
    "authorized_sheet_format": "auxiliary_view",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--special-result", type=Path, required=True)
    parser.add_argument("--hole-result", type=Path, required=True)
    parser.add_argument("--execution-base-url", default="http://localhost:5000")
    parser.add_argument("--reuse-handoff-root", type=Path)
    args = parser.parse_args()

    root = args.output_root.resolve()
    if root.exists():
        raise ValueError(f"output root must be new: {root}")
    root.mkdir(parents=True)

    sources = _load_sources(args.special_result, args.hole_result)
    if args.reuse_handoff_root is not None:
        handoffs = {
            name: _existing_handoff(args.reuse_handoff_root / name)
            for name in sources
        }
    else:
        handoffs = {
            name: _initialize_handoff(
                source,
                root / "handoffs" / name,
                args.execution_base_url,
            )
            for name, source in sources.items()
        }

    positive_cases: list[dict[str, Any]] = []
    requests: dict[str, dict[str, Any]] = {}
    for index, (scenario, source_name) in enumerate(SCENARIO_SOURCES.items(), 1):
        case_root = root / "cases" / scenario
        publication = case_root / "plan"
        publication.mkdir(parents=True)
        request = _planning_request(
            scenario,
            index,
            sources[source_name],
            handoffs[source_name],
            publication,
        )
        result = DrawingLayoutPlannerEngine().plan(request)
        if result.status != "published" or result.plan is None:
            issues = [issue.model_dump(mode="json") for issue in result.validation.issues]
            raise RuntimeError(f"{scenario} layout planning rejected: {issues}")
        if result.execution_readiness != "capability_blocked":
            raise RuntimeError(f"{scenario} must remain production capability_blocked")
        plan_path = Path(result.plan.path)
        plan = _load_json(plan_path)
        requests[scenario] = request
        positive_cases.append(
            {
                "case_id": f"G7-{index:02d}-{scenario.upper().replace('_', '-')}",
                "scenario": scenario,
                "plan_path": str(plan_path.resolve()),
                "plan_file_sha256": file_sha256(plan_path),
                "plan_canonical_sha256": canonical_json_sha256(
                    plan, "DrawingLayoutPlan"
                ),
                "planning_request": request,
                "planning_request_sha256": canonical_json_sha256(
                    request, "layout planning request"
                ),
                "source_dimension_request_sha256": canonical_json_sha256(
                    request["source_dimension_request"],
                    "source dimension planning request",
                ),
                "output_path": str((case_root / "qualified-final.SLDDRW").resolve()),
                "evidence_path": str((case_root / "g7-evidence.json").resolve()),
            }
        )

    negative_root = root / "cases" / "unauthorized_sheet_format"
    negative_root.mkdir(parents=True)
    negative_request = copy.deepcopy(requests["authorized_sheet_format"])
    negative_request["request_id"] = "DLPR-G7-UNAUTHORIZED-SHEET-FORMAT"
    negative_request["plan_id"] = "DLP-G7-UNAUTHORIZED-SHEET-FORMAT"
    negative_request["publication_directory"] = str(negative_root.resolve())
    negative_request["authorization"]["allowed_sheet_formats"] = []

    boundary = _load_json(
        REPOSITORY_ROOT / "drawing_layout_planner" / "capabilities" / "current.json"
    )["live_evidence"]
    matrix = {
        "protocol_id": "solidworks-drawing-layout-g7-matrix-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "g0_qualification": {
            "path": boundary["qualification_path"],
            "sha256": boundary["qualification_sha256"],
        },
        "positive_cases": positive_cases,
        "negative_cases": [
            {
                "case_id": "G7-10-UNAUTHORIZED-SHEET-FORMAT",
                "scenario": "unauthorized_sheet_format",
                "planning_request": negative_request,
                "planning_request_sha256": canonical_json_sha256(
                    negative_request, "layout planning request"
                ),
                "expected_issue_code": "sheet-format-unauthorized",
                "evidence_path": str(
                    (negative_root / "g7-negative-evidence.json").resolve()
                ),
            }
        ],
    }
    normalized = validate_g7_matrix_request(matrix)
    request_path = root / "drawing-layout-g7-matrix-request.json"
    _publish_json_once(request_path, normalized)
    report = {
        "status": "prepared",
        "request_path": str(request_path),
        "request_sha256": file_sha256(request_path),
        "positive_case_count": len(positive_cases),
        "negative_case_count": 1,
        "operation_coverage": sorted(
            {
                operation["kind"]
                for case in positive_cases
                for operation in _load_json(Path(case["plan_path"]))["operations"]
            }
        ),
    }
    _publish_json_once(root / "preparation-result.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _load_sources(special_path: Path, hole_path: Path) -> dict[str, dict[str, Any]]:
    special = _load_json(special_path.resolve(strict=True))
    sources: dict[str, dict[str, Any]] = {}
    for case in special["cases"]:
        create_result = case["stages"]["create"]["result"]
        sources[case["scenario"]] = {
            "plan_path": case["plan_path"],
            "drawing_path": case["output_path"],
            "sidecar_path": create_result["verification_path"],
            "dimension_request": case["planning_request"],
        }
    hole = _load_json(hole_path.resolve(strict=True))
    sources["hole_pattern"] = {
        "plan_path": hole["plan_path"],
        "drawing_path": hole["output_path"],
        "sidecar_path": hole["verification_sidecar_path"],
        "dimension_request": hole["planning_request"],
    }
    expected = {"section_view", "detail_view", "auxiliary_view", "hole_pattern"}
    if set(sources) != expected:
        raise ValueError(f"source inventory mismatch: {sorted(sources)}")
    return sources


def _initialize_handoff(
    source: dict[str, Any], publication: Path, execution_base_url: str
) -> dict[str, Any]:
    request = build_layout_handoff_request(
        Path(source["plan_path"]),
        Path(source["drawing_path"]),
        Path(source["sidecar_path"]),
        publication,
    )
    with httpx.Client(trust_env=False) as client:
        response = client.post(
            execution_base_url.rstrip("/") + "/api/layout-planning/handoff",
            json=request,
            timeout=300,
        )
    payload = response.json()
    if response.status_code != 200:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    path = Path(payload["handoff_path"])
    handoff = validate_drawing_layout_handoff(_load_json(path))
    if handoff["status"] != "ready" or handoff["boundary_capabilities"]["unsupported"]:
        raise RuntimeError(f"G1 handoff is not ready: {path}")
    return {"path": str(path.resolve()), "sha256": file_sha256(path), "value": handoff}


def _existing_handoff(publication: Path) -> dict[str, Any]:
    path = (publication / "drawing-layout-handoff.json").resolve(strict=True)
    handoff = validate_drawing_layout_handoff(_load_json(path))
    if handoff["status"] != "ready" or handoff["boundary_capabilities"]["unsupported"]:
        raise RuntimeError(f"reused G1 handoff is not ready: {path}")
    return {"path": str(path), "sha256": file_sha256(path), "value": handoff}


def _planning_request(
    scenario: str,
    index: int,
    source: dict[str, Any],
    handoff_binding: dict[str, Any],
    publication: Path,
) -> dict[str, Any]:
    handoff = handoff_binding["value"]
    dimensions = sorted(
        (
            row
            for row in handoff["objects"]
            if row["category"] == "dimension_display_bounds"
        ),
        key=lambda row: row["dimension_id"],
    )
    dimension = dimensions[0]
    views = handoff["constraints"]["view_constraints"]
    parented = {
        row["view"] for row in handoff["constraints"]["view_parentage"]
    }
    independent_view = next(row for row in views if row["view"] not in parented)
    authorization = {
        "movable_view_names": [],
        "scalable_view_names": [],
        "allow_sheet_scale_change": False,
        "allowed_sheet_formats": [],
    }
    intents: dict[str, Any] = {
        "dimensions": [
            {
                "dimension_id": dimension["dimension_id"],
                "object_id": dimension["id"],
                "preferred_position_sheet_m": dimension["current_position_sheet_m"],
                "tier": "inner",
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
    if scenario == "hole_pattern":
        note = next(
            row
            for row in handoff["objects"]
            if row["category"] == "note_text_bounds" and row.get("leader_count") == 1
        )
        leader = next(
            row for row in handoff["objects"] if row["category"] == "leader_bounds"
        )
        points = leader["leader_points_sheet_m"]
        intents["annotations"] = [
            {
                "object_id": note["id"],
                "preferred_position_sheet_m": note["current_position_sheet_m"],
                "priority": 90,
            }
        ]
        intents["leaders"] = [
            {
                "object_id": leader["id"],
                # IAnnotation.GetLeaderPointsAtIndex returns annotation-side first and
                # attached entity last; the planning contract uses engineering direction.
                "attachment_point_sheet_m": points[-1],
                "preferred_end_sheet_m": points[0],
                "priority": 80,
            }
        ]
    if scenario == "multi_view":
        authorization["movable_view_names"] = [independent_view["view"]]
        intents["views"] = [
            {
                "view_name": independent_view["view"],
                "preferred_position_sheet_m": independent_view["position_sheet_m"],
                "priority": 70,
            }
        ]
    if scenario == "scale_change":
        authorization["scalable_view_names"] = [independent_view["view"]]
        authorization["allow_sheet_scale_change"] = True
        intents["view_scales"] = [
            {
                "view_name": independent_view["view"],
                "candidates": [
                    {
                        "numerator": independent_view["scale_numerator"],
                        "denominator": independent_view["scale_denominator"],
                    }
                ],
                "priority": 60,
            }
        ]
        intents["sheet_scale"] = {
            "candidates": [
                {
                    "numerator": handoff["sheet"]["scale_numerator"],
                    "denominator": handoff["sheet"]["scale_denominator"],
                }
            ],
            "priority": 50,
        }
    if scenario == "authorized_sheet_format":
        sheet = handoff["sheet"]
        approval = {
            "authorization_id": "G7-A3-OWNER-APPROVAL",
            "format_id": "ISO-A3-landscape",
            "width_m": sheet["bounds_m"][2] - sheet["bounds_m"][0],
            "height_m": sheet["bounds_m"][3] - sheet["bounds_m"][1],
            "approved_by": "drawing-owner",
            "approved_at_utc": "2026-08-15T09:00:00+08:00",
            "approval_reference": "G7 immutable live-matrix owner authorization",
        }
        authorization["allowed_sheet_formats"] = [approval]
        intents["sheet_format"] = {
            "authorization_ids": [approval["authorization_id"]],
            "priority": 40,
        }
    return {
        "protocol_id": "solidworks-drawing-layout-planning-request",
        "schema_version": "1.0",
        "request_id": f"DLPR-G7-{index:02d}-{scenario.upper().replace('_', '-')}",
        "plan_id": f"DLP-G7-{index:02d}-{scenario.upper().replace('_', '-')}",
        "created_at_utc": "2026-08-15T09:00:00+08:00",
        "source_dimension_request": source["dimension_request"],
        "handoff": {
            "path": handoff_binding["path"],
            "sha256": handoff_binding["sha256"],
        },
        "publication_directory": str(publication.resolve()),
        "authorization": authorization,
        "intents": intents,
        "assumptions": [
            "G7 qualification preserves all upstream engineering semantics and permits only explicit layout operations.",
            "Pre-existing exact-boundary contacts are frozen; the final transaction may not introduce a new contact.",
        ],
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _publish_json_once(path: Path, value: Any) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(data)


if __name__ == "__main__":
    raise SystemExit(main())
