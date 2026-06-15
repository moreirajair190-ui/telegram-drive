"""Smoke tests da UI do player premium (offscreen).

Não reproduz mídia de verdade; valida construção da janela, overlay de
carregamento, seek bar com buffer, retomada, atalhos e salvamento de progresso.

Executar:
    QT_QPA_PLATFORM=offscreen python tests/test_player_ui.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

from tgplayer import player  # noqa: E402


class FakeService:
    def buffer_ratio(self, token):  # noqa: ANN001
        return 0.42

    def call(self, coro):  # noqa: ANN001
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    def release_stream(self, token, delete_file=True):  # noqa: ANN001
        async def _noop():
            return {}

        return _noop()


def main() -> None:
    progress: list[tuple[int, int]] = []
    vlc_calls: list[int] = []

    dlg = player.VideoPlayerDialog(
        "Aula — Cálculo I",
        "http://127.0.0.1:9/stream/t/v.mp4",
        token="t",
        service=FakeService(),
        start_position_ms=65000,
        on_progress=lambda p, d: progress.append((p, d)),
        on_open_vlc=lambda: vlc_calls.append(1),
    )
    dlg.resize(1280, 760)
    dlg.show()
    app.processEvents()
    print("[info] backend em uso:", dlg.mode)

    assert hasattr(dlg, "seek") and hasattr(dlg, "play_btn")
    assert hasattr(dlg, "loading_overlay")
    print("[ok] elementos de UID premium presentes")

    dlg._update_buffer()
    assert dlg.seek._buffered_ratio > 0.4
    print("[ok] seek bar reflete o buffer carregado")

    # Retomada (com aviso).
    dlg._duration = 600000
    dlg.seek.setRange(0, dlg._duration)
    dlg._maybe_resume()
    assert dlg._resumed and "Retomando" in dlg.toast.text()
    print("[ok] retomada na posição salva:", dlg.toast.text())

    # Tempo formatado.
    dlg.update_time_label(65000)
    assert dlg.time_label.text() == "01:05 / 10:00"
    print("[ok] rótulo de tempo:", dlg.time_label.text())

    # Volume / mudo.
    dlg._on_volume_changed(0)
    assert dlg._muted
    dlg.toggle_mute()
    assert not dlg._muted
    print("[ok] volume/mudo")

    # Botão VLC após ~6 s no overlay.
    dlg._is_ready = False
    dlg._show_loading()
    app.processEvents()
    dlg._elapsed_loading = 6.0
    dlg._update_buffer()
    app.processEvents()
    assert dlg.loading_vlc_btn.isVisible()
    print("[ok] botão 'Abrir no VLC' aparece após ~6 s")

    dlg._request_vlc()
    assert vlc_calls
    print("[ok] callback de abrir no VLC")

    # Salvamento final ao fechar.
    dlg._last_position = 123000
    dlg._last_duration = 600000
    dlg.close()
    assert progress and progress[-1] == (123000, 600000)
    print("[ok] progresso salvo ao fechar:", progress[-1])

    print("TODOS OS TESTES DE UI DO PLAYER PASSARAM")


if __name__ == "__main__":
    main()
