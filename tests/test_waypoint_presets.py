from pathlib import Path

from src.utils import waypoint_presets as wp


def test_arrival_key_preset_roundtrip(tmp_path, monkeypatch):
    preset_file = tmp_path / "waypoint_presets.json"
    monkeypatch.setattr(wp, "PRESET_FILE", preset_file)

    wp.upsert_arrival_key_preset(
        "default_keys",
        [{"key": "f1", "wait_after": 0.3, "repeat_count": 1}],
    )

    presets = wp.list_arrival_key_presets()
    assert len(presets) == 1
    assert presets[0]["name"] == "default_keys"
    assert presets[0]["keys"][0]["key"] == "f1"

    wp.remove_arrival_key_preset("default_keys")
    assert wp.list_arrival_key_presets() == []


def test_image_preset_filters_missing_files(tmp_path, monkeypatch):
    preset_file = tmp_path / "waypoint_presets.json"
    image_file = tmp_path / "boss.png"
    item_file = tmp_path / "item.png"
    image_file.write_bytes(b"fake")
    item_file.write_bytes(b"fake")
    monkeypatch.setattr(wp, "PRESET_FILE", preset_file)

    wp.upsert_image_preset("boss", "boss_a", str(image_file))
    wp.upsert_image_preset("character", "char_missing", str(tmp_path / "missing.png"))
    wp.upsert_image_preset("item", "item_a", str(item_file))

    boss = wp.list_image_presets("boss")
    character = wp.list_image_presets("character")
    item = wp.list_image_presets("item")

    assert boss == [{"name": "boss_a", "path": str(image_file)}]
    assert character == []
    assert item == [{"name": "item_a", "path": str(item_file)}]


def test_image_preset_path_lookup_falls_back_to_same_file_fingerprint(tmp_path, monkeypatch):
    preset_file = tmp_path / "waypoint_presets.json"
    image_a = tmp_path / "boss_a.png"
    image_b = tmp_path / "boss_b.png"
    image_a.write_bytes(b"same-boss-template")
    image_b.write_bytes(b"same-boss-template")
    monkeypatch.setattr(wp, "PRESET_FILE", preset_file)

    wp.upsert_image_preset("boss", "boss_a", str(image_a), region=[1, 2, 3, 4], ocr_text="원각")

    preset = wp.get_image_preset("boss", path=str(image_b))

    assert preset is not None
    assert preset["name"] == "boss_a"
    assert preset["path"] == str(image_a)
    assert preset["region"] == [1, 2, 3, 4]
    assert preset["ocr_text"] == "원각"


def test_image_preset_persists_confidence(tmp_path, monkeypatch):
    preset_file = tmp_path / "waypoint_presets.json"
    image_file = tmp_path / "boss.png"
    image_file.write_bytes(b"boss")
    monkeypatch.setattr(wp, "PRESET_FILE", preset_file)

    wp.upsert_image_preset("boss", "boss_a", str(image_file), confidence=0.7, region=[1, 2, 3, 4])
    preset = wp.get_image_preset("boss", path=str(image_file))

    assert preset is not None
    assert preset["confidence"] == 0.7

    wp.set_image_preset_confidence("boss", path=str(image_file), confidence=0.72)
    preset = wp.get_image_preset("boss", path=str(image_file))
    assert preset["confidence"] == 0.72


def test_image_preset_remove_roundtrip(tmp_path, monkeypatch):
    preset_file = tmp_path / "waypoint_presets.json"
    image_file = tmp_path / "boss.png"
    image_file.write_bytes(b"fake")
    monkeypatch.setattr(wp, "PRESET_FILE", preset_file)

    wp.upsert_image_preset("boss", "boss_a", str(image_file))
    assert wp.list_image_presets("boss") == [{"name": "boss_a", "path": str(image_file)}]

    wp.remove_image_preset("boss", "boss_a")

    assert wp.list_image_presets("boss") == []


