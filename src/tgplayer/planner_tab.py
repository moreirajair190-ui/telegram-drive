"""Aba Planejador 🗓 — organização das aulas com calendário grande + Kanban.

Visão geral
-----------
A tela foi redesenhada para girar em torno de um **calendário mensal grande**,
no qual você arrasta as aulas direto para a **célula do dia** desejado. Cada
célula mostra, de forma organizada, as aulas agendadas para aquele dia.

Layout
------
* À **esquerda**, um quadro Kanban enxuto (Backlog · Esta semana · Concluído)
  com cartões arrastáveis — é de onde você "puxa" as aulas.
* À **direita**, o **calendário grande** (grade do mês inteiro). Solte um cartão
  sobre o dia para agendá-lo; clique num dia para ver/editar as aulas dele.

Persistência em `plan_items` (ver db.py). O drag-and-drop usa QListWidget com
`DragDrop`; cada cartão carrega o `plan_item_id` para gravar a movimentação.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    Qt,
    QDate,
    QMimeData,
    QPoint,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .db import Database


# Chaves de coluna usadas no banco. "sched" é dinâmica (sched_<YYYY-MM-DD>).
COL_BACKLOG = "backlog"
COL_WEEK = "week"
COL_DONE = "done"

ROLE_ITEM_ID = Qt.UserRole + 1
ROLE_VIDEO_ID = Qt.UserRole + 2

CARD_COLORS = ["#7c5cff", "#22d3ee", "#34d399", "#fbbf24", "#f87171", "#a78bfa"]

# Tipo MIME usado no drag-and-drop dos cartões do planejador.
MIME_PLAN_ITEM = "application/x-tgplayer-plan-item"

WEEKDAYS_SHORT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
WEEKDAYS_FULL = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MONTHS_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def _sched_key(date_str: str) -> str:
    return f"sched_{date_str}"


def _is_sched_key(key: str) -> bool:
    return bool(key) and key.startswith("sched_")


def _date_from_sched(key: str) -> str:
    return key[len("sched_"):] if _is_sched_key(key) else ""


class KanbanList(QListWidget):
    """Coluna do Kanban com drag-and-drop entre colunas e para o calendário.

    O arrasto usa um MIME próprio (`MIME_PLAN_ITEM`) carregando o id do cartão,
    para que tanto outra coluna quanto uma célula do calendário consigam recebê-lo.
    """

    item_dropped = Signal(int, str)        # (plan_item_id, column_key)
    reordered = Signal(str, list)          # (column_key, [ids...])

    def __init__(self, column_key: str) -> None:
        super().__init__()
        self.column_key = column_key
        self.setObjectName("KanbanList")
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setMovement(QListWidget.Snap)
        self.setSpacing(6)
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)

    # -- arrasto com MIME próprio (necessário para o calendário receber) -----
    def startDrag(self, supported_actions) -> None:  # noqa: N802, ANN001
        item = self.currentItem()
        if item is None:
            return
        item_id = int(item.data(ROLE_ITEM_ID) or 0)
        if not item_id:
            return
        mime = QMimeData()
        mime.setData(MIME_PLAN_ITEM, str(item_id).encode("utf-8"))
        mime.setText(item.text())
        drag = QDrag(self)
        drag.setMimeData(mime)
        widget = self.itemWidget(item)
        if widget is not None:
            pix = widget.grab()
            drag.setPixmap(pix)
            drag.setHotSpot(QPoint(pix.width() // 2, pix.height() // 2))
        drag.exec(Qt.MoveAction)

    def dropEvent(self, event) -> None:  # noqa: N802
        source = event.source()
        if isinstance(source, KanbanList) and source is not self:
            item = source.currentItem()
            if item is not None:
                item_id = int(item.data(ROLE_ITEM_ID) or 0)
                if item_id:
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                    self.item_dropped.emit(item_id, self.column_key)
                    return
        super().dropEvent(event)
        ids = []
        for i in range(self.count()):
            it = self.item(i)
            iid = int(it.data(ROLE_ITEM_ID) or 0)
            if iid:
                ids.append(iid)
        self.reordered.emit(self.column_key, ids)


class DayCell(QFrame):
    """Célula de um dia no calendário grande. Aceita soltar cartões (aulas)."""

    clicked = Signal(QDate)
    card_dropped = Signal(int, QDate)   # (plan_item_id, data)
    item_activated = Signal(int)        # (video_id) — duplo clique numa aula

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("DayCell")
        self.setAcceptDrops(True)
        self.date: QDate | None = None
        self._in_month = True
        self._is_today = False
        self._selected = False
        self._palette: dict = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(7, 6, 7, 6)
        v.setSpacing(3)

        # Cabeçalho em um widget de altura fixa, para o número do dia nunca
        # colidir com os "pills" das aulas (evita sobreposição no relayout).
        head_w = QWidget()
        head_w.setFixedHeight(20)
        head = QHBoxLayout(head_w)
        head.setContentsMargins(0, 0, 0, 0)
        self.num_lbl = QLabel("")
        self.num_lbl.setObjectName("DayNum")
        head.addWidget(self.num_lbl)
        head.addStretch(1)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("DayCount")
        head.addWidget(self.count_lbl)
        v.addWidget(head_w, 0)

        # Lista compacta de aulas agendadas naquele dia, dentro de um widget
        # hospedeiro real (não um layout "solto"). Isso garante que os pills
        # recebam geometria própria abaixo do cabeçalho e nunca o sobreponham
        # durante relayouts em massa do calendário.
        items_host = QWidget()
        self.items_box = QVBoxLayout(items_host)
        self.items_box.setContentsMargins(0, 0, 0, 0)
        self.items_box.setSpacing(2)
        v.addWidget(items_host, 0)
        v.addStretch(1)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(86)

    def set_palette(self, colors: dict) -> None:
        self._palette = dict(colors or {})

    def set_day(self, date: QDate, in_month: bool, is_today: bool,
                selected: bool, items: list[dict]) -> None:
        self.date = date
        self._in_month = in_month
        self._is_today = is_today
        self._selected = selected
        self.num_lbl.setText(str(date.day()))
        # Limpa pills antigos de forma SÍNCRONA. Usar deleteLater() aqui faz o
        # widget velho sobreviver até o próximo ciclo de eventos, mantendo a
        # geometria antiga (y≈0) e sobrepondo o cabeçalho durante relayouts em
        # massa. setParent(None) + remoção imediata do layout evita isso.
        while self.items_box.count():
            it = self.items_box.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        # Mostra até 3 pills; o resto vira "+N".
        shown = items[:3]
        for it in shown:
            self.items_box.addWidget(self._make_pill(it))
        extra = len(items) - len(shown)
        if extra > 0:
            more = QLabel(f"+{extra} aula(s)")
            more.setObjectName("DayMore")
            self.items_box.addWidget(more)
        self.count_lbl.setText(str(len(items)) if items else "")
        self._apply_state_style()
        # Força o layout a recalcular imediatamente: ao adicionar/remover pills
        # rapidamente (ex.: relayout em massa do calendário), o espaço do
        # cabeçalho precisa ser reservado já — senão o 1º pill sobrepõe o número
        # do dia até um próximo ciclo de eventos.
        if self.layout() is not None:
            self.items_box.invalidate()
            self.items_box.activate()
            self.layout().invalidate()
            self.layout().activate()

    def _make_pill(self, data: dict) -> QWidget:
        watched = bool(data.get("v_watched_at"))
        accent = data.get("color") or CARD_COLORS[int(data["id"]) % len(CARD_COLORS)]
        pill = QLabel()
        pill.setObjectName("DayPill")
        title = data.get("title") or data.get("v_title") or "Aula"
        icon = "✅" if watched else ("🎬" if data.get("video_id") else "📝")
        # Trunca para caber na célula.
        short = title if len(title) <= 22 else title[:21] + "…"
        pill.setText(f"{icon} {short}")
        pill.setToolTip(title)
        pill.setProperty("video_id", int(data.get("video_id") or 0))
        pill.setFixedHeight(19)
        pill.setStyleSheet(
            f"#DayPill {{ background: {self._pill_bg(accent)}; "
            f"color: {self._palette.get('text', '#222')}; "
            f"border-left: 3px solid {accent}; border-radius: 6px; "
            f"padding: 1px 6px; font-size: 11px; font-weight: 600; }}"
        )
        return pill

    def _pill_bg(self, accent: str) -> str:
        # Fundo translúcido suave derivado do acento.
        col = QColor(accent)
        if self._palette.get("name") == "dark":
            col.setAlpha(46)
        else:
            col.setAlpha(30)
        return f"rgba({col.red()},{col.green()},{col.blue()},{col.alpha()/255:.2f})"

    def _apply_state_style(self) -> None:
        c = self._palette
        bg = c.get("panel", "#fff")
        if not self._in_month:
            bg = c.get("bg2", "#f0f0f0")
        border = c.get("border_soft", "#e0e0e0")
        if self._is_today:
            border = c.get("accent2", "#22d3ee")
        if self._selected:
            border = c.get("accent", "#7c5cff")
        self.setProperty("inMonth", self._in_month)
        self.setProperty("today", self._is_today)
        self.setProperty("selected", self._selected)
        num_color = c.get("text" if self._in_month else "muted2", "#222")
        if self._is_today:
            num_color = c.get("accent", "#7c5cff")
        self.num_lbl.setStyleSheet(
            f"#DayNum {{ color: {num_color}; font-weight: "
            f"{'900' if self._is_today else '700'}; font-size: 13px; }}"
        )
        self.setStyleSheet(
            f"#DayCell {{ background: {bg}; border: "
            f"{'2px' if (self._selected or self._is_today) else '1px'} solid {border}; "
            f"border-radius: 12px; }}"
            f"#DayCount {{ color: {c.get('muted2', '#888')}; font-size: 10px; "
            f"font-weight: 800; }}"
            f"#DayMore {{ color: {c.get('muted', '#888')}; font-size: 10px; "
            f"font-weight: 700; }}"
        )

    # -- interação ----------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        if self.date is not None and event.button() == Qt.LeftButton:
            self.clicked.emit(self.date)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802, ANN001
        # Duplo clique numa aula (pill) abre no Telegram.
        child = self.childAt(event.position().toPoint())
        if child is not None:
            vid = int(child.property("video_id") or 0)
            if vid:
                self.item_activated.emit(vid)
        super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.mimeData().hasFormat(MIME_PLAN_ITEM):
            event.acceptProposedAction()
            self._set_drop_highlight(True)
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self._set_drop_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802, ANN001
        self._set_drop_highlight(False)
        md = event.mimeData()
        if md.hasFormat(MIME_PLAN_ITEM) and self.date is not None:
            try:
                item_id = int(bytes(md.data(MIME_PLAN_ITEM)).decode("utf-8"))
            except Exception:  # noqa: BLE001
                item_id = 0
            if item_id:
                event.acceptProposedAction()
                self.card_dropped.emit(item_id, self.date)
                return
        super().dropEvent(event)

    def _set_drop_highlight(self, on: bool) -> None:
        c = self._palette
        if on:
            self.setStyleSheet(
                self.styleSheet()
                + f"#DayCell {{ border: 2px dashed {c.get('accent', '#7c5cff')}; "
                f"background: {c.get('hover', '#eef')}; }}"
            )
        else:
            self._apply_state_style()


class PlannerTab(QWidget):
    """Planejador: calendário grande (com drop por dia) + quadro Kanban."""

    changed = Signal()
    open_lesson_requested = Signal(int)

    def __init__(self, db: Database, get_current_course: Callable[[], object | None]) -> None:
        super().__init__()
        self.db = db
        self.get_current_course = get_current_course
        self._palette: dict = {}
        self.selected_date = QDate.currentDate()
        self.shown_month = QDate(self.selected_date.year(), self.selected_date.month(), 1)
        self._cells: list[DayCell] = []
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_calendar_panel(), 1)

    # ---- coluna esquerda: cartões + ações --------------------------------
    def _build_sidebar(self) -> QWidget:
        left = QFrame()
        left.setObjectName("Card")
        left.setMaximumWidth(310)
        left.setMinimumWidth(268)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 16, 16)
        ll.setSpacing(10)

        title = QLabel("🗓  PLANEJADOR")
        title.setObjectName("SectionTitle")
        ll.addWidget(title)

        sub = QLabel(
            "Arraste as aulas dos cartões abaixo direto para o dia desejado no "
            "calendário ao lado. Organize o que assistir e acompanhe tudo."
        )
        sub.setObjectName("Muted2")
        sub.setWordWrap(True)
        ll.addWidget(sub)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.add_task_btn = QPushButton("＋ Tarefa")
        self.add_task_btn.setToolTip("Cria um cartão livre (tarefa) no Backlog.")
        self.add_task_btn.clicked.connect(self._add_free_task)
        self.add_lesson_btn = QPushButton("＋ Aula")
        self.add_lesson_btn.setObjectName("PrimaryButton")
        self.add_lesson_btn.setToolTip("Escolhe uma aula dos seus grupos/canais para planejar.")
        self.add_lesson_btn.clicked.connect(self._add_lesson_from_course)
        for b in (self.add_task_btn, self.add_lesson_btn):
            b.setMinimumHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            btn_row.addWidget(b)
        ll.addLayout(btn_row)

        # Mini-quadro de colunas (rolagem vertical), enxuto.
        self.col_backlog = self._make_column("📥  Backlog", COL_BACKLOG)
        self.col_week = self._make_column("📌  Esta semana", COL_WEEK)
        self.col_done = self._make_column("✅  Concluído", COL_DONE)
        for col in (self.col_backlog, self.col_week, self.col_done):
            ll.addWidget(col["frame"], col["stretch"])

        self.summary_label = QLabel()
        self.summary_label.setObjectName("Muted2")
        self.summary_label.setWordWrap(True)
        ll.addWidget(self.summary_label)

        return left

    def _make_column(self, title: str, key: str) -> dict:
        frame = QFrame()
        frame.setObjectName("KanbanColumn")
        v = QVBoxLayout(frame)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)

        head = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("KanbanTitle")
        head.addWidget(lbl)
        head.addStretch(1)
        count = QLabel("0")
        count.setObjectName("KanbanCount")
        head.addWidget(count)
        v.addLayout(head)

        listw = KanbanList(key)
        listw.item_dropped.connect(self._on_item_dropped)
        listw.reordered.connect(self._on_reordered)
        listw.customContextMenuRequested.connect(
            lambda pos, lw=listw: self._card_menu(lw, pos)
        )
        listw.setContextMenuPolicy(Qt.CustomContextMenu)
        listw.itemDoubleClicked.connect(self._on_card_double_clicked)
        # Backlog é o maior (de onde se puxa aulas); demais menores.
        stretch = 3 if key == COL_BACKLOG else 2
        listw.setMinimumHeight(90 if key == COL_BACKLOG else 70)
        v.addWidget(listw, 1)
        return {"frame": frame, "list": listw, "count": count, "key": key,
                "stretch": stretch}

    # ---- painel direito: calendário grande -------------------------------
    def _build_calendar_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(10)

        # Barra de navegação do mês.
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.prev_btn = QPushButton("‹")
        self.prev_btn.setObjectName("CalNav")
        self.prev_btn.setFixedSize(36, 32)
        self.prev_btn.setCursor(Qt.PointingHandCursor)
        self.prev_btn.clicked.connect(lambda: self._shift_month(-1))
        self.next_btn = QPushButton("›")
        self.next_btn.setObjectName("CalNav")
        self.next_btn.setFixedSize(36, 32)
        self.next_btn.setCursor(Qt.PointingHandCursor)
        self.next_btn.clicked.connect(lambda: self._shift_month(1))
        self.month_lbl = QLabel("")
        self.month_lbl.setObjectName("CalMonth")
        self.today_btn = QPushButton("Hoje")
        self.today_btn.setCursor(Qt.PointingHandCursor)
        self.today_btn.clicked.connect(self._go_today)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.next_btn)
        nav.addSpacing(6)
        nav.addWidget(self.month_lbl)
        nav.addStretch(1)
        nav.addWidget(self.today_btn)
        v.addLayout(nav)

        # Cabeçalho com os dias da semana.
        head = QGridLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self.weekday_labels: list[QLabel] = []
        for i, wd in enumerate(WEEKDAYS_SHORT):
            lbl = QLabel(wd.upper())
            lbl.setObjectName("CalWeekday")
            lbl.setAlignment(Qt.AlignCenter)
            head.addWidget(lbl, 0, i)
            head.setColumnStretch(i, 1)
            self.weekday_labels.append(lbl)
        v.addLayout(head)

        # Grade do mês (6 semanas x 7 dias) com rolagem caso falte espaço.
        scroll = QScrollArea()
        scroll.setObjectName("StudyScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        grid_host = QWidget()
        grid_host.setObjectName("StudyContent")
        self.grid = QGridLayout(grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        for r in range(6):
            self.grid.setRowStretch(r, 1)
        for col_i in range(7):
            self.grid.setColumnStretch(col_i, 1)
        for r in range(6):
            for col_i in range(7):
                cell = DayCell()
                cell.clicked.connect(self._on_cell_clicked)
                cell.card_dropped.connect(self._on_card_dropped_on_date)
                cell.item_activated.connect(self.open_lesson_requested.emit)
                self._cells.append(cell)
                self.grid.addWidget(cell, r, col_i)
        scroll.setWidget(grid_host)
        v.addWidget(scroll, 1)

        # Rodapé: dia selecionado.
        self.day_label = QLabel()
        self.day_label.setObjectName("CalSelectedDay")
        v.addWidget(self.day_label)

        return panel

    # ------------------------------------------------------------ helpers
    def _column_key_for(self, base_key: str) -> str:
        if base_key == "sched":
            return _sched_key(self.selected_date.toString("yyyy-MM-dd"))
        return base_key

    def _current_course_id(self) -> int | None:
        course = self.get_current_course()
        return getattr(course, "id", None) if course else None

    # ------------------------------------------------------------ navegação
    def _shift_month(self, delta: int) -> None:
        self.shown_month = self.shown_month.addMonths(delta)
        self.refresh()

    def _go_today(self) -> None:
        today = QDate.currentDate()
        self.selected_date = today
        self.shown_month = QDate(today.year(), today.month(), 1)
        self.refresh()

    def _on_cell_clicked(self, date: QDate) -> None:
        self.selected_date = date
        if date.month() != self.shown_month.month() or date.year() != self.shown_month.year():
            self.shown_month = QDate(date.year(), date.month(), 1)
        self.refresh()

    def _refresh_day_label(self) -> None:
        d = self.selected_date
        try:
            wd = WEEKDAYS_FULL[d.dayOfWeek() - 1]
        except Exception:  # noqa: BLE001
            wd = ""
        self.day_label.setText(
            f"📌 Dia selecionado: {wd.capitalize()}, {d.toString('dd/MM/yyyy')} "
            f"— arraste aulas para aqui ou para qualquer dia."
        )

    def _refresh_month_label(self) -> None:
        m = MONTHS_PT[self.shown_month.month() - 1]
        self.month_lbl.setText(f"{m.capitalize()} {self.shown_month.year()}")

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        self._refresh_month_label()
        self._refresh_day_label()

        for col in (self.col_backlog, self.col_week, self.col_done):
            col["list"].clear()

        items = self.db.list_plan_items()

        # Mapeia aulas por dia (para preencher as células).
        by_date: dict[str, list[dict]] = {}
        for it in items:
            key = it.get("column_key") or COL_BACKLOG
            if key == COL_BACKLOG:
                self._add_card_widget(self.col_backlog["list"], it)
            elif key == COL_WEEK:
                self._add_card_widget(self.col_week["list"], it)
            elif key == COL_DONE:
                self._add_card_widget(self.col_done["list"], it)
            elif _is_sched_key(key):
                d = _date_from_sched(key)
                by_date.setdefault(d, []).append(it)

        self.col_backlog["count"].setText(str(self.col_backlog["list"].count()))
        self.col_week["count"].setText(str(self.col_week["list"].count()))
        self.col_done["count"].setText(str(self.col_done["list"].count()))

        self._fill_calendar(by_date)
        self._refresh_summary(items, by_date)

    def _fill_calendar(self, by_date: dict[str, list[dict]]) -> None:
        # Primeiro dia exibido = segunda-feira da semana do dia 1.
        first = QDate(self.shown_month.year(), self.shown_month.month(), 1)
        offset = first.dayOfWeek() - 1  # 0 = segunda
        start = first.addDays(-offset)
        today = QDate.currentDate()
        for i, cell in enumerate(self._cells):
            date = start.addDays(i)
            date_str = date.toString("yyyy-MM-dd")
            in_month = (date.month() == self.shown_month.month()
                        and date.year() == self.shown_month.year())
            is_today = date == today
            selected = date == self.selected_date
            cell.set_palette(self._palette)
            cell.set_day(date, in_month, is_today, selected, by_date.get(date_str, []))

    def _add_card_widget(self, listw: QListWidget, data: dict) -> None:
        item = QListWidgetItem(listw)
        item.setData(ROLE_ITEM_ID, int(data["id"]))
        item.setData(ROLE_VIDEO_ID, int(data["video_id"]) if data.get("video_id") else 0)
        item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        widget = self._build_card(data)
        item.setSizeHint(widget.sizeHint())
        listw.addItem(item)
        listw.setItemWidget(item, widget)

    def _build_card(self, data: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("KanbanCard")
        watched = bool(data.get("v_watched_at"))
        accent = data.get("color") or CARD_COLORS[(int(data["id"])) % len(CARD_COLORS)]
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(3)

        title = data.get("title") or data.get("v_title") or "Aula"
        icon = "🎬" if data.get("video_id") else "📝"
        if watched:
            icon = "✅"
        title_lbl = QLabel(f"{icon}  {title}")
        title_lbl.setObjectName("KanbanCardTitle")
        title_lbl.setWordWrap(True)
        v.addWidget(title_lbl)

        meta_bits = []
        if data.get("course_title"):
            meta_bits.append(str(data["course_title"]))
        if data.get("subtitle"):
            meta_bits.append(str(data["subtitle"]))
        prog = data.get("v_progress")
        if prog and float(prog) > 0 and not watched:
            meta_bits.append(f"{int(float(prog) * 100)}% assistido")
        if meta_bits:
            meta = QLabel(" · ".join(meta_bits))
            meta.setObjectName("Muted2")
            meta.setWordWrap(True)
            v.addWidget(meta)

        card.setStyleSheet(f"#KanbanCard {{ border-left: 4px solid {accent}; }}")
        return card

    def _refresh_summary(self, items: list[dict], by_date: dict[str, list[dict]]) -> None:
        total = len(items)
        done = sum(1 for it in items if (it.get("column_key") == COL_DONE or it.get("v_watched_at")))
        week = sum(1 for it in items if it.get("column_key") == COL_WEEK)
        scheduled = sum(len(v) for v in by_date.values())
        self.summary_label.setText(
            f"📊 {total} cartões · {scheduled} agendados · {week} nesta semana · "
            f"{done} concluídos."
        )

    # ------------------------------------------------------------ drag/drop
    def _on_item_dropped(self, item_id: int, base_column_key: str) -> None:
        target_key = base_column_key
        sched_date = None
        explicit = True
        if base_column_key == "sched":
            target_key = self._column_key_for("sched")
            sched_date = self.selected_date.toString("yyyy-MM-dd")
        elif _is_sched_key(base_column_key):
            sched_date = _date_from_sched(base_column_key)
        else:
            sched_date = None  # sai da agenda
        self.db.move_plan_item(
            item_id, target_key, 9999,
            scheduled_date=sched_date, scheduled_date_explicit=explicit,
        )
        self.refresh()
        self.changed.emit()

    def _on_reordered(self, base_column_key: str, ids: list) -> None:
        if not ids:
            return
        key = self._column_key_for(base_column_key) if base_column_key == "sched" else base_column_key
        self.db.reorder_plan_column(key, [int(i) for i in ids])
        self.changed.emit()

    def _on_card_dropped_on_date(self, item_id: int, date) -> None:  # noqa: ANN001
        # Aceita QDate (vindo das células) ou string "YYYY-MM-DD" (API/menus).
        if isinstance(date, QDate):
            qd = date
            date_str = date.toString("yyyy-MM-dd")
        else:
            date_str = str(date)
            try:
                y, m, d = (int(x) for x in date_str.split("-"))
                qd = QDate(y, m, d)
            except Exception:  # noqa: BLE001
                qd = self.selected_date
        self.db.move_plan_item(
            item_id, _sched_key(date_str), 9999,
            scheduled_date=date_str, scheduled_date_explicit=True,
        )
        self.selected_date = qd
        # Garante que o mês exibido contenha o dia destino.
        self.shown_month = QDate(qd.year(), qd.month(), 1)
        self.refresh()
        self.changed.emit()

    # ------------------------------------------------------------ actions
    def _add_free_task(self) -> None:
        text, ok = QInputDialog.getText(self, "Nova tarefa", "Descrição da tarefa:")
        if not ok or not text.strip():
            return
        self.db.add_plan_item(
            text.strip(), column_key=COL_WEEK, course_id=self._current_course_id()
        )
        self.refresh()
        self.changed.emit()

    def _add_lesson_from_course(self) -> None:
        course = self.get_current_course()
        if not course:
            QMessageBox.information(
                self, "Planejador",
                "Selecione um curso na aba Aulas para escolher uma aula."
            )
            return
        videos = self.db.list_videos(course.id)
        if not videos:
            QMessageBox.information(
                self, "Planejador",
                "Este curso ainda não tem aulas. Sincronize na aba Aulas."
            )
            return
        labels = [f"{v.title}" for v in videos]
        choice, ok = QInputDialog.getItem(
            self, "Adicionar aula", "Escolha a aula para planejar:", labels, 0, False
        )
        if not ok:
            return
        video = videos[labels.index(choice)]
        # Adiciona ao Backlog (de lá o usuário arrasta para o dia desejado).
        self.add_video(video.id, column_key=COL_BACKLOG)

    def add_video(self, video_id: int, column_key: str = COL_BACKLOG,
                  silent: bool = False) -> bool:
        """Adiciona uma aula ao planejador. Usado pelo menu da aba Aulas."""
        video = self.db.get_video(int(video_id))
        if not video:
            return False
        if self.db.plan_item_exists_for_video(int(video_id)):
            if not silent:
                QMessageBox.information(
                    self, "Planejador", "Esta aula já está no planejamento."
                )
            return False
        course_id = getattr(video, "course_id", None) or self._current_course_id()
        sched_date = None
        key = column_key
        if column_key == "sched":
            sched_date = self.selected_date.toString("yyyy-MM-dd")
            key = _sched_key(sched_date)
        subtitle = getattr(video, "module", None) or getattr(video, "lesson", None)
        self.db.add_plan_item(
            video.title, column_key=key, video_id=video.id,
            course_id=course_id, subtitle=subtitle, scheduled_date=sched_date,
        )
        self.refresh()
        self.changed.emit()
        return True

    # ------------------------------------------------------------ card menu
    def _on_card_double_clicked(self, item: QListWidgetItem) -> None:
        vid = int(item.data(ROLE_VIDEO_ID) or 0)
        if vid:
            self.open_lesson_requested.emit(vid)

    def _card_menu(self, listw: QListWidget, pos) -> None:
        item = listw.itemAt(pos)
        if item is None:
            return
        item_id = int(item.data(ROLE_ITEM_ID) or 0)
        vid = int(item.data(ROLE_VIDEO_ID) or 0)
        menu = QMenu(self)
        open_act = menu.addAction("📲 Assistir no Telegram") if vid else None
        to_today = menu.addAction("🗓 Agendar para o dia selecionado")
        to_week = menu.addAction("📌 Mover para Esta semana")
        to_backlog = menu.addAction("📥 Mover para Backlog")
        to_done = menu.addAction("✅ Marcar como concluído")
        menu.addSeparator()
        recolor = menu.addAction("🎨 Mudar cor")
        delete = menu.addAction("🗑 Remover do planejamento")
        action = menu.exec(listw.mapToGlobal(pos))
        if action is None:
            return
        if open_act is not None and action == open_act:
            self.open_lesson_requested.emit(vid)
        elif action == to_today:
            self._on_item_dropped(item_id, "sched")
        elif action == to_week:
            self._on_item_dropped(item_id, COL_WEEK)
        elif action == to_backlog:
            self._on_item_dropped(item_id, COL_BACKLOG)
        elif action == to_done:
            self.db.move_plan_item(item_id, COL_DONE, 9999)
            self.refresh()
            self.changed.emit()
        elif action == recolor:
            idx = (item_id) % len(CARD_COLORS)
            color, ok = QInputDialog.getItem(
                self, "Cor do cartão", "Escolha uma cor:", CARD_COLORS, idx, False
            )
            if ok:
                self.db.update_plan_item(item_id, color=color)
                self.refresh()
        elif action == delete:
            self.db.delete_plan_item(item_id)
            self.refresh()
            self.changed.emit()

    # ------------------------------------------------------------ theming
    def apply_palette(self, colors: dict) -> None:
        self._palette = dict(colors or {})
        for cell in self._cells:
            cell.set_palette(self._palette)
        self.refresh()
