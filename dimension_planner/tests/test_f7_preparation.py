from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from dimension_planner import f7_preparation
from dimension_planner.f7_preparation import prepare_f7_live_matrix
from dimension_planner.handoff import file_sha256
from dimension_planner.planning_models import DimensionPlanningRequest
from dimension_planner.tests.test_f7_live_matrix import _fixture


ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_f7_preparation_contract_is_valid_draft_2020_12() -> None:
    schema = json.loads(
        (
            ROOT
            / "dimension_planner/contracts/dimension-f7-preparation-request.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)


def test_f7_preparer_publishes_six_plans_then_complete_matrix(
    tmp_path: Path, monkeypatch
) -> None:
    live_request = _fixture(tmp_path)
    by_handoff: dict[str, tuple[dict, DimensionPlanningRequest, dict]] = {}
    preparation_cases = []
    for index, case in enumerate(live_request["cases"]):
        plan_path = Path(case["plan_path"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_path.unlink()
        planning_request = DimensionPlanningRequest.model_validate(
            case["planning_request"]
        )
        handoff_path = Path(plan["handoff"]["path"])
        recipe_path = handoff_path.parent / f"f7-recipe-{index}.json"
        _write_json(
            recipe_path,
            {
                "protocol_id": "solidworks-dimension-first-draft-recipe",
                "schema_version": "1.1",
                "plan_id": plan["plan_id"],
                "created_at_utc": plan["created_at_utc"],
                "dimensions": [],
                "assumptions": [],
            },
        )
        by_handoff[str(handoff_path.resolve())] = (
            plan,
            planning_request,
            {"execution_readiness": "capability_blocked"},
        )
        preparation_cases.append(
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "handoff": {
                    "path": str(handoff_path.resolve()),
                    "sha256": file_sha256(handoff_path),
                },
                "recipe": {
                    "path": str(recipe_path.resolve()),
                    "sha256": file_sha256(recipe_path),
                },
                "output_path": case["output_path"],
                "evidence_path": case["evidence_path"],
            }
        )

    def fake_build(handoff_path: Path, recipe: dict):
        return by_handoff[str(handoff_path.resolve())]

    class FakeEngine:
        def validate_and_publish(self, plan: dict, request: DimensionPlanningRequest):
            path = Path(request.publication_directory) / "dimension_plan.json"
            _write_json(path, plan)
            return SimpleNamespace(
                status="published",
                execution_readiness="capability_blocked",
                plan=SimpleNamespace(path=str(path.resolve())),
                validation=SimpleNamespace(engineering_passed=True, issues=()),
            )

    monkeypatch.setattr(f7_preparation, "build_first_draft_candidate", fake_build)
    monkeypatch.setattr(f7_preparation, "DimensionPlannerEngine", FakeEngine)
    preparation = {
        "protocol_id": "solidworks-dimension-f7-preparation-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "f0_evidence": live_request["f0_evidence"],
        "matrix_request_output": str((tmp_path / "dimension-f7-matrix.json").resolve()),
        "cases": preparation_cases,
    }
    result = prepare_f7_live_matrix(preparation)
    assert result["status"] == "prepared"
    assert result["case_count"] == 6
    assert result["dimension_kind_count"] == 18
    assert result["execution_element_count"] == 6
    matrix = json.loads(Path(result["matrix_request_path"]).read_text(encoding="utf-8"))
    assert len(matrix["cases"]) == 6
    assert all(Path(case["plan_path"]).is_file() for case in matrix["cases"])
