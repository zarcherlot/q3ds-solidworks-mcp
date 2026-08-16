"""Append-only capture for an H2-authorized five-Skill production session."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .h1_chain_evidence import validate_h1_chain_evidence
from .h2_session_preflight import PRODUCTION_SCHEDULE, planned_session_outputs


PACKAGE_ROOT = Path(__file__).resolve().parent
MANIFEST_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h3-session-manifest.schema.json"
STAGE_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h3-stage-capture.schema.json"
PREFLIGHT_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h2-session-preflight.schema.json"

_SKILLS = (
    "bootstrap-solidworks-host",
    "solidworks-initialize-drawing-handoff",
    "solidworks-create-drawing-views",
    "solidworks-dimension-drawing",
    "solidworks-finalize-drawing-layout",
)
_STAGE_ROLES = {
    1: (set(), set()),
    2: (
        {"source_model", "drawing_template"},
        {
            "initializer_handoff", "blank_drawing", "readiness_report",
            "geometry_report", "front_image", "back_image", "left_image",
            "right_image", "top_image", "bottom_image",
        },
    ),
    3: (
        {"initializer_handoff", "blank_drawing"},
        {"view_plan", "view_drawing", "view_verification_sidecar"},
    ),
    4: (
        {"view_plan", "view_drawing", "view_verification_sidecar"},
        {
            "dimension_handoff", "dimension_plan", "dimensioned_drawing",
            "dimension_verification_sidecar",
        },
    ),
    5: (
        {"dimension_plan", "dimensioned_drawing", "dimension_verification_sidecar"},
        {"layout_handoff", "layout_plan", "final_drawing", "final_verification_sidecar"},
    ),
}
_PLANNED_ROLE_KEYS = {
    "initializer_handoff": "initializer_handoff",
    "blank_drawing": "blank_drawing",
    "view_plan": "view_plan",
    "view_drawing": "view_drawing",
    "view_verification_sidecar": "view_verification_sidecar",
    "dimension_handoff": "dimension_handoff",
    "dimension_plan": "dimension_plan",
    "dimensioned_drawing": "dimension_drawing",
    "dimension_verification_sidecar": "dimension_verification_sidecar",
    "layout_handoff": "layout_handoff",
    "layout_plan": "layout_plan",
    "final_drawing": "final_drawing",
    "final_verification_sidecar": "final_verification_sidecar",
}


class H3SessionCaptureError(ValueError):
    """Raised when an append-only H3 session operation fails closed."""


def create_h3_session(
    preflight_path: Path,
    preflight_sha256: str,
    repository_root: Path,
    *,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    preflight_file = preflight_path.resolve(strict=True)
    if _sha256(preflight_file) != preflight_sha256:
        raise H3SessionCaptureError("H2 preflight SHA-256 mismatch")
    preflight = _load_json(preflight_file)
    _validate(PREFLIGHT_SCHEMA_PATH, preflight, "H2 preflight")
    if preflight["status"] != "ready" or preflight["blockers"]:
        raise H3SessionCaptureError("H3 requires one blocker-free ready H2 preflight")
    _revalidate_session_inputs(preflight, repository_root)

    session_root = Path(preflight["session_root"])
    if session_root.exists() or not session_root.parent.is_dir():
        raise H3SessionCaptureError("H3 session root must still be new")
    expected_schedule = _schedule()
    if preflight["schedule"] != expected_schedule:
        raise H3SessionCaptureError("H2 production schedule drifted before H3")
    if preflight["planned_outputs"] != planned_session_outputs(session_root):
        raise H3SessionCaptureError("H2 planned output namespace drifted before H3")
    manifest = {
        "protocol_id": "solidworks-five-skill-live-session",
        "schema_version": "1.0",
        "created_at_utc": created_at_utc or _utc_now(),
        "solidworks_revision": preflight["solidworks_revision"],
        "git_commit": preflight["git_commit"],
        "repository_root": str(repository_root.resolve(strict=True)),
        "preflight": {"path": str(preflight_file), "sha256": preflight_sha256},
        "h0_readiness": preflight["h0_readiness"],
        "execution_service": preflight["execution_service"],
        "source_model": preflight["source_model"],
        "drawing_template": preflight["drawing_template"],
        "session_root": str(session_root),
        "planned_outputs": preflight["planned_outputs"],
        "schedule": expected_schedule,
        "append_only": True,
    }
    _validate(MANIFEST_SCHEMA_PATH, manifest, "H3 session manifest")

    session_root.mkdir()
    for path in _session_directories(manifest):
        path.mkdir()
    manifest_path = session_root / "session-manifest.json"
    _publish_once(manifest, manifest_path)
    return {
        "ok": True,
        "status": "created",
        "session_manifest_path": str(manifest_path.resolve(strict=True)),
        "session_manifest_sha256": _sha256(manifest_path),
        "next_sequence": 1,
        "next_tool": expected_schedule[0]["tool"],
        "solidworks_contacted": False,
    }


def capture_h3_operation(
    session_manifest_path: Path,
    session_manifest_sha256: str,
    tool: str,
    response: Mapping[str, Any],
) -> dict[str, Any]:
    manifest, manifest_path = _session_manifest(
        session_manifest_path, session_manifest_sha256
    )
    completed = _captured_operations(manifest)
    if completed and completed[-1]["successful"] is not True:
        raise H3SessionCaptureError("the H3 session is permanently stopped by a failed response")
    if len(completed) >= len(manifest["schedule"]):
        raise H3SessionCaptureError("all H3 semantic operations are already captured")
    expected = manifest["schedule"][len(completed)]
    if (
        expected["stage_order"] > 1
        and (
            not completed
            or completed[-1]["stage_order"] != expected["stage_order"]
        )
        and not _stage_path(manifest, expected["stage_order"] - 1).is_file()
    ):
        raise H3SessionCaptureError(
            "the preceding H3 stage must be frozen before the next stage starts"
        )
    if tool != expected["tool"]:
        raise H3SessionCaptureError(
            f"H3 expected operation {expected['sequence']} {expected['tool']}, got {tool}"
        )
    payload = _json_copy(response, "semantic response")
    successful = _response_succeeded(tool, payload)
    response_directory = Path(manifest["planned_outputs"]["response_directory"])
    event_path = response_directory / f"{expected['sequence']:02d}-{tool}.json"
    # The captured file is the exact semantic response consumed by H1. Sequence, tool and stage
    # identity are fixed by the H2 schedule and immutable filename rather than a mutable wrapper.
    _publish_once(payload, event_path)
    return {
        "ok": successful,
        "status": "captured" if successful else "blocked",
        "event_path": str(event_path.resolve(strict=True)),
        "event_sha256": _sha256(event_path),
        "sequence": expected["sequence"],
        "next_tool": (
            manifest["schedule"][expected["sequence"]]["tool"]
            if successful and expected["sequence"] < len(manifest["schedule"])
            else None
        ),
        "session_manifest_path": str(manifest_path),
    }


def inspect_h3_session(
    session_manifest_path: Path,
    session_manifest_sha256: str,
) -> dict[str, Any]:
    """Return the only legal next action without changing the H3 session."""
    manifest, manifest_path = _session_manifest(
        session_manifest_path, session_manifest_sha256
    )
    completed = _captured_operations(manifest)
    captured_count = len(completed)
    if completed and completed[-1]["successful"] is not True:
        return {
            "ok": False,
            "status": "blocked",
            "session_manifest_path": str(manifest_path),
            "captured_operation_count": captured_count,
            "next_sequence": None,
            "next_stage_order": None,
            "next_skill": None,
            "next_tool": None,
        }

    complete_stage_orders = {
        row["stage_order"]
        for row in manifest["schedule"]
        if row["sequence"] <= captured_count
        and not any(
            later["stage_order"] == row["stage_order"]
            and later["sequence"] > captured_count
            for later in manifest["schedule"]
        )
    }
    existing_stage_orders: set[int] = set()
    for order in range(1, 6):
        stage_path = _stage_path(manifest, order)
        if stage_path.is_file():
            if order not in complete_stage_orders:
                raise H3SessionCaptureError(
                    f"H3 stage {order} was frozen before its operations completed"
                )
            _load_stage(manifest, order, session_manifest_sha256)
            existing_stage_orders.add(order)
    if existing_stage_orders and existing_stage_orders != set(
        range(1, max(existing_stage_orders) + 1)
    ):
        raise H3SessionCaptureError("H3 stage captures are not a contiguous prefix")
    for order in range(1, 6):
        if order in complete_stage_orders and order not in existing_stage_orders:
            return {
                "ok": True,
                "status": "awaiting_stage_capture",
                "session_manifest_path": str(manifest_path),
                "captured_operation_count": captured_count,
                "next_sequence": None,
                "next_stage_order": order,
                "next_skill": _SKILLS[order - 1],
                "next_tool": None,
            }

    if captured_count == len(manifest["schedule"]):
        return {
            "ok": True,
            "status": "ready_to_finalize",
            "session_manifest_path": str(manifest_path),
            "captured_operation_count": captured_count,
            "next_sequence": None,
            "next_stage_order": None,
            "next_skill": None,
            "next_tool": None,
        }

    expected = manifest["schedule"][captured_count]
    return {
        "ok": True,
        "status": "awaiting_operation",
        "session_manifest_path": str(manifest_path),
        "captured_operation_count": captured_count,
        "next_sequence": expected["sequence"],
        "next_stage_order": expected["stage_order"],
        "next_skill": expected["skill"],
        "next_tool": expected["tool"],
        "mutating": expected["mutating"],
    }


def capture_h3_stage(
    session_manifest_path: Path,
    session_manifest_sha256: str,
    order: int,
    inputs: Sequence[Mapping[str, Any]],
    outputs: Sequence[Mapping[str, Any]],
    *,
    captured_at_utc: str | None = None,
) -> dict[str, Any]:
    manifest, _ = _session_manifest(session_manifest_path, session_manifest_sha256)
    if order not in range(1, 6):
        raise H3SessionCaptureError("H3 stage order must be 1 through 5")
    events = _captured_operations(manifest)
    expected_sequences = [
        row["sequence"] for row in manifest["schedule"] if row["stage_order"] == order
    ]
    actual_sequences = [row["sequence"] for row in events if row["stage_order"] == order]
    if actual_sequences != expected_sequences or any(
        not row["successful"] for row in events if row["stage_order"] == order
    ):
        raise H3SessionCaptureError("H3 stage artifacts require every stage operation to succeed")
    for earlier in range(1, order):
        if not _stage_path(manifest, earlier).is_file():
            raise H3SessionCaptureError("H3 stages must be frozen in order")

    normalized_inputs = _artifact_rows(inputs, "inputs", manifest)
    normalized_outputs = _artifact_rows(outputs, "outputs", manifest)
    expected_inputs, expected_outputs = _STAGE_ROLES[order]
    if {row["role"] for row in normalized_inputs} != expected_inputs:
        raise H3SessionCaptureError("H3 stage input roles are incomplete")
    if {row["role"] for row in normalized_outputs} != expected_outputs:
        raise H3SessionCaptureError("H3 stage output roles are incomplete")
    _validate_planned_output_paths(manifest, normalized_outputs)
    capture = {
        "protocol_id": "solidworks-five-skill-stage-capture",
        "schema_version": "1.0",
        "captured_at_utc": captured_at_utc or _utc_now(),
        "session_manifest_sha256": session_manifest_sha256,
        "order": order,
        "skill": _SKILLS[order - 1],
        "inputs": normalized_inputs,
        "outputs": normalized_outputs,
    }
    _validate(STAGE_SCHEMA_PATH, capture, "H3 stage capture")
    path = _stage_path(manifest, order)
    _publish_once(capture, path)
    return {
        "ok": True,
        "status": "captured",
        "stage_path": str(path.resolve(strict=True)),
        "stage_sha256": _sha256(path),
        "order": order,
    }


def finalize_h3_session(
    session_manifest_path: Path,
    session_manifest_sha256: str,
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    manifest, _ = _session_manifest(session_manifest_path, session_manifest_sha256)
    events = _captured_operations(manifest)
    if len(events) != 16 or any(not row["successful"] for row in events):
        raise H3SessionCaptureError("H3 cannot finalize before all 16 operations succeed")
    stages = [_load_stage(manifest, order, session_manifest_sha256) for order in range(1, 6)]
    _ensure_unchanged(manifest["source_model"], "source_model")
    _ensure_unchanged(manifest["drawing_template"], "drawing_template")
    _ensure_unchanged(manifest["execution_service"], "execution_service")

    event_by_sequence = {row["sequence"]: row for row in events}
    h1_stages = []
    for stage in stages:
        operations = []
        for schedule in manifest["schedule"]:
            if schedule["stage_order"] != stage["order"]:
                continue
            event = event_by_sequence[schedule["sequence"]]
            operations.append(
                {
                    "sequence": schedule["sequence"],
                    "tool": schedule["tool"],
                    "response": event["response_binding"],
                }
            )
        h1_stages.append(
            {
                "order": stage["order"],
                "skill": stage["skill"],
                "inputs": stage["inputs"],
                "outputs": stage["outputs"],
                "operations": operations,
            }
        )
    final_outputs = {row["role"]: row for row in stages[-1]["outputs"]}
    candidate = {
        "protocol_id": "solidworks-five-skill-chain-evidence",
        "schema_version": "1.0",
        "solidworks_revision": manifest["solidworks_revision"],
        "generated_at_utc": generated_at_utc or _utc_now(),
        "git_commit": manifest["git_commit"],
        "h0_readiness": manifest["h0_readiness"],
        "execution_service": manifest["execution_service"],
        "immutable_inputs": [
            {
                "role": role,
                "path": manifest[role]["path"],
                "sha256_before": manifest[role]["sha256"],
                "sha256_after": _sha256(Path(manifest[role]["path"])),
            }
            for role in ("source_model", "drawing_template")
        ],
        "stages": h1_stages,
        "final_artifacts": {
            "drawing": _without_role(final_outputs["final_drawing"]),
            "verification_sidecar": _without_role(
                final_outputs["final_verification_sidecar"]
            ),
        },
    }
    validate_h1_chain_evidence(candidate)
    output = Path(manifest["planned_outputs"]["h1_candidate"])
    _publish_once(candidate, output)
    return {
        "ok": True,
        "status": "complete",
        "h1_candidate_path": str(output.resolve(strict=True)),
        "h1_candidate_sha256": _sha256(output),
        "operation_count": 16,
        "stage_count": 5,
    }


def _session_manifest(path: Path, expected_sha256: str) -> tuple[dict[str, Any], Path]:
    resolved = path.resolve(strict=True)
    if _sha256(resolved) != expected_sha256:
        raise H3SessionCaptureError("H3 session manifest SHA-256 mismatch")
    value = _load_json(resolved)
    _validate(MANIFEST_SCHEMA_PATH, value, "H3 session manifest")
    if resolved != Path(value["session_root"]).resolve() / "session-manifest.json":
        raise H3SessionCaptureError("H3 session manifest path drifted")
    for role in (
        "preflight",
        "h0_readiness",
        "execution_service",
        "source_model",
        "drawing_template",
    ):
        _ensure_unchanged(value[role], role)
    git = _git_state(Path(value["repository_root"]).resolve(strict=True))
    if git["commit"] != value["git_commit"] or not git["clean"]:
        raise H3SessionCaptureError("repository commit/worktree changed during H3")
    return value, resolved


def _captured_operations(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    directory = Path(manifest["planned_outputs"]["response_directory"])
    files = sorted(directory.glob("*.json"))
    events: list[dict[str, Any]] = []
    for index, path in enumerate(files, 1):
        response = _load_json(path)
        expected = manifest["schedule"][index - 1] if index <= 16 else None
        if (
            expected is None
            or path.name != f"{index:02d}-{expected['tool']}.json"
        ):
            raise H3SessionCaptureError(f"invalid append-only operation event: {path}")
        events.append(
            {
                "sequence": index,
                "stage_order": expected["stage_order"],
                "skill": expected["skill"],
                "tool": expected["tool"],
                "successful": _response_succeeded(expected["tool"], response),
                "response": response,
                "response_binding": {
                    "path": str(path.resolve()),
                    "sha256": _sha256(path),
                },
            }
        )
    return events


def _artifact_rows(
    rows: Sequence[Mapping[str, Any]], label: str, manifest: Mapping[str, Any]
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    roles: set[str] = set()
    session_root = Path(manifest["session_root"]).resolve()
    for row in rows:
        if set(row) != {"role", "path"} or not isinstance(row["role"], str):
            raise H3SessionCaptureError(f"H3 {label} rows require exactly role/path")
        if row["role"] in roles:
            raise H3SessionCaptureError(f"H3 {label} contains duplicate roles")
        roles.add(row["role"])
        path = Path(str(row["path"]))
        if not path.is_absolute() or not path.is_file():
            raise H3SessionCaptureError(f"H3 {label} artifact must be an existing absolute file")
        resolved = path.resolve(strict=True)
        if label == "outputs" and session_root not in resolved.parents:
            raise H3SessionCaptureError("H3 stage outputs must remain inside the session root")
        normalized.append(
            {"role": row["role"], "path": str(resolved), "sha256": _sha256(resolved)}
        )
    return normalized


def _validate_planned_output_paths(
    manifest: Mapping[str, Any], outputs: Sequence[Mapping[str, str]]
) -> None:
    planned = manifest["planned_outputs"]
    for row in outputs:
        key = _PLANNED_ROLE_KEYS.get(row["role"])
        if key and Path(row["path"]).resolve() != Path(planned[key]).resolve():
            raise H3SessionCaptureError(f"H3 output path drifted: {row['role']}")


def _load_stage(
    manifest: Mapping[str, Any], order: int, session_sha256: str
) -> dict[str, Any]:
    path = _stage_path(manifest, order)
    if not path.is_file():
        raise H3SessionCaptureError(f"H3 stage {order} capture is missing")
    value = _load_json(path)
    _validate(STAGE_SCHEMA_PATH, value, "H3 stage capture")
    if (
        value["order"] != order
        or value["skill"] != _SKILLS[order - 1]
        or value["session_manifest_sha256"] != session_sha256
    ):
        raise H3SessionCaptureError(f"H3 stage {order} binding drifted")
    for row in (*value["inputs"], *value["outputs"]):
        _ensure_unchanged(row, f"stage {order} {row['role']}")
    return value


def _stage_path(manifest: Mapping[str, Any], order: int) -> Path:
    return Path(manifest["planned_outputs"]["stage_directory"]) / (
        f"{order:02d}-{_SKILLS[order - 1]}.json"
    )


def _session_directories(manifest: Mapping[str, Any]) -> list[Path]:
    return [
        Path(manifest["planned_outputs"]["initializer_directory"]),
        Path(manifest["planned_outputs"]["view_drawing"]).parent,
        Path(manifest["planned_outputs"]["dimension_directory"]),
        Path(manifest["planned_outputs"]["layout_directory"]),
        Path(manifest["planned_outputs"]["response_directory"]),
        Path(manifest["planned_outputs"]["stage_directory"]),
    ]


def _revalidate_session_inputs(preflight: Mapping[str, Any], repository_root: Path) -> None:
    for role in ("h0_readiness", "execution_service", "source_model", "drawing_template"):
        _ensure_unchanged(preflight[role], role)
    git = _git_state(repository_root.resolve(strict=True))
    if git["commit"] != preflight["git_commit"] or not git["clean"]:
        raise H3SessionCaptureError("repository commit/worktree changed after H2 preflight")


def _ensure_unchanged(binding: Mapping[str, Any], label: str) -> None:
    path = Path(str(binding["path"]))
    if not path.is_file() or _sha256(path) != binding["sha256"]:
        raise H3SessionCaptureError(f"frozen H3 artifact changed: {label}")


def _response_succeeded(tool: str, payload: Mapping[str, Any]) -> bool:
    if payload.get("ok") is not True:
        return False
    if tool.startswith("publish_validated_"):
        return payload.get("status") == "published"
    if tool.startswith("validate_"):
        return payload.get("status") == "VALID"
    if tool.startswith("create_") or tool.startswith("verify_"):
        return payload.get("status") == "COMPLETED"
    if tool in {
        "initialize_part_drawing_dimension_handoff",
        "initialize_part_drawing_layout_handoff",
    }:
        return payload.get("status") == "ready"
    return True


def _schedule() -> list[dict[str, Any]]:
    return [
        {
            "sequence": index,
            "stage_order": stage,
            "skill": skill,
            "tool": tool,
            "mutating": mutating,
        }
        for index, (stage, skill, tool, mutating) in enumerate(PRODUCTION_SCHEDULE, 1)
    ]


def _git_state(root: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    lines = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.splitlines()
    return {"commit": commit, "clean": not lines}


def _without_role(row: Mapping[str, str]) -> dict[str, str]:
    return {"path": row["path"], "sha256": row["sha256"]}


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
        raise H3SessionCaptureError(
            f"{label} contract failed at {pointer or '/'}: {error.message}"
        )


def _json_copy(candidate: Mapping[str, Any], label: str) -> dict[str, Any]:
    try:
        value = json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise H3SessionCaptureError(f"{label} is not strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise H3SessionCaptureError(f"{label} must be an object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise H3SessionCaptureError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise H3SessionCaptureError(f"JSON artifact must contain an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_once(value: Mapping[str, Any], output: Path) -> None:
    if output.exists() or not output.parent.is_dir():
        raise H3SessionCaptureError(f"append-only output already exists or parent is missing: {output}")
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
