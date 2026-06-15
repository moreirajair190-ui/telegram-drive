from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .db import Database, Subject, Video
from .summary_parser import all_tags, extract_hashtags, parse_summary
from .telegram_service import TelegramService

log = logging.getLogger(__name__)


def wait_future(
    future, title: str, message: str, parent: QWidget | None = None, timeout_ms: int | None = None
):
    """Espera um future do event loop assíncrono mostrando um diálogo de progresso."""
    dialog = QProgressDialog(message, "", 0, 0, parent)
    dialog.setWindowTitle(title)
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setCancelButton(None)
    deadline = QTimer(dialog) if timeout_ms else None
    timer = QTimer(dialog)

    def check_done():
        if future.done():
            timer.stop()
            if deadline:
                deadline.stop()
            dialog.close()

    def timeout_stop():
        if not future.done():
            future.cancel()
        check_done()

    timer.timeout.connect(check_done)
    timer.start(100)
    if deadline:
        deadline.setSingleShot(True)
        deadline.timeout.connect(timeout_stop)
        deadline.start(timeout_ms)
    dialog.exec()
    return future.result()


# ----------------------------------------------------------------------- Login
class LoginDialog(QDialog):
    def __init__(self, service: TelegramService, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.service = service
        self.db = db
        self.setWindowTitle("Conectar ao Telegram")
        self.setMinimumWidth(480)

        self.api_id = QLineEdit(self.db.get_setting("api_id") or "")
        self.api_hash = QLineEdit(self.db.get_setting("api_hash") or "")
        self.phone = QLineEdit(self.db.get_setting("phone_number") or "+55")
        self.code = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.status = QLabel(
            "Use o API ID e API HASH gerados em my.telegram.org. "
            "O código de login chega no próprio Telegram."
        )
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)

        self.code.setPlaceholderText("Código recebido no Telegram")
        self.password.setPlaceholderText("Senha da verificação em duas etapas (se houver)")
        self.code.hide()
        self.password.hide()

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("API ID", self.api_id)
        form.addRow("API HASH", self.api_hash)
        form.addRow("Telefone", self.phone)
        form.addRow("Código", self.code)
        form.addRow("Senha 2FA", self.password)

        self.next_btn = QPushButton("Conectar")
        self.next_btn.setObjectName("PrimaryButton")
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.clicked.connect(self.reject)
        self.next_btn.clicked.connect(self.on_next)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.next_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(8)
        title = QLabel("Login seguro do Telegram")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        layout.addWidget(self.status)
        layout.addSpacing(8)
        layout.addLayout(form)
        layout.addSpacing(8)
        layout.addLayout(buttons)

        self.step = "connect"

    def on_next(self) -> None:
        try:
            if self.step == "connect":
                api_id = self.api_id.text().strip()
                api_hash = self.api_hash.text().strip()
                phone = self.phone.text().strip()
                if not api_id or not api_hash:
                    QMessageBox.warning(self, "Dados incompletos", "Informe API ID e API HASH.")
                    return
                self.db.set_setting("api_id", api_id)
                self.db.set_setting("api_hash", api_hash)
                self.db.set_setting("phone_number", phone)
                result = wait_future(
                    self.service.call(self.service.ensure_connected(api_id, api_hash)),
                    "Telegram", "Conectando ao Telegram...", self,
                )
                if result.get("authorized"):
                    self.accept()
                    return
                if not phone or phone == "+55":
                    QMessageBox.warning(
                        self, "Telefone",
                        "Informe o telefone com DDI. Exemplo: +5547999999999",
                    )
                    return
                wait_future(
                    self.service.call(self.service.send_code(phone)),
                    "Telegram", "Enviando código de login...", self,
                )
                self.code.show()
                self.step = "code"
                self.next_btn.setText("Confirmar código")
                self.status.setText(
                    "Digite o código recebido no Telegram. Nunca compartilhe esse código."
                )
                return

            if self.step == "code":
                result = wait_future(
                    self.service.call(self.service.sign_in(self.code.text())),
                    "Telegram", "Confirmando código...", self,
                )
                if result.get("needs_password"):
                    self.password.show()
                    self.step = "password"
                    self.next_btn.setText("Confirmar senha")
                    self.status.setText(
                        "Sua conta tem verificação em duas etapas. Digite sua senha 2FA."
                    )
                    return
                if result.get("authorized"):
                    self.accept()
                    return

            if self.step == "password":
                result = wait_future(
                    self.service.call(self.service.check_password(self.password.text())),
                    "Telegram", "Confirmando senha...", self,
                )
                if result.get("authorized"):
                    self.accept()
                    return
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro no login")
            QMessageBox.critical(
                self, "Erro no login",
                f"{exc}\n\nVeja os logs em Ferramentas → Abrir pasta de logs.",
            )


