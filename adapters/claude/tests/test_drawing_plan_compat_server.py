import asyncio
import json
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch


_HERE = Path(__file__).resolve().parent
_ADAPTER_DIR = _HERE.parent
_ROOT = _ADAPTER_DIR.parents[1]
_CONTRACT = _ADAPTER_DIR / "contracts" / "drawing-plan-compat-tools.schema.json"
sys.path.insert(0, str(_ADAPTER_DIR))

import drawing_plan_compat_server as compat  # noqa: E402
import server as default_server  # noqa: E402
from semantic_models import DrawingPlan  # noqa: E402


_TOOL_NAMES = {
    "validate_part_drawing_plan",
    "create_part_drawing",
    "verify_part_drawing",
}


def _plan(tmp_path: Path) -> DrawingPlan:
    model = tmp_path / "part.SLDPRT"
    template = tmp_path / "sheet.DRWDOT"
    model.write_bytes(b"part")
    template.write_bytes(b"template")
    return DrawingPlan.model_validate(
        {
            "schema_version": "1.0",
            "model": {"path": str(model.resolve())},
            "drawing": {
                "template_path": str(template.resolve()),
                "output_path": str((tmp_path / "output.SLDDRW").resolve()),
            },
            "sheet": {},
            "views": [
                {
                    "id": "front",
                    "kind": "base",
                    "orientation": "front",
                    "position": {"x": 0.1, "y": 0.1},
                    "scale_mode": "sheet",
                }
            ],
        }
    )


def _completed(state: int = 0) -> dict:
    return {
        "status": "COMPLETED",
        "verified": True,
        "stateVersion": state,
        "result_geometry": {"verified": True},
    }


def test_compatibility_surface_matches_its_machine_contract():
    tools = asyncio.run(compat.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    assert set(by_name) == _TOOL_NAMES == set(contract["required"])
    assert set(contract["properties"]) == _TOOL_NAMES
    for tool in by_name.values():
        assert set(tool.parameters["properties"]) == {"plan"}
        assert set(tool.parameters["required"]) == {"plan"}
        plan = tool.parameters["properties"]["plan"]
        assert plan["type"] == "object"
        assert plan["additionalProperties"] is False
        assert plan["properties"]["schema_version"]["const"] == "1.0"
        assert plan["properties"]["views"]["minItems"] == 1
    assert compat.mcp.version == "1.0.0"
    assert asyncio.run(compat.mcp.list_prompts()) == []


def test_default_surface_and_codex_auto_registration_exclude_compatibility_tools():
    default_names = {tool.name for tool in asyncio.run(default_server.mcp.list_tools())}
    assert not (default_names & _TOOL_NAMES)
    assert all(not hasattr(default_server, name) for name in _TOOL_NAMES)
    with (_ROOT / ".codex" / "config.toml").open("rb") as handle:
        enabled = set(tomllib.load(handle)["mcp_servers"]["solidpilot"]["enabled_tools"])
    assert not (enabled & _TOOL_NAMES)
    launcher = (_ROOT / "scripts" / "start_drawing_plan_compat_mcp.ps1").read_text(
        encoding="utf-8"
    )
    assert "drawing_plan_compat_server.py" in launcher


def test_validate_returns_canonical_native_plan(tmp_path):
    plan = _plan(tmp_path)
    result = json.loads(compat.validate_part_drawing_plan(plan))
    assert result["ok"] is True
    assert result["schema_version"] == "1.0"
    assert result["plan_sha256"] == plan.sha256()
    assert result["normalized_plan"] == plan.execution_dict()


def test_create_routes_only_to_private_native_transaction(tmp_path):
    plan = _plan(tmp_path)
    with patch.object(compat, "_execute", return_value=_completed(1)) as execute:
        result = json.loads(compat.create_part_drawing(plan))
    assert result["ok"] is True
    execute.assert_called_once_with(
        "execute_drawing_plan",
        {"plan": plan.canonical_json()},
        mutating=True,
    )


def test_verify_routes_only_to_private_read_only_transaction(tmp_path):
    plan = _plan(tmp_path)
    with patch.object(compat, "_execute", return_value=_completed()) as execute:
        result = json.loads(compat.verify_part_drawing(plan))
    assert result["ok"] is True
    execute.assert_called_once_with(
        "verify_drawing_plan",
        {"plan": plan.canonical_json()},
        mutating=False,
    )


def test_compatibility_process_resyncs_independent_state_before_mutation():
    mismatch = {
        "status": "FAILED",
        "error": {"code": "INVALID_STATE_VERSION", "message": "resync"},
    }
    compat._state_version = 0
    with patch.object(
        compat, "call_tool", side_effect=(mismatch, _completed(8))
    ) as call_tool, patch.object(compat, "get_state", return_value=7):
        result = compat._execute("execute_drawing_plan", {"plan": "{}"}, mutating=True)
    assert result["status"] == "COMPLETED"
    assert compat._state_version == 8
    assert call_tool.call_args_list[0].args[2] == 0
    assert call_tool.call_args_list[1].args[2] == 7
