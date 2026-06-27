import re
from pathlib import Path

from src.ui.virtual_scroll import VirtualScrollFrame


ROOT = Path(__file__).resolve().parents[1]
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"
RECORDER_VIEW = ROOT / "src" / "ui" / "recorder_view.py"
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
VIRTUAL_SCROLL = ROOT / "src" / "ui" / "virtual_scroll.py"
MONITORING_EDITOR = ROOT / "src" / "ui" / "monitoring_editor.py"
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
UI_BATCHER = ROOT / "src" / "ui" / "ui_batcher.py"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_virtual_scroll_supports_scroll_preservation():
    text = _read_text(VIRTUAL_SCROLL)

    assert 'def set_items(self, items: list, preserve_scroll: bool = False):' in text
    assert 'scroll_pos = self._canvas.yview()[0]' in text
    assert 'self._canvas.yview_moveto(max(0.0, min(scroll_pos, 1.0)))' in text
    assert 'if isinstance(item, tuple) and item:' in text
    assert 'getattr(obj, "plan_id", None)' in text
    assert 'getattr(obj, "id", None)' in text


def test_virtual_scroll_cancels_pending_render_on_destroy():
    text = _read_text(VIRTUAL_SCROLL)
    schedule_method = text[
        text.index("def _schedule_render(self, delay_ms: int = 0):"):
        text.index("def _do_scheduled_render(self):")
    ]
    run_method = text[
        text.index("def _do_scheduled_render(self):"):
        text.index("def _get_visible_range(self):")
    ]
    destroy_method = text[
        text.index("def destroy(self):"):
    ]

    assert "self._scroll_after_id = None" in text
    assert "self._scroll_after_id = self.after(delay, self._do_scheduled_render)" in schedule_method
    assert "self.after(10, self._do_scheduled_render)" not in schedule_method
    assert "self._scroll_after_id = None" in run_method
    assert "self.after_cancel(after_id)" in destroy_method
    assert "self._clear_all_widgets()" in destroy_method
    assert "super().destroy()" in destroy_method


def test_virtual_scroll_coalesces_high_frequency_wheel_renders():
    text = _read_text(VIRTUAL_SCROLL)
    init_method = text[
        text.index("def __init__(self, parent, item_height=75, buffer_count=6, **kwargs):"):
        text.index("def _apply_appearance_mode(self, color):")
    ]
    wheel_method = text[
        text.index("def _on_mousewheel(self, event):"):
        text.index("def _on_mousewheel_linux(self, event):")
    ]
    schedule_method = text[
        text.index("def _schedule_render(self, delay_ms: int = 0):"):
        text.index("def _do_scheduled_render(self):")
    ]

    assert "self._buffer_count = max(6, int(buffer_count or 6))" in init_method
    assert "yscrollincrement=self._scroll_unit_px" in init_method
    assert "self._wheel_render_delay_ms = 12" in init_method
    assert "self._wheel_scroll_units" in wheel_method
    assert "self._schedule_render(delay_ms=self._wheel_render_delay_ms)" in wheel_method
    assert "delay = max(0, int(delay_ms or 0))" in schedule_method
    assert "self._scroll_after_delay_ms" in schedule_method
    assert "delay < current_delay" in schedule_method


def test_virtual_scroll_accumulates_high_resolution_wheel_delta():
    frame = object.__new__(VirtualScrollFrame)
    frame._wheel_remainder = 0

    assert frame._wheel_scroll_units(40) == 0
    assert frame._wheel_scroll_units(40) == -1
    assert frame._wheel_remainder == 0
    assert frame._wheel_scroll_units(-120) == 1


