"""Production MCP entry point: a small, semantic, strongly validated tool surface.

Low-level COM-shaped operations live behind the local execution service. The historical broad
adapter remains available as ``legacy_server.py`` for development diagnostics, but is not the
default MCP server.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from pathlib import Path
from typing import Annotated

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from drawing_planner.capability_registry import current_registry
from drawing_planner.model_gateway import (
    PlanningModelResponseError,
    PlanningModelUnavailable,
)
from drawing_planner.plan_store import PlanStore
from drawing_planner.planner_engine import PlannerEngine
from drawing_planner.planning_models import (
    PlanPublicationResult,
    PlanningAudit,
    PlanningRequest,
    canonical_json_sha256,
    json_object_copy,
)
from drawing_planner.planning_prompt_compiler import (
    PlannerProfileUnavailable,
    RepositoryPlanningPromptCompiler,
)
from drawing_planner.validators import HandoffIntegrityValidator, RepositoryViewPlanValidator
from dimension_planner.capability_registry import (
    current_registry as current_dimension_registry,
)
from dimension_planner.handoff import (
    build_handoff_request,
    file_sha256,
    validate_dimension_planning_handoff,
)
from dimension_planner.f7_evidence import (
    validate_f7_matrix_request,
    validate_f7_matrix_request_for_evaluation,
)
from dimension_planner.planner_engine import DimensionPlannerEngine
from dimension_planner.planning_models import DimensionPlanningRequest
from dimension_planner.validators import RepositoryDimensionPlanValidator
from execution_client import (
    bootstrap_host,
    call_tool,
    create_dimension_planning_handoff,
    ensure_ready,
    get_health,
    get_state,
    release_owned_session,
)
from planning_sampling import McpSamplingPlanningModelGateway
from config import SAMPLING_FALLBACK
from sampling_fallback import build_sampling_fallback_handler
from semantic_models import (
    ApprovedDimensionInput,
    DimensionPlan,
    ViewPlan,
    validate_drawing_template_path,
    validate_host_report_directory,
    validate_model_path,
    validate_publication_directory,
)


MCP_INSTRUCTIONS = (
    "Use inspect_solidworks_host for deep installation/registry/filesystem inspection and "
    "bootstrap_solidworks_host for isolated COM verification. Registration repair is opt-in and "
    "requires an execution service that is already elevated; MCP never elevates itself. "
    "ViewPlan 1.4 and DimensionPlan 1.0 are the only production drawing protocols on this "
    "MCP surface. Use "
    "initialize_part_drawing_handoff to generate a verified, immutable initializer handoff, then "
    "either plan_part_drawing_views for MCP Sampling or the explicit upper-layer Skill followed "
    "by publish_validated_part_drawing_view_plan; both publish a frozen ViewPlan only after the "
    "same deterministic validation. capability_blocked is valid planning output but cannot "
    "be executed. Validate, create, and verify drawings only through the repository ViewPlan "
    "semantic tools with the original PlanningRequest. DrawingPlan 1.0 is not available on the "
    "default surface. Never convert between protocols, repair a frozen plan, or expose private COM "
    "operations. Dimension planning must start from the independently initialized immutable "
    "dimension handoff and retain one unchanged DimensionPlanningRequest through publish, "
    "validate, create, and verify."
)

_sampling_fallback_handler = build_sampling_fallback_handler(SAMPLING_FALLBACK)

mcp = FastMCP(
    "Q3DS SolidWorks Engineering",
    instructions=MCP_INSTRUCTIONS,
    version="2.3.0",
    strict_input_validation=True,
    sampling_handler=_sampling_fallback_handler,
    sampling_handler_behavior="fallback",
)

_state_version = 0
_state_lock = threading.RLock()


def _operation_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def _state_mismatch(response: dict) -> bool:
    return (
        response.get("status") == "FAILED"
        and (response.get("error") or {}).get("code") == "INVALID_STATE_VERSION"
    )


def _execute(tool: str, params: dict, *, mutating: bool) -> dict:
    """Serialize state-version handling and retry one authoritative resync."""
    global _state_version
    with _state_lock:
        response = call_tool(tool, _operation_id(tool), _state_version, params)
        if _state_mismatch(response):
            _state_version = get_state()
            response = call_tool(tool, _operation_id(tool), _state_version, params)
        if response.get("status") == "COMPLETED" and mutating:
            returned = response.get("stateVersion")
            if not isinstance(returned, int) or returned <= _state_version:
                raise RuntimeError(
                    f"execution layer returned invalid stateVersion for mutating tool '{tool}'"
                )
            _state_version = returned
        return response


def _semantic_response(response: dict) -> str:
    status = response.get("status") or "UNKNOWN"
    payload = {
        "ok": status in {"COMPLETED", "DUPLICATE"} and bool(response.get("verified", True)),
        "status": status,
        "verified": bool(response.get("verified", False)),
        "state_version": response.get("stateVersion", response.get("last_known_state_version")),
    }
    if response.get("result_geometry") is not None:
        payload["result"] = response["result_geometry"]
    error = response.get("error") or {}
    if error:
        payload["error"] = {"code": error.get("code"), "message": error.get("message")}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _planning_request_sha256(request: PlanningRequest) -> str:
    return canonical_json_sha256(
        request.model_dump(mode="json"), "planning request"
    )


def _semantic_response_with_plan_binding(
    response: dict, plan: dict, request: PlanningRequest
) -> str:
    payload = json.loads(_semantic_response(response))
    payload["planning_request_sha256"] = _planning_request_sha256(request)
    payload["plan_canonical_sha256"] = canonical_json_sha256(plan, "view plan")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(
    description=(
        "Report the local execution/SolidWorks readiness and authoritative state version. "
        "When launch_if_needed=true, the C# Execution Service performs a bounded readiness probe "
        "and releases only a session it launched before returning."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def solidworks_status(launch_if_needed: bool = False) -> str:
    global _state_version
    cleanup = None
    if launch_if_needed:
        health = ensure_ready()
        cleanup = release_owned_session()
    else:
        health = get_health()
    with _state_lock:
        returned_state = health.get("stateVersion")
        _state_version = int(returned_state if returned_state is not None else get_state())
    return json.dumps(
        {
            "ok": health.get("status") == "UP" and (
                cleanup is None or cleanup.get("status") != "blocked"
            ),
            "com_attached": bool(health.get("comAttached")),
            "solidworks_launched": bool(health.get("swLaunched", False)),
            "solidworks_version": health.get("swVersion"),
            "active_document": health.get("activeDocument"),
            "state_version": _state_version,
            "owned_session_cleanup": cleanup,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _host_bootstrap_payload(
    *,
    mode: str,
    output_directory: str,
    drawing_template_path: str | None,
    visible: bool,
    keep_solidworks_running: bool,
    com_timeout_seconds: int,
    regserver_timeout_seconds: int,
) -> dict:
    output = validate_host_report_directory(output_directory)
    template = (
        validate_drawing_template_path(drawing_template_path)
        if drawing_template_path is not None
        else None
    )
    return {
        "mode": mode,
        "output_directory": output,
        "drawing_template_path": template,
        "visible": visible,
        "keep_solidworks_running": keep_solidworks_running,
        "com_timeout_seconds": com_timeout_seconds,
        "regserver_timeout_seconds": regserver_timeout_seconds,
    }


@mcp.tool(
    description=(
        "Inspect this Windows host for SolidWorks automation readiness without launching "
        "SolidWorks or attempting registration repair. The repository-owned native x64 helper "
        "checks both registry views, installation files, type library/Interop inventory, optional "
        "drawing template, interactive-session constraints, and report-directory read/write/delete "
        "access, then publishes host-preflight-report.json with a verified SHA-256."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def inspect_solidworks_host(
    output_directory: str,
    drawing_template_path: str | None = None,
) -> str:
    payload = _host_bootstrap_payload(
        mode="inspect",
        output_directory=output_directory,
        drawing_template_path=drawing_template_path,
        visible=False,
        keep_solidworks_running=False,
        com_timeout_seconds=180,
        regserver_timeout_seconds=120,
    )
    return json.dumps(
        bootstrap_host(payload), ensure_ascii=False, separators=(",", ":")
    )


@mcp.tool(
    description=(
        "Verify SolidWorks COM activation in an isolated bounded child process using the "
        "repository-owned native x64 helper, preserving any pre-existing user session and quitting "
        "only a session the probe owns. Set allow_registration_repair=true only for an explicit "
        "one-time /regserver repair attempt; the Execution Service must already be elevated and MCP "
        "cannot elevate it. Never opens or modifies business part, assembly, or drawing documents."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def bootstrap_solidworks_host(
    output_directory: str,
    drawing_template_path: str | None = None,
    allow_registration_repair: bool = False,
    visible: bool = False,
    keep_solidworks_running: bool = False,
    com_timeout_seconds: Annotated[int, Field(ge=10, le=600)] = 180,
    regserver_timeout_seconds: Annotated[int, Field(ge=10, le=300)] = 120,
) -> str:
    payload = _host_bootstrap_payload(
        mode="repair" if allow_registration_repair else "verify",
        output_directory=output_directory,
        drawing_template_path=drawing_template_path,
        visible=visible,
        keep_solidworks_running=keep_solidworks_running,
        com_timeout_seconds=com_timeout_seconds,
        regserver_timeout_seconds=regserver_timeout_seconds,
    )
    return json.dumps(
        bootstrap_host(payload), ensure_ascii=False, separators=(",", ":")
    )


@mcp.tool(
    description=(
        "Read-only drawing-source preflight for one saved part. Returns exact configuration "
        "names, localized standard-view names, and its bounding box in meters; restores the "
        "previous active document."
    ),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def inspect_part_for_drawing(model_path: str) -> str:
    path = validate_model_path(model_path)
    return _semantic_response(
        _execute("inspect_part_for_drawing", {"model_path": path}, mutating=False)
    )


@mcp.tool(
    description=(
        "Generate the complete immutable initializer handoff for one saved SolidWorks part. The "
        "repository C# transaction opens the source read-only, creates and read-only-reopens a new "
        "blank drawing from the supplied template, captures six real standard-view PNGs, freezes "
        "geometry/readiness reports, and publishes drawing-planning-handoff.json last. Every output "
        "must be new; the previous active document is restored and Python performs no COM calls."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def initialize_part_drawing_handoff(
    model_path: str,
    drawing_template_path: str,
    publication_directory: str,
    image_width: Annotated[int, Field(ge=320, le=2000)] = 1024,
    image_height: Annotated[int, Field(ge=240, le=2000)] = 768,
) -> str:
    model = validate_model_path(model_path)
    template = validate_drawing_template_path(drawing_template_path)
    publication = validate_publication_directory(publication_directory)
    expected = [
        "drawing-planning-handoff.json",
        "initializer-blank.SLDDRW",
        "drawing-readiness.json",
        "model-geometry.json",
        "front.png",
        "back.png",
        "left.png",
        "right.png",
        "top.png",
        "bottom.png",
    ]
    collisions = [name for name in expected if (Path(publication) / name).exists()]
    if collisions:
        raise ValueError(
            "initializer outputs must all be new: " + ", ".join(collisions)
        )
    response = _execute(
        "initialize_part_drawing_handoff",
        {
            "model_path": model,
            "drawing_template_path": template,
            "publication_directory": publication,
            "image_width": image_width,
            "image_height": image_height,
        },
        mutating=True,
    )
    payload = json.loads(_semantic_response(response))
    if not payload.get("ok"):
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ToolError("INITIALIZER_RESULT_INVALID: executor returned no result object")
    manifest_path = result.get("manifest_path")
    manifest_sha256 = result.get("manifest_sha256")
    if (
        not isinstance(manifest_path, str)
        or Path(manifest_path).name.lower() != "drawing-planning-handoff.json"
        or not isinstance(manifest_sha256, str)
    ):
        raise ToolError("INITIALIZER_RESULT_INVALID: manifest path/hash is missing")
    request = PlanningRequest(
        handoff_manifest_path=manifest_path,
        handoff_manifest_sha256=manifest_sha256,
        planner_profile="production",
        debug_prompt_directory=None,
        publication_directory=publication,
        user_requirements={"source_model_read_only": True},
    )
    integrity = HandoffIntegrityValidator().validate(request)
    if integrity.status != "pass":
        details = "; ".join(
            f"{issue.code}: {issue.message}" for issue in integrity.issues
        )
        raise ToolError(
            "INITIALIZER_INTEGRITY_VALIDATION_FAILED: "
            + (details or "repository handoff validation failed")
        )
    payload["handoff_integrity"] = "pass"
    payload["planning_request"] = request.model_dump(mode="json")
    payload["planning_request_sha256"] = _planning_request_sha256(request)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(
    description=(
        "Plan the main view, minimum view set, section/detail/auxiliary decisions, feature "
        "coverage, and sheet layout for one verified part-drawing initializer handoff. Calls the "
        "connected client's MCP Sampling model with the immutable repository prompt/profile and "
        "exact ViewPlan 1.4 Schema, then applies integrity, Schema, semantics, coverage, layout, "
        "and capability gates before atomically publishing view_plan.json. Never starts or writes "
        "SolidWorks. A valid plan may be published as capability_blocked without downgrade."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def plan_part_drawing_views(
    request: PlanningRequest, ctx: Context
) -> str:
    engine = PlannerEngine(
        prompt_compiler=RepositoryPlanningPromptCompiler(),
        model_gateway=McpSamplingPlanningModelGateway(ctx),
        validator=RepositoryViewPlanValidator(),
        capabilities=current_registry(),
        plan_store=PlanStore(),
    )
    try:
        result = await engine.plan(request)
    except PlanningModelUnavailable as exc:
        raise ToolError(f"PLANNER_MODEL_UNAVAILABLE: {exc}") from exc
    except PlanningModelResponseError as exc:
        raise ToolError(f"PLANNER_MODEL_RESPONSE_INVALID: {exc}") from exc
    except PlannerProfileUnavailable as exc:
        raise ToolError(f"PLANNER_PROFILE_UNAVAILABLE: {exc}") from exc
    except FileExistsError as exc:
        raise ToolError(f"VIEW_PLAN_ALREADY_EXISTS: {exc}") from exc
    return result.model_dump_json()


@mcp.tool(
    description=(
        "Revalidate and atomically publish exactly one complete ViewPlan 1.4 candidate generated "
        "by an explicit upper-layer planning workflow. Re-hashes the immutable handoff, applies "
        "repository Schema, engineering semantics, feature coverage, sheet layout, and current "
        "executor capability gates, then creates view_plan.json without overwrite. Does not call "
        "a model, start SolidWorks, invoke COM, or claim unverifiable model/prompt provenance. A "
        "valid plan may be published as capability_blocked but cannot be executed."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def publish_validated_part_drawing_view_plan(
    plan: ViewPlan, request: PlanningRequest
) -> str:
    normalized, validation, assessment = _validate_view_plan(plan, request)
    request_sha256 = _planning_request_sha256(request)
    candidate_sha256 = canonical_json_sha256(normalized, "model plan candidate")
    if not validation.passed:
        return PlanPublicationResult(
            ok=False,
            status="rejected",
            execution_readiness="not_assessed",
            validation=validation,
            plan=None,
            audit=PlanningAudit(
                request_sha256=request_sha256,
                candidate_sha256=candidate_sha256,
            ),
        ).model_dump_json()

    if assessment is None:
        raise RuntimeError("validated ViewPlan did not receive a capability assessment")
    try:
        published = PlanStore().publish(normalized, request.publication_directory)
    except FileExistsError as exc:
        raise ToolError(f"VIEW_PLAN_ALREADY_EXISTS: {exc}") from exc
    return PlanPublicationResult(
        ok=True,
        status="published",
        execution_readiness=assessment.status,
        validation=validation,
        plan=published,
        audit=PlanningAudit(
            request_sha256=request_sha256,
            candidate_sha256=candidate_sha256,
            capability_manifest_version=assessment.manifest_version,
        ),
        unsupported_capabilities=assessment.unsupported_capabilities,
    ).model_dump_json()


@mcp.tool(
    description=(
        "Validate one complete solidworks-view-plan schema-1.4 object against its original strict "
        "PlanningRequest. Re-hashes the handoff and all frozen artifacts, then applies repository "
        "Schema, engineering semantics, feature coverage, sheet layout, current executor capability, "
        "and the independent C# execution contract. Does not start or write SolidWorks."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def validate_part_drawing_view_plan(
    plan: ViewPlan, request: PlanningRequest
) -> str:
    normalized, validation, assessment = _validate_view_plan(plan, request)
    payload = {
        "ok": validation.passed,
        "status": "VALID" if validation.passed else "REJECTED",
        "planning_request_sha256": _planning_request_sha256(request),
        "plan_canonical_sha256": canonical_json_sha256(normalized, "view plan"),
        "validation": validation.model_dump(mode="json"),
        "execution_readiness": assessment.status if assessment else "not_assessed",
        "unsupported_capabilities": list(
            assessment.unsupported_capabilities if assessment else ()
        ),
    }
    if validation.passed:
        executor_response = _execute(
            "validate_frozen_part_drawing_view_plan",
            {"plan": normalized},
            mutating=False,
        )
        payload["executor"] = json.loads(_semantic_response(executor_response))
        payload["ok"] = bool(payload["executor"].get("ok"))
        if not payload["ok"]:
            payload["status"] = "EXECUTOR_REJECTED"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(
    description=(
        "Transactionally create one new associated .SLDDRW from a complete ViewPlan 1.4 object and "
        "its original PlanningRequest. Re-runs all deterministic and capability gates, re-hashes ten "
        "frozen execution inputs before COM, and succeeds only after save, close, read-only reopen, "
        "exact view/sheet verification, and no-overwrite drawing plus SHA-256 audit-sidecar commit."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def create_part_drawing_from_view_plan(
    plan: ViewPlan,
    request: PlanningRequest,
    output_path: str,
) -> str:
    normalized, validation, assessment = _validate_view_plan(plan, request)
    _require_executable_view_plan(validation, assessment)
    output = _validate_drawing_output_path(output_path, require_existing=False)
    response = _execute(
        "execute_part_drawing_view_plan",
        {"plan": normalized, "output_path": output},
        mutating=True,
    )
    return _semantic_response_with_plan_binding(response, normalized, request)


@mcp.tool(
    description=(
        "Independently verify an existing drawing created from a complete ViewPlan 1.4 object and "
        "its original PlanningRequest. Re-runs all frozen-input, deterministic and capability gates, "
        "validates the B3 audit sidecar and drawing hash before COM, then opens the drawing read-only "
        "and rechecks persistent identities, parentage, orientation, layout, display and sheet contract."
    ),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def verify_part_drawing_view_plan(
    plan: ViewPlan,
    request: PlanningRequest,
    output_path: str,
) -> str:
    normalized, validation, assessment = _validate_view_plan(plan, request)
    _require_executable_view_plan(validation, assessment)
    output = _validate_drawing_output_path(output_path, require_existing=True)
    response = _execute(
        "verify_committed_part_drawing_view_plan",
        {"plan": normalized, "output_path": output},
        mutating=False,
    )
    return _semantic_response_with_plan_binding(response, normalized, request)


def _validate_view_plan(plan: ViewPlan, request: PlanningRequest):
    candidate = plan.root if isinstance(plan, ViewPlan) else plan
    normalized = json_object_copy(candidate, "view plan")
    validation = RepositoryViewPlanValidator().validate(normalized, request)
    assessment = current_registry().assess(normalized) if validation.passed else None
    return normalized, validation, assessment


def _require_executable_view_plan(validation, assessment) -> None:
    if not validation.passed:
        details = "; ".join(
            f"{issue.code}@{issue.json_pointer or '/'}: {issue.message}"
            for issue in validation.issues
        )
        raise ToolError(
            "VIEW_PLAN_DETERMINISTIC_VALIDATION_FAILED: "
            + (details or "one or more repository validation gates failed")
        )
    if assessment is None or assessment.status != "supported":
        blocked = ",".join(
            assessment.unsupported_capabilities if assessment is not None else ()
        )
        raise ToolError(
            "VIEW_PLAN_CAPABILITY_BLOCKED: "
            + (blocked or "the current executor cannot verify every required capability")
        )


def _validate_drawing_output_path(value: str, *, require_existing: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("output_path must be a non-empty absolute .SLDDRW path")
    if any(character in value for character in ("*", "?", "[", "]")):
        raise ValueError("output_path must not contain wildcard characters")
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".slddrw":
        raise ValueError("output_path must be an absolute .SLDDRW path")
    normalized = path.resolve()
    if not normalized.parent.is_dir():
        raise ValueError("output_path parent directory must already exist")
    report = Path(str(normalized) + ".verification.json")
    if require_existing:
        if not normalized.is_file():
            raise ValueError("output_path must reference an existing .SLDDRW file")
        if not report.is_file():
            raise ValueError("the drawing verification sidecar does not exist")
    elif normalized.exists() or report.exists():
        raise ValueError("output_path and its verification sidecar must both be new")
    return str(normalized)


def _dimension_request_sha256(request: DimensionPlanningRequest) -> str:
    return canonical_json_sha256(
        request.model_dump(mode="json"), "dimension planning request"
    )


def _dimension_plan_binding(
    plan: dict, request: DimensionPlanningRequest
) -> tuple[str, str]:
    plan_path = Path(request.publication_directory).resolve() / "dimension_plan.json"
    if not plan_path.is_file():
        raise ToolError(
            "DIMENSION_PLAN_NOT_PUBLISHED: immutable dimension_plan.json is missing"
        )
    try:
        disk_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ToolError(f"DIMENSION_PLAN_PUBLICATION_INVALID: {exc}") from exc
    if disk_plan != plan:
        raise ToolError(
            "DIMENSION_PLAN_PUBLICATION_MISMATCH: structured plan differs from dimension_plan.json"
        )
    return str(plan_path), file_sha256(plan_path)


def _validate_dimension_output_path(value: str, *, require_existing: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("output_path must be a non-empty absolute .SLDDRW path")
    if any(character in value for character in ("*", "?", "[", "]")):
        raise ValueError("output_path must not contain wildcard characters")
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".slddrw":
        raise ValueError("output_path must be an absolute .SLDDRW path")
    normalized = path.resolve()
    if not normalized.parent.is_dir():
        raise ValueError("output_path parent directory must already exist")
    report = Path(str(normalized) + ".dimension-verification.json")
    if require_existing:
        if not normalized.is_file() or not report.is_file():
            raise ValueError(
                "output_path and its dimension verification sidecar must both exist"
            )
    elif normalized.exists() or report.exists():
        raise ValueError("output_path and its dimension verification sidecar must both be new")
    return str(normalized)


def _validate_dimension_plan(plan: DimensionPlan, request: DimensionPlanningRequest):
    candidate = plan.root if isinstance(plan, DimensionPlan) else plan
    normalized = json_object_copy(candidate, "dimension plan")
    validator = RepositoryDimensionPlanValidator()
    validation = validator.validate(normalized, request)
    assessment = (
        current_dimension_registry().assess(normalized)
        if validation.engineering_passed
        else None
    )
    return normalized, validation, assessment


def _require_executable_dimension_plan(validation, assessment) -> None:
    if not validation.engineering_passed:
        details = "; ".join(
            f"{issue.code}@{issue.json_pointer or '/'}: {issue.message}"
            for issue in validation.issues
            if issue.gate != "capability"
        )
        raise ToolError(
            "DIMENSION_PLAN_DETERMINISTIC_VALIDATION_FAILED: "
            + (details or "one or more repository validation gates failed")
        )
    if assessment is None or assessment.status != "supported":
        blocked = ",".join(
            assessment.unsupported_capabilities if assessment is not None else ()
        )
        raise ToolError(
            "DIMENSION_PLAN_CAPABILITY_BLOCKED: "
            + (blocked or "the current executor lacks live persisted evidence")
        )


def _require_qualification_dimension_plan(plan, validation) -> None:
    if not validation.engineering_passed:
        details = "; ".join(
            f"{issue.code}@{issue.json_pointer or '/'}: {issue.message}"
            for issue in validation.issues
            if issue.gate != "capability"
        )
        raise ToolError(
            "DIMENSION_PLAN_DETERMINISTIC_VALIDATION_FAILED: "
            + (details or "one or more repository validation gates failed")
        )
    try:
        current_dimension_registry().require_qualification_eligible(plan)
    except ValueError as exc:
        raise ToolError(f"DIMENSION_F7_QUALIFICATION_BLOCKED: {exc}") from exc


def _dimension_f7_case_binding(
    *,
    matrix_request_path: str,
    matrix_request_sha256: str,
    case_id: str,
    plan: dict,
    request: DimensionPlanningRequest,
    output_path: str,
    require_existing_output: bool,
) -> tuple[dict, str]:
    path = Path(matrix_request_path)
    if not path.is_absolute() or path.suffix.lower() != ".json" or not path.is_file():
        raise ToolError("DIMENSION_F7_MATRIX_INVALID: matrix request must be an existing absolute JSON file")
    path = path.resolve()
    if file_sha256(path) != matrix_request_sha256:
        raise ToolError("DIMENSION_F7_MATRIX_HASH_MISMATCH: matrix request SHA-256 changed")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        # The immutable request is published only after the initial all-new-path gate. During a
        # sequential run, earlier cases legitimately have committed outputs while the current
        # create target must still be new. Revalidate all frozen inputs/hashes without imposing
        # one global output state, then apply the exact target state below.
        matrix = validate_f7_matrix_request_for_evaluation(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ToolError(f"DIMENSION_F7_MATRIX_INVALID: {exc}") from exc
    matches = [case for case in matrix["cases"] if case["case_id"] == case_id]
    if len(matches) != 1:
        raise ToolError("DIMENSION_F7_CASE_MISSING: case_id is not unique in the matrix")
    case = matches[0]
    request_hash = _dimension_request_sha256(request)
    plan_hash = canonical_json_sha256(plan, "dimension plan")
    normalized_output = _validate_dimension_output_path(
        output_path, require_existing=require_existing_output
    )
    if (
        case["planning_request"] != request.model_dump(mode="json")
        or case["planning_request_sha256"] != request_hash
        or case["plan_canonical_sha256"] != plan_hash
        or Path(case["output_path"]).resolve() != Path(normalized_output)
    ):
        raise ToolError(
            "DIMENSION_F7_CASE_BINDING_MISMATCH: plan, request, or output differs from the immutable matrix case"
        )
    return case, str(path)


def _dimension_semantic_response_with_binding(
    response: dict, plan: dict, request: DimensionPlanningRequest
) -> str:
    payload = json.loads(_semantic_response(response))
    payload["planning_request_sha256"] = _dimension_request_sha256(request)
    payload["plan_canonical_sha256"] = canonical_json_sha256(plan, "dimension plan")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(
    description=(
        "Initialize the immutable dimension-planning handoff from one verified ViewPlan 1.4 "
        "drawing and optional explicitly approved user inputs. The repository validates all "
        "upstream hashes, opens the drawing read-only, extracts native model dimensions, PMI, "
        "features, projected persistent references and reference-only measurements, then "
        "publishes dimension-planning-handoff.json last. No source document is saved."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def initialize_part_drawing_dimension_handoff(
    view_plan_path: str,
    verified_drawing_path: str,
    verification_sidecar_path: str,
    publication_directory: str,
    approved_user_inputs: tuple[ApprovedDimensionInput, ...] = (),
) -> str:
    request_payload = build_handoff_request(
        Path(view_plan_path),
        Path(verified_drawing_path),
        Path(verification_sidecar_path),
        Path(publication_directory),
        approved_user_inputs=tuple(
            item.model_dump(mode="json") for item in approved_user_inputs
        ),
    )
    response = create_dimension_planning_handoff(request_payload)
    payload = dict(response)
    payload["ok"] = payload.get("status") == "ready"
    if not payload["ok"]:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    handoff_path = payload.get("handoff_path")
    handoff_hash = payload.get("handoff_sha256")
    if not isinstance(handoff_path, str) or not isinstance(handoff_hash, str):
        raise ToolError("DIMENSION_HANDOFF_RESULT_INVALID: path/hash is missing")
    handoff = validate_dimension_planning_handoff(
        json.loads(Path(handoff_path).read_text(encoding="utf-8"))
    )
    if file_sha256(Path(handoff_path)) != handoff_hash:
        raise ToolError("DIMENSION_HANDOFF_RESULT_INVALID: published hash mismatch")
    planning_request = DimensionPlanningRequest(
        handoff_path=handoff_path,
        handoff_sha256=handoff_hash,
        planner_profile="production",
        publication_directory=str(Path(publication_directory).resolve()),
        user_requirements={"source_drawing_read_only": True},
    )
    payload["handoff_id"] = handoff["handoff_id"]
    payload["handoff_integrity"] = "pass"
    payload["planning_request"] = planning_request.model_dump(mode="json")
    payload["planning_request_sha256"] = _dimension_request_sha256(planning_request)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(
    description=(
        "Revalidate and atomically publish exactly one complete DimensionPlan 1.0 candidate "
        "against its original immutable DimensionPlanningRequest. Applies integrity, exact "
        "Schema, trusted-source, attachment, engineering semantics, coverage, redundancy, "
        "layout and capability gates, then creates dimension_plan.json without overwrite. "
        "A valid capability_blocked plan is retained without downgrade and cannot execute."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def publish_validated_part_drawing_dimension_plan(
    plan: DimensionPlan, request: DimensionPlanningRequest
) -> str:
    candidate = plan.root if isinstance(plan, DimensionPlan) else plan
    try:
        result = DimensionPlannerEngine().validate_and_publish(candidate, request)
    except FileExistsError as exc:
        raise ToolError(f"DIMENSION_PLAN_ALREADY_EXISTS: {exc}") from exc
    payload = result.model_dump(mode="json")
    payload["ok"] = result.status == "published"
    payload["planning_request_sha256"] = result.audit.request_sha256
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(
    description=(
        "Validate one published immutable DimensionPlan 1.0 and its unchanged original request "
        "for a new output path. Re-runs all Python engineering and capability gates, verifies "
        "the on-disk dimension_plan.json binding, then calls the independent COM-free C# "
        "compiler, handoff resolver, trusted-input checks and capability preflight."
    ),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def validate_part_drawing_dimension_plan(
    plan: DimensionPlan,
    request: DimensionPlanningRequest,
    output_path: str,
) -> str:
    normalized, validation, assessment = _validate_dimension_plan(plan, request)
    payload = {
        "ok": validation.engineering_passed,
        "status": "VALID" if validation.engineering_passed else "REJECTED",
        "planning_request_sha256": _dimension_request_sha256(request),
        "plan_canonical_sha256": canonical_json_sha256(normalized, "dimension plan"),
        "validation": validation.model_dump(mode="json"),
        "execution_readiness": assessment.status if assessment else "not_assessed",
        "unsupported_capabilities": list(
            assessment.unsupported_capabilities if assessment else ()
        ),
    }
    if validation.engineering_passed:
        plan_path, plan_sha256 = _dimension_plan_binding(normalized, request)
        output = _validate_dimension_output_path(output_path, require_existing=False)
        executor = _execute(
            "validate_frozen_part_drawing_dimension_plan",
            {
                "plan": normalized,
                "plan_path": plan_path,
                "plan_sha256": plan_sha256,
                "output_path": output,
            },
            mutating=False,
        )
        payload["executor"] = json.loads(_semantic_response(executor))
        payload["ok"] = bool(payload["executor"].get("ok"))
        if not payload["ok"]:
            payload["status"] = "EXECUTOR_REJECTED"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(
    description=(
        "F7 qualification-only native creation for one immutable matrix-bound DimensionPlan. "
        "Allows planned capabilities solely to collect live evidence, rejects known-unsupported "
        "capabilities, and never changes production capability state. The matrix request, case, "
        "plan, original request and new output path are re-hashed before the C# transaction."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def qualify_dimensioned_part_drawing(
    plan: DimensionPlan,
    request: DimensionPlanningRequest,
    output_path: str,
    matrix_request_path: str,
    matrix_request_sha256: str,
    case_id: str,
) -> str:
    normalized, validation, _ = _validate_dimension_plan(plan, request)
    _require_qualification_dimension_plan(normalized, validation)
    case, matrix_path = _dimension_f7_case_binding(
        matrix_request_path=matrix_request_path,
        matrix_request_sha256=matrix_request_sha256,
        case_id=case_id,
        plan=normalized,
        request=request,
        output_path=output_path,
        require_existing_output=False,
    )
    plan_path, plan_sha256 = _dimension_plan_binding(normalized, request)
    response = _execute(
        "qualify_part_drawing_dimension_plan",
        {
            "plan": normalized,
            "plan_path": plan_path,
            "plan_sha256": plan_sha256,
            "output_path": case["output_path"],
            "matrix_request_path": matrix_path,
            "matrix_request_sha256": matrix_request_sha256,
            "planning_request_sha256": case["planning_request_sha256"],
            "case_id": case_id,
        },
        mutating=True,
    )
    return _dimension_semantic_response_with_binding(response, normalized, request)


@mcp.tool(
    description=(
        "Independently read-only verify an F7 qualification drawing against its immutable "
        "matrix-bound DimensionPlan. This qualification-only verifier accepts planned "
        "capabilities but rejects known-unsupported ones and does not promote the capability "
        "manifest; promotion requires a complete six-category evidence summary."
    ),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def verify_qualified_dimensioned_part_drawing(
    plan: DimensionPlan,
    request: DimensionPlanningRequest,
    output_path: str,
    matrix_request_path: str,
    matrix_request_sha256: str,
    case_id: str,
) -> str:
    normalized, validation, _ = _validate_dimension_plan(plan, request)
    _require_qualification_dimension_plan(normalized, validation)
    case, matrix_path = _dimension_f7_case_binding(
        matrix_request_path=matrix_request_path,
        matrix_request_sha256=matrix_request_sha256,
        case_id=case_id,
        plan=normalized,
        request=request,
        output_path=output_path,
        require_existing_output=True,
    )
    plan_path, plan_sha256 = _dimension_plan_binding(normalized, request)
    response = _execute(
        "verify_qualified_part_drawing_dimension_plan",
        {
            "plan": normalized,
            "plan_path": plan_path,
            "plan_sha256": plan_sha256,
            "output_path": case["output_path"],
            "matrix_request_path": matrix_path,
            "matrix_request_sha256": matrix_request_sha256,
            "planning_request_sha256": case["planning_request_sha256"],
            "case_id": case_id,
        },
        mutating=False,
    )
    return _dimension_semantic_response_with_binding(response, normalized, request)


@mcp.tool(
    description=(
        "Transactionally create one new dimensioned .SLDDRW from a published immutable "
        "DimensionPlan 1.0. Requires every planned capability to have live evidence, re-hashes "
        "all frozen inputs, copies the verified upstream drawing, creates only planned native "
        "dimensions, verifies in memory and after read-only reopen, and atomically commits the "
        "new drawing plus dimension verification sidecar without modifying upstream artifacts."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def create_dimensioned_part_drawing(
    plan: DimensionPlan,
    request: DimensionPlanningRequest,
    output_path: str,
) -> str:
    normalized, validation, assessment = _validate_dimension_plan(plan, request)
    _require_executable_dimension_plan(validation, assessment)
    plan_path, plan_sha256 = _dimension_plan_binding(normalized, request)
    output = _validate_dimension_output_path(output_path, require_existing=False)
    response = _execute(
        "execute_part_drawing_dimension_plan",
        {
            "plan": normalized,
            "plan_path": plan_path,
            "plan_sha256": plan_sha256,
            "output_path": output,
        },
        mutating=True,
    )
    return _dimension_semantic_response_with_binding(response, normalized, request)


@mcp.tool(
    description=(
        "Independently verify an existing dimensioned drawing against the same published "
        "DimensionPlan 1.0 and original request. Re-runs deterministic and capability gates, "
        "validates the immutable plan, audit sidecar, frozen inputs and drawing hash before COM, "
        "then opens the drawing read-only and rechecks native identity, attachments, values, "
        "format, tolerance, hole variables and persistence fingerprints without saving."
    ),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def verify_dimensioned_part_drawing(
    plan: DimensionPlan,
    request: DimensionPlanningRequest,
    output_path: str,
) -> str:
    normalized, validation, assessment = _validate_dimension_plan(plan, request)
    _require_executable_dimension_plan(validation, assessment)
    plan_path, plan_sha256 = _dimension_plan_binding(normalized, request)
    output = _validate_dimension_output_path(output_path, require_existing=True)
    response = _execute(
        "verify_committed_part_drawing_dimension_plan",
        {
            "plan": normalized,
            "plan_path": plan_path,
            "plan_sha256": plan_sha256,
            "output_path": output,
        },
        mutating=False,
    )
    return _dimension_semantic_response_with_binding(response, normalized, request)


if __name__ == "__main__":
    mcp.run()
