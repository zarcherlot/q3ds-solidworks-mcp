"""Repository-owned orchestration boundary for model-assisted ViewPlan generation."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from drawing_planner.capability_registry import CapabilityRegistry
from drawing_planner.model_gateway import PlanningModelGateway
from drawing_planner.plan_store import PlanStore
from drawing_planner.planning_models import (
    CompiledPlanningPrompt,
    ModelPlanningResponse,
    PlanningRequest,
    PlanningAudit,
    PlanningResult,
    PlanningValidation,
    PromptProvenance,
    canonical_json_sha256,
    json_object_copy,
)
from drawing_planner.validators.integrity import (
    HandoffIntegrityValidator,
    IntegrityValidationResult,
)


class PlanningPromptCompiler(Protocol):
    def compile(
        self,
        request: PlanningRequest,
        *,
        debug_reference_selection: Mapping[str, Any] | None = None,
    ) -> CompiledPlanningPrompt:
        ...

    def compile_reference_selection(
        self, request: PlanningRequest
    ) -> CompiledPlanningPrompt | None:
        ...


class ViewPlanValidator(Protocol):
    def validate(
        self, plan: dict, request: PlanningRequest
    ) -> PlanningValidation:
        ...


class PlanningInputValidator(Protocol):
    def validate(self, request: PlanningRequest) -> IntegrityValidationResult:
        ...


class PlannerEngine:
    """Generate, deterministically validate, assess, and atomically publish one plan."""

    def __init__(
        self,
        *,
        prompt_compiler: PlanningPromptCompiler,
        model_gateway: PlanningModelGateway,
        validator: ViewPlanValidator,
        capabilities: CapabilityRegistry,
        plan_store: PlanStore,
        input_validator: PlanningInputValidator | None = None,
    ):
        self._prompt_compiler = prompt_compiler
        self._model_gateway = model_gateway
        self._validator = validator
        self._capabilities = capabilities
        self._plan_store = plan_store
        self._input_validator = input_validator or HandoffIntegrityValidator()

    async def plan(self, request: PlanningRequest) -> PlanningResult:
        request_sha256 = canonical_json_sha256(
            request.model_dump(mode="json"), "planning request"
        )
        input_validation = self._input_validator.validate(request)
        if input_validation.status != "pass":
            return PlanningResult(
                status="rejected",
                execution_readiness="not_assessed",
                validation=PlanningValidation(
                    integrity="fail",
                    schema_check="not_run",
                    semantics="not_run",
                    coverage="not_run",
                    layout="not_run",
                    issues=input_validation.issues,
                ),
                plan=None,
                prompt_provenance=None,
                audit=PlanningAudit(request_sha256=request_sha256),
            )

        selection_prompt_compiler = getattr(
            self._prompt_compiler, "compile_reference_selection", None
        )
        selection: dict[str, Any] | None = None
        if callable(selection_prompt_compiler):
            selection_prompt = selection_prompt_compiler(request)
            if selection_prompt is not None:
                _validate_prompt_binding(selection_prompt, request)
                if selection_prompt.purpose != "debug_reference_selection":
                    raise ValueError(
                        "reference selector compiler returned the wrong prompt purpose"
                    )
                selection_response = await self._model_gateway.generate(selection_prompt)
                if not isinstance(selection_response, ModelPlanningResponse):
                    raise TypeError(
                        "planning model gateway must return ModelPlanningResponse"
                    )
                selection = json_object_copy(
                    selection_response.plan, "debug reference selection"
                )

        if selection is None:
            prompt = self._prompt_compiler.compile(request)
        else:
            prompt = self._prompt_compiler.compile(
                request, debug_reference_selection=selection
            )
        _validate_prompt_binding(prompt, request)
        if prompt.purpose != "view_plan":
            raise ValueError("final compiler returned the wrong prompt purpose")
        response = await self._model_gateway.generate(prompt)
        if not isinstance(response, ModelPlanningResponse):
            raise TypeError("planning model gateway must return ModelPlanningResponse")
        candidate = json_object_copy(response.plan, "model plan candidate")
        candidate_sha256 = canonical_json_sha256(candidate, "model plan candidate")
        provenance = PromptProvenance(
            planner_profile=prompt.planner_profile,
            core_policy_sha256=prompt.core_policy_sha256,
            prompt_pack_sha256=prompt.prompt_pack_sha256,
            schema_sha256=prompt.schema_sha256,
            input_manifest_sha256=prompt.input_manifest_sha256,
            envelope_sha256=prompt.envelope_sha256,
            provider=response.provider,
            model=response.model,
            response_id=response.response_id,
        )
        validation = self._validator.validate(candidate, request)
        if not validation.passed:
            return PlanningResult(
                status="rejected",
                execution_readiness="not_assessed",
                validation=validation,
                plan=None,
                prompt_provenance=provenance,
                audit=PlanningAudit(
                    request_sha256=request_sha256,
                    candidate_sha256=candidate_sha256,
                ),
            )

        assessment = self._capabilities.assess(candidate)
        published = self._plan_store.publish(
            candidate, request.publication_directory
        )
        return PlanningResult(
            status="published",
            execution_readiness=assessment.status,
            validation=validation,
            plan=published,
            prompt_provenance=provenance,
            audit=PlanningAudit(
                request_sha256=request_sha256,
                candidate_sha256=candidate_sha256,
                capability_manifest_version=assessment.manifest_version,
            ),
            unsupported_capabilities=assessment.unsupported_capabilities,
        )


def _validate_prompt_binding(
    prompt: CompiledPlanningPrompt, request: PlanningRequest
) -> None:
    if prompt.planner_profile != request.planner_profile:
        raise ValueError("compiled prompt changed the requested planner profile")
    if prompt.input_manifest_sha256 != request.handoff_manifest_sha256:
        raise ValueError("compiled prompt is not bound to the requested handoff manifest")
