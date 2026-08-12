"""Explicit DrawingPlan 1.0 compatibility MCP entry point.

This server is intentionally separate from the default ViewPlan 1.4 MCP. It accepts only the
native structured DrawingPlan 1.0 contract and routes it to the existing private C# transaction.
"""

from __future__ import annotations

import json
import threading
import uuid

from fastmcp import FastMCP

from execution_client import call_tool, get_state
from semantic_models import DrawingPlan


MCP_INSTRUCTIONS = (
    "Explicit compatibility endpoint for native DrawingPlan 1.0 callers only. Validate the same "
    "complete structured plan before creation and use it again for independent verification. "
    "This endpoint is not the default drawing workflow. Never submit ViewPlan 1.4, translate "
    "between protocols, or call private executor operations directly."
)

mcp = FastMCP(
    "Q3DS SolidWorks DrawingPlan 1.0 Compatibility",
    instructions=MCP_INSTRUCTIONS,
    version="1.0.0",
    strict_input_validation=True,
)

_state_version = 0
_state_lock = threading.RLock()


def _operation_id(prefix: str) -> str:
    return f"drawing-plan-v1-{prefix}-{uuid.uuid4()}"


def _state_mismatch(response: dict) -> bool:
    return (
        response.get("status") == "FAILED"
        and (response.get("error") or {}).get("code") == "INVALID_STATE_VERSION"
    )


def _execute(tool: str, params: dict, *, mutating: bool) -> dict:
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
        "ok": status in {"COMPLETED", "DUPLICATE"}
        and bool(response.get("verified", True)),
        "status": status,
        "verified": bool(response.get("verified", False)),
        "state_version": response.get(
            "stateVersion", response.get("last_known_state_version")
        ),
    }
    if response.get("result_geometry") is not None:
        payload["result"] = response["result_geometry"]
    error = response.get("error") or {}
    if error:
        payload["error"] = {
            "code": error.get("code"),
            "message": error.get("message"),
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@mcp.tool(
    description=(
        "Validate one complete native DrawingPlan 1.0 object without contacting SolidWorks. "
        "Returns its normalized form and canonical SHA-256. This compatibility tool does not "
        "accept or convert ViewPlan 1.4."
    ),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def validate_part_drawing_plan(plan: DrawingPlan) -> str:
    return json.dumps(
        {
            "ok": True,
            "schema_version": plan.schema_version,
            "plan_sha256": plan.sha256(),
            "normalized_plan": plan.execution_dict(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


@mcp.tool(
    description=(
        "Transactionally create one associated part drawing from a validated native DrawingPlan "
        "1.0 object. The private C# transaction saves, closes, reopens read-only, verifies, and "
        "atomically commits the drawing and verification sidecar."
    ),
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def create_part_drawing(plan: DrawingPlan) -> str:
    return _semantic_response(
        _execute(
            "execute_drawing_plan",
            {"plan": plan.canonical_json()},
            mutating=True,
        )
    )


@mcp.tool(
    description=(
        "Independently verify an existing drawing against the same native DrawingPlan 1.0 object "
        "through the private read-only C# verification transaction."
    ),
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def verify_part_drawing(plan: DrawingPlan) -> str:
    return _semantic_response(
        _execute(
            "verify_drawing_plan",
            {"plan": plan.canonical_json()},
            mutating=False,
        )
    )


if __name__ == "__main__":
    mcp.run()
