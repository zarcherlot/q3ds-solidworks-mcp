"""Keep the private execution dispatcher and its low-level contract synchronized."""

import hashlib
import json
import os
import re


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_CONTROLLER = os.path.join(
    _ROOT,
    "solidworks-execution",
    "SolidworksExecution",
    "Controllers",
    "ToolController.cs",
)
_CONTRACT = os.path.join(
    _ROOT, "solidworks-execution", "contracts", "tool-schemas.json"
)
_VIEW_PLAN_TRANSACTION = os.path.join(
    _ROOT,
    "solidworks-execution",
    "SolidworksExecution",
    "Services",
    "ViewPlanBasicDrawingTransaction.cs",
)
_DIMENSION_CONTROLLER = os.path.join(
    _ROOT,
    "solidworks-execution",
    "SolidworksExecution",
    "Controllers",
    "DimensionApiProbeController.cs",
)
_DIMENSION_SERVICE = os.path.join(
    _ROOT,
    "solidworks-execution",
    "SolidworksExecution",
    "Services",
    "SolidWorksService.DimensionApiProbe.cs",
)
_F0_LIVE_RUNNER = os.path.join(
    _ROOT, "scripts", "run_dimension_f0_live_probes.py"
)


def test_every_dispatched_operation_has_a_contract():
    with open(_CONTROLLER, encoding="utf-8-sig") as handle:
        dispatched = set(re.findall(r'case\s+"([a-z0-9_]+)"\s*:', handle.read()))
    with open(_CONTRACT, encoding="utf-8") as handle:
        contract = set(json.load(handle))
    # The public legacy name is load_reference_image; the private executor operation is
    # deliberately named prepare_reference_image because it does no COM/document mutation.
    if "load_reference_image" in contract:
        contract.add("prepare_reference_image")
    missing = sorted(dispatched - contract)
    assert not missing, f"execution operations missing from tool-schemas.json: {missing}"


def test_semantic_executor_operations_are_not_agent_tools():
    semantic_execution = {
        "execute_drawing_plan",
        "verify_drawing_plan",
        "validate_frozen_part_drawing_view_plan",
        "execute_part_drawing_view_plan",
        "verify_committed_part_drawing_view_plan",
        "validate_frozen_part_drawing_dimension_plan",
        "execute_part_drawing_dimension_plan",
    }
    from test_schema_contract import _adapter_tools

    exposed = set(_adapter_tools())
    assert not (semantic_execution & exposed), (
        "executor operation names leaked through the MCP boundary; expose engineering verbs instead"
    )


def test_csharp_viewplan_parser_links_and_locks_the_repository_schema():
    schema = os.path.join(
        _ROOT, "drawing_planner", "contracts", "view-plan.schema.json"
    )
    with open(schema, "rb") as handle:
        expected_hash = hashlib.sha256(handle.read()).hexdigest()
    parser = os.path.join(
        _ROOT,
        "solidworks-execution",
        "SolidworksExecution",
        "Contracts",
        "ViewPlanContractValidator.cs",
    )
    project = os.path.join(
        _ROOT,
        "solidworks-execution",
        "SolidworksExecution",
        "SolidworksExecution.csproj",
    )
    with open(parser, encoding="utf-8-sig") as handle:
        source = handle.read().lower()
    with open(project, encoding="utf-8-sig") as handle:
        project_text = handle.read().lower()
    assert expected_hash in source
    assert "drawing_planner\\contracts\\view-plan.schema.json" in project_text
    assert "<link>contracts\\view-plan.schema.json</link>" in project_text


def test_host_bootstrap_is_an_independent_controlled_lifecycle_endpoint():
    execution_root = os.path.join(
        _ROOT, "solidworks-execution", "SolidworksExecution"
    )
    with open(_CONTROLLER, encoding="utf-8-sig") as handle:
        tool_controller = handle.read()
    with open(
        os.path.join(execution_root, "Controllers", "HostBootstrapController.cs"),
        encoding="utf-8-sig",
    ) as handle:
        host_controller = handle.read()
    with open(
        os.path.join(execution_root, "Services", "HostBootstrapRunner.cs"),
        encoding="utf-8-sig",
    ) as handle:
        runner = handle.read()
    with open(
        os.path.join(execution_root, "Models", "HostBootstrapRequest.cs"),
        encoding="utf-8-sig",
    ) as handle:
        request = handle.read()

    assert 'RoutePrefix("host")' in host_controller
    assert 'Route("bootstrap")' in host_controller
    assert "StaExecutor" not in host_controller
    assert "HostBootstrap" not in tool_controller
    assert '"HostBootstrap"' in runner
    assert '"SolidWorksHostBootstrap.exe"' in runner
    assert "request.Executable" not in runner
    assert "request.Arguments" not in runner
    assert 'JsonProperty("output_directory")' in request
    assert 'JsonProperty("drawing_template_path")' in request


def test_health_identifies_the_execution_service_and_host_bootstrap_capability():
    with open(_CONTROLLER, encoding="utf-8-sig") as handle:
        controller = handle.read()
    assert '["service"] = "solidworks-execution"' in controller
    assert '["capabilities"] = new[] { "host-bootstrap-v1" }' in controller


def test_viewplan_transaction_uses_native_copy_document_for_initializer():
    with open(_VIEW_PLAN_TRANSACTION, encoding="utf-8-sig") as handle:
        source = handle.read()

    assert "_solidWorks.CopyDocument(plan.DrawingPath, temporaryDrawing" in source
    assert "swMoveCopyError_e.swMoveCopyErrorNone" in source
    assert '"VIEW_PLAN_COPY_REQUIRES_NO_OPEN_DOCUMENTS"' in source
    assert "File.Copy(plan.DrawingPath, temporaryDrawing" not in source
    assert '".tmp.SLDDRW"' not in source


def test_f0_probe_owns_launch_and_bounded_cleanup_inside_execution_service():
    with open(_DIMENSION_CONTROLLER, encoding="utf-8-sig") as handle:
        controller = handle.read()
    with open(_DIMENSION_SERVICE, encoding="utf-8-sig") as handle:
        service = handle.read()

    assert "RunManagedDimensionApiProbe" in controller
    assert 'Route("cleanup-session")' in controller
    assert 'Value<int?>("expected_process_id")' in controller
    assert 'Value<bool?>("allow_unowned_idle_session")' in controller
    assert "Dictionary<string, object> readiness = EnsureReady();" in service
    assert "CleanupOwnedSolidWorksSession()" in service
    assert "application.UserControl = false;" in service
    assert "application.ExitApp();" in service
    assert '"OPEN_DOCUMENTS_PRESENT"' in service
    assert 'DateTime.UtcNow.AddSeconds(10)' in service
    assert 'DateTime.UtcNow.AddSeconds(15)' in service
    assert "SnapshotManagedSolidWorksProcessIds()" in service
    assert "ForceTerminateTrackedProcesses" in service
    assert "process.Kill();" in service
    assert service.index('"OPEN_DOCUMENTS_PRESENT"') < service.index("process.Kill();")
    with open(
        os.path.join(
            _ROOT,
            "solidworks-execution",
            "SolidworksExecution",
            "Services",
            "SolidWorksService.cs",
        ),
        encoding="utf-8-sig",
    ) as handle:
        core_service = handle.read()
    assert "EnsureVisible(false);" in core_service


def test_f0_runner_fails_closed_when_service_cleanup_does_not_complete():
    with open(_F0_LIVE_RUNNER, encoding="utf-8-sig") as handle:
        runner = handle.read()

    assert 'response.get("status") != "evidence_ready"' in runner
    assert "execution service did not complete the probe lifecycle" in runner
