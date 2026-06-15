"""Diálogo de reprodução PREMIUM do TgPlayer (v6.3).

Objetivos desta versão:

A) CARREGAR RÁPIDO (sem ficar preso em 00:00 com tela preta)
   - O servidor local agora pré-busca o início + a cauda (moov) do MP4 e libera
     os primeiros bytes sem esperar o bloco inteiro (ver ``stream_cache.py``).
   - Aqui no player há um OVERLAY "Carregando aula… NN%" que mostra o progresso
     real de buffer e, após ~6 s, oferece o botão "Está demorando? Abrir no VLC"
     — o usuário NUNCA fica olhando para uma tela preta morta.
   - Backend preferencial: **libVLC embarcado** (``vlc_embed``) quando disponível
     (ganha a velocidade do VLC dentro da nossa janela). Caso contrário, cai
     automaticamente para o **QMediaPlayer** (codecs nativos do SO). O
     QtWebEngine permanece como último fallback.

B) INTERFACE PREMIUM
   - Barra de controles FLUTUANTE com auto-hide (~3 s).
   - Seek bar premium com faixa de buffer (buffered range), thumb com hover e
     tooltip de tempo.
   - Botões grandes/legíveis: play central, ±10s, volume, velocidade 0.5–2x,
     tela cheia e "Abrir no VLC". Cabeçalho com o título. Alto contraste.
   - Atalhos: Espaço, ←/→ (±10s), ↑/↓ (volume), F (tela cheia), Esc (sair de
     tela cheia / fechar), M (mudo).

C) RETOMADA CONFIÁVEL
   - Salva a posição a cada ~5 s e ao fechar.
   - Retoma exatamente em ``start_position_ms`` quando a mídia carrega; se faltar
     menos de 5 s para o fim, recomeça do zero.
   - Aviso discreto "Retomando de mm:ss".
   - Marca como assistida ✅ ao passar de ~92% (feito no banco via on_progress).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .player_html import build_player_html
from .quality import QUALITIES, cap_to_source, label_for

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

try:
    from . import vlc_embed

    HAS_VLC_EMBED = vlc_embed.is_available()
except Exception:  # noqa: BLE001
    vlc_embed = None  # type: ignore
    HAS_VLC_EMBED = False


def _fmt_time(ms: int) -> str:
    total = max(0, int(ms // 1000))
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


# ----------------------------------------------------------------------- seek bar
class SeekBar(QSlider):
    """Slider de progresso premium: mostra a faixa de buffer e tooltip de tempo."""

    def __init__(self, on_seek: Callable[[int], None]) -> None:
        super().__init__(Qt.Horizontal)
        self._on_seek = on_seek
        self._buffered_ratio = 0.0
        self.setRange(0, 0)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(26)
        self.setObjectName("SeekBar")

    def set_buffered_ratio(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, ratio))
        if abs(ratio - self._buffered_ratio) > 0.002:
            self._buffered_ratio = ratio
            self.update()

    def _value_at(self, x: int) -> int:
        if self.maximum() <= self.minimum():
            return self.minimum()
        ratio = max(0.0, min(1.0, x / max(1, self.width())))
        return int(self.minimum() + ratio * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            value = self._value_at(int(event.position().x()))
            self.setValue(value)
            self._on_seek(value)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        value = self._value_at(int(event.position().x()))
        QToolTip.showText(
            event.globalPosition().toPoint(),
            _fmt_time(value),
            self,
        )
        if event.buttons() & Qt.LeftButton:
            self.setValue(value)
        super().mouseMoveEvent(event)

    def paintEvent(self, event) -> None:
        from PySide6.QtGui import QPainter, QLinearGradient, QBrush

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        h = self.height()
        track_h = 7
        y = (h - track_h) // 2
        radius = track_h / 2

        # Trilho de fundo.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 38))
        painter.drawRoundedRect(0, y, w, track_h, radius, radius)

        rng = max(1, self.maximum() - self.minimum())
        played_ratio = (self.value() - self.minimum()) / rng if rng else 0.0

        # Faixa de buffer carregado.
        buf_w = int(w * self._buffered_ratio)
        if buf_w > 0:
            painter.setBrush(QColor(255, 255, 255, 80))
            painter.drawRoundedRect(0, y, buf_w, track_h, radius, radius)

        # Faixa reproduzida (gradiente).
        played_w = int(w * played_ratio)
        if played_w > 0:
            grad = QLinearGradient(0, 0, played_w, 0)
            grad.setColorAt(0.0, QColor("#7c5cff"))
            grad.setColorAt(1.0, QColor("#22d3ee"))
            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(0, y, played_w, track_h, radius, radius)

        # Thumb.
        thumb_x = max(7, min(w - 7, played_w))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPoint(thumb_x, h // 2), 8, 8)
        painter.setBrush(QColor(124, 92, 255, 90))
        painter.drawEllipse(QPoint(thumb_x, h // 2), 12, 12)
        painter.end()


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
        on_next: Callable[[], None] | None = None,
        on_prev: Callable[[], None] | None = None,
        current_index: int = 0,
        total_items: int = 1,
        source_width: int | None = None,
        source_height: int | None = None,
        initial_quality: str = "original",
        adaptive_mode: bool = False,
        on_quality_change: Callable[[str, bool], None] | None = None,
        on_clear_cache: Callable[[], None] | None = None,
        debug_overlay: bool = False,
        playback_rate: float = 1.0,
        on_rate_change: Callable[[float], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.token = token
        self.service = service
        self.on_progress = on_progress
        self.on_open_vlc = on_open_vlc
        self.on_next = on_next
        self.on_prev = on_prev
        self.on_quality_change = on_quality_change
        self.on_clear_cache = on_clear_cache
        self.on_rate_change = on_rate_change
        self._current_index = int(current_index)
        self._total_items = max(1, int(total_items))
        self._source_width = source_width
        self._source_height = source_height
        self._quality = (initial_quality or "original").lower()
        self._adaptive = bool(adaptive_mode)
        self._debug_on = bool(debug_overlay)
        self._initial_rate = float(playback_rate or 1.0)
        self._stream_url = url
        self._player_url = player_url
        self._title = title
        self._start_position_ms = max(0, int(start_position_ms))
        self._vlc_requested = False
        self._auto_next_requested = False
        self._navigated = False  # próxima/anterior pedida -> não fecha por completo
        self._last_position = 0
        self._last_duration = 0
        self._duration = 0
        self._dragging = False
        self._resumed = False
        self._muted = False
        self._volume = 1.0
        self._elapsed_loading = 0.0
        self._is_ready = False
        self._autoplay_countdown = 0
        self.setWindowTitle(title)
        self.setObjectName("PlayerDialog")
        self.setMinimumSize(1080, 660)
        self.resize(1280, 760)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Escolha do backend, do mais rápido ao fallback:
        #   1) libVLC embarcado (vlc_embed)   2) QtMultimedia   3) QtWebEngine
        if HAS_VLC_EMBED:
            self.mode = "vlc"
            self._build_vlc(title, url, layout)
        elif HAS_QT_PLAYER:
            self.mode = "qt"
            self._build_qt(title, url, layout)
        elif HAS_WEB_PLAYER:
            self.mode = "web"
            self._build_web(title, url, player_url, self._start_position_ms, layout)
        else:
            raise RuntimeError(
                "Nenhum player disponível neste ambiente. Use o VLC como alternativa."
            )

        # Timer de progresso/estado (salva posição, atualiza UI).
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._collect_state)
        self._poll.start(1000)

        # Salvar progresso a cada ~5 s.
        self._save_timer = QTimer(self)
        self._save_timer.timeout.connect(self._periodic_save)
        self._save_timer.start(5000)

        # Atualiza overlay de carregamento + buffer da seek bar.
        self._buffer_timer = QTimer(self)
        self._buffer_timer.timeout.connect(self._update_buffer)
        self._buffer_timer.start(300)

        # Auto-hide dos controles.
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._hide_controls)
        self.setMouseTracking(True)

    # ============================================================ overlays comuns
    def _build_overlays(self, parent: QWidget) -> None:
        """Cria o overlay de CARREGAMENTO e o overlay de ERRO sobre o vídeo."""
        # ---- Carregando aula… ----------------------------------------------
        self.loading_overlay = QFrame(parent)
        self.loading_overlay.setObjectName("PlayerLoading")
        self.loading_overlay.setStyleSheet(
            "#PlayerLoading { background: rgba(5,7,15,0.86); border-radius: 18px; }"
            " QLabel { color:#f8fafc; background:transparent; }"
        )
        lbox = QVBoxLayout(self.loading_overlay)
        lbox.setContentsMargins(34, 28, 34, 28)
        lbox.setSpacing(14)
        lbox.setAlignment(Qt.AlignCenter)

        self.loading_title = QLabel("Carregando aula…")
        self.loading_title.setAlignment(Qt.AlignCenter)
        self.loading_title.setStyleSheet(
            "font-size:16pt;font-weight:800;background:transparent;"
        )
        self.loading_bar = QFrame()
        self.loading_bar.setFixedSize(320, 8)
        self.loading_bar.setStyleSheet(
            "background:rgba(255,255,255,0.16);border-radius:4px;"
        )
        self.loading_fill = QFrame(self.loading_bar)
        self.loading_fill.setGeometry(0, 0, 0, 8)
        self.loading_fill.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #7c5cff, stop:1 #22d3ee);border-radius:4px;"
        )
        self.loading_pct = QLabel("0%")
        self.loading_pct.setAlignment(Qt.AlignCenter)
        self.loading_pct.setStyleSheet(
            "color:#c4cde0;font-weight:800;font-size:11pt;background:transparent;"
        )
        self.loading_vlc_btn = QPushButton("Está demorando? Abrir no VLC")
        self.loading_vlc_btn.setObjectName("PrimaryButton")
        self.loading_vlc_btn.clicked.connect(self._request_vlc)
        self.loading_vlc_btn.hide()

        lbox.addWidget(self.loading_title)
        lbox.addWidget(self.loading_bar, 0, Qt.AlignCenter)
        lbox.addWidget(self.loading_pct)
        lbox.addWidget(self.loading_vlc_btn, 0, Qt.AlignCenter)

        # ---- Erro ----------------------------------------------------------
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

        # ---- Overlay de DEBUG (tecla D) ------------------------------------
        self.debug_overlay = QFrame(parent)
        self.debug_overlay.setObjectName("PlayerDebug")
        self.debug_overlay.setStyleSheet(
            "#PlayerDebug { background: rgba(2,4,10,0.82);"
            " border:1px solid rgba(124,92,255,0.45); border-radius:12px; }"
            " QLabel { color:#b9f5d0; background:transparent;"
            " font-family:'Consolas','Menlo',monospace; font-size:9.5pt; }"
        )
        dbox = QVBoxLayout(self.debug_overlay)
        dbox.setContentsMargins(14, 12, 14, 12)
        dbox.setSpacing(6)
        self.debug_label = QLabel("debug…")
        self.debug_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dbox.addWidget(self.debug_label)
        self.debug_clear_btn = QPushButton("Limpar cache desta aula")
        self.debug_clear_btn.setStyleSheet(self._btn_style())
        self.debug_clear_btn.clicked.connect(self._clear_cache_clicked)
        dbox.addWidget(self.debug_clear_btn)
        self.debug_overlay.setVisible(self._debug_on)

        # Toast (aviso discreto, ex.: "Retomando de mm:ss").
        self.toast = QLabel("", parent)
        self.toast.setObjectName("PlayerToast")
        self.toast.setStyleSheet(
            "background: rgba(20,26,46,0.94); color:#fff;"
            " border:1px solid rgba(124,92,255,0.55); border-radius:999px;"
            " padding:8px 16px; font-weight:800; font-size:11pt;"
        )
        self.toast.setAlignment(Qt.AlignCenter)
        self.toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)

        # ---- Auto-play da próxima aula (countdown) -------------------------
        self.autoplay_overlay = QFrame(parent)
        self.autoplay_overlay.setObjectName("PlayerAutoNext")
        self.autoplay_overlay.setStyleSheet(
            "#PlayerAutoNext { background: rgba(8,11,22,0.95);"
            " border:1px solid rgba(124,92,255,0.55); border-radius:14px; }"
            " QLabel { color:#f8fafc; background:transparent; }"
        )
        abox = QVBoxLayout(self.autoplay_overlay)
        abox.setContentsMargins(22, 18, 22, 18)
        abox.setSpacing(10)
        abox.setAlignment(Qt.AlignCenter)
        self.autoplay_label = QLabel("Próxima aula em 5…")
        self.autoplay_label.setAlignment(Qt.AlignCenter)
        self.autoplay_label.setStyleSheet(
            "font-size:13pt;font-weight:800;background:transparent;"
        )
        arow = QHBoxLayout()
        arow.setAlignment(Qt.AlignCenter)
        self.autoplay_go = QPushButton("▶ Assistir agora")
        self.autoplay_go.setObjectName("PrimaryButton")
        self.autoplay_go.setStyleSheet(self._btn_style(primary=True))
        self.autoplay_go.clicked.connect(self._autoplay_now)
        self.autoplay_cancel = QPushButton("Cancelar")
        self.autoplay_cancel.setStyleSheet(self._btn_style())
        self.autoplay_cancel.clicked.connect(self._cancel_autoplay)
        arow.addWidget(self.autoplay_go)
        arow.addWidget(self.autoplay_cancel)
        abox.addWidget(self.autoplay_label)
        abox.addLayout(arow)
        self.autoplay_overlay.hide()
        self._autoplay_timer = QTimer(self)
        self._autoplay_timer.timeout.connect(self._autoplay_tick)

        self._reposition_overlays()
        self._show_loading()

    def _show_toast(self, text: str, ms: int = 1800) -> None:
        if not hasattr(self, "toast"):
            return
        self.toast.setText(text)
        self.toast.adjustSize()
        self.toast.show()
        self.toast.raise_()
        self._reposition_overlays()
        self._toast_timer.start(ms)

    def _reposition_overlays(self) -> None:
        parent = getattr(self, "video_container", None)
        if not parent:
            return
        pw, ph = parent.width(), parent.height()
        if hasattr(self, "loading_overlay"):
            w, h = 420, 230
            self.loading_overlay.setGeometry((pw - w) // 2, (ph - h) // 2, w, h)
        if hasattr(self, "toast") and self.toast.isVisible():
            tw = self.toast.width()
            self.toast.move((pw - tw) // 2, 28)
        if hasattr(self, "debug_overlay"):
            dw, dh = 360, 150
            self.debug_overlay.setGeometry(pw - dw - 16, 16, dw, dh)
        if hasattr(self, "autoplay_overlay"):
            aw, ah = 360, 140
            self.autoplay_overlay.setGeometry(pw - aw - 24, ph - ah - 110, aw, ah)

    def _show_loading(self) -> None:
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.show()
            self.loading_overlay.raise_()

    def _hide_loading(self) -> None:
        self._is_ready = True
        if hasattr(self, "loading_overlay"):
            self.loading_overlay.hide()

    def _update_buffer(self) -> None:
        ratio = 0.0
        if self.service and self.token and hasattr(self.service, "buffer_ratio"):
            try:
                ratio = float(self.service.buffer_ratio(self.token))
            except Exception:  # noqa: BLE001
                ratio = 0.0
        # Atualiza a faixa de buffer da seek bar.
        if hasattr(self, "seek"):
            self.seek.set_buffered_ratio(ratio)
        # Atualiza o overlay de carregamento (até estar pronto).
        if not self._is_ready and hasattr(self, "loading_fill"):
            pct = int(round(ratio * 100))
            self.loading_fill.setGeometry(0, 0, int(320 * ratio), 8)
            self.loading_pct.setText(f"{pct}%")
            self._elapsed_loading += 0.3
            if self._elapsed_loading >= 6.0:
                self.loading_vlc_btn.show()
        # Atualiza o overlay de debug, se ativo.
        if self._debug_on:
            self._update_debug()

    # ----------------------------------------------------------- debug overlay
    def _fmt_speed(self, kbps: float) -> str:
        if kbps >= 1000:
            return f"{kbps / 1000:.2f} Mbps"
        return f"{kbps:.0f} Kbps"

    def _buffered_seconds(self) -> float:
        """Segundos no buffer à frente da posição atual (aprox.)."""
        if not self.service or not self.token or not self._duration:
            return 0.0
        try:
            ratio = float(self.service.buffer_ratio(self.token))
        except Exception:  # noqa: BLE001
            return 0.0
        return max(0.0, (ratio * self._duration - self._last_position) / 1000.0)

    def _update_debug(self) -> None:
        if not hasattr(self, "debug_label"):
            return
        info = {}
        if self.service and self.token and hasattr(self.service, "session_info"):
            try:
                info = self.service.session_info(self.token) or {}
            except Exception:  # noqa: BLE001
                info = {}
        kbps = float(info.get("kbps", 0.0))
        throttle = int(info.get("throttle_kbps", 0) or 0)
        moov = info.get("moov", {}) or {}
        loc = {1: "head", 2: "tail"}.get(int(moov.get("located", 0) or 0), "—")
        size = int(info.get("size", 0) or 0)
        cap = "Auto" if self._adaptive else (
            "ilimitado" if throttle == 0 else f"{throttle} kbps"
        )
        lines = [
            f"Velocidade: {self._fmt_speed(kbps)}",
            f"Qualidade: {self._quality_badge_text()} (cap: {cap})",
            f"Buffer: {self._buffered_seconds():.1f}s à frente",
            f"Modo: {self._mode_badge_text()}",
            f"Tamanho: {size / (1024*1024):.1f} MiB" if size else "Tamanho: —",
            f"moov: {loc} @ {moov.get('moov_offset', '—')}",
        ]
        self.debug_label.setText("\n".join(lines))

    def _toggle_debug(self) -> None:
        self._debug_on = not self._debug_on
        if hasattr(self, "debug_overlay"):
            self.debug_overlay.setVisible(self._debug_on)
            self.debug_overlay.raise_()
        if self._debug_on:
            self._update_debug()
        # Persiste a escolha.
        try:
            if self.service and getattr(self.service, "db", None):
                self.service.db.set_setting("debug_overlay", "1" if self._debug_on else "0")
        except Exception:  # noqa: BLE001
            pass
        self._show_toast("Debug ON" if self._debug_on else "Debug OFF", 900)

    def _clear_cache_clicked(self) -> None:
        if self.on_clear_cache:
            try:
                self.on_clear_cache()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao limpar cache da aula")
        self._show_toast("Cache desta aula limpo", 1200)

    # ----------------------------------------------------------- auto-play next
    def _maybe_offer_autoplay(self) -> None:
        """Ao chegar perto do fim (≥92%), oferece a próxima aula com countdown."""
        if self._auto_next_requested or not self.on_next:
            return
        if self._current_index >= self._total_items - 1:
            return
        if not self._duration or self._last_position <= 0:
            return
        if self._last_position < self._duration * 0.92:
            return
        self._auto_next_requested = True
        self._autoplay_countdown = 5
        self.autoplay_label.setText(f"Próxima aula em {self._autoplay_countdown}…")
        self._reposition_overlays()
        self.autoplay_overlay.show()
        self.autoplay_overlay.raise_()
        self._autoplay_timer.start(1000)

    def _autoplay_tick(self) -> None:
        self._autoplay_countdown -= 1
        if self._autoplay_countdown <= 0:
            self._autoplay_timer.stop()
            self._autoplay_now()
            return
        self.autoplay_label.setText(f"Próxima aula em {self._autoplay_countdown}…")

    def _autoplay_now(self) -> None:
        self._autoplay_timer.stop()
        self.autoplay_overlay.hide()
        self.go_next()

    def _cancel_autoplay(self) -> None:
        self._autoplay_timer.stop()
        self.autoplay_overlay.hide()

    # ---------------------------------------------------------------- erro comum
    def _show_error(self, detail: str) -> None:
        self._hide_loading()
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

    # ====================================================== controles (UI comum)
    def _build_controls(self, layout: QVBoxLayout) -> None:
        """Monta a barra de controles flutuante (usada por VLC e QtMultimedia)."""
        self.controls_frame = QFrame()
        self.controls_frame.setObjectName("PlayerControls")
        self.controls_frame.setStyleSheet(
            "#PlayerControls { background: rgba(8,11,22,0.92);"
            " border-top:1px solid rgba(124,92,255,0.25); }"
        )
        outer = QVBoxLayout(self.controls_frame)
        outer.setContentsMargins(18, 8, 18, 14)
        outer.setSpacing(8)

        self.seek = SeekBar(self._seek_to)
        outer.addWidget(self.seek)

        row = QHBoxLayout()
        row.setSpacing(10)

        self.prev_btn = QPushButton("‹")
        self.prev_btn.setFixedSize(46, 44)
        self.prev_btn.setToolTip("Aula anterior (P)")
        self.next_btn = QPushButton("›")
        self.next_btn.setFixedSize(46, 44)
        self.next_btn.setToolTip("Próxima aula (N)")
        for b in (self.prev_btn, self.next_btn):
            b.setStyleSheet(self._btn_style())
        self.prev_btn.setEnabled(self.on_prev is not None and self._current_index > 0)
        self.next_btn.setEnabled(
            self.on_next is not None and self._current_index < self._total_items - 1
        )

        self.play_btn = QPushButton("❚❚")
        self.play_btn.setObjectName("PrimaryButton")
        self.play_btn.setFixedSize(52, 44)
        self.play_btn.setStyleSheet(self._btn_style(primary=True))
        self.back_btn = QPushButton("↺ 10s")
        self.forward_btn = QPushButton("10s ↻")
        for b in (self.back_btn, self.forward_btn):
            b.setFixedHeight(44)
            b.setStyleSheet(self._btn_style())

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet(
            "color:#e5ebf7;font-weight:800;font-size:12pt;background:transparent;"
        )

        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFixedSize(46, 44)
        self.mute_btn.setStyleSheet(self._btn_style())
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setFixedWidth(110)

        speed_lbl = QLabel("Velocidade")
        speed_lbl.setStyleSheet("color:#c4cde0;background:transparent;font-weight:700;")
        self.speed_box = QComboBox()
        self.speed_box.addItems(["0.5x", "0.75x", "1x", "1.25x", "1.5x", "1.75x", "2x"])
        self.speed_box.setCurrentText("1x")
        self.speed_box.setFixedHeight(44)

        # Seletor de qualidade (engrenagem ⚙). Inclui "Auto (adaptativo)".
        self.quality_box = QComboBox()
        self.quality_box.setToolTip("Qualidade do streaming")
        self.quality_box.setFixedHeight(44)
        self._populate_quality_box()

        self.vlc_btn = QPushButton("Abrir no VLC")
        self.vlc_btn.setFixedHeight(44)
        self.vlc_btn.setStyleSheet(self._btn_style())
        self.full_btn = QPushButton("⛶")
        self.full_btn.setFixedSize(46, 44)
        self.full_btn.setStyleSheet(self._btn_style())

        row.addWidget(self.prev_btn)
        row.addWidget(self.play_btn)
        row.addWidget(self.next_btn)
        row.addSpacing(4)
        row.addWidget(self.back_btn)
        row.addWidget(self.forward_btn)
        row.addSpacing(6)
        row.addWidget(self.time_label)
        row.addStretch(1)
        row.addWidget(self.mute_btn)
        row.addWidget(self.volume)
        row.addSpacing(8)
        row.addWidget(QLabel("⚙"))
        row.addWidget(self.quality_box)
        row.addSpacing(8)
        row.addWidget(speed_lbl)
        row.addWidget(self.speed_box)
        row.addSpacing(8)
        row.addWidget(self.vlc_btn)
        row.addWidget(self.full_btn)
        outer.addLayout(row)

        layout.addWidget(self.controls_frame)

        # Ligações de UI.
        self.prev_btn.clicked.connect(self.go_prev)
        self.next_btn.clicked.connect(self.go_next)
        self.play_btn.clicked.connect(self.toggle_play)
        self.back_btn.clicked.connect(lambda: self.seek_relative(-10_000))
        self.forward_btn.clicked.connect(lambda: self.seek_relative(10_000))
        self.full_btn.clicked.connect(self.toggle_fullscreen)
        self.vlc_btn.clicked.connect(self._request_vlc)
        self.mute_btn.clicked.connect(self.toggle_mute)
        self.volume.valueChanged.connect(self._on_volume_changed)
        self.speed_box.currentTextChanged.connect(self.change_speed)
        self.quality_box.currentIndexChanged.connect(self._on_quality_box)

    def _populate_quality_box(self) -> None:
        """Preenche o seletor de qualidade, evitando upscale acima da fonte."""
        self.quality_box.blockSignals(True)
        self.quality_box.clear()
        self.quality_box.addItem("Auto (adaptativo)", userData="__auto__")
        for q in QUALITIES:
            allowed = (q == "original") or cap_to_source(q, self._source_height) == q
            if not allowed and q != "original":
                continue
            self.quality_box.addItem(
                "Original" if q == "original" else label_for(q), userData=q
            )
        if self._adaptive:
            self.quality_box.setCurrentIndex(0)
        else:
            idx = self.quality_box.findData(self._quality)
            self.quality_box.setCurrentIndex(idx if idx >= 0 else 0)
        self.quality_box.blockSignals(False)

    def _on_quality_box(self, _index: int) -> None:
        data = self.quality_box.currentData()
        if data == "__auto__":
            self._adaptive = True
        else:
            self._adaptive = False
            self._quality = str(data or "original")
        self._refresh_badges()
        if self.on_quality_change:
            try:
                self.on_quality_change(self._quality, self._adaptive)
            except Exception:  # noqa: BLE001
                log.exception("Falha ao aplicar qualidade")
        self._show_toast(
            "Qualidade: Auto" if self._adaptive
            else f"Qualidade: {label_for(self._quality)}",
            1100,
        )

    def go_next(self) -> None:
        if self.on_next and self._current_index < self._total_items - 1:
            self._navigated = True
            self._save_final_progress()
            try:
                self.on_next()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao ir para a próxima aula")
            # Fecha este player; o app reabre na aula vizinha.
            self.close()

    def go_prev(self) -> None:
        if self.on_prev and self._current_index > 0:
            self._navigated = True
            self._save_final_progress()
            try:
                self.on_prev()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao ir para a aula anterior")
            self.close()

    def _btn_style(self, primary: bool = False) -> str:
        if primary:
            return (
                "QPushButton{background:#7c5cff;color:#fff;border:none;"
                "border-radius:12px;font-size:15pt;font-weight:800;}"
                "QPushButton:hover{background:#8e72ff;}"
            )
        return (
            "QPushButton{background:rgba(28,36,62,0.9);color:#eef2fb;"
            "border:1px solid rgba(255,255,255,0.12);border-radius:12px;"
            "padding:0 14px;font-size:12pt;font-weight:750;}"
            "QPushButton:hover{background:rgba(124,92,255,0.34);"
            "border-color:rgba(124,92,255,0.6);}"
        )

    def _build_header(self, title: str, layout: QVBoxLayout) -> None:
        self.header = QFrame()
        self.header.setObjectName("PlayerHeader")
        self.header.setStyleSheet(
            "#PlayerHeader{background:rgba(8,11,22,0.92);"
            "border-bottom:1px solid rgba(124,92,255,0.18);}"
        )
        h = QHBoxLayout(self.header)
        h.setContentsMargins(20, 14, 20, 14)
        title_label = QLabel(title)
        title_label.setObjectName("PlayerTitle")
        title_label.setStyleSheet(
            "color:#fff;font-size:15pt;font-weight:800;background:transparent;"
        )
        title_label.setWordWrap(True)
        backend = {"vlc": "VLC", "qt": "Nativo", "web": "Web"}.get(self.mode, "")

        def _badge(text: str) -> QLabel:
            lab = QLabel(text)
            lab.setStyleSheet(
                "color:#cdbcff;background:rgba(124,92,255,0.2);"
                "border:1px solid rgba(124,92,255,0.5);border-radius:999px;"
                "padding:5px 12px;font-weight:800;font-size:10pt;"
            )
            return lab

        backend_badge = _badge(f"⚡ {backend}")
        # Badge de qualidade atual (ex.: "720p · 2.5k" / "Auto").
        self.quality_badge = _badge(self._quality_badge_text())
        # Badge de resolução (Source / Playing).
        self.res_badge = _badge(self._res_badge_text())
        # Badge de modo (Original / Throttled / Auto).
        self.mode_badge = _badge(self._mode_badge_text())
        # Indicador de posição na fila (ex.: "3/12").
        self.pos_badge = _badge(f"{self._current_index + 1}/{self._total_items}")

        h.addWidget(title_label, 1)
        h.addWidget(self.pos_badge, 0, Qt.AlignRight)
        h.addWidget(self.res_badge, 0, Qt.AlignRight)
        h.addWidget(self.quality_badge, 0, Qt.AlignRight)
        h.addWidget(self.mode_badge, 0, Qt.AlignRight)
        h.addWidget(backend_badge, 0, Qt.AlignRight)
        layout.addWidget(self.header)

    # ----------------------------------------------------------- textos de badge
    def _quality_badge_text(self) -> str:
        if self._adaptive:
            return "Auto"
        return label_for(self._quality)

    def _res_badge_text(self) -> str:
        sw, sh = self._source_width, self._source_height
        if sw and sh:
            return f"Source: {sw}×{sh}"
        return "Source: —"

    def _mode_badge_text(self) -> str:
        if self._adaptive:
            return "Auto (adaptativo)"
        if self._quality == "original":
            return "Original"
        return "Throttled"

    def _refresh_badges(self) -> None:
        for name, fn in (
            ("quality_badge", self._quality_badge_text),
            ("res_badge", self._res_badge_text),
            ("mode_badge", self._mode_badge_text),
        ):
            if hasattr(self, name):
                getattr(self, name).setText(fn())
        if hasattr(self, "pos_badge"):
            self.pos_badge.setText(f"{self._current_index + 1}/{self._total_items}")

    # ============================================================ backend: libVLC
    def _build_vlc(self, title, url, layout) -> None:
        self._build_header(title, layout)

        self.video_container = QWidget()
        self.video_container.setStyleSheet("background:#000;")
        self.video_container.setMouseTracking(True)
        layout.addWidget(self.video_container, 1)

        self._build_overlays(self.video_container)
        self._build_controls(layout)

        try:
            self.vlc = vlc_embed.VlcBackend()
            self.vlc.set_callbacks(
                on_error=lambda d: self._show_error(d),
                on_end=self._on_ended,
                on_playing=self._on_vlc_playing,
            )
            self.vlc.bind_widget(self.video_container)
            self.vlc.set_source(url)
            self.vlc.play()
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao iniciar libVLC embarcado; caindo para QtMultimedia")
            # Fallback em tempo de execução para o QtMultimedia.
            if HAS_QT_PLAYER:
                self._teardown_layout(layout)
                self.mode = "qt"
                self._build_qt(title, url, layout)
            else:
                self._show_error(str(exc))

    def _teardown_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)

    def _on_vlc_playing(self) -> None:
        self._hide_loading()
        self._maybe_resume()

    # ============================================================ backend: Qt
    def _build_qt(self, title, url, layout) -> None:
        self._build_header(title, layout)

        self.video_container = QWidget()
        self.video_container.setStyleSheet("background:#000;")
        self.video_container.setMouseTracking(True)
        cl = QVBoxLayout(self.video_container)
        cl.setContentsMargins(0, 0, 0, 0)

        self.video = QVideoWidget()
        self.video.setStyleSheet("background:#000;")
        cl.addWidget(self.video)
        layout.addWidget(self.video_container, 1)

        self._build_overlays(self.video_container)
        self._build_controls(layout)

        self.audio = QAudioOutput(self)
        self.audio.setVolume(1.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)

        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        try:
            self.player.playbackStateChanged.connect(self.on_state_changed)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.player.mediaStatusChanged.connect(self._on_media_status)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.player.errorOccurred.connect(self._on_media_error)
        except Exception:  # noqa: BLE001
            try:
                self.player.errorChanged.connect(self._on_media_error_legacy)
            except Exception:  # noqa: BLE001
                pass
        self.player.setSource(QUrl(url))
        self.player.play()

    def _on_media_status(self, status) -> None:
        try:
            loaded = getattr(QMediaPlayer.MediaStatus, "LoadedMedia", None)
            buffered = getattr(QMediaPlayer.MediaStatus, "BufferedMedia", None)
            buffering = getattr(QMediaPlayer.MediaStatus, "BufferingMedia", None)
            if status in (loaded, buffered):
                self._hide_loading()
                self._maybe_resume()
            elif status == buffering:
                pass
        except Exception:  # noqa: BLE001
            pass

    def _on_media_error(self, error, error_string: str = "") -> None:
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

    # ============================================================ backend: Web
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
        if self.mode == "web":
            try:
                self.web.page().runJavaScript(
                    "JSON.stringify(window.__tg_state||{})", self._on_state_js
                )
            except Exception:  # noqa: BLE001
                pass
        elif self.mode == "vlc":
            self._poll_vlc()

    def _on_state_js(self, value) -> None:
        try:
            data = json.loads(value) if value else {}
        except Exception:  # noqa: BLE001
            return
        pos = int(data.get("position") or 0)
        dur = int(data.get("duration") or 0)
        if pos:
            self._last_position = pos
            self._hide_loading()
        if dur:
            self._last_duration = dur
        if self.on_progress and pos:
            try:
                self.on_progress(pos, dur)
            except Exception:  # noqa: BLE001
                pass
        if data.get("wantVlc") and not self._vlc_requested:
            self._request_vlc()

    # ============================================================== libVLC poll
    def _poll_vlc(self) -> None:
        if not hasattr(self, "vlc"):
            return
        try:
            pos = self.vlc.position_ms()
            dur = self.vlc.duration_ms()
        except Exception:  # noqa: BLE001
            return
        if dur and dur != self._duration:
            self._duration = dur
            self.seek.setRange(0, dur)
        if pos:
            self._hide_loading()
            self._maybe_resume()
        self._last_position = pos
        self._last_duration = self._duration
        if not self._dragging:
            self.seek.setValue(pos)
        self.update_time_label(pos)
        is_playing = self.vlc.is_playing()
        self.play_btn.setText("❚❚" if is_playing else "▶")
        if self.on_progress and pos:
            try:
                self.on_progress(pos, self._duration)
            except Exception:  # noqa: BLE001
                pass
        self._maybe_offer_autoplay()

    def _on_ended(self) -> None:
        # Ao terminar, marca progresso completo.
        if self.on_progress and self._last_duration:
            try:
                self.on_progress(self._last_duration, self._last_duration)
            except Exception:  # noqa: BLE001
                pass
        # Auto-play imediato da próxima aula (sem esperar o countdown).
        if self.on_next and self._current_index < self._total_items - 1:
            if not self._auto_next_requested:
                self._auto_next_requested = True
                self.go_next()

    # =============================================================== comandos UI
    def _request_vlc(self) -> None:
        if self._vlc_requested:
            return
        self._vlc_requested = True
        if self.on_open_vlc:
            try:
                self.on_open_vlc()
            except Exception:  # noqa: BLE001
                pass

    def _retry_play(self) -> None:
        self._hide_error()
        self._is_ready = False
        self._elapsed_loading = 0.0
        self._show_loading()
        if self.mode == "vlc":
            try:
                self.vlc.stop()
                self.vlc.set_source(self._stream_url)
                self.vlc.play()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao tentar reproduzir novamente (vlc)")
        elif self.mode == "qt":
            try:
                self.player.stop()
                self.player.setSource(QUrl(self._stream_url))
                self.player.play()
            except Exception:  # noqa: BLE001
                log.exception("Falha ao tentar reproduzir novamente (qt)")
        elif self.mode == "web":
            try:
                if self._player_url:
                    self.web.load(QUrl(self._player_url))
            except Exception:  # noqa: BLE001
                pass

    def _seek_to(self, value: int) -> None:
        if self.mode == "vlc":
            self.vlc.set_position_ms(value)
        elif self.mode == "qt":
            self.player.setPosition(value)

    def toggle_play(self) -> None:
        if self.mode == "vlc":
            self.vlc.toggle_pause()
        elif self.mode == "qt":
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

    def toggle_mute(self) -> None:
        self._muted = not self._muted
        self.mute_btn.setText("🔇" if self._muted else "🔊")
        if self.mode == "vlc":
            self.vlc.set_muted(self._muted)
        elif self.mode == "qt":
            self.audio.setMuted(self._muted)

    def _on_volume_changed(self, value: int) -> None:
        self._volume = value / 100.0
        self._muted = value == 0
        self.mute_btn.setText("🔇" if self._muted else "🔊")
        if self.mode == "vlc":
            self.vlc.set_volume(self._volume)
        elif self.mode == "qt":
            self.audio.setVolume(self._volume)

    def seek_relative(self, delta_ms: int) -> None:
        current = self._current_position()
        target = max(0, current + delta_ms)
        if self._duration:
            target = min(target, self._duration)
        self._seek_to(target)
        self._show_toast("↺ 10s" if delta_ms < 0 else "10s ↻", 900)

    def _current_position(self) -> int:
        if self.mode == "vlc":
            return self.vlc.position_ms()
        if self.mode == "qt":
            return int(self.player.position())
        return self._last_position

    def change_speed(self, text: str) -> None:
        try:
            rate = float(text.replace("x", "").replace(",", "."))
        except Exception:  # noqa: BLE001
            rate = 1.0
        if self.mode == "vlc":
            self.vlc.set_rate(rate)
        elif self.mode == "qt":
            self.player.setPlaybackRate(rate)
        # Velocidade persistente por curso (callback opcional do app).
        if self.on_rate_change:
            try:
                self.on_rate_change(rate)
            except Exception:  # noqa: BLE001
                pass
        self._show_toast(f"{text}", 900)

    def _cycle_speed(self, direction: int) -> None:
        """Atalhos [ e ]: diminui/aumenta a velocidade um passo."""
        idx = self.speed_box.currentIndex()
        idx = max(0, min(self.speed_box.count() - 1, idx + direction))
        self.speed_box.setCurrentIndex(idx)

    def _seek_percent(self, pct: float) -> None:
        """Atalhos 0–9: pula para uma porcentagem do vídeo."""
        if self._duration:
            self._seek_to(int(self._duration * max(0.0, min(1.0, pct))))

    # =============================================================== QtMultimedia
    def on_duration(self, duration: int) -> None:
        self._duration = int(duration or 0)
        self.seek.setRange(0, max(self._duration, 0))
        self._maybe_resume()
        self.update_time_label(self._current_position())

    def _maybe_resume(self) -> None:
        """Retoma na posição salva (uma única vez), com aviso discreto."""
        if self._resumed or not self._duration:
            return
        start = self._start_position_ms
        # Se faltar menos de 5 s para o fim, recomeça do zero.
        if start and start < self._duration - 5000:
            self._seek_to(start)
            self._show_toast(f"Retomando de {_fmt_time(start)}")
        self._resumed = True

    def on_position(self, position: int) -> None:
        if not self._dragging:
            self.seek.setValue(int(position))
        self._last_position = int(position)
        self._last_duration = self._duration
        if position > 0:
            self._hide_loading()
            self._hide_error()
        self.update_time_label(int(position))
        if self.on_progress and position:
            try:
                self.on_progress(int(position), self._duration)
            except Exception:  # noqa: BLE001
                pass
        self._maybe_offer_autoplay()

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
        self.time_label.setText(f"{_fmt_time(position)} / {_fmt_time(self._duration)}")

    # ============================================================ progresso/salvar
    def _periodic_save(self) -> None:
        if self.on_progress and self._last_position:
            try:
                self.on_progress(self._last_position, self._last_duration)
            except Exception:  # noqa: BLE001
                pass

    def _save_final_progress(self) -> None:
        if self.on_progress and self._last_position:
            try:
                self.on_progress(self._last_position, self._last_duration)
            except Exception:  # noqa: BLE001
                pass

    # =================================================================== eventos
    def _hide_controls(self) -> None:
        if self.isFullScreen() and hasattr(self, "controls_frame"):
            self.controls_frame.hide()
            if hasattr(self, "header"):
                self.header.hide()
            self.setCursor(Qt.BlankCursor)

    def _show_controls(self) -> None:
        if hasattr(self, "controls_frame"):
            self.controls_frame.show()
        if hasattr(self, "header"):
            self.header.show()
        self.setCursor(Qt.ArrowCursor)
        self._idle_timer.start(3000)

    def mouseMoveEvent(self, event) -> None:
        self._show_controls()
        super().mouseMoveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_overlays()
        if hasattr(self, "error_overlay") and self.error_overlay.isVisible():
            self._show_error(self.error_detail.text())

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Space:
            self.toggle_play()
            event.accept()
            return
        if key in (Qt.Key_Left, Qt.Key_J):
            self.seek_relative(-10_000)
            event.accept()
            return
        if key in (Qt.Key_Right, Qt.Key_L):
            self.seek_relative(10_000)
            event.accept()
            return
        if key == Qt.Key_Up:
            self.volume.setValue(min(100, self.volume.value() + 5))
            self._show_toast(f"Vol {self.volume.value()}%", 800)
            event.accept()
            return
        if key == Qt.Key_Down:
            self.volume.setValue(max(0, self.volume.value() - 5))
            self._show_toast(f"Vol {self.volume.value()}%", 800)
            event.accept()
            return
        if key == Qt.Key_F:
            self.toggle_fullscreen()
            event.accept()
            return
        if key in (Qt.Key_M,):
            self.toggle_mute()
            event.accept()
            return
        if key == Qt.Key_D:
            self._toggle_debug()
            event.accept()
            return
        if key == Qt.Key_N:
            self.go_next()
            event.accept()
            return
        if key == Qt.Key_P:
            self.go_prev()
            event.accept()
            return
        if key == Qt.Key_BracketLeft:
            self._cycle_speed(-1)
            event.accept()
            return
        if key == Qt.Key_BracketRight:
            self._cycle_speed(+1)
            event.accept()
            return
        if Qt.Key_0 <= key <= Qt.Key_9:
            self._seek_percent((key - Qt.Key_0) / 10.0)
            event.accept()
            return
        if key == Qt.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
                event.accept()
                return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        for timer_name in (
            "_poll", "_save_timer", "_buffer_timer", "_idle_timer", "_autoplay_timer",
        ):
            try:
                getattr(self, timer_name).stop()
            except Exception:  # noqa: BLE001
                pass
        self._save_final_progress()
        try:
            if self.mode == "vlc" and hasattr(self, "vlc"):
                self.vlc.release()
            elif self.mode == "qt":
                self.player.stop()
            elif self.mode == "web":
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
