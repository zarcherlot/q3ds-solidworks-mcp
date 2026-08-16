"""Prepare the complete immutable F7 production-capability qualification matrix.

The preparer is COM-free.  It consumes six distinct real handoffs and six advanced evidence-bound
recipes, validates all candidates before publication, requires the 18 DimensionPlan kinds exactly
once plus all six execution elements, publishes one plan per handoff, and publishes the matrix
request last.  Live qualification remains the responsibility of ``run_dimension_f7_live_matrix``.
"""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from drawing_planner.planning_models import canonical_json_sha256

from .f7_evidence import (
    F7_CATEGORIES,
    DimensionF7EvidenceError,
    publish_json_once,
    validate_f7_matrix_request,
    validate_f7_pre_live_coverage,
)
from .first_draft import build_first_draft_candidate
from .handoff import file_sha256
from .planner_engine import DimensionPlannerEngine


PACKAGE_ROOT = Path(__file__).resolve().parent
PREPARATION_SCHEMA_PATH = (
    PACKAGE_ROOT / "contracts" / "dimension-f7-preparation-request.schema.json"
)


def prepare_f7_live_matrix(candidate: Mapping[str, Any]) -> dict[str, Any]:
    request = _json_copy(candidate, "F7 preparation request")
    _validate_schema(request)
    matrix_output = _new_json_path(
        request["matrix_request_output"], "matrix_request_output"
    )
    f0_path = _bound_json(request["f0_evidence"], "F0 evidence")
    if tuple(case["category"] for case in request["cases"]) != F7_CATEGORIES:
        raise DimensionF7EvidenceError(
            "F7 preparation cases must preserve the canonical six-category order"
        )
    if len({case["case_id"] for case in request["cases"]}) != 6:
        raise DimensionF7EvidenceError("F7 preparation case_id values must be unique")

    prepared: list[dict[str, Any]] = []
    protected: set[Path] = {f0_path, PREPARATION_SCHEMA_PATH.resolve(strict=True)}
    targets: set[Path] = {matrix_output}
    for index, case in enumerate(request["cases"]):
        handoff_path = _bound_json(case["handoff"], f"cases[{index}].handoff")
        recipe_path = _bound_json(case["recipe"], f"cases[{index}].recipe")
        recipe = _load_json(recipe_path)
        if recipe.get("protocol_id") != "solidworks-dimension-first-draft-recipe" or recipe.get(
            "schema_version"
        ) != "1.1":
            raise DimensionF7EvidenceError(
                f"cases[{index}] must use the advanced evidence-bound recipe version 1.1"
            )
        plan, planning_request, report = build_first_draft_candidate(
            handoff_path, recipe
        )
        if planning_request.handoff_sha256 != case["handoff"]["sha256"]:
            raise DimensionF7EvidenceError(
                f"cases[{index}] recipe builder changed the handoff binding"
            )
        if report["execution_readiness"] != "capability_blocked":
            raise DimensionF7EvidenceError(
                f"cases[{index}] is not an unpromoted F7 qualification candidate"
            )
        plan_path = (
            Path(planning_request.publication_directory).resolve()
            / "dimension_plan.json"
        )
        output_path = _new_path(case["output_path"], ".slddrw", f"cases[{index}].output")
        evidence_path = _new_path(case["evidence_path"], ".json", f"cases[{index}].evidence")
        sidecar_path = Path(str(output_path) + ".dimension-verification.json")
        if plan_path.exists():
            raise DimensionF7EvidenceError(
                f"cases[{index}] immutable plan publication already exists: {plan_path}"
            )
        for target in (plan_path, output_path, sidecar_path, evidence_path):
            resolved = target.resolve()
            if resolved in targets:
                raise DimensionF7EvidenceError(f"duplicate F7 target path: {resolved}")
            targets.add(resolved)
        protected.update(_plan_inputs(plan))
        protected.update({handoff_path, recipe_path})
        prepared.append(
            {
                "source": copy.deepcopy(case),
                "plan": plan,
                "request": planning_request,
                "plan_path": plan_path,
                "output_path": output_path,
                "evidence_path": evidence_path,
            }
        )

    validate_f7_pre_live_coverage(
        tuple((row["source"], row["plan"]) for row in prepared),
        require_each_kind_exactly_once=True,
    )
    protected_before = _snapshot(protected)

    matrix_cases: list[dict[str, Any]] = []
    engine = DimensionPlannerEngine()
    for row in prepared:
        result = engine.validate_and_publish(row["plan"], row["request"])
        if (
            result.status != "published"
            or result.execution_readiness != "capability_blocked"
            or result.plan is None
            or not result.validation.engineering_passed
        ):
            issues = [issue.model_dump(mode="json") for issue in result.validation.issues]
            raise DimensionF7EvidenceError(
                f"{row['source']['case_id']} publication failed closed: {issues}"
            )
        published_path = Path(result.plan.path).resolve(strict=True)
        if published_path != row["plan_path"]:
            raise DimensionF7EvidenceError("DimensionPlan publication path drifted")
        published = _load_json(published_path)
        planning_request = row["request"].model_dump(mode="json")
        matrix_cases.append(
            {
                "case_id": row["source"]["case_id"],
                "category": row["source"]["category"],
                "plan_path": str(published_path),
                "plan_file_sha256": file_sha256(published_path),
                "plan_canonical_sha256": canonical_json_sha256(
                    published, "DimensionPlan"
                ),
                "planning_request": planning_request,
                "planning_request_sha256": canonical_json_sha256(
                    planning_request, "dimension planning request"
                ),
                "output_path": str(row["output_path"]),
                "evidence_path": str(row["evidence_path"]),
            }
        )

    if protected_before != _snapshot(protected):
        raise DimensionF7EvidenceError(
            "F7 preparation changed a handoff, recipe, model, drawing, plan or verification input"
        )
    matrix = {
        "protocol_id": "solidworks-dimension-f7-matrix-request",
        "schema_version": "1.0",
        "solidworks_revision": "33.5.0",
        "f0_evidence": copy.deepcopy(request["f0_evidence"]),
        "cases": matrix_cases,
    }
    normalized = validate_f7_matrix_request(matrix)
    path, sha256 = publish_json_once(normalized, matrix_output)
    return {
        "ok": True,
        "status": "prepared",
        "matrix_request_path": path,
        "matrix_request_sha256": sha256,
        "case_count": len(matrix_cases),
        "dimension_kind_count": len(
            {
                dimension["kind"]
                for row in prepared
                for dimension in row["plan"]["dimensions"]
            }
        ),
        "execution_element_count": 6,
        "production_execution_readiness": "capability_blocked",
    }


