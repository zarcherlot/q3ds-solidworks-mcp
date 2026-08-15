"""Publish, create and independently verify the three G7 derived-view sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
sys.path.insert(0, str(REPOSITORY_ROOT / "adapters" / "claude"))

from drawing_planner.planning_models import PlanningRequest  # noqa: E402
from semantic_models import ViewPlan  # noqa: E402
from server import (  # noqa: E402
    create_part_drawing_from_view_plan,
    publish_validated_part_drawing_view_plan,
    verify_part_drawing_view_plan,
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _call(payload: str, stage: str, scenario: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError(f"{scenario} {stage} failed: {payload}")
    return value


def run(manifest_path: Path, result_path: Path) -> dict[str, Any]:
    manifest = _load(manifest_path)
    result_file = result_path.resolve()
    if result_file.exists():
        raise FileExistsError(f"refusing to overwrite result: {result_file}")
    results: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        scenario = case["scenario"]
        plan_value = _load(Path(case["candidate_path"]))
        request_value = _load(Path(case["request_path"]))
        plan = ViewPlan(root=plan_value)
        request = PlanningRequest.model_validate(request_value)
        publish = _call(
            publish_validated_part_drawing_view_plan(plan=plan, request=request),
            "publish", scenario,
        )
        output = Path(case["output_path"]).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        create = _call(
            create_part_drawing_from_view_plan(
                plan=plan, request=request, output_path=str(output)
            ),
            "create", scenario,
        )
        verify = _call(
            verify_part_drawing_view_plan(
                plan=plan, request=request, output_path=str(output)
            ),
            "verify", scenario,
        )
        results.append({
            "scenario": scenario,
            "view_plan_path": str(
                (Path(case["publication_directory"]) / "view_plan.json").resolve()
            ),
            "planning_request": request.model_dump(mode="json"),
            "output_path": str(output),
            "verification_sidecar_path": str(Path(str(output) + ".verification.json")),
            "stages": {"publish": publish, "create": create, "verify": verify},
        })
    report = {"status": "complete", "cases": results}
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"result_path": str(result_file), "case_count": len(results)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
