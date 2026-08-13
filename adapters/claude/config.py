import os
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv

# Load adapters/claude/.env by EXPLICIT path. A bare load_dotenv() searches the current working
# directory, but the MCP host launches server.py from an arbitrary cwd, so the adapter's own .env
# was silently missed. Anchor it to this file's directory.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


class SamplingFallbackConfigurationError(RuntimeError):
    """Raised when the opt-in server-side Sampling fallback is unsafe to start."""


@dataclass(frozen=True)
class SamplingFallbackConfig:
    enabled: bool
    api_key: str | None
    model: str | None
    base_url: str | None


def load_sampling_fallback_config(
    environ: Mapping[str, str] | None = None,
) -> SamplingFallbackConfig:
    """Read the dedicated, opt-in model API configuration without exposing secrets."""
    source = os.environ if environ is None else environ
    enabled_text = source.get("PLANNER_SAMPLING_FALLBACK_ENABLED", "false").strip().lower()
    if enabled_text in {"1", "true", "yes", "on"}:
        enabled = True
    elif enabled_text in {"0", "false", "no", "off"}:
        enabled = False
    else:
        raise SamplingFallbackConfigurationError(
            "PLANNER_SAMPLING_FALLBACK_ENABLED must be true or false"
        )

    api_key = source.get("PLANNER_SAMPLING_API_KEY", "").strip() or None
    model = source.get("PLANNER_SAMPLING_MODEL", "").strip() or None
    base_url = source.get("PLANNER_SAMPLING_BASE_URL", "").strip().rstrip("/") or None

    if enabled and api_key is None:
        raise SamplingFallbackConfigurationError(
            "PLANNER_SAMPLING_API_KEY is required when server-side Sampling fallback is enabled"
        )
    if enabled and model is None:
        raise SamplingFallbackConfigurationError(
            "PLANNER_SAMPLING_MODEL is required when server-side Sampling fallback is enabled"
        )
    if base_url is not None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SamplingFallbackConfigurationError(
                "PLANNER_SAMPLING_BASE_URL must be an absolute HTTP(S) URL"
            )

    return SamplingFallbackConfig(
        enabled=enabled,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


SAMPLING_FALLBACK = load_sampling_fallback_config()

EXECUTION_BASE_URL = os.getenv("EXECUTION_BASE_URL", "http://localhost:5000")
EXECUTE_ENDPOINT = f"{EXECUTION_BASE_URL}/api/tool/execute"
STATE_ENDPOINT = f"{EXECUTION_BASE_URL}/api/tool/state"
HEALTH_ENDPOINT = f"{EXECUTION_BASE_URL}/health"
ENSURE_ENDPOINT = f"{EXECUTION_BASE_URL}/ensure_ready"
RELEASE_OWNED_SESSION_ENDPOINT = f"{EXECUTION_BASE_URL}/release_owned_session"
HOST_BOOTSTRAP_ENDPOINT = f"{EXECUTION_BASE_URL}/host/bootstrap"
DIMENSION_HANDOFF_ENDPOINT = f"{EXECUTION_BASE_URL}/api/dimension-planning/handoff"
DIMENSION_PROBE_CLEANUP_ENDPOINT = (
    f"{EXECUTION_BASE_URL}/api/research/dimension-probe/cleanup-session"
)
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
SIMULATION_TIMEOUT = float(os.getenv("SIMULATION_TIMEOUT", "600"))
VIEW_PLAN_TIMEOUT = float(os.getenv("VIEW_PLAN_TIMEOUT", "180"))
# ensure_ready may cold-launch SolidWorks, which can take tens of seconds — give it room.
ENSURE_TIMEOUT = float(os.getenv("ENSURE_TIMEOUT", "120"))
# Added to the helper's own bounded COM/registration timeouts for process startup and report IO.
HOST_BOOTSTRAP_TIMEOUT_MARGIN = float(os.getenv("HOST_BOOTSTRAP_TIMEOUT_MARGIN", "90"))

# Auto-start of the execution server (so the user never has to launch the exe by hand).
# Default points at the standard Debug build output, two dirs up from this adapter package
# (adapters/claude → repo root → solidworks-execution/...). Override via .env if needed.
_DEFAULT_EXE = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..",
        "solidworks-execution", "SolidworksExecution", "bin", "Debug", "SolidworksExecution.exe",
    )
)
EXECUTION_EXE_PATH = os.getenv("EXECUTION_EXE_PATH", _DEFAULT_EXE)
# How long to wait for a freshly-spawned server to answer /health before giving up.
SERVER_SPAWN_TIMEOUT = float(os.getenv("SERVER_SPAWN_TIMEOUT", "20"))
