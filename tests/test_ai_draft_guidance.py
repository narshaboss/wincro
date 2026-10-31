"""Beginner guidance and connection truthfulness without a desktop or network."""

import copy

import pytest

from src.agent_bridge.contracts import BridgeError, digest
from src.agent_bridge.service import DraftService
from src.agent_bridge.transport import ConnectionLease, LocalHost
from src.ui.ai_draft_guidance import (HELP_TEXT, ROLE_TEXT, authoring_request, friendly_error,
                                     parse_exclusions, readable_action, readable_analysis, review_progress)


def state():
    plan = {"name": "초안", "initial_rules": [{"rule_id": "rule_a", "action_type": "hotkey",
            "description": "선택 확인", "action_keys": ["enter"], "children": []}]}
    hashed = digest(plan)
    return {"plan": plan, "analysis": {"goal": "확인하기", "plan_digest": hashed, "open_questions": [],
            "steps": [{"rule_id": "rule_a", "observation": "확인 창이 보임", "purpose": "선택 확인",
                       "expected_result": "다음 화면", "seconds": 1}]},
            "crosschecks": [{"direction": direction, "passed": True, "plan_digest": hashed}
                            for direction in ("video_to_plan", "plan_to_video")]}


def test_roles_and_usage_never_claim_an_embedded_or_automatic_gpt():
    assert "GPT" in ROLE_TEXT and "WinCro" in ROLE_TEXT
    assert "모델이 내장된 것은 아닙니다" in HELP_TEXT
    assert "자동 전송하지 않습니다" in HELP_TEXT
    assert "12초" in HELP_TEXT
    assert "영상 근거 없이" in HELP_TEXT


@pytest.mark.parametrize("raw,expected", [("", []), ("0-5,12-18", [[0, 5], [12, 18]]), (" 1.5 - 2.6 ", [[1.5, 2.6]])])
def test_optional_excluded_intervals(raw, expected):
    assert parse_exclusions(raw) == expected


@pytest.mark.parametrize("raw", ["-1-5", "0-0", "8-2", "NaN-5", "0-inf", "1-2-3", "abc", "0-"])
def test_bad_private_intervals_are_not_silently_accepted(raw):
    with pytest.raises(ValueError):
        parse_exclusions(raw)


def test_readable_analysis_and_action_hide_raw_json_by_default():
    current = state()
    text = readable_analysis(current)
    assert "분석 담당: GPT" in text and "동작 목적: 선택 확인" in text
    assert '"rule_id"' not in text
    assert "입력 키: ENTER" in readable_action(current["plan"]["initial_rules"][0])
    assert "실제 인식·재생 성공을 증명하지는 않습니다" in text


def test_registration_guidance_matches_the_service_review_gate():
    current = state()
    assert review_progress(current)[1] == DraftService._crosscheck_ready(current) is True
    for change in ("stale", "order", "failed", "one", "question", "dirty", "special", "published"):
        changed = copy.deepcopy(current)
        if change == "stale":
            changed["plan"]["name"] = "수정한 초안"
        elif change == "order":
            changed["crosschecks"].reverse()
        elif change == "failed":
            changed["crosschecks"][-1]["passed"] = False
        elif change == "one":
            changed["crosschecks"].pop()
        elif change == "question":
            changed["analysis"]["open_questions"] = ["언제 종료하나요?"]
        elif change == "special":
            changed["plan"]["initial_rules"][0]["action_type"] = "game_mode"
        elif change == "published":
            changed["publication"] = {"plan_id": "plan_a"}
        assert not review_progress(changed, dirty=change == "dirty")[1], change
    assert not review_progress(None)[1]


def test_reference_only_request_cannot_manufacture_video_evidence():
    request = authoring_request("플랜 검토", "plan_a", has_media=False)
    assert "녹화 없음" in request and "영상 검수를 완료한 것처럼 보고하지 마" in request
    assert "자동 실행 없이 초안만" in request
    assert "영상 → 플랜, 플랜 → 영상" in request and "반복 대조" in request
    assert "list_recordings" in authoring_request("제작", has_media=True)


def test_error_message_has_a_next_step_and_retains_the_diagnostic_code():
    assert "입력은 보관" in friendly_error("REVISION_CONFLICT: read latest")
    assert "성공 처리하지 않았습니다" in friendly_error("SAVE_FAILED: write denied")
    assert "(REVISION_CONFLICT)" in friendly_error("REVISION_CONFLICT: read latest")
    assert "오류 상세" in friendly_error("UnexpectedFailure: details")


def test_connection_requires_a_real_client_signal_and_expires():
    now = [0.0]
    lease = ConnectionLease(clock=lambda: now[0])
    assert not lease.connected
    lease.heartbeat("a" * 32)
    assert lease.connected
    now[0] = 11.99
    assert lease.connected
    now[0] = 12
    assert not lease.connected
    lease.heartbeat("b" * 32)
    lease.disconnect("a" * 32)
    assert lease.connected
    lease.disconnect("b" * 32)
    assert not lease.connected


@pytest.mark.parametrize("session_id", [None, "", "secret", "g" * 32, ["a" * 32]])
def test_invalid_heartbeat_never_sets_on(session_id):
    lease = ConnectionLease()
    with pytest.raises(BridgeError):
        lease.heartbeat(session_id)
    assert not lease.connected


def test_client_sessions_are_bounded_and_disconnect_cannot_clear_another_session():
    lease = ConnectionLease()
    for index in range(4):
        lease.heartbeat(f"{index:032x}")
    with pytest.raises(BridgeError):
        lease.heartbeat("f" * 32)
    lease.disconnect("not-the-current-session")
    assert lease.connected


def test_descriptor_delete_failure_still_revokes_the_client_and_does_not_trap_gui_shutdown():
    from threading import Event
    from types import SimpleNamespace
    host = object.__new__(LocalHost)
    host.stop = Event()
    host.thread = SimpleNamespace(is_alive=lambda: True)
    host.lease = ConnectionLease()
    host.lease.heartbeat("a" * 32)

    def locked(**_):
        raise PermissionError("simulated descriptor lock")

    host.connection_file = SimpleNamespace(unlink=locked)
    assert host.client_connected
    assert host.close() is False
    assert host.cleanup_error == "PermissionError"
    assert host.stop.is_set() and not host.client_connected


def test_recording_workspace_is_in_recording_tab_and_model_execution_stays_explicit():
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    settings = (root / "src/ui/settings_view.py").read_text(encoding="utf-8-sig")
    source = (root / "src/ui/recorder_view.py").read_text(encoding="utf-8-sig")
    assert "_open_ai_draft_editor" not in settings
    assert "GPT로 플랜 만들기" not in settings
    tree = ast.parse(source)
    recorder = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RecorderView")
    methods = {node.name: node for node in recorder.body if isinstance(node, ast.FunctionDef)}
    assert any(isinstance(node, ast.ImportFrom) and node.module == "recording_workspace" for node in ast.walk(methods["_setup_ui"]))
    assert not any(isinstance(node, ast.ImportFrom) and node.module and "agent_bridge" in node.module for node in tree.body)
    assert "_setup_status_card" not in methods and "_setup_settings_card" not in methods
    assert "CodexRunner(" not in ast.get_source_segment(source, methods["_setup_ui"])