# --------------------------------------------------------------- Selecionar cursos
class SelectCoursesDialog(QDialog):
    def __init__(self, courses: list[dict[str, Any]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Selecionar grupos / cursos")
        self.setMinimumSize(640, 560)
        self.courses = courses
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar grupo, canal ou curso...")
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.search.textChanged.connect(self.populate)

        self.ok_btn = QPushButton("Adicionar selecionados")
        self.ok_btn.setObjectName("PrimaryButton")
        self.cancel_btn = QPushButton("Cancelar")
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        label = QLabel(
            "Cada grupo, canal ou supergrupo do Telegram vira um curso. "
            "Fóruns (com tópicos) viram cursos com várias matérias automaticamente."
        )
        label.setObjectName("Muted")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.ok_btn)
        layout.addLayout(row)
        self.populate()

    def populate(self) -> None:
        query = self.search.text().strip().lower()
        self.list.clear()
        type_names = {
            "SUPERGROUP": "Supergrupo",
            "GROUP": "Grupo",
            "CHANNEL": "Canal",
        }
        for course in self.courses:
            title = course.get("title") or str(course.get("chat_id"))
            if (
                query
                and query not in title.lower()
                and query not in str(course.get("username") or "").lower()
            ):
                continue
            kind = type_names.get(course.get("chat_type", ""), course.get("chat_type", ""))
            if course.get("is_forum"):
                kind += " · Fórum 🗂️"
            item = QListWidgetItem(f"{title}   ·   {kind}")
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, course)
            self.list.addItem(item)

    def selected_courses(self) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for idx in range(self.list.count()):
            item = self.list.item(idx)
            if item.checkState() == Qt.Checked:
                selected.append(item.data(Qt.UserRole))
        return selected


