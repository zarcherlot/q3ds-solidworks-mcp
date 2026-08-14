"""Build one strict, hash-bound F1 dimension planning handoff request."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dimension_planner.handoff import build_handoff_request, file_sha256  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view-plan", type=Path, required=True)
    parser.add_argument("--verified-drawing", type=Path, required=True)
    parser.add_argument("--verification-sidecar", type=Path, required=True)
    parser.add_argument("--publication-directory", type=Path, required=True)
    parser.add_argument("--approved-user-inputs", type=Path)
    parser.add_argument("--output-request", type=Path, required=True)
    args = parser.parse_args()

    approved_inputs: list[dict] = []
    if args.approved_user_inputs:
        value = json.loads(args.approved_user_inputs.read_text(encoding="utf-8"))
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError("approved-user-inputs must contain one JSON array of objects")
        approved_inputs = value

    output = args.output_request.resolve()
    if output.exists() or output.suffix.lower() != ".json" or not output.parent.is_dir():
        raise ValueError("output-request must be a new .json file in an existing directory")
    request = build_handoff_request(
        args.view_plan,
        args.verified_drawing,
        args.verification_sidecar,
        args.publication_directory,
        approved_user_inputs=approved_inputs,
    )
    temporary = output.with_name("." + output.name + ".tmp")
    if temporary.exists():
        raise ValueError(f"temporary request path already exists: {temporary}")
    temporary.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output)
    print(
        json.dumps(
            {
                "status": "ready",
                "request_path": str(output),
                "request_sha256": file_sha256(output),
                "publication_directory": request["publication_directory"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
