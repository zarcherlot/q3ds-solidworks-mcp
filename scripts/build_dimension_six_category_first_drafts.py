"""Write six immutable provisional DimensionPlan candidates from one complete handoff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dimension_planner.category_first_draft import (  # noqa: E402
    build_six_category_first_drafts,
)
from dimension_planner.f7_evidence import publish_json_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()

    output = Path(args.output_directory).resolve(strict=True)
    profile_path = Path(args.profile).resolve(strict=True)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    candidates, summary = build_six_category_first_drafts(
        Path(args.handoff), profile
    )
    written = []
    for category, result in candidates.items():
        category_directory = output / category
        category_directory.mkdir(exist_ok=False)
        plan_path, _ = publish_json_once(
            result["plan"], category_directory / "dimension_plan.candidate.json"
        )
        request_path, _ = publish_json_once(
            result["request"],
            category_directory / "dimension_planning_request.candidate.json",
        )
        validation_path, _ = publish_json_once(
            result["validation"],
            category_directory / "dimension_plan.candidate.validation.json",
        )
        written.append(
            {
                "category": category,
                "plan_path": str(plan_path),
                "request_path": str(request_path),
                "validation_path": str(validation_path),
            }
        )
    summary["written_candidates"] = written
    summary_path, _ = publish_json_once(
        summary, output / "six-category-first-draft.summary.json"
    )
    print(
        json.dumps(
            {
                "status": "candidate_output_ready",
                "summary_path": str(summary_path),
                "category_count": summary["category_count"],
                "dimension_kind_count": summary["dimension_kind_count"],
                "eligible_for_f7_promotion": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
