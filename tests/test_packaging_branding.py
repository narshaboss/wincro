from pathlib import Path
import json
import ast
import re


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


def test_pyinstaller_spec_filters_runtime_data_from_release_bundle():
    spec = (ROOT / "WinCro.spec").read_text(encoding="utf-8")

    assert "def collect_packaged_data()" in spec
    assert "('data', 'data')" not in spec
    assert '"wincro.db"' in spec
    assert '"update_cache.json"' in spec
    assert 'rel.match("digit_templates/debug_region_*.png")' in spec
    assert '".bak1"' in spec


def test_pyinstaller_data_collector_excludes_runtime_files_when_executed():
    spec_path = ROOT / "WinCro.spec"
    spec_tree = ast.parse(spec_path.read_text(encoding="utf-8"))
    collect_def = next(
        node for node in spec_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "collect_packaged_data"
    )
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=[collect_def], type_ignores=[]), str(spec_path), "exec"), namespace)

    collected = namespace["collect_packaged_data"]()
    collected_sources = {Path(src).as_posix() for src, _dest in collected}

    assert "data/update_cache.json" not in collected_sources
    assert "data/wincro.db" not in collected_sources
    assert "data/digit_templates/debug_region_1.png" not in collected_sources
    assert not any(path.endswith(".bak1") for path in collected_sources)
    assert any(path.startswith("data/plans/") and path.endswith(".json") for path in collected_sources)


def test_version_resource_uses_korean_fixed_branding():
    version_info = (ROOT / "version_info.txt").read_text(encoding="utf-8")

    assert "StringStruct(u'CompanyName', u'윈크로')" in version_info
    assert "StringStruct(u'FileDescription', u'업무 지원 자동화 도구')" in version_info
    assert "StringStruct(u'InternalName', u'업무지원도구')" in version_info
    assert "StringStruct(u'OriginalFilename', u'업무지원도구.exe')" in version_info
    assert "StringStruct(u'ProductName', u'업무지원도구')" in version_info
    assert "작업도우미.exe" not in version_info


def test_release_versions_are_synchronized():
    init_text = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")
    config_text = (ROOT / "src" / "utils" / "config.py").read_text(encoding="utf-8")
    version_info = (ROOT / "version_info.txt").read_text(encoding="utf-8")

    package_version = re.search(r'__version__ = "([^"]+)"', init_text).group(1)
    app_version = re.search(r'APP_VERSION = "([^"]+)"', config_text).group(1)
    assert package_version == app_version

    version_parts = tuple(int(part) for part in app_version.split("."))
    expected_tuple = version_parts + (0,) * (4 - len(version_parts))
    expected_string = ".".join(str(part) for part in expected_tuple)

    for key in ("filevers", "prodvers"):
        match = re.search(rf"{key}=\(([^)]*)\)", version_info)
        assert match is not None
        actual_tuple = tuple(int(part.strip()) for part in match.group(1).split(","))
        assert actual_tuple == expected_tuple

    for key in ("FileVersion", "ProductVersion"):
        match = re.search(rf"StringStruct\(u'{key}', u'([^']+)'\)", version_info)
        assert match is not None
        assert match.group(1) == expected_string


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
