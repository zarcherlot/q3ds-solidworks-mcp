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
MCP_LAUNCHER = ROOT / "scripts" / "start_codex_mcp.ps1"
CODEX_CONFIG = ROOT / ".codex" / "config.toml"


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
    assert "repository-host-setup-state.json" in source
    assert "Get-SetupInputFingerprint" in source
    assert "Test-SetupState" in source
    assert "runtime_platform" in source
    assert "-notmatch '^win-'" in source
    assert 'Install-SystemPackage "Python.Python.3.12"' in source
    assert 'Install-SystemPackage "Microsoft.DotNet.Framework.DeveloperPack_4"' in source
    assert "Microsoft.VisualStudio.2022.BuildTools" not in source
    assert "Find-MsBuild" not in source
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


def test_configure_uses_repository_roslyn_and_skips_unchanged_hosts() -> None:
    source = SETUP_SCRIPT.read_text(encoding="utf-8")
    configure = source.split("function Invoke-Configure", 1)[1].split(
        "function Collect-ReadinessChecks", 1
    )[0]

    state_check = configure.index("if (Test-SetupState $script:SetupFingerprint)")
    pip_install = configure.index("-m pip install --quiet --disable-pip-version-check")
    nuget_restore = configure.index("$script:NuGet install $packagesConfig")
    roslyn_build = configure.index("scripts\\build_view_plan_live_runtime.ps1")
    state_publish = source.index("Write-SetupState $script:SetupFingerprint")
    assert state_check < pip_install < nuget_restore < roslyn_build < state_publish
    assert 'builder = "Microsoft.Net.Compilers.Toolset/4.14.0"' in source
    assert "Visual Studio is not required." in source


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


def test_codex_launcher_runs_the_idempotent_bootstrap_gate() -> None:
    source = MCP_LAUNCHER.read_text(encoding="utf-8")

    assert "repository-host-setup-state.json" in source
    assert 'New-Item -ItemType Directory -Path (Split-Path -Parent $setupLog)' in source
    assert '& $setupScript -Mode Configure -AllowSystemPackageInstall *> $setupLog' in source
    assert '& $setupScript -Mode Configure *> $setupLog' in source
    assert source.index('& $setupScript -Mode Configure -AllowSystemPackageInstall *> $setupLog') < source.index("& $pythonExe $serverEntry")
    assert "startup_timeout_sec = 900" in CODEX_CONFIG.read_text(encoding="utf-8")
