"""Validate and publish one complete H5-H9 five-Skill release candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release_candidate.h5_h9_release_closure import (  # noqa: E402
    H5H9ReleaseClosureError,
    build_and_publish_h5_h9_release_candidate,
    load_h5_h9_release_request,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit H5-H9 and publish one immutable five-Skill release candidate."
    )
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        request = load_h5_h9_release_request(args.request, args.request_sha256)
        result = build_and_publish_h5_h9_release_candidate(
            request, args.repository_root, args.output
        )
    except (H5H9ReleaseClosureError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