def test_ui_callback_dispatcher_limits_work_by_time_budget():
    text = _read_text(UI_BATCHER)
    init_method = text[
        text.index("def __init__(\n        self,\n        widget,"):
        text.index("def post(self, callback", text.index("class UiCallbackDispatcher"))
    ]
    drain_method = text[
        text.index("def _drain(self) -> None:"):
        text.index("class BufferedRecordPump", text.index("def _drain(self) -> None:"))
    ]

    assert "import time" in text
    assert "max_millis_per_tick: float = 8.0" in init_method
    assert "self._max_millis_per_tick = max(1.0, float(max_millis_per_tick))" in init_method
    assert "max_queue_items: int = 2000" in init_method
    assert "self._max_queue_items = max(128, int(max_queue_items))" in init_method
    assert "while len(self._queue) >= self._max_queue_items:" in text
    assert "started_at = time.perf_counter()" in drain_method
    assert "processed < self._max_callbacks_per_tick" in drain_method
    assert "elapsed_ms = (time.perf_counter() - started_at) * 1000" in drain_method
    assert "elapsed_ms >= self._max_millis_per_tick" in drain_method


def test_ui_batcher_drops_old_items_when_worker_queue_is_saturated():
    from src.ui.ui_batcher import UiCallbackDispatcher

    class FakeWidget:
        def after(self, _ms, _func=None, *_args):
            return "after-id"

        def after_cancel(self, _after_id):
            return None

        def winfo_exists(self):
            return True

    dispatcher = UiCallbackDispatcher(FakeWidget(), max_queue_items=3)

    def post_from_worker():
        for index in range(140):
            dispatcher.post(lambda i=index: i)

    import threading

    worker = threading.Thread(target=post_from_worker)
    worker.start()
    worker.join()

    assert dispatcher.pending_count() == 128
    assert dispatcher.dropped_count() == 12
    dispatcher.close()


def test_buffered_record_pump_drops_old_items_when_queue_is_saturated():
    from src.ui.ui_batcher import BufferedRecordPump, UiCallbackDispatcher

    class FakeWidget:
        def after(self, _ms, _func=None, *_args):
            return "after-id"

        def after_cancel(self, _after_id):
            return None

        def winfo_exists(self):
            return True

    dispatcher = UiCallbackDispatcher(FakeWidget())
    pump = BufferedRecordPump(
        FakeWidget(),
        dispatcher,
        lambda records: None,
        max_items_per_flush=2,
        max_queue_items=4,
    )

    for index in range(10):
        pump.push(index)

    assert pump.pending_count() == 4
    assert pump.dropped_count() == 6
    pump.close()
    dispatcher.close()


def test_analyzer_view_uses_virtual_lists_and_async_apply():
    text = _read_text(ANALYZER_VIEW)
    plan_item_method = text[
        text.index("def _create_plan_item(self, plan: AutomationPlan, parent=None):"):
        text.index("def _edit_plan(self, plan: AutomationPlan):")
    ]

    assert 'from .virtual_scroll import VirtualScrollFrame' in text
    assert 'list_frame = VirtualScrollFrame(' in text
    assert 'self._plans_scroll = VirtualScrollFrame(' in text
    assert 'self._recordings_scroll = VirtualScrollFrame(' in text
    assert 'def _render_image_row(self, parent, item_data, _index: int):' in text
    assert 'def _load_recordings_async(self):' in text
    assert 'def _apply_recordings(self, recordings, generation=None):' in text
    assert 'self._plans_scroll.set_items(self._plan_items, preserve_scroll=True)' in text
    assert 'self._recordings_scroll.set_items(self._recording_items, preserve_scroll=True)' in text
    assert 'item_wrapper = ctk.CTkFrame(' in plan_item_method
    assert 'height=2' in plan_item_method
    assert 'fg_color=COLORS["accent"]' in plan_item_method
    assert "return item_wrapper" in plan_item_method


def test_recorder_view_uses_virtual_list_and_async_apply():
    text = _read_text(RECORDER_VIEW)

    assert 'from .virtual_scroll import VirtualScrollFrame' in text
    assert 'from .ui_batcher import UiCallbackDispatcher' in text
    assert 'self._recordings_scroll = VirtualScrollFrame(' in text
    assert 'def _refresh_recordings_list_async(self):' in text
    assert 'def _apply_recordings_list(self, recordings, generation=None):' in text
    assert 'self._recordings_scroll.set_items(self._recording_items, preserve_scroll=True)' in text


