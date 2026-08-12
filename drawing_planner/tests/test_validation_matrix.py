import json
import sys
from pathlib import Path

import pytest

from drawing_planner.validation_matrix import (
    MatrixCase,
    default_cases,
    run_validation_matrix,
    snapshot_validation_tree,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    validation = root / "validation"
    validation.mkdir(parents=True)
    (validation / "part.SLDPRT").write_bytes(b"part")
    (validation / "blank.SLDDRW").write_bytes(b"drawing")
    return root


def _python_case(case_id: str, lane: str, code: str) -> MatrixCase:
    return MatrixCase(
        case_id=case_id,
        lane=lane,
        argv=(sys.executable, "-c", code),
        timeout_seconds=10,
    )


def test_snapshot_is_stable_and_tracks_content(tmp_path):
    root = _repo(tmp_path)
    first = snapshot_validation_tree(root / "validation")
    second = snapshot_validation_tree(root / "validation")
    assert first == second
    (root / "validation" / "part.SLDPRT").write_bytes(b"changed")
    assert snapshot_validation_tree(root / "validation")["tree_sha256"] != first[
        "tree_sha256"
    ]


def test_passing_matrix_writes_auditable_report(tmp_path):
    root = _repo(tmp_path)
    output = tmp_path / "matrix"
    report = run_validation_matrix(
        root,
        output,
        (
            _python_case("offline-pass", "offline", "print('offline')"),
            _python_case("integration-pass", "integration", "print('integration')"),
        ),
    )
    assert report["status"] == "pass"
    assert report["validation_inputs"]["unchanged"] is True
    persisted = json.loads(
        (output / "view-plan-validation-matrix.json").read_text(encoding="utf-8")
    )
    assert persisted["cases"] == report["cases"]
    assert all(Path(row["stdout_path"]).is_file() for row in report["cases"])


def test_failure_is_recorded_with_stable_exit_evidence(tmp_path):
    root = _repo(tmp_path)
    report = run_validation_matrix(
        root,
        tmp_path / "matrix",
        (_python_case("offline-fail", "offline", "raise SystemExit(7)"),),
    )
    assert report["status"] == "fail"
    assert report["cases"][0]["returncode"] == 7


def test_failed_prerequisite_prevents_live_case(tmp_path):
    root = _repo(tmp_path)
    marker = tmp_path / "live-ran"
    report = run_validation_matrix(
        root,
        tmp_path / "matrix",
        (
            _python_case("integration-fail", "integration", "raise SystemExit(1)"),
            _python_case(
                "live-write",
                "live",
                f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')",
            ),
        ),
    )
    assert report["cases"][1]["status"] == "not_run"
    assert not marker.exists()


def test_validation_mutation_fails_even_when_command_returns_zero(tmp_path):
    root = _repo(tmp_path)
    protected = root / "validation" / "part.SLDPRT"
    report = run_validation_matrix(
        root,
        tmp_path / "matrix",
        (
            _python_case(
                "offline-mutates-input",
                "offline",
                f"from pathlib import Path; Path({str(protected)!r}).write_bytes(b'bad')",
            ),
        ),
    )
    assert report["cases"][0]["status"] == "pass"
    assert report["validation_inputs"]["unchanged"] is False
    assert report["status"] == "fail"


def test_output_cannot_alias_or_descend_from_validation(tmp_path):
    root = _repo(tmp_path)
    case = _python_case("offline-pass", "offline", "pass")
    with pytest.raises(ValueError, match="must not be inside"):
        run_validation_matrix(root, root / "validation" / "output", (case,))


def test_existing_nonempty_output_is_rejected(tmp_path):
    root = _repo(tmp_path)
    output = tmp_path / "matrix"
    output.mkdir()
    (output / "owned.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="new or empty"):
        run_validation_matrix(
            root,
            output,
            (_python_case("offline-pass", "offline", "pass"),),
        )


def test_existing_empty_output_is_accepted(tmp_path):
    root = _repo(tmp_path)
    output = tmp_path / "matrix"
    output.mkdir()
    report = run_validation_matrix(
        root,
        output,
        (_python_case("offline-pass", "offline", "pass"),),
    )
    assert report["status"] == "pass"


def test_default_inventory_adds_live_only_with_preflight(tmp_path):
    root = _repo(tmp_path)
    (root / "scripts").mkdir()
    output = tmp_path / "matrix"
    without_live = default_cases(
        root, output, python_executable=Path(sys.executable)
    )
    assert {case.lane for case in without_live} == {"offline", "integration"}
    with_live = default_cases(
        root,
        output,
        python_executable=Path(sys.executable),
        host_preflight_report=tmp_path / "host-preflight-report.json",
    )
    assert with_live[-1].case_id == "solidworks-viewplan-live-matrix"
    assert with_live[-1].lane == "live"