def _plan_inputs(plan: Mapping[str, Any]) -> set[Path]:
    return {
        Path(plan[name]["path"]).resolve(strict=True)
        for name in (
            "handoff",
            "source_model",
            "source_drawing",
            "view_plan",
            "verification_sidecar",
        )
    }


def _bound_json(binding: Mapping[str, Any], label: str) -> Path:
    path = Path(str(binding["path"])).resolve()
    if not path.is_file() or path.suffix.lower() != ".json":
        raise DimensionF7EvidenceError(f"{label} must be an existing JSON file: {path}")
    if file_sha256(path) != binding["sha256"]:
        raise DimensionF7EvidenceError(f"{label} SHA-256 mismatch: {path}")
    return path


def _new_json_path(value: str, label: str) -> Path:
    return _new_path(value, ".json", label)


def _new_path(value: str, suffix: str, label: str) -> Path:
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


def _snapshot(paths: set[Path]) -> dict[str, str]:
    return {
        os.path.normcase(str(path.resolve(strict=True))): file_sha256(path)
        for path in sorted(paths, key=lambda item: os.path.normcase(str(item)))
    }


def _validate_schema(candidate: Mapping[str, Any]) -> None:
    schema = _load_json(PREPARATION_SCHEMA_PATH)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(candidate),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise DimensionF7EvidenceError(
            f"F7 preparation contract failed at {pointer or '/'}: {error.message}"
        )


def _json_copy(candidate: Mapping[str, Any], label: str) -> dict[str, Any]:
    try:
        value = json.loads(json.dumps(candidate, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise DimensionF7EvidenceError(f"{label} is not strict JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DimensionF7EvidenceError(f"{label} must be an object")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DimensionF7EvidenceError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DimensionF7EvidenceError(f"JSON artifact must contain an object: {path}")
    return value
