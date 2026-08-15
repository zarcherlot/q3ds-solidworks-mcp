"""Build one immutable G0 matrix request from verified F7 case evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from drawing_layout_planner.g0_matrix import (  # noqa: E402
    build_matrix_request_from_f7,
    canonical_sha256,
    publish_json_once,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--f7-evidence-directory", type=Path, required=True)
    parser.add_argument("--matrix-root", type=Path, required=True)
    parser.add_argument("--matrix-id", required=True)
    parser.add_argument("--error-budget-m", type=float, default=0.0005)
    args = parser.parse_args()
    request = build_matrix_request_from_f7(
        args.f7_evidence_directory,
        args.matrix_root,
        matrix_id=args.matrix_id,
        error_budget_m=args.error_budget_m,
    )
    output = args.matrix_root.resolve() / "layout-g0-matrix-request.json"
    file_hash = publish_json_once(output, request)
    print(
        json.dumps(
            {
                "request_path": str(output),
                "request_file_sha256": file_hash,
                "request_canonical_sha256": canonical_sha256(request),
                "case_count": len(request["cases"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
