"""Run the private G1 read-only transaction for repository qualification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from drawing_layout_planner.handoff import (
    build_layout_handoff_request,
    file_sha256,
    validate_drawing_layout_handoff,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dimension-plan", type=Path, required=True)
    parser.add_argument("--dimensioned-drawing", type=Path, required=True)
    parser.add_argument("--verification-sidecar", type=Path, required=True)
    parser.add_argument("--publication-directory", type=Path, required=True)
    parser.add_argument("--execution-base-url", default="http://localhost:5000")
    args = parser.parse_args()
    request = build_layout_handoff_request(
        args.dimension_plan,
        args.dimensioned_drawing,
        args.verification_sidecar,
        args.publication_directory,
    )
    with httpx.Client(trust_env=False) as client:
        response = client.post(
            args.execution_base_url.rstrip("/") + "/api/layout-planning/handoff",
            json=request,
            timeout=240,
        )
    payload = response.json()
    if response.status_code != 200:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    handoff_path = Path(payload["handoff_path"])
    handoff = validate_drawing_layout_handoff(
        json.loads(handoff_path.read_text(encoding="utf-8"))
    )
    if file_sha256(handoff_path) != payload["handoff_sha256"]:
        raise RuntimeError("published handoff SHA-256 differs from executor result")
    print(
        json.dumps(
            {
                "status": handoff["status"],
                "handoff_id": handoff["handoff_id"],
                "handoff_path": str(handoff_path),
                "handoff_sha256": payload["handoff_sha256"],
                "object_count": len(handoff["objects"]),
                "dimension_count": handoff["dimension_semantics"]["planned_count"],
                "unsupported_capabilities": handoff["boundary_capabilities"][
                    "unsupported"
                ],
                "source_immutability": handoff["source_immutability"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
