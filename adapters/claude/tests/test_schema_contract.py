"""Contract test for the default, engineering-semantic MCP surface."""

import asyncio
import json
import os
import sys
import tomllib
from pathlib import Path


_HERE = os.path.dirname(os.path.abspath(__file__))
_ADAPTER_DIR = os.path.dirname(_HERE)
_CONTRACT = os.path.join(
    _ADAPTER_DIR, "contracts", "semantic-tools.schema.json"
)
sys.path.insert(0, _ADAPTER_DIR)

import server  # noqa: E402


def _adapter_tools():
    tools = asyncio.run(server.mcp.list_tools())
    return {
        tool.name: {
            "parameters": set((tool.parameters or {}).get("properties", {})),
            "required": set((tool.parameters or {}).get("required", [])),
            "description": tool.description or "",
        }
        for tool in tools
    }


def _contract_tools():
    with open(_CONTRACT, encoding="utf-8") as handle:
        raw = json.load(handle)
    required_tools = set(raw["required"])
    properties = raw["properties"]
    out = {}
    expected = {
        "solidworks_status": ({"launch_if_needed"}, set()),
        "inspect_solidworks_host": (
            {"output_directory", "drawing_template_path"},
            {"output_directory"},
        ),
        "bootstrap_solidworks_host": (
            {
                "output_directory",
                "drawing_template_path",
                "allow_registration_repair",
                "visible",
                "keep_solidworks_running",
                "com_timeout_seconds",
                "regserver_timeout_seconds",
            },
            {"output_directory"},
        ),
        "inspect_part_for_drawing": ({"model_path"}, {"model_path"}),
        "initialize_part_drawing_handoff": (
            {
                "model_path",
                "drawing_template_path",
                "publication_directory",
                "image_width",
                "image_height",
            },
            {"model_path", "drawing_template_path", "publication_directory"},
        ),
        "plan_part_drawing_views": ({"request"}, {"request"}),
        "publish_validated_part_drawing_view_plan": (
            {"plan", "request"},
            {"plan", "request"},
        ),
        "validate_part_drawing_view_plan": (
            {"plan", "request"},
            {"plan", "request"},
        ),
        "create_part_drawing_from_view_plan": (
            {"plan", "request", "output_path"},
            {"plan", "request", "output_path"},
        ),
        "verify_part_drawing_view_plan": (
            {"plan", "request", "output_path"},
            {"plan", "request", "output_path"},
        ),
        "initialize_part_drawing_dimension_handoff": (
            {
                "view_plan_path",
                "verified_drawing_path",
                "verification_sidecar_path",
                "publication_directory",
                "approved_user_inputs",
            },
            {
                "view_plan_path",
                "verified_drawing_path",
                "verification_sidecar_path",
                "publication_directory",
            },
        ),
        "publish_validated_part_drawing_dimension_plan": (
            {"plan", "request"},
            {"plan", "request"},
        ),
        "validate_part_drawing_dimension_plan": (
            {"plan", "request", "output_path"},
            {"plan", "request", "output_path"},
        ),
        "create_dimensioned_part_drawing": (
            {"plan", "request", "output_path"},
            {"plan", "request", "output_path"},
        ),
        "verify_dimensioned_part_drawing": (
            {"plan", "request", "output_path"},
            {"plan", "request", "output_path"},
        ),
    }
    for name in required_tools:
        parameters, required = expected[name]
        out[name] = {"parameters": parameters, "required": required}
        assert name in properties
    return out


def find_drift():
    adapter = _adapter_tools()
    contract = _contract_tools()
    errors = []
    for name in sorted(set(adapter) - set(contract)):
        errors.append(f"unexpected agent-facing tool: {name}")
    for name in sorted(set(contract) - set(adapter)):
        errors.append(f"contract tool is not exposed: {name}")
    for name in sorted(set(adapter) & set(contract)):
        for key in ("parameters", "required"):
            if adapter[name][key] != contract[name][key]:
                errors.append(
                    f"tool '{name}' {key}: adapter={sorted(adapter[name][key])} "
                    f"contract={sorted(contract[name][key])}"
                )
        if len(adapter[name]["description"].strip()) < 20:
            errors.append(f"tool '{name}' needs a substantive model-facing description")
    return errors


