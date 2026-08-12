"""MCP Sampling adapter for the repository-owned drawing PlannerEngine."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastmcp import Context
from fastmcp.server.sampling import SamplingTool
from mcp.types import ImageContent, SamplingMessage, TextContent

from drawing_planner.model_gateway import (
    PlanningModelResponseError,
    PlanningModelUnavailable,
)
from drawing_planner.planning_models import (
    CompiledPlanningPrompt,
    ModelPlanningResponse,
)


_PROVIDER = "mcp-sampling"
_SUBMISSIONS = {
    "view_plan": (
        "submit_solidworks_view_plan",
        "Submit the complete final solidworks-view-plan 1.4 candidate. "
        "This is the only accepted response; do not return prose.",
        "ViewPlan",
    ),
    "debug_reference_selection": (
        "submit_debug_reference_selection",
        "Submit only the reference-map category, feature, and deferred Markdown path "
        "selections. This is routing metadata, not a ViewPlan; do not return prose.",
        "debug reference selection",
    ),
}


class McpSamplingPlanningModelGateway:
    """Request one schema-constrained candidate from the connected MCP client."""

    def __init__(
        self,
        context: Context,
        *,
        timeout_seconds: float = 180.0,
        max_tokens: int = 32768,
    ):
        if not callable(getattr(context, "sample_step", None)):
            raise TypeError("context must provide the FastMCP sampling interface")
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        self._context = context
        self._timeout_seconds = float(timeout_seconds)
        self._max_tokens = max_tokens

    async def generate(
        self, prompt: CompiledPlanningPrompt
    ) -> ModelPlanningResponse:
        system_prompt, messages = _sampling_messages(prompt)
        tool_name, description, response_label = _SUBMISSIONS[prompt.purpose]
        timeout_seconds = (
            min(self._timeout_seconds, 45.0)
            if prompt.purpose == "debug_reference_selection"
            else self._timeout_seconds
        )
        max_tokens = (
            min(self._max_tokens, 4096)
            if prompt.purpose == "debug_reference_selection"
            else self._max_tokens
        )
        submit_tool = SamplingTool(
            name=tool_name,
            description=description,
            parameters=prompt.response_schema,
            fn=_unreachable_submit,
        )
        try:
            step = await asyncio.wait_for(
                self._context.sample_step(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=0.0,
                    max_tokens=max_tokens,
                    tools=[submit_tool],
                    tool_choice="required",
                    execute_tools=False,
                    mask_error_details=True,
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            raise PlanningModelUnavailable(
                f"MCP sampling timed out after {timeout_seconds:g} seconds"
            ) from exc
        except Exception as exc:
            raise PlanningModelUnavailable(
                f"connected MCP client could not provide planning sampling: {exc}"
            ) from exc

        calls = step.tool_calls
        if len(calls) != 1 or calls[0].name != tool_name:
            raise PlanningModelResponseError(
                f"planning sampling must return exactly one {response_label} submission"
            )
        candidate = calls[0].input
        if not isinstance(candidate, Mapping):
            raise PlanningModelResponseError(
                f"planning sampling returned a non-object {response_label}"
            )
        model = getattr(step.response, "model", None)
        if not isinstance(model, str) or not model.strip():
            raise PlanningModelResponseError(
                "planning sampling response did not identify the selected model"
            )
        response_id = calls[0].id
        if not isinstance(response_id, str) or len(response_id) > 256:
            response_id = None
        return ModelPlanningResponse(
            provider=_PROVIDER,
            model=model.strip(),
            response_id=response_id,
            plan=dict(candidate),
        )


def _sampling_messages(
    prompt: CompiledPlanningPrompt,
) -> tuple[str, list[SamplingMessage]]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    for message in prompt.messages:
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user"} or not isinstance(content, str):
            raise PlanningModelResponseError(
                "compiled planning prompt contains an unsupported message"
            )
        (system_parts if role == "system" else user_parts).append(content)
    if not system_parts or not user_parts:
        raise PlanningModelResponseError(
            "compiled planning prompt requires system and user messages"
        )

    blocks: list[TextContent | ImageContent] = [
        TextContent(type="text", text="\n\n".join(user_parts))
    ]
    for artifact in prompt.artifacts:
        payload = _read_verified_artifact(artifact.path, artifact.sha256)
        label = (
            f"kind={artifact.kind}; path={artifact.path}; "
            f"sha256={artifact.sha256}"
        )
        if artifact.media_type == "application/json":
            try:
                document = payload.decode("utf-8-sig")
            except UnicodeError as exc:
                raise PlanningModelUnavailable(
                    f"planning JSON artifact is not UTF-8: {artifact.path}"
                ) from exc
            blocks.append(
                TextContent(
                    type="text",
                    text=f"\n\nVerified planning artifact ({label}):\n{document}",
                )
            )
        elif artifact.media_type in {"image/png", "image/jpeg"}:
            image_label = (
                f"Verified {artifact.view} standard-view image"
                if artifact.kind == "standard_view_image"
                else "Verified selected debug reference image"
            )
            blocks.append(
                TextContent(
                    type="text",
                    text=f"\n\n{image_label} ({label}):",
                )
            )
            blocks.append(
                ImageContent(
                    type="image",
                    data=base64.b64encode(payload).decode("ascii"),
                    mimeType=artifact.media_type,
                )
            )
        else:
            raise PlanningModelResponseError(
                f"unsupported planning artifact media type: {artifact.media_type}"
            )
    return "\n\n".join(system_parts), [
        SamplingMessage(role="user", content=blocks)
    ]


def _read_verified_artifact(path: str, expected_sha256: str) -> bytes:
    try:
        payload = Path(path).read_bytes()
    except OSError as exc:
        raise PlanningModelUnavailable(
            f"planning artifact became unavailable before sampling: {path}"
        ) from exc
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise PlanningModelUnavailable(
            f"planning artifact changed after prompt compilation: {path}"
        )
    return payload


def _unreachable_submit(**_candidate: Any) -> None:
    raise RuntimeError("ViewPlan submission is captured without tool execution")
