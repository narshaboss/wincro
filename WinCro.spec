# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


def collect_packaged_data():
    """Bundle stable data assets without leaking runtime state into releases."""
    data_root = Path("data")
    excluded_top_level_dirs = {
        "recordings",
    }
    excluded_names = {
        "wincro.db",
        "업무지원도구.db",
        "작업도우미.db",
        "update_cache.json",
    }
    excluded_suffixes = (
        ".bak",
        ".bak1",
        ".bak2",
        ".bak3",
        ".tmp",
        ".log",
    )
    packaged = []
    for path in data_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(data_root)
        rel_parts = set(rel.parts)
        if rel.parts and rel.parts[0] in excluded_top_level_dirs:
            continue
        if "__pycache__" in rel_parts:
            continue
        if path.name in excluded_names:
            continue
        if path.suffix in excluded_suffixes or any(path.name.endswith(s) for s in excluded_suffixes):
            continue
        if rel.match("digit_templates/debug_region_*.png"):
            continue
        packaged.append((str(path), str(Path("data") / rel.parent)))
    return packaged

datas = [
    ('icon.ico', '.'),
    ('icon_preview.png', '.'),
    ('src/i18n', 'src/i18n'),
    ('src/ui/ctk_white_gold_theme.json', 'src/ui'),
    ('src/ui/assets', 'src/ui/assets'),
    ('arduino', 'arduino'),
]
datas += collect_packaged_data()
binaries = []
hiddenimports = []
tmp_ret = collect_all('customtkinter')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['src\\main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='업무지원도구',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='version_info.txt',
    icon='icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='업무지원도구',
)