def test_schema_contract_in_sync():
    errors = find_drift()
    assert not errors, "semantic MCP contract drift:\n  - " + "\n  - ".join(errors)


def test_default_surface_contains_only_repository_view_and_dimension_protocols():
    tools = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in tools}
    legacy_names = {
        "validate_part_drawing_plan",
        "create_part_drawing",
        "verify_part_drawing",
    }
    assert not (names & legacy_names)
    assert all(not hasattr(server, name) for name in legacy_names)
    assert "ViewPlan 1.4 and DimensionPlan 1.0" in server.MCP_INSTRUCTIONS
    assert server.mcp.version == "2.3.0"


def test_host_bootstrap_tools_are_semantic_and_do_not_expose_cli_escape_hatches():
    tools = _adapter_tools()
    host_parameters = (
        tools["inspect_solidworks_host"]["parameters"]
        | tools["bootstrap_solidworks_host"]["parameters"]
    )
    assert not host_parameters.intersection(
        {"executable", "arguments", "command", "lock_file", "helper_path", "regserver"}
    )


def test_default_tool_contract_has_no_drawing_plan_1_0_reference():
    with open(_CONTRACT, encoding="utf-8") as handle:
        contract = json.load(handle)
    serialized = json.dumps(contract, sort_keys=True)
    assert "drawing-plan-1.0" not in serialized
    assert "planTool" not in contract["$defs"]


def test_planner_tool_publishes_the_structured_planning_contract():
    tools = asyncio.run(server.mcp.list_tools())
    request = {
        tool.name: tool for tool in tools
    }["plan_part_drawing_views"].parameters["properties"]["request"]
    assert request["type"] == "object"
    assert request["additionalProperties"] is False
    assert set(request["required"]) == {
        "handoff_manifest_path",
        "handoff_manifest_sha256",
        "publication_directory",
    }
    assert request["properties"]["planner_profile"]["default"] == "production"


def test_viewplan_tools_publish_the_exact_structured_viewplan_contract():
    tools = asyncio.run(server.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    for name in (
        "publish_validated_part_drawing_view_plan",
        "validate_part_drawing_view_plan",
        "create_part_drawing_from_view_plan",
        "verify_part_drawing_view_plan",
    ):
        plan = by_name[name].parameters["properties"]["plan"]
        assert plan["type"] == "object"
        assert plan["additionalProperties"] is False
        assert plan["properties"]["protocol_id"]["const"] == "solidworks-view-plan"
        assert plan["properties"]["schema_version"]["const"] == "1.4"
        assert plan["properties"]["views"]["minItems"] == 1
        assert "model_path" in plan["required"]


def test_dimension_tools_publish_the_exact_structured_dimension_contract():
    tools = asyncio.run(server.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    for name in (
        "publish_validated_part_drawing_dimension_plan",
        "validate_part_drawing_dimension_plan",
        "create_dimensioned_part_drawing",
        "verify_dimensioned_part_drawing",
    ):
        plan = by_name[name].parameters["properties"]["plan"]
        assert plan["type"] == "object"
        assert plan["additionalProperties"] is False
        assert plan["properties"]["protocol_id"]["const"] == "solidworks-dimension-plan"
        assert plan["properties"]["schema_version"]["const"] == "1.0"
        assert plan["properties"]["dimensions"]["minItems"] == 1
        assert "handoff_id" in plan["required"]

        request = by_name[name].parameters["properties"]["request"]
        assert request["type"] == "object"
        assert request["additionalProperties"] is False
        assert set(request["required"]) == {
            "handoff_path",
            "handoff_sha256",
            "publication_directory",
        }


def test_default_server_publishes_no_external_skill_invocation_prompts():
    prompts = asyncio.run(server.mcp.list_prompts())
    assert prompts == []


def test_codex_allow_list_matches_the_shared_default_surface():
    config_path = Path(_ADAPTER_DIR).parents[1] / ".codex" / "config.toml"
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    enabled = set(config["mcp_servers"]["solidpilot"]["enabled_tools"])
    assert enabled == set(_adapter_tools())


if __name__ == "__main__":
    drift = find_drift()
    if drift:
        print("SEMANTIC MCP CONTRACT DRIFT:")
        for item in drift:
            print("  -", item)
        raise SystemExit(1)
    print(f"OK - {len(_adapter_tools())} semantic MCP tools in sync")
