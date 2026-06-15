"""Backend de reprodução com **libVLC embarcado** (python-vlc) — OPCIONAL.

Por que existe (Opção 3 do plano de aceleração):
- O VLC inicia a reprodução de streams HTTP MUITO mais rápido que o
  QMediaPlayer, porque tem um demuxer/buffer de rede próprio e robusto.
- Aqui usamos o **libVLC dentro da NOSSA janela** (via ``set_hwnd`` no Windows,
  ``set_nsobject`` no macOS e ``set_xwindow`` no Linux), em vez de abrir o
  programa VLC externo. Assim ganhamos a velocidade do VLC sem sair do app.

É **totalmente opcional**: se ``python-vlc`` (e a libVLC do sistema) não
estiverem instalados, ``is_available()`` retorna ``False`` e o player cai
automaticamente para o QMediaPlayer (codecs nativos do SO).

Este módulo expõe apenas o necessário para o ``VideoPlayerDialog`` controlar a
mídia (play/pause/seek/posição/velocidade/volume) com uma interface enxuta e
parecida com a do QMediaPlayer, de modo que o restante do player não precise
saber qual backend está em uso.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

try:
    import vlc  # type: ignore

    _HAS_VLC = True
except Exception:  # noqa: BLE001
    vlc = None  # type: ignore
    _HAS_VLC = False


def is_available() -> bool:
    """True se python-vlc + libVLC estiverem realmente utilizáveis."""
    if not _HAS_VLC:
        return False
    try:
        # Tenta criar uma instância mínima: confirma que a libVLC carrega.
        inst = vlc.Instance("--no-video-title-show")
        if inst is None:
            return False
        inst.release()
        return True
    except Exception:  # noqa: BLE001
        return False


class VlcBackend:
    """Encapsula um ``MediaPlayer`` da libVLC ligado a um widget nativo.

    Métodos espelham (de forma simplificada) o QMediaPlayer:
    - ``play`` / ``pause`` / ``stop`` / ``is_playing``
    - ``set_position_ms`` / ``position_ms`` / ``duration_ms``
    - ``set_rate`` / ``set_volume``
    - ``bind_widget`` para incorporar o vídeo na janela
    - ``set_callbacks`` para receber eventos de erro/fim/pronto
    """

    def __init__(self) -> None:
        if not _HAS_VLC:
            raise RuntimeError("python-vlc não está instalado.")
        # network-caching baixo = partida rápida; reconexão HTTP ligada.
        self.instance = vlc.Instance(
            "--no-video-title-show",
            "--network-caching=1200",
            "--http-reconnect",
            "--quiet",
        )
        self.player = self.instance.media_player_new()
        self._media = None
        self._on_error = None
        self._on_end = None
        self._on_playing = None
        self._duration_ms = 0
        self._attach_events()

    # ----------------------------------------------------------------- eventos
    def _attach_events(self) -> None:
        try:
            em = self.player.event_manager()
            em.event_attach(vlc.EventType.MediaPlayerEncounteredError, self._evt_error)
            em.event_attach(vlc.EventType.MediaPlayerEndReached, self._evt_end)
            em.event_attach(vlc.EventType.MediaPlayerPlaying, self._evt_playing)
            em.event_attach(vlc.EventType.MediaPlayerLengthChanged, self._evt_length)
        except Exception:  # noqa: BLE001
            log.exception("Falha ao registrar eventos da libVLC")

    def set_callbacks(self, on_error=None, on_end=None, on_playing=None) -> None:
        self._on_error = on_error
        self._on_end = on_end
        self._on_playing = on_playing

    def _evt_error(self, _event) -> None:
        if self._on_error:
            try:
                self._on_error("Erro de reprodução na libVLC.")
            except Exception:  # noqa: BLE001
                pass

    def _evt_end(self, _event) -> None:
        if self._on_end:
            try:
                self._on_end()
            except Exception:  # noqa: BLE001
                pass

    def _evt_playing(self, _event) -> None:
        if self._on_playing:
            try:
                self._on_playing()
            except Exception:  # noqa: BLE001
                pass

    def _evt_length(self, _event) -> None:
        try:
            self._duration_ms = int(self.player.get_length() or 0)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------------- incorporar
    def bind_widget(self, widget) -> None:
        """Liga a saída de vídeo da libVLC ao widget nativo da janela."""
        win_id = int(widget.winId())
        if sys.platform.startswith("win"):
            self.player.set_hwnd(win_id)
        elif sys.platform == "darwin":
            self.player.set_nsobject(win_id)
        else:
            self.player.set_xwindow(win_id)

    # ------------------------------------------------------------------- mídia
    def set_source(self, url: str) -> None:
        self._media = self.instance.media_new(url)
        self.player.set_media(self._media)

    def play(self) -> None:
        self.player.play()

    def pause(self) -> None:
        self.player.set_pause(1)

    def resume(self) -> None:
        self.player.set_pause(0)

    def toggle_pause(self) -> None:
        self.player.pause()

    def stop(self) -> None:
        try:
            self.player.stop()
        except Exception:  # noqa: BLE001
            pass

    def is_playing(self) -> bool:
        try:
            return bool(self.player.is_playing())
        except Exception:  # noqa: BLE001
            return False

    # ----------------------------------------------------------------- posição
    def position_ms(self) -> int:
        try:
            return int(self.player.get_time() or 0)
        except Exception:  # noqa: BLE001
            return 0

    def duration_ms(self) -> int:
        try:
            length = int(self.player.get_length() or 0)
        except Exception:  # noqa: BLE001
            length = 0
        if length > 0:
            self._duration_ms = length
        return self._duration_ms

    def set_position_ms(self, ms: int) -> None:
        try:
            self.player.set_time(max(0, int(ms)))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ taxa / volume
    def set_rate(self, rate: float) -> None:
        try:
            self.player.set_rate(float(rate))
        except Exception:  # noqa: BLE001
            pass

    def set_volume(self, volume_0_1: float) -> None:
        try:
            self.player.audio_set_volume(int(max(0.0, min(1.0, volume_0_1)) * 100))
        except Exception:  # noqa: BLE001
            pass

    def set_muted(self, muted: bool) -> None:
        try:
            self.player.audio_set_mute(bool(muted))
        except Exception:  # noqa: BLE001
            pass

    def release(self) -> None:
        try:
            self.stop()
            self.player.release()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.instance.release()
        except Exception:  # noqa: BLE001
            pass
