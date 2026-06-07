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


def test_database_keeps_legacy_korean_name_as_fallback():
    db_manager = (ROOT / "src" / "database" / "db_manager.py").read_text(encoding="utf-8")

    assert 'DATA_DIR / "작업도우미.db"' in db_manager
    assert 'DATA_DIR / "wincro.db"' in db_manager
    assert 'PRIMARY_DB_PATH = DATA_DIR / f"{PRIMARY_PACKAGE_DIR_NAME}.db"' in db_manager
