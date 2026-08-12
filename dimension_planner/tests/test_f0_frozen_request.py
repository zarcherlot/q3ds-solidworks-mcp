import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from drawing_planner.planning_models import canonical_json_sha256


_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "build_dimension_f0_frozen_request",
    _ROOT / "scripts" / "build_dimension_f0_frozen_request.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifacts(tmp_path: Path):
    plan = tmp_path / "view_plan.json"
    drawing = tmp_path / "verified.SLDDRW"
    sidecar = tmp_path / "verified.SLDDRW.verification.json"
    plan_value = {"protocol_id": "solidworks-view-plan", "schema_version": "1.4"}
    plan.write_text(json.dumps(plan_value), encoding="utf-8")
    drawing.write_bytes(b"verified drawing")
    sidecar.write_text(
        json.dumps(
            {
                "verified": True,
                "output_path": str(drawing.resolve()),
                "artifact_sha256": _sha(drawing),
                "plan_canonical_sha256": canonical_json_sha256(
                    plan_value, "view plan"
                ),
            }
        ),
        encoding="utf-8",
    )
    return plan, drawing, sidecar


def test_builds_hash_bound_frozen_request(tmp_path):
    plan, drawing, sidecar = _artifacts(tmp_path)
    publication = tmp_path / "new-publication"

    request = _MODULE.build_request(plan, drawing, sidecar, publication)

    assert request["source"]["kind"] == "frozen_viewplan_drawing"
    assert request["source"]["view_plan"]["sha256"] == _sha(plan)
    assert request["source"]["verified_drawing"]["sha256"] == _sha(drawing)
    assert request["source"]["verification_sidecar"]["sha256"] == _sha(sidecar)
    assert request["publication_directory"] == str(publication.resolve())
    assert len(request["capability_ids"]) == 14


def test_rejects_sidecar_drawing_hash_mismatch(tmp_path):
    plan, drawing, sidecar = _artifacts(tmp_path)
    value = json.loads(sidecar.read_text(encoding="utf-8"))
    value["artifact_sha256"] = "0" * 64
    sidecar.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="artifact_sha256"):
        _MODULE.build_request(plan, drawing, sidecar, tmp_path / "new-publication")
