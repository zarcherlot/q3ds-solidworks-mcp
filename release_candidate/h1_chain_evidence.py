"""Fail-closed verification for one completed five-Skill production chain.

This module never calls SolidWorks.  It verifies the immutable evidence ledger emitted by a
future live H runner and deliberately rejects F7/G7 qualification tools as substitutes for the
production create/verify transactions.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from drawing_planner.planning_models import canonical_json_sha256

from .h0_readiness import validate_h0_readiness_report

PROTOCOL_ID = "solidworks-five-skill-chain-evidence"
SCHEMA_VERSION = "1.0"
PACKAGE_ROOT = Path(__file__).resolve().parent
SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h1-chain-evidence.schema.json"

_SKILLS = (
    "bootstrap-solidworks-host",
    "solidworks-initialize-drawing-handoff",
    "solidworks-create-drawing-views",
    "solidworks-dimension-drawing",
    "solidworks-finalize-drawing-layout",
)
_CORE_OPERATIONS = {
    "bootstrap-solidworks-host": ("inspect_solidworks_host",),
    "solidworks-initialize-drawing-handoff": (
        "initialize_part_drawing_handoff",
    ),
    "solidworks-create-drawing-views": (
        "publish_validated_part_drawing_view_plan",
        "validate_part_drawing_view_plan",
        "create_part_drawing_from_view_plan",
        "verify_part_drawing_view_plan",
    ),
    "solidworks-dimension-drawing": (
        "initialize_part_drawing_dimension_handoff",
        "publish_validated_part_drawing_dimension_plan",
        "validate_part_drawing_dimension_plan",
        "create_dimensioned_part_drawing",
        "verify_dimensioned_part_drawing",
    ),
    "solidworks-finalize-drawing-layout": (
        "initialize_part_drawing_layout_handoff",
        "publish_validated_part_drawing_layout_plan",
        "validate_part_drawing_layout_plan",
        "create_final_part_drawing",
        "verify_final_part_drawing",
    ),
}
_OPTIONAL_TOOLS = {
    "bootstrap-solidworks-host": {"bootstrap_solidworks_host"},
    "solidworks-initialize-drawing-handoff": {"solidworks_status"},
    "solidworks-create-drawing-views": {"solidworks_status", "inspect_part_for_drawing"},
    "solidworks-dimension-drawing": {"solidworks_status"},
    "solidworks-finalize-drawing-layout": {"solidworks_status"},
}
_QUALIFICATION_TOOLS = {
    "qualify_dimensioned_part_drawing",
    "verify_qualified_dimensioned_part_drawing",
    "qualify_final_part_drawing",
    "verify_qualified_final_part_drawing",
}
_EXPECTED_ROLES = {
    "solidworks-initialize-drawing-handoff": {
        "inputs": {"source_model", "drawing_template"},
        "outputs": {
            "initializer_handoff",
            "blank_drawing",
            "readiness_report",
            "geometry_report",
            "front_image",
            "back_image",
            "left_image",
            "right_image",
            "top_image",
            "bottom_image",
        },
    },
    "solidworks-create-drawing-views": {
        "inputs": {"initializer_handoff", "blank_drawing"},
        "outputs": {"view_plan", "view_drawing", "view_verification_sidecar"},
    },
    "solidworks-dimension-drawing": {
        "inputs": {"view_plan", "view_drawing", "view_verification_sidecar"},
        "outputs": {
            "dimension_handoff",
            "dimension_plan",
            "dimensioned_drawing",
            "dimension_verification_sidecar",
        },
    },
    "solidworks-finalize-drawing-layout": {
        "inputs": {
            "dimension_plan",
            "dimensioned_drawing",
            "dimension_verification_sidecar",
        },
        "outputs": {
            "layout_handoff",
            "layout_plan",
            "final_drawing",
            "final_verification_sidecar",
        },
    },
}
_PLAN_ROLES = {
    "view_plan": ("solidworks-view-plan", "1.4", "view plan"),
    "dimension_plan": ("solidworks-dimension-plan", "1.0", "dimension plan"),
    "layout_plan": ("solidworks-drawing-layout-plan", "1.0", "drawing layout plan"),
}


class H1ChainEvidenceError(ValueError):
    """Raised when a five-Skill chain cannot be accepted as production evidence."""


def validate_h1_chain_evidence(candidate: Mapping[str, Any]) -> dict[str, Any]:
    evidence = _json_copy(candidate)
    _validate_schema(evidence)
    _validate_h0_binding(evidence)
    _verify_artifact(evidence["execution_service"], "execution_service")
    _validate_immutable_inputs(evidence["immutable_inputs"])
    _validate_stages(evidence["stages"])
    _validate_cross_stage_continuity(evidence["stages"], evidence["immutable_inputs"])
    _validate_final(evidence)
    return evidence


def validate_and_publish_h1_chain_evidence(
    candidate: Mapping[str, Any], output_path: Path
) -> dict[str, Any]:
    evidence = validate_h1_chain_evidence(candidate)
    path, sha256 = _publish_once(evidence, output_path)
    return {
        "ok": True,
        "status": "complete",
        "evidence_path": path,
        "evidence_sha256": sha256,
        "git_commit": evidence["git_commit"],
        "stage_count": 5,
        "production_only": True,
    }


def _validate_h0_binding(evidence: Mapping[str, Any]) -> None:
    path = _verify_artifact(evidence["h0_readiness"], "h0_readiness")
    readiness = _load_json(path)
    try:
        validate_h0_readiness_report(PACKAGE_ROOT.parent, readiness)
    except Exception as exc:
        raise H1ChainEvidenceError(f"H0 readiness contract is invalid: {exc}") from exc
    if (
        readiness.get("protocol_id") != "solidworks-five-skill-release-readiness"
        or readiness.get("schema_version") != "1.0"
        or readiness.get("status") != "ready"
        or readiness.get("git", {}).get("commit") != evidence["git_commit"]
        or readiness.get("git", {}).get("clean") is not True
    ):
        raise H1ChainEvidenceError(
            "H1 requires one ready, clean H0 report bound to the exact evidence commit"
        )


def _validate_immutable_inputs(rows: Sequence[Mapping[str, Any]]) -> None:
    if {row["role"] for row in rows} != {"source_model", "drawing_template"}:
        raise H1ChainEvidenceError("immutable inputs must be source_model and drawing_template")
    for row in rows:
        path = Path(row["path"]).resolve(strict=True)
        actual = _sha256(path)
        if row["sha256_before"] != row["sha256_after"] or actual != row["sha256_after"]:
            raise H1ChainEvidenceError(f"immutable input changed during H1: {row['role']}")


def _validate_stages(stages: Sequence[Mapping[str, Any]]) -> None:
    if tuple(stage["skill"] for stage in stages) != _SKILLS or tuple(
        stage["order"] for stage in stages
    ) != (1, 2, 3, 4, 5):
        raise H1ChainEvidenceError("H1 stages must preserve the exact five-Skill order")
    all_response_paths: set[str] = set()
    all_output_paths: set[str] = set()
    all_sequences: list[int] = []
    for stage in stages:
        skill = stage["skill"]
        _unique_roles(stage["inputs"], f"{skill}.inputs")
        outputs = _unique_roles(stage["outputs"], f"{skill}.outputs")
        if skill in _EXPECTED_ROLES:
            expected = _EXPECTED_ROLES[skill]
            if set(_by_role(stage["inputs"])) != expected["inputs"]:
                raise H1ChainEvidenceError(f"{skill} input artifact roles are incomplete")
            if set(outputs) != expected["outputs"]:
                raise H1ChainEvidenceError(f"{skill} output artifact roles are incomplete")
        for artifact in (*stage["inputs"], *stage["outputs"]):
            _verify_artifact(artifact, f"{skill}.{artifact['role']}")
        for artifact in stage["outputs"]:
            key = _path_key(artifact["path"])
            if _is_validation_path(Path(artifact["path"])):
                raise H1ChainEvidenceError("H1 live outputs must not be written under validation/")
            if key in all_output_paths:
                raise H1ChainEvidenceError("one artifact path cannot be emitted by two stages")
            all_output_paths.add(key)
        _validate_operations(stage, all_response_paths)
        all_sequences.extend(row["sequence"] for row in stage["operations"])
        for role, (protocol, version, canonical_label) in _PLAN_ROLES.items():
            if role in outputs:
                plan = _load_json(Path(outputs[role]["path"]))
                if plan.get("protocol_id") != protocol or plan.get("schema_version") != version:
                    raise H1ChainEvidenceError(f"{role} protocol/version mismatch")
                canonical_sha256 = canonical_json_sha256(plan, canonical_label)
                plan_tools = tuple(
                    tool
                    for tool in _CORE_OPERATIONS[skill]
                    if not tool.startswith("initialize_")
                )
                responses = _responses_by_tool(stage)
                _same_hash_field(
                    responses,
                    plan_tools,
                    "plan_canonical_sha256",
                    canonical_sha256,
                )
        _validate_stage_request_hashes(stage)
    if all_sequences != list(range(1, len(all_sequences) + 1)):
        raise H1ChainEvidenceError("H1 semantic operation sequence is not globally contiguous")


def _validate_operations(stage: Mapping[str, Any], response_paths: set[str]) -> None:
    operations = stage["operations"]
    sequences = [row["sequence"] for row in operations]
    if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
        raise H1ChainEvidenceError(f"{stage['skill']} operation sequence is invalid")
    tools = [row["tool"] for row in operations]
    if any(tool in _QUALIFICATION_TOOLS for tool in tools):
        raise H1ChainEvidenceError("qualification tools cannot substitute for H1 production tools")
    allowed = set(_CORE_OPERATIONS[stage["skill"]]) | _OPTIONAL_TOOLS[stage["skill"]]
    if any(tool not in allowed for tool in tools):
        raise H1ChainEvidenceError(f"{stage['skill']} used a tool outside its H1 allow-list")
    if stage["skill"] == "bootstrap-solidworks-host" and tools not in (
        ["inspect_solidworks_host"],
        ["inspect_solidworks_host", "bootstrap_solidworks_host"],
    ):
        raise H1ChainEvidenceError(
            "bootstrap stage must inspect first and may perform one explicit repair"
        )
    core = tuple(tool for tool in tools if tool in _CORE_OPERATIONS[stage["skill"]])
    if core != _CORE_OPERATIONS[stage["skill"]]:
        raise H1ChainEvidenceError(f"{stage['skill']} production operation order is incomplete")
    for operation in operations:
        path = _verify_artifact(operation["response"], f"{operation['tool']}.response")
        key = _path_key(path)
        if key in response_paths:
            raise H1ChainEvidenceError("each semantic operation requires one distinct response artifact")
        response_paths.add(key)
        response = _load_json(path)
        if response.get("ok") is not True:
            raise H1ChainEvidenceError(f"semantic operation did not succeed: {operation['tool']}")
        expected_status = _expected_status(operation["tool"])
        if expected_status is not None and response.get("status") != expected_status:
            raise H1ChainEvidenceError(
                f"semantic operation status mismatch: {operation['tool']}"
            )


def _validate_stage_request_hashes(stage: Mapping[str, Any]) -> None:
    responses = _responses_by_tool(stage)
    skill = stage["skill"]
    if skill == "solidworks-create-drawing-views":
        _same_hash_field(responses, _CORE_OPERATIONS[skill], "planning_request_sha256")
        _same_hash_field(responses, _CORE_OPERATIONS[skill], "plan_canonical_sha256")
    elif skill == "solidworks-dimension-drawing":
        initializer = responses["initialize_part_drawing_dimension_handoff"]
        expected = initializer.get("planning_request_sha256")
        _same_hash_field(
            responses, _CORE_OPERATIONS[skill][1:], "planning_request_sha256", expected
        )
        _same_hash_field(responses, _CORE_OPERATIONS[skill][1:], "plan_canonical_sha256")
    elif skill == "solidworks-finalize-drawing-layout":
        initializer = responses["initialize_part_drawing_layout_handoff"]
        dimension_hash = initializer.get("source_dimension_request_sha256")
        _same_hash_field(
            responses, _CORE_OPERATIONS[skill][1:], "source_dimension_request_sha256",
            dimension_hash,
        )
        _same_hash_field(responses, _CORE_OPERATIONS[skill][1:], "planning_request_sha256")
        _same_hash_field(responses, _CORE_OPERATIONS[skill][1:], "plan_canonical_sha256")


def _same_hash_field(
    responses: Mapping[str, Mapping[str, Any]],
    tools: Sequence[str],
    field: str,
    expected: object = None,
) -> None:
    values = [_response_hash(responses[tool], field) for tool in tools]
    if expected is not None:
        values.append(expected)
    if not values or any(not _is_sha256(value) for value in values) or len(set(values)) != 1:
        raise H1ChainEvidenceError(f"H1 request/plan continuity failed for {field}")


def _response_hash(response: Mapping[str, Any], field: str) -> object:
    direct = response.get(field)
    if direct is not None:
        return direct
    audit = response.get("audit")
    if isinstance(audit, Mapping):
        if field == "planning_request_sha256":
            return audit.get("request_sha256")
        if field == "plan_canonical_sha256":
            return audit.get("candidate_sha256")
    return None


def _validate_cross_stage_continuity(
    stages: Sequence[Mapping[str, Any]], immutable_inputs: Sequence[Mapping[str, Any]]
) -> None:
    source = {
        row["role"]: {"path": row["path"], "sha256": row["sha256_after"]}
        for row in immutable_inputs
    }
    initializer = _by_role(stages[1]["inputs"])
    _same_artifact(source["source_model"], initializer["source_model"], "source model")
    _same_artifact(source["drawing_template"], initializer["drawing_template"], "template")
    initializer_response = _responses_by_tool(stages[1])[
        "initialize_part_drawing_handoff"
    ]
    _same_hash_field(
        _responses_by_tool(stages[2]),
        _CORE_OPERATIONS["solidworks-create-drawing-views"],
        "planning_request_sha256",
        initializer_response.get("planning_request_sha256"),
    )
    _link(stages[1], stages[2], ("initializer_handoff", "blank_drawing"))
    _link(
        stages[2], stages[3],
        ("view_plan", "view_drawing", "view_verification_sidecar"),
    )
    _link(
        stages[3], stages[4],
        ("dimension_plan", "dimensioned_drawing", "dimension_verification_sidecar"),
    )
    dimension_initializer = _responses_by_tool(stages[3])[
        "initialize_part_drawing_dimension_handoff"
    ]
    layout_initializer = _responses_by_tool(stages[4])[
        "initialize_part_drawing_layout_handoff"
    ]
    if (
        not _is_sha256(dimension_initializer.get("planning_request_sha256"))
        or layout_initializer.get("source_dimension_request_sha256")
        != dimension_initializer.get("planning_request_sha256")
    ):
        raise H1ChainEvidenceError(
            "layout stage does not embed the unchanged dimension planning request"
        )
    drawing_roles = (
        "blank_drawing", "view_drawing", "dimensioned_drawing", "final_drawing"
    )
    drawing_paths = [
        _path_key(_by_role(stage["outputs"])[role]["path"])
        for stage, role in zip(stages[1:], drawing_roles, strict=True)
    ]
    if len(set(drawing_paths)) != 4:
        raise H1ChainEvidenceError("each drawing stage must create one new successor drawing")


def _validate_final(evidence: Mapping[str, Any]) -> None:
    outputs = _by_role(evidence["stages"][-1]["outputs"])
    _same_artifact(outputs["final_drawing"], evidence["final_artifacts"]["drawing"], "final drawing")
    _same_artifact(
        outputs["final_verification_sidecar"],
        evidence["final_artifacts"]["verification_sidecar"],
        "final verification sidecar",
    )
    sidecar_path = _verify_artifact(
        evidence["final_artifacts"]["verification_sidecar"], "final sidecar"
    )
    sidecar = _load_json(sidecar_path)
    drawing = evidence["final_artifacts"]["drawing"]
    if (
        sidecar.get("protocol_id") != "solidworks-drawing-layout-verification"
        or sidecar.get("schema_version") != "1.0"
        or sidecar.get("verified") is not True
        or Path(str(sidecar.get("output_path"))).resolve() != Path(drawing["path"]).resolve()
        or sidecar.get("artifact_sha256") != drawing["sha256"]
    ):
        raise H1ChainEvidenceError("final read-only verification sidecar is not bound to the drawing")


def _expected_status(tool: str) -> str | None:
    if tool.startswith("publish_validated_"):
        return "published"
    if tool.startswith("validate_"):
        return "VALID"
    if tool.startswith("create_") or tool.startswith("verify_"):
        return "COMPLETED"
    if tool == "initialize_part_drawing_dimension_handoff":
        return "ready"
    if tool == "initialize_part_drawing_layout_handoff":
        return "ready"
    return None


def _link(
    producer: Mapping[str, Any], consumer: Mapping[str, Any], roles: Sequence[str]
) -> None:
    outputs = _by_role(producer["outputs"])
    inputs = _by_role(consumer["inputs"])
    for role in roles:
        _same_artifact(outputs[role], inputs[role], role)


def _same_artifact(left: Mapping[str, Any], right: Mapping[str, Any], label: str) -> None:
    if _path_key(left["path"]) != _path_key(right["path"]) or left["sha256"] != right["sha256"]:
        raise H1ChainEvidenceError(f"cross-stage artifact continuity failed: {label}")


def _unique_roles(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    result = _by_role(rows)
    if len(result) != len(rows):
        raise H1ChainEvidenceError(f"{label} contains duplicate artifact roles")
    return result


def _by_role(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {row["role"]: row for row in rows}


def _responses_by_tool(stage: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        row["tool"]: _load_json(Path(row["response"]["path"]))
        for row in stage["operations"]
    }


def _verify_artifact(binding: Mapping[str, Any], label: str) -> Path:
    path = Path(str(binding["path"]))
    if not path.is_absolute() or not path.is_file():
        raise H1ChainEvidenceError(f"{label} must bind an existing absolute file")
    resolved = path.resolve(strict=True)
    if _sha256(resolved) != binding["sha256"]:
        raise H1ChainEvidenceError(f"{label} SHA-256 mismatch")
    return resolved


def _validate_schema(candidate: Mapping[str, Any]) -> None:
    schema = _load_json(SCHEMA_PATH)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(candidate),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise H1ChainEvidenceError(
            f"H1 chain evidence contract failed at {pointer or '/'}: {error.message}"
        )


def _json_copy(candidate: Mapping[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise H1ChainEvidenceError(f"H1 evidence is not strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise H1ChainEvidenceError("H1 evidence must be an object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise H1ChainEvidenceError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise H1ChainEvidenceError(f"JSON artifact must contain an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_key(value: object) -> str:
    return os.path.normcase(str(Path(str(value)).resolve()))


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _publish_once(value: Mapping[str, Any], output_path: Path) -> tuple[str, str]:
    output = output_path.resolve()
    if (
        output.exists()
        or not output.parent.is_dir()
        or output.suffix.lower() != ".json"
        or _is_validation_path(output)
    ):
        raise H1ChainEvidenceError(
            "H1 evidence output must be a new JSON file outside validation/ in an existing directory"
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


def _is_validation_path(path: Path) -> bool:
    validation = (PACKAGE_ROOT.parent / "validation").resolve()
    resolved = path.resolve()
    return resolved == validation or validation in resolved.parents
