from __future__ import annotations


def ready_h0_report(commit: str) -> dict:
    artifact = {
        "path": "C:\\frozen\\artifact.json",
        "size_bytes": 1,
        "sha256": "e" * 64,
    }
    return {
        "protocol_id": "solidworks-five-skill-release-readiness",
        "schema_version": "1.0",
        "status": "ready",
        "git": {"commit": commit, "clean": True, "changed_paths": []},
        "semantic_mcp": {
            "tool_count": 24,
            "tools": [f"fixture_tool_{index:02d}" for index in range(24)],
            "prompt_count": 0,
            "contract": dict(artifact),
            "config": dict(artifact),
            "schema": dict(artifact),
        },
        "skills": [
            {
                "name": f"fixture-skill-{index}",
                "path": f"C:\\frozen\\skill-{index}.md",
                "size_bytes": 1,
                "sha256": "e" * 64,
            }
            for index in range(5)
        ],
        "plan_schemas": [dict(artifact) for _ in range(3)],
        "capability_manifests": {
            "view": dict(artifact),
            "dimension": dict(artifact),
            "layout_boundary": dict(artifact),
            "layout_plan": dict(artifact),
        },
        "blockers": [],
    }
