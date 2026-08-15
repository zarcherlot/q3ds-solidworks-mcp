"""Run the private read-only G0 probe and apply repository evidence gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from jsonschema import Draft202012Validator

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from drawing_layout_planner.g0_evidence import (  # noqa: E402
    G0_CAPABILITY_IDS,
    evaluate_g0_evidence,
)


REQUEST_SCHEMA = (
    REPOSITORY_ROOT
    / "drawing_layout_planner"
    / "contracts"
    / "layout-boundary-probe.schema.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    return {"path": str(resolved), "sha256": _sha256(resolved)}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dimension-plan", type=Path)
    parser.add_argument("--dimensioned-drawing", type=Path)
    parser.add_argument("--view-plan", type=Path)
    parser.add_argument("--view-drawing", type=Path)
    parser.add_argument("--layout-fixture-manifest", type=Path)
    parser.add_argument("--fixture-drawing", type=Path)
    parser.add_argument("--verification-sidecar", type=Path, required=True)
    parser.add_argument("--publication-directory", type=Path, required=True)
    parser.add_argument("--execution-base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--error-budget-m", type=float, default=0.0005)
    args = parser.parse_args()
    dimension = args.dimension_plan is not None or args.dimensioned_drawing is not None
    view = args.view_plan is not None or args.view_drawing is not None
    fixture = (
        args.layout_fixture_manifest is not None or args.fixture_drawing is not None
    )
    if sum((dimension, view, fixture)) != 1:
        parser.error("supply exactly one complete dimension, ViewPlan, or fixture source pair")
    if dimension and (args.dimension_plan is None or args.dimensioned_drawing is None):
        parser.error("--dimension-plan and --dimensioned-drawing are required together")
    if view and (args.view_plan is None or args.view_drawing is None):
        parser.error("--view-plan and --view-drawing are required together")
    if fixture and (
        args.layout_fixture_manifest is None or args.fixture_drawing is None
    ):
        parser.error("--layout-fixture-manifest and --fixture-drawing are required together")
    return args


def main() -> int:
    args = _parse_args()
    publication = args.publication_directory.resolve()
    if publication.exists() and any(publication.iterdir()):
        raise SystemExit(f"publication directory must be new or empty: {publication}")
    if args.layout_fixture_manifest is not None:
        source = {
            "kind": "verified_layout_fixture",
            "layout_fixture_manifest": _artifact(args.layout_fixture_manifest),
            "fixture_drawing": _artifact(args.fixture_drawing),
            "source_verification_sidecar": _artifact(args.verification_sidecar),
        }
    elif args.view_plan is not None:
        source = {
            "kind": "verified_view_plan_drawing",
            "view_plan": _artifact(args.view_plan),
            "view_drawing": _artifact(args.view_drawing),
            "view_verification_sidecar": _artifact(args.verification_sidecar),
        }
    else:
        source = {
            "kind": "verified_dimension_drawing",
            "dimension_plan": _artifact(args.dimension_plan),
            "dimensioned_drawing": _artifact(args.dimensioned_drawing),
            "dimension_verification_sidecar": _artifact(args.verification_sidecar),
        }
    request_value = {
        "protocol_id": "solidworks-layout-boundary-probe",
        "schema_version": "1.0",
        "source": source,
        "publication_directory": str(publication),
        "required_solidworks_revision": "33.5.0",
        "error_budget_m": args.error_budget_m,
        "capability_ids": list(G0_CAPABILITY_IDS),
    }
    schema = json.loads(REQUEST_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(request_value)
    before = {
        role: row["sha256"] for role, row in request_value["source"].items()
        if isinstance(row, dict) and "sha256" in row
    }
    body = json.dumps(request_value, ensure_ascii=False).encode("utf-8")
    endpoint = args.execution_base_url.rstrip("/") + "/api/research/layout-boundary-probe"
    http_request = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(http_request, timeout=args.timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise SystemExit(f"G0 endpoint returned HTTP {error.code}: {detail}") from error
    if result.get("status") != "pass":
        raise SystemExit("G0 probe did not pass: " + json.dumps(result, ensure_ascii=False))

    evidence_path = Path(result["evidence_path"]).resolve(strict=True)
    if _sha256(evidence_path) != result["evidence_sha256"]:
        raise SystemExit("evidence file hash does not match the execution response")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evaluation = evaluate_g0_evidence(evidence, source_request=request_value)
    after = {
        role: _sha256(Path(row["path"]))
        for role, row in request_value["source"].items()
        if isinstance(row, dict) and "sha256" in row
    }
    if before != after:
        raise SystemExit("one or more frozen G0 inputs changed outside the evidence report")
    summary = {
        "status": evaluation.overall_status,
        "evidence_path": str(evidence_path),
        "evidence_sha256": evaluation.evidence_sha256,
        "capability_statuses": dict(evaluation.capability_statuses),
        "blockers": list(evaluation.blockers),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if evaluation.overall_status in {"complete", "incomplete"} else 2


if __name__ == "__main__":
    sys.exit(main())
