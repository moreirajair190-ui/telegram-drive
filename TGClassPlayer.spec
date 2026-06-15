# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec do TGClassPlayer v5.

Gera um executável de pasta única (onedir) — é a forma MAIS confiável para
apps com QtWebEngine (o player HTML5). O modo "onefile" também é possível,
mas o WebEngine costuma falhar/ficar lento; por isso o padrão aqui é onedir.

Build:
    pyinstaller --noconfirm TGClassPlayer.spec

Resultado:
    dist/TGClassPlayer/TGClassPlayer.exe
"""

import os
import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)

block_cipher = None

# ---------------------------------------------------------------------------
# Coleta de dependências "pesadas" que o PyInstaller não detecta sozinho.
# ---------------------------------------------------------------------------
datas = []
binaries = []
hiddenimports = []

# Pyrogram + TgCrypto: muitos submódulos carregados dinamicamente.
for pkg in ("pyrogram", "tgcrypto"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# aiohttp.
hiddenimports += collect_submodules("aiohttp")

# QtWebEngine: precisa do QtWebEngineProcess + recursos (.pak, locales, ICU).
# collect_all garante que o processo auxiliar e os dados venham junto.
for pkg in (
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# Submódulos PySide6 usados explicitamente.
hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtNetwork",
]

# Pacote da própria aplicação (em src/).
hiddenimports += collect_submodules("tgclassplayer")

# Ícone opcional (assets/icon.ico).
icon_path = os.path.join("assets", "icon.ico")
if not os.path.exists(icon_path):
    icon_path = None


a = Analysis(
    ["TGClassPlayer.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "PIL",
        "pytest",
        "PySide6.QtQuick3D",
        "PySide6.QtDataVisualization",
        "PySide6.QtCharts",
        "PySide6.Qt3DCore",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TGClassPlayer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,            # app de janela (sem terminal preto)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TGClassPlayer",
)
