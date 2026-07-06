import json
from datetime import datetime

import cv2
import numpy as np

from tools import parsec_fast_bridge as bridge


def _pattern_image(width=14, height=10):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:, :] = (20, 30, 40)
    cv2.rectangle(image, (2, 2), (width - 3, height - 3), (210, 80, 20), 1)
    cv2.line(image, (1, height - 2), (width - 2, 1), (40, 220, 180), 1)
    cv2.circle(image, (width // 2, height // 2), 2, (240, 240, 30), -1)
    return image


def test_match_template_returns_window_relative_coordinates():
    screen = np.full((80, 120, 3), 7, dtype=np.uint8)
    template = _pattern_image()
    screen[31 : 31 + template.shape[0], 42 : 42 + template.shape[1]] = template

    result = bridge.match_template(screen, template, threshold=0.95)

    assert result.ok
    assert result.x == 42
    assert result.y == 31
    assert result.center_x == 49
    assert result.center_y == 36
    assert result.score >= 0.99


def test_match_template_respects_search_region_offset():
    screen = np.full((90, 140, 3), 11, dtype=np.uint8)
    template = _pattern_image()
    screen[50 : 50 + template.shape[0], 70 : 70 + template.shape[1]] = template

    result = bridge.match_template(screen, template, threshold=0.95, region=(60, 40, 60, 40))

    assert result.ok
    assert result.x == 70
    assert result.y == 50


def test_match_template_too_small_region_fails_without_exception():
    screen = np.full((40, 40, 3), 3, dtype=np.uint8)
    template = _pattern_image(width=20, height=16)

    result = bridge.match_template(screen, template, threshold=0.8, region=(0, 0, 5, 5))

    assert not result.ok
    assert result.score == 0.0


def test_parse_region_and_clamp_region():
    assert bridge.parse_region("10,20,30,40") == (10, 20, 30, 40)
    assert bridge.clamp_region((-5, 8, 20, 20), 30, 25) == (0, 8, 15, 17)
    assert bridge.clamp_region((20, 20, 50, 50), 30, 25) == (20, 20, 10, 5)


def test_parse_key_combo_supports_common_names():
    assert bridge.parse_key_combo("Shift+Up") == [0x10, 0x26]
    assert bridge.parse_key_combo("Ctrl+A") == [0x11, 0x41]
    assert bridge.parse_key_combo("Enter") == [0x0D]
    assert bridge.parse_key_combo("F12") == [0x7B]
    assert bridge.parse_key_combo("Ctrl+Alt+~") == [0x11, 0x12, 0xC0]


def test_dangerous_alt_f4_is_blocked_by_default():
    vks = bridge.parse_key_combo("Alt+F4")

    try:
        bridge.assert_key_combo_allowed(vks)
    except ValueError as exc:
        assert "dangerous" in str(exc)
    else:
        raise AssertionError("Alt+F4 should be blocked")


def test_dangerous_alt_f4_can_be_forced_for_manual_debug_only():
    bridge.assert_key_combo_allowed(bridge.parse_key_combo("Alt+F4"), force=True)


def test_key_command_dry_run_does_not_require_windows_input():
    parser = bridge.build_parser()
    args = parser.parse_args(["key", "--keys", "Shift+Up", "--hold-ms", "40"])

    result = args.func(args)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["vk"] == [0x10, 0x26]
    assert result["hold_ms"] == 40
    assert result["activated"] is True or result["activated"] is False


def test_key_command_execute_without_activation_is_refused():
    parser = bridge.build_parser()
    args = parser.parse_args(["key", "--keys", "Enter", "--execute", "--no-activate"])

    try:
        args.func(args)
    except RuntimeError as exc:
        assert "without target window activation" in str(exc)
    else:
        raise AssertionError("Executable key input without activation should be refused")


def test_connect_peer_command_dry_run_builds_parsecd_command(tmp_path):
    parsecd = tmp_path / "parsecd.exe"
    parsecd.write_bytes(b"fake")
    parser = bridge.build_parser()
    args = parser.parse_args(
        [
            "connect-peer",
            "--peer-id",
            "example-peer",
            "--parsecd",
            str(parsecd),
            "--settings",
            "client_vsync=1",
        ]
    )

    result = args.func(args)

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["parsecd"] == str(parsecd)
    assert result["command"] == [str(parsecd), "peer_id=example-peer:client_vsync=1"]
    assert result["pid"] is None


def test_classify_parsec_status_connected_flow():
    lines = [
        "[D 2026-07-01 15:45:42] Client status changed to: 20",
        "[D 2026-07-01 15:45:43] net           = BUD|::ffff:192.168.31.52|22766",
        "[D 2026-07-01 15:45:43] BUD AES_GCM   = 256",
        "[D 2026-07-01 15:45:43] Client status changed to: 0",
        "[I 2026-07-01 15:45:44] Host's virtual microphone is enabled",
    ]

    result = bridge.classify_parsec_status_lines(lines)

    assert result["state"] == "connected"
    assert result["connected"] is True
    assert result["connecting_then_connected"] is True
    assert result["statuses"] == ["20", "0"]
    assert result["latest_status"] == "0"
    assert result["latest_cycle"] == ["20", "0"]
    assert result["has_net"] is True
    assert result["has_crypto"] is True
    assert result["has_host_ready"] is True


def test_classify_parsec_status_uses_latest_status_for_idle():
    lines = [
        "[I 2026-07-01 15:45:42] Client Status received: 20",
        "[I 2026-07-01 15:45:44] Client Status received: 0",
        "[I 2026-07-01 16:02:51] Client Status received: -3",
    ]

    result = bridge.classify_parsec_status_lines(lines)

    assert result["state"] == "idle"
    assert result["connected"] is False
    assert result["latest_status"] == "-3"
    assert result["latest_cycle"] == ["-3"]


def test_classify_parsec_status_failed_flow_without_connected():
    lines = [
        "[D 2026-07-01 15:45:42] Client status changed to: 20",
        "[E 2026-07-01 15:45:43] connection failed: timeout",
    ]

    result = bridge.classify_parsec_status_lines(lines)

    assert result["state"] == "failed"
    assert result["connected"] is False
    assert result["failures"]


def test_wait_parsec_connection_status_ignores_old_lines(tmp_path):
    log_path = tmp_path / "log_cl.txt"
    log_path.write_text(
        "\n".join(
            [
                "[D 2026-07-01 15:00:00] Client status changed to: 0",
                "[D 2026-07-01 15:45:42] Client status changed to: 20",
                "[D 2026-07-01 15:45:43] Client status changed to: 0",
            ]
        ),
        encoding="utf-8",
    )

    result = bridge.wait_parsec_connection_status(
        log_path,
        offset=0,
        started_at=datetime(2026, 7, 1, 15, 45, 42),
        timeout_s=0.2,
        poll_s=0.01,
    )

    assert result["connected"] is True
    assert result["statuses"] == ["20", "0"]


def test_wait_parsec_connection_status_tail_rescan_catches_recent_lines(tmp_path):
    log_path = tmp_path / "log_cl.txt"
    text = "\n".join(
        [
            "[D 2026-07-01 15:46:48] Client status changed to: 20",
            "[D 2026-07-01 15:46:54] net           = BUD|::ffff:121.152.22.62|21637",
            "[D 2026-07-01 15:46:54] BUD AES_GCM   = 256",
            "[D 2026-07-01 15:46:54] Client status changed to: 0",
        ]
    )
    log_path.write_text(text, encoding="utf-8")

    result = bridge.wait_parsec_connection_status(
        log_path,
        offset=log_path.stat().st_size,
        started_at=datetime(2026, 7, 1, 15, 46, 48),
        timeout_s=0.1,
        poll_s=0.01,
    )

    assert result["connected"] is True
    assert result["timed_out"] is True
    assert result["statuses"] == ["20", "0"]


def test_current_status_fallback_detects_existing_connection(tmp_path):
    app_log = tmp_path / "log.txt"
    app_log.write_text(
        "\n".join(
            [
                "[I 2026-07-01 15:46:46] Client Status received: -3",
                "[I 2026-07-01 15:46:48] Client Status received: 20",
                "[I 2026-07-01 15:46:55] Client Status received: 0",
            ]
        ),
        encoding="utf-8",
    )

    result = bridge.read_current_parsec_status(
        [app_log],
        max_age_s=600,
        now=datetime(2026, 7, 1, 15, 50, 0),
    )

    assert result["connected"] is True
    assert result["method"] == "current_status"
    assert result["statuses"] == ["-3", "20", "0"]
    assert result["latest_status"] == "0"
    assert result["latest_cycle"] == ["20", "0"]


def test_wait_status_uses_current_status_fallback_when_no_new_lines(tmp_path):
    log_path = tmp_path / "log_cl.txt"
    app_log = tmp_path / "log.txt"
    log_path.write_text("", encoding="utf-8")
    app_log.write_text(
        "[I 2026-07-01 15:46:55] Client Status received: 0\n",
        encoding="utf-8",
    )

    result = bridge.wait_parsec_connection_status(
        log_path,
        offset=0,
        started_at=datetime(2026, 7, 1, 15, 50, 0),
        timeout_s=0.1,
        poll_s=0.01,
        fallback_log_paths=[app_log],
        current_status_max_age_s=60 * 60 * 24 * 365,
    )

    assert result["connected"] is True
    assert result["method"] == "current_status"
    assert "Peer identity is not verified" in result["note"]


def test_wait_parsec_disconnect_status_detects_idle(tmp_path):
    log_path = tmp_path / "log_cl.txt"
    log_path.write_text(
        "\n".join(
            [
                "[I 2026-07-01 15:45:42] Client Status received: 20",
                "[I 2026-07-01 15:45:44] Client Status received: 0",
                "[I 2026-07-01 16:02:51] Client Status received: -3",
            ]
        ),
        encoding="utf-8",
    )

    result = bridge.wait_parsec_disconnect_status(
        log_path,
        offset=0,
        started_at=datetime(2026, 7, 1, 16, 2, 50),
        timeout_s=0.2,
        poll_s=0.01,
    )

    assert result["state"] == "idle"
    assert result["connected"] is False
    assert result["timed_out"] is False


def test_json_result_preserves_korean():
    payload = bridge._json_result(ok=True, message="테스트")
    loaded = json.loads(payload)

    assert loaded == {"ok": True, "message": "테스트"}
