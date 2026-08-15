"""Build immutable ViewPlan candidates used by the G7 derived-view matrix cases."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _publish(path: Path, value: dict[str, Any]) -> None:
    path = path.resolve()
    if path.exists():
        raise FileExistsError(f"refusing to overwrite candidate: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _request(plan: dict[str, Any], publication: Path, handoff: Path) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "handoff_manifest_path": str(handoff.resolve(strict=True)),
        "handoff_manifest_sha256": _sha256(handoff.resolve(strict=True)),
        "planner_profile": "production",
        "debug_prompt_directory": None,
        "publication_directory": str(publication.resolve()),
        "user_requirements": {
            "source_drawing_read_only": True,
            "g7_qualification_view_types": [row["type"] for row in plan["views"]],
        },
    }


def _copy_initializer_bundle(
    plan: dict[str, Any], source_handoff: Path, publication: Path
) -> Path:
    manifest = _load(source_handoff)
    publication.mkdir(parents=True, exist_ok=False)
    original_geometry_path = str(plan["geometry_report_path"])
    mapping = (
        ("blank_drawing", "drawing_path"),
        ("readiness_report", "readiness_report_path"),
        ("geometry_report", "geometry_report_path"),
    )
    for role, plan_field in mapping:
        source = Path(manifest[role]["path"]).resolve(strict=True)
        target = publication / source.name
        shutil.copy2(source, target)
        manifest[role]["path"] = str(target.resolve())
        manifest[role]["sha256"] = _sha256(target)
        plan[plan_field] = str(target.resolve())
        hash_field = plan_field.replace("_path", "_sha256")
        plan[hash_field] = manifest[role]["sha256"]
    copied_images: list[dict[str, Any]] = []
    for row in manifest["standard_view_images"]:
        source = Path(row["path"]).resolve(strict=True)
        target = publication / source.name
        shutil.copy2(source, target)
        copied = dict(row)
        copied["path"] = str(target.resolve())
        copied["sha256"] = _sha256(target)
        copied_images.append(copied)
    manifest["standard_view_images"] = copied_images
    plan["standard_view_images"] = copy.deepcopy(copied_images)
    _replace_string(plan, original_geometry_path, plan["geometry_report_path"])
    copied_handoff = publication / "drawing-planning-handoff.json"
    _publish(copied_handoff, manifest)
    return copied_handoff


def _replace_string(value: Any, old: str, new: str) -> None:
    if isinstance(value, dict):
        for key, child in list(value.items()):
            if child == old:
                value[key] = new
            else:
                _replace_string(child, old, new)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if child == old:
                value[index] = new
            else:
                _replace_string(child, old, new)


def _zone(
    view_id: str, zone_id: str, bounds: dict[str, float], side: str = "bottom"
) -> dict[str, Any]:
    return {
        "id": zone_id,
        "view_id": view_id,
        "side": side,
        "dimension_layers": 1,
        "required_depth_m": 0.025,
        "bounds_sheet_m": bounds,
    }


def _base(template: dict[str, Any], plan_id: str) -> dict[str, Any]:
    result = copy.deepcopy(template)
    result["plan_id"] = plan_id
    result["created_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result["assumptions"] = list(result.get("assumptions", [])) + [
        "This derived-view candidate is reserved for the repository G7 live qualification matrix."
    ]
    return result


def build(section_template: Path, auxiliary_template: Path, output_root: Path) -> dict[str, Any]:
    root = output_root.resolve()
    if root.exists():
        raise FileExistsError(f"output root must be new: {root}")
    section_source = _load(section_template)
    auxiliary_source = _load(auxiliary_template)

    section = _base(section_source, "VP-G7-SECTION-20260815")
    section["dimension_zones"] = [
        _zone("g0-main", "dz-g7-section-main-right", {
            "x_min_m": 0.19, "y_min_m": 0.05,
            "x_max_m": 0.215, "y_max_m": 0.15,
        }, "right")
    ]

    auxiliary = _base(auxiliary_source, "VP-G7-AUXILIARY-20260815")
    auxiliary["views"][0]["placement_box"] = {
        "x_min_m": 0.06, "y_min_m": 0.175,
        "x_max_m": 0.15, "y_max_m": 0.235,
    }
    auxiliary["views"][1]["placement_box"] = {
        "x_min_m": 0.27, "y_min_m": 0.145,
        "x_max_m": 0.35, "y_max_m": 0.265,
    }
    auxiliary["views"][2]["placement_box"] = {
        "x_min_m": 0.14, "y_min_m": 0.025,
        "x_max_m": 0.28, "y_max_m": 0.115,
    }
    density_view = copy.deepcopy(auxiliary["views"][1])
    density_view.update({
        "id": "g7-auxiliary-density",
        "position_sheet_m": [0.205, 0.205],
        "placement_box": {
            "x_min_m": 0.18, "y_min_m": 0.175,
            "x_max_m": 0.23, "y_max_m": 0.235,
        },
        "scale": 0.08,
        "scale_reason": (
            "A compact independent auxiliary view provides five additional "
            "native model-dimension imports for the G7 high-density case."
        ),
        "label": {
            "show": True,
            "text": "G7-HD",
            "position_mode": "document_default",
        },
        "purpose": (
            "Provide a second native auxiliary projection so the verified "
            "dimension source contains at least twelve real dimensions."
        ),
        "layout_reason": "The compact child occupies the gap between the two upper views.",
    })
    auxiliary["views"].append(density_view)
    auxiliary["dimension_zones"] = [
        _zone("g0-main", "dz-g7-aux-main-right", {
            "x_min_m": 0.15, "y_min_m": 0.155,
            "x_max_m": 0.175, "y_max_m": 0.255,
        }, "right"),
        _zone("g0-centers", "dz-g7-aux-centers-right", {
            "x_min_m": 0.28, "y_min_m": 0.035,
            "x_max_m": 0.305, "y_max_m": 0.115,
        }, "right"),
        _zone("g0-auxiliary", "dz-g7-aux-derived-left", {
            "x_min_m": 0.245, "y_min_m": 0.155,
            "x_max_m": 0.27, "y_max_m": 0.255,
        }, "left"),
        _zone("g7-auxiliary-density", "dz-g7-aux-density-bottom", {
            "x_min_m": 0.18, "y_min_m": 0.12,
            "x_max_m": 0.23, "y_max_m": 0.145,
        }, "bottom"),
    ]
    auxiliary["decision_summary"]["final_minimum_view_set"].append({
        "view_id": "g7-auxiliary-density",
        "omission_impact": (
            "The G7 high-density source would contain fewer than twelve "
            "independently verified native dimensions."
        ),
    })

    detail = _base(section_source, "VP-G7-DETAIL-20260815")
    main = copy.deepcopy(detail["views"][0])
    detail_view = copy.deepcopy(detail["views"][1])
    detail_view.update({
        "id": "g7-detail",
        "type": "detail_view",
        "purpose": "Magnify the right-hand through-hole for the G7 native detail-view case.",
        "expressed_features": ["B0F20"],
        "source": {"kind": "parent_view", "reference": "g0-main", "projection_direction": None},
        "parent_view_id": "g0-main",
        "alignment": "none",
        "orientation": {"kind": "derived_from_parent"},
        "section_definition": None,
        "broken_out_definition": None,
        "detail_definition": {
            "profile_mode": "circle",
            "center_offset_from_parent_m": [0.025, 0.0],
            "radius_sheet_m": 0.012,
            "style": "standard",
            "show_type": "circle",
            "full_outline": True,
            "jagged_outline": False,
            "no_outline": False,
            "shape_intensity": 3,
        },
        "auxiliary_definition": None,
        "position_sheet_m": [0.30, 0.215],
        "placement_box": {
            "x_min_m": 0.22, "y_min_m": 0.15,
            "x_max_m": 0.39, "y_max_m": 0.275,
        },
        "label": {"show": True, "text": "D", "position_mode": "document_default"},
        "layout_reason": "The detail occupies the disjoint upper-right safe-zone box.",
        "scale": 2.0,
        "scale_reason": "2:1 exposes the selected hole without changing model semantics.",
        "rejected_alternatives": [{
            "alternative": "Omit the detail view",
            "reason": "The G7 matrix requires a native detail-view save/reopen case.",
        }],
    })
    detail["views"] = [main, detail_view]
    detail["dimension_zones"] = [
        _zone("g0-main", "dz-g7-detail-main-right", {
            "x_min_m": 0.19, "y_min_m": 0.05,
            "x_max_m": 0.215, "y_max_m": 0.15,
        }, "right")
    ]
    detail["feature_coverage"] = [
        {
            **copy.deepcopy(row),
            "requirements": [
                requirement for requirement in copy.deepcopy(row["requirements"])
                if requirement["satisfied_by"] == "g0-main"
            ],
        }
        for row in detail["feature_coverage"]
    ]
    detail["decision_summary"]["final_minimum_view_set"] = [
        {
            "view_id": "g0-main",
            "omission_impact": "The detail profile would lose its parent opening view.",
        },
        {
            "view_id": "g7-detail",
            "omission_impact": "The G7 native detail-view case would be absent.",
        },
    ]
    detail["decision_summary"]["layout_strategy"] = (
        "Parent left and detail upper-right, with disjoint placement boxes."
    )

    result: dict[str, Any] = {"cases": []}
    for scenario, plan, source in (
        ("section_view", section, section_template),
        ("auxiliary_view", auxiliary, auxiliary_template),
        ("detail_view", detail, section_template),
    ):
        candidate = root / "candidates" / f"{scenario}.view-plan.candidate.json"
        publication = root / "views" / scenario
        request_path = root / "candidates" / f"{scenario}.planning-request.json"
        source_handoff = source.resolve(strict=True).parent / "drawing-planning-handoff.json"
        handoff = _copy_initializer_bundle(plan, source_handoff, publication)
        request = _request(plan, publication, handoff)
        _publish(candidate, plan)
        _publish(request_path, request)
        result["cases"].append({
            "scenario": scenario,
            "candidate_path": str(candidate.resolve()),
            "request_path": str(request_path.resolve()),
            "publication_directory": str(publication.resolve()),
            "output_path": str((root / "view-drawings" / f"g7-{scenario}.SLDDRW").resolve()),
        })
    manifest = root / "g7-special-view-candidates.json"
    _publish(manifest, result)
    return {"manifest_path": str(manifest.resolve()), **result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section-template", type=Path, required=True)
    parser.add_argument("--auxiliary-template", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.section_template, args.auxiliary_template, args.output_root),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
