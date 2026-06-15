"""Gráficos leves desenhados com QPainter (sem dependências frágeis).

Usados na aba "Acompanhamento". Cada widget recebe os dados via `set_data` e
respeita o tema atual (cores) via `set_palette`.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget


class _BaseChart(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._colors = {
            "text": "#eef2fb",
            "muted": "#8b97b3",
            "accent": "#7c5cff",
            "accent2": "#22d3ee",
            "grid": "#22304d",
            "track": "#1d2840",
        }
        self.setMinimumHeight(180)

    def set_palette(self, colors: dict) -> None:
        self._colors.update(
            {
                "text": colors.get("text", self._colors["text"]),
                "muted": colors.get("muted", self._colors["muted"]),
                "accent": colors.get("accent", self._colors["accent"]),
                "accent2": colors.get("accent2", self._colors["accent2"]),
                "grid": colors.get("chart_grid", self._colors["grid"]),
                "track": colors.get("track", self._colors["track"]),
            }
        )
        self.update()


class BarChart(_BaseChart):
    """Gráfico de barras verticais com rótulos e valores."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: list[str] = []
        self._values: list[float] = []
        self._value_fmt = lambda v: str(int(v))

    def set_data(self, labels: list[str], values: list[float], value_fmt=None) -> None:
        self._labels = list(labels)
        self._values = [float(v) for v in values]
        if value_fmt:
            self._value_fmt = value_fmt
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        pad_left, pad_right, pad_top, pad_bottom = 14, 14, 18, 34
        plot_w = max(1, w - pad_left - pad_right)
        plot_h = max(1, h - pad_top - pad_bottom)

        if not self._values:
            p.setPen(QColor(self._colors["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Sem dados ainda")
            p.end()
            return

        max_val = max(self._values) or 1.0
        n = len(self._values)
        gap = 10
        bar_w = max(8.0, (plot_w - gap * (n - 1)) / n)

        # Linhas de grade horizontais.
        grid_pen = QPen(QColor(self._colors["grid"]))
        grid_pen.setWidth(1)
        p.setPen(grid_pen)
        for i in range(4):
            y = pad_top + plot_h * i / 3.0
            p.drawLine(int(pad_left), int(y), int(w - pad_right), int(y))

        font = QFont(self.font())
        font.setPointSize(8)
        p.setFont(font)

        for i, value in enumerate(self._values):
            x = pad_left + i * (bar_w + gap)
            bar_h = (value / max_val) * plot_h if max_val else 0
            top = pad_top + (plot_h - bar_h)
            rect = QRectF(x, top, bar_w, bar_h)

            grad = QLinearGradient(0, top, 0, pad_top + plot_h)
            grad.setColorAt(0, QColor(self._colors["accent2"]))
            grad.setColorAt(1, QColor(self._colors["accent"]))
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            if bar_h > 0:
                p.drawRoundedRect(rect, 5, 5)

            # Valor acima da barra (só se houver valor).
            if value > 0:
                p.setPen(QColor(self._colors["text"]))
                p.drawText(
                    QRectF(x - 4, top - 16, bar_w + 8, 14),
                    Qt.AlignCenter,
                    self._value_fmt(value),
                )

            # Rótulo abaixo.
            p.setPen(QColor(self._colors["muted"]))
            label = self._labels[i] if i < len(self._labels) else ""
            p.drawText(
                QRectF(x - 6, pad_top + plot_h + 6, bar_w + 12, 22),
                Qt.AlignCenter | Qt.TextWordWrap,
                label,
            )
        p.end()


class HBarChart(_BaseChart):
    """Gráfico de barras horizontais (bom para categorias/matérias)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, float]] = []

    def set_data(self, items: list[tuple[str, float]]) -> None:
        self._items = list(items)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        if not self._items:
            p.setPen(QColor(self._colors["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Sem dados ainda")
            p.end()
            return

        items = self._items[:8]
        max_val = max((v for _, v in items), default=1) or 1
        pad = 12
        row_h = max(22.0, (h - pad * 2) / len(items))
        label_w = min(160, w * 0.4)
        bar_area = max(40.0, w - label_w - pad * 2 - 46)

        font = QFont(self.font())
        font.setPointSize(9)
        p.setFont(font)

        for i, (label, value) in enumerate(items):
            y = pad + i * row_h
            # Rótulo.
            p.setPen(QColor(self._colors["text"]))
            p.drawText(
                QRectF(pad, y, label_w, row_h),
                Qt.AlignVCenter | Qt.AlignLeft,
                self._elide(label, 22),
            )
            # Trilho.
            track = QRectF(pad + label_w, y + row_h * 0.22, bar_area, row_h * 0.56)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self._colors["track"]))
            p.drawRoundedRect(track, 6, 6)
            # Barra.
            frac = value / max_val if max_val else 0
            grad = QLinearGradient(track.left(), 0, track.right(), 0)
            grad.setColorAt(0, QColor(self._colors["accent"]))
            grad.setColorAt(1, QColor(self._colors["accent2"]))
            p.setBrush(grad)
            bar = QRectF(track.left(), track.top(), max(0.0, bar_area * frac), track.height())
            if bar.width() > 0:
                p.drawRoundedRect(bar, 6, 6)
            # Valor.
            p.setPen(QColor(self._colors["muted"]))
            p.drawText(
                QRectF(track.right() + 6, y, 44, row_h),
                Qt.AlignVCenter | Qt.AlignLeft,
                str(int(value)),
            )
        p.end()

    @staticmethod
    def _elide(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[: limit - 1] + "…"


class DonutChart(_BaseChart):
    """Anel de progresso (0-100%) com percentual no centro."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pct = 0.0
        self._caption = ""
        self.setMinimumHeight(160)

    def set_value(self, pct: float, caption: str = "") -> None:
        self._pct = max(0.0, min(100.0, float(pct)))
        self._caption = caption
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        h = self.height()
        size = min(w, h) - 24
        x = (w - size) / 2
        y = (h - size) / 2
        rect = QRectF(x, y, size, size)
        thickness = max(10, size * 0.12)

        # Trilho.
        pen = QPen(QColor(self._colors["track"]))
        pen.setWidthF(thickness)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)

        # Arco de progresso.
        pen2 = QPen(QColor(self._colors["accent"]))
        pen2.setWidthF(thickness)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        span = int(-self._pct / 100.0 * 360 * 16)
        p.drawArc(rect, 90 * 16, span)

        # Texto central.
        p.setPen(QColor(self._colors["text"]))
        font = QFont(self.font())
        font.setPointSize(int(max(13, size * 0.16)))
        font.setBold(True)
        p.setFont(font)
        p.drawText(rect, Qt.AlignCenter, f"{int(self._pct)}%")

        if self._caption:
            p.setPen(QColor(self._colors["muted"]))
            cf = QFont(self.font())
            cf.setPointSize(9)
            p.setFont(cf)
            p.drawText(
                QRectF(x, y + size * 0.62, size, 20),
                Qt.AlignCenter,
                self._caption,
            )
        p.end()
