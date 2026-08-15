"""Publish final G0 qualification and a repository capability promotion candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drawing_layout_planner.g0_matrix import publish_json_once  # noqa: E402
from drawing_layout_planner.g0_qualification import (  # noqa: E402
    build_g0_qualification,
    promoted_capability_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-matrix", type=Path, required=True)
    parser.add_argument("--supplemental-evidence", type=Path, action="append", required=True)
    parser.add_argument("--qualification-output", type=Path, required=True)
    parser.add_argument("--promotion-output", type=Path, required=True)
    parser.add_argument("--qualification-id", required=True)
    args = parser.parse_args()
    qualification = build_g0_qualification(
        args.base_matrix,
        args.supplemental_evidence,
        qualification_id=args.qualification_id,
    )
    qualification_hash = publish_json_once(args.qualification_output, qualification)
    current = json.loads(
        (ROOT / "drawing_layout_planner/capabilities/current.json").read_text(encoding="utf-8")
    )
    promotion = promoted_capability_manifest(current, args.qualification_output)
    promotion_hash = publish_json_once(args.promotion_output, promotion)
    print(json.dumps({
        "qualification_path": str(args.qualification_output.resolve()),
        "qualification_sha256": qualification_hash,
        "promotion_path": str(args.promotion_output.resolve()),
        "promotion_sha256": promotion_hash,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
