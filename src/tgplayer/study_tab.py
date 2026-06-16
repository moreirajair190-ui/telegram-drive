"""Aba Acompanhamento — dashboard de estudos integrado ao TgPlayer.

A lógica desta aba foi redesenhada a partir das ideias do projeto de referência
`yasmedstudies`: cards de métricas, meta semanal, gráficos, Pomodoro, tarefas e
atividade recente. Tudo foi adaptado para PySide6/SQLite e integrado aos dados
reais do TgPlayer: aulas assistidas, progresso dos cursos, sessões Pomodoro e
checklist local.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .charts import BarChart, DonutChart, HBarChart
from .db import Database

PRIORITY_MARKS = {0: "○", 1: "◐", 2: "●"}
PRIORITY_LABELS = ["Baixa", "Média", "Alta"]


def _fmt_minutes_from_seconds(seconds: int) -> str:
    minutes = max(0, int(round(seconds / 60)))
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}h {m:02d}min" if m else f"{h}h"
    return f"{minutes}min"


def _pct(done: int, total: int) -> int:
    return int(round((done / total) * 100)) if total else 0


def _card(title: str = "", subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    if title:
        header = QVBoxLayout()
        header.setSpacing(2)
        lbl = QLabel(title)
        lbl.setObjectName("SectionTitle")
        header.addWidget(lbl)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Muted2")
            sub.setWordWrap(True)
            header.addWidget(sub)
        layout.addLayout(header)
    return frame, layout


class MetricCard(QFrame):
    """Card de métrica inspirado no dashboard do YasMedStudies."""

    def __init__(self, icon: str, label: str, value: str = "—", sub: str = "") -> None:
        super().__init__()
        self.setObjectName("StatCard")
        self.setMinimumHeight(92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self.label = QLabel(f"{icon}  {label}")
        self.label.setObjectName("CardLabel")
        self.value = QLabel(value)
        self.value.setObjectName("BigNumber")
        self.sub = QLabel(sub)
        self.sub.setObjectName("Muted2")
        self.sub.setWordWrap(True)

        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.sub)
        layout.addStretch(1)

    def set_values(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)


class GoalCard(QFrame):
    """Meta semanal simples, local, editável e sem dependência externa."""

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.setObjectName("Card")
        self.setMinimumHeight(150)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        top = QHBoxLayout()
        title = QLabel("🎯  META SEMANAL")
        title.setObjectName("SectionTitle")
        top.addWidget(title)
        top.addStretch(1)
        self.goal_spin = QSpinBox()
        self.goal_spin.setRange(1, 120)
        self.goal_spin.setSuffix(" h/sem")
        self.goal_spin.setButtonSymbols(QSpinBox.NoButtons)
        self.goal_spin.setAlignment(Qt.AlignCenter)
        self.goal_spin.setFixedWidth(92)
        self.goal_spin.setValue(int(db.get_setting("weekly_goal_hours", "20") or 20))
        self.goal_spin.valueChanged.connect(self._save_goal)
        top.addWidget(self.goal_spin)
        layout.addLayout(top)

        self.value = QLabel("0%")
        self.value.setObjectName("BigNumber")
        self.caption = QLabel("Sem estudos registrados nesta semana")
        self.caption.setObjectName("Muted2")
        self.caption.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)

        layout.addWidget(self.value)
        layout.addWidget(self.progress)
        layout.addWidget(self.caption)

    def _save_goal(self) -> None:
        self.db.set_setting("weekly_goal_hours", str(self.goal_spin.value()))
        self.refresh(self.db.week_study_seconds())

    def refresh(self, week_seconds: int) -> None:
        goal_seconds = max(1, self.goal_spin.value() * 3600)
        pct = min(1000, int(week_seconds / goal_seconds * 1000))
        self.progress.setValue(pct)
        pct_text = int(round(pct / 10))
        missing = max(0, goal_seconds - week_seconds)
        self.value.setText(f"{pct_text}%")
        self.caption.setText(
            f"{_fmt_minutes_from_seconds(week_seconds)} de {_fmt_minutes_from_seconds(goal_seconds)} · "
            f"faltam {_fmt_minutes_from_seconds(missing)}"
        )


class PomodoroWidget(QFrame):
    """Timer Pomodoro mais organizado e integrado ao log de estudos."""

    session_completed = Signal(int, str)  # segundos, fase

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.setMinimumHeight(330)
        self.phase = "foco"
        self.cycles = 0
        self.remaining = 0
        self.running = False
        self.elapsed_in_phase = 0

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("⏱️  ESTUDAR AGORA")
        title.setObjectName("SectionTitle")
        top.addWidget(title)
        top.addStretch(1)
        self.phase_label = QLabel("🎯 Foco")
        self.phase_label.setObjectName("PomoPhase")
        top.addWidget(self.phase_label)
        layout.addLayout(top)

        self.time_label = QLabel("25:00")
        self.time_label.setObjectName("PomoTime")
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setMinimumHeight(58)
        layout.addWidget(self.time_label)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(10)
        layout.addWidget(self.progress)

        self.cycle_label = QLabel("Ciclos de foco concluídos hoje: 0")
        self.cycle_label.setObjectName("Muted2")
        self.cycle_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.cycle_label)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.start_btn = QPushButton("▶ Iniciar")
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.clicked.connect(self.toggle)
        self.reset_btn = QPushButton("↺ Zerar")
        self.reset_btn.clicked.connect(self.reset)
        self.skip_btn = QPushButton("⤼ Pular")
        self.skip_btn.clicked.connect(self.skip_phase)
        for btn in (self.start_btn, self.reset_btn, self.skip_btn):
            btn.setMinimumHeight(38)
        controls.addWidget(self.start_btn, 2)
        controls.addWidget(self.reset_btn, 1)
        controls.addWidget(self.skip_btn, 1)
        layout.addLayout(controls)

        presets = QHBoxLayout()
        presets.setSpacing(8)
        for label, values, tip in (
            ("Clássico 25/5", (25, 5, 15), "25 min de foco, 5 min de pausa curta e 15 min de pausa longa"),
            ("Prova 50/10", (50, 10, 20), "50 min de foco e pausas maiores"),
            ("Intensivo 90/15", (90, 15, 30), "Bloco longo para revisão ou simulado"),
        ):
            btn = QPushButton(label)
            btn.setObjectName("PomoPreset")
            btn.setMinimumHeight(34)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _checked=False, vals=values: self.apply_preset(*vals))
            presets.addWidget(btn)
        layout.addLayout(presets)

        cfg = QGridLayout()
        cfg.setHorizontalSpacing(10)
        cfg.setVerticalSpacing(6)
        for col, text in enumerate(("Foco", "Pausa curta", "Pausa longa")):
            lbl = QLabel(text)
            lbl.setObjectName("Muted2")
            cfg.addWidget(lbl, 0, col)
        self.focus_spin = self._spin(int(db.get_setting("pomo_focus", "25") or 25), 1, 180)
        self.short_spin = self._spin(int(db.get_setting("pomo_short", "5") or 5), 1, 60)
        self.long_spin = self._spin(int(db.get_setting("pomo_long", "15") or 15), 1, 120)
        for col, sp in enumerate((self.focus_spin, self.short_spin, self.long_spin)):
            sp.valueChanged.connect(self._save_config)
            cfg.addWidget(sp, 1, col)
        layout.addLayout(cfg)
        layout.addStretch(1)

        self.reset()
        self.refresh_cycle_label()

    def _spin(self, value: int, lo: int, hi: int) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(value)
        sp.setButtonSymbols(QSpinBox.NoButtons)
        sp.setObjectName("PomoSpin")
        sp.setMinimumHeight(38)
        sp.setAlignment(Qt.AlignCenter)
        sp.setSuffix(" min")
        return sp

    def apply_preset(self, focus: int, short: int, long: int) -> None:
        was_running = self.running
        if was_running:
            self.toggle()
        self.focus_spin.setValue(focus)
        self.short_spin.setValue(short)
        self.long_spin.setValue(long)
        self._save_config()

    def _save_config(self) -> None:
        self.db.set_setting("pomo_focus", str(self.focus_spin.value()))
        self.db.set_setting("pomo_short", str(self.short_spin.value()))
        self.db.set_setting("pomo_long", str(self.long_spin.value()))
        if not self.running:
            self.reset()

    def _phase_seconds(self) -> int:
        return {
            "foco": self.focus_spin.value() * 60,
            "curta": self.short_spin.value() * 60,
            "longa": self.long_spin.value() * 60,
        }[self.phase]

    def refresh_cycle_label(self) -> None:
        self.cycle_label.setText(f"Ciclos de foco concluídos hoje: {self.db.count_pomodoros_today()}")

    def toggle(self) -> None:
        if self.running:
            self.running = False
            self.timer.stop()
            self.start_btn.setText("▶ Continuar")
        else:
            self.running = True
            self.timer.start()
            self.start_btn.setText("❚❚ Pausar")

    def reset(self) -> None:
        self.running = False
        self.timer.stop()
        self.elapsed_in_phase = 0
        self.remaining = self._phase_seconds()
        self.start_btn.setText("▶ Iniciar")
        self._update_label()

    def skip_phase(self) -> None:
        self._advance_phase(register=False)

    def _tick(self) -> None:
        self.remaining -= 1
        self.elapsed_in_phase += 1
        if self.remaining <= 0:
            self._advance_phase(register=True)
        else:
            self._update_label()

    def _advance_phase(self, register: bool) -> None:
        finished_phase = self.phase
        seconds = self.elapsed_in_phase
        if register and seconds > 0:
            self.session_completed.emit(seconds, finished_phase)
            if finished_phase == "foco":
                self.db.add_pomodoro_session(seconds, "foco")
                self.cycles += 1
        if finished_phase == "foco":
            self.phase = "longa" if self.cycles and self.cycles % 4 == 0 else "curta"
        else:
            self.phase = "foco"
        self.elapsed_in_phase = 0
        self.remaining = self._phase_seconds()
        self.refresh_cycle_label()
        self._update_label()
        if self.running:
            self.timer.start()

    def _update_label(self) -> None:
        names = {"foco": "🎯 Foco", "curta": "☕ Pausa curta", "longa": "🌙 Pausa longa"}
        self.phase_label.setText(names.get(self.phase, "🎯 Foco"))
        m, s = divmod(max(0, self.remaining), 60)
        self.time_label.setText(f"{m:02d}:{s:02d}")
        total = max(1, self._phase_seconds())
        done = max(0, total - self.remaining)
        self.progress.setValue(int(done / total * 1000))


class TasksWidget(QFrame):
    """Checklist mais limpo, com filtro simples e associação ao curso atual."""

    changed = Signal()

    def __init__(self, db: Database, get_current_course: Callable[[], object | None]) -> None:
        super().__init__()
        self.db = db
        self.get_current_course = get_current_course
        self.mode = "open"
        self.setObjectName("Card")
        self.setMinimumHeight(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("✅  TAREFAS")
        title.setObjectName("SectionTitle")
        top.addWidget(title)
        top.addStretch(1)
        self.open_btn = QPushButton("A fazer")
        self.done_btn = QPushButton("Concluídas")
        for btn in (self.open_btn, self.done_btn):
            btn.setCheckable(True)
            btn.setMinimumHeight(30)
        self.open_btn.setChecked(True)
        self.open_btn.clicked.connect(lambda: self._set_mode("open"))
        self.done_btn.clicked.connect(lambda: self._set_mode("done"))
        top.addWidget(self.open_btn)
        top.addWidget(self.done_btn)
        layout.addLayout(top)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Nova tarefa de estudo...")
        self.input.returnPressed.connect(self.add_task)
        self.priority_box = QComboBox()
        self.priority_box.addItems(PRIORITY_LABELS)
        self.priority_box.setCurrentIndex(1)
        self.priority_box.setMinimumWidth(95)
        add_btn = QPushButton("+ Adicionar")
        add_btn.setObjectName("PrimaryButton")
        add_btn.clicked.connect(self.add_task)
        add_row.addWidget(self.input, 1)
        add_row.addWidget(self.priority_box)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemChanged.connect(self._on_item_changed)
        self.list.itemDoubleClicked.connect(lambda _i: self._edit_current())
        layout.addWidget(self.list, 1)

        hint = QLabel("Clique para concluir · botão direito para editar, priorizar ou excluir.")
        hint.setObjectName("Muted2")
        layout.addWidget(hint)
        self.refresh()

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.open_btn.setChecked(mode == "open")
        self.done_btn.setChecked(mode == "done")
        self.refresh()

    def add_task(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        course = self.get_current_course()
        course_id = getattr(course, "id", None) if course else None
        self.db.add_task(text, priority=self.priority_box.currentIndex(), course_id=course_id)
        self.input.clear()
        self._set_mode("open")
        self.changed.emit()

    def refresh(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        tasks = self.db.list_tasks(include_done=True)
        for task in tasks:
            done = bool(task["done"])
            if self.mode == "open" and done:
                continue
            if self.mode == "done" and not done:
                continue
            mark = PRIORITY_MARKS.get(int(task["priority"]), "◐")
            label = f"{mark}  {task['text']}"
            if task.get("due_date"):
                label += f"   ⏳ {task['due_date']}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, int(task["id"]))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if done else Qt.Unchecked)
            if done:
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
            self.list.addItem(item)
        self.list.blockSignals(False)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        task_id = int(item.data(Qt.UserRole))
        want_done = item.checkState() == Qt.Checked
        tasks = {int(t["id"]): t for t in self.db.list_tasks()}
        current = tasks.get(task_id)
        if current and bool(current["done"]) != want_done:
            self.db.toggle_task(task_id)
            self.refresh()
            self.changed.emit()

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        edit = menu.addAction("✎ Editar")
        prio = menu.addAction("Mudar prioridade")
        due = menu.addAction("Definir prazo")
        menu.addSeparator()
        delete = menu.addAction("Excluir")
        action = menu.exec(self.list.mapToGlobal(pos))
        task_id = int(item.data(Qt.UserRole))
        if action == edit:
            self._edit_current()
        elif action == prio:
            label, ok = QInputDialog.getItem(self, "Prioridade", "Selecione:", PRIORITY_LABELS, 1, False)
            if ok:
                self.db.update_task(task_id, priority=PRIORITY_LABELS.index(label))
                self.refresh()
                self.changed.emit()
        elif action == due:
            text, ok = QInputDialog.getText(self, "Prazo", "Data (AAAA-MM-DD) ou vazio para remover:")
            if ok:
                self.db.update_task(task_id, due_date=text.strip() or None)
                self.refresh()
                self.changed.emit()
        elif action == delete:
            self.db.delete_task(task_id)
            self.refresh()
            self.changed.emit()

    def _edit_current(self) -> None:
        item = self.list.currentItem()
        if not item:
            return
        task_id = int(item.data(Qt.UserRole))
        tasks = {int(t["id"]): t for t in self.db.list_tasks()}
        current = tasks.get(task_id)
        if not current:
            return
        text, ok = QInputDialog.getText(self, "Editar tarefa", "Texto:", text=current["text"])
        if ok and text.strip():
            self.db.update_task(task_id, text=text.strip())
            self.refresh()
            self.changed.emit()


class RecentActivity(QFrame):
    """Lista de aulas marcadas como assistidas recentemente."""

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.setObjectName("Card")
        self.setMinimumHeight(250)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("🕘  ATIVIDADE RECENTE")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        rows = self.db.recent_completed_videos(8)
        if not rows:
            item = QListWidgetItem("Nenhuma aula concluída ainda")
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self.list.addItem(item)
            return
        for r in rows:
            title = r.get("title") or "Aula"
            course = r.get("course_title") or "Curso"
            when = (r.get("watched_at") or "")[:10]
            self.list.addItem(f"✓ {title}\n   {course} · {when}")


class StudyTab(QWidget):
    """Dashboard completo de acompanhamento."""

    def __init__(self, db: Database, get_current_course: Callable[[], object | None]) -> None:
        super().__init__()
        self.db = db
        self.get_current_course = get_current_course

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("StudyScroll")
        root.addWidget(scroll, 1)

        content = QWidget()
        content.setObjectName("StudyContent")
        scroll.setWidget(content)

        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 20, 24, 28)
        outer.setSpacing(16)

        hero, hero_l = _card()
        hero_l.setSpacing(6)
        hero_top = QHBoxLayout()
        hero_text = QVBoxLayout()
        title = QLabel("Acompanhamento")
        title.setObjectName("PageTitle")
        subtitle = QLabel("Dashboard integrado com suas aulas do Telegram, Pomodoro, metas e tarefas.")
        subtitle.setObjectName("Muted")
        subtitle.setWordWrap(True)
        hero_text.addWidget(title)
        hero_text.addWidget(subtitle)
        hero_top.addLayout(hero_text, 1)
        self.refresh_btn = QPushButton("↻ Atualizar")
        self.refresh_btn.clicked.connect(self.refresh)
        hero_top.addWidget(self.refresh_btn)
        hero_l.addLayout(hero_top)
        outer.addWidget(hero)

        self.today_card = MetricCard("⏱️", "Hoje", "0min")
        self.week_card = MetricCard("📈", "Semana", "0min")
        self.streak_card = MetricCard("🔥", "Sequência", "0 dias")
        self.lessons_card = MetricCard("✅", "Aulas concluídas", "0/0")
        self.pomo_card = MetricCard("🍅", "Pomodoros hoje", "0")
        self.course_card = MetricCard("📘", "Curso atual", "—")
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, card in enumerate((
            self.today_card,
            self.week_card,
            self.streak_card,
            self.lessons_card,
            self.pomo_card,
            self.course_card,
        )):
            grid.addWidget(card, i // 3, i % 3)
        outer.addLayout(grid)

        # Linha principal: esquerda = estudo/tarefas; direita = metas/gráficos.
        main = QHBoxLayout()
        main.setSpacing(16)
        outer.addLayout(main, 1)

        left = QVBoxLayout()
        left.setSpacing(16)
        self.pomodoro = PomodoroWidget(db)
        self.pomodoro.session_completed.connect(self._on_pomodoro_done)
        left.addWidget(self.pomodoro)
        self.tasks = TasksWidget(db, get_current_course)
        self.tasks.changed.connect(self.refresh)
        left.addWidget(self.tasks, 1)
        left_wrap = QWidget()
        left_wrap.setLayout(left)
        left_wrap.setMinimumWidth(420)
        left_wrap.setMaximumWidth(560)
        main.addWidget(left_wrap)

        right = QVBoxLayout()
        right.setSpacing(16)
        self.goal = GoalCard(db)
        right.addWidget(self.goal)

        chart_card, chart_l = _card("📊  HORAS LÍQUIDAS — ÚLTIMOS 7 DIAS")
        self.day_chart = BarChart()
        self.day_chart.setMinimumHeight(210)
        chart_l.addWidget(self.day_chart)
        right.addWidget(chart_card)

        row2 = QHBoxLayout()
        row2.setSpacing(16)
        course_chart_card, ccl = _card("🎓  AULAS POR CURSO")
        self.course_chart = HBarChart()
        self.course_chart.setMinimumHeight(190)
        ccl.addWidget(self.course_chart)
        row2.addWidget(course_chart_card, 1)

        donut_card, dl = _card("✅  PROGRESSO ATUAL")
        self.donut = DonutChart()
        dl.addWidget(self.donut)
        row2.addWidget(donut_card)
        right.addLayout(row2)

        subject_card, scl = _card("📚  PROGRESSO POR MATÉRIA", "Do curso selecionado na aba Aulas.")
        self.subject_chart = HBarChart()
        self.subject_chart.setMinimumHeight(210)
        scl.addWidget(self.subject_chart)
        right.addWidget(subject_card)

        self.activity = RecentActivity(db)
        right.addWidget(self.activity)

        right_wrap = QWidget()
        right_wrap.setLayout(right)
        main.addWidget(right_wrap, 1)

        self.refresh()

    def apply_palette(self, colors: dict) -> None:
        for chart in (self.day_chart, self.course_chart, self.subject_chart, self.donut):
            chart.set_palette(colors)

    def _on_pomodoro_done(self, seconds: int, kind: str) -> None:
        if kind == "foco" and seconds > 0:
            course = self.get_current_course()
            course_id = getattr(course, "id", None) if course else None
            self.db.log_study_time(seconds, course_id=course_id)
        self.refresh()

    def _weekday_label(self, day: str) -> str:
        try:
            weekday = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
            return weekday[datetime.strptime(day, "%Y-%m-%d").weekday()]
        except Exception:  # noqa: BLE001
            return day[5:]

    def refresh(self) -> None:
        today = self.db.today_study_seconds()
        week = self.db.week_study_seconds()
        total_done, total_videos = self.db.video_totals()
        task_counts = self.db.task_counts()

        self.today_card.set_values(_fmt_minutes_from_seconds(today), "tempo registrado hoje")
        self.week_card.set_values(_fmt_minutes_from_seconds(week), "horas líquidas na semana")
        streak = self.db.study_streak_days()
        self.streak_card.set_values(f"{streak} {'dia' if streak == 1 else 'dias'}", "sequência de estudo")
        self.lessons_card.set_values(f"{total_done}/{total_videos}", f"{_pct(total_done, total_videos)}% do acervo")
        self.pomo_card.set_values(str(self.db.count_pomodoros_today()), f"{task_counts.get('open', 0)} tarefas abertas")

        try:
            self.pomodoro.refresh_cycle_label()
        except Exception:  # noqa: BLE001
            pass

        course = self.get_current_course()
        if course:
            watched, total = self.db.course_progress(getattr(course, "id"))
            pct = _pct(watched, total)
            self.course_card.set_values(f"{pct}%", f"{watched}/{total} aulas · {getattr(course, 'title', 'Curso')}")
            self.donut.set_value(pct, f"{watched}/{total} aulas")
            subject_rows = self.db.subject_completion_stats(getattr(course, "id"))
            self.subject_chart.set_data([(r["title"], r["pct"]) for r in subject_rows])
        else:
            self.course_card.set_values("—", "selecione um curso")
            self.donut.set_value(0, "Sem curso")
            self.subject_chart.set_data([])

        self.goal.refresh(week)

        data = self.db.study_seconds_by_day(7)
        labels = [self._weekday_label(day) for day, _secs in data]
        values = [secs / 60.0 for _day, secs in data]
        self.day_chart.set_data(labels, values, value_fmt=lambda v: (f"{int(v)}m" if v else ""))

        course_rows = self.db.course_completion_stats()
        self.course_chart.set_data([(r["title"], r["done"]) for r in course_rows])
        self.activity.refresh()
