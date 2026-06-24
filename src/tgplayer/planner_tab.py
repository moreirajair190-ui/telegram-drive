"""Aba Planejador 🗓 — organização das aulas em estilo Kanban + calendário.

Visão geral
-----------
Inspirado em quadros Trello/Kanban, esta tela permite:

* Arrastar e soltar cartões (aulas ou tarefas livres) entre colunas:
  - "📥 Backlog"      → aulas guardadas para planejar depois;
  - "🗓 Para hoje"    → aulas agendadas para o dia selecionado no calendário;
  - "📌 Esta semana"  → as "tarefas da semana";
  - "✅ Concluído"    → aulas já assistidas/marcadas.
* Um calendário integrado e funcional: ao clicar num dia, a coluna "Para hoje"
  passa a mostrar (e agendar) as aulas daquele dia. Dias com aulas agendadas
  ficam destacados.
* Adicionar aulas vindas dos grupos/canais do Telegram (via menu de contexto da
  aba Aulas → "🗓 Adicionar ao planejamento" ou pelo botão "+ Aula" daqui).

Persistência em `plan_items` (ver db.py). O drag-and-drop usa QListWidget com
`InternalMove`/`DragDrop`; cada cartão carrega o `plan_item_id` para que a
reordenação/movimentação seja gravada no banco.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCalendarWidget,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
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


def _sched_key(date_str: str) -> str:
    return f"sched_{date_str}"


def _is_sched_key(key: str) -> bool:
    return bool(key) and key.startswith("sched_")


def _date_from_sched(key: str) -> str:
    return key[len("sched_"):] if _is_sched_key(key) else ""


class KanbanList(QListWidget):
    """Coluna do Kanban com drag-and-drop entre colunas.

    Emite `item_dropped(item_id, target_column_key)` quando um cartão é solto
    aqui (vindo de outra coluna ou reordenado dentro da mesma), para que o pai
    persista a mudança no banco.
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

    def dropEvent(self, event) -> None:  # noqa: N802
        source = event.source()
        if isinstance(source, KanbanList) and source is not self:
            # Drop vindo de OUTRA coluna: capturamos o id e recriamos aqui.
            item = source.currentItem()
            if item is not None:
                item_id = int(item.data(ROLE_ITEM_ID) or 0)
                if item_id:
                    event.setDropAction(Qt.MoveAction)
                    event.accept()
                    self.item_dropped.emit(item_id, self.column_key)
                    return
        # Reordenação interna: deixa o Qt mover e depois grava a nova ordem.
        super().dropEvent(event)
        ids = []
        for i in range(self.count()):
            it = self.item(i)
            iid = int(it.data(ROLE_ITEM_ID) or 0)
            if iid:
                ids.append(iid)
        self.reordered.emit(self.column_key, ids)


class PlannerCalendar(QCalendarWidget):
    """Calendário que aceita soltar cartões para agendá-los num dia."""

    card_dropped_on_date = Signal(int, str)  # (plan_item_id, YYYY-MM-DD)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("PlannerCalendar")
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setNavigationBarVisible(True)
        self.setFirstDayOfWeek(Qt.Monday)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if isinstance(event.source(), KanbanList):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if isinstance(event.source(), KanbanList):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        source = event.source()
        if isinstance(source, KanbanList):
            item = source.currentItem()
            if item is not None:
                item_id = int(item.data(ROLE_ITEM_ID) or 0)
                # Tabela do calendário fica no viewport: usa a data selecionada
                # como alvo (o usuário clica no dia e arrasta o cartão).
                target = self.selectedDate().toString("yyyy-MM-dd")
                if item_id:
                    event.acceptProposedAction()
                    self.card_dropped_on_date.emit(item_id, target)
                    return
        super().dropEvent(event)


