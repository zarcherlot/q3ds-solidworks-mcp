"""Build one hash-bound frozen ViewPlan request for the F0 live probe.

This utility only reads already-published upstream artifacts and writes a new
probe request. SolidWorks access remains inside the C# Execution Service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dimension_planner.f0_evidence import F0_CAPABILITY_IDS  # noqa: E402
from drawing_planner.planning_models import canonical_json_sha256  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _absolute_file(path: Path, suffix: str, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.suffix.lower() != suffix.lower():
        raise ValueError(f"{label} must be an existing {suffix} file: {resolved}")
    return resolved


def build_request(
    view_plan_path: Path,
    verified_drawing_path: Path,
    verification_sidecar_path: Path,
    publication_directory: Path,
) -> dict:
    view_plan = _absolute_file(view_plan_path, ".json", "view plan")
    drawing = _absolute_file(verified_drawing_path, ".slddrw", "verified drawing")
    sidecar = _absolute_file(
        verification_sidecar_path, ".json", "verification sidecar"
    )
    publication = publication_directory.resolve()
    if publication.exists():
        raise ValueError(f"publication directory must not exist: {publication}")

    plan_value = json.loads(view_plan.read_text(encoding="utf-8"))
    sidecar_value = json.loads(sidecar.read_text(encoding="utf-8"))
    drawing_sha256 = _sha256(drawing)
    if Path(sidecar_value.get("output_path", "")).resolve() != drawing:
        raise ValueError("verification sidecar output_path does not match the drawing")
    if sidecar_value.get("artifact_sha256") != drawing_sha256:
        raise ValueError("verification sidecar artifact_sha256 does not match the drawing")
    plan_sha256 = canonical_json_sha256(plan_value, "view plan")
    if sidecar_value.get("plan_canonical_sha256") != plan_sha256:
        raise ValueError("verification sidecar plan hash does not match the ViewPlan")
    if sidecar_value.get("verified") is not True:
        raise ValueError("verification sidecar is not marked verified")

    return {
        "protocol_id": "solidworks-dimension-api-probe",
        "schema_version": "1.0",
        "source": {
            "kind": "frozen_viewplan_drawing",
            "view_plan": {"path": str(view_plan), "sha256": _sha256(view_plan)},
            "verified_drawing": {
                "path": str(drawing),
                "sha256": drawing_sha256,
            },
            "verification_sidecar": {
                "path": str(sidecar),
                "sha256": _sha256(sidecar),
            },
        },
        "publication_directory": str(publication),
        "required_solidworks_revision": "33.5.0",
        "capability_ids": list(F0_CAPABILITY_IDS),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view-plan", type=Path, required=True)
    parser.add_argument("--verified-drawing", type=Path, required=True)
    parser.add_argument("--verification-sidecar", type=Path, required=True)
    parser.add_argument("--publication-directory", type=Path, required=True)
    parser.add_argument("--output-request", type=Path, required=True)
    args = parser.parse_args()

    output = args.output_request.resolve()
    if output.exists():
        raise ValueError(f"output request must be new: {output}")
    if output.suffix.lower() != ".json" or not output.parent.is_dir():
        raise ValueError("output request must be a .json file in an existing directory")
    request = build_request(
        args.view_plan,
        args.verified_drawing,
        args.verification_sidecar,
        args.publication_directory,
    )
    temporary = output.with_name("." + output.name + ".tmp")
    if temporary.exists():
        raise ValueError(f"temporary request path already exists: {temporary}")
    temporary.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output)
    print(
        json.dumps(
            {
                "status": "ready",
                "request_path": str(output),
                "request_sha256": _sha256(output),
                "publication_directory": request["publication_directory"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