def test_main_window_defers_hidden_view_refreshes():
    text = _read_text(MAIN_WINDOW)

    assert 'self._dirty_views = set()' in text
    assert 'if view_id == self._current_view:' in text
    assert 'self._dirty_views.add(view_id)' in text
    assert 'def _refresh_view_if_needed(self, view_id: Optional[str]):' in text
    assert 'if view_id in self._dirty_views:' in text


def test_monitoring_editor_uses_single_image_action_flow():
    text = _read_text(MONITORING_EDITOR)

    assert "self._monitor_image" not in text
    assert "self._monitor_region" not in text
    assert "self._monitor_actions" not in text
    assert "def _build_image_card" not in text
    assert "def _build_actions_card" not in text
    assert "command=self._add_monitor_action" not in text
    assert "def _edit_monitor_action" not in text
    assert "self._route_watches" in text
    assert "class MonitorActionEditorDialog" in text
    assert "def _add_route_action" in text
    assert "def _edit_route_action" in text
    assert "복사한 일반 액션 붙여넣기" not in text
    assert "액션붙여넣기" not in text
    assert "1. 최종이미지 대기   →   2. 이동 이미지 발견" in text
    assert "이미지별 이동" not in text
    assert "모니터링 이미지 액션" in text
    assert "def _build_routes_card" in text
    assert "goto_index" in text
    assert "def _default_route_goto_index" in text
    assert "def _build_route_separator" in text
    assert "self._build_route_separator()" in text
    assert "root = ctk.CTkScrollableFrame(self" in text
    assert 'self._routes_frame = ctk.CTkFrame(card, fg_color="transparent")' in text
    assert 'self._routes_frame = ctk.CTkScrollableFrame(card' not in text
    assert 'text=f"R{idx + 1}"' not in text
    assert "condition_image" in text
    assert "def _image_quality_warning" in text
    assert "def _open_route_condition_settings" in text
    assert "조건 이미지 설정" in text
    assert "조건 인식률" in text
    assert "warning_var = tk.StringVar()" in text
    assert "조건 이미지 재캡처 필요" in text
    assert "condition_jump_when_visible" in text
    assert "jump_enabled" in text
    assert "condition_verify_image_color" in text
    assert "condition_verify_image_brightness" in text
    assert "CTkSegmentedButton" in text
    assert "안 보일 때 점프" in text
    assert "보일 때 점프" in text
    assert "색상 확인" in text
    assert "밝기 확인" in text
    assert "def _on_route_condition_jump_mode_changed" in text
    assert "def _on_route_jump_enabled_changed" in text
    assert "def _on_route_condition_confidence_changed" in text
    assert 'target == "condition"' in text
    assert "전용액션" in text
    assert "점프액션" in text
    assert 'values=["활성", "비활성"]' in text
    assert text.index("action_row = ctk.CTkFrame(inner, fg_color=\"transparent\")") < text.index("jump_row = ctk.CTkFrame(inner, fg_color=\"transparent\")")
    assert text.index("self._build_route_actions_preview(inner, idx, monitor_actions)") < text.index("jump_row = ctk.CTkFrame(inner, fg_color=\"transparent\")")
    assert "def _show_region_options" in text
    assert 'build_preset_row("a", "A영역", COLORS["accent_blue"])' in text
    assert 'build_preset_row("b", "B영역", COLORS["accent_orange"])' in text
    assert "self._expanded_route_actions" in text
    assert "def _toggle_route_actions" in text
    assert "def _build_route_actions_preview" in text
    assert "_MONITORING_SETTINGS_CLIPBOARD" in text
    assert "def _copy_monitoring_settings" in text
    assert "def _paste_monitoring_settings" in text
    assert "def _normalize_pasted_route" in text
    assert 'text="설정 복사"' in text
    assert 'text="붙여넣기"' in text
    assert "전용액션 없음: + 추가를 눌러 이 이미지가 감지됐을 때 먼저 실행할 액션을 등록하세요." in text
    assert "조건해제" in text
    assert "watch_thumb = ctk.CTkLabel(" in text
    assert "condition_thumb = ctk.CTkLabel(" in text
    assert "self._schedule_thumbnail(watch_thumb, image_path, size=(52, 38))" in text
    assert "def _build_monitor_action_thumbnail" in text
    assert 'image_path = action.get("image") if action.get("type") == "이미지 클릭" else None' in text
    assert "self._schedule_thumbnail(thumb, image_path, size=(30, 22))" in text
    assert 'height=2,' in text
    assert 'fg_color=COLORS["accent"]' in text
    assert "separator.pack_propagate(False)" in text
    assert "self._schedule_thumbnail(condition_thumb, condition_image, size=(44, 30))" in text
    assert "self._schedule_thumbnail(preview, image_path, size=(88, 64))" in text
    assert "def _refresh_watch_list" not in text
    assert "def _create_watch_item" not in text


