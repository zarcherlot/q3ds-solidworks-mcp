"""COM-free H2 preflight for a future five-Skill production session."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


PROTOCOL_ID = "solidworks-five-skill-session-preflight"
SCHEMA_VERSION = "1.0"
PACKAGE_ROOT = Path(__file__).resolve().parent
REQUEST_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h2-session-request.schema.json"
REPORT_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h2-session-preflight.schema.json"

_SCHEDULE = (
    (1, "bootstrap-solidworks-host", "inspect_solidworks_host", False),
    (2, "solidworks-initialize-drawing-handoff", "initialize_part_drawing_handoff", True),
    (3, "solidworks-create-drawing-views", "publish_validated_part_drawing_view_plan", True),
    (3, "solidworks-create-drawing-views", "validate_part_drawing_view_plan", False),
    (3, "solidworks-create-drawing-views", "create_part_drawing_from_view_plan", True),
    (3, "solidworks-create-drawing-views", "verify_part_drawing_view_plan", False),
    (4, "solidworks-dimension-drawing", "initialize_part_drawing_dimension_handoff", True),
    (4, "solidworks-dimension-drawing", "publish_validated_part_drawing_dimension_plan", True),
    (4, "solidworks-dimension-drawing", "validate_part_drawing_dimension_plan", False),
    (4, "solidworks-dimension-drawing", "create_dimensioned_part_drawing", True),
    (4, "solidworks-dimension-drawing", "verify_dimensioned_part_drawing", False),
    (5, "solidworks-finalize-drawing-layout", "initialize_part_drawing_layout_handoff", True),
    (5, "solidworks-finalize-drawing-layout", "publish_validated_part_drawing_layout_plan", True),
    (5, "solidworks-finalize-drawing-layout", "validate_part_drawing_layout_plan", False),
    (5, "solidworks-finalize-drawing-layout", "create_final_part_drawing", True),
    (5, "solidworks-finalize-drawing-layout", "verify_final_part_drawing", False),
)


class H2SessionPreflightError(ValueError):
    """Raised when the H2 request/report contract itself is invalid."""


def build_h2_session_preflight(
    candidate: Mapping[str, Any], repository_root: Path
) -> dict[str, Any]:
    request = _json_copy(candidate)
    _validate(REQUEST_SCHEMA_PATH, request, "H2 session request")
    root = repository_root.resolve(strict=True)
    blockers: list[dict[str, Any]] = []
    bindings: dict[str, dict[str, str]] = {}
    for role in (
        "h0_readiness",
        "execution_service",
        "source_model",
        "drawing_template",
    ):
        bindings[role] = _binding(request[role], role, blockers)

    _validate_extensions(bindings, blockers)
    _validate_h0(bindings["h0_readiness"], request["git_commit"], blockers)
    git = _git_state(root)
    if git["commit"] != request["git_commit"]:
        _block(
            blockers,
            "git-commit-drift",
            "the current repository commit differs from the H0/session request commit",
            git["commit"],
            request["git_commit"],
        )
    if not git["clean"]:
        _block(
            blockers,
            "git-worktree-not-frozen",
            "the H2 production session requires a clean worktree",
            *git["changed_paths"],
        )

    session_root = Path(request["session_root"])
    if not session_root.is_absolute():
        _block(blockers, "session-root-not-absolute", "session_root must be absolute")
        resolved_session = session_root.resolve()
    else:
        resolved_session = session_root.resolve()
    if resolved_session.exists() or not resolved_session.parent.is_dir():
        _block(
            blockers,
            "session-root-not-new",
            "session_root must be new and its parent must already exist",
            str(resolved_session),
        )
    validation = (root / "validation").resolve()
    if resolved_session == validation or validation in resolved_session.parents:
        _block(
            blockers,
            "session-root-under-validation",
            "H2 live outputs must not be written under validation/",
            str(resolved_session),
        )

    planned = _planned_outputs(resolved_session)
    schedule = [
        {
            "sequence": index,
            "stage_order": stage,
            "skill": skill,
            "tool": tool,
            "mutating": mutating,
        }
        for index, (stage, skill, tool, mutating) in enumerate(_SCHEDULE, 1)
    ]
    report = {
        "protocol_id": PROTOCOL_ID,
        "schema_version": SCHEMA_VERSION,
        "solidworks_revision": request["solidworks_revision"],
        "status": "ready" if not blockers else "blocked",
        "git_commit": request["git_commit"],
        "h0_readiness": bindings["h0_readiness"],
        "execution_service": bindings["execution_service"],
        "source_model": bindings["source_model"],
        "drawing_template": bindings["drawing_template"],
        "session_root": str(resolved_session),
        "planned_outputs": planned,
        "schedule": schedule,
        "solidworks_contacted": False,
        "blockers": blockers,
    }
    _validate(REPORT_SCHEMA_PATH, report, "H2 session preflight")
    return report


def build_and_publish_h2_session_preflight(
    candidate: Mapping[str, Any], repository_root: Path, output_path: Path
) -> dict[str, Any]:
    report = build_h2_session_preflight(candidate, repository_root)
    path, sha256 = _publish_once(report, output_path, repository_root)
    return {
        "ok": report["status"] == "ready",
        "status": report["status"],
        "preflight_path": path,
        "preflight_sha256": sha256,
        "solidworks_contacted": False,
        "blockers": report["blockers"],
    }


def _validate_h0(
    binding: Mapping[str, str], git_commit: str, blockers: list[dict[str, Any]]
) -> None:
    path = Path(binding["path"])
    try:
        value = _load_json(path)
    except H2SessionPreflightError as exc:
        _block(blockers, "h0-readiness-invalid", str(exc), str(path))
        return
    if (
        value.get("protocol_id") != "solidworks-five-skill-release-readiness"
        or value.get("schema_version") != "1.0"
        or value.get("status") != "ready"
        or value.get("git", {}).get("commit") != git_commit
        or value.get("git", {}).get("clean") is not True
    ):
        _block(
            blockers,
            "h0-readiness-not-ready",
            "H2 requires one ready, clean H0 report bound to the exact commit",
            str(path),
        )


def _validate_extensions(
    bindings: Mapping[str, Mapping[str, str]], blockers: list[dict[str, Any]]
) -> None:
    expected = {
        "execution_service": ".exe",
        "source_model": ".sldprt",
        "drawing_template": ".drwdot",
        "h0_readiness": ".json",
    }
    for role, suffix in expected.items():
        if Path(bindings[role]["path"]).suffix.lower() != suffix:
            _block(
                blockers,
                "artifact-extension-invalid",
                f"{role} must use the {suffix} extension",
                bindings[role]["path"],
            )


def _binding(
    candidate: Mapping[str, Any], role: str, blockers: list[dict[str, Any]]
) -> dict[str, str]:
    path = Path(str(candidate["path"]))
    result = {"path": str(path.resolve()), "sha256": candidate["sha256"]}
    if not path.is_absolute() or not path.is_file():
        _block(
            blockers,
            "artifact-missing",
            f"{role} must bind an existing absolute file",
            str(path),
        )
        return result
    resolved = path.resolve(strict=True)
    result["path"] = str(resolved)
    if _sha256(resolved) != candidate["sha256"]:
        _block(
            blockers,
            "artifact-hash-mismatch",
            f"{role} SHA-256 does not match the frozen request",
            str(resolved),
        )
    return result


def _planned_outputs(root: Path) -> dict[str, str]:
    initializer = root / "01-initializer"
    view_drawing = root / "02-views" / "view-drawing.SLDDRW"
    dimensions = root / "03-dimensions"
    dimension_drawing = dimensions / "dimensioned.SLDDRW"
    layout = root / "04-layout"
    final_drawing = layout / "final.SLDDRW"
    return {
        "initializer_directory": str(initializer),
        "initializer_handoff": str(initializer / "drawing-planning-handoff.json"),
        "blank_drawing": str(initializer / "initializer-blank.SLDDRW"),
        "view_plan": str(initializer / "view_plan.json"),
        "view_drawing": str(view_drawing),
        "view_verification_sidecar": str(
            Path(str(view_drawing) + ".verification.json")
        ),
        "dimension_directory": str(dimensions),
        "dimension_handoff": str(dimensions / "dimension-planning-handoff.json"),
        "dimension_plan": str(dimensions / "dimension_plan.json"),
        "dimension_drawing": str(dimension_drawing),
        "dimension_verification_sidecar": str(
            Path(str(dimension_drawing) + ".dimension-verification.json")
        ),
        "layout_directory": str(layout),
        "layout_handoff": str(layout / "drawing-layout-handoff.json"),
        "layout_plan": str(layout / "drawing_layout_plan.json"),
        "final_drawing": str(final_drawing),
        "final_verification_sidecar": str(
            Path(str(final_drawing) + ".layout-verification.json")
        ),
        "response_directory": str(root / "responses"),
        "stage_directory": str(root / "stages"),
        "h1_candidate": str(root / "h1-chain-evidence.candidate.json"),
    }


def _git_state(root: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    lines = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.splitlines()
    return {
        "commit": commit,
        "clean": not lines,
        "changed_paths": sorted(line[3:] for line in lines if len(line) > 3),
    }


def _block(
    blockers: list[dict[str, Any]], code: str, message: str, *references: str
) -> None:
    blockers.append(
        {"code": code, "message": message, "references": [str(row) for row in references]}
    )


def _validate(path: Path, candidate: Mapping[str, Any], label: str) -> None:
    errors = sorted(
        Draft202012Validator(
            _load_json(path), format_checker=FormatChecker()
        ).iter_errors(candidate),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise H2SessionPreflightError(
            f"{label} contract failed at {pointer or '/'}: {error.message}"
        )


def _json_copy(candidate: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise H2SessionPreflightError(f"H2 request is not strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise H2SessionPreflightError("H2 request must be an object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise H2SessionPreflightError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise H2SessionPreflightError(f"JSON artifact must contain an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_once(
    value: Mapping[str, Any], output_path: Path, repository_root: Path
) -> tuple[str, str]:
    output = output_path.resolve()
    validation = (repository_root.resolve() / "validation").resolve()
    if (
        output.exists()
        or not output.parent.is_dir()
        or output.suffix.lower() != ".json"
        or output == validation
        or validation in output.parents
    ):
        raise H2SessionPreflightError(
            "H2 preflight output must be a new JSON file outside validation/"
        )
    payload = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return str(output), _sha256(output)