# --------------------------------------------------- Editor de matérias/sumários
class SubjectsEditorDialog(QDialog):
    """Editor completo das MATÉRIAS de um curso e dos seus sumários.

    Permite criar/renomear/reordenar/excluir matérias e editar o texto do
    sumário (`= Módulo / == Aula / === Tipo / #TAG`) de cada matéria. As
    operações são aplicadas direto no banco ao salvar (via callbacks do app).
    """

    def __init__(self, subjects: list[Subject], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Editar matérias e sumários")
        self.setMinimumSize(1040, 700)
        # Cópia editável: cada item é um dict {id, title, summary_text, manual}.
        self.subjects: list[dict[str, Any]] = [
            {
                "id": s.id,
                "title": s.title,
                "summary_text": s.summary_text or "",
                "telegram_topic_id": s.telegram_topic_id,
                "manual": s.manual,
            }
            for s in (subjects or [])
        ]
        self.deleted_ids: list[int] = []
        self._current_index = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)
        root = QHBoxLayout()
        root.setSpacing(14)
        outer.addLayout(root, 1)

        # Coluna esquerda: lista de matérias + ações.
        left = QFrame()
        left.setObjectName("Card")
        left.setMinimumWidth(280)
        left.setMaximumWidth(340)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 14, 14, 14)
        ll.addWidget(self._title("Matérias / tópicos"))
        self.subject_list = QListWidget()
        self.subject_list.currentRowChanged.connect(self.on_select)
        ll.addWidget(self.subject_list, 1)
        btns = QHBoxLayout()
        add_btn = QPushButton("+ Nova")
        add_btn.clicked.connect(self.add_subject)
        up_btn = QPushButton("↑")
        up_btn.setObjectName("IconButton")
        up_btn.clicked.connect(lambda: self.move(-1))
        down_btn = QPushButton("↓")
        down_btn.setObjectName("IconButton")
        down_btn.clicked.connect(lambda: self.move(1))
        del_btn = QPushButton("Excluir")
        del_btn.setObjectName("DangerButton")
        del_btn.clicked.connect(self.delete_subject)
        btns.addWidget(add_btn)
        btns.addWidget(up_btn)
        btns.addWidget(down_btn)
        btns.addStretch(1)
        btns.addWidget(del_btn)
        ll.addLayout(btns)

        # Coluna direita: edição da matéria atual.
        right = QFrame()
        right.setObjectName("Card")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 14, 14, 14)
        rl.addWidget(self._title("Nome da matéria"))
        self.title_edit = QLineEdit()
        self.title_edit.textChanged.connect(self.on_title_changed)
        rl.addWidget(self.title_edit)
        hint = QLabel(
            "Sumário desta matéria (liga as hashtags às aulas):\n"
            "= Módulo\n== Aula\n=== Tipo (Videoaula / Resumo / Bônus)\n"
            "#TAG01 #TAG02"
        )
        hint.setObjectName("Muted2")
        rl.addWidget(hint)
        self.text_edit = QPlainTextEdit()
        self.text_edit.textChanged.connect(self.on_text_changed)
        rl.addWidget(self.text_edit, 1)
        self.tag_label = QLabel("")
        self.tag_label.setObjectName("Muted2")
        rl.addWidget(self.tag_label)

        root.addWidget(left)
        root.addWidget(right, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel = QPushButton("Cancelar")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Salvar tudo")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self.accept)
        bottom.addWidget(cancel)
        bottom.addWidget(save)
        outer.addLayout(bottom)

        self.refresh_list()
        if self.subjects:
            self.subject_list.setCurrentRow(0)

    def _title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def _tag_count(self, summary: str | None) -> int:
        return len(all_tags(parse_summary(summary)))

    def refresh_list(self) -> None:
        cur = self.subject_list.currentRow()
        self.subject_list.blockSignals(True)
        self.subject_list.clear()
        for subject in self.subjects:
            title = subject.get("title") or "Matéria"
            count = self._tag_count(subject.get("summary_text"))
            self.subject_list.addItem(QListWidgetItem(f"{title}   ·   {count} tags"))
        self.subject_list.blockSignals(False)
        if 0 <= cur < len(self.subjects):
            self.subject_list.setCurrentRow(cur)

    def on_select(self, row: int) -> None:
        self._current_index = row
        if not (0 <= row < len(self.subjects)):
            self.title_edit.setText("")
            self.text_edit.setPlainText("")
            return
        subject = self.subjects[row]
        self.title_edit.blockSignals(True)
        self.text_edit.blockSignals(True)
        self.title_edit.setText(subject.get("title") or "")
        self.text_edit.setPlainText(subject.get("summary_text") or "")
        self.title_edit.blockSignals(False)
        self.text_edit.blockSignals(False)
        self._update_tags()

    def on_title_changed(self, text: str) -> None:
        if 0 <= self._current_index < len(self.subjects):
            self.subjects[self._current_index]["title"] = text
            item = self.subject_list.item(self._current_index)
            if item:
                count = self._tag_count(self.subjects[self._current_index].get("summary_text"))
                item.setText(f"{text or 'Matéria'}   ·   {count} tags")

    def on_text_changed(self) -> None:
        if 0 <= self._current_index < len(self.subjects):
            text = self.text_edit.toPlainText()
            self.subjects[self._current_index]["summary_text"] = text
            self._update_tags()
            item = self.subject_list.item(self._current_index)
            if item:
                title = self.subjects[self._current_index].get("title") or "Matéria"
                item.setText(f"{title}   ·   {self._tag_count(text)} tags")

    def _update_tags(self) -> None:
        if 0 <= self._current_index < len(self.subjects):
            tags = all_tags(parse_summary(self.subjects[self._current_index].get("summary_text")))
            self.tag_label.setText(
                f"{len(tags)} hashtag(s): " + " ".join(tags[:30])
                if tags
                else "Nenhuma hashtag detectada."
            )

    def add_subject(self) -> None:
        title, ok = QInputDialog.getText(self, "Nova matéria", "Nome da matéria:")
        if not ok or not title.strip():
            return
        self.subjects.append(
            {
                "id": None,
                "title": title.strip(),
                "summary_text": f"= {title.strip()}\n",
                "telegram_topic_id": None,
                "manual": 1,
            }
        )
        self.refresh_list()
        self.subject_list.setCurrentRow(len(self.subjects) - 1)

    def delete_subject(self) -> None:
        row = self.subject_list.currentRow()
        if not (0 <= row < len(self.subjects)):
            return
        title = self.subjects[row].get("title") or "matéria"
        if QMessageBox.question(self, "Excluir", f"Excluir a matéria “{title}”?") != QMessageBox.Yes:
            return
        removed = self.subjects.pop(row)
        if removed.get("id"):
            self.deleted_ids.append(int(removed["id"]))
        self._current_index = -1
        self.refresh_list()
        if self.subjects:
            self.subject_list.setCurrentRow(min(row, len(self.subjects) - 1))
        else:
            self.title_edit.setText("")
            self.text_edit.setPlainText("")

    def move(self, delta: int) -> None:
        row = self.subject_list.currentRow()
        new = row + delta
        if not (0 <= row < len(self.subjects)) or not (0 <= new < len(self.subjects)):
            return
        self.subjects[row], self.subjects[new] = self.subjects[new], self.subjects[row]
        self.refresh_list()
        self.subject_list.setCurrentRow(new)

    def result(self) -> tuple[list[dict[str, Any]], list[int]]:
        """Retorna (matérias editadas em ordem, ids excluídos)."""
        return self.subjects, self.deleted_ids


# ----------------------------------------------------- Editor de aula (vídeo)
class EditVideoDialog(QDialog):
    def __init__(self, video: Video, subjects: list[Subject], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Editar aula")
        self.setMinimumWidth(540)
        self.video = video
        self.subjects = subjects or []

        self.title_edit = QLineEdit(video.title)

        self.subject_box = QComboBox()
        self.subject_box.addItem("— Sem matéria —", None)
        selected_idx = 0
        for i, s in enumerate(self.subjects, start=1):
            self.subject_box.addItem(s.title, s.id)
            if video.subject_id == s.id:
                selected_idx = i
        self.subject_box.setCurrentIndex(selected_idx)

        self.module_edit = QLineEdit(video.module or "")
        self.lesson_edit = QLineEdit(video.lesson or "")
        self.type_box = QComboBox()
        self.type_box.setEditable(True)
        self.type_box.addItems(["", "Videoaula", "Resumo", "Bônus", "Revisão", "Exercícios"])
        self.type_box.setCurrentText(video.type or "")

        self.tags_edit = QLineEdit(" ".join(video.hashtags))
        self.note_edit = QPlainTextEdit(video.note or "")
        self.note_edit.setPlaceholderText("Anotações pessoais sobre esta aula...")
        self.note_edit.setMaximumHeight(120)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Título da aula", self.title_edit)
        form.addRow("Matéria", self.subject_box)
        form.addRow("Módulo", self.module_edit)
        form.addRow("Aula", self.lesson_edit)
        form.addRow("Tipo", self.type_box)
        form.addRow("Hashtags", self.tags_edit)
        form.addRow("Anotações", self.note_edit)

        info = QLabel(
            f"Arquivo: {video.file_name}\nMensagem #{video.message_id}"
        )
        info.setObjectName("Muted2")
        info.setWordWrap(True)

        save = QPushButton("Salvar")
        save.setObjectName("PrimaryButton")
        cancel = QPushButton("Cancelar")
        save.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        title = QLabel("Editar aula")
        title.setObjectName("PanelTitle")
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(info)
        layout.addSpacing(6)
        layout.addLayout(row)

    def values(self) -> dict[str, Any]:
        tags = extract_hashtags(self.tags_edit.text())
        if not tags:
            raw = [w.strip() for w in self.tags_edit.text().replace(",", " ").split() if w.strip()]
            tags = ["#" + w.lstrip("#").upper() for w in raw]
        return {
            "title": self.title_edit.text().strip() or self.video.title,
            "subject_id": self.subject_box.currentData(),
            "module": self.module_edit.text().strip() or None,
            "lesson": self.lesson_edit.text().strip() or None,
            "type": self.type_box.currentText().strip() or None,
            "hashtags": tags,
            "note": self.note_edit.toPlainText().strip() or None,
        }
