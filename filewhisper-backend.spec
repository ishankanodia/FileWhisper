# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ("static", "static"),
    (".env.example", "."),
]
datas += collect_data_files("sentence_transformers")

hiddenimports = [
    "sentence_transformers",
    "transformers",
    "torch",
    "tokenizers",
]
hiddenimports += collect_submodules("langgraph")
hiddenimports += collect_submodules("langchain_core")


a = Analysis(
    ["server_launcher.py"],
    pathex=[],
    binaries=[],
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
    a.binaries,
    a.datas,
    [],
    name="filewhisper-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
