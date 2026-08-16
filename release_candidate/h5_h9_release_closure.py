"""Final COM-free H5-H9 closure for one complete five-Skill production chain."""

from __future__ import annotations

import hashlib
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .h1_chain_evidence import validate_h1_chain_evidence
from .h2_session_preflight import PRODUCTION_SCHEDULE
from .h3_session_capture import inspect_h3_session


PACKAGE_ROOT = Path(__file__).resolve().parent
REQUEST_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h5-h9-release-request.schema.json"
CANDIDATE_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h5-h9-release-candidate.schema.json"
H4_CLAIM_SCHEMA_PATH = PACKAGE_ROOT / "contracts" / "h4-semantic-call-claim.schema.json"

_GATE_ORDER = ("H5", "H6", "H7", "H8", "H9")
_PLAN_ROWS = {
    "view_plan": (
        "drawing_planner/contracts/view-plan.schema.json",
        "solidworks-view-plan",
        "1.4",
    ),
    "dimension_plan": (
        "dimension_planner/contracts/dimension-plan.schema.json",
        "solidworks-dimension-plan",
        "1.0",
    ),
    "layout_plan": (
        "drawing_layout_planner/contracts/drawing-layout-plan.schema.json",
        "solidworks-drawing-layout-plan",
        "1.0",
    ),
}
_CSHARP_CONTRACT_ROWS = {
    "view": (
        "solidworks-execution/SolidworksExecution/Contracts/ViewPlanContractValidator.cs",
        "solidworks-execution/SolidworksExecution/Contracts/ViewPlanBasicExecutionCompiler.cs",
        "solidworks-execution/SolidworksExecution/Contracts/ViewPlanBasicTransactionPreflight.cs",
        "solidworks-execution/SolidworksExecution/Contracts/ViewPlanBasicVerificationPreflight.cs",
        "solidworks-execution/SolidworksExecution/Services/ViewPlanBasicDrawingTransaction.cs",
        "solidworks-execution/SolidworksExecution/Services/ViewPlanBasicDrawingVerifier.cs",
    ),
    "dimension": (
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanContractValidator.cs",
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanExecutionCompiler.cs",
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanTransactionPreflight.cs",
        "solidworks-execution/SolidworksExecution/Contracts/DimensionPlanVerificationPreflight.cs",
        "solidworks-execution/SolidworksExecution/Services/DimensionPlanDrawingTransaction.cs",
        "solidworks-execution/SolidworksExecution/Services/DimensionPlanDrawingVerifier.cs",
    ),
    "layout": (
        "solidworks-execution/SolidworksExecution/Contracts/DrawingLayoutPlanContractValidator.cs",
        "solidworks-execution/SolidworksExecution/Contracts/DrawingLayoutPlanExecutionCompiler.cs",
        "solidworks-execution/SolidworksExecution/Contracts/DrawingLayoutPlanTransactionPreflight.cs",
        "solidworks-execution/SolidworksExecution/Contracts/DrawingLayoutPlanVerificationPreflight.cs",
        "solidworks-execution/SolidworksExecution/Services/DrawingLayoutPlanDrawingTransaction.cs",
        "solidworks-execution/SolidworksExecution/Services/DrawingLayoutPlanDrawingVerifier.cs",
    ),
}
_HANDOFF_ROLES = {"initializer_handoff", "dimension_handoff", "layout_handoff"}
_DRAWING_ROLES = {
    "blank_drawing", "view_drawing", "dimensioned_drawing", "final_drawing"
}
_SIDECAR_ROLES = {
    "view_verification_sidecar",
    "dimension_verification_sidecar",
    "final_verification_sidecar",
}


class H5H9ReleaseClosureError(ValueError):
    """Raised when a final five-Skill release candidate cannot be accepted."""


def load_h5_h9_release_request(
    path: Path, expected_sha256: str
) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if _sha256(resolved) != expected_sha256:
        raise H5H9ReleaseClosureError("H5-H9 release request SHA-256 mismatch")
    return validate_h5_h9_release_request(_load_json(resolved))


