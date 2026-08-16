from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from release_candidate import h2_session_preflight as h2


COMMIT = "a" * 40


def _write(path: Path, value: object) -> dict[str, str]:
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _request(tmp_path: Path, *, h0_status: str = "ready") -> dict:
    h0 = _write(
        tmp_path / "h0-readiness.json",
        {
            "protocol_id": "solidworks-five-skill-release-readiness",
            "schema_version": "1.0",
            "status": h0_status,
            "git": {"commit": COMMIT, "clean": True},
        },
    )
    return {
        "protocol_id": "solidworks-five-skill-session-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "git_commit": COMMIT,
        "h0_readiness": h0,
        "execution_service": _write(
            tmp_path / "SolidworksExecution.exe", b"runtime"
        ),
        "source_model": _write(tmp_path / "part.SLDPRT", b"part"),
        "drawing_template": _write(tmp_path / "sheet.DRWDOT", b"template"),
        "session_root": str((tmp_path / "future-session").resolve()),
    }


def _clean_git(monkeypatch) -> None:
    monkeypatch.setattr(
        h2,
        "_git_state",
        lambda _: {"commit": COMMIT, "clean": True, "changed_paths": []},
    )


def test_h2_contracts_are_valid_draft_2020_12() -> None:
    for path in (h2.REQUEST_SCHEMA_PATH, h2.REPORT_SCHEMA_PATH):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_h2_ready_preflight_freezes_exact_production_schedule(
    tmp_path: Path, monkeypatch
) -> None:
    _clean_git(monkeypatch)
    report = h2.build_h2_session_preflight(_request(tmp_path), tmp_path)
    assert report["status"] == "ready"
    assert report["solidworks_contacted"] is False
    assert len(report["schedule"]) == 16
    assert [row["sequence"] for row in report["schedule"]] == list(range(1, 17))
    tools = [row["tool"] for row in report["schedule"]]
    assert "create_dimensioned_part_drawing" in tools
    assert "create_final_part_drawing" in tools
    assert not any("qualif" in tool for tool in tools)
    assert report["planned_outputs"]["final_drawing"].endswith("final.SLDDRW")
    assert not Path(report["session_root"]).exists()


def test_h2_blocked_h0_publishes_report_without_creating_session(
    tmp_path: Path, monkeypatch
) -> None:
    _clean_git(monkeypatch)
    request = _request(tmp_path, h0_status="blocked")
    output = tmp_path / "h2-preflight.json"
    result = h2.build_and_publish_h2_session_preflight(
        request, tmp_path, output
    )
    assert result["status"] == "blocked"
    assert result["solidworks_contacted"] is False
    assert output.is_file()
    assert not Path(request["session_root"]).exists()
    assert any(
        blocker["code"] == "h0-readiness-not-ready"
        for blocker in result["blockers"]
    )


def test_h2_rejects_dirty_commit_hash_drift_and_existing_root(
    tmp_path: Path, monkeypatch
) -> None:
    request = _request(tmp_path)
    Path(request["session_root"]).mkdir()
    monkeypatch.setattr(
        h2,
        "_git_state",
        lambda _: {
            "commit": "b" * 40,
            "clean": False,
            "changed_paths": ["user-change.txt"],
        },
    )
    report = h2.build_h2_session_preflight(request, tmp_path)
    codes = {row["code"] for row in report["blockers"]}
    assert report["status"] == "blocked"
    assert {
        "git-commit-drift",
        "git-worktree-not-frozen",
        "session-root-not-new",
    } <= codes


def test_h2_detects_runtime_hash_and_artifact_extension_drift(
    tmp_path: Path, monkeypatch
) -> None:
    _clean_git(monkeypatch)
    request = _request(tmp_path)
    runtime = Path(request["execution_service"]["path"])
    runtime.write_bytes(b"changed")
    wrong_model = tmp_path / "part.step"
    wrong_model.write_bytes(b"part")
    request["source_model"] = {
        "path": str(wrong_model.resolve()),
        "sha256": hashlib.sha256(wrong_model.read_bytes()).hexdigest(),
    }
    report = h2.build_h2_session_preflight(request, tmp_path)
    codes = [row["code"] for row in report["blockers"]]
    assert report["status"] == "blocked"
    assert "artifact-hash-mismatch" in codes
    assert "artifact-extension-invalid" in codes
