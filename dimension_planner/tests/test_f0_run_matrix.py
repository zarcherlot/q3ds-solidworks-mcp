from __future__ import annotations

import json

from dimension_planner.f0_evidence import F0_CAPABILITY_IDS
from scripts.run_dimension_f0_live_probes import build_f0_run_matrix


def _evidence(source_kind: str = "research_model_drawing_pair") -> dict:
    checks = {
        "native_api_invoked": True,
        "in_memory_readback": True,
        "save_close_readonly_reopen": True,
        "stable_identity": True,
        "attachment_readback": False,
        "position_readback": True,
        "text_bounds_readback": False,
    }
    return {
        "source_kind": source_kind,
        "capabilities": [
            {
                "id": capability_id,
                "status": (
                    "unsupported"
                    if capability_id == "annotation_text_bounds"
                    else "planned"
                ),
                "checks": dict(checks),
                "evidence": (
                    [
                        json.dumps(
                            {
                                "stable_failure": {
                                    "expected_failure_observed": True
                                }
                            }
                        )
                    ]
                    if capability_id
                    in {
                        "linear_dimension",
                        "diameter_dimension",
                        "radius_dimension",
                        "hole_callout",
                        "chamfer_dimension",
                    }
                    else []
                ),
            }
            for capability_id in F0_CAPABILITY_IDS
        ],
    }


def test_research_matrix_reports_coverage_but_not_production_completion():
    matrix = build_f0_run_matrix([_evidence()])

    assert matrix["research_coverage_complete"] is True
    assert matrix["production_frozen_case_count"] == 0
    assert matrix["overall_status"] == "incomplete"
    radius = next(
        row for row in matrix["capabilities"] if row["id"] == "radius_dimension"
    )
    assert radius["research_coverage"] == "covered"
    assert radius["stable_failure_case_count"] == 1


def test_frozen_case_is_required_for_complete_matrix():
    matrix = build_f0_run_matrix(
        [_evidence(), _evidence("frozen_viewplan_drawing")]
    )

    assert matrix["production_frozen_case_count"] == 1
    assert matrix["overall_status"] == "complete"


def test_missing_positive_check_keeps_capability_partial():
    evidence = _evidence()
    radius = next(
        row
        for row in evidence["capabilities"]
        if row["id"] == "radius_dimension"
    )
    radius["checks"]["in_memory_readback"] = False

    matrix = build_f0_run_matrix([evidence])

    radius_matrix = next(
        row for row in matrix["capabilities"] if row["id"] == "radius_dimension"
    )
    assert radius_matrix["research_coverage"] == "partial"
    assert matrix["research_coverage_complete"] is False