def test_item_image_preset_remove_roundtrip(tmp_path, monkeypatch):
    preset_file = tmp_path / "waypoint_presets.json"
    image_file = tmp_path / "item.png"
    image_file.write_bytes(b"fake")
    monkeypatch.setattr(wp, "PRESET_FILE", preset_file)

    wp.upsert_image_preset("item", "item_a", str(image_file))
    assert wp.list_image_presets("item") == [{"name": "item_a", "path": str(image_file)}]

    wp.remove_image_preset("item", "item_a")

    assert wp.list_image_presets("item") == []


def test_save_waypoint_presets_sanitizes_invalid_entries(tmp_path, monkeypatch):
    preset_file = tmp_path / "waypoint_presets.json"
    monkeypatch.setattr(wp, "PRESET_FILE", preset_file)

    wp.save_waypoint_presets(
        {
            wp.ARRIVAL_KEY_PRESETS: [
                {"name": "ok", "keys": [{"key": "f2"}]},
                {"name": "", "keys": [{"key": "f3"}]},
                {"name": "bad", "keys": []},
            ],
            wp.BOSS_IMAGE_PRESETS: [
                {"name": "boss", "path": "C:/x.png"},
                {"name": "boss2", "path": ""},
            ],
            wp.CHARACTER_IMAGE_PRESETS: [
                {"name": "char", "path": "C:/y.png"},
                {"oops": "broken"},
            ],
            wp.ITEM_IMAGE_PRESETS: [
                {"name": "item", "path": "C:/z.png"},
                {"name": "item2", "path": ""},
            ],
        }
    )

    data = wp.load_waypoint_presets()
    assert data[wp.ARRIVAL_KEY_PRESETS] == [{"name": "ok", "keys": [{"key": "f2"}]}]
    assert data[wp.BOSS_IMAGE_PRESETS] == [{"name": "boss", "path": "C:/x.png"}]
    assert data[wp.CHARACTER_IMAGE_PRESETS] == [{"name": "char", "path": "C:/y.png"}]
    assert data[wp.ITEM_IMAGE_PRESETS] == [{"name": "item", "path": "C:/z.png"}]


def test_arrival_key_preset_order_is_append_based(tmp_path, monkeypatch):
    preset_file = tmp_path / "waypoint_presets.json"
    monkeypatch.setattr(wp, "PRESET_FILE", preset_file)

    wp.upsert_arrival_key_preset("6", [{"key": "6"}])
    wp.upsert_arrival_key_preset("2", [{"key": "2"}])
    wp.upsert_arrival_key_preset("ENTER", [{"key": "enter"}])

    presets = wp.list_arrival_key_presets()
    assert [item["name"] for item in presets] == ["6", "2", "ENTER"]


def test_arrival_key_dialog_is_reduced_to_add_and_delete_controls():
    player_view = Path(__file__).resolve().parents[1] / "src" / "ui" / "player_view.py"
    text = player_view.read_text(encoding="utf-8-sig")
    dialog_slice = text[
        text.index('keys_list = wp_cfg.get("arrival_keys", [])'):
        text.index("def _format_coord_region(self, region) -> str:")
    ]

    assert "키추가" in dialog_slice
    assert "키삭제" in dialog_slice
    assert 'text="적용"' in dialog_slice
    assert "toggle_preset_apply" in dialog_slice
    assert "list_arrival_key_presets()" in dialog_slice
    assert "upsert_arrival_key_preset" in dialog_slice
    assert "remove_arrival_key_preset" in dialog_slice
    assert "저장된 키가 없습니다. 아래에서 추가하세요." in dialog_slice
    assert "VirtualScrollFrame(" in dialog_slice
    assert "CTkScrollableFrame(dlg" not in dialog_slice
    assert "_key_preset_name" in dialog_slice
    assert "save_all" not in dialog_slice


