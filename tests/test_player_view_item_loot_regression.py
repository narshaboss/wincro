from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAYER_VIEW = ROOT / "src" / "ui" / "player_view.py"


def _read() -> str:
    return PLAYER_VIEW.read_text(encoding="utf-8-sig")


def test_waypoint_card_includes_item_image_controls():
    text = _read()
    card_slice = text[
        text.index("add_item_img_btn = ctk.CTkButton("):
        text.index("# 위젯 참조 저장 (모든 버튼 포함)")
    ]

    assert 'text="아이템"' in card_slice
    assert "self._add_item_image(idx)" in card_slice
    assert "item_img_name = \"\"" in card_slice
    assert "self._edit_item_image(idx)" in card_slice
    assert "self._delete_item_image(idx)" in card_slice


def test_waypoint_item_image_storage_and_picker_support_exists():
    text = _read()
    helper_slice = text[
        text.index("def _get_waypoint_image_path"):
        text.index("def _delete_arrival_keys")
    ]

    assert "if kind == 'item':" in helper_slice
    assert "return str(wp[3].get('item_image', '') or '')" in helper_slice
    assert "img_cfg['item_image'] = image_path" in helper_slice
    assert "wp[3].pop('item_image', None)" in helper_slice
    assert "'item': '아이템'" in helper_slice
    assert "'item': 'wp_item'" in helper_slice
    assert "def _add_item_image(self, idx: int):" in helper_slice
    assert "def _edit_item_image(self, idx: int):" in helper_slice
    assert "def _delete_item_image(self, idx: int):" in helper_slice


def test_item_test_ui_and_runtime_helpers_exist():
    text = _read()

    assert "아이템 이미지: 없음" in text
    assert 'text="아이템테스트"' in text
    assert "def _itemtest_select(self):" in text
    assert "def _itemtest_run(self):" in text
    assert "def _detect_item_template(self, screen: np.ndarray, item_img_path: str):" in text
    assert "def _run_item_loot_sequence(" in text
    assert "def _run_boss_item_and_arrival_flow(" in text
    assert "🎁 아이템 탐색 시작" in text
    assert "🎁 아이템 루팅 완료" in text
    assert "🎁 아이템 없음" in text
    assert "def _press_escape_twice_before_arrival_keys(self, stop_event=None) -> None:" in text


def test_boss_completion_paths_use_item_flow_before_arrival_keys():
    text = _read()

    assert text.count("_run_boss_item_and_arrival_flow(") >= 2
    assert "allow_item_loot=False" in text
    assert "self._handle_escape_hotkey" in text


def test_item_loot_sequence_orders_z_then_double_click_then_loot_key():
    text = _read()
    func_slice = text[
        text.index("def _run_item_loot_sequence("):
        text.index("def _run_boss_item_and_arrival_flow(")
    ]

    z_idx = func_slice.index('get_input_controller().press("z")')
    dbl_idx = func_slice.index("get_input_controller().double_click")
    loot_idx = func_slice.index('get_input_controller().press(",")')

    assert z_idx < dbl_idx < loot_idx


def test_item_test_button_uses_item_loot_state_machine():
    text = _read()
    itemtest_slice = text[
        text.index("def _itemtest_run(self):"):
        text.index("def _bosstest_release_key(self):")
    ]

    assert "self._run_item_loot_sequence(" in itemtest_slice
    assert 'log_prefix="[아이템테스트]"' in itemtest_slice
    assert "initial_wait_s=1.2" in itemtest_slice
    assert "no_item_timeout_s=8.0" in itemtest_slice


def test_boss_dungeon_has_passive_item_detection_helpers():
    text = _read()

    assert "def _reset_boss_item_passive_state(self) -> None:" in text
    assert "def _has_recent_boss_item_hint(self, *, max_age_s: float = 6.0) -> bool:" in text
    assert "def _passive_detect_boss_dungeon_item(self, screen: np.ndarray, item_img_path: str, *, boss_mode: str = \"\") -> bool:" in text
    assert "👀 아이템 후보 감지" in text


def test_boss_dungeon_loop_runs_passive_item_detection_and_reuses_hint():
    text = _read()

    assert "_boss_item_img_path = self._get_waypoint_image_path(target_idx, \"item\")" in text
    assert "iteration % 4 == 0" in text
    assert "self._passive_detect_boss_dungeon_item(" in text
    assert "_passive_item_hint = self._has_recent_boss_item_hint(max_age_s=6.0)" in text
    assert "_loot_initial_wait = 0.35 if _passive_item_hint else 1.2" in text
    assert "_loot_timeout = 10.0 if _passive_item_hint else 8.0" in text
    assert "🎁 아이템 탐색 시작\" + (\" (상시탐지 힌트)\"" in text


def test_boss_dungeon_item_detection_keeps_hint_only_until_kill_confirm():
    text = _read()

    assert "_boss_item_force_loot = False" in text
    assert "🎁 아이템 후보 감지" in text
    assert "boss-item-passive-hint" in text
    assert "🎁 아이템 감지 → 즉시 루팅 전환" not in text
    assert "boss-item-force-loot" not in text
    assert "_boss_item_force_loot or (" not in text


def test_item_loot_sequence_always_uses_z_before_click():
    text = _read()
    helper_slice = text[
        text.index("def _is_loot_candidate_plausible(*, tile_dist, pixel_dist) -> bool:"):
        text.index("def _should_use_loot_z(*, tile_dist, pixel_dist) -> bool:")
    ]
    z_slice = text[
        text.index("def _should_use_loot_z(*, tile_dist, pixel_dist) -> bool:"):
        text.index("def _did_loot_vector_progress(")
    ]

    assert "return True" in helper_slice
    assert "return True" in z_slice


def test_item_loot_sequence_has_settle_delays_after_z_and_click():
    text = _read()
    func_slice = text[
        text.index("def _run_item_loot_sequence("):
        text.index("def _run_boss_item_and_arrival_flow(")
    ]

    assert "_loot_z_settle_s = 0.35" in func_slice
    assert "_loot_click_settle_s = 0.15" in func_slice
    assert "action_cooldown_until = _now + _loot_z_settle_s" in func_slice
    assert "time.sleep(_loot_click_settle_s)" in func_slice


def test_item_loot_sequence_does_not_fail_hard_when_z_progress_is_small():
    text = _read()
    func_slice = text[
        text.index("def _run_item_loot_sequence("):
        text.index("def _run_boss_item_and_arrival_flow(")
    ]

    assert "아이템 접근: z 후 거리 변화 미미 → 클릭 시도 유지" in func_slice
    assert "아이템 접근 실패: z 후 진전 없음" not in func_slice
    assert "아이템 후보 무시: 거리 과다" not in func_slice
    assert "아이템 접근 실패: z 후 거리 과다" not in func_slice
