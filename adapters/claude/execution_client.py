import os
import subprocess
import threading
import time
import atexit
import httpx
from config import (
    EXECUTE_ENDPOINT,
    STATE_ENDPOINT,
    HEALTH_ENDPOINT,
    ENSURE_ENDPOINT,
    HOST_BOOTSTRAP_ENDPOINT,
    HTTP_TIMEOUT,
    SIMULATION_TIMEOUT,
    VIEW_PLAN_TIMEOUT,
    ENSURE_TIMEOUT,
    HOST_BOOTSTRAP_TIMEOUT_MARGIN,
    EXECUTION_EXE_PATH,
    SERVER_SPAWN_TIMEOUT,
)
from adapter_log import write as _log


class ExecutionLayerError(Exception):
    """Raised when the execution layer returns an unexpected HTTP error."""
    pass


# Serializes auto-start so two concurrent tool calls hitting a down server don't each
# spawn a duplicate exe.
_spawn_lock = threading.Lock()

_EXECUTION_SERVICE_ID = "solidworks-execution"
_HOST_BOOTSTRAP_CAPABILITY = "host-bootstrap-v1"

# Reuse localhost HTTP connections across tool calls. Creating a new client for
# every sketch entity and analysis added avoidable setup overhead to long builds.
_client = httpx.Client(
    timeout=HTTP_TIMEOUT,
    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
    trust_env=False,  # the execution bridge is loopback-only and must never use a host proxy
)
_ensure_client = httpx.Client(
    timeout=ENSURE_TIMEOUT,
    limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
    trust_env=False,
)


def _close_clients() -> None:
    _client.close()
    _ensure_client.close()


atexit.register(_close_clients)


def _server_probe() -> tuple[str, str | None]:
    """Return compatible, incompatible, or down for the configured execution endpoint."""
    try:
        response = _client.get(HEALTH_ENDPOINT, timeout=2.0)
    except Exception:
        return "down", None
    if response.status_code != 200:
        return "down", None
    try:
        body = response.json()
    except ValueError:
        return "incompatible", "the health response is not JSON"
    capabilities = body.get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = []
    missing = []
    if body.get("service") != _EXECUTION_SERVICE_ID:
        missing.append(f"service={_EXECUTION_SERVICE_ID}")
    if _HOST_BOOTSTRAP_CAPABILITY not in capabilities:
        missing.append(_HOST_BOOTSTRAP_CAPABILITY)
    if missing:
        return "incompatible", "missing " + ", ".join(missing)
    return "compatible", None


def _server_is_up() -> bool:
    """True only when /health identifies a compatible repository execution service."""
    status, _ = _server_probe()
    return status == "compatible"


def _incompatible_server_error(detail: str | None) -> ExecutionLayerError:
    reason = detail or "its health contract is incompatible"
    return ExecutionLayerError(
        f"{HEALTH_ENDPOINT} is occupied by an incompatible Execution Service ({reason}). "
        "Stop the stale service or correct any EXECUTION_EXE_PATH override, then retry. "
        f"The configured repository executable is {EXECUTION_EXE_PATH}."
    )


def _ensure_server_up() -> None:
    """Start the execution-server exe if it isn't already answering /health.

    Guarded against duplicate spawns: re-checks /health inside the lock, spawns at most
    one headless/detached process, then polls /health until it's up. The server is meant
    to outlive this call (it persists while SolidWorks is open), so it is launched detached.
    Raises ExecutionLayerError if it can't be brought up (missing exe, or never came up).
    """
    with _spawn_lock:
        probe_status, probe_detail = _server_probe()
        if probe_status == "compatible":
            return
        if probe_status == "incompatible":
            raise _incompatible_server_error(probe_detail)
        if not os.path.isfile(EXECUTION_EXE_PATH):
            _log(f"!! cannot auto-start server — exe not found at {EXECUTION_EXE_PATH}")
            raise ExecutionLayerError(
                "solidworks-execution is not running and its exe was not found at "
                f"{EXECUTION_EXE_PATH}. Build the server or set EXECUTION_EXE_PATH."
            )
        _log(f"-> auto-starting execution server: {EXECUTION_EXE_PATH}")
        # DETACHED_PROCESS | CREATE_NO_WINDOW: survive the adapter, no console window flash.
        creationflags = 0x00000008 | 0x08000000
        try:
            subprocess.Popen(
                [EXECUTION_EXE_PATH],
                cwd=os.path.dirname(EXECUTION_EXE_PATH),
                creationflags=creationflags,
                close_fds=True,
            )
        except Exception as ex:
            _log(f"!! server spawn failed: {ex}")
            raise ExecutionLayerError(f"Failed to start solidworks-execution: {ex}")

        deadline = time.monotonic() + SERVER_SPAWN_TIMEOUT
        while time.monotonic() < deadline:
            probe_status, probe_detail = _server_probe()
            if probe_status == "compatible":
                _log("<- execution server is up")
                return
            if probe_status == "incompatible":
                raise _incompatible_server_error(probe_detail)
            time.sleep(0.5)
        _log("!! execution server did not answer /health within timeout")
        raise ExecutionLayerError(
            f"Started solidworks-execution but it did not answer {HEALTH_ENDPOINT} "
            f"within {SERVER_SPAWN_TIMEOUT}s."
        )


