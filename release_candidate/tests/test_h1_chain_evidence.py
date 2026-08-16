from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from drawing_planner.planning_models import canonical_json_sha256
from release_candidate.h1_chain_evidence import (
    H1ChainEvidenceError,
    SCHEMA_PATH,
    validate_and_publish_h1_chain_evidence,
    validate_h1_chain_evidence,
)
from release_candidate.tests.h0_fixture import ready_h0_report


COMMIT = "a" * 40
VIEW_REQUEST_SHA = "b" * 64
DIMENSION_REQUEST_SHA = "c" * 64
LAYOUT_REQUEST_SHA = "d" * 64


def _write(path: Path, value: object) -> dict[str, str]:
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {"path": str(path.resolve()), "sha256": _sha(path)}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _named(role: str, binding: dict[str, str]) -> dict[str, str]:
    return {"role": role, **binding}


def _response(
    root: Path,
    sequence: int,
    tool: str,
    *,
    status: str | None = None,
    request_sha: str | None = None,
    plan_sha: str | None = None,
    dimension_request_sha: str | None = None,
    publish: bool = False,
) -> dict:
    payload: dict = {"ok": True}
    if status is not None:
        payload["status"] = status
    if publish:
        payload["audit"] = {
            "request_sha256": request_sha,
            "candidate_sha256": plan_sha,
        }
    else:
        if request_sha is not None:
            payload["planning_request_sha256"] = request_sha
        if plan_sha is not None:
            payload["plan_canonical_sha256"] = plan_sha
    if dimension_request_sha is not None:
        payload["source_dimension_request_sha256"] = dimension_request_sha
    binding = _write(root / f"response-{sequence:02d}-{tool}.json", payload)
    return {"sequence": sequence, "tool": tool, "response": binding}