class PlannerTab(QWidget):
    """Planejador completo: calendário + quadro Kanban com drag-and-drop."""

    # Emite quando algo muda (para o dashboard de Acompanhamento se atualizar).
    changed = Signal()
    # Pedido para abrir uma aula (video_id) no Telegram a partir de um cartão.
    open_lesson_requested = Signal(int)

    def __init__(self, db: Database, get_current_course: Callable[[], object | None]) -> None:
        super().__init__()
        self.db = db
        self.get_current_course = get_current_course
        self._palette: dict = {}
        self.selected_date = QDate.currentDate()
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        # ---- Coluna esquerda: calendário + ações --------------------------
        left = QFrame()
        left.setObjectName("Card")
        left.setMaximumWidth(340)
        left.setMinimumWidth(300)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 16, 16)
        ll.setSpacing(12)

        title = QLabel("🗓  PLANEJADOR DE AULAS")
        title.setObjectName("SectionTitle")
        ll.addWidget(title)

        sub = QLabel(
            "Arraste os cartões entre as colunas ou solte-os no dia desejado do "
            "calendário. Organize o que assistir hoje, nesta semana ou depois."
        )
        sub.setObjectName("Muted2")
        sub.setWordWrap(True)
        ll.addWidget(sub)

        self.calendar = PlannerCalendar()
        self.calendar.selectionChanged.connect(self._on_date_changed)
        self.calendar.card_dropped_on_date.connect(self._on_card_dropped_on_date)
        ll.addWidget(self.calendar)

        self.day_label = QLabel()
        self.day_label.setObjectName("SectionTitle")
        ll.addWidget(self.day_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.add_task_btn = QPushButton("＋ Tarefa")
        self.add_task_btn.setToolTip("Cria um cartão livre (tarefa) na coluna selecionada.")
        self.add_task_btn.clicked.connect(self._add_free_task)
        self.add_lesson_btn = QPushButton("＋ Aula")
        self.add_lesson_btn.setObjectName("PrimaryButton")
        self.add_lesson_btn.setToolTip("Escolhe uma aula dos seus grupos/canais para planejar.")
        self.add_lesson_btn.clicked.connect(self._add_lesson_from_course)
        for b in (self.add_task_btn, self.add_lesson_btn):
            b.setMinimumHeight(36)
            b.setCursor(Qt.PointingHandCursor)
            btn_row.addWidget(b)
        ll.addLayout(btn_row)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("Muted2")
        self.summary_label.setWordWrap(True)
        ll.addWidget(self.summary_label)
        ll.addStretch(1)

        root.addWidget(left)

        # ---- Quadro Kanban (colunas com scroll horizontal) ----------------
        board_scroll = QScrollArea()
        board_scroll.setObjectName("StudyScroll")
        board_scroll.setWidgetResizable(True)
        board_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        board = QWidget()
        board.setObjectName("StudyContent")
        self.board_layout = QHBoxLayout(board)
        self.board_layout.setContentsMargins(2, 2, 2, 2)
        self.board_layout.setSpacing(14)

        # Colunas fixas. A coluna "Para hoje" usa a data selecionada.
        self.col_backlog = self._make_column("📥  Backlog", COL_BACKLOG,
                                              "Aulas guardadas para planejar")
        self.col_today = self._make_column("🗓  Para o dia", "sched",
                                           "Agendadas para o dia escolhido")
        self.col_week = self._make_column("📌  Esta semana", COL_WEEK,
                                          "Tarefas da semana")
        self.col_done = self._make_column("✅  Concluído", COL_DONE,
                                          "Aulas já assistidas")
        for col in (self.col_backlog, self.col_today, self.col_week, self.col_done):
            self.board_layout.addWidget(col["frame"])
        self.board_layout.addStretch(1)

        board_scroll.setWidget(board)
        root.addWidget(board_scroll, 1)

    def _make_column(self, title: str, key: str, hint: str) -> dict:
        frame = QFrame()
        frame.setObjectName("KanbanColumn")
        frame.setMinimumWidth(248)
        frame.setMaximumWidth(320)
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("KanbanTitle")
        head.addWidget(lbl)
        head.addStretch(1)
        count = QLabel("0")
        count.setObjectName("KanbanCount")
        head.addWidget(count)
        v.addLayout(head)

        hint_lbl = QLabel(hint)
        hint_lbl.setObjectName("Muted2")
        hint_lbl.setWordWrap(True)
        v.addWidget(hint_lbl)

        listw = KanbanList(key)
        listw.item_dropped.connect(self._on_item_dropped)
        listw.reordered.connect(self._on_reordered)
        listw.customContextMenuRequested.connect(
            lambda pos, lw=listw: self._card_menu(lw, pos)
        )
        listw.setContextMenuPolicy(Qt.CustomContextMenu)
        listw.itemDoubleClicked.connect(self._on_card_double_clicked)
        v.addWidget(listw, 1)

        return {"frame": frame, "list": listw, "count": count, "key": key}

    # ------------------------------------------------------------ helpers
    def _column_key_for(self, base_key: str) -> str:
        """Resolve a chave real da coluna (a de 'hoje' depende da data)."""
        if base_key == "sched":
            return _sched_key(self.selected_date.toString("yyyy-MM-dd"))
        return base_key

    def _current_course_id(self) -> int | None:
        course = self.get_current_course()
        return getattr(course, "id", None) if course else None

    # ------------------------------------------------------------ data flow
    def _on_date_changed(self) -> None:
        self.selected_date = self.calendar.selectedDate()
        self._refresh_day_label()
        self.refresh()

    def _refresh_day_label(self) -> None:
        d = self.selected_date
        weekdays = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
        try:
            wd = weekdays[d.dayOfWeek() - 1]
        except Exception:  # noqa: BLE001
            wd = ""
        self.day_label.setText(f"📌 {wd.capitalize()}, {d.toString('dd/MM/yyyy')}")

    def refresh(self) -> None:
        self._refresh_day_label()
        # Limpa as listas.
        for col in (self.col_backlog, self.col_today, self.col_week, self.col_done):
            col["list"].clear()

        today_key = self._column_key_for("sched")
        items = self.db.list_plan_items()
        counts = {COL_BACKLOG: 0, today_key: 0, COL_WEEK: 0, COL_DONE: 0}

        for it in items:
            key = it.get("column_key") or COL_BACKLOG
            # Sincroniza status concluído com o estado real da aula.
            if it.get("v_watched_at") and key != COL_DONE:
                # mantém na coluna, mas marca visualmente como assistida
                pass
            target_col = None
            if key == COL_BACKLOG:
                target_col = self.col_backlog
            elif key == COL_WEEK:
                target_col = self.col_week
            elif key == COL_DONE:
                target_col = self.col_done
            elif _is_sched_key(key):
                if key == today_key:
                    target_col = self.col_today
                else:
                    # agendada para outro dia: não aparece na coluna de hoje
                    continue
            if target_col is not None:
                self._add_card_widget(target_col["list"], it)
                counts[key if key in counts else COL_BACKLOG] = counts.get(key, 0) + 1

        self.col_backlog["count"].setText(str(self.col_backlog["list"].count()))
        self.col_today["count"].setText(str(self.col_today["list"].count()))
        self.col_week["count"].setText(str(self.col_week["list"].count()))
        self.col_done["count"].setText(str(self.col_done["list"].count()))

        self._mark_calendar()
        self._refresh_summary(items)

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
        card.setProperty("accent", accent)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(4)

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

        # Barra de acento colorida no topo do cartão.
        card.setStyleSheet(
            f"#KanbanCard {{ border-left: 4px solid {accent}; }}"
        )
        return card

    def _mark_calendar(self) -> None:
        # Limpa formatação anterior e destaca dias com aulas agendadas.
        empty = QTextCharFormat()
        # Reaplicar formato em um range razoável (mês visível +/-).
        base = QDate(self.calendar.yearShown(), self.calendar.monthShown(), 1)
        for off in range(-40, 75):
            self.calendar.setDateTextFormat(base.addDays(off), empty)

        counts = self.db.plan_counts_by_date()
        accent = self._palette.get("accent", "#7c5cff")
        for date_str, n in counts.items():
            if n <= 0:
                continue
            try:
                y, m, d = (int(x) for x in date_str.split("-"))
                qd = QDate(y, m, d)
            except Exception:  # noqa: BLE001
                continue
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._palette.get("accent_text", "#ffffff")))
            fmt.setBackground(QColor(accent))
            fmt.setFontWeight(75)
            self.calendar.setDateTextFormat(qd, fmt)

    def _refresh_summary(self, items: list[dict]) -> None:
        total = len(items)
        done = sum(1 for it in items if (it.get("column_key") == COL_DONE or it.get("v_watched_at")))
        week = sum(1 for it in items if it.get("column_key") == COL_WEEK)
        today_key = self._column_key_for("sched")
        today = sum(1 for it in items if it.get("column_key") == today_key)
        self.summary_label.setText(
            f"📊 {total} cartões · {today} para o dia · {week} nesta semana · "
            f"{done} concluídos."
        )

    # ------------------------------------------------------------ drag/drop
    def _on_item_dropped(self, item_id: int, base_column_key: str) -> None:
        target_key = base_column_key
        if base_column_key == "sched":
            target_key = self._column_key_for("sched")
        sched_date = None
        explicit = False
        if _is_sched_key(target_key):
            sched_date = _date_from_sched(target_key)
            explicit = True
        elif base_column_key in (COL_BACKLOG, COL_WEEK):
            # Sai da agenda → limpa a data.
            sched_date = None
            explicit = True
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

    def _on_card_dropped_on_date(self, item_id: int, date_str: str) -> None:
        self.db.move_plan_item(
            item_id, _sched_key(date_str), 9999,
            scheduled_date=date_str, scheduled_date_explicit=True,
        )
        # Seleciona a data alvo para o usuário ver o resultado.
        try:
            y, m, d = (int(x) for x in date_str.split("-"))
            self.calendar.setSelectedDate(QDate(y, m, d))
        except Exception:  # noqa: BLE001
            pass
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
        self.add_video(video.id, column_key="sched")

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
        to_today = menu.addAction("🗓 Mover para o dia selecionado")
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
        self._mark_calendar()
