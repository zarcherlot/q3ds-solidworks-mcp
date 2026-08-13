"""Deterministic F7 live-matrix validation and capability-promotion candidate generation.

This module is COM-free. It accepts only immutable matrix requests and evidence files emitted by
the repository C# evidence transaction, re-hashes every referenced artifact, and refuses capability
promotion until all six part categories, all 18 DimensionPlan kinds, the six production execution
elements, save/reopen readback, and independent verification are covered.
"""

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

from .capability_registry import DimensionCapabilityManifest
from .handoff import file_sha256


PACKAGE_ROOT = Path(__file__).resolve().parent
CONTRACT_ROOT = PACKAGE_ROOT / "contracts"
MATRIX_REQUEST_SCHEMA_PATH = CONTRACT_ROOT / "dimension-f7-matrix-request.schema.json"
CASE_EVIDENCE_SCHEMA_PATH = CONTRACT_ROOT / "dimension-f7-case-evidence.schema.json"
SUMMARY_SCHEMA_PATH = CONTRACT_ROOT / "dimension-f7-summary.schema.json"
PLANNING_REQUEST_SCHEMA_PATH = CONTRACT_ROOT / "dimension-planning-request.schema.json"
DIMENSION_PLAN_SCHEMA_PATH = CONTRACT_ROOT / "dimension-plan.schema.json"
DIMENSION_VERIFICATION_SCHEMA_PATH = (
    CONTRACT_ROOT / "dimension-drawing-verification.schema.json"
)

F7_CATEGORIES = (
    "plate",
    "shaft_sleeve",
    "bracket",
    "flange",
    "slot_cavity",
    "threaded",
)
F7_DIMENSION_KINDS = (
    "linear",
    "aligned",
    "diameter",
    "radius",
    "angular",
    "reference",
    "hole_diameter",
    "hole_depth",
    "hole_quantity",
    "hole_spacing",
    "hole_group_location",
    "overall",
    "step",
    "boss",
    "slot",
    "chamfer",
    "fillet",
    "symmetric",
)
F7_EXECUTION_ELEMENTS = (
    "model_dimension_import",
    "attachment_persistent_reference",
    "annotation_position",
    "dimension_tolerance",
    "dimension_prefix_suffix",
    "save_reopen_stable_identity",
)


class DimensionF7EvidenceError(ValueError):
    """Raised when F7 evidence is missing, inconsistent, or not promotion-ready."""


def load_f7_contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    return (
        _load_json(MATRIX_REQUEST_SCHEMA_PATH),
        _load_json(CASE_EVIDENCE_SCHEMA_PATH),
        _load_json(SUMMARY_SCHEMA_PATH),
    )