def _request_with_autostart(do_request, label: str):
    """Run an httpx request; on ConnectError, auto-start the server once and retry.

    Makes every tool call self-heal the "server is down" case transparently. Timeouts are
    NOT caught here (they propagate to each caller's own timeout handling).
    """
    try:
        return do_request()
    except httpx.ConnectError:
        _log(f"<- {label} CONNECT_ERROR — attempting server auto-start")
        _ensure_server_up()  # raises ExecutionLayerError if it can't bring the server up
        try:
            return do_request()
        except httpx.ConnectError:
            _log(f"<- {label} CONNECT_ERROR after auto-start")
            raise ExecutionLayerError(
                "Cannot connect to solidworks-execution even after auto-start. "
                f"Is {HEALTH_ENDPOINT} reachable?"
            )


def get_health() -> dict:
    """GET /health — server status + COM attach state (does not touch state_version)."""
    try:
        response = _client.get(HEALTH_ENDPOINT)
    except httpx.ConnectError:
        _log("<- health CONNECT_ERROR (server down?)")
        raise ExecutionLayerError(
            f"Cannot connect to solidworks-execution. Is the server running on {HEALTH_ENDPOINT}?"
        )
    except httpx.TimeoutException:
        _log("<- health TIMEOUT")
        raise ExecutionLayerError(f"Health request timed out after {HTTP_TIMEOUT}s.")
    if response.status_code != 200:
        raise ExecutionLayerError(f"Unexpected HTTP {response.status_code} from /health: {response.text}")
    body = response.json()
    _log(f"<- health status={body.get('status')} comAttached={body.get('comAttached')} sv={body.get('stateVersion')}")
    return body


def get_state() -> int:
    """
    GET /api/tool/state — fetch the current authoritative state_version.

    Used to resync after a desync (e.g. execution server restarted on rebuild).
    Read-only on the server: no state_version check, no increment.
    """
    _log("-> get_state (resync)")

    def _do():
        return _client.get(STATE_ENDPOINT)

    try:
        response = _request_with_autostart(_do, "get_state")
    except httpx.TimeoutException:
        _log("<- get_state TIMEOUT")
        raise ExecutionLayerError(
            f"Resync request to solidworks-execution timed out after {HTTP_TIMEOUT}s."
        )

    if response.status_code != 200:
        raise ExecutionLayerError(
            f"Unexpected HTTP {response.status_code} from /state: {response.text}"
        )

    sv = int(response.json().get("stateVersion", 0))
    _log(f"<- get_state sv={sv}")
    return sv


def call_tool(tool_name: str, operation_id: str, state_version: int, params: dict) -> dict:
    """
    POST /api/tool/execute on the solidworks-execution layer.

    Returns the parsed JSON response body on HTTP 200.
    Raises ExecutionLayerError on HTTP 400 or unexpected status codes.
    """
    payload = {
        "operationId": operation_id,
        "tool": tool_name,
        "stateVersion": state_version,
        "params": params,
    }

    _log(f"-> {tool_name} op={operation_id} sv={state_version}")

    if tool_name == "sim_mesh_and_run":
        request_timeout = SIMULATION_TIMEOUT
    elif tool_name in {
        "execute_drawing_plan",
        "verify_drawing_plan",
        "execute_part_drawing_view_plan",
        "verify_committed_part_drawing_view_plan",
        "initialize_part_drawing_handoff",
    }:
        request_timeout = VIEW_PLAN_TIMEOUT
    else:
        request_timeout = HTTP_TIMEOUT

    def _do():
        return _client.post(EXECUTE_ENDPOINT, json=payload, timeout=request_timeout)

    try:
        response = _request_with_autostart(_do, tool_name)
    except httpx.TimeoutException:
        _log(f"<- {tool_name} TIMEOUT after {request_timeout}s")
        raise ExecutionLayerError(
            f"Request to solidworks-execution timed out after {request_timeout}s."
        )

    if response.status_code == 400:
        body = response.json()
        _log(f"<- {tool_name} HTTP_400 {body.get('error', '')}")
        raise ExecutionLayerError(f"Bad request: {body.get('error', response.text)}")

    if response.status_code != 200:
        _log(f"<- {tool_name} HTTP_{response.status_code}")
        raise ExecutionLayerError(
            f"Unexpected HTTP {response.status_code} from execution layer: {response.text}"
        )

    body = response.json()
    err = (body.get("error") or {}).get("code")
    _log(f"<- {tool_name} {body.get('status')}{(' ' + err) if err else ''} sv={body.get('stateVersion')}")
    return body


