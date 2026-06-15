#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TgPlayer v6.4 — Ponto de entrada (entry point).

Player premium de videoaulas do Telegram com streaming sob demanda
(carrega a aula sem baixar/armazenar o vídeo inteiro e sem travar).

Como rodar em modo desenvolvimento:
    pip install -r requirements.txt
    python TgPlayer.py

Como gerar o .exe (Windows):
    build_exe.bat
"""

from __future__ import annotations

import os
import sys


def _setup_path() -> None:
    """Garante que o pacote `tgplayer` seja encontrado em dev e no .exe."""
    base = os.path.dirname(os.path.abspath(__file__))

    # Quando empacotado pelo PyInstaller, os módulos ficam em sys._MEIPASS.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates = [meipass, os.path.join(meipass, "src")]
    else:
        candidates = [os.path.join(base, "src"), base]

    for path in candidates:
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def run() -> None:
    _setup_path()
    try:
        from tgplayer.app import main
    except Exception as exc:  # noqa: BLE001
        # Falha de import (dependência faltando, etc.) — mostra mensagem clara.
        msg = (
            "Não foi possível iniciar o TgPlayer.\n\n"
            f"Detalhe técnico: {exc}\n\n"
            "Se você estiver rodando o código-fonte, instale as dependências:\n"
            "    pip install -r requirements.txt"
        )
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            if QApplication.instance() is None:
                QApplication(sys.argv)
            QMessageBox.critical(None, "TgPlayer — Erro ao iniciar", msg)
        except Exception:  # noqa: BLE001
            print(msg, file=sys.stderr)
        sys.exit(1)

    main()


if __name__ == "__main__":
    run()
