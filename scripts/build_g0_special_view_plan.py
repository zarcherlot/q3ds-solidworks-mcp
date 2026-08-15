"""Build one evidence-bound G0 ViewPlan candidate from an immutable handoff.

This utility does not publish or execute the plan. The normal semantic ViewPlan
publish/validate/create/verify transaction remains authoritative.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drawing_planner.planner_profiles import producer_contract_for_profile


def _box(x_min: float, y_min: float, x_max: float, y_max: float) -> dict[str, float]:
    return {
        "x_min_m": x_min,
        "y_min_m": y_min,
        "x_max_m": x_max,
        "y_max_m": y_max,
    }


def _evidence(geometry_path: str, finding: str) -> list[dict[str, str]]:
    return [
        {
            "report_path": geometry_path,
            "json_pointer": "/part_box_m",
            "finding": finding,
        }
    ]


def _base_view(geometry_path: str) -> dict:
    return {
        "id": "g0-main",
        "type": "model_view",
        "purpose": "Provide the stable front parent required by the native auxiliary-view transaction.",
        "model_evidence": _evidence(
            geometry_path,
            "The frozen envelope provides a stable long reference edge for the auxiliary view.",
        ),
        "expressed_features": [],
        "source": {"kind": "model_document", "reference": "model"},
        "orientation": {
            "kind": "standard_model_view",
            "standard_view": "front",
            "roll_angle_rad": 0.0,
        },
        "parent_view_id": None,
        "alignment": "none",
        "scale": 0.2,
        "scale_reason": "1:5 preserves the front parent while leaving room for two independent evidence views.",
        "layout_reason": "The upper-left safe-zone box leaves disjoint boxes for the auxiliary and center views.",
        "rejected_alternatives": [
            {
                "alternative": "Omit the bottom view",
                "reason": "The auxiliary view would lose its required parent edge.",
            }
        ],
        "display_style": {
            "mode": "hidden_lines_removed",
            "faceted": False,
            "edges": True,
        },
        "hidden_lines": "removed",
        "tangent_edges": "removed",
        "center_marks": [],
        "symmetry_centerlines": [],
        "section_definition": None,
        "broken_out_definition": None,
        "detail_definition": None,
        "auxiliary_definition": None,
        "position_sheet_m": [0.105, 0.205],
        "placement_box": _box(0.035, 0.145, 0.175, 0.265),
        "label": None,
    }


def _center_view(geometry_path: str) -> dict:
    view = copy.deepcopy(_base_view(geometry_path))
    view.update(
        {
            "id": "g0-centers",
            "purpose": "Expose the two-hole pattern and both symmetry axes for native G0 boundary qualification.",
            "model_evidence": _evidence(
                geometry_path,
                "The frozen 118 x 8 x 26 mm envelope and two circular faces support a bottom true-shape view.",
            ),
            "expressed_features": ["B0F18", "B0F20"],
            "orientation": {
                "kind": "standard_model_view",
                "standard_view": "bottom",
                "roll_angle_rad": 0.0,
            },
            "scale": 1.0,
            "scale_reason": "1:1 keeps both 7 mm holes and center elements legible.",
            "layout_reason": "The lower safe-zone box is disjoint from both upper views.",
            "position_sheet_m": [0.21, 0.075],
            "placement_box": _box(0.045, 0.025, 0.375, 0.125),
        }
    )
    view["rejected_alternatives"] = [
        {
            "alternative": "Omit the bottom view",
            "reason": "The circular holes would lose their direct true-shape center evidence.",
        }
    ]
    view["center_marks"] = [
            {
                "id": "cm-g0-hole-pair",
                "feature_ids": ["B0F18", "B0F20"],
                "selection_strategy": "visible_closed_circular_edges_by_feature",
                "deduplicate_by": "projected_center",
                "expected_count": 2,
                "style": "linear_group",
                "use_document_defaults": False,
                "show_lines": False,
                "propagate": False,
                "slot": False,
                "color_rgb": [255, 0, 0],
            }
        ]
    view["symmetry_centerlines"] = [
            {
                "id": "cl-g0-horizontal",
                "axis": "horizontal",
                "selection_strategy": "opposed_visible_linear_edges",
                "minimum_edge_span_ratio": 0.6,
                "purpose": "Persisted horizontal symmetry datum for G0 qualification.",
                "color_rgb": [255, 0, 0],
                "model_evidence": _evidence(
                    geometry_path, "The frozen part box is symmetric about this sheet axis."
                ),
            },
            {
                "id": "cl-g0-vertical",
                "axis": "vertical",
                "selection_strategy": "opposed_visible_linear_edges",
                "minimum_edge_span_ratio": 0.6,
                "purpose": "Persisted vertical symmetry datum for G0 qualification.",
                "color_rgb": [255, 0, 0],
                "model_evidence": _evidence(
                    geometry_path, "The frozen hole pair is symmetric about this sheet axis."
                ),
            },
        ]
    return view


def _auxiliary_view(geometry_path: str) -> dict:
    return {
        "id": "g0-auxiliary",
        "type": "auxiliary_view",
        "purpose": "Exercise the persisted auxiliary arrow and explicit managed view label boundary.",
        "model_evidence": _evidence(
            geometry_path,
            "Frozen edge endpoints provide an exact reference for the repository auxiliary transaction.",
        ),
        "expressed_features": [],
        "source": {
            "kind": "parent_view",
            "reference": "g0-main",
            "projection_direction": None,
        },
        "orientation": {"kind": "derived_from_parent"},
        "parent_view_id": "g0-main",
        "alignment": "not_aligned",
        "scale": 0.2,
        "scale_reason": "The child inherits the 1:5 parent scale.",
        "layout_reason": "The child occupies the disjoint upper-right safe-zone box.",
        "rejected_alternatives": [
            {
                "alternative": "Document-default auxiliary label",
                "reason": "An explicit managed label is required for exact native note extent qualification.",
            }
        ],
        "display_style": {
            "mode": "hidden_lines_removed",
            "faceted": False,
            "edges": True,
        },
        "hidden_lines": "removed",
        "tangent_edges": "removed",
        "center_marks": [],
        "symmetry_centerlines": [],
        "section_definition": None,
        "broken_out_definition": None,
        "detail_definition": None,
        "auxiliary_definition": {
            "reference_edge_start_model_m": [-0.059, 0.0005, 0.013],
            "reference_edge_end_model_m": [0.059, 0.0005, 0.013],
            "match_tolerance_sheet_m": 0.00001,
            "not_aligned": True,
            "show_arrow": True,
            "flip": True,
        },
        "position_sheet_m": [0.31, 0.205],
        "placement_box": _box(0.235, 0.145, 0.385, 0.265),
        "label": {
            "text": "G0-A",
            "show": True,
            "position_mode": "explicit",
            "position_sheet_m": [0.10529098974017003, 0.226980319227],
        },
    }


def _section_view(geometry_path: str) -> dict:
    return {
        "id": "g0-section",
        "type": "full_section",
        "purpose": "Expose a native cutting line, arrowheads, and section label for G0 boundary qualification.",
        "model_evidence": _evidence(
            geometry_path,
            "The two frozen cylindrical hole axes define one unambiguous cutting plane.",
        ),
        "expressed_features": ["B0F18", "B0F20"],
        "source": {
            "kind": "parent_view",
            "reference": "g0-main",
            "projection_direction": None,
        },
        "orientation": {"kind": "derived_from_parent"},
        "parent_view_id": "g0-main",
        "alignment": "projected",
        "scale": 1.0,
        "scale_reason": "The section inherits the 1:1 parent scale.",
        "layout_reason": "The section occupies the disjoint right safe-zone box.",
        "rejected_alternatives": [
            {
                "alternative": "Hidden lines in the parent",
                "reason": "Hidden lines do not exercise the required native section-symbol boundary.",
            }
        ],
        "display_style": {
            "mode": "hidden_lines_removed",
            "faceted": False,
            "edges": True,
        },
        "hidden_lines": "removed",
        "tangent_edges": "removed",
        "center_marks": [],
        "symmetry_centerlines": [],
        "section_definition": {
            "cutting_plane_mode": "through_feature_axes",
            "feature_ids": ["B0F18", "B0F20"],
            "cutting_line_points_model_m": [],
            "cutting_line_axis": "horizontal",
            "line_extension_ratio": 0.1,
            "reverse_direction": False,
            "section_depth_m": 0,
        },
        "broken_out_definition": None,
        "detail_definition": None,
        "auxiliary_definition": None,
        "position_sheet_m": [0.105, 0.225],
        "placement_box": _box(0.025, 0.17, 0.19, 0.275),
        "label": {"text": "G", "show": True, "position_mode": "document_default"},
    }


def build_plan(handoff: dict, plan_id: str, kind: str = "aux_center") -> dict:
    context = handoff["drawing_context"]
    geometry_path = handoff["geometry_report"]["path"]
    if kind == "title_block":
        main = _base_view(geometry_path)
        main["purpose"] = "Provide one associated model view while preserving the template title block."
        main["scale"] = 0.5
        main["scale_reason"] = "1:2 fits the model above the title block."
        main["position_sheet_m"] = [0.15, 0.19]
        main["placement_box"] = _box(0.03, 0.09, 0.27, 0.27)
        views = [main]
        final_set = [
            {
                "view_id": "g0-main",
                "omission_impact": "The drawing would no longer prove an associated model view and native title block together.",
            }
        ]
    elif kind == "full_section":
        main = _center_view(geometry_path)
        main["id"] = "g0-main"
        main["position_sheet_m"] = [0.105, 0.1]
        main["placement_box"] = _box(0.025, 0.04, 0.19, 0.16)
        views = [main, _section_view(geometry_path)]
        final_set = [
            {
                "view_id": "g0-main",
                "omission_impact": "The cutting plane would lose its true-shape parent opening view.",
            },
            {
                "view_id": "g0-section",
                "omission_impact": "The native section-symbol evidence would be lost.",
            },
        ]
    else:
        views = [
            _base_view(geometry_path),
            _auxiliary_view(geometry_path),
            _center_view(geometry_path),
        ]
        final_set = [
            {
                "view_id": "g0-main",
                "omission_impact": "The auxiliary view would lose its stable parent reference.",
            },
            {
                "view_id": "g0-auxiliary",
                "omission_impact": "The exact explicit auxiliary-label evidence would be lost.",
            },
            {
                "view_id": "g0-centers",
                "omission_impact": "The center-mark and symmetry-centerline evidence would be lost.",
            },
        ]
    return {
        "$schema": "https://local.example/schemas/solidworks/view-plan/1.4",
        "protocol_id": "solidworks-view-plan",
        "schema_version": "1.4",
        "plan_id": plan_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "producer": producer_contract_for_profile("production"),
        "execution_policy": {
            "on_integrity_mismatch": "fail",
            "on_selection_ambiguity": "fail",
            "on_unsupported_view": "fail",
            "on_layout_violation": "fail",
            "allow_source_model_write": False,
            "allow_source_drawing_overwrite": False,
            "transient_model_view_policy": "allow_in_memory_restore",
        },
        "model_path": handoff["model"]["path"],
        "model_sha256": handoff["model"]["sha256"],
        "drawing_path": handoff["blank_drawing"]["path"],
        "drawing_sha256": handoff["blank_drawing"]["sha256"],
        "geometry_report_path": geometry_path,
        "geometry_report_sha256": handoff["geometry_report"]["sha256"],
        "readiness_report_path": handoff["readiness_report"]["path"],
        "readiness_report_sha256": handoff["readiness_report"]["sha256"],
        "standard_view_images": copy.deepcopy(handoff["standard_view_images"]),
        "configuration": handoff["model"]["configuration"],
        "display_state": handoff["model"]["display_state"],
        "sheet": copy.deepcopy(context["sheet"]),
        "projection_method": context["projection_method"],
        "sheet_scale": copy.deepcopy(context["sheet_scale"]),
        "main_view_id": "g0-main",
        "views": views,
        "feature_coverage": [] if kind == "title_block" else [
            {
                "feature_id": feature_id,
                "feature_class": "circular_through_hole",
                "requirements": [
                    {
                        "requirement_id": "quantity-and-location",
                        "required_mode": "direct_opening_view",
                        "expression_mode": "direct_opening_view",
                        "satisfied_by": "g0-main" if kind == "full_section" else "g0-centers",
                        "status": "pass",
                    }
                ]
                + (
                    [
                        {
                            "requirement_id": "axial-profile",
                            "required_mode": "section_through_axis",
                            "expression_mode": "section_through_axis",
                            "satisfied_by": "g0-section",
                            "status": "pass",
                        }
                    ]
                    if kind == "full_section"
                    else []
                ),
            }
            for feature_id in ("B0F18", "B0F20")
        ],
        "inner_frame": copy.deepcopy(context["inner_frame"]),
        "clearance_policy": {
            "frame_clearance_m": 0.01,
            "single_layer_dimension_depth_m": 0.025,
            "multi_layer_dimension_depth_m": 0.035,
        },
        "dimension_zones": [],
        "reserved_zones": copy.deepcopy(context["reserved_zones"]),
        "decision_summary": {
            "main_orientation_comparison": [
                {
                    "candidate": "bottom",
                    "advantages": ["Shows both circular holes in true shape."],
                    "disadvantages": ["Requires an auxiliary child for the second G0 object class."],
                    "selected": True,
                },
                {
                    "candidate": "front",
                    "advantages": ["Shows the 8 mm thickness."],
                    "disadvantages": ["Collapses the hole openings."],
                    "selected": False,
                },
            ],
            "final_minimum_view_set": final_set,
            "scale_selection_process": (
                "1:2 preserves the template title block clearance."
                if kind == "title_block"
                else "1:1 preserves the hole pattern and section profile."
                if kind == "full_section"
                else "1:5 fits the auxiliary pair while 1:1 preserves center-element legibility."
            ),
            "layout_strategy": (
                "One model view occupies the upper-left sheet area."
                if kind == "title_block"
                else "Parent left and full section right, with no zone overlap."
                if kind == "full_section"
                else "Auxiliary pair above, independent center view below, with no zone overlap."
            ),
            "open_questions": [],
        },
        "assumptions": [],
        "open_questions": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument(
        "--kind", choices=("aux_center", "full_section", "title_block"), default="aux_center"
    )
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite candidate: {args.output}")
    handoff = json.loads(args.handoff.read_text(encoding="utf-8"))
    plan = build_plan(handoff, args.plan_id, args.kind)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