def ensure_ready() -> dict:
    """POST /ensure_ready — bring the whole stack up and report readiness.

    Two layers: (1) make sure the execution server itself is running (auto-spawn it if
    down — a down server can't answer /ensure_ready); (2) the server attaches to SolidWorks,
    launching it via COM if it's closed. Uses a long timeout because a cold SolidWorks launch
    can take tens of seconds. Returns the parsed readiness dict; does NOT open any document.
    """
    _log("-> ensure_ready")
    # A down server can't answer /ensure_ready, so spawn it up front (idempotent / guarded).
    if not _server_is_up():
        _ensure_server_up()

    def _do():
        return _ensure_client.post(ENSURE_ENDPOINT)

    try:
        response = _request_with_autostart(_do, "ensure_ready")
    except httpx.TimeoutException:
        _log("<- ensure_ready TIMEOUT")
        raise ExecutionLayerError(
            f"ensure_ready timed out after {ENSURE_TIMEOUT}s (a SolidWorks cold launch can be slow)."
        )

    if response.status_code != 200:
        _log(f"<- ensure_ready HTTP_{response.status_code}")
        raise ExecutionLayerError(
            f"Unexpected HTTP {response.status_code} from /ensure_ready: {response.text}"
        )

    body = response.json()
    _log(
        f"<- ensure_ready comAttached={body.get('comAttached')} "
        f"swLaunched={body.get('swLaunched')} doc={body.get('activeDocument')}"
    )
    return body


def bootstrap_host(payload: dict) -> dict:
    """Run the private repository HostBootstrap lifecycle endpoint.

    This endpoint is intentionally separate from the executor-shaped tool dispatcher: the C#
    service launches a controlled repository helper and Python only sends validated semantic
    options. Structured blockers are returned to the MCP caller instead of being collapsed into
    transport errors.
    """
    mode = payload.get("mode")
    com_timeout = int(payload.get("com_timeout_seconds", 180))
    regserver_timeout = int(payload.get("regserver_timeout_seconds", 120))
    if mode == "inspect":
        request_timeout = max(60.0, HTTP_TIMEOUT)
    elif mode == "verify":
        request_timeout = com_timeout + HOST_BOOTSTRAP_TIMEOUT_MARGIN
    elif mode == "repair":
        request_timeout = (
            (com_timeout * 2) + regserver_timeout + HOST_BOOTSTRAP_TIMEOUT_MARGIN
        )
    else:
        raise ValueError("mode must be inspect, verify, or repair")

    # Unlike executor operations, an older service can answer /health and still lack this
    # independent lifecycle endpoint. Verify the service identity/capability before posting so a
    # stale EXE or port collision produces an actionable error instead of an opaque HTTP 404.
    _ensure_server_up()

    _log(f"-> host/bootstrap mode={mode}")

    def _do():
        return _client.post(
            HOST_BOOTSTRAP_ENDPOINT,
            json=payload,
            timeout=request_timeout,
        )

    try:
        response = _request_with_autostart(_do, "host/bootstrap")
    except httpx.TimeoutException as exc:
        _log(f"<- host/bootstrap TIMEOUT after {request_timeout}s")
        raise ExecutionLayerError(
            f"Host bootstrap request timed out after {request_timeout}s."
        ) from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise ExecutionLayerError(
            f"Host bootstrap returned non-JSON HTTP {response.status_code}."
        ) from exc
    if response.status_code == 200:
        _log(f"<- host/bootstrap {body.get('status')} mode={mode}")
        return body
    if response.status_code in {400, 409, 500} and body.get("status") == "blocked":
        _log(f"<- host/bootstrap BLOCKED HTTP_{response.status_code}")
        return body
    raise ExecutionLayerError(
        f"Unexpected HTTP {response.status_code} from /host/bootstrap: {response.text}"
    )
