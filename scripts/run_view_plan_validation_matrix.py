"""Run the repository-owned D1 offline/integration validation matrix."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--lanes",
        nargs="+",
        choices=("offline", "integration", "live"),
        default=("offline", "integration"),
    )
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--host-preflight-report")
    args = parser.parse_args()

    root = _repository_root()
    sys.path.insert(0, str(root))
    from drawing_planner.validation_matrix import (  # noqa: PLC0415
        default_cases,
        run_validation_matrix,
    )

    output = Path(args.output_dir)
    if not output.is_absolute():
        output = root / output
    selected_lanes = set(args.lanes)
    if "live" in selected_lanes and not args.host_preflight_report:
        parser.error("--host-preflight-report is required when the live lane is selected")
    preflight = None
    if args.host_preflight_report:
        preflight = Path(args.host_preflight_report)
        if not preflight.is_absolute():
            preflight = root / preflight
        preflight = preflight.resolve()
    cases = tuple(
        case
        for case in default_cases(
            root,
            output.resolve(),
            python_executable=Path(args.python_executable).resolve(),
            host_preflight_report=preflight,
        )
        if case.lane in selected_lanes
    )
    try:
        report = run_validation_matrix(root, output, cases)
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"D1 matrix setup failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