def validate_f7_matrix_request(candidate: Mapping[str, Any]) -> dict[str, Any]:
    request = _json_copy(candidate, "F7 matrix request")
    request_schema, _, _ = load_f7_contracts()
    planning_schema = _load_json(PLANNING_REQUEST_SCHEMA_PATH)
    plan_schema = _load_json(DIMENSION_PLAN_SCHEMA_PATH)
    registry = Registry().with_resource(
        planning_schema["$id"], Resource.from_contents(planning_schema)
    )
    _validate_schema(request, request_schema, "F7 matrix request", registry=registry)

    f0 = _absolute_file_binding(request["f0_evidence"], ".json", "F0 evidence")
    request["f0_evidence"]["path"] = str(f0)
    _validate_f0_summary(_load_json(f0))
    case_ids: set[str] = set()
    categories: set[str] = set()
    frozen_outputs: set[str] = set()
    for index, case in enumerate(request["cases"]):
        case_id = case["case_id"]
        if case_id in case_ids:
            raise DimensionF7EvidenceError(f"duplicate F7 case_id: {case_id}")
        case_ids.add(case_id)
        categories.add(case["category"])

        plan_path = _absolute_file_binding(
            {"path": case["plan_path"], "sha256": case["plan_file_sha256"]},
            ".json",
            f"cases[{index}].plan",
        )
        plan = _load_json(plan_path)
        _validate_schema(plan, plan_schema, f"cases[{index}] DimensionPlan")
        if canonical_json_sha256(plan, "DimensionPlan") != case["plan_canonical_sha256"]:
            raise DimensionF7EvidenceError(
                f"cases[{index}] plan_canonical_sha256 does not match plan_path"
            )
        request_hash = canonical_json_sha256(
            case["planning_request"], "dimension planning request"
        )
        if request_hash != case["planning_request_sha256"]:
            raise DimensionF7EvidenceError(
                f"cases[{index}] planning_request_sha256 does not match planning_request"
            )
        expected_plan = (
            Path(case["planning_request"]["publication_directory"]).resolve()
            / "dimension_plan.json"
        )
        if expected_plan != plan_path:
            raise DimensionF7EvidenceError(
                f"cases[{index}] plan_path is not the request publication"
            )
        case["plan_path"] = str(plan_path)

        output = _new_absolute_path(case["output_path"], ".slddrw", f"cases[{index}].output")
        evidence = _new_absolute_path(case["evidence_path"], ".json", f"cases[{index}].evidence")
        sidecar = Path(str(output) + ".dimension-verification.json")
        if sidecar.exists():
            raise DimensionF7EvidenceError(
                f"cases[{index}] transaction sidecar must be new: {sidecar}"
            )
        for target in (output, sidecar, evidence):
            key = os.path.normcase(str(target.resolve()))
            if key in frozen_outputs:
                raise DimensionF7EvidenceError(f"duplicate F7 output path: {target}")
            frozen_outputs.add(key)
        if evidence == sidecar:
            raise DimensionF7EvidenceError("F7 evidence_path must differ from transaction sidecar")
        case["output_path"] = str(output)
        case["evidence_path"] = str(evidence)

    if categories != set(F7_CATEGORIES):
        missing = sorted(set(F7_CATEGORIES) - categories)
        raise DimensionF7EvidenceError(
            "F7 matrix must cover every required part category; missing: " + ", ".join(missing)
        )
    return request


def validate_f7_case_evidence(candidate: Mapping[str, Any]) -> dict[str, Any]:
    evidence = _json_copy(candidate, "F7 case evidence")
    _, evidence_schema, _ = load_f7_contracts()
    _validate_schema(evidence, evidence_schema, "F7 case evidence")
    return evidence


