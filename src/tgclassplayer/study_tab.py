"""Aba "Acompanhamento" 📊 — produtividade de estudos.

Componentes:
- ⏱️ Pomodoro: timer configurável (foco / pausa curta / pausa longa), com
  start/pause/reset, contagem de ciclos e registro das sessões concluídas
  (vinculadas à matéria atual quando houver).
- ✅ Tarefas/checklists: "A fazer" e "Feito", criar/editar/excluir/reordenar,
  marcar concluído, prioridade e prazo opcional.
- 📈 Gráficos: tempo de estudo por dia (últimos 7 dias) e aulas concluídas por
  curso (QPainter, sem dependências frágeis).
- Cartões de resumo: streak de dias, total de horas e % do curso atual.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .charts import BarChart, DonutChart, HBarChart
from .db import Database

PRIORITY_MARKS = {0: "○", 1: "◐", 2: "●"}


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    if title:
        lbl = QLabel(title)
        lbl.setObjectName("SectionTitle")
        layout.addWidget(lbl)
    return frame, layout


class StatCard(QFrame):
    def __init__(self, label: str, value: str = "—") -> None:
        super().__init__()
        self.setObjectName("StatCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(2)
        self.label = QLabel(label)
        self.label.setObjectName("CardLabel")
        self.value = QLabel(value)
        self.value.setObjectName("BigNumber")
        layout.addWidget(self.label)
        layout.addWidget(self.value)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


class PomodoroWidget(QFrame):
    """Timer Pomodoro com fases e registro de sessões."""

    session_completed = Signal(int, str)  # (segundos, tipo)

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.setObjectName("Card")
        self.phase = "foco"
        self.cycles = 0
        self.remaining = 0
        self.running = False
        self.elapsed_in_phase = 0

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("⏱️  POMODORO")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        self.phase_label = QLabel("Foco")
        self.phase_label.setObjectName("PanelTitle")
        self.phase_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.phase_label)

        self.time_label = QLabel("25:00")
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setStyleSheet("font-size: 54px; font-weight: 900;")
        layout.addWidget(self.time_label)

        self.cycle_label = QLabel("Ciclos concluídos hoje: 0")
        self.cycle_label.setObjectName("Muted2")
        self.cycle_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.cycle_label)

        controls = QHBoxLayout()
        self.start_btn = QPushButton("▶ Iniciar")
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.clicked.connect(self.toggle)
        self.reset_btn = QPushButton("↺ Zerar")
        self.reset_btn.clicked.connect(self.reset)
        self.skip_btn = QPushButton("⤼ Pular fase")
        self.skip_btn.clicked.connect(self.skip_phase)
        controls.addWidget(self.start_btn)
        controls.addWidget(self.reset_btn)
        controls.addWidget(self.skip_btn)
        layout.addLayout(controls)

        cfg = QGridLayout()
        cfg.setHorizontalSpacing(10)
        cfg.addWidget(self._small("Foco (min)"), 0, 0)
        cfg.addWidget(self._small("Pausa curta"), 0, 1)
        cfg.addWidget(self._small("Pausa longa"), 0, 2)
        self.focus_spin = self._spin(int(db.get_setting("pomo_focus", "25") or 25), 1, 180)
        self.short_spin = self._spin(int(db.get_setting("pomo_short", "5") or 5), 1, 60)
        self.long_spin = self._spin(int(db.get_setting("pomo_long", "15") or 15), 1, 90)
        for sp in (self.focus_spin, self.short_spin, self.long_spin):
            sp.valueChanged.connect(self._save_config)
        cfg.addWidget(self.focus_spin, 1, 0)
        cfg.addWidget(self.short_spin, 1, 1)
        cfg.addWidget(self.long_spin, 1, 2)
        layout.addLayout(cfg)

        self.reset()
        self.refresh_cycle_label()

    def _small(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("Muted2")
        return lbl

    def _spin(self, value: int, lo: int, hi: int) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(value)
        return sp

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
        today = self.db.count_pomodoros_today()
        self.cycle_label.setText(f"Ciclos de foco concluídos hoje: {today}")

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
            self.phase = "longa" if self.cycles % 4 == 0 and self.cycles > 0 else "curta"
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
        self.phase_label.setText(names.get(self.phase, "Foco"))
        m, s = divmod(max(0, self.remaining), 60)
        self.time_label.setText(f"{m:02d}:{s:02d}")


class TasksWidget(QFrame):
    """Checklist de tarefas (A fazer / Feito) com prioridade e prazo."""

    changed = Signal()

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("✅  TAREFAS / CHECKLIST")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        add_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Nova tarefa de estudo...")
        self.input.returnPressed.connect(self.add_task)
        self.priority_box = QComboBox()
        self.priority_box.addItems(["Baixa", "Média", "Alta"])
        self.priority_box.setCurrentIndex(1)
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

        hint = QLabel("Clique na caixa para concluir · botão direito para editar/excluir.")
        hint.setObjectName("Muted2")
        layout.addWidget(hint)

        self.refresh()

    def add_task(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.db.add_task(text, priority=self.priority_box.currentIndex())
        self.input.clear()
        self.refresh()
        self.changed.emit()

    def refresh(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for task in self.db.list_tasks(include_done=True):
            mark = PRIORITY_MARKS.get(int(task["priority"]), "◐")
            label = f"{mark}  {task['text']}"
            if task.get("due_date"):
                label += f"   ⏳ {task['due_date']}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, int(task["id"]))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if task["done"] else Qt.Unchecked)
            if task["done"]:
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
        up = menu.addAction("↑ Subir")
        down = menu.addAction("↓ Descer")
        menu.addSeparator()
        delete = menu.addAction("Excluir")
        action = menu.exec(self.list.mapToGlobal(pos))
        task_id = int(item.data(Qt.UserRole))
        if action == edit:
            self._edit_current()
        elif action == prio:
            label, ok = QInputDialog.getItem(
                self, "Prioridade", "Selecione:", ["Baixa", "Média", "Alta"], 1, False
            )
            if ok:
                inv = {"Baixa": 0, "Média": 1, "Alta": 2}
                self.db.update_task(task_id, priority=inv[label])
                self.refresh()
        elif action == due:
            text, ok = QInputDialog.getText(
                self, "Prazo", "Data (AAAA-MM-DD) ou vazio para remover:"
            )
            if ok:
                self.db.update_task(task_id, due_date=text.strip() or None)
                self.refresh()
        elif action == up:
            self._move(task_id, -1)
        elif action == down:
            self._move(task_id, 1)
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
        text, ok = QInputDialog.getText(
            self, "Editar tarefa", "Texto:", text=current["text"]
        )
        if ok and text.strip():
            self.db.update_task(task_id, text=text.strip())
            self.refresh()

    def _move(self, task_id: int, delta: int) -> None:
        ids = [int(t["id"]) for t in self.db.list_tasks()]
        if task_id not in ids:
            return
        idx = ids.index(task_id)
        new = idx + delta
        if 0 <= new < len(ids):
            ids[idx], ids[new] = ids[new], ids[idx]
            self.db.reorder_tasks(ids)
            self.refresh()


class StudyTab(QWidget):
    """Aba completa de Acompanhamento."""

    def __init__(self, db: Database, get_current_course: Callable[[], object | None]) -> None:
        super().__init__()
        self.db = db
        self.get_current_course = get_current_course

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(16)

        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.streak_card = StatCard("🔥 Sequência (dias)", "0")
        self.hours_card = StatCard("⏳ Horas estudadas", "0h")
        self.pomo_card = StatCard("🍅 Pomodoros hoje", "0")
        self.course_card = StatCard("📘 Curso atual", "0%")
        for card in (self.streak_card, self.hours_card, self.pomo_card, self.course_card):
            cards.addWidget(card, 1)
        outer.addLayout(cards)

        main = QHBoxLayout()
        main.setSpacing(16)
        outer.addLayout(main, 1)

        left = QVBoxLayout()
        left.setSpacing(16)
        self.pomodoro = PomodoroWidget(db)
        self.pomodoro.session_completed.connect(self._on_pomodoro_done)
        left.addWidget(self.pomodoro)
        self.tasks = TasksWidget(db)
        self.tasks.changed.connect(self.refresh)
        left.addWidget(self.tasks, 1)
        left_wrap = QWidget()
        left_wrap.setLayout(left)
        left_wrap.setMaximumWidth(440)
        main.addWidget(left_wrap)

        right = QVBoxLayout()
        right.setSpacing(16)

        chart1_card, c1l = _card("📈  TEMPO DE ESTUDO (ÚLTIMOS 7 DIAS)")
        self.day_chart = BarChart()
        c1l.addWidget(self.day_chart, 1)
        right.addWidget(chart1_card, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        chart2_card, c2l = _card("🎓  AULAS CONCLUÍDAS POR CURSO")
        self.course_chart = HBarChart()
        c2l.addWidget(self.course_chart, 1)
        bottom.addWidget(chart2_card, 1)

        donut_card, c3l = _card("✅  PROGRESSO DO CURSO ATUAL")
        self.donut = DonutChart()
        c3l.addWidget(self.donut, 1)
        bottom.addWidget(donut_card)
        right.addLayout(bottom, 1)

        right_wrap = QWidget()
        right_wrap.setLayout(right)
        main.addWidget(right_wrap, 1)

        self.refresh()

    def apply_palette(self, colors: dict) -> None:
        for chart in (self.day_chart, self.course_chart, self.donut):
            chart.set_palette(colors)

    def _on_pomodoro_done(self, seconds: int, kind: str) -> None:
        if kind == "foco" and seconds > 0:
            course = self.get_current_course()
            course_id = getattr(course, "id", None) if course else None
            self.db.log_study_time(seconds, course_id=course_id)
        self.refresh()

    def _fmt_hours(self, seconds: int) -> str:
        h = seconds / 3600.0
        if h >= 10:
            return f"{int(h)}h"
        if h >= 1:
            return f"{h:.1f}h"
        return f"{int(seconds // 60)}min"

    def refresh(self) -> None:
        self.streak_card.set_value(str(self.db.study_streak_days()))
        self.hours_card.set_value(self._fmt_hours(self.db.total_study_seconds()))
        self.pomo_card.set_value(str(self.db.count_pomodoros_today()))
        try:
            self.pomodoro.refresh_cycle_label()
        except Exception:  # noqa: BLE001
            pass

        data = self.db.study_seconds_by_day(7)
        weekday = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
        labels = []
        values = []
        for day, secs in data:
            try:
                from datetime import datetime

                wd = datetime.strptime(day, "%Y-%m-%d").weekday()
                labels.append(weekday[wd])
            except Exception:  # noqa: BLE001
                labels.append(day[5:])
            values.append(secs / 60.0)
        self.day_chart.set_data(
            labels, values, value_fmt=lambda v: (f"{int(v)}m" if v else "")
        )

        self.course_chart.set_data(self.db.watched_count_by_course())

        course = self.get_current_course()
        if course:
            videos = self.db.list_videos(getattr(course, "id"))
            watched = sum(1 for v in videos if v.watched_at)
            pct = (watched / len(videos) * 100) if videos else 0
            self.donut.set_value(pct, f"{watched}/{len(videos)} aulas")
            self.course_card.set_value(f"{int(pct)}%")
        else:
            self.donut.set_value(0, "Sem curso")
            self.course_card.set_value("—")
