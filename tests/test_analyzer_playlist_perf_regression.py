from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYZER_VIEW = ROOT / "src" / "ui" / "analyzer_view.py"


def _read_text() -> str:
    return ANALYZER_VIEW.read_text(encoding="utf-8")


def _method_slice(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


def test_playlist_dialog_item_collapse_does_not_rebuild_full_action_list():
    text = _read_text()
    method = _method_slice(
        text,
        "def _toggle_item_collapse(self, rule_id: str):",
        "def _refresh_action_list(self",
    )

    assert "self._refresh_action_list()" not in method
    assert "self._refresh_action_list(preserve_scroll=True)" in method
    assert "self._apply_rule_collapse_state(rule_id)" not in method
    assert "self._action_widgets.get(rule_id)" not in method


def test_playlist_dialog_expand_all_keeps_nested_children_lazy():
    text = _read_text()
    method = _method_slice(
        text,
        "def _toggle_all_collapse(self):",
        "def _iter_top_level_collapsible_rules(self):",
    )
    helper = _method_slice(
        text,
        "def _iter_top_level_collapsible_rules(self):",
        "def _toggle_item_collapse(self, rule_id: str):",
    )

    assert "self._collapsed_items.clear()" not in method
    assert "for rule in self._iter_top_level_collapsible_rules():" in method
    assert "self._collapsed_items.discard(rule.rule_id)" in method
    assert "self._plan.initial_rules" in helper
    assert "self._plan.monitoring_rules" in helper
    assert "yield rule" in helper


def test_playlist_dialog_uses_lazy_child_containers_for_fast_expand():
    text = _read_text()

    assert "self._action_widgets = {}" in text
    assert "self._collapsible_rule_ids = set()" in text
    assert '"children_rendered": False' in text
    assert '"children_container"' in text
    assert "def _ensure_children_rendered(self, rule_id: str) -> None:" in text
    assert "container.pack_forget()" in text
    assert 'container.pack(fill="x")' in text
    ensure_method = _method_slice(
        text,
        "def _ensure_children_rendered(self, rule_id: str) -> None:",
        "def _apply_rule_collapse_state(self, rule_id: str) -> None:",
    )
    create_method = _method_slice(
        text,
        "def _create_action_item(",
        "def _display_thumbnail(self, parent, rule: AutomationRule):",
    )

    assert "self._render_rule_children_batch(" in ensure_method
    assert "widget_data.get(\"children_rendering\")" in ensure_method
    assert "self._ensure_children_rendered(rule.rule_id)" in create_method
    assert "self._render_rules(children_container" not in create_method


def test_playlist_dialog_uses_virtual_scroll_for_action_rows():
    text = _read_text()
    setup_method = _method_slice(
        text,
        "def _setup_ui(self):",
        "def _count_all_rules(self, rules) -> int:",
    )
    refresh_method = _method_slice(
        text,
        "def _refresh_action_list(self",
        "def _cancel_action_list_render_batch(self):",
    )
    render_method = _method_slice(
        text,
        "def _render_virtual_action_item(self, parent, item_data, _index: int):",
        "def _on_virtual_action_item_destroyed(self, item_data, _index: int, widget) -> None:",
    )

    assert "self._scrollable = VirtualScrollFrame(" in setup_method
    assert "self._scrollable.set_render_callback(self._render_virtual_action_item)" in setup_method
    assert "self._scrollable.set_destroy_callback(self._on_virtual_action_item_destroyed)" in setup_method
    assert "self._cancel_action_list_render_batch()" in refresh_method
    assert "is_virtual = isinstance(self._scrollable, VirtualScrollFrame)" in refresh_method
    assert "if is_virtual and not preserve_scroll:" in refresh_method
    assert "self._scrollable.set_items([], preserve_scroll=False)" in refresh_method
    assert "if not is_virtual or not preserve_scroll:" in refresh_method
    assert "self._action_widgets = {}" in refresh_method
    assert "self._collapsible_rule_ids.clear()" in refresh_method
    assert "self._render_rules(" not in refresh_method
    assert "items = self._build_visible_rule_render_items()" in refresh_method
    assert "self._scrollable.set_items(items, preserve_scroll=preserve_scroll)" in refresh_method
    assert "manage_geometry=False" in render_method
    assert "render_inline_children=False" in render_method


def test_playlist_dialog_virtual_items_flatten_only_visible_children():
    text = _read_text()
    collect_method = _method_slice(
        text,
        "def _collect_visible_rule_items(",
        "def _render_virtual_action_item(self, parent, item_data, _index: int):",
    )

    assert '"rule": rule' in collect_method
    assert '"depth": depth' in collect_method
    assert '"index_label": label' in collect_method
    assert '"parent_id": parent_id' in collect_method
    assert "self._collapsible_rule_ids.add(rule.rule_id)" in collect_method
    assert "if rule.rule_id not in self._collapsed_items:" in collect_method
    assert "self._collect_visible_rule_items(" in collect_method


def test_playlist_dialog_render_batch_is_cancelled_on_cleanup():
    text = _read_text()
    cancel_method = _method_slice(
        text,
        "def _cancel_action_list_render_batch(self):",
        "def _build_root_rule_render_items(self):",
    )
    cleanup_method = _method_slice(
        text,
        "def _cleanup_resources(self):",
        "def _is_valid_image_path(self, path: str) -> bool:",
    )

    assert "self._render_batch_generation += 1" in cancel_method
    assert "after_ids = set(getattr(self, \"_render_batch_after_ids\", set()))" in cancel_method
    assert "self.after_cancel(after_id)" in cancel_method
    assert "self._cancel_action_list_render_batch()" in cleanup_method


def test_playlist_dialog_expands_large_children_in_batches():
    text = _read_text()
    child_batch_method = _method_slice(
        text,
        "def _render_rule_children_batch(self, rule_id: str, parent, items, start: int, generation: int, batch_size: Optional[int] = None):",
        "def _render_rules(self, parent, rules, depth=0, prefix: str = \"\"):",
    )

    assert "generation != self._render_batch_generation" in child_batch_method
    assert "widget_data.get(\"children_rendering\")" not in child_batch_method
    assert "widget_data[\"children_rendered\"] = True" in child_batch_method
    assert "widget_data[\"children_rendering\"] = False" in child_batch_method
    assert "self._schedule_action_list_render_batch(" in child_batch_method


def test_image_crop_dialog_arrow_keys_move_crop_selection():
    text = _read_text()
    key_method = _method_slice(
        text,
        "def _on_crop_arrow_key(self, event):",
        "def _move_crop_selection(self, dx: int, dy: int) -> bool:",
    )
    move_method = _method_slice(
        text,
        "def _move_crop_selection(self, dx: int, dy: int) -> bool:",
        "def _setup_ui(self):",
    )
    set_method = _method_slice(
        text,
        "def _set_crop_selection(self, coords: tuple[int, int, int, int], *, refresh_mask: bool = True):",
        "def _refresh_preview(self):",
    )
    save_method = _method_slice(
        text,
        "def _save_crop(self):",
        "def _delete_image(self):",
    )

    assert "self._bind_crop_keyboard_controls()" in text
    assert "def _focus_crop_canvas(self):" in text
    assert "def _is_full_image_crop_selection(self) -> bool:" in text
    assert "takefocus=1" in text
    assert "self.after(100, self._focus_crop_canvas)" in text
    assert "선택 후 방향키=1px 이동, Shift+방향키=10px 이동" in text
    assert 'self._canvas.bind("<Left>", self._on_crop_arrow_key)' in text
    assert 'self._canvas.bind("<Shift-Right>", self._on_crop_arrow_key)' in text
    assert 'step = 10 if (getattr(event, "state", 0) & 0x0001) else 1' in key_method
    assert "self._is_full_image_crop_selection()" in key_method
    assert '"Left": (-step, 0)' in key_method
    assert '"Down": (0, step)' in key_method
    assert "self._move_crop_selection(dx, dy)" in key_method
    assert "self._set_crop_selection(new_coords, refresh_mask=False)" in move_method
    assert "new_x1 = max(0, min(max_x1, x1 + int(dx)))" in move_method
    assert "new_y1 = max(0, min(max_y1, y1 + int(dy)))" in move_method
    assert "self._crop_mask_needs_refresh = True" in set_method
    assert "def _ensure_current_crop_mask(self, *, refresh_view: bool = False):" in set_method
    assert "self._ensure_current_crop_mask()" in save_method


def test_image_crop_dialog_navigation_skips_missing_files_and_refocuses_canvas():
    text = _read_text()
    nav_method = _method_slice(
        text,
        "def _navigate_image(self, direction: int):",
        "def _update_nav_buttons(self):",
    )

    assert "step = 1 if direction > 0 else -1" in nav_method
    assert "while True:" in nav_method
    assert "not Path(new_image_path).exists()" in nav_method
    assert "continue" in nav_method
    assert "logger.warning" in nav_method
    assert "self.after(10, self._focus_crop_canvas)" in nav_method


def test_image_crop_dialog_navigation_keeps_current_rule_settings_connected():
    text = _read_text()
    setup_method = _method_slice(
        text,
        "def _setup_ui(self):",
        "def _update_canvas_image(self):",
    )
    nav_method = _method_slice(
        text,
        "def _navigate_image(self, direction: int):",
        "def _update_nav_buttons(self):",
    )
    crop_method = _method_slice(
        text,
        "def _save_crop(self):",
        "def _delete_image(self):",
    )
    change_method = _method_slice(
        text,
        "def _change_image(self):",
        "def _invoke_image_callback(self, callback, *args):",
    )

    assert "if self._rule is not None and hasattr(self._rule, 'target_images')" in setup_method
    assert "def _refresh_rule_setting_controls(self):" in text
    assert "new_rule = item" in nav_method
    assert "self._refresh_rule_setting_controls()" in nav_method
    assert "old_path = self._set_current_rule_image(str(new_path))" in crop_method
    assert "self._invoke_image_callback(self._on_crop, str(new_path), self._rule, old_path)" in crop_method
    assert "old_path = self._set_current_rule_image(str(dest_path))" in change_method
    assert "self._invoke_image_callback(self._on_change, str(dest_path), self._rule, old_path)" in change_method


def test_analyzer_result_image_editor_passes_rule_list_and_setting_callbacks():
    text = _read_text()
    method = _method_slice(
        text,
        "def _open_image_editor(self, image_path: str, rule: AutomationRule):",
        "def _on_approve(self):",
    )

    assert "all_image_rules = []" in method
    assert "collect(self._plan.initial_rules)" in method
    assert "collect(self._plan.monitoring_rules)" in method
    assert "rule=rule" in method
    assert "image_list=all_image_rules" in method
    assert "current_index=current_index" in method
    assert "on_search_radius_change=on_search_radius_change" in method
    assert "for rule_id in changed_rule_ids:" in method
    assert "self._refresh_rule_row(rule_id)" in method
    assert "if not refreshed:" not in method
    assert "self._refresh_action_list()" not in method


def test_analyzer_playlist_dialog_can_refresh_single_rule_row():
    text = _read_text()
    helper_method = _method_slice(
        text,
        "def _refresh_rule_row(self, rule_id: str) -> bool:",
        "def _create_action_item(",
    )
    create_method = _method_slice(
        text,
        "def _create_action_item(",
        "def _display_thumbnail(self, parent, rule: AutomationRule):",
    )

    assert "self._scrollable.find_item_index_by_object_id(rule_id, \"AutomationRule\")" in helper_method
    assert "self._scrollable.refresh_item(index)" in helper_method
    assert "self._drop_rule_widget_mappings(rule)" in helper_method
    assert "wrapper.destroy()" in helper_method
    assert "before_widget=before_widget" in helper_method
    assert 'item_wrapper.pack(fill="x", before=before_widget)' in create_method
    assert "manage_geometry: bool = True" in create_method
    assert "render_inline_children: bool = True" in create_method


def test_analyzer_playlist_thumbnails_load_off_ui_thread():
    text = _read_text()
    method = _method_slice(
        text,
        "def _display_thumbnail(self, parent, rule: AutomationRule):",
        "def _display_thumbnail_sync_legacy(self, parent, rule: AutomationRule):",
    )

    assert "placeholder = ctk.CTkLabel(" in method
    assert "def load_thumbnail(" in method
    assert "submit_thumbnail_task(load_thumbnail)" in method
    assert "threading.Thread(target=load_thumbnail" not in method
    assert "self.after(0, apply_thumbnail)" in method
    assert "ctk.CTkImage(" in method
    assert method.index("def load_thumbnail(") < method.index("ctk.CTkImage(")


def test_analyzer_playlist_thumbnails_use_bounded_daemon_worker_queue():
    text = _read_text()
    module_setup = text[:text.index("def get_cached_thumbnail(image_path: str, size: tuple):")]

    assert "import queue" in module_setup
    assert "_THUMBNAIL_WORKER_COUNT = 4" in module_setup
    assert "def submit_thumbnail_task(task):" in text
    assert "target=_thumbnail_worker_loop" in text
    assert "daemon=True" in text
    assert "_thumbnail_task_queue.put(task)" in text


def test_image_confidence_dialog_supports_left_right_arrow_adjustment():
    text = _read_text()
    method = _method_slice(
        text,
        "def _set_confidence(self):",
        "def _navigate_image(self, direction: int):",
    )

    assert "def adjust_confidence(delta: int):" in method
    assert "conf_var.set(max(30, min(95, current + delta)))" in method
    assert 'if key == "Left":' in method
    assert "adjust_confidence(-1)" in method
    assert 'if key == "Right":' in method
    assert "adjust_confidence(1)" in method
    assert 'conf_slider.bind("<Left>", on_conf_key)' in method
    assert 'conf_slider.bind("<Right>", on_conf_key)' in method
    assert 'conf_dialog.bind("<Left>", on_conf_key)' in method
    assert 'conf_dialog.bind("<Right>", on_conf_key)' in method
    assert "conf_dialog.after(100, conf_slider.focus_set)" in method
