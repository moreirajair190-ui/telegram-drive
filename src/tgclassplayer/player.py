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
)

from .player_html import build_player_html

log = logging.getLogger(__name__)

try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings

    HAS_WEB_PLAYER = True
except Exception:  # noqa: BLE001
    QWebEngineView = None  # type: ignore
    QWebEngineSettings = None  # type: ignore
    HAS_WEB_PLAYER = False

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    HAS_QT_PLAYER = True
except Exception:  # noqa: BLE001
    QAudioOutput = None  # type: ignore
    QMediaPlayer = None  # type: ignore
    QVideoWidget = None  # type: ignore
    HAS_QT_PLAYER = False


class VideoPlayerDialog(QDialog):
    """Diálogo de reprodução premium.

    Usa o player HTML (QtWebEngine) quando disponível — interface bonita e
    seek instantâneo. Faz fallback para QtMultimedia se o WebEngine faltar.

    Salva o progresso de reprodução periodicamente via callback `on_progress`.
    """

    def __init__(
        self,
        title: str,
        url: str,
        token: str | None = None,
        service: Any = None,
        start_position_ms: int = 0,
        on_progress: Callable[[int, int], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.token = token
        self.service = service
        self.on_progress = on_progress
        self._last_position = 0
        self._last_duration = 0
        self.setWindowTitle(title)
        self.setObjectName("PlayerDialog")
        self.setMinimumSize(1080, 660)
        self.resize(1280, 760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if HAS_WEB_PLAYER:
            self.mode = "web"
            self._build_web(title, url, start_position_ms, layout)
        elif HAS_QT_PLAYER:
            self.mode = "qt"
            self._build_qt(title, url, start_position_ms, layout)
        else:
            raise RuntimeError(
                "Nenhum player disponível neste ambiente. Use o VLC como alternativa."
            )

        # Timer que coleta o progresso e o repassa ao app.
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._collect_state)
        self._poll.start(1500)

    # ------------------------------------------------------------------- web
    def _build_web(self, title, url, start_position_ms, layout) -> None:
        self.web = QWebEngineView(self)
        try:
            settings = self.web.settings()
            settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
            settings.setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
            settings.setAttribute(QWebEngineSettings.ScreenCaptureEnabled, False)
            settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        except Exception:  # noqa: BLE001
            pass
        html_text = build_player_html(title, url, start_position_ms)
        self.web.setHtml(html_text, QUrl("http://127.0.0.1/"))
        layout.addWidget(self.web, 1)

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

    # -------------------------------------------------------------------- qt
    def _build_qt(self, title, url, start_position_ms, layout) -> None:
        self._duration = 0
        self._dragging = False
        self._start_position_ms = max(0, int(start_position_ms))
        self._resumed = False

        header = QHBoxLayout()
        header.setContentsMargins(16, 14, 16, 0)
        title_label = QLabel(title)
        title_label.setObjectName("PlayerTitle")
        title_label.setStyleSheet("color:#fff;font-size:15pt;font-weight:800;")
        title_label.setWordWrap(True)
        header.addWidget(title_label, 1)
        layout.addLayout(header)

        self.video = QVideoWidget()
        self.video.setStyleSheet("background:#000;")
        self.audio = QAudioOutput(self)
        self.audio.setVolume(1.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        layout.addWidget(self.video, 1)

        self.position = QSlider(Qt.Horizontal)
        self.position.setRange(0, 0)
        self.position.setStyleSheet("margin:0 16px;")

        self.play_btn = QPushButton("❚❚")
        self.play_btn.setObjectName("PrimaryButton")
        self.back_btn = QPushButton("↺ 10s")
        self.forward_btn = QPushButton("10s ↻")
        self.full_btn = QPushButton("⛶")
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
        controls.addWidget(QLabel("Velocidade"))
        controls.addWidget(self.speed_box)
        controls.addWidget(self.full_btn)

        layout.addWidget(self.position)
        layout.addWidget(controls_frame)

        self.play_btn.clicked.connect(self.toggle_play)
        self.back_btn.clicked.connect(lambda: self.seek_relative(-10_000))
        self.forward_btn.clicked.connect(lambda: self.seek_relative(10_000))
        self.full_btn.clicked.connect(self.toggle_fullscreen)
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
        self.player.setSource(QUrl(url))
        self.player.play()

    def _slider_released(self) -> None:
        self._dragging = False
        self.player.setPosition(self.position.value())

    def toggle_play(self) -> None:
        playing = getattr(QMediaPlayer, "PlayingState", None) or QMediaPlayer.PlaybackState.PlayingState
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
        if not self._resumed and self._start_position_ms and self._duration > self._start_position_ms + 2000:
            self.player.setPosition(self._start_position_ms)
        self._resumed = True
        self.update_time_label(int(self.player.position()))

    def on_position(self, position: int) -> None:
        if not self._dragging:
            self.position.setValue(int(position))
        self._last_position = int(position)
        self._last_duration = self._duration
        self.update_time_label(int(position))

    def on_state_changed(self, *_args) -> None:
        try:
            playing = getattr(QMediaPlayer, "PlayingState", None) or QMediaPlayer.PlaybackState.PlayingState
            self.play_btn.setText("❚❚" if self.player.playbackState() == playing else "▶")
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

    def keyPressEvent(self, event) -> None:
        # No modo web os atalhos são tratados pelo JS.
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
                self.service.call(self.service.release_stream(self.token, delete_file=True))
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)
