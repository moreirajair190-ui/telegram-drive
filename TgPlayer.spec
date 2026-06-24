# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec do TgPlayer v6.4.15 (compatível com PyInstaller 6.x).

Gera um executável de pasta única (onedir) — é a forma MAIS confiável para
apps PySide6. O modo "onefile" também é possível, mas o padrão aqui é onedir por ser mais confiável.

IMPORTANTE: o PyInstaller 6.x REMOVEU os argumentos antigos
``win_no_prefer_redirects``, ``win_private_assemblies``, ``cipher`` e
``block_cipher``. Este .spec NÃO os usa mais (era a causa do build falhar).

Build:
    pyinstaller --noconfirm TgPlayer.spec

Resultado:
    dist/TgPlayer/TgPlayer.exe
"""

import os
from pathlib import Path

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

# python-vlc (OPCIONAL): só é coletado se estiver instalado. O VLC local é a
# SEGUNDA opção de reprodução (a principal é abrir a aula no Telegram).
_collect("vlc")

# aiohttp.
try:
    hiddenimports += collect_submodules("aiohttp")
except Exception:
    pass

# Submódulos PySide6 usados explicitamente.
# v6.6: o player embutido (QtWebEngine) foi REMOVIDO. A reprodução agora é feita
# abrindo a aula no Telegram instalado (Desktop/64Gram/oficial) e, como segunda
# opção, no VLC local. Por isso NÃO coletamos mais os módulos QtWebEngine*,
# deixando o executável bem menor.
hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
]

# QtCharts é OPCIONAL: os gráficos da aba "Acompanhamento" usam QPainter puro
# (módulo charts.py). Se o pacote estiver instalado, coletamos; senão, ignora.
_collect("PySide6.QtCharts")

# O pacote tgplayer é descoberto a partir dos imports reais de TgPlayer.py/app.py.
# Não usamos collect_submodules("tgplayer") para não embutir o player local legado
# e módulos QtMultimedia/QtWebEngine que não são mais usados pela interface.

# Ícone e assets visuais.
icon_path = os.path.join("assets", "icon.ico")
if not os.path.exists(icon_path):
    icon_path = None
try:
    for asset in Path("assets").glob("*"):
        if asset.is_file():
            datas.append((str(asset), "assets"))
except Exception:
    pass


a = Analysis(
    ["TgPlayer.py"],
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
        # v6.6: player embutido removido — QtWebEngine e QtMultimedia ficam
        # FORA do build (executável menor). A reprodução usa Telegram + VLC.
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TgPlayer",
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
    name="TgPlayer",
)