def _fixture(tmp_path: Path) -> dict:
    h0 = _write(
        tmp_path / "h0.json",
        ready_h0_report(COMMIT),
    )
    runtime = _write(tmp_path / "SolidworksExecution.exe", b"runtime")
    model = _write(tmp_path / "part.SLDPRT", b"part")
    template = _write(tmp_path / "sheet.DRWDOT", b"template")

    initializer_handoff = _write(tmp_path / "drawing-planning-handoff.json", {"h": 1})
    blank = _write(tmp_path / "blank.SLDDRW", b"blank")
    readiness = _write(tmp_path / "drawing-readiness.json", {"ready": True})
    geometry = _write(tmp_path / "model-geometry.json", {"geometry": True})
    images = {
        name: _write(tmp_path / f"{name}.png", f"{name}-image".encode())
        for name in ("front", "back", "left", "right", "top", "bottom")
    }
    view_plan = _write(
        tmp_path / "view_plan.json",
        {"protocol_id": "solidworks-view-plan", "schema_version": "1.4"},
    )
    view_drawing = _write(tmp_path / "views.SLDDRW", b"views")
    view_sidecar = _write(tmp_path / "views.sidecar.json", {"verified": True})
    dimension_handoff = _write(tmp_path / "dimension-handoff.json", {"h": 2})
    dimension_plan = _write(
        tmp_path / "dimension_plan.json",
        {"protocol_id": "solidworks-dimension-plan", "schema_version": "1.0"},
    )
    dimensioned = _write(tmp_path / "dimensioned.SLDDRW", b"dimensioned")
    dimension_sidecar = _write(
        tmp_path / "dimensioned.SLDDRW.dimension-verification.json", {"verified": True}
    )
    layout_handoff = _write(tmp_path / "layout-handoff.json", {"h": 3})
    layout_plan = _write(
        tmp_path / "drawing_layout_plan.json",
        {"protocol_id": "solidworks-drawing-layout-plan", "schema_version": "1.0"},
    )
    final_drawing = _write(tmp_path / "final.SLDDRW", b"final")
    final_sidecar_value = {
        "protocol_id": "solidworks-drawing-layout-verification",
        "schema_version": "1.0",
        "verified": True,
        "output_path": final_drawing["path"],
        "artifact_sha256": final_drawing["sha256"],
    }
    final_sidecar = _write(tmp_path / "final.SLDDRW.layout-verification.json", final_sidecar_value)
    view_plan_sha = canonical_json_sha256(
        json.loads(Path(view_plan["path"]).read_text(encoding="utf-8")), "view plan"
    )
    dimension_plan_sha = canonical_json_sha256(
        json.loads(Path(dimension_plan["path"]).read_text(encoding="utf-8")),
        "dimension plan",
    )
    layout_plan_sha = canonical_json_sha256(
        json.loads(Path(layout_plan["path"]).read_text(encoding="utf-8")),
        "drawing layout plan",
    )

    counter = 0

    def operation(tool: str, **kwargs) -> dict:
        nonlocal counter
        counter += 1
        return _response(tmp_path, counter, tool, **kwargs)

    stages = [
        {
            "order": 1,
            "skill": "bootstrap-solidworks-host",
            "inputs": [],
            "outputs": [],
            "operations": [operation("inspect_solidworks_host")],
        },
        {
            "order": 2,
            "skill": "solidworks-initialize-drawing-handoff",
            "inputs": [_named("source_model", model), _named("drawing_template", template)],
            "outputs": [
                _named("initializer_handoff", initializer_handoff),
                _named("blank_drawing", blank),
                _named("readiness_report", readiness),
                _named("geometry_report", geometry),
                *[
                    _named(f"{name}_image", images[name])
                    for name in ("front", "back", "left", "right", "top", "bottom")
                ],
            ],
            "operations": [
                operation(
                    "initialize_part_drawing_handoff", request_sha=VIEW_REQUEST_SHA
                )
            ],
        },
        {
            "order": 3,
            "skill": "solidworks-create-drawing-views",
            "inputs": [
                _named("initializer_handoff", initializer_handoff),
                _named("blank_drawing", blank),
            ],
            "outputs": [
                _named("view_plan", view_plan),
                _named("view_drawing", view_drawing),
                _named("view_verification_sidecar", view_sidecar),
            ],
            "operations": [
                operation(
                    "publish_validated_part_drawing_view_plan",
                    status="published", request_sha=VIEW_REQUEST_SHA,
                    plan_sha=view_plan_sha, publish=True,
                ),
                operation(
                    "validate_part_drawing_view_plan", status="VALID",
                    request_sha=VIEW_REQUEST_SHA, plan_sha=view_plan_sha,
                ),
                operation(
                    "create_part_drawing_from_view_plan", status="COMPLETED",
                    request_sha=VIEW_REQUEST_SHA, plan_sha=view_plan_sha,
                ),
                operation(
                    "verify_part_drawing_view_plan", status="COMPLETED",
                    request_sha=VIEW_REQUEST_SHA, plan_sha=view_plan_sha,
                ),
            ],
        },
        {
            "order": 4,
            "skill": "solidworks-dimension-drawing",
            "inputs": [
                _named("view_plan", view_plan),
                _named("view_drawing", view_drawing),
                _named("view_verification_sidecar", view_sidecar),
            ],
            "outputs": [
                _named("dimension_handoff", dimension_handoff),
                _named("dimension_plan", dimension_plan),
                _named("dimensioned_drawing", dimensioned),
                _named("dimension_verification_sidecar", dimension_sidecar),
            ],
            "operations": [
                operation(
                    "initialize_part_drawing_dimension_handoff", status="ready",
                    request_sha=DIMENSION_REQUEST_SHA,
                ),
                operation(
                    "publish_validated_part_drawing_dimension_plan",
                    status="published", request_sha=DIMENSION_REQUEST_SHA,
                    plan_sha=dimension_plan_sha, publish=True,
                ),
                operation(
                    "validate_part_drawing_dimension_plan", status="VALID",
                    request_sha=DIMENSION_REQUEST_SHA, plan_sha=dimension_plan_sha,
                ),
                operation(
                    "create_dimensioned_part_drawing", status="COMPLETED",
                    request_sha=DIMENSION_REQUEST_SHA, plan_sha=dimension_plan_sha,
                ),
                operation(
                    "verify_dimensioned_part_drawing", status="COMPLETED",
                    request_sha=DIMENSION_REQUEST_SHA, plan_sha=dimension_plan_sha,
                ),
            ],
        },
        {
            "order": 5,
            "skill": "solidworks-finalize-drawing-layout",
            "inputs": [
                _named("dimension_plan", dimension_plan),
                _named("dimensioned_drawing", dimensioned),
                _named("dimension_verification_sidecar", dimension_sidecar),
            ],
            "outputs": [
                _named("layout_handoff", layout_handoff),
                _named("layout_plan", layout_plan),
                _named("final_drawing", final_drawing),
                _named("final_verification_sidecar", final_sidecar),
            ],
            "operations": [
                operation(
                    "initialize_part_drawing_layout_handoff", status="ready",
                    dimension_request_sha=DIMENSION_REQUEST_SHA,
                ),
                operation(
                    "publish_validated_part_drawing_layout_plan",
                    status="published", request_sha=LAYOUT_REQUEST_SHA,
                    plan_sha=layout_plan_sha, dimension_request_sha=DIMENSION_REQUEST_SHA,
                    publish=True,
                ),
                operation(
                    "validate_part_drawing_layout_plan", status="VALID",
                    request_sha=LAYOUT_REQUEST_SHA, plan_sha=layout_plan_sha,
                    dimension_request_sha=DIMENSION_REQUEST_SHA,
                ),
                operation(
                    "create_final_part_drawing", status="COMPLETED",
                    request_sha=LAYOUT_REQUEST_SHA, plan_sha=layout_plan_sha,
                    dimension_request_sha=DIMENSION_REQUEST_SHA,
                ),
                operation(
                    "verify_final_part_drawing", status="COMPLETED",
                    request_sha=LAYOUT_REQUEST_SHA, plan_sha=layout_plan_sha,
                    dimension_request_sha=DIMENSION_REQUEST_SHA,
                ),
            ],
        },
    ]
    return {
        "protocol_id": "solidworks-five-skill-chain-evidence",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "generated_at_utc": "2026-08-16T08:00:00Z",
        "git_commit": COMMIT,
        "h0_readiness": h0,
        "execution_service": runtime,
        "immutable_inputs": [
            {
                "role": "source_model", "path": model["path"],
                "sha256_before": model["sha256"], "sha256_after": model["sha256"],
            },
            {
                "role": "drawing_template", "path": template["path"],
                "sha256_before": template["sha256"], "sha256_after": template["sha256"],
            },
        ],
        "stages": stages,
        "final_artifacts": {
            "drawing": final_drawing,
            "verification_sidecar": final_sidecar,
        },
    }


