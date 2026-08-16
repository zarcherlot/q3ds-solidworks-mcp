"""Publish one COM-free H0 five-Skill release-readiness report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from release_candidate.h0_readiness import audit_h0_readiness  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether the repository may enter the H0 production live chain."
    )
    parser.add_argument("--repository-root", default=str(REPOSITORY_ROOT))
    parser.add_argument("--output", required=True, help="New immutable JSON report path")
    args = parser.parse_args()
    try:
        root = Path(args.repository_root).resolve(strict=True)
        output = Path(args.output).resolve()
        if output.exists():
            raise FileExistsError(f"H0 readiness report already exists: {output}")
        if not output.parent.is_dir():
            raise FileNotFoundError(f"H0 report parent does not exist: {output.parent}")
        report = audit_h0_readiness(root)
        _publish_once(report, output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ready" else 2
    except Exception as exc:
        print(f"H0 release-readiness audit failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


def _publish_once(report: dict, output: Path) -> None:
    payload = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
