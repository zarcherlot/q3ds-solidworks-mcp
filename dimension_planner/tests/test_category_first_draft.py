from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from dimension_planner.category_first_draft import (
    CATEGORIES,
    DIMENSION_KINDS,
    build_six_category_first_drafts,
)
from dimension_planner.first_draft import DimensionFirstDraftError
from dimension_planner.tests.test_f3_deterministic_gates import (
    _fixture,
    _republish_handoff,
)


def _profile() -> dict:
    rows = []
    for category_index, category in enumerate(CATEGORIES):
        kinds = DIMENSION_KINDS[category_index * 3 : category_index * 3 + 3]
        rows.append(
            {
                "category": category,
                "plan_id": f"DP-DRAFT-{category.upper()}",
                "dimensions": [
                    {"kind": kind, "source_dimension_id": f"MD-{kind}"}
                    for kind in kinds
                ],
            }
        )
    return {
        "protocol_id": "solidworks-dimension-six-category-first-draft-profile",
        "schema_version": "1.0",
        "profile_id": "test-six-category-v1",
        "created_at_utc": "2026-08-13T09:30:00Z",
        "preferred_views": ["view-0", "view-1", "view-2"],
        "categories": rows,
        "assumptions": ["Proxy evidence for deterministic output only."],
    }


def _six_category_handoff(tmp_path: Path) -> Path:
    plan, request, handoff = _fixture(tmp_path)
    base_view = handoff["views"][0]
    base_zone = handoff["dimension_zones"][0]
    handoff["views"] = []
    handoff["dimension_zones"] = []
    for index in range(3):
        view = copy.deepcopy(base_view)
        view["view_id"] = f"view-{index}"
        view["solidworks_name"] = f"Q3DS_VP_view_{index}"
        x_min = 0.02 + index * 0.13
        x_max = x_min + 0.08
        view["bounds_sheet_m"] = [x_min, 0.1, x_max, 0.2]
        for entity_index, entity in enumerate(view["projected_geometry"]):
            entity["entity_id"] = f"GE-{index}-{entity_index}"
        zone = copy.deepcopy(base_zone)
        zone["id"] = f"DZ-view-{index}"
        zone["view_id"] = view["view_id"]
        zone["bounds_sheet_m"] = [x_min, 0.22, x_max, 0.27]
        handoff["views"].append(view)
        handoff["dimension_zones"].append(zone)
    handoff["model_driven_dimensions"] = [
        {
            "dimension_id": f"MD-{kind}",
            "full_name": f"D1@{kind}@part.SLDPRT",
            "value_si": (
                3.0
                if kind == "hole_quantity"
                else 0.523598776
                if kind == "angular"
                else 0.01 + index * 0.001
            ),
            "source_tier": "model_or_pmi",
            "provenance": "model_driven_dimension",
        }
        for index, kind in enumerate(DIMENSION_KINDS)
    ]
    request = _republish_handoff(plan, request, handoff)
    return Path(request.handoff_path)


def test_six_category_builder_emits_six_valid_candidates_and_all_kinds(
    tmp_path: Path,
) -> None:
    candidates, summary = build_six_category_first_drafts(
        _six_category_handoff(tmp_path), _profile()
    )
    assert tuple(candidates) == CATEGORIES
    assert summary["category_count"] == 6
    assert summary["dimension_kind_count"] == 18
    assert summary["category_evidence"] == "proxy"
    assert summary["eligible_for_f7_promotion"] is False
    observed = []
    for result in candidates.values():
        observed.extend(row["kind"] for row in result["plan"]["dimensions"])
        assert result["validation"]["engineering_passed"] is True
        assert result["validation"]["execution_readiness"] == "capability_blocked"
    assert tuple(observed) == DIMENSION_KINDS


def test_six_category_profile_cannot_claim_partial_or_reordered_coverage(
    tmp_path: Path,
) -> None:
    handoff = _six_category_handoff(tmp_path)
    profile = _profile()
    profile["categories"][0]["dimensions"][0]["kind"] = "diameter"
    with pytest.raises(DimensionFirstDraftError, match="18 DimensionPlan kinds"):
        build_six_category_first_drafts(handoff, profile)


def test_six_category_profile_is_strict(tmp_path: Path) -> None:
    profile = _profile()
    profile["unexpected"] = json.loads("true")
    with pytest.raises(DimensionFirstDraftError, match="properties must be exactly"):
        build_six_category_first_drafts(
            _six_category_handoff(tmp_path), profile
        )