def build_f7_case_evidence_from_semantic_results(
    case: Mapping[str, Any],
    stage_results: Mapping[str, Mapping[str, Any]],
    *,
    execution_service_path: Path,
    capability_manifest_path: Path,
) -> dict[str, Any]:
    """Build one immutable case report after the public validate/create/verify chain passes."""

    if set(stage_results) != {"validate", "create", "verify"}:
        raise DimensionF7EvidenceError("F7 semantic stages must be exactly validate/create/verify")
    plan_path = Path(str(case["plan_path"])).resolve()
    plan = _load_json(plan_path)
    output = Path(str(case["output_path"])).resolve()
    sidecar_path = Path(str(output) + ".dimension-verification.json")
    sidecar = _load_json(sidecar_path)
    _validate_schema(
        sidecar,
        _load_json(DIMENSION_VERIFICATION_SCHEMA_PATH),
        f"{case['case_id']} dimension verification sidecar",
    )
    if file_sha256(plan_path) != case["plan_file_sha256"] or canonical_json_sha256(
        plan, "DimensionPlan"
    ) != case["plan_canonical_sha256"]:
        raise DimensionF7EvidenceError("F7 plan changed during the semantic transaction")
    if not output.is_file():
        raise DimensionF7EvidenceError("F7 create stage did not commit the output drawing")

    stages: dict[str, dict[str, Any]] = {}
    for name in ("validate", "create", "verify"):
        result = stage_results[name]
        expected_status = "VALID" if name == "validate" else "COMPLETED"
        if result.get("ok") is not True or result.get("status") != expected_status:
            raise DimensionF7EvidenceError(f"F7 {name} stage did not complete successfully")
        if (
            result.get("planning_request_sha256") != case["planning_request_sha256"]
            or result.get("plan_canonical_sha256") != case["plan_canonical_sha256"]
        ):
            raise DimensionF7EvidenceError(f"F7 {name} stage broke request/plan continuity")
        stages[name] = {
            "ok": True,
            "status": expected_status,
            "planning_request_sha256": case["planning_request_sha256"],
            "plan_canonical_sha256": case["plan_canonical_sha256"],
        }
    if not _contains_true(stage_results["verify"], "independent_read_only_reopen"):
        raise DimensionF7EvidenceError("F7 verify stage lacks independent read-only reopen evidence")

    planned_ids = [dimension["dimension_id"] for dimension in plan["dimensions"]]
    rows = sidecar["reopen_verification"]["dimensions"]
    verified_ids = [row["dimension_id"] for row in rows]
    frozen = sidecar["frozen_inputs"]
    expected_frozen = {
        "dimension_plan": case["plan_file_sha256"],
        "handoff": file_sha256(Path(plan["handoff"]["path"])),
        "source_model": file_sha256(Path(plan["source_model"]["path"])),
        "source_drawing": file_sha256(Path(plan["source_drawing"]["path"])),
        "view_plan": file_sha256(Path(plan["view_plan"]["path"])),
        "verification_sidecar": file_sha256(Path(plan["verification_sidecar"]["path"])),
    }
    no_duplicate = len(verified_ids) == len(set(verified_ids))
    no_unplanned = verified_ids == planned_ids
    no_dangling = all(row["model_persistent_references"] for row in rows)
    source_unchanged = frozen == expected_frozen
    persisted = (
        sidecar["in_memory_verification"]["verified"] is True
        and sidecar["reopen_verification"]["verified"] is True
        and sidecar["in_memory_verification"]["dimensions"] == rows
    )
    invariants = {
        "no_dangling": no_dangling,
        "no_duplicate": no_duplicate,
        "no_unplanned": no_unplanned,
        "source_hashes_unchanged": source_unchanged,
        "save_close_readonly_reopen": sidecar["reopen_verification"]["verified"] is True,
        "independent_readonly_verify": True,
        "persisted_fingerprint_match": persisted,
    }
    if not all(invariants.values()):
        failed = sorted(name for name, passed in invariants.items() if not passed)
        raise DimensionF7EvidenceError("F7 case invariants failed: " + ", ".join(failed))

    report = {
        "protocol_id": "solidworks-dimension-f7-case-evidence",
        "schema_version": "1.0",
        "case_id": case["case_id"],
        "category": case["category"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "solidworks_revision": "33.5.0",
        "execution_service_sha256": file_sha256(execution_service_path.resolve()),
        "capability_manifest_sha256": file_sha256(capability_manifest_path.resolve()),
        "plan": {
            "plan_id": plan["plan_id"],
            "path": str(plan_path),
            "file_sha256": case["plan_file_sha256"],
            "canonical_sha256": case["plan_canonical_sha256"],
            "planning_request_sha256": case["planning_request_sha256"],
        },
        "output": {
            "path": str(output),
            "sha256": file_sha256(output),
            "verification_sidecar_path": str(sidecar_path),
            "verification_sidecar_sha256": file_sha256(sidecar_path),
        },
        "frozen_inputs": copy.deepcopy(frozen),
        "planned_dimension_ids": planned_ids,
        "planned_kinds": sorted({dimension["kind"] for dimension in plan["dimensions"]}),
        "verified_dimension_ids": verified_ids,
        "stages": stages,
        "invariants": invariants,
    }
    return validate_f7_case_evidence(report)


def build_f7_summary(
    request_candidate: Mapping[str, Any], evidence_paths: Sequence[Path]
) -> dict[str, Any]:
    request = validate_f7_matrix_request_for_evaluation(request_candidate)
    expected = {case["case_id"]: case for case in request["cases"]}
    reports: dict[str, tuple[Path, dict[str, Any]]] = {}
    for evidence_path in evidence_paths:
        path = evidence_path.resolve()
        report = validate_f7_case_evidence(_load_json(path))
        case_id = report["case_id"]
        if case_id in reports:
            raise DimensionF7EvidenceError(f"duplicate F7 evidence case_id: {case_id}")
        reports[case_id] = (path, report)
    if set(reports) != set(expected):
        missing = sorted(set(expected) - set(reports))
        unexpected = sorted(set(reports) - set(expected))
        raise DimensionF7EvidenceError(
            f"F7 evidence inventory mismatch; missing={missing}, unexpected={unexpected}"
        )

    category_counts = Counter({category: 0 for category in F7_CATEGORIES})
    kind_counts = Counter({kind: 0 for kind in F7_DIMENSION_KINDS})
    element_counts = Counter({element: 0 for element in F7_EXECUTION_ELEMENTS})
    bindings: list[dict[str, str]] = []
    for case_id, case in expected.items():
        evidence_path, report = reports[case_id]
        plan = _load_json(Path(case["plan_path"]))
        sidecar_path = Path(str(Path(case["output_path"])) + ".dimension-verification.json")
        sidecar = _load_json(sidecar_path)
        _validate_schema(
            sidecar,
            _load_json(DIMENSION_VERIFICATION_SCHEMA_PATH),
            f"{case_id} dimension verification sidecar",
        )
        _validate_case_binding(case, plan, sidecar, report)

        category_counts[case["category"]] += 1
        dimensions = plan["dimensions"]
        for dimension in dimensions:
            kind_counts[dimension["kind"]] += 1
            element_counts["attachment_persistent_reference"] += 1
            element_counts["annotation_position"] += 1
            element_counts["save_reopen_stable_identity"] += 1
            if dimension["source"]["source_tier"] == "model_or_pmi":
                element_counts["model_dimension_import"] += 1
            if dimension.get("tolerance") is not None:
                element_counts["dimension_tolerance"] += 1
            display = dimension["display_format"]
            if display.get("prefix") or display.get("suffix"):
                element_counts["dimension_prefix_suffix"] += 1
        bindings.append(
            {
                "case_id": case_id,
                "category": case["category"],
                "path": str(evidence_path),
                "sha256": file_sha256(evidence_path),
            }
        )

    complete = (
        all(category_counts[name] > 0 for name in F7_CATEGORIES)
        and all(kind_counts[name] > 0 for name in F7_DIMENSION_KINDS)
        and all(element_counts[name] > 0 for name in F7_EXECUTION_ELEMENTS)
    )
    summary = {
        "protocol_id": "solidworks-dimension-f7-live-matrix-summary",
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "solidworks_revision": "33.5.0",
        "f0_evidence": copy.deepcopy(request["f0_evidence"]),
        "case_evidence": bindings,
        "category_coverage": dict(category_counts),
        "dimension_kind_coverage": dict(kind_counts),
        "element_coverage": dict(element_counts),
        "source_hashes_unchanged": True,
        "all_cases_independently_verified": True,
        "overall_status": "complete" if complete else "incomplete",
    }
    _, _, summary_schema = load_f7_contracts()
    _validate_schema(summary, summary_schema, "F7 matrix summary")
    return summary


def build_f7_capability_promotion_candidate(
    current_manifest: Mapping[str, Any], summary_path: Path
) -> dict[str, Any]:
    manifest = _json_copy(current_manifest, "dimension capability manifest")
    summary_file = summary_path.resolve()
    summary = _load_json(summary_file)
    _, _, summary_schema = load_f7_contracts()
    _validate_schema(summary, summary_schema, "F7 matrix summary")
    if summary["overall_status"] != "complete":
        raise DimensionF7EvidenceError("incomplete F7 evidence cannot promote capabilities")
    if not summary["source_hashes_unchanged"] or not summary[
        "all_cases_independently_verified"
    ]:
        raise DimensionF7EvidenceError("F7 summary lacks immutable independent verification")
    if any(summary["category_coverage"][name] < 1 for name in F7_CATEGORIES):
        raise DimensionF7EvidenceError("F7 category coverage is incomplete")
    if any(summary["dimension_kind_coverage"][name] < 1 for name in F7_DIMENSION_KINDS):
        raise DimensionF7EvidenceError("F7 DimensionPlan kind coverage is incomplete")
    if any(summary["element_coverage"][name] < 1 for name in F7_EXECUTION_ELEMENTS):
        raise DimensionF7EvidenceError("F7 execution-element coverage is incomplete")

    evidence_sha = file_sha256(summary_file)
    manifest["registry_version"] = "0.4.0"
    manifest["executor_version"] = "0.3.0"
    manifest["live_evidence"] = {
        "summary_sha256": evidence_sha,
        "solidworks_revision": "33.5.0",
    }
    for row in manifest["capabilities"]:
        row["status"] = "unsupported" if row["id"] == "annotation_text_bounds" else "supported"
    for name in F7_DIMENSION_KINDS:
        manifest["dimension_types"][name] = {
            "status": "supported",
            "reason": "F7 six-category native create/readback/save/reopen/independent-verify matrix passed.",
            "verification": "live",
            "evidence_sha256": evidence_sha,
        }
    for name in F7_EXECUTION_ELEMENTS:
        manifest["elements"][name] = {
            "status": "supported",
            "reason": "F7 six-category persisted native evidence matrix passed.",
            "verification": "live",
            "evidence_sha256": evidence_sha,
        }
    manifest["elements"]["annotation_text_bounds"] = {
        "status": "unsupported",
        "reason": (
            "SolidWorks 2025 SP5 exposes stable anchors/display primitives but not exact ordinary "
            "display-dimension glyph bounds; F7 summary binds the frozen F0 conclusion."
        ),
        "verification": "live",
        "evidence_sha256": evidence_sha,
    }
    return DimensionCapabilityManifest.model_validate(manifest).model_dump(mode="json")


def publish_json_once(candidate: Mapping[str, Any], output_path: Path) -> tuple[str, str]:
    output = output_path.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite immutable F7 artifact: {output}")
    if output.suffix.lower() != ".json" or not output.parent.is_dir():
        raise DimensionF7EvidenceError("F7 output must be a new .json in an existing directory")
    validation_root = (PACKAGE_ROOT.parent / "validation").resolve()
    if output == validation_root or validation_root in output.parents:
        raise DimensionF7EvidenceError("F7 output must not be written under validation/")
    payload = json.dumps(
        _json_copy(candidate, "F7 output"), ensure_ascii=False, indent=2, allow_nan=False
    ) + "\n"
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


def validate_f7_matrix_request_for_evaluation(
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a request after the live run, when declared outputs now must exist."""

    request = _json_copy(candidate, "F7 matrix request")
    request_schema, _, _ = load_f7_contracts()
    planning_schema = _load_json(PLANNING_REQUEST_SCHEMA_PATH)
    plan_schema = _load_json(DIMENSION_PLAN_SCHEMA_PATH)
    registry = Registry().with_resource(
        planning_schema["$id"], Resource.from_contents(planning_schema)
    )
    _validate_schema(request, request_schema, "F7 matrix request", registry=registry)
    f0 = _absolute_file_binding(request["f0_evidence"], ".json", "F0 evidence")
    request["f0_evidence"]["path"] = str(f0)
    _validate_f0_summary(_load_json(f0))
    if {case["category"] for case in request["cases"]} != set(F7_CATEGORIES):
        raise DimensionF7EvidenceError("F7 matrix category coverage is incomplete")
    if len({case["case_id"] for case in request["cases"]}) != len(request["cases"]):
        raise DimensionF7EvidenceError("F7 matrix case_id values must be unique")
    for case in request["cases"]:
        plan_path = _absolute_file_binding(
            {"path": case["plan_path"], "sha256": case["plan_file_sha256"]},
            ".json",
            f"{case['case_id']} plan",
        )
        plan = _load_json(plan_path)
        case["plan_path"] = str(plan_path)
        _validate_schema(plan, plan_schema, f"{case['case_id']} DimensionPlan")
        if canonical_json_sha256(plan, "DimensionPlan") != case["plan_canonical_sha256"]:
            raise DimensionF7EvidenceError(f"{case['case_id']} plan canonical hash mismatch")
        if canonical_json_sha256(
            case["planning_request"], "dimension planning request"
        ) != case["planning_request_sha256"]:
            raise DimensionF7EvidenceError(
                f"{case['case_id']} planning request hash mismatch"
            )
    return request


def _validate_f0_summary(summary: Mapping[str, Any]) -> None:
    if (
        summary.get("protocol_id") != "solidworks-dimension-api-probe-run-summary"
        or summary.get("schema_version") != "1.0"
    ):
        raise DimensionF7EvidenceError("F0 evidence is not the frozen live-probe summary")
    case_count = summary.get("case_count")
    cases = summary.get("cases")
    matrix = summary.get("matrix")
    if (
        not isinstance(case_count, int)
        or isinstance(case_count, bool)
        or case_count < 1
        or summary.get("failed_count") != 0
        or not isinstance(cases, list)
        or len(cases) != case_count
        or any(
            not isinstance(case, Mapping) or case.get("status") != "evaluated"
            for case in cases
        )
        or not isinstance(matrix, Mapping)
        or matrix.get("overall_status") != "complete"
        or matrix.get("research_coverage_complete") is not True
        or not isinstance(matrix.get("production_frozen_case_count"), int)
        or matrix["production_frozen_case_count"] < 1
    ):
        raise DimensionF7EvidenceError("F0 live-probe summary is incomplete or contains failures")


def _validate_case_binding(
    case: Mapping[str, Any],
    plan: Mapping[str, Any],
    sidecar: Mapping[str, Any],
    report: Mapping[str, Any],
) -> None:
    case_id = case["case_id"]
    output = Path(case["output_path"]).resolve()
    evidence_plan = report["plan"]
    evidence_output = report["output"]
    if report["category"] != case["category"] or report["solidworks_revision"] != "33.5.0":
        raise DimensionF7EvidenceError(f"{case_id} category/revision mismatch")
    if (
        Path(evidence_plan["path"]).resolve() != Path(case["plan_path"]).resolve()
        or evidence_plan["file_sha256"] != case["plan_file_sha256"]
        or evidence_plan["canonical_sha256"] != case["plan_canonical_sha256"]
        or evidence_plan["planning_request_sha256"] != case["planning_request_sha256"]
        or evidence_plan["plan_id"] != plan["plan_id"]
    ):
        raise DimensionF7EvidenceError(f"{case_id} immutable plan/request binding mismatch")
    sidecar_path = Path(str(output) + ".dimension-verification.json")
    if (
        Path(evidence_output["path"]).resolve() != output
        or evidence_output["sha256"] != file_sha256(output)
        or Path(evidence_output["verification_sidecar_path"]).resolve() != sidecar_path
        or evidence_output["verification_sidecar_sha256"] != file_sha256(sidecar_path)
    ):
        raise DimensionF7EvidenceError(f"{case_id} output/sidecar hash binding mismatch")
    if (
        sidecar.get("verified") is not True
        or Path(sidecar.get("output_path", "")).resolve() != output
        or sidecar.get("artifact_sha256") != evidence_output["sha256"]
        or sidecar.get("plan_file_sha256") != case["plan_file_sha256"]
        or sidecar.get("plan_canonical_sha256") != case["plan_canonical_sha256"]
    ):
        raise DimensionF7EvidenceError(f"{case_id} committed sidecar binding mismatch")

    expected_frozen = {
        "dimension_plan": case["plan_file_sha256"],
        "handoff": file_sha256(Path(plan["handoff"]["path"])),
        "source_model": file_sha256(Path(plan["source_model"]["path"])),
        "source_drawing": file_sha256(Path(plan["source_drawing"]["path"])),
        "view_plan": file_sha256(Path(plan["view_plan"]["path"])),
        "verification_sidecar": file_sha256(Path(plan["verification_sidecar"]["path"])),
    }
    if report["frozen_inputs"] != expected_frozen or sidecar.get("frozen_inputs") != expected_frozen:
        raise DimensionF7EvidenceError(f"{case_id} frozen input hashes changed")

    planned_ids = [dimension["dimension_id"] for dimension in plan["dimensions"]]
    verified_rows = sidecar["reopen_verification"]["dimensions"]
    verified_ids = [row["dimension_id"] for row in verified_rows]
    planned_kinds = sorted({dimension["kind"] for dimension in plan["dimensions"]})
    if (
        report["planned_dimension_ids"] != planned_ids
        or report["verified_dimension_ids"] != planned_ids
        or verified_ids != planned_ids
        or report["planned_kinds"] != planned_kinds
    ):
        raise DimensionF7EvidenceError(f"{case_id} planned/persisted dimension inventory mismatch")
    for stage in report["stages"].values():
        if (
            stage["planning_request_sha256"] != case["planning_request_sha256"]
            or stage["plan_canonical_sha256"] != case["plan_canonical_sha256"]
        ):
            raise DimensionF7EvidenceError(f"{case_id} semantic-stage continuity mismatch")


def _absolute_file_binding(binding: Mapping[str, Any], suffix: str, label: str) -> Path:
    path = Path(str(binding["path"])).resolve()
    if not path.is_file() or path.suffix.lower() != suffix:
        raise DimensionF7EvidenceError(f"{label} must be an existing {suffix} file: {path}")
    if file_sha256(path) != binding["sha256"]:
        raise DimensionF7EvidenceError(f"{label} SHA-256 mismatch: {path}")
    return path


def _contains_true(value: object, key: str) -> bool:
    if isinstance(value, Mapping):
        if value.get(key) is True:
            return True
        return any(_contains_true(item, key) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_true(item, key) for item in value)
    return False


def _new_absolute_path(value: str, suffix: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != suffix:
        raise DimensionF7EvidenceError(f"{label} must be an absolute new {suffix} path")
    resolved = path.resolve()
    if resolved.exists() or not resolved.parent.is_dir():
        raise DimensionF7EvidenceError(f"{label} must be new in an existing directory: {resolved}")
    validation_root = (PACKAGE_ROOT.parent / "validation").resolve()
    if resolved == validation_root or validation_root in resolved.parents:
        raise DimensionF7EvidenceError(f"{label} must not be under validation/")
    return resolved


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DimensionF7EvidenceError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DimensionF7EvidenceError(f"JSON artifact must contain an object: {path}")
    return value


def _json_copy(candidate: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise DimensionF7EvidenceError(f"{label} must be an object")
    try:
        value = json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DimensionF7EvidenceError(f"{label} is not strict JSON: {exc}") from exc
    return value


def _validate_schema(
    candidate: Mapping[str, Any],
    schema: Mapping[str, Any],
    label: str,
    *,
    registry: Registry | None = None,
) -> None:
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
        registry=registry or Registry(),
    )
    errors = sorted(validator.iter_errors(candidate), key=lambda item: list(item.path))
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise DimensionF7EvidenceError(
            f"{label} contract failed at {pointer or '/'}: {error.message}"
        )
