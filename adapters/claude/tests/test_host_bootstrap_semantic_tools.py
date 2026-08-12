"""Semantic MCP orchestration contract for the repository-owned HostBootstrap."""

import json
import os
import sys
from unittest.mock import patch


_HERE = os.path.dirname(os.path.abspath(__file__))
_ADAPTER_DIR = os.path.dirname(_HERE)
sys.path.insert(0, _ADAPTER_DIR)

import server  # noqa: E402


def test_inspect_host_uses_fixed_non_launch_mode(tmp_path):
    response = {"ok": True, "status": "pass", "reportSha256": "a" * 64}
    with patch.object(server, "bootstrap_host", return_value=response) as bootstrap:
        result = json.loads(server.inspect_solidworks_host(str(tmp_path)))

    assert result == response
    payload = bootstrap.call_args.args[0]
    assert payload == {
        "mode": "inspect",
        "output_directory": os.path.abspath(tmp_path),
        "drawing_template_path": None,
        "visible": False,
        "keep_solidworks_running": False,
        "com_timeout_seconds": 180,
        "regserver_timeout_seconds": 120,
    }


def test_bootstrap_host_maps_repair_only_from_explicit_authorization(tmp_path):
    with patch.object(
        server,
        "bootstrap_host",
        return_value={"ok": False, "status": "blocked"},
    ) as bootstrap:
        result = json.loads(
            server.bootstrap_solidworks_host(
                str(tmp_path),
                allow_registration_repair=True,
                visible=True,
                keep_solidworks_running=True,
                com_timeout_seconds=45,
                regserver_timeout_seconds=30,
            )
        )

    assert result["status"] == "blocked"
    payload = bootstrap.call_args.args[0]
    assert payload["mode"] == "repair"
    assert payload["visible"] is True
    assert payload["keep_solidworks_running"] is True
    assert payload["com_timeout_seconds"] == 45
    assert payload["regserver_timeout_seconds"] == 30


def test_host_report_directory_must_preexist(tmp_path):
    missing = tmp_path / "missing"
    try:
        server.inspect_solidworks_host(str(missing))
    except ValueError as exception:
        assert "does not exist" in str(exception)
    else:
        raise AssertionError("a missing report directory must be rejected before HTTP")
