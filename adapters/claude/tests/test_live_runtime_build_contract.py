from pathlib import Path


_ROOT = Path(__file__).resolve().parents[3]


def test_live_runtime_build_deploys_repository_host_bootstrap_helper():
    script = (
        _ROOT / "scripts/build_view_plan_live_runtime.ps1"
    ).read_text(encoding="utf-8")
    assert "solidworks-execution\\HostBootstrap\\Program.cs" in script
    assert "HostBootstrap" in script
    assert "SolidWorksHostBootstrap.exe" in script
    assert "/platform:x64" in script
    assert "Get-FileSha256 $hostBootstrapExecutable" in script


def test_initializer_freezes_sheet_scale_as_schema_integers():
    source = (
        _ROOT
        / "solidworks-execution/SolidworksExecution/Services/"
        "SolidWorksService.DrawingInitializer.cs"
    ).read_text(encoding="utf-8")
    assert '["numerator"] = scaleNumerator' in source
    assert '["denominator"] = scaleDenominator' in source
    assert 'PositiveScaleInteger(values[2], "numerator")' in source
    assert 'PositiveScaleInteger(values[3], "denominator")' in source


def test_execution_runtime_supports_only_explicit_loopback_origin_override():
    source = (
        _ROOT / "solidworks-execution/SolidworksExecution/Program.cs"
    ).read_text(encoding="utf-8")
    assert 'GetEnvironmentVariable("EXECUTION_BASE_URL")' in source
    assert 'args[0] != "--base-url"' in source
    assert "uri.IsLoopback" in source
    assert "Uri.UriSchemeHttp" in source
    assert 'uri.AbsolutePath != "/"' in source
    assert 'return "http://localhost:5000/"' in source
