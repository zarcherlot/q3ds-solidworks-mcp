from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[3]
SETUP_SCRIPT = ROOT / "scripts" / "setup_repository_host.ps1"
MCP_VERIFIER = ROOT / "scripts" / "verify_repository_mcp.py"


def test_setup_script_preserves_pre_mcp_and_solidworks_boundaries() -> None:
    source = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'ValidateSet("Inspect", "Configure", "Verify")' in source
    assert "repository-host-setup-report.json" in source
    assert "requirements.lock" in source
    assert "requirements-dev.lock" in source
    assert "--require-hashes" in source
    assert "-m ensurepip --upgrade" in source
    assert "c0ddc9cb0633c4607da7e8028eb4f91248c8b74e45a68b0c79fcfa7d78c2a481" in source
    assert "AllowSystemPackageInstall" in source
    assert 'installs_solidworks = $false' in source
    assert 'manages_solidworks_license = $false' in source
    assert 'elevates_process = $false' in source
    assert 'launches_solidworks = $false' in source
    assert "pywin32" not in source.lower()
    assert "makepy" not in source.lower()
    assert "/regserver" not in source.lower()
    assert "Activator.CreateInstance" not in source
    assert "Type.GetTypeFromProgID" not in source
    assert "New-Object -ComObject" not in source


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell setup is Windows-specific")
def test_inspect_is_read_only_and_always_publishes_a_structured_report(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SETUP_SCRIPT),
            "-Mode",
            "Inspect",
            "-ReportDirectory",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert completed.returncode in {0, 2}
    report_path = tmp_path / "repository-host-setup-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_schema = json.loads(
        (ROOT / "scripts" / "contracts" / "repository-host-setup-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(report_schema, format_checker=FormatChecker()).validate(report)
    assert report["kind"] == "solidpilot_repository_host_setup"
    assert report["schema_version"] == 1
    assert report["mode"] == "inspect"
    assert report["status"] in {"pass", "warning", "blocked"}
    assert report["policy"] == {
        "runs_before_semantic_mcp": True,
        "repository_scoped_changes_only_by_default": True,
        "system_package_install_authorized": False,
        "installs_solidworks": False,
        "manages_solidworks_license": False,
        "elevates_process": False,
        "launches_solidworks": False,
    }
    assert isinstance(report["checks"], list)
    assert isinstance(report["actions"], list)
    assert isinstance(report["blocking_issues"], list)
    assert report["outputs"]["report_path"] == str(report_path)


def test_mcp_verifier_uses_the_repository_contract_as_its_tool_allow_list() -> None:
    source = MCP_VERIFIER.read_text(encoding="utf-8")

    assert "semantic-tools.schema.json" in source
    assert 'expected = set(schema["required"])' in source
    assert "start_codex_mcp.ps1" in source
    assert "session.list_tools()" in source
    assert "session.call_tool" not in source
