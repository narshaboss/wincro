from pathlib import Path

from src.ui.text_overflow import truncate_ui_text


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src" / "ui" / "main_window.py"
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"
SETTINGS_VIEW = ROOT / "src" / "ui" / "settings_view.py"
MONITORING_EDITOR = ROOT / "src" / "ui" / "monitoring_editor.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _method_slice(text: str, start: str, end: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[start_index:end_index]


def test_truncate_ui_text_keeps_single_line_and_caps_length():
    assert truncate_ui_text("short", 10) == "short"
    assert truncate_ui_text("abc\ndef", 20) == "abc def"
    result = truncate_ui_text("x" * 40, 12)
    assert result.endswith("...")
    assert len(result) <= 12


def test_play_mode_active_bar_truncates_dynamic_group_and_plan_names():
    text = _read(MAIN_WINDOW)
    method = text[
        text.index("def _mini_update_active_bar("):
        text.index("def _toggle_mini_auto_update_from_indicator(", text.index("def _mini_update_active_bar("))
    ]
    mini_slice = text[
        text.index("def _create_mini_player_ui(self):"):
        text.index("def _refresh_mini_plans_sync(self):")
    ]

    assert "from .text_overflow import truncate_ui_text" in text
    assert "display_names = [truncate_ui_text(name, 34) for name in display_names]" in method
    assert "detail = truncate_ui_text(detail, 82)" in method
    assert "width=280" in mini_slice


def test_player_view_truncates_playlist_selection_and_current_action_labels():
    text = _read(PLAYER_VIEW)

    assert "text=truncate_ui_text(plan.name, 38)" in text
    assert "text=truncate_ui_text(sequence.name, 38)" in text
    assert "truncate_ui_text(self._sequence.name, 42)" in text
    assert "self._current_action_label.configure(text=truncate_ui_text(message, 90))" in text
    assert "text=f\" - {truncate_ui_text(action.description, 42)}\"" in text


def test_analyzer_and_monitoring_views_truncate_file_and_plan_names():
    analyzer_text = _read(ANALYZER_VIEW)
    monitoring_text = _read(MONITORING_EDITOR)

    assert "text=f\"📁 {truncate_ui_text(filename, 58)}\"" in analyzer_text
    assert "text=truncate_ui_text(plan.name, 38)" in analyzer_text
    assert "text=truncate_ui_text(recording.name, 38)" in analyzer_text
    assert "text=truncate_ui_text(\"  |  \".join(details), 76)" in analyzer_text
    assert "truncate_ui_text(image_name, 42)" in monitoring_text
    assert "truncate_ui_text(self._action_detail(action), 28)" in monitoring_text
    assert "truncate_ui_text(self._action_options_summary(action), 38)" in monitoring_text


def test_settings_auto_run_group_editor_truncates_visible_names_only():
    text = _read(SETTINGS_VIEW)

    assert "from .text_overflow import truncate_ui_text" in text
    assert "truncate_ui_text(group.get('name', '그룹'), 26)" in text
    assert "truncate_ui_text(group.get('name', '그룹'), 32)" in text
    assert "truncate_ui_text(self._seq_plan_name(plan_path), 34)" in text


def test_edit_dialog_action_toolbar_uses_v236_header_position():
    text = _read(PLAYER_VIEW)

    assert text.count('btn_container = ctk.CTkFrame(header, fg_color="transparent")') >= 2
    assert text.count('btn_container.pack(side="right", padx=(5, 10))') >= 2
    assert text.count('btn_row1.pack(fill="x", pady=(0, 3))') >= 2


def test_compact_action_rows_keep_v236_single_line_button_positions():
    text = _read(PLAYER_VIEW)

    assert "primary_controls = ctk.CTkFrame(controls_frame" not in text
    assert "secondary_controls = ctk.CTkFrame(controls_frame" not in text
    assert text.count(').pack(side="right", padx=3, pady=8)') >= 4
    assert "ACTION_COMPACT_THUMB_FRAME_SIZE = 52" in text
    assert "ACTION_COMPACT_THUMB_IMAGE_SIZE = 44" in text
    assert text.count('["thumb_size"] = ACTION_COMPACT_THUMB_IMAGE_SIZE') >= 2


def test_action_row_buttons_are_reserved_before_long_text_labels():
    text = _read(PLAYER_VIEW)
    plan_compact = _method_slice(
        text,
        "def _create_compact_rule_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\"):",
        "def _create_action_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\", use_pack: bool = True):",
    )
    plan_full = _method_slice(
        text,
        "def _create_action_item(self, parent, rule: AutomationRule, depth: int = 0, index_str: str = \"1\", use_pack: bool = True):",
        "def _on_drag_start(self, event, rule: AutomationRule, widget):",
    )
    sequence_compact = _method_slice(
        text,
        "def _create_compact_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
        "def _create_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
    )
    sequence_full = _method_slice(
        text,
        "def _create_action_item(self, parent, action: Action, depth: int = 0, index_str: str = \"1\", before_widget=None, use_pack: bool = True):",
        "def _ensure_action_children_rendered(self, action_id) -> bool:",
    )

    for method in (plan_compact, sequence_compact):
        assert method.index("control_frame.pack(side=\"right\"") < method.index("name_label = ctk.CTkLabel(")
        assert "width=1," in method
        assert "repeat_btn = ctk.CTkButton(\n            control_frame," in method
        assert "delay_btn = ctk.CTkButton(\n            control_frame," in method

    for method in (plan_full, sequence_full):
        assert method.index("btn_frame.pack(side=\"right\"") < method.index("# 정보 영역")
        assert "width=1,\n            anchor=\"w\"," in method


def test_image_crop_dialog_splits_primary_and_option_buttons():
    text = _read(ANALYZER_VIEW)

    assert "bottom_panel = ctk.CTkFrame(" in text
    assert "name_frame.grid_columnconfigure(1, weight=1)" in text
    assert "primary_btn_row = ctk.CTkFrame(btn_frame" in text
    assert "option_btn_row = ctk.CTkFrame(btn_frame" in text
    assert "primary_btn_row.pack(anchor=\"center\", pady=(0, 6))" in text
    assert "option_btn_row.pack(anchor=\"center\")" in text
    assert "self._save_btn.pack(side=\"left\", padx=5)" in text
    assert "self._save_btn = ctk.CTkButton(\n            primary_btn_row," in text
    assert "self._alt_image_btn = ctk.CTkButton(\n                option_btn_row," in text
    assert "self._move_mouse_cb = ctk.CTkCheckBox(\n                option_btn_row," in text
