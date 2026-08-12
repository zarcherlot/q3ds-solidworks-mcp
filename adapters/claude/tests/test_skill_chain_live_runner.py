from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "run_skill_chain_live", _ROOT / "scripts/run_skill_chain_live.py"
)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)


def test_runner_fails_closed_on_business_failure() -> None:
    with pytest.raises(RuntimeError, match="failed closed"):
        runner._require_business(
            {"ok": False, "status": "FAILED", "error": {"code": "BLOCKED"}},
            "create",
            "COMPLETED",
        )


def test_runner_checks_status_without_transaction_status_field() -> None:
    runner._require_host_status(
        {"ok": True, "com_attached": True, "state_version": 3}
    )
    with pytest.raises(RuntimeError, match="not ready"):
        runner._require_host_status(
            {"ok": True, "com_attached": False, "state_version": 3}
        )


def test_runner_rejects_request_and_plan_hash_drift() -> None:
    request_hash = "a" * 64
    plan_hash = "b" * 64
    with pytest.raises(RuntimeError, match="request hash mismatch"):
        runner._require_bound_operation(
            {
                "planning_request_sha256": "c" * 64,
                "plan_canonical_sha256": plan_hash,
            },
            request_hash,
            plan_hash,
            "verify",
        )
    with pytest.raises(RuntimeError, match="canonical plan hash mismatch"):
        runner._require_bound_operation(
            {
                "planning_request_sha256": request_hash,
                "plan_canonical_sha256": "c" * 64,
            },
            request_hash,
            plan_hash,
            "verify",
        )


def test_runner_rejects_surface_drift_and_prompts() -> None:
    runner._require_surface(runner._EXPECTED_TOOLS, ())
    with pytest.raises(RuntimeError, match="unexpected default MCP surface"):
        runner._require_surface(runner._EXPECTED_TOOLS[:-1], ())
    with pytest.raises(RuntimeError, match="zero prompts"):
        runner._require_surface(runner._EXPECTED_TOOLS, (object(),))


def test_runner_rejects_output_collision(tmp_path: Path) -> None:
    output = tmp_path / "drawing.SLDDRW"
    output.write_bytes(b"occupied")
    with pytest.raises(FileExistsError, match="output path collision"):
        runner._require_new_output(output, tmp_path)


def test_runner_accepts_new_or_empty_publication_directory(tmp_path: Path) -> None:
    new_directory = tmp_path / "new"
    runner._require_new_publication_directory(new_directory)
    new_directory.mkdir()
    runner._require_new_publication_directory(new_directory)
    (new_directory / "occupied.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(FileExistsError, match="new or empty"):
        runner._require_new_publication_directory(new_directory)


def test_runner_rejects_blocked_host() -> None:
    with pytest.raises(RuntimeError, match="not an unqualified pass"):
        runner._require_host_pass(
            {"status": "blocked", "blocking_issues": ["COM"], "warnings": []},
            Path("template.DRWDOT"),
        )


def test_runner_accepts_only_explicit_http_loopback_port() -> None:
    assert runner._loopback_address("http://localhost:5013") == ("localhost", 5013)
    with pytest.raises(ValueError, match="loopback"):
        runner._loopback_address("https://example.com:5013")
    with pytest.raises(ValueError, match="explicit loopback port"):
        runner._loopback_address("http://localhost")


def test_runner_excludes_solidworks_session_locks_from_protected_inputs(
    tmp_path: Path,
) -> None:
    model = tmp_path / "part.SLDPRT"
    drawing = tmp_path / "drawing.SLDDRW"
    lock = tmp_path / "~$part.SLDPRT"
    model.write_bytes(b"model")
    drawing.write_bytes(b"drawing")
    lock.write_bytes(b"session")

    assert runner._files_under(tmp_path) == [drawing, model]
    assert runner._is_solidworks_session_lock(lock)
    assert not runner._is_solidworks_session_lock(model)
