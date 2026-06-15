# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec do TGClassPlayer v6 (compatível com PyInstaller 6.x).

Gera um executável de pasta única (onedir) — é a forma MAIS confiável para
apps com QtWebEngine / QtMultimedia. O modo "onefile" também é possível,
mas o WebEngine costuma falhar/ficar lento; por isso o padrão aqui é onedir.

IMPORTANTE: o PyInstaller 6.x REMOVEU os argumentos antigos
``win_no_prefer_redirects``, ``win_private_assemblies``, ``cipher`` e
``block_cipher``. Este .spec NÃO os usa mais (era a causa do build falhar).

Build:
    pyinstaller --noconfirm TGClassPlayer.spec

Resultado:
    dist/TGClassPlayer/TGClassPlayer.exe
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# ---------------------------------------------------------------------------
# Coleta de dependências "pesadas" que o PyInstaller não detecta sozinho.
# ---------------------------------------------------------------------------
datas = []
binaries = []
hiddenimports = []


def _collect(pkg):
    """collect_all tolerante a falha (pacote ausente não quebra o build)."""
    global datas, binaries, hiddenimports
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass


# Pyrogram (TgCrypto é opcional — só coletamos se existir).
_collect("pyrogram")
_collect("tgcrypto")

# aiohttp.
try:
    hiddenimports += collect_submodules("aiohttp")
except Exception:
    pass

# QtWebEngine: precisa do QtWebEngineProcess + recursos (.pak, locales, ICU).
for pkg in (
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
):
    _collect(pkg)

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

# QtCharts é OPCIONAL: os gráficos da aba "Acompanhamento" usam QPainter puro
# (módulo charts.py). Se o pacote estiver instalado, coletamos; senão, ignora.
_collect("PySide6.QtCharts")

# Pacote da própria aplicação (em src/).
try:
    hiddenimports += collect_submodules("tgclassplayer")
except Exception:
    pass

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
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

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
