"""Player de vídeo EMBUTIDO do TgPlayer (v6.5).

Histórico:
- Até a v6.4.x o player local foi removido e o app só abria as aulas no
  Telegram Desktop ou no VLC externo. O VLC, porém, demora muito para iniciar
  o streaming HTTP local — era a principal reclamação dos usuários.
- A partir da v6.5 o player local VOLTA, agora baseado em **QtWebEngine** (o
  motor Chromium embutido no Qt). Ele carrega a MESMA página HTML5 que o player
  da web usa (``player_html.build_player_html``), servida pelo próprio servidor
  local em ``/player/{token}`` — ou seja, **mesma origem** do vídeo, o que evita
  o bloqueio de mídia cross-origin do Chromium.

Por que isto é RÁPIDO:
- O backend (``stream_cache.StreamSession``) faz *faststart virtual*: monta o
  cabeçalho ``ftyp+moov`` em memória e o serve ANTES do ``mdat``. Assim o
  ``<video>`` do Chromium recebe o índice do MP4 já no começo e começa a tocar
  em poucos segundos — sem esperar o VLC abrir, sem baixar o arquivo inteiro.

Robustez:
- Se o QtWebEngine NÃO estiver disponível no ambiente (ex.: build sem
  ``PySide6-Addons`` ou Qt sem WebEngine), ``is_webengine_available()`` retorna
  ``False`` e o app pode cair para "Abrir no VLC" automaticamente, sem quebrar.

Compatibilidade:
- A classe ``VideoPlayerDialog`` mantém a MESMA assinatura/atributos esperados
  pelos testes de UI (``seek``, ``play_btn``, ``loading_overlay``, ``toast``,
  ``time_label``, ``_maybe_resume``, ``toggle_mute``, ``update_time_label``,
  ``_request_vlc``, salvamento de progresso ao fechar, etc.). A maior parte dos
  controles "reais" (play/seek/velocidade) acontece dentro do HTML; do lado Qt
  mantemos uma camada fina que reflete estado e cuida de retomada/progresso.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Detecção do QtWebEngine                                                       #
# --------------------------------------------------------------------------- #
def is_webengine_available() -> bool:
    """True se PySide6.QtWebEngineWidgets puder ser importado neste ambiente."""
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Imports do Qt (feitos de forma tolerante para não quebrar em headless)        #
# --------------------------------------------------------------------------- #
from PySide6.QtCore import Qt, QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class VideoPlayerDialog(QDialog):
    """Janela do player embutido (QtWebEngine + HTML5 same-origin).

    Parâmetros:
        title: título exibido no topo do player.
        url: URL de STREAMING direto (``/stream/{token}/...``). Mantido por
            compatibilidade; o player carrega ``player_url`` quando fornecido.
        token: token da sessão de streaming (para buffer/liberação).
        service: ``TelegramService`` (acesso a buffer_ratio, release_stream...).
        start_position_ms: posição salva para retomar.
        on_progress: callback ``(position_ms, duration_ms)`` chamado ao fechar e
            periodicamente, para salvar o progresso da aula.
        on_open_vlc: callback acionado quando o usuário pede "Abrir no VLC".
        player_url: URL da PÁGINA do player same-origin (``/player/{token}``).
            Quando ausente, é derivada de ``url`` trocando ``/stream/`` por
            ``/player/``.
    """

    def __init__(
        self,
        title: str,
        url: str,
        token: str | None = None,
        service: Any = None,
        start_position_ms: int = 0,
        on_progress: Callable[[int, int], None] | None = None,
        on_open_vlc: Callable[[], None] | None = None,
        player_url: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PlayerDialog")
        self.setWindowTitle(title or "TgPlayer")
        self.setMinimumSize(900, 560)

        self._title = title or "Aula"
        self._stream_url = url
        self._player_url = player_url or self._derive_player_url(url)
        self.token = token
        self.service = service
        self._start_position_ms = int(start_position_ms or 0)
        self._on_progress = on_progress
        self._on_open_vlc = on_open_vlc

        # Estado refletido (lido do HTML via runJavaScript).
        self._duration = 0
        self._last_position = 0
        self._last_duration = 0
        self._muted = False
        self._volume = 1.0
        self._resumed = False
        self._is_ready = False
        self._elapsed_loading = 0.0
        self._want_vlc = False
        self.mode = "webengine" if is_webengine_available() else "unavailable"

        self._build_ui()
        self._build_shortcuts()
        self._start_timers()
        self._load()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _derive_player_url(stream_url: str | None) -> str | None:
        if not stream_url:
            return None
        # http://127.0.0.1:PORTA/stream/{token}/arquivo -> .../player/{token}
        try:
            if "/stream/" in stream_url:
                base, rest = stream_url.split("/stream/", 1)
                token = rest.split("/", 1)[0]
                return f"{base}/player/{token}"
        except Exception:  # noqa: BLE001
            pass
        return None

    # ----------------------------------------------------------------- UI build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Área do vídeo (QWebEngineView quando disponível).
        self.stage = QFrame()
        self.stage.setObjectName("PlayerStage")
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        stage_layout.setSpacing(0)

        self.web = None
        if is_webengine_available():
            from PySide6.QtWebEngineWidgets import QWebEngineView

            self.web = QWebEngineView()
            self.web.setObjectName("PlayerWeb")
            # Permite autoplay (sem exigir clique do usuário) e habilita
            # fullscreen/JS para o player HTML5 funcionar plenamente.
            try:
                from PySide6.QtWebEngineCore import QWebEngineSettings

                s = self.web.settings()
                s.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
                s.setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
                s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
                s.setAttribute(QWebEngineSettings.ScreenCaptureEnabled, False)
                s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
            except Exception:  # noqa: BLE001
                log.debug("Não foi possível ajustar QWebEngineSettings", exc_info=True)
            # Aceita pedidos de fullscreen vindos do HTML (botão ⛶).
            try:
                page = self.web.page()
                page.fullScreenRequested.connect(self._on_html_fullscreen)
            except Exception:  # noqa: BLE001
                pass
            stage_layout.addWidget(self.web)
        else:
            fallback = QLabel(
                "O player embutido precisa do componente QtWebEngine, que não foi "
                "encontrado neste ambiente.\n\nUse 'Abrir no VLC' para assistir."
            )
            fallback.setAlignment(Qt.AlignCenter)
            fallback.setWordWrap(True)
            fallback.setObjectName("Muted")
            stage_layout.addWidget(fallback)

        # Overlay de carregamento (sobre o stage).
        self.loading_overlay = QFrame(self.stage)
        self.loading_overlay.setObjectName("PlayerLoading")
        ov = QVBoxLayout(self.loading_overlay)
        ov.setAlignment(Qt.AlignCenter)
        self.loading_label = QLabel("Preparando a aula…")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setObjectName("PanelTitle")
        ov.addWidget(self.loading_label)
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 100)
        self.loading_bar.setValue(0)
        self.loading_bar.setMaximumWidth(360)
        self.loading_bar.setMaximumHeight(10)
        ov.addWidget(self.loading_bar, alignment=Qt.AlignCenter)
        self.loading_vlc_btn = QPushButton("Está demorando? Abrir no VLC")
        self.loading_vlc_btn.setObjectName("GhostButton")
        self.loading_vlc_btn.clicked.connect(self._request_vlc)
        self.loading_vlc_btn.hide()
        ov.addWidget(self.loading_vlc_btn, alignment=Qt.AlignCenter)
        self.loading_overlay.hide()

        root.addWidget(self.stage, 1)

        # Toast informativo (retomada, mensagens curtas).
        self.toast = QLabel("")
        self.toast.setObjectName("PlayerToast")
        self.toast.setAlignment(Qt.AlignCenter)
        self.toast.hide()

        # Barra de controle "fina" do lado Qt (a barra rica está no HTML;
        # mantemos estes widgets para integração nativa e para os testes).
        bar = QFrame()
        bar.setObjectName("PlayerBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 8, 14, 10)
        bl.setSpacing(10)

        self.play_btn = QPushButton("⏯")
        self.play_btn.setObjectName("IconButton")
        self.play_btn.clicked.connect(self.toggle_play)
        bl.addWidget(self.play_btn)

        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek._buffered_ratio = 0.0  # type: ignore[attr-defined]
        self.seek.sliderReleased.connect(self._on_seek_released)
        bl.addWidget(self.seek, 1)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("Muted")
        bl.addWidget(self.time_label)

        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setObjectName("IconButton")
        self.mute_btn.clicked.connect(self.toggle_mute)
        bl.addWidget(self.mute_btn)

        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(100)
        self.vol.setMaximumWidth(110)
        self.vol.valueChanged.connect(self._on_volume_changed)
        bl.addWidget(self.vol)

        self.vlc_btn = QPushButton("Abrir no VLC")
        self.vlc_btn.setObjectName("GhostButton")
        self.vlc_btn.clicked.connect(self._request_vlc)
        bl.addWidget(self.vlc_btn)

        root.addWidget(self.toast)
        root.addWidget(bar)

    def _build_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.close)
        QShortcut(QKeySequence("F"), self, activated=self._toggle_fullscreen)
        QShortcut(QKeySequence("M"), self, activated=self.toggle_mute)

    def _start_timers(self) -> None:
        # Atualiza buffer/estado periodicamente.
        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._update_buffer)
        self._poll.start()

        self._loading_clock = QTimer(self)
        self._loading_clock.setInterval(1000)
        self._loading_clock.timeout.connect(self._tick_loading)
        self._loading_clock.start()

    def _load(self) -> None:
        self._show_loading()
        if self.web is not None and self._player_url:
            self.web.load(QUrl(self._player_url))
        elif self.web is not None and self._stream_url:
            # Sem player_url: carrega o stream direto num <video> mínimo.
            self.web.load(QUrl(self._stream_url))

    # --------------------------------------------------------------- overlay UI
    def _show_loading(self) -> None:
        self._is_ready = False
        self._elapsed_loading = 0.0
        self.loading_vlc_btn.hide()
        self.loading_overlay.setGeometry(self.stage.rect())
        self.loading_overlay.show()
        self.loading_overlay.raise_()

    def _hide_loading(self) -> None:
        self._is_ready = True
        self.loading_overlay.hide()

    def _tick_loading(self) -> None:
        if self._is_ready:
            return
        self._elapsed_loading += 1.0

    def _update_buffer(self) -> None:
        """Lê o progresso de buffer do serviço e o estado do <video> do HTML."""
        ratio = 0.0
        if self.service and self.token:
            try:
                ratio = float(self.service.buffer_ratio(self.token))
            except Exception:  # noqa: BLE001
                ratio = 0.0
        self.seek._buffered_ratio = ratio  # type: ignore[attr-defined]
        try:
            self.loading_bar.setValue(int(max(0.0, min(1.0, ratio)) * 100))
        except Exception:  # noqa: BLE001
            pass

        # Após ~5 s sem ficar pronto, oferece o VLC no overlay.
        if not self._is_ready and self._elapsed_loading >= 5.0:
            self.loading_vlc_btn.show()

        # Quando o overlay está visível e o buffer está cheio, esconde.
        if not self._is_ready and ratio >= 0.999:
            self._hide_loading()

        # Lê estado real do <video> dentro do HTML (posição/duração/pausa).
        self._sync_state_from_web()

    def _sync_state_from_web(self) -> None:
        if self.web is None:
            return
        page = self.web.page()
        if page is None:
            return

        def _apply(state: Any) -> None:
            if not isinstance(state, dict):
                return
            pos = int(state.get("position") or 0)
            dur = int(state.get("duration") or 0)
            paused = bool(state.get("paused"))
            ended = bool(state.get("ended"))
            want_vlc = bool(state.get("wantVlc"))
            if dur > 0:
                self._duration = dur
                if self.seek.maximum() != dur:
                    self.seek.setRange(0, dur)
                if not self.seek.isSliderDown():
                    self.seek.setValue(pos)
            self._last_position = pos
            self._last_duration = dur
            self.play_btn.setText("▶" if paused else "❚❚")
            self.update_time_label(pos)
            if dur > 0 and not self._is_ready:
                self._hide_loading()
            if want_vlc and not self._want_vlc:
                self._want_vlc = True
                self._request_vlc()
            if ended:
                self._on_ended()

        try:
            page.runJavaScript(
                "JSON.stringify(window.__tg_state||{})",
                lambda res: _apply(self._parse_state(res)),
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _parse_state(res: Any) -> dict[str, Any]:
        import json

        if isinstance(res, dict):
            return res
        if isinstance(res, str) and res:
            try:
                return json.loads(res)
            except Exception:  # noqa: BLE001
                return {}
        return {}

    # ----------------------------------------------------------- retomada/tempo
    def _maybe_resume(self) -> None:
        """Solicita ao HTML que retome na posição salva (uma única vez)."""
        if self._resumed or self._start_position_ms <= 0:
            return
        if self._duration and self._start_position_ms >= self._duration - 5000:
            return
        self._resumed = True
        secs = self._start_position_ms / 1000.0
        self._run_js(f"try{{document.getElementById('video').currentTime={secs};}}catch(e){{}}")
        self._show_toast(f"Retomando de {self._fmt(self._start_position_ms)}")

    def update_time_label(self, position_ms: int) -> None:
        self.time_label.setText(
            f"{self._fmt(position_ms)} / {self._fmt(self._duration)}"
        )

    @staticmethod
    def _fmt(ms: int) -> str:
        s = max(0, int(ms)) // 1000
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{sec:02d}"
        return f"{m:02d}:{sec:02d}"

    def _show_toast(self, text: str, ms: int = 1800) -> None:
        self.toast.setText(text)
        self.toast.show()
        QTimer.singleShot(ms, self.toast.hide)

    # ------------------------------------------------------------ controles Qt
    def _run_js(self, code: str) -> None:
        if self.web is None:
            return
        page = self.web.page()
        if page is not None:
            try:
                page.runJavaScript(code)
            except Exception:  # noqa: BLE001
                pass

    def toggle_play(self) -> None:
        self._run_js(
            "(function(){var v=document.getElementById('video');"
            "if(!v)return;if(v.paused)v.play();else v.pause();})()"
        )

    def _on_seek_released(self) -> None:
        if self._duration <= 0:
            return
        secs = self.seek.value() / 1000.0
        self._run_js(f"try{{document.getElementById('video').currentTime={secs};}}catch(e){{}}")

    def _on_volume_changed(self, value: int) -> None:
        self._volume = max(0.0, min(1.0, value / 100.0))
        self._muted = value == 0
        self.mute_btn.setText("🔇" if self._muted else "🔊")
        self._run_js(
            f"try{{var v=document.getElementById('video');v.volume={self._volume};"
            f"v.muted={'true' if self._muted else 'false'};}}catch(e){{}}"
        )

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        self.mute_btn.setText("🔇" if self._muted else "🔊")
        self._run_js(
            f"try{{document.getElementById('video').muted={'true' if self._muted else 'false'};}}catch(e){{}}"
        )

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _on_html_fullscreen(self, request: Any) -> None:
        """Aceita os pedidos de tela cheia disparados pelo HTML (botão ⛶)."""
        try:
            request.accept()
            if request.toggleOn():
                self.showFullScreen()
            else:
                self.showNormal()
        except Exception:  # noqa: BLE001
            log.debug("Falha ao tratar fullScreenRequested", exc_info=True)

    def _request_vlc(self) -> None:
        if self._on_open_vlc:
            try:
                self._on_open_vlc()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao acionar abertura no VLC")

    def _on_ended(self) -> None:
        # Marca a aula como concluída via progresso (posição = duração).
        if self._on_progress and self._last_duration > 0:
            try:
                self._on_progress(self._last_duration, self._last_duration)
            except Exception:  # noqa: BLE001
                pass

    # ----------------------------------------------------- ciclo de vida da UI
    def resizeEvent(self, event) -> None:  # noqa: D401
        super().resizeEvent(event)
        try:
            self.loading_overlay.setGeometry(self.stage.rect())
        except Exception:  # noqa: BLE001
            pass

    def showEvent(self, event) -> None:  # noqa: D401
        super().showEvent(event)
        # Tenta retomar quando a duração já for conhecida (poll cuidará disso).
        QTimer.singleShot(1500, self._maybe_resume)

    def closeEvent(self, event) -> None:  # noqa: D401
        # Salva o progresso final.
        if self._on_progress:
            try:
                self._on_progress(self._last_position, self._last_duration)
            except Exception:  # noqa: BLE001
                pass
        # Para timers.
        for timer in ("_poll", "_loading_clock"):
            t = getattr(self, timer, None)
            if t is not None:
                try:
                    t.stop()
                except Exception:  # noqa: BLE001
                    pass
        # Libera a sessão de streaming (apaga o cache temporário).
        if self.service and self.token:
            try:
                self.service.call(self.service.release_stream(self.token))
            except Exception:  # noqa: BLE001
                pass
        super().closeEvent(event)
