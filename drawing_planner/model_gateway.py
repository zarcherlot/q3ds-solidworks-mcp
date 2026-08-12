"""Provider-neutral model boundary used only by the repository PlannerEngine."""

from __future__ import annotations

import asyncio
import math
from typing import Any, Awaitable, Callable, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from drawing_planner.planning_models import (
    CompiledPlanningPrompt,
    ModelPlanningResponse,
)


class PlanningModelGateway(Protocol):
    async def generate(
        self, prompt: CompiledPlanningPrompt
    ) -> ModelPlanningResponse:
        """Return one structured ViewPlan candidate; never execute MCP or COM tools."""
        ...


class PlanningModelUnavailable(RuntimeError):
    """Raised when the configured planner profile has no usable model runner."""


class PlanningModelResponseError(ValueError):
    """Raised when a model runner violates the structured candidate contract."""


class _RunnerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    response_id: str | None = Field(default=None, max_length=256)
    plan: dict[str, Any]


StructuredPlanningRunner = Callable[
    [CompiledPlanningPrompt], Awaitable[Mapping[str, Any]]
]


class CallablePlanningModelGateway:
    """Pin provider identity and enforce a strict async structured-output boundary."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        runner: StructuredPlanningRunner,
        timeout_seconds: float = 180.0,
    ):
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("provider must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if not callable(runner):
            raise ValueError("runner must be callable")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        self._provider = provider.strip()
        self._model = model.strip()
        self._runner = runner
        self._timeout_seconds = float(timeout_seconds)

    async def generate(
        self, prompt: CompiledPlanningPrompt
    ) -> ModelPlanningResponse:
        timeout_seconds = (
            min(self._timeout_seconds, 45.0)
            if prompt.purpose == "debug_reference_selection"
            else self._timeout_seconds
        )
        try:
            raw = await asyncio.wait_for(
                self._runner(prompt), timeout=timeout_seconds
            )
        except TimeoutError as exc:
            raise PlanningModelUnavailable(
                f"planning model timed out after {timeout_seconds:g} seconds"
            ) from exc
        try:
            parsed = _RunnerResponse.model_validate(raw)
            return ModelPlanningResponse(
                provider=self._provider,
                model=self._model,
                response_id=parsed.response_id,
                plan=parsed.plan,
            )
        except ValidationError as exc:
            raise PlanningModelResponseError(
                "planning model returned an invalid structured candidate"
            ) from exc
