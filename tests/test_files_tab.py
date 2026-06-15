"""Smoke tests da aba 🗂️ Arquivos (classificação de mídia + UI offscreen).

Executar:
    QT_QPA_PLATFORM=offscreen python tests/test_files_tab.py
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tgplayer.telegram_service import TelegramService  # noqa: E402


def _msg(**kwargs):
    """Cria uma mensagem-falsa com atributos arbitrários."""
    m = types.SimpleNamespace(
        id=kwargs.pop("id", 1),
        caption=kwargs.pop("caption", ""),
        date=None,
        video=None,
        animation=None,
        photo=None,
        audio=None,
        voice=None,
        document=None,
    )
    for key, value in kwargs.items():
        setattr(m, key, value)
    return m


def _media(**kwargs):
    base = dict(
        file_name=None, mime_type=None, file_size=None,
        duration=None, width=None, height=None, thumbs=None,
    )
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def test_classify_media() -> None:
    classify = TelegramService._classify_media

    # Vídeo nativo.
    v = classify(_msg(id=10, video=_media(file_size=1000, duration=12, thumbs=[1])))
    assert v and v["kind"] == "video" and v["has_thumb"] is True

    # PDF como documento.
    p = classify(_msg(id=11, document=_media(
        file_name="aula.pdf", mime_type="application/pdf", file_size=500)))
    assert p and p["kind"] == "pdf"

    # Imagem (foto).
    img = classify(_msg(id=12, photo=_media(file_size=200)))
    assert img and img["kind"] == "image" and img["has_thumb"] is True

    # Zip por extensão.
    z = classify(_msg(id=13, document=_media(file_name="material.zip", file_size=900)))
    assert z and z["kind"] == "zip"

    # Áudio por mime.
    a = classify(_msg(id=14, audio=_media(file_name="a.mp3", mime_type="audio/mpeg")))
    assert a and a["kind"] == "audio"

    # Texto puro -> sem mídia.
    assert classify(_msg(id=15)) is None
    print("[ok] classificação de mídia (vídeo/pdf/imagem/zip/áudio/none)")


def test_files_tab_filters() -> None:
    from PySide6.QtWidgets import QApplication

    from tgplayer.files_tab import FilesTab

    app = QApplication.instance() or QApplication(sys.argv)

    class FakeDB:
        def list_courses(self):
            return []

    class FakeService:
        client = None

        def cached_thumb(self, *_a):
            return None

        def telegram_message_link(self, username, mid):
            return f"https://t.me/{username}/{mid}" if username else None

    tab = FilesTab(FakeDB(), FakeService(), lambda: None)
    tab._items = [
        {"kind": "video", "file_name": "Aula 1.mp4", "caption": "",
         "size": 100, "chat_id": "1", "message_id": 1, "has_thumb": False},
        {"kind": "pdf", "file_name": "resumo.pdf", "caption": "estudo",
         "size": 50, "chat_id": "1", "message_id": 2, "has_thumb": False},
        {"kind": "image", "file_name": "foto.jpg", "caption": "",
         "size": 30, "chat_id": "1", "message_id": 3, "has_thumb": True},
    ]

    tab._apply_local_filter()
    assert tab.grid.count() == 3, "Tudo deve mostrar 3 itens"

    tab._set_filter("pdf")
    assert tab.grid.count() == 1, "Filtro PDF deve mostrar 1 item"

    tab._set_filter("all")
    tab.search.setText("aula")
    tab._apply_local_filter()
    assert tab.grid.count() == 1, "Busca 'aula' deve mostrar 1 item"

    print("[ok] FilesTab: filtros por tipo + busca")
    _ = app  # mantém referência


if __name__ == "__main__":
    test_classify_media()
    test_files_tab_filters()
    print("TODOS OS TESTES DA ABA ARQUIVOS PASSARAM")
