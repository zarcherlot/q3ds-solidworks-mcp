"""Publish one COM-free H2 five-Skill production-session preflight."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from release_candidate.h2_session_preflight import (  # noqa: E402
    build_and_publish_h2_session_preflight,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze and preflight a future five-Skill production session."
    )
    parser.add_argument("--request", required=True, help="H2 session request JSON")
    parser.add_argument("--output", required=True, help="New H2 preflight report JSON")
    parser.add_argument("--repository-root", default=str(REPOSITORY_ROOT))
    args = parser.parse_args()
    try:
        request = json.loads(
            Path(args.request).resolve(strict=True).read_text(encoding="utf-8")
        )
        result = build_and_publish_h2_session_preflight(
            request,
            Path(args.repository_root),
            Path(args.output),
        )
    except Exception as exc:
        print(f"H2 session preflight failed closed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
