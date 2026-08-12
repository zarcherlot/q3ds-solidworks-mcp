"""Repository-owned D1 validation-matrix orchestration.

The matrix treats ``validation/`` as immutable input.  Every lane runs from a
fresh output directory and the complete validation tree is hashed before and
after execution.  A changed input makes the whole matrix fail even when every
individual command returned zero.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence


Lane = Literal["offline", "integration", "live"]
_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class MatrixCase:
    case_id: str
    lane: Lane
    argv: tuple[str, ...]
    timeout_seconds: int


def default_cases(
    repository_root: Path,
    output_directory: Path,
    *,
    python_executable: Path,
    host_preflight_report: Path | None = None,
) -> tuple[MatrixCase, ...]:
    """Return the immutable offline/integration D1 case inventory."""

    contract_output = output_directory / "integration-csharp-contracts"
    cases = (
        MatrixCase(
            case_id="planner-offline-contracts",
            lane="offline",
            argv=(
                str(python_executable),
                "-m",
                "pytest",
                "-q",
                "drawing_planner/tests",
            ),
            timeout_seconds=300,
        ),
        MatrixCase(
            case_id="feature-compiler-offline-contracts",
            lane="offline",
            argv=(
                str(python_executable),
                "solidworks-compiler/pycompiler/tests/test_compiler.py",
            ),
            timeout_seconds=300,
        ),
        MatrixCase(
            case_id="python-bytecode-contract",
            lane="offline",
            argv=(
                str(python_executable),
                "-m",
                "compileall",
                "-q",
                "adapters",
                "drawing_planner",
                "solidworks-compiler",
                "scripts",
            ),
            timeout_seconds=300,
        ),
        MatrixCase(
            case_id="semantic-mcp-integration-contracts",
            lane="integration",
            argv=(
                str(python_executable),
                "-m",
                "pytest",
                "-q",
                "adapters/claude/tests",
            ),
            timeout_seconds=300,
        ),
        MatrixCase(
            case_id="csharp-viewplan-integration-contracts",
            lane="integration",
            argv=(
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(repository_root / "scripts" / "run_view_plan_contract_tests.ps1"),
                "-RepositoryRoot",
                str(repository_root),
                "-OutputDirectory",
                str(contract_output),
            ),
            timeout_seconds=300,
        ),
    )
    if host_preflight_report is None:
        return cases
    return cases + (
        MatrixCase(
            case_id="solidworks-viewplan-live-matrix",
            lane="live",
            argv=(
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(repository_root / "scripts" / "run_view_plan_live_matrix.ps1"),
                "-RepositoryRoot",
                str(repository_root),
                "-OutputDirectory",
                str(output_directory / "live-artifacts"),
                "-HostPreflightReport",
                str(host_preflight_report),
                "-PythonExecutable",
                str(python_executable),
            ),
            timeout_seconds=1800,
        ),
    )


def snapshot_validation_tree(validation_root: Path) -> dict:
    root = validation_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("validation_root must be an existing directory")
    files: list[dict] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if path.is_symlink():
            raise ValueError(f"validation inputs must not contain symlinks: {path}")
        if not path.is_file():
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _hash_file(path),
            }
        )
    if not files:
        raise ValueError("validation_root must contain at least one file")
    canonical = json.dumps(
        files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "root": str(root),
        "file_count": len(files),
        "tree_sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def run_validation_matrix(
    repository_root: Path,
    output_directory: Path,
    cases: Sequence[MatrixCase],
    *,
    validation_root: Path | None = None,
) -> dict:
    root = repository_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("repository_root must be an existing directory")
    validation = (validation_root or root / "validation").resolve(strict=True)
    output = output_directory.resolve()
    _require_fresh_output(output, validation)
    output.mkdir(parents=True, exist_ok=True)

    before = snapshot_validation_tree(validation)
    started = time.time()
    rows: list[dict] = []
    blocked = False
    for case in cases:
        if blocked and case.lane == "live":
            rows.append(
                {
                    "case_id": case.case_id,
                    "lane": case.lane,
                    "status": "not_run",
                    "reason": "an offline or integration prerequisite failed",
                }
            )
            continue
        row = _run_case(root, output, case)
        rows.append(row)
        if case.lane in {"offline", "integration"} and row["status"] != "pass":
            blocked = True

    try:
        after = snapshot_validation_tree(validation)
        unchanged = before["tree_sha256"] == after["tree_sha256"]
    except (OSError, ValueError) as exc:
        after = {"root": str(validation), "snapshot_error": str(exc)}
        unchanged = False
    passed = bool(rows) and all(row["status"] == "pass" for row in rows) and unchanged
    report = {
        "schema_version": _SCHEMA_VERSION,
        "status": "pass" if passed else "fail",
        "repository_root": str(root),
        "output_directory": str(output),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "started_at_utc": _utc_timestamp(started),
        "completed_at_utc": _utc_timestamp(time.time()),
        "duration_seconds": round(time.time() - started, 3),
        "lanes": sorted({case.lane for case in cases}),
        "validation_inputs": {
            "unchanged": unchanged,
            "before": before,
            "after": after,
        },
        "cases": rows,
    }
    report_path = output / "view-plan-validation-matrix.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _run_case(root: Path, output: Path, case: MatrixCase) -> dict:
    if not case.case_id or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in case.case_id
    ):
        raise ValueError(f"invalid matrix case_id: {case.case_id!r}")
    if not case.argv or case.timeout_seconds <= 0:
        raise ValueError(f"invalid matrix command contract: {case.case_id}")
    stdout_path = output / f"{case.case_id}.stdout.log"
    stderr_path = output / f"{case.case_id}.stderr.log"
    started = time.time()
    try:
        completed = subprocess.run(
            list(case.argv),
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=case.timeout_seconds,
            check=False,
            env=os.environ.copy(),
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        status = "pass" if returncode == 0 else "fail"
        reason = None
    except subprocess.TimeoutExpired as exc:
        returncode = None
        stdout = _decoded_timeout_stream(exc.stdout)
        stderr = _decoded_timeout_stream(exc.stderr)
        status = "fail"
        reason = f"timed out after {case.timeout_seconds} seconds"
    except OSError as exc:
        returncode = None
        stdout = ""
        stderr = str(exc)
        status = "fail"
        reason = "command could not be started"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    row = {
        "case_id": case.case_id,
        "lane": case.lane,
        "status": status,
        "argv": list(case.argv),
        "timeout_seconds": case.timeout_seconds,
        "duration_seconds": round(time.time() - started, 3),
        "returncode": returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_sha256": _hash_file(stdout_path),
        "stderr_sha256": _hash_file(stderr_path),
    }
    if reason is not None:
        row["reason"] = reason
    return row


def _require_fresh_output(output: Path, validation: Path) -> None:
    if output == validation or validation in output.parents:
        raise ValueError("matrix output must not be inside validation_root")
    if output.exists():
        if not output.is_dir():
            raise ValueError("output_directory must be a directory")
        if any(output.iterdir()):
            raise FileExistsError("output_directory must be new or empty")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decoded_timeout_stream(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _utc_timestamp(value: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))
