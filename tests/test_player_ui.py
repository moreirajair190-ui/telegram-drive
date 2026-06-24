"""Smoke tests da UI do player embutido (QtWebEngine, offscreen).

Não reproduz mídia de verdade; valida a construção da janela, o overlay de
carregamento, a barra de seek com buffer, a retomada, atalhos e o salvamento de
progresso ao fechar.

Executar:
    QT_QPA_PLATFORM=offscreen python tests/test_player_ui.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# QtWebEngine pode não inicializar em offscreen; forçamos software para o teste.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402

try:
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
except Exception:  # noqa: BLE001
    pass

from PySide6.QtWidgets import QApplication  # noqa: E402

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
        player_url="http://127.0.0.1:9/player/t",
    )
    dlg.resize(1280, 760)
    dlg.show()
    app.processEvents()
    print("[info] modo do player:", dlg.mode)

    # Estrutura básica esperada pela integração e pela UI.
    assert hasattr(dlg, "seek") and hasattr(dlg, "play_btn")
    assert hasattr(dlg, "loading_overlay")
    assert hasattr(dlg, "toast") and hasattr(dlg, "time_label")
    print("[ok] elementos da UI presentes")

    # player_url derivada corretamente quando não informada.
    assert (
        player.VideoPlayerDialog._derive_player_url(
            "http://127.0.0.1:9/stream/abc/v.mp4"
        )
        == "http://127.0.0.1:9/player/abc"
    )
    print("[ok] derivação de player_url")

    # Buffer reflete o serviço.
    dlg._update_buffer()
    assert dlg.seek._buffered_ratio > 0.4
    print("[ok] seek bar reflete o buffer carregado")

    # Retomada (com aviso) quando a duração já é conhecida.
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

    # Botão "Abrir no VLC" aparece após ~5 s no overlay de carregamento.
    dlg._is_ready = False
    dlg._show_loading()
    app.processEvents()
    dlg._elapsed_loading = 6.0
    dlg._update_buffer()
    app.processEvents()
    assert dlg.loading_vlc_btn.isVisible()
    print("[ok] botão 'Abrir no VLC' aparece após ~5 s")

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
