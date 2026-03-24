from pathlib import Path

from src.utils import updater


def test_future_timestamp_cache_is_ignored():
    cache = {
        "timestamp": 9999999999,
        "current_version": "1.0.185",
        "result": {"update_available": False, "version": "1.0.185", "release_data": None},
    }
    assert updater._is_cache_usable(cache, "1.0.185") is False


def test_startup_update_check_uses_force_true():
    app_text = Path(r"C:\Projects\wincro\src\app.py").read_text(encoding="utf-8")
    assert "check_for_update(repo, APP_VERSION, force=True)" in app_text


def test_release_workflow_excludes_update_cache_file():
    workflow_text = Path(r"C:\Projects\wincro\.github\workflows\build-release.yml").read_text(encoding="utf-8")
    assert "update_cache.json" in workflow_text
    assert "Remove-Item $packagedCache -Force" in workflow_text
