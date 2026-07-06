from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
RULE_EXECUTOR = ROOT / "src" / "player" / "rule_executor.py"
MONITORING_EDITOR = ROOT / "src" / "ui" / "monitoring_editor.py"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_rule_executor_matches_video_templates_by_sampled_frames():
    text = _read_text(RULE_EXECUTOR)
    find_start = text.index("def _find_image_on_screen(")
    find_end = text.index("def _wait_for_trigger(", find_start)
    find_method = text[find_start:find_end]

    assert "_VIDEO_TEMPLATE_EXTS" in text
    assert "_get_cached_video_template_frames" in text
    assert "return _get_cached_video_template_frames(image_path)" in text
    assert "template_variants = _get_cached_template_variants(image_path)" in find_method
    assert "for tmpl_gray, th, tw, template_bgr, variant_label in template_variants" in find_method
    assert "variant_label" in find_method


def test_monitoring_condition_dialog_accepts_video_and_tests_variants():
    text = _read_text(MONITORING_EDITOR)
    dialog_start = text.index("def _open_route_condition_settings")
    dialog_end = text.index("def _test_route_condition_image", dialog_start)
    dialog_method = text[dialog_start:dialog_end]
    match_start = text.index("def _match_condition_image_for_test")
    match_end = text.index("def _condition_verify_label", match_start)
    match_method = text[match_start:match_end]

    assert "def _select_route_condition_video" in text
    assert "VIDEO_FILE_PATTERNS" in text
    assert "동영상 입력" in dialog_method
    assert "조건 이미지/동영상" in dialog_method
    assert "_get_cached_template_variants(image_path)" in match_method
    assert "for template_gray, _template_h, _template_w, template_bgr, variant_label in template_variants" in match_method
    assert '"variant": str(variant_label or "")' in match_method


def test_monitoring_dedicated_action_menu_accepts_video_input():
    editor_text = _read_text(MONITORING_EDITOR)
    dialog_text = editor_text[
        editor_text.index("class MonitorActionEditorDialog"):
        editor_text.index("class MonitoringModeEditor")
    ]
    executor_text = _read_text(RULE_EXECUTOR)
    execute_start = executor_text.index("def _execute_monitor_action(")
    execute_end = executor_text.index("def _execute_monitor_image_click_until_disappears", execute_start)
    execute_method = executor_text[execute_start:execute_end]

    assert 'VIDEO_CLICK_TYPE = "동영상클릭"' in dialog_text
    assert 'LEGACY_VIDEO_CLICK_TYPE = "동영상 입력"' in dialog_text
    assert 'RANDOM_KEY_TYPE = "랜덤키 입력"' in dialog_text
    assert 'ACTION_TYPES = ("이미지 클릭", VIDEO_CLICK_TYPE, "마우스 클릭", "키 입력", RANDOM_KEY_TYPE, "텍스트 입력", "스크롤", "드래그")' in dialog_text
    assert 'MEDIA_CLICK_TYPES = ("이미지 클릭", VIDEO_CLICK_TYPE, LEGACY_VIDEO_CLICK_TYPE)' in dialog_text
    assert 'title="전용액션 동영상 선택" if is_video_action else "전용액션 이미지 선택"' in dialog_text
    assert 'filetypes=[("동영상 파일", VIDEO_FILE_PATTERNS), ("모든 파일", "*.*")] if is_video_action else' in dialog_text
    assert 'prefix="monitor_action_video" if is_video_action else "monitor_action"' in dialog_text
    assert 'text="영상 테스트"' in dialog_text
    assert "def _test_video_template(self) -> None:" in dialog_text
    assert "elif action_type in {'이미지 클릭', '동영상클릭', '동영상 입력'}:" in execute_method
    assert 'media_label = "동영상" if action_type in {"동영상클릭", "동영상 입력"} else "이미지"' in execute_method


def test_media_path_helpers_ignore_empty_values():
    from src.ui.analyzer_view import is_supported_media_path, is_video_media_path

    assert is_video_media_path(None) is False
    assert is_video_media_path("") is False
    assert is_supported_media_path(None) is False
    assert is_supported_media_path("") is False


def test_monitoring_route_rows_render_without_condition_image():
    try:
        import customtkinter as ctk
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"customtkinter unavailable: {exc}")

    from src.ui.monitoring_editor import MonitoringModeEditor

    try:
        root = ctk.CTk()
    except Exception as exc:  # pragma: no cover - display guard
        pytest.skip(f"tk display unavailable: {exc}")

    root.withdraw()
    try:
        plan_rules = [
            SimpleNamespace(
                action_type="click",
                description="다이쇼 시작",
                enabled=True,
                children=[],
                rule_id="rule_1",
            )
        ]
        watch = {
            "image": "C:/Projects/wincro/data/templates/not_exists.png",
            "images": [{"image": "C:/Projects/wincro/data/templates/not_exists.png", "priority": 1}],
            "goto_index": 0,
            "goto_rule_id": "rule_1",
            "jump_enabled": True,
            "pre_jump_recheck": True,
            "monitor_actions": [{"type": "키 입력", "keys": ["esc"], "repeat_count": 1}],
            "condition_image": None,
        }
        rule = SimpleNamespace(
            is_monitoring_mode=True,
            confidence=0.8,
            monitoring_watches=[dict(watch) for _ in range(3)],
        )

        dialog = MonitoringModeEditor(root, rule, plan_rules)
        try:
            for _ in range(20):
                root.update()
            assert len(dialog._route_watches) == 3
            assert len(dialog._route_slots) == 3
            assert len(dialog._routes_frame.winfo_children()) == 3
        finally:
            dialog.destroy()
    finally:
        root.destroy()
