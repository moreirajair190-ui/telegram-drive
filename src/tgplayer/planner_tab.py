"""Aba Planejador 🗓 — organização das aulas no estilo de um calendário.

Visão geral
-----------
A tela foi pensada para funcionar como um **calendário** (à la Google Agenda):

* **Esquerda** — uma única lista enxuta, *"Aulas a planejar"*, com as aulas que
  ainda não têm data. É de onde você "puxa" (arrasta) as aulas para um dia.
* **Centro** — o **calendário grande** do mês. Cada dia mostra os eventos
  (aulas/anotações) daquele dia. Você pode:
    - **arrastar** uma aula da lista para a célula do dia (agendar);
    - **arrastar** um evento de um dia para outro (reagendar);
    - **clicar** num dia para abri-lo no painel da direita.
* **Direita** — o **painel do dia** selecionado: lista os eventos daquele dia,
  permite marcar como assistida, abrir no Telegram, mover/remover e escrever
  uma **anotação** livre (campo de texto salvo por dia).

Persistência em `plan_items` (ver db.py):
* itens sem data → `column_key = "backlog"`;
* itens com data → `column_key = "sched_<YYYY-MM-DD>"` e `scheduled_date`;
* `item_type` distingue aula (`lesson`) de anotação/tarefa (`note`).
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
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .db import Database


# Chaves de coluna usadas no banco. "sched_<data>" é dinâmica.
COL_BACKLOG = "backlog"
# Mantidas por compatibilidade com bancos/testes antigos (não usadas no UI novo).
COL_WEEK = "week"
COL_DONE = "done"

ROLE_ITEM_ID = Qt.UserRole + 1
ROLE_VIDEO_ID = Qt.UserRole + 2

CARD_COLORS = ["#7c5cff", "#22d3ee", "#34d399", "#fbbf24", "#f87171", "#a78bfa"]

# Tipo MIME usado no drag-and-drop dos cartões/eventos do planejador.
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


def _accent_for(data: dict) -> str:
    return data.get("color") or CARD_COLORS[int(data.get("id") or 0) % len(CARD_COLORS)]


def _icon_for(data: dict) -> str:
    if bool(data.get("v_watched_at")) or data.get("status") == "done":
        return "✅"
    if (data.get("item_type") or "lesson") == "note" and not data.get("video_id"):
        return "📝"
    return "🎬" if data.get("video_id") else "📝"


class BacklogList(QListWidget):
    """Lista 'Aulas a planejar' (itens sem data). Origem de arrasto p/ o calendário.

    O arrasto carrega um MIME próprio (`MIME_PLAN_ITEM`) com o id do item, para
    que qualquer célula do calendário consiga recebê-lo.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("KanbanList")
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSpacing(6)
        self.setUniformItemSizes(False)
        self.setWordWrap(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setDragEnabled(True)

    def startDrag(self, supported_actions) -> None:  # noqa: N802, ANN001
        item = self.currentItem()
        if item is None:
            return
        item_id = int(item.data(ROLE_ITEM_ID) or 0)
        if not item_id:
            return
        _begin_item_drag(self, item_id, self.itemWidget(item))


def _begin_item_drag(source: QWidget, item_id: int, preview: QWidget | None) -> None:
    """Inicia um QDrag carregando o id do item no MIME próprio."""
    mime = QMimeData()
    mime.setData(MIME_PLAN_ITEM, str(item_id).encode("utf-8"))
    drag = QDrag(source)
    drag.setMimeData(mime)
    if preview is not None:
        pix = preview.grab()
        drag.setPixmap(pix)
        drag.setHotSpot(QPoint(pix.width() // 2, min(pix.height() // 2, 16)))
    drag.exec(Qt.MoveAction)


class DayCell(QFrame):
    """Célula de um dia no calendário. É origem E destino de arrasto.

    - Soltar um item (da lista ou de outro dia) agenda/reagenda para este dia.
    - Pressionar e arrastar um *pill* daqui inicia o arrasto desse evento.
    - Clique simples seleciona o dia (abre no painel da direita).
    """

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
        self._items: list[dict] = []
        self._press_pos: QPoint | None = None
        self._press_item_id = 0

        v = QVBoxLayout(self)
        v.setContentsMargins(7, 5, 7, 5)
        v.setSpacing(3)

        # Cabeçalho de altura fixa (número do dia + contador), para nunca colidir
        # com os pills das aulas durante relayouts em massa do calendário.
        head_w = QWidget()
        head_w.setFixedHeight(18)
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

        # Os pills moram num widget hospedeiro real (não um layout solto), o que
        # garante geometria própria abaixo do cabeçalho.
        items_host = QWidget()
        self.items_box = QVBoxLayout(items_host)
        self.items_box.setContentsMargins(0, 0, 0, 0)
        self.items_box.setSpacing(2)
        v.addWidget(items_host, 0)
        v.addStretch(1)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(84)

    def set_palette(self, colors: dict) -> None:
        self._palette = dict(colors or {})

    def set_day(self, date: QDate, in_month: bool, is_today: bool,
                selected: bool, items: list[dict]) -> None:
        self.date = date
        self._in_month = in_month
        self._is_today = is_today
        self._selected = selected
        self._items = list(items or [])
        self.num_lbl.setText(str(date.day()))
        # Limpa pills antigos de forma SÍNCRONA (evita widget órfão com geometria
        # velha sobrepondo o cabeçalho até o próximo ciclo de eventos).
        while self.items_box.count():
            it = self.items_box.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        shown = items[:3]
        for it in shown:
            self.items_box.addWidget(self._make_pill(it))
        extra = len(items) - len(shown)
        if extra > 0:
            more = QLabel(f"+{extra} evento(s)")
            more.setObjectName("DayMore")
            self.items_box.addWidget(more)
        self.count_lbl.setText(str(len(items)) if items else "")
        self._apply_state_style()
        if self.layout() is not None:
            self.items_box.invalidate()
            self.items_box.activate()
            self.layout().invalidate()
            self.layout().activate()

    def _make_pill(self, data: dict) -> QWidget:
        accent = _accent_for(data)
        pill = QLabel()
        pill.setObjectName("DayPill")
        title = data.get("title") or data.get("v_title") or "Evento"
        icon = _icon_for(data)
        short = title if len(title) <= 22 else title[:21] + "…"
        pill.setText(f"{icon} {short}")
        pill.setToolTip(title)
        pill.setProperty("video_id", int(data.get("video_id") or 0))
        pill.setProperty("item_id", int(data.get("id") or 0))
        pill.setFixedHeight(19)
        pill.setStyleSheet(
            f"#DayPill {{ background: {self._pill_bg(accent)}; "
            f"color: {self._palette.get('text', '#222')}; "
            f"border-left: 3px solid {accent}; border-radius: 6px; "
            f"padding: 1px 6px; font-size: 11px; font-weight: 600; }}"
        )
        return pill

    def _pill_bg(self, accent: str) -> str:
        col = QColor(accent)
        col.setAlpha(46 if self._palette.get("name") == "dark" else 30)
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
            f"#DayCell:hover {{ border-color: {c.get('accent', '#7c5cff')}; }}"
            f"#DayCount {{ color: {c.get('muted2', '#888')}; font-size: 10px; "
            f"font-weight: 800; }}"
            f"#DayMore {{ color: {c.get('muted', '#888')}; font-size: 10px; "
            f"font-weight: 700; }}"
        )

    # -- interação ----------------------------------------------------------
    def _item_id_at(self, pos: QPoint) -> int:
        child = self.childAt(pos)
        if child is not None:
            return int(child.property("item_id") or 0)
        return 0

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self._press_item_id = self._item_id_at(self._press_pos)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802, ANN001
        # Arrastar um pill para outro dia = reagendar.
        if (self._press_pos is not None and self._press_item_id
                and (event.buttons() & Qt.LeftButton)):
            dist = (event.position().toPoint() - self._press_pos).manhattanLength()
            if dist >= 8:
                item_id = self._press_item_id
                self._press_item_id = 0
                self._press_pos = None
                _begin_item_drag(self, item_id, self._preview_for(item_id))
                return
        super().mouseMoveEvent(event)

    def _preview_for(self, item_id: int) -> QWidget | None:
        for i in range(self.items_box.count()):
            w = self.items_box.itemAt(i).widget()
            if w is not None and int(w.property("item_id") or 0) == item_id:
                return w
        return None

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802, ANN001
        # Clique simples (sem arrasto) seleciona o dia.
        if (self.date is not None and event.button() == Qt.LeftButton
                and self._press_pos is not None):
            dist = (event.position().toPoint() - self._press_pos).manhattanLength()
            if dist < 8:
                self.clicked.emit(self.date)
        self._press_pos = None
        self._press_item_id = 0
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802, ANN001
        vid = 0
        child = self.childAt(event.position().toPoint())
        if child is not None:
            vid = int(child.property("video_id") or 0)
        if vid:
            self.item_activated.emit(vid)
        elif self.date is not None:
            self.clicked.emit(self.date)
        super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.mimeData().hasFormat(MIME_PLAN_ITEM):
            event.acceptProposedAction()
            self._set_drop_highlight(True)
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.mimeData().hasFormat(MIME_PLAN_ITEM):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

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
    """Planejador estilo calendário: lista de aulas a planejar · calendário · dia."""

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
        self._note_item_id = 0          # id do plan_item que guarda a nota do dia
        self._note_dirty = False
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        root.addWidget(self._build_sidebar(), 0)
        root.addWidget(self._build_calendar_panel(), 1)
        root.addWidget(self._build_day_panel(), 0)

    # ---- coluna esquerda: aulas a planejar -------------------------------
    def _build_sidebar(self) -> QWidget:
        left = QFrame()
        left.setObjectName("Card")
        left.setMaximumWidth(290)
        left.setMinimumWidth(252)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 16, 16)
        ll.setSpacing(10)

        title = QLabel("🗓  PLANEJADOR")
        title.setObjectName("SectionTitle")
        ll.addWidget(title)

        sub = QLabel(
            "Arraste uma aula daqui para um dia do calendário. Clique num dia "
            "para ver os eventos e escrever anotações."
        )
        sub.setObjectName("Muted2")
        sub.setWordWrap(True)
        ll.addWidget(sub)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.add_task_btn = QPushButton("＋ Anotação")
        self.add_task_btn.setToolTip("Cria uma anotação/tarefa no dia selecionado.")
        self.add_task_btn.clicked.connect(self._add_free_task)
        self.add_lesson_btn = QPushButton("＋ Aula")
        self.add_lesson_btn.setObjectName("PrimaryButton")
        self.add_lesson_btn.setToolTip("Escolhe uma aula do curso atual para planejar.")
        self.add_lesson_btn.clicked.connect(self._add_lesson_from_course)
        for b in (self.add_task_btn, self.add_lesson_btn):
            b.setMinimumHeight(34)
            b.setCursor(Qt.PointingHandCursor)
            btn_row.addWidget(b)
        ll.addLayout(btn_row)

        head = QHBoxLayout()
        lbl = QLabel("📥  Aulas a planejar")
        lbl.setObjectName("KanbanTitle")
        head.addWidget(lbl)
        head.addStretch(1)
        self.backlog_count = QLabel("0")
        self.backlog_count.setObjectName("KanbanCount")
        head.addWidget(self.backlog_count)
        ll.addLayout(head)

        self.backlog = BacklogList()
        self.backlog.customContextMenuRequested.connect(
            lambda pos: self._card_menu(self.backlog, pos)
        )
        self.backlog.setContextMenuPolicy(Qt.CustomContextMenu)
        self.backlog.itemDoubleClicked.connect(self._on_card_double_clicked)
        ll.addWidget(self.backlog, 1)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("Muted2")
        self.summary_label.setWordWrap(True)
        ll.addWidget(self.summary_label)

        return left

    # ---- painel central: calendário grande -------------------------------
    def _build_calendar_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(10)

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

        return panel

    # ---- painel direito: dia selecionado ---------------------------------
    def _build_day_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Card")
        panel.setMinimumWidth(290)
        panel.setMaximumWidth(340)
        v = QVBoxLayout(panel)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)

        self.day_title = QLabel("")
        self.day_title.setObjectName("CalMonth")
        self.day_title.setWordWrap(True)
        v.addWidget(self.day_title)

        self.day_sub = QLabel("")
        self.day_sub.setObjectName("Muted2")
        self.day_sub.setWordWrap(True)
        v.addWidget(self.day_sub)

        ev_head = QLabel("Eventos do dia")
        ev_head.setObjectName("KanbanTitle")
        v.addWidget(ev_head)

        self.day_events = QListWidget()
        self.day_events.setObjectName("DayEvents")
        self.day_events.setWordWrap(True)
        self.day_events.setSelectionMode(QAbstractItemView.SingleSelection)
        self.day_events.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.day_events.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.day_events.setContextMenuPolicy(Qt.CustomContextMenu)
        self.day_events.customContextMenuRequested.connect(
            lambda pos: self._card_menu(self.day_events, pos)
        )
        self.day_events.itemDoubleClicked.connect(self._on_card_double_clicked)
        v.addWidget(self.day_events, 1)

        day_btns = QHBoxLayout()
        day_btns.setSpacing(8)
        self.day_add_lesson = QPushButton("＋ Aula")
        self.day_add_lesson.setCursor(Qt.PointingHandCursor)
        self.day_add_lesson.clicked.connect(self._add_lesson_to_selected_day)
        self.day_add_note = QPushButton("＋ Anotação")
        self.day_add_note.setCursor(Qt.PointingHandCursor)
        self.day_add_note.clicked.connect(self._add_free_task)
        for b in (self.day_add_lesson, self.day_add_note):
            b.setMinimumHeight(32)
            day_btns.addWidget(b)
        v.addLayout(day_btns)

        notes_head = QLabel("📝  Anotações do dia")
        notes_head.setObjectName("KanbanTitle")
        v.addWidget(notes_head)

        self.note_edit = QPlainTextEdit()
        self.note_edit.setObjectName("DayNote")
        self.note_edit.setPlaceholderText(
            "Escreva aqui o que quiser lembrar deste dia…"
        )
        self.note_edit.setMaximumHeight(140)
        self.note_edit.textChanged.connect(self._on_note_changed)
        v.addWidget(self.note_edit)

        self.save_note_btn = QPushButton("Salvar anotação")
        self.save_note_btn.setCursor(Qt.PointingHandCursor)
        self.save_note_btn.clicked.connect(self._save_day_note)
        v.addWidget(self.save_note_btn)

        return panel

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
        self._flush_note_if_dirty()
        self.selected_date = date
        if date.month() != self.shown_month.month() or date.year() != self.shown_month.year():
            self.shown_month = QDate(date.year(), date.month(), 1)
        self.refresh()

    def _refresh_month_label(self) -> None:
        m = MONTHS_PT[self.shown_month.month() - 1]
        self.month_lbl.setText(f"{m.capitalize()} {self.shown_month.year()}")

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        self._refresh_month_label()

        self.backlog.clear()
        items = self.db.list_plan_items()

        by_date: dict[str, list[dict]] = {}
        backlog_items: list[dict] = []
        for it in items:
            key = it.get("column_key") or COL_BACKLOG
            if _is_sched_key(key) or it.get("scheduled_date"):
                d = it.get("scheduled_date") or _date_from_sched(key)
                if d:
                    by_date.setdefault(d, []).append(it)
                    continue
            # Tudo que não tem data cai na lista "Aulas a planejar".
            backlog_items.append(it)

        for it in backlog_items:
            self._add_card_widget(self.backlog, it)
        self.backlog_count.setText(str(len(backlog_items)))

        self._fill_calendar(by_date)
        self._refresh_day_panel(by_date.get(self.selected_date.toString("yyyy-MM-dd"), []))
        self._refresh_summary(items, by_date)

    def _fill_calendar(self, by_date: dict[str, list[dict]]) -> None:
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
            # Não mostramos a "nota do dia" como pill (ela vai no painel lateral).
            visible = [it for it in by_date.get(date_str, [])
                       if not self._is_day_note(it)]
            cell.set_day(date, in_month, is_today, selected, visible)

    # ------------------------------------------------------------ painel do dia
    def _is_day_note(self, it: dict) -> bool:
        """Nota do dia = anotação livre sem título 'real' (guardada só p/ texto)."""
        return ((it.get("item_type") or "lesson") == "note"
                and (it.get("title") or "").strip() == "•nota do dia•")

    def _refresh_day_panel(self, day_items: list[dict]) -> None:
        d = self.selected_date
        try:
            wd = WEEKDAYS_FULL[d.dayOfWeek() - 1]
        except Exception:  # noqa: BLE001
            wd = ""
        self.day_title.setText(f"{d.day()} de {MONTHS_PT[d.month() - 1]}")
        self.day_sub.setText(f"{wd.capitalize()} · {d.toString('dd/MM/yyyy')}")

        # Lista de eventos (exceto a nota oculta do dia).
        self.day_events.clear()
        events = [it for it in day_items if not self._is_day_note(it)]
        if not events:
            ph = QListWidgetItem("Nenhum evento. Arraste uma aula para cá ou use os botões abaixo.")
            ph.setFlags(Qt.NoItemFlags)
            ph.setForeground(QColor(self._palette.get("muted2", "#888")))
            self.day_events.addItem(ph)
        else:
            for it in events:
                self._add_day_event_widget(it)

        # Nota do dia.
        note_item = next((it for it in day_items if self._is_day_note(it)), None)
        self._note_item_id = int(note_item["id"]) if note_item else 0
        self.note_edit.blockSignals(True)
        self.note_edit.setPlainText((note_item or {}).get("note") or "")
        self.note_edit.blockSignals(False)
        self._note_dirty = False

    def _add_day_event_widget(self, data: dict) -> None:
        item = QListWidgetItem(self.day_events)
        item.setData(ROLE_ITEM_ID, int(data["id"]))
        item.setData(ROLE_VIDEO_ID, int(data["video_id"]) if data.get("video_id") else 0)
        widget = self._build_card(data, compact=True)
        item.setSizeHint(widget.sizeHint())
        self.day_events.addItem(item)
        self.day_events.setItemWidget(item, widget)

    def _on_note_changed(self) -> None:
        self._note_dirty = True

    def _flush_note_if_dirty(self) -> None:
        if self._note_dirty:
            self._save_day_note(silent=True)

    def _save_day_note(self, silent: bool = False) -> None:
        text = self.note_edit.toPlainText().strip()
        date_str = self.selected_date.toString("yyyy-MM-dd")
        if self._note_item_id:
            if text:
                self.db.update_plan_item(self._note_item_id, note=text)
            else:
                # Nota esvaziada → remove o item oculto.
                self.db.delete_plan_item(self._note_item_id)
                self._note_item_id = 0
        elif text:
            self._note_item_id = self.db.add_plan_item(
                "•nota do dia•", column_key=_sched_key(date_str),
                course_id=self._current_course_id(), scheduled_date=date_str,
                note=text, item_type="note",
            )
        self._note_dirty = False
        if not silent:
            self.refresh()
            self.changed.emit()

    # ------------------------------------------------------------ cards
    def _current_course_id(self) -> int | None:
        course = self.get_current_course()
        return getattr(course, "id", None) if course else None

    def _add_card_widget(self, listw: QListWidget, data: dict) -> None:
        item = QListWidgetItem(listw)
        item.setData(ROLE_ITEM_ID, int(data["id"]))
        item.setData(ROLE_VIDEO_ID, int(data["video_id"]) if data.get("video_id") else 0)
        item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        widget = self._build_card(data)
        item.setSizeHint(widget.sizeHint())
        listw.addItem(item)
        listw.setItemWidget(item, widget)

    def _build_card(self, data: dict, compact: bool = False) -> QWidget:
        card = QFrame()
        card.setObjectName("KanbanCard")
        watched = bool(data.get("v_watched_at")) or data.get("status") == "done"
        accent = _accent_for(data)
        v = QVBoxLayout(card)
        if compact:
            v.setContentsMargins(10, 5, 10, 5)
            v.setSpacing(1)
        else:
            v.setContentsMargins(10, 7, 10, 7)
            v.setSpacing(2)

        title = data.get("title") or data.get("v_title") or "Evento"
        icon = _icon_for(data)
        title_lbl = QLabel(f"{icon}  {title}")
        title_lbl.setObjectName("KanbanCardTitle")
        title_lbl.setWordWrap(True)
        v.addWidget(title_lbl)

        meta_bits = []
        if not compact and data.get("course_title"):
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
        real = [it for it in items if not self._is_day_note(it)]
        total = len(real)
        done = sum(1 for it in real
                   if (it.get("status") == "done" or it.get("v_watched_at")))
        scheduled = sum(len([it for it in v if not self._is_day_note(it)])
                        for v in by_date.values())
        self.summary_label.setText(
            f"📊 {total} aulas · {scheduled} agendadas · {done} concluídas."
        )

    # ------------------------------------------------------------ drag/drop
    def _on_card_dropped_on_date(self, item_id: int, date) -> None:  # noqa: ANN001
        # Aceita QDate (das células) ou string "YYYY-MM-DD" (API/menus/testes).
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
                date_str = qd.toString("yyyy-MM-dd")
        self.db.set_plan_item_date(item_id, date_str)
        self.selected_date = qd
        self.shown_month = QDate(qd.year(), qd.month(), 1)
        self.refresh()
        self.changed.emit()

    # mantido por compatibilidade com versões antigas (kanban) e testes.
    def _on_item_dropped(self, item_id: int, base_column_key: str) -> None:
        if base_column_key == "sched":
            self._on_card_dropped_on_date(
                item_id, self.selected_date.toString("yyyy-MM-dd")
            )
            return
        if _is_sched_key(base_column_key):
            self._on_card_dropped_on_date(item_id, _date_from_sched(base_column_key))
            return
        # Qualquer outra coluna (backlog/week/done) → desagenda (volta p/ lista).
        if base_column_key == COL_DONE:
            self.db.move_plan_item(item_id, COL_BACKLOG, 9999,
                                   scheduled_date=None, scheduled_date_explicit=True)
            self.db.update_plan_item(item_id, status="done")
        else:
            self.db.set_plan_item_date(item_id, None)
        self.refresh()
        self.changed.emit()

    def _on_reordered(self, base_column_key: str, ids: list) -> None:
        if not ids:
            return
        self.db.reorder_plan_column(base_column_key, [int(i) for i in ids])
        self.changed.emit()

    # ------------------------------------------------------------ actions
    def _add_free_task(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Nova anotação",
            f"Anotação para {self.selected_date.toString('dd/MM/yyyy')}:",
        )
        if not ok or not text.strip():
            return
        date_str = self.selected_date.toString("yyyy-MM-dd")
        self.db.add_plan_item(
            text.strip(), column_key=_sched_key(date_str),
            course_id=self._current_course_id(), scheduled_date=date_str,
            item_type="note",
        )
        self.refresh()
        self.changed.emit()

    def _pick_lesson(self) -> object | None:
        course = self.get_current_course()
        if not course:
            QMessageBox.information(
                self, "Planejador",
                "Selecione um curso na aba Aulas para escolher uma aula."
            )
            return None
        videos = self.db.list_videos(course.id)
        if not videos:
            QMessageBox.information(
                self, "Planejador",
                "Este curso ainda não tem aulas. Sincronize na aba Aulas."
            )
            return None
        labels = [f"{v.title}" for v in videos]
        choice, ok = QInputDialog.getItem(
            self, "Adicionar aula", "Escolha a aula para planejar:", labels, 0, False
        )
        if not ok:
            return None
        return videos[labels.index(choice)]

    def _add_lesson_from_course(self) -> None:
        video = self._pick_lesson()
        if video is not None:
            # Vai para a lista "Aulas a planejar" (de lá arrasta-se para o dia).
            self.add_video(video.id, column_key=COL_BACKLOG)

    def _add_lesson_to_selected_day(self) -> None:
        video = self._pick_lesson()
        if video is not None:
            self.add_video(video.id, column_key="sched")

    def add_video(self, video_id: int, column_key: str = COL_BACKLOG,
                  silent: bool = False) -> bool:
        """Adiciona uma aula ao planejador. Usado também pela aba Aulas."""
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
            item_type="lesson",
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
        if not item_id:
            return
        menu = QMenu(self)
        open_act = menu.addAction("📲 Assistir no Telegram") if vid else None
        to_day = menu.addAction("🗓 Agendar para o dia selecionado")
        unschedule = menu.addAction("📥 Tirar do dia (voltar p/ lista)")
        done_act = menu.addAction("✅ Marcar como concluído")
        menu.addSeparator()
        recolor = menu.addAction("🎨 Mudar cor")
        delete = menu.addAction("🗑 Remover do planejamento")
        action = menu.exec(listw.mapToGlobal(pos))
        if action is None:
            return
        if open_act is not None and action == open_act:
            self.open_lesson_requested.emit(vid)
        elif action == to_day:
            self._on_card_dropped_on_date(
                item_id, self.selected_date.toString("yyyy-MM-dd")
            )
        elif action == unschedule:
            self.db.set_plan_item_date(item_id, None)
            self.refresh()
            self.changed.emit()
        elif action == done_act:
            self.db.update_plan_item(item_id, status="done")
            self.refresh()
            self.changed.emit()
        elif action == recolor:
            idx = item_id % len(CARD_COLORS)
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
