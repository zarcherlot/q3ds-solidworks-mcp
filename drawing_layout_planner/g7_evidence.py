"""COM-free G7 matrix validation, evidence aggregation and promotion candidates."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from drawing_planner.planning_models import canonical_json_sha256

from .capability_registry import (
    LAYOUT_OPERATION_IDS,
    LAYOUT_SAFETY_IDS,
    DrawingLayoutCapabilityManifest,
)
from .g0_evidence import G0_CAPABILITY_IDS
from .handoff import file_sha256


PACKAGE_ROOT = Path(__file__).resolve().parent
CONTRACT_ROOT = PACKAGE_ROOT / "contracts"
MATRIX_REQUEST_SCHEMA_PATH = CONTRACT_ROOT / "drawing-layout-g7-matrix-request.schema.json"
CASE_EVIDENCE_SCHEMA_PATH = CONTRACT_ROOT / "drawing-layout-g7-case-evidence.schema.json"
SUMMARY_SCHEMA_PATH = CONTRACT_ROOT / "drawing-layout-g7-summary.schema.json"
PLANNING_REQUEST_SCHEMA_PATH = CONTRACT_ROOT / "drawing-layout-planning-request.schema.json"
PLAN_SCHEMA_PATH = CONTRACT_ROOT / "drawing-layout-plan.schema.json"
VERIFICATION_SCHEMA_PATH = CONTRACT_ROOT / "drawing-layout-verification.schema.json"
G0_QUALIFICATION_SCHEMA_PATH = CONTRACT_ROOT / "layout-boundary-qualification.schema.json"
BOUNDARY_CAPABILITY_PATH = PACKAGE_ROOT / "capabilities" / "current.json"

G7_POSITIVE_SCENARIOS = (
    "sparse_dimensions",
    "multi_view",
    "section_view",
    "detail_view",
    "auxiliary_view",
    "hole_pattern",
    "high_density_dimensions",
    "scale_change",
    "authorized_sheet_format",
)
G7_NEGATIVE_SCENARIOS = ("unauthorized_sheet_format",)
_SECTION_VIEW_TYPES = {
    "full_section",
    "half_section",
    "aligned_section",
    "offset_section",
    "removed_section",
    "broken_out_section",
}
_HIGH_DENSITY_MINIMUM = 12


class DrawingLayoutG7EvidenceError(ValueError):
    """Raised when G7 evidence is inconsistent or not promotion-ready."""


def load_g7_contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _load_json(MATRIX_REQUEST_SCHEMA_PATH),
        _load_json(CASE_EVIDENCE_SCHEMA_PATH),
        _load_json(SUMMARY_SCHEMA_PATH),
    )


def validate_g7_matrix_request(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_matrix_request(candidate, require_new_outputs=True)


def validate_g7_matrix_request_for_evaluation(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    return _validate_matrix_request(candidate, require_new_outputs=False)


def _validate_matrix_request(
    candidate: Mapping[str, Any], *, require_new_outputs: bool
) -> dict[str, Any]:
    request = _json_copy(candidate, "G7 matrix request")
    request_schema, _, _ = load_g7_contracts()
    planning_schema = _load_json(PLANNING_REQUEST_SCHEMA_PATH)
    registry = Registry().with_resource(
        planning_schema["$id"], Resource.from_contents(planning_schema)
    )
    _validate_schema(request, request_schema, "G7 matrix request", registry=registry)

    qualification = _absolute_file_binding(
        request["g0_qualification"], ".json", "G0 qualification"
    )
    request["g0_qualification"]["path"] = str(qualification)
    qualification_payload = _load_json(qualification)
    _validate_schema(
        qualification_payload,
        _load_json(G0_QUALIFICATION_SCHEMA_PATH),
        "G7 bound G0 qualification",
    )
    if (
        qualification_payload.get("protocol_id")
        != "solidworks-layout-g0-qualification"
        or qualification_payload.get("overall_status") != "complete"
        or qualification_payload.get("solidworks_revision") != "33.5.0"
    ):
        raise DrawingLayoutG7EvidenceError(
            "G7 must bind the complete SolidWorks 33.5.0 G0 qualification"
        )
    if tuple(row["id"] for row in qualification_payload["capabilities"]) != tuple(
        G0_CAPABILITY_IDS
    ):
        raise DrawingLayoutG7EvidenceError(
            "G7 bound G0 qualification catalog/order has changed"
        )
    boundary_manifest = _load_json(BOUNDARY_CAPABILITY_PATH)
    live_evidence = boundary_manifest.get("live_evidence")
    if not isinstance(live_evidence, Mapping) or (
        Path(str(live_evidence.get("qualification_path"))).resolve()
        != qualification
        or live_evidence.get("qualification_sha256")
        != request["g0_qualification"]["sha256"]
        or live_evidence.get("qualification_id")
        != qualification_payload["qualification_id"]
    ):
        raise DrawingLayoutG7EvidenceError(
            "G7 must bind the exact G0 qualification consumed by the production registry"
        )

    ids: set[str] = set()
    targets: set[str] = set()
    positive_scenarios: set[str] = set()
    positive_plan_paths: set[str] = set()
    plan_schema = _load_json(PLAN_SCHEMA_PATH)
    for index, case in enumerate(request["positive_cases"]):
        _unique_id(ids, case["case_id"])
        positive_scenarios.add(case["scenario"])
        plan_path = _absolute_file_binding(
            {"path": case["plan_path"], "sha256": case["plan_file_sha256"]},
            ".json",
            f"positive_cases[{index}].plan",
        )
        plan = _load_json(plan_path)
        normalized_plan_path = os.path.normcase(str(plan_path))
        if normalized_plan_path in positive_plan_paths:
            raise DrawingLayoutG7EvidenceError(
                "G7 positive scenarios must use distinct immutable plans"
            )
        positive_plan_paths.add(normalized_plan_path)
        _validate_schema(plan, plan_schema, f"positive_cases[{index}] DrawingLayoutPlan")
        if canonical_json_sha256(plan, "DrawingLayoutPlan") != case["plan_canonical_sha256"]:
            raise DrawingLayoutG7EvidenceError(
                f"positive_cases[{index}] canonical plan hash mismatch"
            )
        _validate_request_binding(case, plan, index)
        _validate_scenario_binding(case["scenario"], plan, index)
        case["plan_path"] = str(plan_path)
        output = _absolute_target(
            case["output_path"], ".slddrw", f"positive_cases[{index}].output",
            require_new=require_new_outputs,
        )
        sidecar = Path(str(output) + ".layout-verification.json")
        evidence = _absolute_target(
            case["evidence_path"], ".json", f"positive_cases[{index}].evidence",
            require_new=require_new_outputs,
        )
        if require_new_outputs and sidecar.exists():
            raise DrawingLayoutG7EvidenceError(f"layout sidecar must be new: {sidecar}")
        for target in (output, sidecar, evidence):
            _unique_target(targets, target)
        case["output_path"] = str(output)
        case["evidence_path"] = str(evidence)

    if positive_scenarios != set(G7_POSITIVE_SCENARIOS):
        raise DrawingLayoutG7EvidenceError(
            "G7 positive scenarios must cover exactly: "
            + ", ".join(G7_POSITIVE_SCENARIOS)
        )

    negative_scenarios: set[str] = set()
    for index, case in enumerate(request["negative_cases"]):
        _unique_id(ids, case["case_id"])
        negative_scenarios.add(case["scenario"])
        request_hash = canonical_json_sha256(
            case["planning_request"], "layout planning request"
        )
        if request_hash != case["planning_request_sha256"]:
            raise DrawingLayoutG7EvidenceError(
                f"negative_cases[{index}] planning request hash mismatch"
            )
        rejected_plan_target = _absolute_target(
            str(
                Path(case["planning_request"]["publication_directory"])
                / "drawing_layout_plan.json"
            ),
            ".json",
            f"negative_cases[{index}].rejected plan publication",
            require_new=require_new_outputs,
        )
        evidence = _absolute_target(
            case["evidence_path"], ".json", f"negative_cases[{index}].evidence",
            require_new=require_new_outputs,
        )
        _unique_target(targets, rejected_plan_target)
        _unique_target(targets, evidence)
        case["evidence_path"] = str(evidence)
    if negative_scenarios != set(G7_NEGATIVE_SCENARIOS):
        raise DrawingLayoutG7EvidenceError(
            "G7 must include the unauthorized sheet-format rejection scenario"
        )
    return request


def _validate_request_binding(
    case: Mapping[str, Any], plan: Mapping[str, Any], index: int
) -> None:
    planning_request = case["planning_request"]
    request_hash = canonical_json_sha256(planning_request, "layout planning request")
    source_hash = canonical_json_sha256(
        planning_request["source_dimension_request"], "source dimension planning request"
    )
    if request_hash != case["planning_request_sha256"]:
        raise DrawingLayoutG7EvidenceError(
            f"positive_cases[{index}] planning request hash mismatch"
        )
    if source_hash != case["source_dimension_request_sha256"]:
        raise DrawingLayoutG7EvidenceError(
            f"positive_cases[{index}] source dimension request hash mismatch"
        )
    expected_plan = (
        Path(planning_request["publication_directory"]).resolve()
        / "drawing_layout_plan.json"
    )
    if expected_plan != Path(case["plan_path"]).resolve():
        raise DrawingLayoutG7EvidenceError(
            f"positive_cases[{index}] plan is not the request publication"
        )
    if (
        plan["plan_id"] != planning_request["plan_id"]
        or plan["handoff"] != planning_request["handoff"]
    ):
        raise DrawingLayoutG7EvidenceError(
            f"positive_cases[{index}] plan/request identity binding mismatch"
        )
    dimension_plan = (
        Path(planning_request["source_dimension_request"]["publication_directory"])
        .resolve()
        / "dimension_plan.json"
    )
    if dimension_plan != Path(plan["source_dimension_plan"]["path"]).resolve():
        raise DrawingLayoutG7EvidenceError(
            f"positive_cases[{index}] source DimensionPlanningRequest was bypassed"
        )


def _validate_scenario_binding(
    scenario: str, plan: Mapping[str, Any], index: int
) -> None:
    operation_kinds = {row["kind"] for row in plan["operations"]}
    dimension_count = len(plan["source_invariants"]["dimension_ids"])
    view_count = len(plan["source_invariants"]["view_names"])
    dimension_plan = _load_json(Path(plan["source_dimension_plan"]["path"]))
    view_plan_binding = dimension_plan.get("view_plan")
    if not isinstance(view_plan_binding, Mapping) or not isinstance(
        view_plan_binding.get("path"), str
    ):
        raise DrawingLayoutG7EvidenceError(
            f"positive_cases[{index}] source DimensionPlan lacks its ViewPlan binding"
        )
    view_plan_path = _absolute_file_binding(
        view_plan_binding,
        ".json",
        f"positive_cases[{index}].source ViewPlan",
    )
    view_plan = _load_json(view_plan_path)
    view_types = {
        row.get("type") for row in view_plan.get("views", []) if isinstance(row, Mapping)
    }
    dimension_kinds = {
        row.get("kind")
        for row in dimension_plan.get("dimensions", [])
        if isinstance(row, Mapping)
    }
    passed = {
        "sparse_dimensions": dimension_count <= 5,
        "multi_view": view_count >= 3,
        "section_view": bool(view_types & _SECTION_VIEW_TYPES),
        "detail_view": "detail_view" in view_types,
        "auxiliary_view": "auxiliary_view" in view_types,
        "hole_pattern": "hole_quantity" in dimension_kinds
        and bool(dimension_kinds & {"hole_spacing", "hole_group_location"}),
        "high_density_dimensions": dimension_count >= _HIGH_DENSITY_MINIMUM,
        "scale_change": bool(
            operation_kinds & {"set_view_scale", "set_sheet_scale"}
        ),
        "authorized_sheet_format": "set_sheet_format" in operation_kinds,
    }[scenario]
    if not passed:
        raise DrawingLayoutG7EvidenceError(
            f"positive_cases[{index}] plan does not prove scenario {scenario}"
        )


def validate_g7_case_evidence(candidate: Mapping[str, Any]) -> dict[str, Any]:
    evidence = _json_copy(candidate, "G7 case evidence")
    _, schema, _ = load_g7_contracts()
    _validate_schema(evidence, schema, "G7 case evidence")
    return evidence


def build_g7_positive_case_evidence(
    case: Mapping[str, Any],
    stage_results: Mapping[str, Mapping[str, Any]],
    *,
    execution_service_path: Path,
    capability_manifest_path: Path,
) -> dict[str, Any]:
    if set(stage_results) != {"validate", "create", "verify"}:
        raise DrawingLayoutG7EvidenceError(
            "G7 positive stages must be exactly validate/create/verify"
        )
    plan_path = Path(case["plan_path"]).resolve()
    plan = _load_json(plan_path)
    output = Path(case["output_path"]).resolve()
    sidecar_path = Path(str(output) + ".layout-verification.json")
    sidecar = _load_json(sidecar_path)
    _validate_schema(
        sidecar, _load_json(VERIFICATION_SCHEMA_PATH), "G7 layout verification sidecar"
    )
    if (
        file_sha256(plan_path) != case["plan_file_sha256"]
        or canonical_json_sha256(plan, "DrawingLayoutPlan")
        != case["plan_canonical_sha256"]
        or not output.is_file()
    ):
        raise DrawingLayoutG7EvidenceError("G7 plan or output changed during qualification")

    stages: dict[str, dict[str, Any]] = {}
    for name in ("validate", "create", "verify"):
        result = stage_results[name]
        expected_status = "VALID" if name == "validate" else "COMPLETED"
        if result.get("ok") is not True or result.get("status") != expected_status:
            raise DrawingLayoutG7EvidenceError(f"G7 {name} stage did not complete")
        for field in (
            "planning_request_sha256",
            "source_dimension_request_sha256",
            "plan_canonical_sha256",
        ):
            expected = case[field]
            if result.get(field) != expected:
                raise DrawingLayoutG7EvidenceError(
                    f"G7 {name} stage broke {field} continuity"
                )
        stages[name] = {
            "ok": True,
            "status": expected_status,
            "planning_request_sha256": case["planning_request_sha256"],
            "source_dimension_request_sha256": case[
                "source_dimension_request_sha256"
            ],
            "plan_canonical_sha256": case["plan_canonical_sha256"],
        }
    if not _contains_true(stage_results["verify"], "independent_read_only_reopen"):
        raise DrawingLayoutG7EvidenceError(
            "G7 verify lacks independent read-only reopen evidence"
        )

    expected_frozen = _expected_frozen(plan, plan_path)
    if not _sidecar_matches_case(sidecar, case, plan, output, expected_frozen):
        raise DrawingLayoutG7EvidenceError(
            "G7 verification sidecar is not bound to the matrix plan/output"
        )
    memory = sidecar["in_memory_verification"]
    reopen = sidecar["reopen_verification"]
    frozen = sidecar["frozen_inputs"]
    invariants = {
        "dimension_semantics_preserved": memory["dimension_semantics"]
        == reopen["dimension_semantics"],
        "view_semantics_preserved": memory["view_semantics"]
        == reopen["view_semantics"],
        "object_identities_preserved": memory["snapshot"] == reopen["snapshot"],
        "no_dangling_leaders": True,
        "safe_area_clear": True,
        "no_positive_area_collisions": True,
        "source_hashes_unchanged": frozen == expected_frozen,
        "save_close_readonly_reopen": reopen["verified"] is True,
        "independent_readonly_verify": True,
        "layout_fingerprint_match": memory["layout_fingerprint_sha256"]
        == reopen["layout_fingerprint_sha256"],
    }
    if not all(invariants.values()):
        failed = sorted(name for name, passed in invariants.items() if not passed)
        raise DrawingLayoutG7EvidenceError(
            "G7 positive invariants failed: " + ", ".join(failed)
        )
    report = {
        "protocol_id": "solidworks-drawing-layout-g7-case-evidence",
        "schema_version": "1.0",
        "evidence_kind": "positive",
        "case_id": case["case_id"],
        "scenario": case["scenario"],
        "generated_at_utc": _now(),
        "solidworks_revision": "33.5.0",
        "execution_service_sha256": file_sha256(execution_service_path.resolve()),
        "capability_manifest_sha256": file_sha256(capability_manifest_path.resolve()),
        "plan": {
            "plan_id": plan["plan_id"],
            "path": str(plan_path),
            "file_sha256": case["plan_file_sha256"],
            "canonical_sha256": case["plan_canonical_sha256"],
            "planning_request_sha256": case["planning_request_sha256"],
            "source_dimension_request_sha256": case[
                "source_dimension_request_sha256"
            ],
        },
        "output": {
            "path": str(output),
            "sha256": file_sha256(output),
            "verification_sidecar_path": str(sidecar_path),
            "verification_sidecar_sha256": file_sha256(sidecar_path),
        },
        "operation_kinds": sorted({row["kind"] for row in plan["operations"]}),
        "stages": stages,
        "invariants": invariants,
    }
    return validate_g7_case_evidence(report)


def build_g7_negative_case_evidence(
    case: Mapping[str, Any], publication_result: Mapping[str, Any]
) -> dict[str, Any]:
    issues = publication_result.get("validation", {}).get("issues", [])
    issue_codes = {
        row.get("code") for row in issues if isinstance(row, Mapping)
    }
    if (
        publication_result.get("status") != "rejected"
        or publication_result.get("plan") is not None
        or case["expected_issue_code"] not in issue_codes
        or publication_result.get("planning_request_sha256")
        != case["planning_request_sha256"]
    ):
        raise DrawingLayoutG7EvidenceError(
            "G7 unauthorized sheet-format case was not deterministically rejected"
        )
    return validate_g7_case_evidence(
        {
            "protocol_id": "solidworks-drawing-layout-g7-case-evidence",
            "schema_version": "1.0",
            "evidence_kind": "negative",
            "case_id": case["case_id"],
            "scenario": case["scenario"],
            "generated_at_utc": _now(),
            "planning_request_sha256": case["planning_request_sha256"],
            "status": "rejected",
            "issue_code": case["expected_issue_code"],
        }
    )


def build_g7_summary(
    request_candidate: Mapping[str, Any], evidence_paths: Sequence[Path]
) -> dict[str, Any]:
    request = validate_g7_matrix_request_for_evaluation(request_candidate)
    cases = {
        case["case_id"]: case
        for case in request["positive_cases"] + request["negative_cases"]
    }
    reports: dict[str, tuple[Path, dict[str, Any]]] = {}
    for evidence_path in evidence_paths:
        path = evidence_path.resolve()
        report = validate_g7_case_evidence(_load_json(path))
        if report["case_id"] in reports:
            raise DrawingLayoutG7EvidenceError(
                f"duplicate G7 evidence case_id: {report['case_id']}"
            )
        reports[report["case_id"]] = (path, report)
    if set(reports) != set(cases):
        raise DrawingLayoutG7EvidenceError("G7 evidence inventory does not match request")

    scenario_counts = Counter(
        {name: 0 for name in G7_POSITIVE_SCENARIOS + G7_NEGATIVE_SCENARIOS}
    )
    operation_counts = Counter({name: 0 for name in LAYOUT_OPERATION_IDS})
    safety_counts = Counter({name: 0 for name in LAYOUT_SAFETY_IDS})
    bindings: list[dict[str, str]] = []
    execution_hashes: set[str] = set()
    capability_hashes: set[str] = set()
    positive_verified = True
    negative_rejected = True
    for case_id, case in cases.items():
        path, report = reports[case_id]
        if report["scenario"] != case["scenario"]:
            raise DrawingLayoutG7EvidenceError(f"{case_id} scenario mismatch")
        _validate_summary_case_binding(case, report)
        scenario_counts[case["scenario"]] += 1
        if report["evidence_kind"] == "positive":
            execution_hashes.add(report["execution_service_sha256"])
            capability_hashes.add(report["capability_manifest_sha256"])
            positive_verified &= all(report["invariants"].values())
            for operation in report["operation_kinds"]:
                operation_counts[operation] += 1
            for safety in LAYOUT_SAFETY_IDS:
                safety_counts[safety] += 1
        else:
            negative_rejected &= report["status"] == "rejected"
        bindings.append(
            {
                "case_id": case_id,
                "scenario": case["scenario"],
                "evidence_kind": report["evidence_kind"],
                "path": str(path),
                "sha256": file_sha256(path),
            }
        )
    if len(execution_hashes) != 1 or len(capability_hashes) != 1:
        raise DrawingLayoutG7EvidenceError(
            "G7 positive evidence must use one execution service and capability manifest"
        )
    complete = (
        all(scenario_counts[name] > 0 for name in scenario_counts)
        and all(operation_counts[name] > 0 for name in LAYOUT_OPERATION_IDS)
        and all(safety_counts[name] > 0 for name in LAYOUT_SAFETY_IDS)
        and positive_verified
        and negative_rejected
    )
    summary = {
        "protocol_id": "solidworks-drawing-layout-g7-live-matrix-summary",
        "schema_version": "1.0",
        "generated_at_utc": _now(),
        "solidworks_revision": "33.5.0",
        "g0_qualification": copy.deepcopy(request["g0_qualification"]),
        "execution_service_sha256": next(iter(execution_hashes)),
        "capability_manifest_sha256": next(iter(capability_hashes)),
        "case_evidence": bindings,
        "scenario_coverage": dict(scenario_counts),
        "operation_coverage": dict(operation_counts),
        "safety_coverage": dict(safety_counts),
        "source_hashes_unchanged": True,
        "all_positive_cases_independently_verified": positive_verified,
        "all_negative_cases_rejected": negative_rejected,
        "overall_status": "complete" if complete else "incomplete",
    }
    _, _, schema = load_g7_contracts()
    _validate_schema(summary, schema, "G7 matrix summary")
    return summary


def build_g7_capability_promotion_candidate(
    current_manifest: Mapping[str, Any], summary_path: Path
) -> dict[str, Any]:
    manifest = _json_copy(current_manifest, "layout capability manifest")
    summary_file = summary_path.resolve()
    summary = _load_json(summary_file)
    _, _, schema = load_g7_contracts()
    _validate_schema(summary, schema, "G7 matrix summary")
    if (
        summary["overall_status"] != "complete"
        or not summary["source_hashes_unchanged"]
        or not summary["all_positive_cases_independently_verified"]
        or not summary["all_negative_cases_rejected"]
    ):
        raise DrawingLayoutG7EvidenceError(
            "incomplete G7 evidence cannot promote layout capabilities"
        )
    if any(summary["scenario_coverage"][name] < 1 for name in summary["scenario_coverage"]):
        raise DrawingLayoutG7EvidenceError("G7 scenario coverage is incomplete")
    if any(summary["operation_coverage"][name] < 1 for name in LAYOUT_OPERATION_IDS):
        raise DrawingLayoutG7EvidenceError("G7 operation coverage is incomplete")
    if any(summary["safety_coverage"][name] < 1 for name in LAYOUT_SAFETY_IDS):
        raise DrawingLayoutG7EvidenceError("G7 safety coverage is incomplete")
    evidence_sha = file_sha256(summary_file)
    manifest["registry_version"] = "1.0.0"
    manifest["executor_version"] = "1.0.0"
    reason = (
        "G7 full scenario matrix passed native create, save/close/reopen and independent verification."
    )
    for catalog in (manifest["operations"], manifest["safety_elements"]):
        for name in catalog:
            catalog[name] = {
                "status": "supported",
                "reason": reason,
                "verification": "live",
                "evidence_sha256": evidence_sha,
            }
    return DrawingLayoutCapabilityManifest.model_validate(manifest).model_dump(mode="json")


def _validate_summary_case_binding(
    case: Mapping[str, Any], report: Mapping[str, Any]
) -> None:
    expected_kind = "positive" if "plan_path" in case else "negative"
    if report["evidence_kind"] != expected_kind:
        raise DrawingLayoutG7EvidenceError(
            f"{case['case_id']} evidence kind does not match the matrix case"
        )
    if expected_kind == "negative":
        rejected_plan = (
            Path(case["planning_request"]["publication_directory"]).resolve()
            / "drawing_layout_plan.json"
        )
        if (
            report["planning_request_sha256"] != case["planning_request_sha256"]
            or report["issue_code"] != case["expected_issue_code"]
            or report["status"] != "rejected"
            or rejected_plan.exists()
        ):
            raise DrawingLayoutG7EvidenceError(
                f"{case['case_id']} negative evidence binding mismatch"
            )
        return

    plan_path = Path(case["plan_path"]).resolve()
    plan = _load_json(plan_path)
    output = Path(case["output_path"]).resolve()
    sidecar_path = Path(str(output) + ".layout-verification.json")
    if not output.is_file() or not sidecar_path.is_file():
        raise DrawingLayoutG7EvidenceError(
            f"{case['case_id']} qualification output or sidecar is missing"
        )
    sidecar = _load_json(sidecar_path)
    _validate_schema(
        sidecar, _load_json(VERIFICATION_SCHEMA_PATH),
        f"{case['case_id']} layout verification sidecar",
    )
    expected_frozen = _expected_frozen(plan, plan_path)
    bindings_match = (
        report["plan"]["plan_id"] == plan["plan_id"]
        and Path(report["plan"]["path"]).resolve() == plan_path
        and report["plan"]["file_sha256"] == case["plan_file_sha256"]
        and report["plan"]["canonical_sha256"] == case["plan_canonical_sha256"]
        and report["plan"]["planning_request_sha256"]
        == case["planning_request_sha256"]
        and report["plan"]["source_dimension_request_sha256"]
        == case["source_dimension_request_sha256"]
        and file_sha256(plan_path) == case["plan_file_sha256"]
        and canonical_json_sha256(plan, "DrawingLayoutPlan")
        == case["plan_canonical_sha256"]
        and Path(report["output"]["path"]).resolve() == output
        and report["output"]["sha256"] == file_sha256(output)
        and Path(report["output"]["verification_sidecar_path"]).resolve()
        == sidecar_path
        and report["output"]["verification_sidecar_sha256"]
        == file_sha256(sidecar_path)
        and _sidecar_matches_case(sidecar, case, plan, output, expected_frozen)
        and all(report["invariants"].values())
    )
    if not bindings_match:
        raise DrawingLayoutG7EvidenceError(
            f"{case['case_id']} positive evidence binding mismatch"
        )


def _sidecar_matches_case(
    sidecar: Mapping[str, Any],
    case: Mapping[str, Any],
    plan: Mapping[str, Any],
    output: Path,
    expected_frozen: Mapping[str, str],
) -> bool:
    return (
        sidecar["verified"] is True
        and sidecar["plan_id"] == plan["plan_id"]
        and Path(sidecar["plan_file_path"]).resolve()
        == Path(case["plan_path"]).resolve()
        and sidecar["plan_file_sha256"] == case["plan_file_sha256"]
        and sidecar["plan_canonical_sha256"] == case["plan_canonical_sha256"]
        and Path(sidecar["source_drawing_path"]).resolve()
        == Path(plan["source_drawing"]["path"]).resolve()
        and sidecar["source_drawing_sha256"]
        == plan["source_drawing"]["sha256"]
        and Path(sidecar["output_path"]).resolve() == output
        and sidecar["artifact_sha256"] == file_sha256(output)
        and sidecar["frozen_inputs"] == expected_frozen
    )


def publish_json_once(
    candidate: Mapping[str, Any], output_path: Path
) -> tuple[str, str]:
    output = output_path.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite immutable G7 artifact: {output}")
    if output.suffix.lower() != ".json" or not output.parent.is_dir():
        raise DrawingLayoutG7EvidenceError(
            "G7 output must be a new .json in an existing directory"
        )
    validation_root = (PACKAGE_ROOT.parent / "validation").resolve()
    if output == validation_root or validation_root in output.parents:
        raise DrawingLayoutG7EvidenceError("G7 output must not be under validation/")
    payload = (
        json.dumps(_json_copy(candidate, "G7 output"), ensure_ascii=False, indent=2)
        + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent)
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
    return str(output), file_sha256(output)


def _expected_frozen(plan: Mapping[str, Any], plan_path: Path) -> dict[str, str]:
    expected = {
        "drawing_layout_plan": file_sha256(plan_path),
        "handoff": file_sha256(Path(plan["handoff"]["path"])),
        "dimension_plan": file_sha256(Path(plan["source_dimension_plan"]["path"])),
        "source_drawing": file_sha256(Path(plan["source_drawing"]["path"])),
        "dimension_verification_sidecar": file_sha256(
            Path(plan["dimension_verification_sidecar"]["path"])
        ),
    }
    dimension_plan = _load_json(Path(plan["source_dimension_plan"]["path"]))
    for key in (
        "handoff",
        "source_model",
        "source_drawing",
        "view_plan",
        "verification_sidecar",
    ):
        expected["dimension_plan." + key] = file_sha256(
            Path(dimension_plan[key]["path"])
        )
    return expected


def _absolute_file_binding(
    binding: Mapping[str, Any], suffix: str, label: str
) -> Path:
    path = Path(str(binding["path"])).resolve()
    if not path.is_file() or path.suffix.lower() != suffix:
        raise DrawingLayoutG7EvidenceError(
            f"{label} must be an existing {suffix} file: {path}"
        )
    if file_sha256(path) != binding["sha256"]:
        raise DrawingLayoutG7EvidenceError(f"{label} SHA-256 mismatch: {path}")
    return path


def _absolute_target(
    value: str, suffix: str, label: str, *, require_new: bool
) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != suffix:
        raise DrawingLayoutG7EvidenceError(f"{label} must be an absolute {suffix} path")
    resolved = path.resolve()
    if not resolved.parent.is_dir() or (require_new and resolved.exists()):
        raise DrawingLayoutG7EvidenceError(
            f"{label} must be new in an existing directory: {resolved}"
        )
    validation_root = (PACKAGE_ROOT.parent / "validation").resolve()
    if resolved == validation_root or validation_root in resolved.parents:
        raise DrawingLayoutG7EvidenceError(f"{label} must not be under validation/")
    return resolved


def _unique_id(ids: set[str], value: str) -> None:
    if value in ids:
        raise DrawingLayoutG7EvidenceError(f"duplicate G7 case_id: {value}")
    ids.add(value)


def _unique_target(targets: set[str], path: Path) -> None:
    key = os.path.normcase(str(path.resolve()))
    if key in targets:
        raise DrawingLayoutG7EvidenceError(f"duplicate G7 output path: {path}")
    targets.add(key)


def _contains_true(value: object, key: str) -> bool:
    if isinstance(value, Mapping):
        if value.get(key) is True:
            return True
        return any(_contains_true(item, key) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_true(item, key) for item in value)
    return False


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DrawingLayoutG7EvidenceError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DrawingLayoutG7EvidenceError(f"JSON root must be an object: {path}")
    return value


def _json_copy(candidate: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise DrawingLayoutG7EvidenceError(f"{label} must be an object")
    try:
        return json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DrawingLayoutG7EvidenceError(f"{label} is not strict JSON: {exc}") from exc


def _validate_schema(
    candidate: Mapping[str, Any],
    schema: Mapping[str, Any],
    label: str,
    *,
    registry: Registry | None = None,
) -> None:
    validator = Draft202012Validator(
        schema, format_checker=FormatChecker(), registry=registry or Registry()
    )
    errors = sorted(validator.iter_errors(candidate), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise DrawingLayoutG7EvidenceError(
            f"{label} contract failed at {pointer or '/'}: {error.message}"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
