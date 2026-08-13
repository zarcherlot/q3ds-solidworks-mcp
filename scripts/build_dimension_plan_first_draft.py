"""Build one evidence-bound DimensionPlan candidate and deterministic validation report."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dimension_planner.f7_evidence import publish_json_once  # noqa: E402
from dimension_planner.first_draft import build_first_draft_candidate  # noqa: E402
from dimension_planner.planning_models import canonical_json_sha256  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()
    try:
        output = Path(args.output_directory)
        if not output.is_absolute() or not output.is_dir():
            raise ValueError("output directory must be an existing absolute directory")
        candidate_path = output / "dimension_plan.candidate.json"
        validation_path = output / "dimension_plan.candidate.validation.json"
        recipe = json.loads(Path(args.recipe).resolve(strict=True).read_text(encoding="utf-8"))
        candidate, request, report = build_first_draft_candidate(
            Path(args.handoff), recipe
        )
        candidate_file, candidate_sha = publish_json_once(candidate, candidate_path)
        report["candidate"] = {
            "path": candidate_file,
            "file_sha256": candidate_sha,
            "canonical_sha256": canonical_json_sha256(candidate, "DimensionPlan"),
        }
        report["planning_request"] = request.model_dump(mode="json")
        report_file, report_sha = publish_json_once(report, validation_path)
        print(
            json.dumps(
                {
                    "ok": True,
                    "status": report["execution_readiness"],
                    "candidate_path": candidate_file,
                    "candidate_sha256": candidate_sha,
                    "validation_path": report_file,
                    "validation_sha256": report_sha,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(f"first-draft DimensionPlan failed closed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
