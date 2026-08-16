"""Run and append-capture exactly one H4 production semantic operation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release_candidate.h4_semantic_step import (  # noqa: E402
    H4SemanticStepError,
    load_h4_step_request,
    run_h4_semantic_step,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Invoke only the next H3-authorized production semantic MCP tool and "
            "append-capture its response."
        )
    )
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--request-sha256", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--diagnostics", type=Path)
    return parser


async def _run(args: argparse.Namespace) -> dict:
    request = load_h4_step_request(args.request, args.request_sha256)
    return await run_h4_semantic_step(
        request,
        timeout_seconds=args.timeout_seconds,
        diagnostics_path=args.diagnostics,
    )


def main() -> int:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_run(args))
    except (H4SemanticStepError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "status": "rejected", "error": str(exc)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