def test_monitoring_settings_clipboard_replaces_routes_without_auto_saving():
    text = _read_text(MONITORING_EDITOR)
    editor_text = text[text.index("class MonitoringModeEditor"):]
    copy_method = editor_text[
        editor_text.index("def _copy_monitoring_settings(self) -> None:"):
        editor_text.index("def _normalize_pasted_route(self, route: dict) -> dict:")
    ]
    paste_method = editor_text[
        editor_text.index("def _paste_monitoring_settings(self) -> None:"):
        editor_text.index("def _delete_route_watch(self, idx: int) -> None:")
    ]

    assert "global _MONITORING_SETTINGS_CLIPBOARD" in copy_method
    assert "_MONITORING_SETTINGS_CLIPBOARD = self._route_clipboard_snapshot()" in copy_method
    assert '"enabled": enabled' in editor_text
    assert '"route_watches": copy.deepcopy(self._route_watches)' in editor_text
    assert "messagebox.askyesno(" in paste_method
    assert "self._normalize_pasted_route(route)" in paste_method
    assert "self._route_watches = routes" in paste_method
    assert 'self._enabled_var.set(bool(_MONITORING_SETTINGS_CLIPBOARD.get("enabled", bool(routes))))' in paste_method
    assert "self._refresh_route_list()" in paste_method
    assert "저장을 눌러 적용" in paste_method
    assert "_complete_save(" not in paste_method


