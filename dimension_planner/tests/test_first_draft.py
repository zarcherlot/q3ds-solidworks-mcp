from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from dimension_planner.first_draft import (
    DimensionFirstDraftError,
    build_first_draft_candidate,
)
from dimension_planner.tests.test_f3_deterministic_gates import (
    _fixture,
    _republish_handoff,
)


def _recipe(plan: dict) -> dict:
    source = plan["dimensions"][0]
    return {
        "protocol_id": "solidworks-dimension-first-draft-recipe",
        "schema_version": "1.0",
        "plan_id": "DP-FIRST-DRAFT-1",
        "created_at_utc": "2026-08-13T09:00:00Z",
        "dimensions": [
            {
                "dimension_id": source["dimension_id"],
                "kind": source["kind"],
                "source_dimension_id": source["source"]["source_ids"][0],
                "target_view_id": source["target_view_id"],
                "attachments": [
                    {
                        "attachment_id": row["attachment_id"],
                        "entity_id": row["entity_id"],
                        "role": row["role"],
                    }
                    for row in source["attachments"]
                ],
                "feature_ids": source["feature_ids"],
                "dimension_zone_id": source["dimension_zone_id"],
                "initial_position_sheet_m": source["initial_position_sheet_m"],
                "display_format": source["display_format"],
                "hierarchy": source["hierarchy"],
                "verification_tolerance": source["verification_tolerance"],
            }
        ],
        "assumptions": ["First evidence-bound draft."],
    }


def test_first_draft_fills_only_frozen_values_references_and_bindings(
    tmp_path: Path,
) -> None:
    plan, request, handoff = _fixture(tmp_path)
    request = _republish_handoff(plan, request, handoff)
    candidate, actual_request, report = build_first_draft_candidate(
        Path(request.handoff_path), _recipe(plan)
    )
    dimension = candidate["dimensions"][0]
    assert dimension["value"]["nominal_si"] == 0.01
    assert dimension["attachments"][0]["model_persistent_reference"] == "AQID"
    assert candidate["handoff"]["sha256"] == actual_request.handoff_sha256
    assert report["engineering_passed"] is True
    assert report["execution_readiness"] == "capability_blocked"


def test_first_draft_refuses_missing_source_entity_feature_and_zone(
    tmp_path: Path,
) -> None:
    plan, request, handoff = _fixture(tmp_path)
    request = _republish_handoff(plan, request, handoff)
    base = _recipe(plan)
    mutations = (
        ("source_dimension_id", "MD-missing", "source dimension is absent"),
        ("feature_ids", ["MF-missing"], "manufacturing features are absent"),
        ("dimension_zone_id", "DZ-missing", "dimension zone is absent"),
    )
    for field, value, message in mutations:
        recipe = copy.deepcopy(base)
        recipe["dimensions"][0][field] = value
        with pytest.raises(DimensionFirstDraftError, match=message):
            build_first_draft_candidate(Path(request.handoff_path), recipe)

    recipe = copy.deepcopy(base)
    recipe["dimensions"][0]["attachments"][0]["entity_id"] = "GE-missing"
    with pytest.raises(DimensionFirstDraftError, match="not visible"):
        build_first_draft_candidate(Path(request.handoff_path), recipe)


def test_first_draft_recipe_is_strict_json(tmp_path: Path) -> None:
    plan, request, handoff = _fixture(tmp_path)
    request = _republish_handoff(plan, request, handoff)
    recipe = _recipe(plan)
    recipe["unexpected"] = True
    with pytest.raises(DimensionFirstDraftError, match="properties must be exactly"):
        build_first_draft_candidate(Path(request.handoff_path), recipe)
