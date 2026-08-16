from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from release_candidate import h4_semantic_step as h4
from release_candidate.tests.test_h3_session_capture import _create


def _request(created: dict, *, sequence: int = 1, tool: str = "inspect_solidworks_host") -> dict:
    return {
        "protocol_id": "solidworks-five-skill-semantic-step-request",
        "schema_version": "1.0",
        "session_manifest": {
            "path": created["session_manifest_path"],
            "sha256": created["session_manifest_sha256"],
        },
        "sequence": sequence,
        "tool": tool,
        "arguments": {"output_directory": "C:\\evidence\\host"},
    }


def test_h4_request_contract_is_valid_draft_2020_12() -> None:
    for path in (h4.REQUEST_SCHEMA_PATH, h4.CLAIM_SCHEMA_PATH):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_h4_loads_only_hash_bound_strict_request(tmp_path: Path) -> None:
    path = tmp_path / "step.json"
    path.write_text(
        json.dumps(
            {
                "protocol_id": "solidworks-five-skill-semantic-step-request",
                "schema_version": "1.0",
                "session_manifest": {"path": "C:\\session\\session-manifest.json", "sha256": "a" * 64},
                "sequence": 1,
                "tool": "inspect_solidworks_host",
                "arguments": {},
            }
        ),
        encoding="utf-8",
    )
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    assert h4.load_h4_step_request(path, sha256)["sequence"] == 1
    with pytest.raises(h4.H4SemanticStepError, match="SHA-256 mismatch"):
        h4.load_h4_step_request(path, "b" * 64)


def test_h4_rejects_wrong_tool_before_call(tmp_path: Path, monkeypatch) -> None:
    created, _, _ = _create(tmp_path, monkeypatch)
    called = False

    async def caller(tool: str, arguments: dict) -> dict:
        nonlocal called
        called = True
        return {"ok": True}

    with pytest.raises(h4.H4SemanticStepError, match="expected tool"):
        asyncio.run(
            h4.run_h4_semantic_step(
                _request(created, tool="initialize_part_drawing_handoff"),
                semantic_caller=caller,
            )
        )
    assert called is False


def test_h4_rejects_diagnostics_outside_session_before_call(
    tmp_path: Path, monkeypatch
) -> None:
    created, _, _ = _create(tmp_path, monkeypatch)
    called = False

    async def caller(tool: str, arguments: dict) -> dict:
        nonlocal called
        called = True
        return {"ok": True}

    with pytest.raises(h4.H4SemanticStepError, match="inside the session root"):
        asyncio.run(
            h4.run_h4_semantic_step(
                _request(created),
                semantic_caller=caller,
                diagnostics_path=(tmp_path / "outside.log").resolve(),
            )
        )
    assert called is False


def test_h4_calls_and_captures_exactly_one_next_operation(tmp_path: Path, monkeypatch) -> None:
    created, _, _ = _create(tmp_path, monkeypatch)
    calls: list[tuple[str, dict]] = []

    async def caller(tool: str, arguments: dict) -> dict:
        calls.append((tool, arguments))
        return {"ok": True, "status": "pass", "host": {"revision": "33.5.0"}}

    result = asyncio.run(
        h4.run_h4_semantic_step(_request(created), semantic_caller=caller)
    )
    assert result["status"] == "captured"
    assert calls == [
        ("inspect_solidworks_host", {"output_directory": "C:\\evidence\\host"})
    ]
    assert Path(result["call_claim"]["path"]).is_file()
    event = Path(result["capture"]["event_path"])
    assert json.loads(event.read_text(encoding="utf-8")) == result["semantic_response"]

    with pytest.raises(h4.H4SemanticStepError, match="awaiting_stage_capture"):
        asyncio.run(
            h4.run_h4_semantic_step(
                _request(
                    created,
                    sequence=2,
                    tool="initialize_part_drawing_handoff",
                ),
                semantic_caller=caller,
            )
        )
    assert len(calls) == 1


def test_h4_captures_semantic_failure_and_permanently_blocks(tmp_path: Path, monkeypatch) -> None:
    created, _, _ = _create(tmp_path, monkeypatch)
    calls = 0

    async def caller(tool: str, arguments: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"ok": False, "status": "blocked", "error": "host unavailable"}

    result = asyncio.run(
        h4.run_h4_semantic_step(_request(created), semantic_caller=caller)
    )
    assert result["status"] == "blocked"
    assert result["semantic_response"]["error"] == "host unavailable"
    with pytest.raises(h4.H4SemanticStepError, match="got blocked"):
        asyncio.run(
            h4.run_h4_semantic_step(_request(created), semantic_caller=caller)
        )
    assert calls == 1


def test_h4_ambiguous_call_is_captured_as_non_retryable_failure(
    tmp_path: Path, monkeypatch
) -> None:
    created, _, _ = _create(tmp_path, monkeypatch)

    async def caller(tool: str, arguments: dict) -> dict:
        raise TimeoutError("response timeout")

    result = asyncio.run(
        h4.run_h4_semantic_step(_request(created), semantic_caller=caller)
    )
    assert result["status"] == "blocked"
    assert result["semantic_response"]["error"] == {
        "code": "h4-ambiguous-semantic-call",
        "message": "response timeout",
        "retry_safe": False,
    }
    assert Path(result["call_claim"]["path"]).is_file()


def test_h4_releases_claim_only_for_typed_pre_call_failure(
    tmp_path: Path, monkeypatch
) -> None:
    created, _, _ = _create(tmp_path, monkeypatch)

    async def pre_call_failure(tool: str, arguments: dict) -> dict:
        raise h4._PreCallMcpError("surface discovery failed")

    with pytest.raises(h4.H4SemanticStepError, match="surface discovery failed"):
        asyncio.run(
            h4.run_h4_semantic_step(
                _request(created), semantic_caller=pre_call_failure
            )
        )

    async def success(tool: str, arguments: dict) -> dict:
        return {"ok": True, "status": "pass"}

    result = asyncio.run(
        h4.run_h4_semantic_step(_request(created), semantic_caller=success)
    )
    assert result["status"] == "captured"


def test_h4_call_claim_prevents_concurrent_replay(tmp_path: Path, monkeypatch) -> None:
    created, _, _ = _create(tmp_path, monkeypatch)

    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def caller(tool: str, arguments: dict) -> dict:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"ok": True, "status": "pass"}

        first = asyncio.create_task(
            h4.run_h4_semantic_step(_request(created), semantic_caller=caller)
        )
        await started.wait()
        with pytest.raises(h4.H4SemanticStepError, match="replay is forbidden"):
            await h4.run_h4_semantic_step(
                _request(created), semantic_caller=caller
            )
        release.set()
        assert (await first)["status"] == "captured"
        assert calls == 1

    asyncio.run(scenario())


def test_h4_default_transport_is_repository_codex_stdio_only() -> None:
    source = Path(h4.__file__).read_text(encoding="utf-8")
    assert "adapters\" / \"codex\" / \"server.py" in source
    assert "stdio_client" in source
    assert "session.call_tool" in source
    assert "EXECUTION_EXE_PATH" in source
    assert "httpx" not in source
    assert "qualify_dimensioned_part_drawing\"," not in source
    assert "/api/" not in source
