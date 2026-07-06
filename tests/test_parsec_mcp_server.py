import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import parsec_mcp_server as server


def test_load_peer_map_returns_string_keys(tmp_path):
    peer_map = tmp_path / "peers.json"
    peer_map.write_text('{"1": "peer-one", "02": "peer-two"}', encoding="utf-8")

    result = server._load_peer_map(str(peer_map))

    assert result == {"1": "peer-one", "02": "peer-two"}


def test_connect_pc_reports_missing_mapping(tmp_path):
    peer_map = tmp_path / "peers.json"
    peer_map.write_text('{"1": "peer-one"}', encoding="utf-8")

    result = server.connect_pc("2", execute=False, wait_status=False, map_path=str(peer_map))

    assert result["ok"] is False
    assert result["pc_number"] == "2"
    assert "No peer_id" in result["error"]


def test_summarize_status_limits_evidence():
    status = {
        "state": "connected",
        "connected": True,
        "method": "current_status",
        "timed_out": False,
        "latest_status": "0",
        "latest_cycle": ["20", "0"],
        "has_net": True,
        "has_crypto": True,
        "has_host_ready": True,
        "evidence": [f"line {idx}" for idx in range(20)],
    }

    result = server._summarize_status(status)

    assert result["connected"] is True
    assert result["evidence"] == [f"line {idx}" for idx in range(12, 20)]
