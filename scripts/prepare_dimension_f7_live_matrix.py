"""Prepare and publish the complete immutable F7 live-matrix request."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dimension_planner.f7_preparation import prepare_f7_live_matrix  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare six real F7 cases with exact 18-kind/6-element coverage."
    )
    parser.add_argument("--request", required=True, help="F7 preparation request JSON")
    args = parser.parse_args()
    try:
        request_path = Path(args.request).resolve(strict=True)
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = prepare_f7_live_matrix(request)
    except Exception as exc:
        print(f"F7 preparation failed closed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
