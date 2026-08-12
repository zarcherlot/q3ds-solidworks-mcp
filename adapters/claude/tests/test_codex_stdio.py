from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


_EXPECTED_TOOLS = {
    "solidworks_status",
    "inspect_solidworks_host",
    "bootstrap_solidworks_host",
    "inspect_part_for_drawing",
    "initialize_part_drawing_handoff",
    "plan_part_drawing_views",
    "publish_validated_part_drawing_view_plan",
    "validate_part_drawing_view_plan",
    "create_part_drawing_from_view_plan",
    "verify_part_drawing_view_plan",
}


async def _initialize_codex_server(
    root: Path, via_powershell_wrapper: bool = False
) -> tuple[str, set[str]]:
    child_env = os.environ.copy()
    # Reproduce a redirected stdio process on a Chinese Windows code page.
    child_env["PYTHONIOENCODING"] = "cp936"
    if via_powershell_wrapper:
        command = "powershell.exe"
        args = [
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts" / "start_codex_mcp.ps1"),
        ]
    else:
        command = sys.executable
        args = [str(root / "adapters" / "codex" / "server.py")]
    params = StdioServerParameters(
        command=command,
        args=args,
        cwd=str(root),
        env=child_env,
        encoding="utf-8",
        encoding_error_handler="strict",
    )
    with tempfile.TemporaryFile(
        mode="w+", encoding="utf-8", errors="strict"
    ) as diagnostics:
        async with stdio_client(params, errlog=diagnostics) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
        diagnostics.seek(0)
        assert "FastMCP 3" not in diagnostics.read()
    return initialized.serverInfo.name, {tool.name for tool in tools.tools}


def test_codex_stdio_initializes_as_utf8_without_banner() -> None:
    root = Path(__file__).resolve().parents[3]

    server_name, tool_names = anyio.run(_initialize_codex_server, root)

    assert server_name == "Q3DS SolidWorks Engineering"
    assert tool_names == _EXPECTED_TOOLS


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell launcher is Windows-specific")
def test_codex_powershell_launcher_initializes_as_utf8() -> None:
    root = Path(__file__).resolve().parents[3]

    server_name, tool_names = anyio.run(_initialize_codex_server, root, True)

    assert server_name == "Q3DS SolidWorks Engineering"
    assert tool_names == _EXPECTED_TOOLS
