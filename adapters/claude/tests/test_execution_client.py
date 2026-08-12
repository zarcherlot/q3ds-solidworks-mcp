"""Timeout routing for long-running private execution transactions."""

import os
import sys
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_ADAPTER_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _ADAPTER_DIR)

import execution_client  # noqa: E402


class _CompletedResponse:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {"status": "COMPLETED", "stateVersion": 0}


class _HostResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict:
        return self._body


def test_server_probe_rejects_stale_execution_service_without_current_capability():
    stale = _HostResponse(200, {"status": "UP", "stateVersion": 0})
    with patch.object(execution_client._client, "get", return_value=stale):
        status, detail = execution_client._server_probe()
    assert status == "incompatible"
    assert "host-bootstrap-v1" in detail


def test_ensure_server_up_reports_stale_path_override_without_spawning():
    with (
        patch.object(
            execution_client,
            "_server_probe",
            return_value=("incompatible", "missing host-bootstrap-v1"),
        ),
        patch.object(execution_client.subprocess, "Popen") as popen,
    ):
        try:
            execution_client._ensure_server_up()
        except execution_client.ExecutionLayerError as exc:
            message = str(exc)
        else:
            raise AssertionError("incompatible service should block startup")
    popen.assert_not_called()
    assert "EXECUTION_EXE_PATH" in message
    assert execution_client.EXECUTION_EXE_PATH in message


def test_drawing_transactions_use_the_long_view_plan_timeout():
    for tool_name in (
        "execute_drawing_plan",
        "verify_drawing_plan",
        "execute_part_drawing_view_plan",
        "verify_committed_part_drawing_view_plan",
        "initialize_part_drawing_handoff",
    ):
        with patch.object(
            execution_client._client, "post", return_value=_CompletedResponse()
        ) as post:
            execution_client.call_tool(tool_name, "op", 0, {})
        assert post.call_args.kwargs["timeout"] == execution_client.VIEW_PLAN_TIMEOUT


def test_host_bootstrap_timeout_tracks_private_lifecycle_mode():
    cases = {
        "inspect": max(60.0, execution_client.HTTP_TIMEOUT),
        "verify": 30 + execution_client.HOST_BOOTSTRAP_TIMEOUT_MARGIN,
        "repair": 60 + 40 + execution_client.HOST_BOOTSTRAP_TIMEOUT_MARGIN,
    }
    for mode, expected_timeout in cases.items():
        payload = {
            "mode": mode,
            "com_timeout_seconds": 30,
            "regserver_timeout_seconds": 40,
        }
        with (
            patch.object(execution_client, "_ensure_server_up") as ensure,
            patch.object(
                execution_client._client,
                "post",
                return_value=_HostResponse(200, {"status": "pass", "ok": True}),
            ) as post,
        ):
            result = execution_client.bootstrap_host(payload)
        ensure.assert_called_once_with()
        assert result["status"] == "pass"
        assert post.call_args.args[0] == execution_client.HOST_BOOTSTRAP_ENDPOINT
        assert post.call_args.kwargs["timeout"] == expected_timeout


def test_host_bootstrap_preserves_structured_service_blockers():
    blocker = {
        "status": "blocked",
        "error": {"code": "HOST_BOOTSTRAP_REQUEST_INVALID", "message": "not elevated"},
    }
    with (
        patch.object(execution_client, "_ensure_server_up"),
        patch.object(
            execution_client._client,
            "post",
            return_value=_HostResponse(400, blocker),
        ),
    ):
        assert execution_client.bootstrap_host({"mode": "repair"}) == blocker
