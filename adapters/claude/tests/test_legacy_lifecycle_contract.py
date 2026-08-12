from __future__ import annotations

import asyncio
import sys
from pathlib import Path


_ADAPTER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ADAPTER_DIR))

import legacy_server  # noqa: E402


def test_legacy_diagnostic_surface_uses_ensure_ready_lifecycle_tool() -> None:
    tools = asyncio.run(legacy_server.mcp.list_tools())
    names = {tool.name for tool in tools}

    assert "ensure_ready" in names
    assert "solidworks_status" not in names
    assert {"open_document", "export_document", "close_document"}.issubset(names)
