from __future__ import annotations

import asyncio
import json
import re
import sys
import tomllib
from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[3]
_CONTRACT_PATH = (
    _ROOT / "adapters/claude/contracts/skill-chain.contract.json"
)
_DEFAULT_TOOL_SCHEMA = (
    _ROOT / "adapters/claude/contracts/semantic-tools.schema.json"
)
_SKILL_ROOT = _ROOT / ".codex/skills"
_FORBIDDEN_SKILL_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".ps1",
    ".py",
    ".vbs",
}


def _contract() -> dict:
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


def _front_matter(text: str) -> dict:
    match = re.match(r"\A---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert match is not None, "SKILL.md must start with YAML front matter"
    value = yaml.safe_load(match.group(1))
    assert isinstance(value, dict)
    return value


def _allowed_tools(text: str) -> list[str]:
    match = re.search(
        r"^## Allowed semantic tools\n\n(?P<body>.*?)(?=^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "SKILL.md must declare Allowed semantic tools"
    tools = re.findall(r"^- `([a-z0-9_]+)`$", match.group("body"), re.MULTILINE)
    assert tools, "Allowed semantic tools must be a backtick bullet list"
    return tools


def test_five_skill_order_front_matter_allow_lists_and_metadata_are_frozen():
    contract = _contract()
    stages = contract["stages"]
    assert [stage["order"] for stage in stages] == [1, 2, 3, 4, 5]
    assert [stage["skill"] for stage in stages] == [
        "bootstrap-solidworks-host",
        "solidworks-initialize-drawing-handoff",
        "solidworks-create-drawing-views",
        "solidworks-dimension-drawing",
        "solidworks-finalize-drawing-layout",
    ]

    for stage in stages:
        skill_path = _ROOT / stage["path"]
        assert skill_path == _SKILL_ROOT / stage["skill"] / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        front_matter = _front_matter(text)
        assert set(front_matter) == {"name", "description"}
        assert front_matter["name"] == stage["skill"]
        assert len(front_matter["description"].strip()) >= 80
        assert _allowed_tools(text) == stage["allowed_tools"]

        metadata_path = skill_path.parent / "agents/openai.yaml"
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        prompt = metadata["interface"]["default_prompt"]
        assert f"${stage['skill']}" in prompt


def test_skill_references_are_local_present_and_have_no_executable_escape_hatches():
    for stage in _contract()["stages"]:
        skill_path = _ROOT / stage["path"]
        skill_root = skill_path.parent.resolve()
        text = skill_path.read_text(encoding="utf-8")
        for relative in re.findall(r"\]\(([^)]+)\)", text):
            target = (skill_root / relative).resolve()
            assert target == skill_root or skill_root in target.parents
            assert target.is_file(), f"missing Skill reference: {relative}"

        for path in skill_root.rglob("*"):
            assert not path.is_symlink()
            if path.is_file():
                assert path.suffix.lower() not in _FORBIDDEN_SKILL_SUFFIXES


def test_contract_locks_default_tools_zero_prompts_and_codex_allow_list():
    contract = _contract()["default_mcp"]
    expected = contract["tools"]
    assert len(expected) == contract["tool_count"] == 24
    assert len(expected) == len(set(expected))
    assert contract["prompt_count"] == 0

    schema = json.loads(_DEFAULT_TOOL_SCHEMA.read_text(encoding="utf-8"))
    assert schema["required"] == expected
    assert list(schema["properties"]) == expected

    with (_ROOT / contract["config_path"]).open("rb") as handle:
        config = tomllib.load(handle)
    assert config["mcp_servers"]["solidpilot"]["enabled_tools"] == expected

    adapter_path = str(_ROOT / "adapters/claude")
    if adapter_path not in sys.path:
        sys.path.insert(0, adapter_path)
    from adapters.claude import server

    discovered = asyncio.run(server.mcp.list_tools())
    assert [tool.name for tool in discovered] == expected
    assert asyncio.run(server.mcp.list_prompts()) == []


def test_contract_freezes_branch_and_request_continuity_rules():
    contract = _contract()
    planning = contract["planning"]
    assert planning == {
        "default_branch": "explicit_skill_publish",
        "sampling_branch_requires_explicit_user_request": True,
        "branches_are_mutually_exclusive": True,
        "candidate_limit": 1,
        "publication_limit": 1,
        "published_plan_is_immutable": True,
        "capability_blocked_may_publish": True,
        "capability_blocked_may_execute": False,
    }
    continuity = contract["request_continuity"]
    assert continuity["must_be_unchanged"] is True
    assert continuity["canonical_sha256_field"] == "planning_request_sha256"
    assert continuity["operations"] == [
        "publish_validated_part_drawing_view_plan",
        "validate_part_drawing_view_plan",
        "create_part_drawing_from_view_plan",
        "verify_part_drawing_view_plan",
    ]
    dimension_continuity = contract["dimension_request_continuity"]
    assert dimension_continuity == {
        "source": "initialize_part_drawing_dimension_handoff.result.planning_request",
        "canonical_sha256_field": "planning_request_sha256",
        "must_be_unchanged": True,
        "operations": [
            "publish_validated_part_drawing_dimension_plan",
            "validate_part_drawing_dimension_plan",
            "create_dimensioned_part_drawing",
            "verify_dimensioned_part_drawing",
        ],
    }
    assert contract["dimension_qualification"] == {
        "scope": "f7_live_evidence_only",
        "matrix_bound": True,
        "allows_planned_capabilities": True,
        "allows_unsupported_capabilities": False,
        "mutates_capability_manifest": False,
        "operations": [
            "qualify_dimensioned_part_drawing",
            "verify_qualified_dimensioned_part_drawing",
        ],
    }
    assert contract["layout_request_continuity"] == {
        "source": "initialize_part_drawing_layout_handoff.result.planning_request_context",
        "predecessor_request_source": "initialize_part_drawing_dimension_handoff.result.planning_request",
        "predecessor_request_field": "source_dimension_request",
        "canonical_sha256_field": "planning_request_sha256",
        "predecessor_sha256_field": "source_dimension_request_sha256",
        "must_be_unchanged": True,
        "operations": [
            "publish_validated_part_drawing_layout_plan",
            "validate_part_drawing_layout_plan",
            "create_final_part_drawing",
            "verify_final_part_drawing",
        ],
    }
    assert contract["layout_qualification"] == {
        "scope": "g7_live_evidence_only",
        "matrix_bound": True,
        "allows_planned_capabilities": True,
        "allows_unsupported_capabilities": False,
        "requires_supported_g0_boundaries": True,
        "mutates_capability_manifest": False,
        "operations": [
            "qualify_final_part_drawing",
            "verify_qualified_final_part_drawing",
        ],
    }
    assert set(contract["forbidden_agent_boundaries"]) == {
        "private_executor_tools",
        "raw_http",
        "second_mcp_client",
        "python_com",
        "ui_automation",
        "legacy_drawing_plan_bridge",
    }
