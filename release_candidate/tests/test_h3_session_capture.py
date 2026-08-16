from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from drawing_planner.planning_models import canonical_json_sha256
from release_candidate import h2_session_preflight as h2
from release_candidate import h3_session_capture as h3
from release_candidate.tests.h0_fixture import ready_h0_report


COMMIT = "a" * 40
VIEW_REQUEST_SHA = "b" * 64
DIMENSION_REQUEST_SHA = "c" * 64
LAYOUT_REQUEST_SHA = "d" * 64


def _write(path: Path, value: object) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _ready_preflight(tmp_path: Path, monkeypatch) -> tuple[Path, str, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    h0 = _write(
        tmp_path / "h0.json",
        ready_h0_report(COMMIT),
    )
    request = {
        "protocol_id": "solidworks-five-skill-session-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "git_commit": COMMIT,
        "h0_readiness": h0,
        "execution_service": _write(tmp_path / "SolidworksExecution.exe", b"runtime"),
        "source_model": _write(tmp_path / "part.SLDPRT", b"part"),
        "drawing_template": _write(tmp_path / "sheet.DRWDOT", b"template"),
        "session_root": str((tmp_path / "session").resolve()),
    }
    clean = lambda _: {"commit": COMMIT, "clean": True, "changed_paths": []}
    monkeypatch.setattr(h2, "_git_state", clean)
    monkeypatch.setattr(h3, "_git_state", clean)
    report = h2.build_h2_session_preflight(request, repository)
    assert report["status"] == "ready"
    path = tmp_path / "h2-preflight.json"
    _write(path, report)
    return path, _sha(path), repository


def _create(tmp_path: Path, monkeypatch) -> tuple[dict, dict, Path]:
    preflight_path, preflight_sha, repository = _ready_preflight(tmp_path, monkeypatch)
    created = h3.create_h3_session(
        preflight_path,
        preflight_sha,
        repository,
        created_at_utc="2026-08-16T09:00:00Z",
    )
    manifest_path = Path(created["session_manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return created, manifest, repository


def _paths(rows: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    return [{"role": role, "path": binding["path"]} for role, binding in rows.items()]


def _semantic_response(
    tool: str,
    *,
    view_plan_sha: str,
    dimension_plan_sha: str,
    layout_plan_sha: str,
) -> dict:
    if tool == "inspect_solidworks_host":
        return {"ok": True, "status": "pass"}
    if tool == "initialize_part_drawing_handoff":
        return {
            "ok": True,
            "status": "COMPLETED",
            "planning_request_sha256": VIEW_REQUEST_SHA,
        }
    if tool == "initialize_part_drawing_dimension_handoff":
        return {
            "ok": True,
            "status": "ready",
            "planning_request_sha256": DIMENSION_REQUEST_SHA,
        }
    if tool == "initialize_part_drawing_layout_handoff":
        return {
            "ok": True,
            "status": "ready",
            "source_dimension_request_sha256": DIMENSION_REQUEST_SHA,
        }

    if "view_plan" in tool:
        request_sha, plan_sha = VIEW_REQUEST_SHA, view_plan_sha
    elif "dimension_plan" in tool or tool in {
        "create_dimensioned_part_drawing",
        "verify_dimensioned_part_drawing",
    }:
        request_sha, plan_sha = DIMENSION_REQUEST_SHA, dimension_plan_sha
    else:
        request_sha, plan_sha = LAYOUT_REQUEST_SHA, layout_plan_sha
    if tool.startswith("publish_validated_"):
        payload = {
            "ok": True,
            "status": "published",
            "audit": {
                "request_sha256": request_sha,
                "candidate_sha256": plan_sha,
            },
        }
    elif tool.startswith("validate_"):
        payload = {"ok": True, "status": "VALID"}
    else:
        payload = {"ok": True, "status": "COMPLETED"}
    payload["planning_request_sha256"] = request_sha
    payload["plan_canonical_sha256"] = plan_sha
    if "layout" in tool or tool in {"create_final_part_drawing", "verify_final_part_drawing"}:
        payload["source_dimension_request_sha256"] = DIMENSION_REQUEST_SHA
    return payload


def test_h3_contracts_are_valid_draft_2020_12() -> None:
    for path in (h3.MANIFEST_SCHEMA_PATH, h3.STAGE_SCHEMA_PATH):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_h3_creates_only_append_only_session_structure(tmp_path: Path, monkeypatch) -> None:
    created, manifest, _ = _create(tmp_path, monkeypatch)
    assert created["next_tool"] == "inspect_solidworks_host"
    assert manifest["append_only"] is True
    assert Path(manifest["planned_outputs"]["response_directory"]).is_dir()
    assert Path(manifest["planned_outputs"]["stage_directory"]).is_dir()
    assert not Path(manifest["planned_outputs"]["h1_candidate"]).exists()


def test_h3_rejects_blocked_preflight_without_creating_root(
    tmp_path: Path, monkeypatch
) -> None:
    preflight_path, _, repository = _ready_preflight(tmp_path, monkeypatch)
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["status"] = "blocked"
    preflight["blockers"] = [
        {"code": "test-blocker", "message": "not ready", "references": []}
    ]
    _write(preflight_path, preflight)
    with pytest.raises(h3.H3SessionCaptureError, match="blocker-free ready"):
        h3.create_h3_session(preflight_path, _sha(preflight_path), repository)
    assert not Path(preflight["session_root"]).exists()


def test_h3_captures_exact_order_and_permanently_stops_after_failure(
    tmp_path: Path, monkeypatch
) -> None:
    created, _, _ = _create(tmp_path, monkeypatch)
    manifest_path = Path(created["session_manifest_path"])
    manifest_sha = created["session_manifest_sha256"]
    with pytest.raises(h3.H3SessionCaptureError, match="expected operation 1"):
        h3.capture_h3_operation(
            manifest_path, manifest_sha, "initialize_part_drawing_handoff", {"ok": True}
        )
    first = h3.capture_h3_operation(
        manifest_path, manifest_sha, "inspect_solidworks_host", {"ok": True}
    )
    assert first["status"] == "captured"
    h3.capture_h3_stage(
        manifest_path,
        manifest_sha,
        1,
        [],
        [],
        captured_at_utc="2026-08-16T09:01:00Z",
    )
    failed = h3.capture_h3_operation(
        manifest_path,
        manifest_sha,
        "initialize_part_drawing_handoff",
        {"ok": False, "status": "FAILED"},
    )
    assert failed["status"] == "blocked"
    with pytest.raises(h3.H3SessionCaptureError, match="permanently stopped"):
        h3.capture_h3_operation(
            manifest_path,
            manifest_sha,
            "publish_validated_part_drawing_view_plan",
            {"ok": True, "status": "published"},
        )


def test_h3_rechecks_frozen_inputs_before_every_capture(
    tmp_path: Path, monkeypatch
) -> None:
    created, manifest, _ = _create(tmp_path, monkeypatch)
    Path(manifest["source_model"]["path"]).write_bytes(b"changed-after-session-create")
    with pytest.raises(h3.H3SessionCaptureError, match="source_model"):
        h3.capture_h3_operation(
            Path(created["session_manifest_path"]),
            created["session_manifest_sha256"],
            "inspect_solidworks_host",
            {"ok": True, "status": "pass"},
        )


def test_h3_full_capture_builds_h1_candidate(tmp_path: Path, monkeypatch) -> None:
    created, manifest, _ = _create(tmp_path, monkeypatch)
    manifest_path = Path(created["session_manifest_path"])
    manifest_sha = created["session_manifest_sha256"]
    planned = manifest["planned_outputs"]

    stage2_outputs = {
        "initializer_handoff": _write(Path(planned["initializer_handoff"]), {"handoff": 1}),
        "blank_drawing": _write(Path(planned["blank_drawing"]), b"blank"),
        "readiness_report": _write(
            Path(planned["initializer_directory"]) / "drawing-readiness.json", {"ready": True}
        ),
        "geometry_report": _write(
            Path(planned["initializer_directory"]) / "model-geometry.json", {"geometry": True}
        ),
    }
    for name in ("front", "back", "left", "right", "top", "bottom"):
        stage2_outputs[f"{name}_image"] = _write(
            Path(planned["initializer_directory"]) / f"{name}.png",
            f"{name}-image".encode(),
        )

    view_plan_value = {"protocol_id": "solidworks-view-plan", "schema_version": "1.4"}
    dimension_plan_value = {
        "protocol_id": "solidworks-dimension-plan", "schema_version": "1.0"
    }
    layout_plan_value = {
        "protocol_id": "solidworks-drawing-layout-plan", "schema_version": "1.0"
    }
    stage3_outputs = {
        "view_plan": _write(Path(planned["view_plan"]), view_plan_value),
        "view_drawing": _write(Path(planned["view_drawing"]), b"views"),
        "view_verification_sidecar": _write(
            Path(planned["view_verification_sidecar"]), {"verified": True}
        ),
    }
    stage4_outputs = {
        "dimension_handoff": _write(Path(planned["dimension_handoff"]), {"handoff": 2}),
        "dimension_plan": _write(Path(planned["dimension_plan"]), dimension_plan_value),
        "dimensioned_drawing": _write(Path(planned["dimension_drawing"]), b"dimensioned"),
        "dimension_verification_sidecar": _write(
            Path(planned["dimension_verification_sidecar"]), {"verified": True}
        ),
    }
    final_drawing = _write(Path(planned["final_drawing"]), b"final")
    stage5_outputs = {
        "layout_handoff": _write(Path(planned["layout_handoff"]), {"handoff": 3}),
        "layout_plan": _write(Path(planned["layout_plan"]), layout_plan_value),
        "final_drawing": final_drawing,
        "final_verification_sidecar": _write(
            Path(planned["final_verification_sidecar"]),
            {
                "protocol_id": "solidworks-drawing-layout-verification",
                "schema_version": "1.0",
                "verified": True,
                "output_path": final_drawing["path"],
                "artifact_sha256": final_drawing["sha256"],
            },
        ),
    }
    view_plan_sha = canonical_json_sha256(view_plan_value, "view plan")
    dimension_plan_sha = canonical_json_sha256(dimension_plan_value, "dimension plan")
    layout_plan_sha = canonical_json_sha256(layout_plan_value, "drawing layout plan")

    stage_artifacts = {
        1: ({}, {}),
        2: (
            {"source_model": manifest["source_model"], "drawing_template": manifest["drawing_template"]},
            stage2_outputs,
        ),
        3: (
            {
                "initializer_handoff": stage2_outputs["initializer_handoff"],
                "blank_drawing": stage2_outputs["blank_drawing"],
            },
            stage3_outputs,
        ),
        4: (
            {
                "view_plan": stage3_outputs["view_plan"],
                "view_drawing": stage3_outputs["view_drawing"],
                "view_verification_sidecar": stage3_outputs["view_verification_sidecar"],
            },
            stage4_outputs,
        ),
        5: (
            {
                "dimension_plan": stage4_outputs["dimension_plan"],
                "dimensioned_drawing": stage4_outputs["dimensioned_drawing"],
                "dimension_verification_sidecar": stage4_outputs[
                    "dimension_verification_sidecar"
                ],
            },
            stage5_outputs,
        ),
    }
    for index, schedule in enumerate(manifest["schedule"]):
        response = _semantic_response(
            schedule["tool"],
            view_plan_sha=view_plan_sha,
            dimension_plan_sha=dimension_plan_sha,
            layout_plan_sha=layout_plan_sha,
        )
        result = h3.capture_h3_operation(
            manifest_path, manifest_sha, schedule["tool"], response
        )
        assert result["ok"] is True
        next_stage = (
            manifest["schedule"][index + 1]["stage_order"]
            if index + 1 < len(manifest["schedule"])
            else None
        )
        if next_stage != schedule["stage_order"]:
            inputs, outputs = stage_artifacts[schedule["stage_order"]]
            h3.capture_h3_stage(
                manifest_path,
                manifest_sha,
                schedule["stage_order"],
                _paths(inputs),
                _paths(outputs),
                captured_at_utc=(
                    f"2026-08-16T09:0{schedule['stage_order']}:00Z"
                ),
            )

    result = h3.finalize_h3_session(
        manifest_path,
        manifest_sha,
        generated_at_utc="2026-08-16T09:10:00Z",
    )
    assert result["status"] == "complete"
    assert result["operation_count"] == 16
    candidate = json.loads(Path(result["h1_candidate_path"]).read_text(encoding="utf-8"))
    assert len(candidate["stages"]) == 5
    assert candidate["final_artifacts"]["drawing"]["sha256"] == final_drawing["sha256"]
