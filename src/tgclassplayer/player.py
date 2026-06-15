"""Diálogo de reprodução premium.

Correção crítica desta versão (v6.1):
- O player PRINCIPAL agora é o **QtMultimedia** (QMediaPlayer + QVideoWidget),
  porque ele usa os codecs NATIVOS do sistema operacional (no Windows, o Media
  Foundation, que SUPORTA H.264/AAC). O QtWebEngine, ao contrário, vem SEM os
  codecs proprietários compilados e, por isso, falhava com
  ``DEMUXER_ERROR_NO_SUPPORTED_STREAMS`` na maioria dos .mp4.
- O QtWebEngine fica como FALLBACK (caso o QtMultimedia não esteja disponível).
- Em QUALQUER erro de reprodução mostramos um aviso amigável com os botões
  "Tentar de novo" e "Abrir no VLC".

Mantém:
- Streaming sob demanda em blocos (HTTP Range) — não trava ao dar seek.
- Resume automático da posição salva + gravação periódica de progresso.
- Botão "Abrir no VLC" sempre acessível.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from .player_html import build_player_html

log = logging.getLogger(__name__)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    HAS_QT_PLAYER = True
except Exception:  # noqa: BLE001
    QAudioOutput = None  # type: ignore
    QMediaPlayer = None  # type: ignore
    QVideoWidget = None  # type: ignore
    HAS_QT_PLAYER = False

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings

    HAS_WEB_PLAYER = True
except Exception:  # noqa: BLE001
    QWebEngineView = None  # type: ignore
    QWebEngineSettings = None  # type: ignore
    HAS_WEB_PLAYER = False


class VideoPlayerDialog(QDialog):
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
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.token = token
        self.service = service
        self.on_progress = on_progress
        self.on_open_vlc = on_open_vlc
        self._stream_url = url
        self._player_url = player_url
        self._start_position_ms = max(0, int(start_position_ms))
        self._vlc_requested = False
        self._last_position = 0
        self._last_duration = 0
        self.setWindowTitle(title)
        self.setObjectName("PlayerDialog")
        self.setMinimumSize(1080, 660)
        self.resize(1280, 760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # PRIORIDADE: QtMultimedia (codecs nativos do SO) -> WebEngine (fallback).
        if HAS_QT_PLAYER:
            self.mode = "qt"
            self._build_qt(title, url, self._start_position_ms, layout)
        elif HAS_WEB_PLAYER:
            self.mode = "web"
            self._build_web(title, url, player_url, self._start_position_ms, layout)
        else:
            raise RuntimeError(
                "Nenhum player disponível neste ambiente. Use o VLC como alternativa."
            )

        self._poll = QTimer(self)
        self._poll.timeout.connect(self._collect_state)
        self._poll.start(1200)

    # ------------------------------------------------------------ overlay de erro
    def _build_error_overlay(self, parent: QWidget) -> None:
        self.error_overlay = QFrame(parent)
        self.error_overlay.setObjectName("PlayerErrorOverlay")
        self.error_overlay.setStyleSheet(
            "#PlayerErrorOverlay { background: rgba(120,20,20,0.92);"
            " border-radius: 16px; }"
            " QLabel { color: #fff; background: transparent; }"
        )
        box = QVBoxLayout(self.error_overlay)
        box.setContentsMargins(28, 24, 28, 24)
        box.setSpacing(10)
        box.setAlignment(Qt.AlignCenter)

        self.error_title = QLabel("Não foi possível reproduzir esta aula")
        self.error_title.setStyleSheet(
            "color:#fff;font-size:15pt;font-weight:800;background:transparent;"
        )
        self.error_title.setAlignment(Qt.AlignCenter)
        self.error_detail = QLabel("")
        self.error_detail.setWordWrap(True)
        self.error_detail.setAlignment(Qt.AlignCenter)
        self.error_detail.setStyleSheet(
            "color:#ffe2e2;font-size:10pt;background:transparent;"
        )

        btn_row = QHBoxLayout()
        btn_row.setAlignment(Qt.AlignCenter)
        retry = QPushButton("↻ Tentar de novo")
        retry.clicked.connect(self._retry_play)
        vlc = QPushButton("Abrir no VLC")
        vlc.setObjectName("PrimaryButton")
        vlc.clicked.connect(self._request_vlc)
        btn_row.addWidget(retry)
        btn_row.addWidget(vlc)

        box.addWidget(self.error_title)
        box.addWidget(self.error_detail)
        box.addLayout(btn_row)
        self.error_overlay.hide()

    def _show_error(self, detail: str) -> None:
        if not hasattr(self, "error_overlay"):
            return
        self.error_detail.setText(detail or "Formato não suportado.")
        parent = self.error_overlay.parentWidget()
        if parent:
            w = min(640, parent.width() - 60)
            h = 220
            self.error_overlay.setGeometry(
                (parent.width() - w) // 2, (parent.height() - h) // 2, w, h
            )
        self.error_overlay.show()
        self.error_overlay.raise_()

    def _hide_error(self) -> None:
        if hasattr(self, "error_overlay"):
            self.error_overlay.hide()

    def _retry_play(self) -> None:
        self._hide_error()
        if getattr(self, "mode", None) == "qt":
            try:
                self.player.stop()
                self.player.setSource(QUrl(self._stream_url))
                self.player.play()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao tentar reproduzir novamente")
        elif getattr(self, "mode", None) == "web":
            try:
                if self._player_url:
                    self.web.load(QUrl(self._player_url))
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------- web
    def _build_web(self, title, stream_url, player_url, start_position_ms, layout) -> None:
        self.web = QWebEngineView(self)
        try:
            settings = self.web.settings()
            settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
            settings.setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
            settings.setAttribute(QWebEngineSettings.ScreenCaptureEnabled, False)
            settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.AllowRunningInsecureContent, True)
            settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        except Exception:  # noqa: BLE001
            pass

        layout.addWidget(self.web, 1)
        if player_url:
            self.web.load(QUrl(player_url))
        else:
            html_text = build_player_html(title, stream_url, start_position_ms)
            origin = (
                stream_url.rsplit("/stream/", 1)[0] + "/"
                if "/stream/" in stream_url
                else "http://127.0.0.1/"
            )
            self.web.setHtml(html_text, QUrl(origin))

    def _collect_state(self) -> None:
        if getattr(self, "mode", None) != "web":
            return
        try:
            self.web.page().runJavaScript(
                "JSON.stringify(window.__tg_state||{})", self._on_state_js
            )
        except Exception:  # noqa: BLE001
            pass

    def _on_state_js(self, value) -> None:
        try:
            data = json.loads(value) if value else {}
        except Exception:  # noqa: BLE001
            return
        pos = int(data.get("position") or 0)
        dur = int(data.get("duration") or 0)
        if pos:
            self._last_position = pos
        if dur:
            self._last_duration = dur
        if self.on_progress and pos:
            try:
                self.on_progress(pos, dur)
            except Exception:  # noqa: BLE001
                pass
        if data.get("wantVlc") and not self._vlc_requested:
            self._vlc_requested = True
            self._request_vlc()

    # -------------------------------------------------------------------- qt
    def _build_qt(self, title, url, start_position_ms, layout) -> None:
        self._duration = 0
        self._dragging = False
        self._resumed = False

        # Container do vídeo (preto) com overlay de erro por cima.
        self.video_container = QWidget()
        self.video_container.setStyleSheet("background:#000;")
        container_layout = QVBoxLayout(self.video_container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.setContentsMargins(16, 14, 16, 0)
        title_label = QLabel(title)
        title_label.setObjectName("PlayerTitle")
        title_label.setStyleSheet(
            "color:#fff;font-size:15pt;font-weight:800;background:transparent;"
        )
        title_label.setWordWrap(True)
        header.addWidget(title_label, 1)
        layout.addLayout(header)

        self.video = QVideoWidget()
        self.video.setStyleSheet("background:#000;")
        container_layout.addWidget(self.video)
        layout.addWidget(self.video_container, 1)

        self._build_error_overlay(self.video_container)

        self.audio = QAudioOutput(self)
        self.audio.setVolume(1.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)

        self.position = QSlider(Qt.Horizontal)
        self.position.setRange(0, 0)
        self.position.setStyleSheet("margin:0 16px;")

        self.play_btn = QPushButton("❚❚")
        self.play_btn.setObjectName("PrimaryButton")
        self.back_btn = QPushButton("↺ 10s")
        self.forward_btn = QPushButton("10s ↻")
        self.full_btn = QPushButton("⛶")
        self.vlc_btn = QPushButton("Abrir no VLC")
        self.speed_box = QComboBox()
        self.speed_box.addItems(["0.5x", "0.75x", "1x", "1.25x", "1.5x", "1.75x", "2x"])
        self.speed_box.setCurrentText("1x")
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color:#cbd5e1;font-weight:700;")

        controls_frame = QFrame()
        controls_frame.setStyleSheet("background:#0a0e1a;")
        controls = QHBoxLayout(controls_frame)
        controls.setContentsMargins(16, 10, 16, 16)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.back_btn)
        controls.addWidget(self.forward_btn)
        controls.addSpacing(10)
        controls.addWidget(self.time_label)
        controls.addStretch(1)
        speed_lbl = QLabel("Velocidade")
        speed_lbl.setStyleSheet("color:#cbd5e1;")
        controls.addWidget(speed_lbl)
        controls.addWidget(self.speed_box)
        controls.addWidget(self.vlc_btn)
        controls.addWidget(self.full_btn)

        layout.addWidget(self.position)
        layout.addWidget(controls_frame)

        self.play_btn.clicked.connect(self.toggle_play)
        self.back_btn.clicked.connect(lambda: self.seek_relative(-10_000))
        self.forward_btn.clicked.connect(lambda: self.seek_relative(10_000))
        self.full_btn.clicked.connect(self.toggle_fullscreen)
        self.vlc_btn.clicked.connect(self._request_vlc)
        self.speed_box.currentTextChanged.connect(self.change_speed)
        self.position.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.position.sliderReleased.connect(self._slider_released)
        self.position.sliderMoved.connect(self.player.setPosition)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        try:
            self.player.playbackStateChanged.connect(self.on_state_changed)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.player.errorOccurred.connect(self._on_media_error)
        except Exception:  # noqa: BLE001
            # Versões antigas usam mediaStatusChanged/error.
            try:
                self.player.errorChanged.connect(self._on_media_error_legacy)
            except Exception:  # noqa: BLE001
                pass
        self.player.setSource(QUrl(url))
        self.player.play()

    def _on_media_error(self, error, error_string: str = "") -> None:
        # error == NoError (0) é ignorado.
        try:
            if int(error) == 0:
                return
        except Exception:  # noqa: BLE001
            pass
        detail = error_string or "Formato de mídia não suportado por este player."
        log.warning("Erro de mídia (QtMultimedia): %s", detail)
        self._show_error(
            f"Detalhe: {detail}\nVocê pode tentar novamente ou abrir no VLC."
        )

    def _on_media_error_legacy(self, *_args) -> None:
        try:
            detail = self.player.errorString()
        except Exception:  # noqa: BLE001
            detail = ""
        if detail:
            self._on_media_error(1, detail)

    def _request_vlc(self) -> None:
        if self._vlc_requested:
            return
        self._vlc_requested = True
        if self.on_open_vlc:
            try:
                self.on_open_vlc()
            except Exception:  # noqa: BLE001
                pass

    def _slider_released(self) -> None:
        self._dragging = False
        self.player.setPosition(self.position.value())

    def toggle_play(self) -> None:
        playing = (
            getattr(QMediaPlayer, "PlayingState", None)
            or QMediaPlayer.PlaybackState.PlayingState
        )
        if self.player.playbackState() == playing:
            self.player.pause()
        else:
            self.player.play()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def seek_relative(self, delta_ms: int) -> None:
        current = int(self.player.position())
        target = max(0, current + delta_ms)
        if self._duration:
            target = min(target, self._duration)
        self.player.setPosition(target)

    def change_speed(self, text: str) -> None:
        try:
            rate = float(text.replace("x", "").replace(",", "."))
        except Exception:  # noqa: BLE001
            rate = 1.0
        self.player.setPlaybackRate(rate)

    def on_duration(self, duration: int) -> None:
        self._duration = int(duration or 0)
        self.position.setRange(0, max(self._duration, 0))
        if (
            not self._resumed
            and self._start_position_ms
            and self._duration > self._start_position_ms + 2000
        ):
            self.player.setPosition(self._start_position_ms)
        self._resumed = True
        self.update_time_label(int(self.player.position()))

    def on_position(self, position: int) -> None:
        if not self._dragging:
            self.position.setValue(int(position))
        self._last_position = int(position)
        self._last_duration = self._duration
        if position > 0:
            self._hide_error()  # reproduzindo: some com o aviso, se houver
        self.update_time_label(int(position))
        if self.on_progress and position:
            try:
                self.on_progress(int(position), self._duration)
            except Exception:  # noqa: BLE001
                pass

    def on_state_changed(self, *_args) -> None:
        try:
            playing = (
                getattr(QMediaPlayer, "PlayingState", None)
                or QMediaPlayer.PlaybackState.PlayingState
            )
            self.play_btn.setText(
                "❚❚" if self.player.playbackState() == playing else "▶"
            )
        except Exception:  # noqa: BLE001
            pass

    def update_time_label(self, position: int) -> None:
        def fmt(ms: int) -> str:
            total = max(0, int(ms // 1000))
            h, rem = divmod(total, 3600)
            m, sec = divmod(rem, 60)
            return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

        self.time_label.setText(f"{fmt(position)} / {fmt(self._duration)}")

    # --------------------------------------------------------- estado/progresso
    def _save_final_progress(self) -> None:
        if self.on_progress and self._last_position:
            try:
                self.on_progress(self._last_position, self._last_duration)
            except Exception:  # noqa: BLE001
                pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "error_overlay") and self.error_overlay.isVisible():
            self._show_error(self.error_detail.text())

    def keyPressEvent(self, event) -> None:
        if getattr(self, "mode", None) == "qt":
            key = event.key()
            if key == Qt.Key_Space:
                self.toggle_play()
                event.accept()
                return
            if key == Qt.Key_Left:
                self.seek_relative(-10_000)
                event.accept()
                return
            if key == Qt.Key_Right:
                self.seek_relative(10_000)
                event.accept()
                return
            if key == Qt.Key_F:
                self.toggle_fullscreen()
                event.accept()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        try:
            self._poll.stop()
        except Exception:  # noqa: BLE001
            pass
        self._save_final_progress()
        try:
            if getattr(self, "mode", None) == "qt":
                self.player.stop()
            elif getattr(self, "mode", None) == "web":
                self.web.setHtml("")
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.service and self.token:
                self.service.call(
                    self.service.release_stream(self.token, delete_file=True)
                )
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