def test_monitoring_editor_persists_only_compat_watch_shape():
    text = _read_text(MONITORING_EDITOR)
    editor_text = text[text.index("class MonitoringModeEditor"):]
    save_method = editor_text[editor_text.index("def _save(self) -> None:"):]

    assert "def _complete_save(self, status_text: str) -> None:" in editor_text
    assert "self._on_save = on_save" in editor_text
    assert "rule.monitoring_final_image = None" in save_method
    assert "final_image = getattr(rule, \"target_image\", None)" in save_method
    assert "rule.is_monitoring_mode = False" not in save_method[:save_method.index("if not enabled:")]
    assert "rule.target_image = self._monitor_image" not in save_method
    assert "rule.confidence = self._monitor_confidence" not in save_method
    assert "rule.search_region = copy.deepcopy(self._monitor_region)" not in save_method
    assert "rule.monitoring_final_image = final_image" in save_method
    assert "valid_watches.append(" in save_method
    assert '"image": self._monitor_image' not in save_method
    assert '"search_region": copy.deepcopy(self._monitor_region)' not in save_method
    assert '"confidence": self._monitor_confidence' not in save_method
    assert '"monitor_actions": copy.deepcopy(self._monitor_actions)' not in save_method
    assert '"goto_index": -1' not in save_method
    assert '"goto_index": goto_index' in save_method
    assert '"image": image' in save_method
    assert '"search_region": copy.deepcopy(route.get("search_region"))' in save_method
    assert '"confidence": self._safe_confidence(route.get("confidence", self._monitor_confidence))' in save_method
    assert '"jump_enabled": bool(route.get("jump_enabled", True))' in save_method
    assert '"monitor_actions": copy.deepcopy(route.get("monitor_actions", []) or [])' in save_method
    assert '"condition_image": route.get("condition_image")' in save_method
    assert '"condition_search_region": copy.deepcopy(route.get("condition_search_region"))' in save_method
    assert '"condition_confidence": self._safe_confidence(route.get("condition_confidence", 0.8))' in save_method
    assert '"condition_jump_when_visible": bool(route.get("condition_jump_when_visible", False))' in save_method
    assert '"condition_verify_image_color": bool(route.get("condition_verify_image_color", False))' in save_method
    assert '"condition_verify_image_brightness": bool(route.get("condition_verify_image_brightness", False))' in save_method
    assert "self.destroy()" not in save_method
    assert "self._complete_save(\"저장됨: 모니터링 OFF\")" in save_method
    assert "self._complete_save(\"저장됨\")" in save_method
    assert save_method.index("valid_watches = []") < save_method.index("if not valid_watches:")
    assert save_method.index("if not valid_watches:") < save_method.index("rule.is_monitoring_mode = True")
    assert "모니터링 이미지 액션을 하나 이상 설정하세요." in save_method


def test_monitoring_action_editor_exposes_normal_image_action_options():
    text = _read_text(MONITORING_EDITOR)
    dialog_text = text[
        text.index("class MonitorActionEditorDialog"):
        text.index("class MonitoringModeEditor")
    ]

    assert "이미지 클릭" in dialog_text
    assert "마우스 클릭" in dialog_text
    assert "키 입력" in dialog_text
    assert "텍스트 입력" in dialog_text
    assert "스크롤" in dialog_text
    assert "드래그" in dialog_text
    assert "색상 확인" in dialog_text
    assert "밝기 확인" in dialog_text
    assert "직각 이동" in dialog_text
    assert "사라질 때까지 반복" in dialog_text
    assert "repeat_count" in dialog_text
    assert "repeat_delay" in dialog_text
    assert "wait_after" in dialog_text
    assert "wait_random_range" in dialog_text
    assert "typing_random" in dialog_text
    assert "A영역" in dialog_text
    assert "B영역" in dialog_text
    assert "자유영역 선택" in dialog_text
    assert 'action["confidence"] = self._confidence' in dialog_text
    assert 'action["search_region"] = copy.deepcopy(self._search_region)' in dialog_text


def test_monitoring_action_key_input_uses_capture_dialog_and_filters_image_options():
    text = _read_text(MONITORING_EDITOR)
    dialog_text = text[
        text.index("class MonitorActionEditorDialog"):
        text.index("class MonitoringModeEditor")
    ]
    key_fields = dialog_text[
        dialog_text.index("def _build_key_fields(self) -> None:"):
        dialog_text.index("def _build_text_fields(self) -> None:")
    ]
    save_common = dialog_text[
        dialog_text.index("def _save_common_options(self, action: dict, action_type: str) -> None:"):
        dialog_text.index("def _save(self) -> None:")
    ]
    save_method = dialog_text[dialog_text.index("def _save(self) -> None:"):]
    summary_method = text[
        text.index("def _action_options_summary(action: dict) -> str:"):
        text.index("def _run_monitor_action_test", text.index("def _action_options_summary(action: dict) -> str:"))
    ]

    assert "from .key_input_dialog import KeyInputDialog, format_key_combo" in text
    assert "KeyInputDialog(self)" in dialog_text
    assert "def _capture_key_input(self) -> None:" in dialog_text
    assert "def _clear_key_input(self) -> None:" in dialog_text
    assert 'text="키 입력 등록"' in key_fields
    assert 'self._entry(row, "keys_text"' not in key_fields
    assert 'action["key_events"] = [dict(event) for event in self._key_events if isinstance(event, dict)]' in save_method
    assert 'if action_type == "텍스트 입력":' in save_common
    assert 'if action_type in ("이미지 클릭", "마우스 클릭"):' in save_common
    assert 'if action_type == "이미지 클릭":' in save_common
    assert '"verify_image_color"' in save_common
    assert '"verify_image_brightness"' in save_common
    assert '"click_until_image_disappears"' in save_common
    assert summary_method.index('if action_type == "이미지 클릭":') < summary_method.index('confidence = action.get("confidence")')


