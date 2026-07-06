from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]


def test_pyinstaller_spec_uses_korean_fixed_branding_without_dwm_copy():
    spec = (ROOT / "WinCro.spec").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-release.yml").read_text(encoding="utf-8")

    assert "name='업무지원도구'" in spec
    assert "name='작업도우미'" not in spec
    assert 'Join-Path $appDir.FullName "dwm.exe"' not in workflow
    assert "v$version 업무지원도구" in workflow


def test_release_build_embeds_and_bundles_wincro_icon_assets():
    spec = (ROOT / "WinCro.spec").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-release.yml").read_text(encoding="utf-8")

    assert "icon='icon.ico'" in spec
    assert "('icon.ico', '.')" in spec
    assert "('icon_preview.png', '.')" in spec
    assert "pyinstaller WinCro.spec --clean --noconfirm" in workflow
    assert 'Compress-Archive -Path (Join-Path $appDir.FullName "*")' in workflow


def test_version_resource_uses_korean_fixed_branding():
    version_info = (ROOT / "version_info.txt").read_text(encoding="utf-8")

    assert "StringStruct(u'CompanyName', u'윈크로')" in version_info
    assert "StringStruct(u'FileDescription', u'업무 지원 자동화 도구')" in version_info
    assert "StringStruct(u'InternalName', u'업무지원도구')" in version_info
    assert "StringStruct(u'OriginalFilename', u'업무지원도구.exe')" in version_info
    assert "StringStruct(u'ProductName', u'업무지원도구')" in version_info
    assert "작업도우미.exe" not in version_info


def test_packaged_config_defaults_to_fixed_korean_branding():
    config = json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))

    assert config["ui"]["app_name"] == "업무지원도구"
    assert config["ui"]["random_name_mode"] is False
    assert config["ui"]["random_name_alias"] == ""


def test_packaged_custom_ctk_theme_is_bundled_and_has_startup_fallback():
    spec = (ROOT / "WinCro.spec").read_text(encoding="utf-8")
    main_window = (ROOT / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "src/ui/ctk_white_gold_theme.json" in spec
    assert "def _apply_ctk_theme() -> None:" in main_window
    assert "WHITE_GOLD_CTK_THEME.exists()" in main_window
    assert 'ctk.set_default_color_theme("blue")' in main_window
    assert "_apply_ctk_theme()" in main_window


def test_shortcut_scripts_use_wincro_icon():
    vbs = (ROOT / "create_shortcut.vbs").read_text(encoding="utf-8")
    ps1 = (ROOT / "create_shortcut.ps1").read_text(encoding="utf-8")

    assert "C:\\Projects\\wincro\\icon.ico" in vbs
    assert "C:\\Projects\\wincro\\icon.ico" in ps1
    assert "shell32.dll,76" not in vbs


def test_database_keeps_legacy_korean_name_as_fallback():
    db_manager = (ROOT / "src" / "database" / "db_manager.py").read_text(encoding="utf-8")

    assert 'DATA_DIR / "작업도우미.db"' in db_manager
    assert 'DATA_DIR / "wincro.db"' in db_manager
    assert 'PRIMARY_DB_PATH = DATA_DIR / f"{PRIMARY_PACKAGE_DIR_NAME}.db"' in db_manager