def validate_h5_h9_release_request(candidate: Mapping[str, Any]) -> dict[str, Any]:
    request = _json_copy(candidate, "H5-H9 release request")
    _validate_schema(REQUEST_SCHEMA_PATH, request, "H5-H9 release request")
    return request


def build_h5_h9_release_candidate(
    candidate: Mapping[str, Any],
    repository_root: Path,
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    request = validate_h5_h9_release_request(candidate)
    root = repository_root.resolve(strict=True)
    h1 = _load_bound_json(request["h1_chain_evidence"], "H1 chain evidence")
    manifest = _load_bound_json(request["h3_session_manifest"], "H3 session manifest")
    try:
        validate_h1_chain_evidence(h1)
    except Exception as exc:
        raise H5H9ReleaseClosureError(f"H5 rejected H1 evidence: {exc}") from exc

    extras: list[dict[str, Any]] = []
    gates = [
        _gate_h5_traceability(request, h1, manifest),
        _gate_h6_plan_contracts(root, h1, extras),
        _gate_h7_semantic_boundary(root, request, h1, manifest, extras),
        _gate_h8_transaction_integrity(root, h1),
    ]
    frozen = _build_frozen_inventory(root, request, h1, manifest, extras)
    gates.append(_gate_h9_freeze(root, h1, frozen))
    report = {
        "protocol_id": "solidworks-five-skill-release-candidate",
        "schema_version": "1.0",
        "generated_at_utc": generated_at_utc or _utc_now(),
        "status": "complete",
        "solidworks_revision": h1["solidworks_revision"],
        "git_commit": h1["git_commit"],
        "request": {
            "h3_session_manifest": request["h3_session_manifest"],
            "h1_chain_evidence": request["h1_chain_evidence"],
        },
        "gates": gates,
        "frozen_artifacts": frozen,
        "blockers": [],
    }
    validate_h5_h9_release_candidate(report)
    return report


def validate_h5_h9_release_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    report = _json_copy(candidate, "H5-H9 release candidate")
    _validate_schema(CANDIDATE_SCHEMA_PATH, report, "H5-H9 release candidate")
    if tuple(row["id"] for row in report["gates"]) != _GATE_ORDER:
        raise H5H9ReleaseClosureError("H5-H9 gates must remain in exact order")
    seen: set[str] = set()
    category_counts: dict[str, int] = {}
    for row in report["frozen_artifacts"]:
        key = os.path.normcase(str(Path(row["path"]).resolve()))
        if key in seen:
            raise H5H9ReleaseClosureError("H9 freeze inventory contains duplicate paths")
        seen.add(key)
        path = Path(row["path"])
        if (
            not path.is_absolute()
            or not path.is_file()
            or path.stat().st_size != row["size_bytes"]
            or _sha256(path) != row["sha256"]
        ):
            raise H5H9ReleaseClosureError(f"H9 frozen artifact drifted: {row['role']}")
        category_counts[row["category"]] = category_counts.get(row["category"], 0) + 1
    required_counts = {
        "skill": 5,
        "plan_schema": 3,
        "plan": 3,
        "capability_manifest": 4,
        "execution_runtime": 1,
        "source_input": 2,
        "drawing": 4,
        "verification_sidecar": 3,
        "semantic_response": 16,
        "semantic_call_claim": 16,
    }
    for category, minimum in required_counts.items():
        if category_counts.get(category, 0) < minimum:
            raise H5H9ReleaseClosureError(
                f"H9 freeze inventory lacks {category}: expected at least {minimum}"
            )
    return report


def build_and_publish_h5_h9_release_candidate(
    candidate: Mapping[str, Any],
    repository_root: Path,
    output_path: Path,
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    report = build_h5_h9_release_candidate(
        candidate, repository_root, generated_at_utc=generated_at_utc
    )
    path, sha256 = _publish_once(report, output_path, repository_root)
    return {
        "ok": True,
        "status": "complete",
        "release_candidate_path": path,
        "release_candidate_sha256": sha256,
        "git_commit": report["git_commit"],
        "gate_count": 5,
        "frozen_artifact_count": len(report["frozen_artifacts"]),
    }


def _gate_h5_traceability(
    request: Mapping[str, Any], h1: Mapping[str, Any], manifest: Mapping[str, Any]
) -> dict[str, Any]:
    state = inspect_h3_session(
        Path(request["h3_session_manifest"]["path"]),
        request["h3_session_manifest"]["sha256"],
    )
    if state["status"] != "ready_to_finalize" or state["captured_operation_count"] != 16:
        raise H5H9ReleaseClosureError("H5 requires one complete five-stage H3 session")
    planned_h1 = Path(manifest["planned_outputs"]["h1_candidate"]).resolve()
    actual_h1 = Path(request["h1_chain_evidence"]["path"]).resolve()
    if planned_h1 != actual_h1 or _sha256(planned_h1) != request["h1_chain_evidence"]["sha256"]:
        raise H5H9ReleaseClosureError("H5 H1 evidence is not the H3-finalized candidate")
    outputs = [row for stage in h1["stages"] for row in stage["outputs"]]
    plan_rows = [row for row in outputs if row["role"] in _PLAN_ROWS]
    drawing_rows = [row for row in outputs if row["role"] in _DRAWING_ROLES]
    if len(plan_rows) != 3 or len({row["path"] for row in plan_rows}) != 3:
        raise H5H9ReleaseClosureError("H5 requires exactly one plan per planning stage")
    if len(drawing_rows) != 4 or len({row["path"] for row in drawing_rows}) != 4:
        raise H5H9ReleaseClosureError("H5 requires four distinct successor drawings")
    return _passed(
        "H5",
        "five-Skill artifact traceability",
        "five ordered Skill stages and sixteen production operations are complete",
        "all cross-stage inputs/outputs retain exact path and SHA-256 continuity",
        "each planning stage emitted one plan and each drawing stage one new successor",
    )


def _gate_h6_plan_contracts(
    root: Path, h1: Mapping[str, Any], extras: list[dict[str, Any]]
) -> dict[str, Any]:
    outputs = _outputs_by_role(h1)
    for role, (relative_schema, protocol, version) in _PLAN_ROWS.items():
        schema_path = root / relative_schema
        schema = _load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        plan = _load_json(Path(outputs[role]["path"]))
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(plan),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            raise H5H9ReleaseClosureError(
                f"H6 {role} violates its repository Schema: {errors[0].message}"
            )
        if plan.get("protocol_id") != protocol or plan.get("schema_version") != version:
            raise H5H9ReleaseClosureError(f"H6 {role} protocol/version drifted")
        extras.append(_freeze_path(schema_path, "plan_schema", role))
        family = "layout" if role == "layout_plan" else role.removesuffix("_plan")
        _validate_csharp_contract_binding(
            root / _CSHARP_CONTRACT_ROWS[family][0],
            schema_path,
            protocol,
            version,
            family,
        )
    for family, relatives in _CSHARP_CONTRACT_ROWS.items():
        for index, relative in enumerate(relatives, 1):
            extras.append(
                _freeze_path(root / relative, "contract_source", f"{family}.{index:02d}")
            )
    h0 = _load_bound_json(h1["h0_readiness"], "H0 readiness")
    if h0.get("status") != "ready" or len(h0.get("capability_manifests", {})) != 4:
        raise H5H9ReleaseClosureError("H6 requires four H0-approved capability manifests")
    for row, role in zip(h0["plan_schemas"], _PLAN_ROWS, strict=True):
        _require_current_binding(
            row, root / _PLAN_ROWS[role][0], f"H0 plan Schema {role}"
        )
    capability_paths = {
        "view": root / "drawing_planner/capabilities/current.json",
        "dimension": root / "dimension_planner/capabilities/current.json",
        "layout_boundary": root / "drawing_layout_planner/capabilities/current.json",
        "layout_plan": root / "drawing_layout_planner/capabilities/plan-current.json",
    }
    for role, path in capability_paths.items():
        _require_current_binding(
            h0["capability_manifests"][role], path, f"H0 capability {role}"
        )
    return _passed(
        "H6",
        "three-plan publication and native contract chain",
        "all three published plans independently pass their Draft 2020-12 Schemas",
        "publish/validate/create/verify responses bind one canonical plan and request hash",
        "four live capability manifests and all three C# contract/transaction families are frozen",
    )


def _gate_h7_semantic_boundary(
    root: Path,
    request: Mapping[str, Any],
    h1: Mapping[str, Any],
    manifest: Mapping[str, Any],
    extras: list[dict[str, Any]],
) -> dict[str, Any]:
    contract_path = root / "adapters/claude/contracts/skill-chain.contract.json"
    semantic_schema_path = root / "adapters/claude/contracts/semantic-tools.schema.json"
    config_path = root / ".codex/config.toml"
    contract = _load_json(contract_path)
    semantic_schema = _load_json(semantic_schema_path)
    with config_path.open("rb") as handle:
        codex_config = tomllib.load(handle)
    tools = contract["default_mcp"]["tools"]
    if (
        len(tools) != 24
        or contract["default_mcp"]["prompt_count"] != 0
        or semantic_schema.get("required") != tools
        or list(semantic_schema.get("properties", {})) != tools
        or codex_config.get("mcp_servers", {}).get("solidpilot", {}).get(
            "enabled_tools"
        ) != tools
    ):
        raise H5H9ReleaseClosureError("H7 default semantic MCP surface drifted")
    actual_tools, actual_prompt_count = _discover_semantic_surface(root)
    if actual_tools != tools or actual_prompt_count != 0:
        raise H5H9ReleaseClosureError("H7 live FastMCP discovery drifted")
    if any(name.startswith("execute_") or name.startswith("verify_committed_") for name in tools):
        raise H5H9ReleaseClosureError("H7 private executor operation reached Agent surface")
    schedule = manifest["schedule"]
    expected_schedule = [
        {
            "sequence": index,
            "stage_order": stage,
            "skill": skill,
            "tool": tool,
            "mutating": mutating,
        }
        for index, (stage, skill, tool, mutating) in enumerate(PRODUCTION_SCHEDULE, 1)
    ]
    if schedule != expected_schedule or any(row["tool"] not in tools for row in schedule):
        raise H5H9ReleaseClosureError("H7 H3 schedule is not the production semantic schedule")
    if any(
        "qualify" in row["tool"] or "executor" in row["tool"] or row["tool"].startswith("bootstrap_")
        for row in schedule
    ):
        raise H5H9ReleaseClosureError("H7 schedule contains a forbidden operation")
    claim_directory = Path(manifest["planned_outputs"]["response_directory"]) / ".h4-claims"
    claims = sorted(claim_directory.glob("*.json")) if claim_directory.is_dir() else []
    if len(claims) != 16:
        raise H5H9ReleaseClosureError("H7 requires exactly sixteen H4 semantic call claims")
    session_sha = request["h3_session_manifest"]["sha256"]
    claim_schema = _load_json(H4_CLAIM_SCHEMA_PATH)
    broker_path = root / "release_candidate/h4_semantic_step.py"
    server_entry_path = root / "adapters/codex/server.py"
    expected_claim_bindings = {
        "broker_sha256": _sha256(broker_path),
        "server_entry_sha256": _sha256(server_entry_path),
        "semantic_contract_sha256": _sha256(contract_path),
        "execution_service_sha256": h1["execution_service"]["sha256"],
    }
    for row, path in zip(schedule, claims, strict=True):
        claim = _load_json(path)
        errors = list(Draft202012Validator(claim_schema).iter_errors(claim))
        if errors or (
            path.name != f"{row['sequence']:02d}-{row['tool']}.json"
            or claim["session_manifest_sha256"] != session_sha
            or claim["sequence"] != row["sequence"]
            or claim["tool"] != row["tool"]
            or hashlib.sha256(
                json.dumps(
                    claim["arguments"], ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest() != claim["arguments_sha256"]
            or any(claim.get(key) != value for key, value in expected_claim_bindings.items())
        ):
            raise H5H9ReleaseClosureError(f"H7 invalid H4 call claim: {path}")
        extras.append(_freeze_path(path, "semantic_call_claim", f"operation.{row['sequence']:02d}"))
    h0 = _load_bound_json(h1["h0_readiness"], "H0 readiness")
    if (
        h0["semantic_mcp"]["tools"] != tools
        or h0["semantic_mcp"]["prompt_count"] != 0
        or len(h0["skills"]) != 5
    ):
        raise H5H9ReleaseClosureError("H7 H0 discovery/Skill evidence drifted")
    semantic_bindings = {
        "contract": contract_path,
        "config": config_path,
        "schema": semantic_schema_path,
    }
    for role, path in semantic_bindings.items():
        _require_current_binding(
            h0["semantic_mcp"][role], path, f"H0 semantic {role}"
        )
    for stage, captured in zip(contract["stages"], h0["skills"], strict=True):
        skill_path = root / stage["path"]
        if captured["name"] != stage["skill"]:
            raise H5H9ReleaseClosureError("H7 H0 Skill order drifted")
        _require_current_binding(captured, skill_path, f"H0 Skill {stage['skill']}")
        text = skill_path.read_text(encoding="utf-8")
        match = re.search(
            r"^## Allowed semantic tools\n\n(?P<body>.*?)(?=^## |\Z)",
            text,
            flags=re.MULTILINE | re.DOTALL,
        )
        allowed = (
            re.findall(r"^- `([a-z0-9_]+)`$", match.group("body"), re.MULTILINE)
            if match else []
        )
        if allowed != stage["allowed_tools"] or any(name not in tools for name in allowed):
            raise H5H9ReleaseClosureError(f"H7 Skill allow-list drifted: {stage['skill']}")
    extras.extend(
        (
            _freeze_path(contract_path, "semantic_contract", "skill_chain"),
            _freeze_path(semantic_schema_path, "semantic_contract", "tool_schema"),
            _freeze_path(config_path, "semantic_contract", "codex_config"),
            _freeze_path(broker_path, "semantic_contract", "h4_broker"),
            _freeze_path(server_entry_path, "semantic_contract", "codex_stdio_entry"),
        )
    )
    return _passed(
        "H7",
        "engineering-semantic MCP confinement",
        "the discovered Agent surface is the frozen 24-tool/zero-prompt contract",
        "all sixteen operations have exclusive H4 stdio call claims in exact order",
        "qualification, repair, private executor, HTTP and direct COM paths are absent",
    )


def _gate_h8_transaction_integrity(root: Path, h1: Mapping[str, Any]) -> dict[str, Any]:
    outputs = _outputs_by_role(h1)
    responses = _responses_by_tool(h1)
    immutable = {
        row["role"]: row["sha256_after"] for row in h1["immutable_inputs"]
    }
    view_sidecar = _load_json(Path(outputs["view_verification_sidecar"]["path"]))
    view_drawing = outputs["view_drawing"]
    if (
        view_sidecar.get("schema_version") != "1.0"
        or view_sidecar.get("verified") is not True
        or Path(str(view_sidecar.get("output_path"))).resolve()
        != Path(view_drawing["path"]).resolve()
        or view_sidecar.get("artifact_sha256") != view_drawing["sha256"]
        or not isinstance(view_sidecar.get("verification"), Mapping)
    ):
        raise H5H9ReleaseClosureError("H8 ViewPlan transaction sidecar is incomplete")

    dimension_sidecar_path = Path(outputs["dimension_verification_sidecar"]["path"])
    dimension_sidecar = _validate_json_document(
        dimension_sidecar_path,
        root / "dimension_planner/contracts/dimension-drawing-verification.schema.json",
        "dimension verification sidecar",
    )
    dimension_drawing = outputs["dimensioned_drawing"]
    if (
        Path(dimension_sidecar["output_path"]).resolve()
        != Path(dimension_drawing["path"]).resolve()
        or dimension_sidecar["artifact_sha256"] != dimension_drawing["sha256"]
        or dimension_sidecar["in_memory_verification"]["verified"] is not True
        or dimension_sidecar["reopen_verification"]["verified"] is not True
    ):
        raise H5H9ReleaseClosureError("H8 dimension save/reopen evidence drifted")
    expected_dimension_frozen = {
        "dimension_plan": outputs["dimension_plan"]["sha256"],
        "handoff": outputs["dimension_handoff"]["sha256"],
        "source_model": immutable["source_model"],
        "source_drawing": outputs["view_drawing"]["sha256"],
        "view_plan": outputs["view_plan"]["sha256"],
        "verification_sidecar": outputs["view_verification_sidecar"]["sha256"],
    }
    if any(
        dimension_sidecar["frozen_inputs"].get(key) != value
        for key, value in expected_dimension_frozen.items()
    ):
        raise H5H9ReleaseClosureError("H8 dimension frozen-input ledger drifted")

    layout_sidecar_path = Path(outputs["final_verification_sidecar"]["path"])
    layout_sidecar = _validate_json_document(
        layout_sidecar_path,
        root / "drawing_layout_planner/contracts/drawing-layout-verification.schema.json",
        "layout verification sidecar",
    )
    final_drawing = outputs["final_drawing"]
    memory = layout_sidecar["in_memory_verification"]
    reopen = layout_sidecar["reopen_verification"]
    if (
        Path(layout_sidecar["output_path"]).resolve()
        != Path(final_drawing["path"]).resolve()
        or layout_sidecar["artifact_sha256"] != final_drawing["sha256"]
        or memory["verified"] is not True
        or reopen["verified"] is not True
        or memory["layout_fingerprint_sha256"] != reopen["layout_fingerprint_sha256"]
    ):
        raise H5H9ReleaseClosureError("H8 final save/reopen layout evidence drifted")
    expected_layout_frozen = {
        "drawing_layout_plan": outputs["layout_plan"]["sha256"],
        "handoff": outputs["layout_handoff"]["sha256"],
        "dimension_plan": outputs["dimension_plan"]["sha256"],
        "source_drawing": outputs["dimensioned_drawing"]["sha256"],
        "dimension_verification_sidecar": outputs[
            "dimension_verification_sidecar"
        ]["sha256"],
    }
    if any(
        layout_sidecar["frozen_inputs"].get(key) != value
        for key, value in expected_layout_frozen.items()
    ):
        raise H5H9ReleaseClosureError("H8 layout frozen-input ledger drifted")
    for tool in (
        "verify_part_drawing_view_plan",
        "verify_dimensioned_part_drawing",
        "verify_final_part_drawing",
    ):
        if not _contains_true(responses[tool], "independent_read_only_reopen"):
            raise H5H9ReleaseClosureError(
                f"H8 {tool} lacks independent read-only reopen proof"
            )
    return _passed(
        "H8",
        "final transaction and immutable-input integrity",
        "view, dimension and layout drawings carry committed save/reopen sidecars",
        "the final drawing passed independent read-only reopen with a stable layout fingerprint",
        "source model, template and every upstream frozen artifact still match their captured hashes",
    )


def _gate_h9_freeze(
    root: Path, h1: Mapping[str, Any], frozen: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    git = _git_state(root)
    if git["commit"] != h1["git_commit"] or not git["clean"]:
        raise H5H9ReleaseClosureError(
            "H9 requires the exact clean Git commit used by H0/H1"
        )
    if len(frozen) < 40:
        raise H5H9ReleaseClosureError("H9 final freeze inventory is incomplete")
    return _passed(
        "H9",
        "final immutable release freeze",
        "the exact clean commit, five Skills, three Schemas/plans and four capabilities are frozen",
        "execution runtime, all semantic responses and C# contract sources are hash-bound",
        "source inputs, handoffs, successor drawings and verification sidecars are hash-bound",
    )


def _build_frozen_inventory(
    root: Path,
    request: Mapping[str, Any],
    h1: Mapping[str, Any],
    manifest: Mapping[str, Any],
    extras: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        _freeze_binding(request["h3_session_manifest"], "release_evidence", "h3_session_manifest"),
        _freeze_binding(request["h1_chain_evidence"], "release_evidence", "h1_chain_evidence"),
        _freeze_binding(h1["h0_readiness"], "release_evidence", "h0_readiness"),
        _freeze_binding(h1["execution_service"], "execution_runtime", "execution_service"),
    ]
    for item in h1["immutable_inputs"]:
        rows.append(_freeze_path(Path(item["path"]), "source_input", item["role"]))
    for stage in h1["stages"]:
        for output in stage["outputs"]:
            role = output["role"]
            if role in _PLAN_ROWS:
                category = "plan"
            elif role in _HANDOFF_ROLES:
                category = "handoff"
            elif role in _DRAWING_ROLES:
                category = "drawing"
            elif role in _SIDECAR_ROLES:
                category = "verification_sidecar"
            else:
                category = "stage_artifact"
            rows.append(_freeze_binding(output, category, role))
        for operation in stage["operations"]:
            rows.append(
                _freeze_binding(
                    operation["response"],
                    "semantic_response",
                    f"operation.{operation['sequence']:02d}.{operation['tool']}",
                )
            )
    h0 = _load_bound_json(h1["h0_readiness"], "H0 readiness")
    for skill in h0["skills"]:
        rows.append(_freeze_binding(skill, "skill", skill["name"]))
    for role, binding in h0["capability_manifests"].items():
        rows.append(_freeze_binding(binding, "capability_manifest", role))
    rows.extend(dict(row) for row in extras)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = os.path.normcase(str(Path(row["path"]).resolve()))
        existing = unique.get(key)
        if existing is not None and existing["sha256"] != row["sha256"]:
            raise H5H9ReleaseClosureError("H9 duplicate artifact path has conflicting hashes")
        unique.setdefault(key, row)
    result = sorted(unique.values(), key=lambda row: (row["category"], row["role"], row["path"]))
    # Revalidate the session root binding after all files were read to catch concurrent drift.
    if Path(manifest["planned_outputs"]["h1_candidate"]).resolve() != Path(
        request["h1_chain_evidence"]["path"]
    ).resolve():
        raise H5H9ReleaseClosureError("H9 H3/H1 output binding drifted")
    return result


def _outputs_by_role(h1: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        row["role"]: row
        for stage in h1["stages"]
        for row in stage["outputs"]
    }


def _responses_by_tool(h1: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        operation["tool"]: _load_json(Path(operation["response"]["path"]))
        for stage in h1["stages"]
        for operation in stage["operations"]
    }


def _validate_json_document(path: Path, schema_path: Path, label: str) -> dict[str, Any]:
    value = _load_json(path)
    schema = _load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        raise H5H9ReleaseClosureError(f"H8 invalid {label}: {errors[0].message}")
    return value


def _contains_true(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return value.get(key) is True or any(_contains_true(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_true(item, key) for item in value)
    return False


def _validate_csharp_contract_binding(
    contract_path: Path,
    schema_path: Path,
    protocol: str,
    version: str,
    family: str,
) -> None:
    text = contract_path.read_text(encoding="utf-8")
    hash_match = re.search(
        r'ContractSha256\s*=\s*"([0-9a-f]{64})"', text, flags=re.MULTILINE
    )
    protocol_match = re.search(r'ProtocolId\s*=\s*"([^"]+)"', text)
    version_match = re.search(r'SchemaVersion\s*=\s*"([^"]+)"', text)
    if (
        hash_match is None
        or hash_match.group(1) != _sha256(schema_path)
        or protocol_match is None
        or protocol_match.group(1) != protocol
        or version_match is None
        or version_match.group(1) != version
    ):
        raise H5H9ReleaseClosureError(
            f"H6 {family} C# contract is not bound to its exact Schema"
        )


def _discover_semantic_surface(root: Path) -> tuple[list[str], int]:
    adapter = str(root / "adapters/claude")
    if adapter not in sys.path:
        sys.path.insert(0, adapter)
    from adapters.claude import server

    async def discover() -> tuple[list[str], int]:
        tools = await server.mcp.list_tools()
        prompts = await server.mcp.list_prompts()
        return [item.name for item in tools], len(prompts)

    return asyncio.run(discover())


def _require_current_binding(
    binding: Mapping[str, Any], expected_path: Path, label: str
) -> None:
    actual = expected_path.resolve(strict=True)
    if (
        Path(str(binding.get("path"))).resolve() != actual
        or binding.get("sha256") != _sha256(actual)
        or binding.get("size_bytes") != actual.stat().st_size
    ):
        raise H5H9ReleaseClosureError(f"{label} binding drifted")


def _passed(gate_id: str, name: str, *checks: str) -> dict[str, Any]:
    return {"id": gate_id, "name": name, "status": "passed", "checks": list(checks)}


def _freeze_binding(
    binding: Mapping[str, Any], category: str, role: str
) -> dict[str, Any]:
    path = Path(str(binding["path"]))
    if not path.is_absolute() or not path.is_file():
        raise H5H9ReleaseClosureError(f"frozen artifact is missing: {role}")
    resolved = path.resolve(strict=True)
    actual = _sha256(resolved)
    if binding.get("sha256") != actual:
        raise H5H9ReleaseClosureError(f"frozen artifact SHA-256 drifted: {role}")
    return _freeze_path(resolved, category, role)


def _freeze_path(path: Path, category: str, role: str) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "category": category,
        "role": role,
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _load_bound_json(binding: Mapping[str, Any], label: str) -> dict[str, Any]:
    path = Path(str(binding["path"]))
    if not path.is_absolute() or not path.is_file() or _sha256(path) != binding.get("sha256"):
        raise H5H9ReleaseClosureError(f"{label} binding is missing or changed")
    return _load_json(path)


def _validate_schema(path: Path, value: Any, label: str) -> None:
    schema = _load_json(path)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise H5H9ReleaseClosureError(
            f"invalid {label} at {location}: {errors[0].message}"
        )


def _json_copy(value: Any, label: str) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise H5H9ReleaseClosureError(f"{label} must contain strict JSON") from exc
    if not isinstance(copied, dict):
        raise H5H9ReleaseClosureError(f"{label} must be a JSON object")
    return copied


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise H5H9ReleaseClosureError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise H5H9ReleaseClosureError(f"JSON artifact must contain an object: {path}")
    return value


def _git_state(root: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.splitlines()
    return {"commit": commit, "clean": not status, "changed_paths": sorted(status)}


def _publish_once(
    value: Mapping[str, Any], output_path: Path, repository_root: Path
) -> tuple[str, str]:
    output = output_path.resolve()
    root = repository_root.resolve(strict=True)
    validation = (root / "validation").resolve()
    if (
        output.exists()
        or not output.parent.is_dir()
        or output.suffix.lower() != ".json"
        or output == root
        or root in output.parents
        or output == validation
        or validation in output.parents
    ):
        raise H5H9ReleaseClosureError(
            "H5-H9 output must be a new JSON file outside the repository/validation tree"
        )
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return str(output), _sha256(output)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
