"""MCP server for fast Parsec control.

This server exposes a narrow, operator-focused tool surface over
``parsec_fast_bridge``. It intentionally does not expose arbitrary shell
execution. Connect uses Parsec's peer_id command-line path; disconnect uses only
the configured Parsec disconnect hotkey after activating the Parsec window.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

import parsec_fast_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PEER_MAP = Path(__file__).resolve().with_name("parsec_peer_map.json")

mcp = FastMCP(
    "wincro-parsec",
    instructions=(
        "Fast Parsec control for WinCro operations. Use connect_pc or "
        "connect_peer to connect, disconnect to leave the current stream, "
        "and status to inspect Parsec log state."
    ),
)


def _load_peer_map(path: str | None = None) -> dict[str, str]:
    map_path = Path(path) if path else DEFAULT_PEER_MAP
    if not map_path.exists():
        return {}
    data = json.loads(map_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Peer map must be a JSON object: {map_path}")
    result: dict[str, str] = {}
    for key, value in data.items():
        if value:
            result[str(key)] = str(value)
    return result


def _summarize_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "state": status.get("state"),
        "connected": status.get("connected"),
        "method": status.get("method", "new_log"),
        "timed_out": status.get("timed_out"),
        "latest_status": status.get("latest_status"),
        "latest_cycle": status.get("latest_cycle"),
        "has_net": status.get("has_net"),
        "has_crypto": status.get("has_crypto"),
        "has_host_ready": status.get("has_host_ready"),
        "note": status.get("note"),
        "evidence": status.get("evidence", [])[-8:],
    }


def connect_peer_impl(
    peer_id: str,
    *,
    wait_status: bool = True,
    timeout: float = 8.0,
    execute: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    parsecd = bridge.find_parsecd_exe(None)
    log_path = bridge.default_parsec_log_path()
    app_log_path = bridge.default_parsec_app_log_path()
    log_offset = bridge.file_size(log_path)
    started_at = bridge.datetime.now()
    command = [str(parsecd), f"peer_id={peer_id}"]
    pid = None
    if execute:
        process = bridge.subprocess.Popen(command, close_fds=True)
        pid = process.pid
    status = None
    if wait_status:
        if not execute:
            raise RuntimeError("wait_status requires execute=true")
        status = bridge.wait_parsec_connection_status(
            log_path,
            offset=log_offset,
            started_at=started_at,
            timeout_s=timeout,
            fallback_log_paths=[log_path, app_log_path],
        )
    return {
        "ok": True if status is None else bool(status.get("connected")),
        "action": "connect_peer",
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "peer_id": peer_id,
        "parsecd": str(parsecd),
        "command": command,
        "pid": pid,
        "dry_run": not execute,
        "status": _summarize_status(status),
    }


@mcp.tool()
def list_pc_map(map_path: str | None = None) -> dict[str, Any]:
    """Return the configured PC number to Parsec peer_id map."""
    peer_map = _load_peer_map(map_path)
    return {
        "ok": True,
        "count": len(peer_map),
        "pcs": sorted(peer_map.keys(), key=lambda item: int(item) if item.isdigit() else item),
    }


@mcp.tool()
def connect_pc(
    pc_number: str | int,
    wait_status: bool = True,
    timeout: float = 8.0,
    execute: bool = True,
    map_path: str | None = None,
) -> dict[str, Any]:
    """Connect to a configured PC number through Parsec peer_id."""
    key = str(pc_number)
    peer_map = _load_peer_map(map_path)
    peer_id = peer_map.get(key)
    if not peer_id:
        return {
            "ok": False,
            "action": "connect_pc",
            "pc_number": key,
            "error": f"No peer_id mapped for PC {key}",
            "known_pcs": sorted(peer_map.keys()),
        }
    result = connect_peer_impl(
        peer_id,
        wait_status=wait_status,
        timeout=timeout,
        execute=execute,
    )
    result["action"] = "connect_pc"
    result["pc_number"] = key
    return result


@mcp.tool()
def connect_peer(
    peer_id: str,
    wait_status: bool = True,
    timeout: float = 8.0,
    execute: bool = True,
) -> dict[str, Any]:
    """Connect to a Parsec peer_id directly."""
    return connect_peer_impl(
        peer_id,
        wait_status=wait_status,
        timeout=timeout,
        execute=execute,
    )


@mcp.tool()
def disconnect(
    execute: bool = True,
    hold_ms: int = 80,
    wait_status: bool = True,
    timeout: float = 5.0,
    method: str = "window_close",
) -> dict[str, Any]:
    """Disconnect the active Parsec stream and verify Parsec reaches idle state.

    method="window_close" posts WM_CLOSE to the Parsec window. This avoids
    sending Alt+F4 to the wrong foreground app and is more reliable on Windows
    than the Linux/Raspberry Pi Ctrl+Alt+` hotkey.
    """
    started = time.perf_counter()
    window = bridge.find_window()
    log_path = bridge.default_parsec_log_path()
    offset = bridge.file_size(log_path)
    started_at = bridge.datetime.now()
    status = None
    if execute:
        if method == "window_close":
            bridge.close_window(window)
        elif method == "hotkey":
            bridge.activate_window(window)
            bridge.send_key_combo("Ctrl+Alt+~", hold_ms=hold_ms)
        else:
            raise ValueError("method must be 'window_close' or 'hotkey'")
        if wait_status:
            status = bridge.wait_parsec_disconnect_status(
                log_path,
                offset=offset,
                started_at=started_at,
                timeout_s=timeout,
                fallback_log_paths=[log_path, bridge.default_parsec_app_log_path()],
            )
    return {
        "ok": True if status is None else status.get("state") == "idle",
        "action": "disconnect",
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "dry_run": not execute,
        "window": window.to_json(),
        "method": method,
        "keys": "Ctrl+Alt+~" if method == "hotkey" else None,
        "hold_ms": hold_ms,
        "status": _summarize_status(status),
    }


@mcp.tool()
def status(max_age_s: float = 1800.0) -> dict[str, Any]:
    """Return current Parsec connection status from logs without screen capture."""
    result = bridge.read_current_parsec_status(
        [bridge.default_parsec_log_path(), bridge.default_parsec_app_log_path()],
        max_age_s=max_age_s,
    )
    return {
        "ok": bool(result.get("connected")),
        "action": "status",
        "status": _summarize_status(result),
    }


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
