import logging
import urllib.error
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


def test_auto_update_does_not_restore_old_plan_playlists():
    app_text = Path(r"C:\Projects\wincro\src\app.py").read_text(encoding="utf-8")

    assert 'xcopy /E /I /Y /Q "{data_backup}\\\\plans\\\\*"' not in app_text
    assert "plans_user_backup" in app_text


def test_transient_github_http_error_is_warning_not_error(monkeypatch, caplog):
    def raise_http_error(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            url="https://api.github.com/repos/test/repo/releases/latest",
            code=504,
            msg="Gateway Timeout",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(updater, "_urlopen_with_fallback", raise_http_error)

    with caplog.at_level(logging.WARNING):
        result = updater.check_for_update("test/repo", "1.0.0", force=True)

    assert result is None
    assert any("GitHub API 일시 오류: 504" in record.message for record in caplog.records)
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)