def test_h1_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def test_h1_validates_and_publishes_complete_production_chain(tmp_path: Path) -> None:
    candidate = _fixture(tmp_path)
    validated = validate_h1_chain_evidence(candidate)
    assert validated["stages"][-1]["skill"] == "solidworks-finalize-drawing-layout"
    output = tmp_path / "h1-chain-evidence.json"
    result = validate_and_publish_h1_chain_evidence(candidate, output)
    assert result["status"] == "complete"
    assert result["production_only"] is True
    assert output.is_file()


def test_h1_rejects_qualification_substitution(tmp_path: Path) -> None:
    candidate = _fixture(tmp_path)
    operation = candidate["stages"][3]["operations"][3]
    operation["tool"] = "qualify_dimensioned_part_drawing"
    with pytest.raises(H1ChainEvidenceError, match="qualification tools"):
        validate_h1_chain_evidence(candidate)


def test_h1_rejects_cross_stage_hash_drift(tmp_path: Path) -> None:
    candidate = _fixture(tmp_path)
    candidate["stages"][3]["inputs"][0]["sha256"] = "f" * 64
    with pytest.raises(H1ChainEvidenceError, match="SHA-256 mismatch|continuity"):
        validate_h1_chain_evidence(candidate)


def test_h1_rejects_plan_file_drift_even_when_file_binding_is_updated(
    tmp_path: Path,
) -> None:
    candidate = _fixture(tmp_path)
    plan_binding = candidate["stages"][2]["outputs"][0]
    plan_path = Path(plan_binding["path"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["plan_id"] = "drifted-after-semantic-responses"
    _write(plan_path, plan)
    plan_binding["sha256"] = _sha(plan_path)
    candidate["stages"][3]["inputs"][0]["sha256"] = plan_binding["sha256"]
    with pytest.raises(H1ChainEvidenceError, match="plan_canonical_sha256"):
        validate_h1_chain_evidence(candidate)


def test_h1_rejects_blocked_readiness_and_request_drift(tmp_path: Path) -> None:
    candidate = _fixture(tmp_path)
    h0_path = Path(candidate["h0_readiness"]["path"])
    h0 = json.loads(h0_path.read_text(encoding="utf-8"))
    h0["status"] = "blocked"
    h0["blockers"] = [
        {"code": "fixture-blocker", "message": "blocked", "references": []}
    ]
    _write(h0_path, h0)
    candidate["h0_readiness"]["sha256"] = _sha(h0_path)
    with pytest.raises(H1ChainEvidenceError, match="ready, clean H0"):
        validate_h1_chain_evidence(candidate)

    candidate = _fixture(tmp_path)
    response_path = Path(candidate["stages"][2]["operations"][-1]["response"]["path"])
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["planning_request_sha256"] = "e" * 64
    _write(response_path, response)
    candidate["stages"][2]["operations"][-1]["response"]["sha256"] = _sha(response_path)
    with pytest.raises(H1ChainEvidenceError, match="continuity"):
        validate_h1_chain_evidence(candidate)