def test_image_dialog_uses_saved_presets_with_row_apply_controls():
    player_view = Path(__file__).resolve().parents[1] / "src" / "ui" / "player_view.py"
    text = player_view.read_text(encoding="utf-8-sig")
    dialog_slice = text[
        text.index("def _open_image_preset_picker"):
        text.index("def _delete_waypoint_image")
    ]

    assert "list_image_presets" in dialog_slice
    assert "upsert_image_preset" in dialog_slice
    assert "remove_image_preset" in dialog_slice
    assert "def add_image():" in dialog_slice
    assert "def delete_image():" in dialog_slice
    assert "_copy_waypoint_image_to_templates" in dialog_slice
    assert "active =" in dialog_slice
    assert "toggle_apply" in dialog_slice
    assert "이미지등록" in dialog_slice
    assert "이미지삭제" in dialog_slice
    assert "취소" in dialog_slice
    assert "저장된 이미지가 없습니다. 아래에서 추가하세요." in dialog_slice
    assert "logger.error(f'[좌표모드] {dialog_title} 등록 실패: {e}')" in dialog_slice
    assert "def _open_waypoint_image_picker" in dialog_slice


def test_bosstest_and_itemtest_image_selection_use_saved_preset_picker():
    player_view = Path(__file__).resolve().parents[1] / "src" / "ui" / "player_view.py"
    text = player_view.read_text(encoding="utf-8-sig")
    boss_item_slice = text[
        text.index("def _bosstest_select(self, kind: str):"):
        text.index("def _bosstest_run(self):")
    ]

    assert "self._open_image_preset_picker(" in boss_item_slice
    assert 'dialog_title="보스 이미지 선택" if kind == "boss" else "캐릭터 이미지 선택"' in boss_item_slice
    assert 'dialog_title="아이템 이미지 선택"' in boss_item_slice
    assert 'self._bosstest_boss_label.configure(text=f"보스 이미지: {Path(_resolved_path).name}")' in boss_item_slice
    assert 'self._bosstest_char_label.configure(text=f"캐릭터 이미지: {Path(_resolved_path).name}")' in boss_item_slice
    assert 'self._itemtest_item_label.configure(text=f"아이템 이미지: {Path(path).name}")' in boss_item_slice


def test_waypoint_card_status_row_includes_image_thumbnails():
    player_view = Path(__file__).resolve().parents[1] / "src" / "ui" / "player_view.py"
    text = player_view.read_text(encoding="utf-8-sig")
    card_slice = text[
        text.index("row5 = ctk.CTkFrame(card"):
        text.index("# 위젯 참조 저장 (모든 버튼 포함)")
    ]

    assert "char_img_thumb = tk.Label" in card_slice
    assert "boss_img_thumb = tk.Label" in card_slice
    assert "_set_status_thumb(char_img_thumb" in card_slice
    assert "_set_status_thumb(boss_img_thumb" in card_slice
    assert "'char_img_thumb'" in text
    assert "'boss_img_thumb'" in text


def test_load_thumb_keeps_label_side_reference():
    player_view = Path(__file__).resolve().parents[1] / "src" / "ui" / "player_view.py"
    text = player_view.read_text(encoding="utf-8-sig")
    load_thumb_slice = text[
        text.index("def _load_thumb(self, path: str, label: ctk.CTkLabel, size: int = 80):"):
        text.index("def _select_auto_skill_cd_image")
    ]

    assert "label._thumb_ref = ctk_img" in load_thumb_slice
    assert "label._thumb_ref = None" in load_thumb_slice
    assert "label._ctk_image = ctk_img" in load_thumb_slice
    assert "label._ctk_image = None" in load_thumb_slice


def test_image_popup_rows_use_tk_labels_for_small_thumbs():
    player_view = Path(__file__).resolve().parents[1] / "src" / "ui" / "player_view.py"
    text = player_view.read_text(encoding="utf-8-sig")
    dialog_slice = text[
        text.index("def _open_image_preset_picker"):
        text.index("def _delete_waypoint_image")
    ]

    assert "thumb = tk.Label(" in dialog_slice
