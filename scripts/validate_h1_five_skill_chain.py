"""Validate and publish one immutable H1 five-Skill production-chain evidence report."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from release_candidate.h1_chain_evidence import (  # noqa: E402
    validate_and_publish_h1_chain_evidence,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a production-only five-Skill chain and publish its H1 evidence."
    )
    parser.add_argument("--candidate", required=True, help="H1 evidence candidate JSON")
    parser.add_argument("--output", required=True, help="New H1 evidence output JSON")
    args = parser.parse_args()
    try:
        candidate = json.loads(Path(args.candidate).resolve(strict=True).read_text(encoding="utf-8"))
        result = validate_and_publish_h1_chain_evidence(
            candidate, Path(args.output).resolve()
        )
    except Exception as exc:
        print(f"H1 chain evidence failed closed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