def test_monitoring_editor_thumbnails_do_not_decode_on_ui_thread():
    text = _read_text(MONITORING_EDITOR)
    helper = text[
        text.index("def _schedule_thumbnail(self, label, path: str, size=(56, 56)) -> None:"):
        text.index("def _show_region_options(self, target: str, idx: int | None = None) -> None:")
    ]

    assert "from .analyzer_view import get_cached_thumbnail, set_cached_thumbnail, submit_thumbnail_task" in text
    assert "monitor_thumb_v2" in helper
    assert "cv2.IMREAD_UNCHANGED" in helper
    assert "img.shape[2] == 4" in helper
    assert "alpha = img[:, :, 3:4]" in helper
    assert "submit_thumbnail_task(load_thumbnail)" in helper
    assert "ctk.CTkImage(" in helper
    assert helper.index("def load_thumbnail(") < helper.index("ctk.CTkImage(")
    assert "self.after(0, apply_thumbnail)" in helper
    assert "self._schedule_thumbnail(watch_thumb, image_path, size=(52, 38))" in text
    assert "self._schedule_thumbnail(thumb, image_path, size=(30, 22))" in text
    assert "self._schedule_thumbnail(condition_thumb, condition_image, size=(44, 30))" in text
    assert "self._schedule_thumbnail(preview, image_path, size=(88, 64))" in text


def test_monitoring_editor_reuses_fonts_in_rebuilt_action_rows():
    text = _read_text(MONITORING_EDITOR)
    editor_text = text[text.index("class MonitoringModeEditor"):]
    init_method = text[
        text.index("def __init__(self, owner, rule, plan_rules, on_save: Callable[[], bool] | None = None):", text.index("class MonitoringModeEditor")):
        text.index("def _font(self, size: int, weight: str | None = None):", text.index("class MonitoringModeEditor"))
    ]
    font_method = editor_text[
        editor_text.index("def _font(self, size: int, weight: str | None = None):"):
        editor_text.index("def _setup_dialog(self) -> None:")
    ]
    action_row = editor_text[
        editor_text.index("def _build_route_actions_preview(self, parent, route_idx: int, actions: list[dict]) -> None:"):
        editor_text.index("def _small_button(self, parent, text, color, hover, command, width=54):")
    ]

    assert "from .theme import COLORS, IOS_FONTS, IOS_METRICS" in text
    assert "self._font_cache: dict[tuple[int, str], ctk.CTkFont] = {}" in init_method
    assert 'kwargs = {"family": IOS_FONTS["family"], "size": size}' in font_method
    assert "self._font_cache[key] = cached" in font_method
    assert "font=self._font(11, \"bold\")" in action_row
    assert "font=self._font(10)" in action_row


def test_player_sequence_picker_uses_virtual_scroll_instead_of_full_rebuild():
    text = _read_text(PLAYER_VIEW)
    start = text.index("def _render_sequence_list(self):")
    end = text.index("def _setup_ui(self) -> None:")
    sequence_render = text[start:end]

    assert "self._sequence_frame = VirtualScrollFrame(" in text
    assert "self._sequence_frame.set_render_callback(self._render_sequence_list_item)" in text
    assert "self._sequence_frame.set_items(items, preserve_scroll=True)" in sequence_render
    assert "winfo_children()" not in sequence_render
    assert "widget.destroy()" not in sequence_render


