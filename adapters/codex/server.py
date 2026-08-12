"""Codex-compatible stdio launcher for the shared semantic SolidWorks MCP server."""

from __future__ import annotations

import sys
from pathlib import Path


def _configure_utf8_stdio() -> None:
    """Keep the stdio MCP transport independent of the Windows ANSI code page.

    Codex decodes both protocol output and server diagnostics as UTF-8.  On a
    Chinese Windows host Python otherwise inherits CP936 for redirected stdio,
    and FastMCP's Unicode diagnostics can make the client abort initialization.
    """
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


if __name__ == "__main__":
    _configure_utf8_stdio()

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHARED_ADAPTER = _REPO_ROOT / "adapters" / "claude"
for path in (str(_REPO_ROOT), str(_SHARED_ADAPTER)):
    if path not in sys.path:
        sys.path.insert(0, path)

from server import mcp  # noqa: E402


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
