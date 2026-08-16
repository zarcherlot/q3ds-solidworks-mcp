"""Create, append to, and finalize one H3 five-Skill evidence-capture session."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from release_candidate.h3_session_capture import (  # noqa: E402
    capture_h3_operation,
    capture_h3_stage,
    create_h3_session,
    finalize_h3_session,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage an append-only H3 five-Skill response-capture session."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create a session from a ready H2 preflight")
    create.add_argument("--preflight", required=True)
    create.add_argument("--preflight-sha256", required=True)
    create.add_argument("--repository-root", default=str(REPOSITORY_ROOT))

    operation = commands.add_parser(
        "capture-operation", help="Append the exact next semantic response"
    )
    _session_arguments(operation)
    operation.add_argument("--tool", required=True)
    operation.add_argument("--response", required=True, help="Semantic response JSON")

    stage = commands.add_parser(
        "capture-stage", help="Freeze one completed stage's input/output artifacts"
    )
    _session_arguments(stage)
    stage.add_argument("--order", required=True, type=int)
    stage.add_argument(
        "--artifacts",
        required=True,
        help="JSON object containing exact inputs and outputs role/path arrays",
    )

    finalize = commands.add_parser(
        "finalize", help="Build the strict H1 candidate after all captures succeed"
    )
    _session_arguments(finalize)
    return parser


def _session_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-manifest", required=True)
    parser.add_argument("--session-sha256", required=True)


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "create":
            result = create_h3_session(
                Path(args.preflight),
                args.preflight_sha256,
                Path(args.repository_root),
            )
        elif args.command == "capture-operation":
            response = _load_json(Path(args.response))
            result = capture_h3_operation(
                Path(args.session_manifest),
                args.session_sha256,
                args.tool,
                response,
            )
        elif args.command == "capture-stage":
            artifacts = _load_json(Path(args.artifacts))
            if set(artifacts) != {"inputs", "outputs"}:
                raise ValueError("stage artifacts JSON must contain exactly inputs and outputs")
            result = capture_h3_stage(
                Path(args.session_manifest),
                args.session_sha256,
                args.order,
                artifacts["inputs"],
                artifacts["outputs"],
            )
        else:
            result = finalize_h3_session(
                Path(args.session_manifest), args.session_sha256
            )
    except Exception as exc:
        print(f"H3 session failed closed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


def _load_json(path: Path) -> dict:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON file must contain an object: {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
