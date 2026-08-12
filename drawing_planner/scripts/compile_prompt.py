#!/usr/bin/env python3
"""Compile a strict repository PlannerEngine prompt request without calling a model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from drawing_planner.prompt_pipeline import compile_prompt_request  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path, help="UTF-8 request JSON")
    parser.add_argument("--output", type=Path, help="New prompt-envelope JSON path")
    args = parser.parse_args()

    request = json.loads(args.request.read_text(encoding="utf-8-sig"))
    if not isinstance(request, dict):
        raise ValueError("prompt request must be a JSON object")
    envelope = compile_prompt_request(request)
    rendered = json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
        return 0
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite prompt envelope: {args.output}")
    if not args.output.parent.is_dir():
        raise FileNotFoundError(f"output parent does not exist: {args.output.parent}")
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
