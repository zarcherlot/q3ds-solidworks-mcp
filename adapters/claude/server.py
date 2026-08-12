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
from drawing_planner.feature_taxonomy import load_feature_taxonomy
from drawing_planner.semantic_features import load_model_semantic_features
from drawing_planner.planning_prompt_compiler import (
    PlannerProfileUnavailable,
    RepositoryPlanningPromptCompiler,
)
from drawing_planner.validators import HandoffIntegrityValidator, RepositoryViewPlanValidator
from execution_client import bootstrap_host, call_tool, ensure_ready, get_health, get_state
from planning_sampling import McpSamplingPlanningModelGateway
from config import SAMPLING_FALLBACK
from sampling_fallback import build_sampling_fallback_handler
from semantic_models import (
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
    "ViewPlan 1.4 is the default and only part-drawing protocol on this MCP surface. Use "
    "initialize_part_drawing_handoff to generate a verified, immutable initializer handoff, then "
    "either plan_part_drawing_views for MCP Sampling or the explicit upper-layer Skill followed "
    "by publish_validated_part_drawing_view_plan; both publish a frozen ViewPlan only after the "
    "same deterministic validation. capability_blocked is valid planning output but cannot "
    "be executed. Validate, create, and verify drawings only through the repository ViewPlan "
    "semantic tools with the original PlanningRequest. DrawingPlan 1.0 is not available on the "
    "default surface. Never convert between protocols, repair a frozen plan, or expose private COM "
    "operations."
)

_sampling_fallback_handler = build_sampling_fallback_handler(SAMPLING_FALLBACK)

mcp = FastMCP(
    "Q3DS SolidWorks Engineering",
    instructions=MCP_INSTRUCTIONS,
    version="2.2.0",
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


@mcp.tool(
    description=(
        "Report the local execution/SolidWorks readiness and authoritative state version. "
        "Set launch_if_needed=true only when a SolidWorks session should be started."
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
    health = ensure_ready() if launch_if_needed else get_health()
    with _state_lock:
        returned_state = health.get("stateVersion")
        _state_version = int(returned_state if returned_state is not None else get_state())
    return json.dumps(
        {
            "ok": health.get("status") == "UP",
            "com_attached": bool(health.get("comAttached")),
            "solidworks_launched": bool(health.get("swLaunched", False)),
            "solidworks_version": health.get("swVersion"),
            "active_document": health.get("activeDocument"),
            "state_version": _state_version,
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
        "must be new; the previous active document is restored and Python performs no COM calls. "
        "The optional semantic_feature_profile=m1-experimental adds a hash-bound semantic artifact; "
        "that artifact remains incomplete until typed FeatureData/PMI evidence is available."
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
    semantic_feature_profile: Annotated[str, Field(pattern=r"^(none|m1-experimental)$")] = "none",
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
    if semantic_feature_profile == "m1-experimental":
        expected.extend([
            "model-semantic-features.json",
            "mechanical-features-1.0.0-experimental.json",
        ])
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
            "semantic_feature_profile": semantic_feature_profile,
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
    if semantic_feature_profile == "m1-experimental":
        assert integrity.manifest is not None
        semantic_binding = integrity.manifest.get("semantic_features")
        taxonomy_binding = integrity.manifest.get("semantic_taxonomy")
        if not isinstance(semantic_binding, dict) or not isinstance(taxonomy_binding, dict):
            raise ToolError("INITIALIZER_SEMANTIC_BINDING_MISSING: experimental artifacts are absent")
        taxonomy = load_feature_taxonomy(Path(taxonomy_binding["path"]))
        semantic = load_model_semantic_features(
            Path(semantic_binding["path"]), taxonomy=taxonomy
        )
        payload["semantic_features"] = {
            "path": semantic_binding["path"],
            "sha256": semantic_binding["sha256"],
            "status": semantic.status,
            "open_question_count": len(semantic.open_questions),
        }
    payload["handoff_integrity"] = "pass"
    payload["planning_request"] = request.model_dump(mode="json")
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
    request_sha256 = canonical_json_sha256(
        request.model_dump(mode="json"), "planning request"
    )
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
    return _semantic_response(response)


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
    return _semantic_response(response)


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


if __name__ == "__main__":
    mcp.run()
