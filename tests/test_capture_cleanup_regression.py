from pathlib import Path

from src.ui import capture_cleanup


def test_crop_cleanup_removes_only_saved_auto_capture_source(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    templates_dir = data_dir / "templates"
    templates_dir.mkdir(parents=True)
    monkeypatch.setattr(capture_cleanup, "DATA_DIR", data_dir)

    source = templates_dir / "trigger_20260528_004813_705.png"
    crop = templates_dir / "trigger_20260528_004813_705_crop_2b6cd7.png"
    source.write_bytes(b"full")
    crop.write_bytes(b"crop")

    assert capture_cleanup.remove_auto_capture_source_after_crop(source, crop) is True
    assert not source.exists()
    assert crop.exists()


def test_crop_cleanup_keeps_source_when_crop_was_not_saved(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    templates_dir = data_dir / "templates"
    templates_dir.mkdir(parents=True)
    monkeypatch.setattr(capture_cleanup, "DATA_DIR", data_dir)

    source = templates_dir / "trigger_20260528_004813_705.png"
    missing_crop = templates_dir / "trigger_20260528_004813_705_crop_2b6cd7.png"
    source.write_bytes(b"full")

    assert capture_cleanup.remove_auto_capture_source_after_crop(source, missing_crop) is False
    assert source.exists()


def test_crop_cleanup_does_not_delete_named_template(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    templates_dir = data_dir / "templates"
    templates_dir.mkdir(parents=True)
    monkeypatch.setattr(capture_cleanup, "DATA_DIR", data_dir)

    source = templates_dir / "custom_template.png"
    crop = templates_dir / "custom_template_crop_123abc.png"
    source.write_bytes(b"template")
    crop.write_bytes(b"crop")

    assert capture_cleanup.remove_auto_capture_source_after_crop(source, crop) is False
    assert source.exists()