def test_player_sequence_picker_reuses_fonts_for_virtual_rows():
    text = _read_text(PLAYER_VIEW)
    player_text = text[text.index("class PlayerView(BaseView):"):]
    init_method = text[
        text.index("def __init__(self, parent, **kwargs):", text.index("class PlayerView(BaseView):")):
        text.index("def after(self, ms, func=None, *args):", text.index("class PlayerView(BaseView):"))
    ]
    font_method = player_text[
        player_text.index("def _font(self, size, weight=None):"):
        player_text.index("def _begin_external_execution", player_text.index("def _font(self, size, weight=None):"))
    ]
    render_method = player_text[
        player_text.index("def _render_sequence_list_item(self, parent, item_data, _index: int):"):
        player_text.index("def _setup_ui(self) -> None:")
    ]
    plan_method = player_text[
        player_text.index("def _create_plan_item(self, plan: AutomationPlan, parent=None):"):
        player_text.index("def _rename_plan(self, plan: AutomationPlan) -> None:")
    ]
    sequence_method = player_text[
        player_text.index("def _create_sequence_item(self, sequence: Sequence, parent=None):"):
        player_text.index("def _delete_sequence(self, sequence: Sequence) -> None:")
    ]

    assert "self._font_cache = {}" in init_method
    assert 'kwargs = {"family": IOS_FONTS["family"], "size": size}' in font_method
    assert "self._font_cache[key] = cached" in font_method
    assert "font=self._font(12, \"bold\")" in render_method
    assert "font=self._font(12)" in render_method
    assert 'item_wrapper = ctk.CTkFrame(parent or self._sequence_frame, fg_color="transparent")' in plan_method
    assert 'fg_color=COLORS["accent"]' in plan_method
    assert "return item_wrapper" in plan_method
    assert "font=self._font(13, \"bold\")" in plan_method
    assert "font=self._font(11)" in plan_method
    assert 'item_wrapper = ctk.CTkFrame(parent or self._sequence_frame, fg_color="transparent")' in sequence_method
    assert 'fg_color=COLORS["accent"]' in sequence_method
    assert "return item_wrapper" in sequence_method
    assert "font=self._font(13, \"bold\")" in sequence_method
    assert "font=self._font(11)" in sequence_method


def test_player_view_avoids_reentrant_tk_update_calls():
    text = _read_text(PLAYER_VIEW)

    assert not re.search(r"\bself\.update\(\)", text)


def test_player_view_skips_duplicate_high_frequency_progress_updates():
    text = _read_text(PLAYER_VIEW)
    player_view_text = text[text.index("class PlayerView(BaseView):"):]
    init_method = text[
        text.index("def __init__(self, parent, **kwargs):", text.index("class PlayerView(BaseView):")):
        text.index("def after(self, ms, func=None, *args):", text.index("class PlayerView(BaseView):"))
    ]
    plan_method = player_view_text[
        player_view_text.index("def _update_plan_progress(self, current: int, total: int, message: str) -> None:"):
        player_view_text.index("def _on_plan_complete(self, success: bool, message: str) -> None:")
    ]
    playback_method = player_view_text[
        player_view_text.index("def _update_progress_snapshot(self, current_step: int, total_steps: int, progress_percent: float) -> None:"):
        player_view_text.index("def _on_action_start(self, index: int, action: Action) -> None:")
    ]
    action_method = player_view_text[
        player_view_text.index("def _flush_action_text_update(self) -> None:"):
        player_view_text.index("def _deferred_load(self):")
    ]

    assert "self._last_plan_progress_snapshot = None" in init_method
    assert "self._last_playback_progress_snapshot = None" in init_method
    assert "self._last_action_text = None" in init_method
    assert "if snapshot == self._last_plan_progress_snapshot:" in plan_method
    assert "if message != self._last_action_text:" in plan_method
    assert "if snapshot == self._last_playback_progress_snapshot:" in playback_method
    assert "if text == self._last_action_text:" in action_method
