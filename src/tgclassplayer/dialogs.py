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

from .db import Database
from .summary_parser import extract_hashtags
from .telegram_service import TelegramService

log = logging.getLogger(__name__)


def wait_future(future, title: str, message: str, parent: QWidget | None = None, timeout_ms: int | None = None):
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
                self.status.setText("Digite o código recebido no Telegram. Nunca compartilhe esse código.")
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
                    self.status.setText("Sua conta tem verificação em duas etapas. Digite sua senha 2FA.")
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
        label = QLabel("Cada grupo, canal ou supergrupo do Telegram vira um curso no app.")
        label.setObjectName("Muted")
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
        for course in self.courses:
            title = course.get("title") or str(course.get("chat_id"))
            if query and query not in title.lower() and query not in str(course.get("username") or "").lower():
                continue
            item = QListWidgetItem(f"{title}   ·   {course.get('chat_type', '')}")
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


# ---------------------------------------------------- Editor de sumários/tópicos
class SummaryEditorDialog(QDialog):
    """Editor completo dos sumários por tópico.

    Permite: criar, renomear, excluir tópicos; editar o texto do sumário (guia
    de organização das aulas) com formato hierárquico `= Módulo / == Aula /
    #TAG`. Tudo é salvo no banco e reflete na árvore de aulas.
    """

    def __init__(self, topics: list[dict[str, Any]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Editar sumários e tópicos")
        self.setMinimumSize(1040, 700)
        # Cópia editável dos tópicos.
        self.topics: list[dict[str, Any]] = [dict(t) for t in (topics or [])]
        self._current_index = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)
        root = QHBoxLayout()
        root.setSpacing(14)
        outer.addLayout(root, 1)

        # Coluna esquerda: lista de tópicos + ações
        left = QFrame()
        left.setObjectName("Card")
        left.setMinimumWidth(280)
        left.setMaximumWidth(340)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 14, 14, 14)
        ll.addWidget(self._title("Tópicos / matérias"))
        self.topic_list = QListWidget()
        self.topic_list.currentRowChanged.connect(self.on_select)
        ll.addWidget(self.topic_list, 1)
        btns = QHBoxLayout()
        add_btn = QPushButton("+ Novo")
        add_btn.clicked.connect(self.add_topic)
        up_btn = QPushButton("↑")
        up_btn.setObjectName("IconButton")
        up_btn.clicked.connect(lambda: self.move(-1))
        down_btn = QPushButton("↓")
        down_btn.setObjectName("IconButton")
        down_btn.clicked.connect(lambda: self.move(1))
        del_btn = QPushButton("Excluir")
        del_btn.setObjectName("DangerButton")
        del_btn.clicked.connect(self.delete_topic)
        btns.addWidget(add_btn)
        btns.addWidget(up_btn)
        btns.addWidget(down_btn)
        btns.addStretch(1)
        btns.addWidget(del_btn)
        ll.addLayout(btns)

        # Coluna direita: edição do tópico atual
        right = QFrame()
        right.setObjectName("Card")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 14, 14, 14)
        rl.addWidget(self._title("Título do tópico"))
        self.title_edit = QLineEdit()
        self.title_edit.textChanged.connect(self.on_title_changed)
        rl.addWidget(self.title_edit)
        hint = QLabel(
            "Guia de organização das aulas. Use o formato:\n"
            "= Módulo\n== Aula\n#TAG01 #TAG02   (hashtags ligam o item ao vídeo)"
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

        # Rodapé
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
        if self.topics:
            self.topic_list.setCurrentRow(0)

    def _title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("SectionTitle")
        return lbl

    def refresh_list(self) -> None:
        cur = self.topic_list.currentRow()
        self.topic_list.blockSignals(True)
        self.topic_list.clear()
        for topic in self.topics:
            title = topic.get("title") or "Sumário"
            count = len(topic.get("tags") or extract_hashtags(topic.get("summary_text")))
            self.topic_list.addItem(QListWidgetItem(f"{title}   ·   {count} tags"))
        self.topic_list.blockSignals(False)
        if 0 <= cur < len(self.topics):
            self.topic_list.setCurrentRow(cur)

    def on_select(self, row: int) -> None:
        self._current_index = row
        if not (0 <= row < len(self.topics)):
            self.title_edit.setText("")
            self.text_edit.setPlainText("")
            return
        topic = self.topics[row]
        self.title_edit.blockSignals(True)
        self.text_edit.blockSignals(True)
        self.title_edit.setText(topic.get("title") or "")
        self.text_edit.setPlainText(topic.get("summary_text") or "")
        self.title_edit.blockSignals(False)
        self.text_edit.blockSignals(False)
        self._update_tags()

    def on_title_changed(self, text: str) -> None:
        if 0 <= self._current_index < len(self.topics):
            self.topics[self._current_index]["title"] = text
            item = self.topic_list.item(self._current_index)
            if item:
                count = len(self.topics[self._current_index].get("tags") or [])
                item.setText(f"{text or 'Sumário'}   ·   {count} tags")

    def on_text_changed(self) -> None:
        if 0 <= self._current_index < len(self.topics):
            text = self.text_edit.toPlainText()
            self.topics[self._current_index]["summary_text"] = text
            tags = extract_hashtags(text)
            self.topics[self._current_index]["tags"] = tags
            self.topics[self._current_index]["tag_count"] = len(tags)
            self._update_tags()
            item = self.topic_list.item(self._current_index)
            if item:
                title = self.topics[self._current_index].get("title") or "Sumário"
                item.setText(f"{title}   ·   {len(tags)} tags")

    def _update_tags(self) -> None:
        if 0 <= self._current_index < len(self.topics):
            tags = self.topics[self._current_index].get("tags") or []
            self.tag_label.setText(
                f"{len(tags)} hashtag(s): " + " ".join(tags[:30]) if tags else "Nenhuma hashtag detectada."
            )

    def add_topic(self) -> None:
        title, ok = QInputDialog.getText(self, "Novo tópico", "Nome do tópico/matéria:")
        if not ok or not title.strip():
            return
        self.topics.append(
            {
                "id": f"manual:{len(self.topics)}",
                "telegram_topic_id": "manual",
                "title": title.strip(),
                "summary_text": f"= {title.strip()}\n",
                "tags": [],
                "tag_count": 0,
                "manual": True,
            }
        )
        self.refresh_list()
        self.topic_list.setCurrentRow(len(self.topics) - 1)

    def delete_topic(self) -> None:
        row = self.topic_list.currentRow()
        if not (0 <= row < len(self.topics)):
            return
        title = self.topics[row].get("title") or "tópico"
        if QMessageBox.question(self, "Excluir", f"Excluir o tópico “{title}”?") != QMessageBox.Yes:
            return
        self.topics.pop(row)
        self._current_index = -1
        self.refresh_list()
        if self.topics:
            self.topic_list.setCurrentRow(min(row, len(self.topics) - 1))
        else:
            self.title_edit.setText("")
            self.text_edit.setPlainText("")

    def move(self, delta: int) -> None:
        row = self.topic_list.currentRow()
        new = row + delta
        if not (0 <= row < len(self.topics)) or not (0 <= new < len(self.topics)):
            return
        self.topics[row], self.topics[new] = self.topics[new], self.topics[row]
        self.refresh_list()
        self.topic_list.setCurrentRow(new)

    def result_topics(self) -> list[dict[str, Any]]:
        return self.topics


# ----------------------------------------------------- Editor de aula (vídeo)
class EditVideoDialog(QDialog):
    def __init__(self, video, topics: list[dict[str, Any]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Editar aula")
        self.setMinimumWidth(520)
        self.video = video

        self.title_edit = QLineEdit(video.title)
        self.topic_box = QComboBox()
        self.topic_box.setEditable(True)
        existing_titles = []
        for t in topics or []:
            tt = t.get("title")
            if tt and tt not in existing_titles:
                existing_titles.append(tt)
        if video.topic_title and video.topic_title not in existing_titles:
            existing_titles.insert(0, video.topic_title)
        self.topic_box.addItems(existing_titles or ["Geral"])
        self.topic_box.setCurrentText(video.topic_title or "Geral")

        self.tags_edit = QLineEdit(" ".join(video.hashtags))
        self.note_edit = QPlainTextEdit(video.note or "")
        self.note_edit.setPlaceholderText("Anotações pessoais sobre esta aula...")
        self.note_edit.setMaximumHeight(120)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Título da aula", self.title_edit)
        form.addRow("Tópico / matéria", self.topic_box)
        form.addRow("Hashtags", self.tags_edit)
        form.addRow("Anotações", self.note_edit)

        info = QLabel(
            f"Arquivo: {video.file_name}\nMensagem #{video.message_id} · Tópico atual: {video.topic_title or 'Geral'}"
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
            # aceita também tags separadas por espaço sem #
            raw = [w.strip() for w in self.tags_edit.text().replace(",", " ").split() if w.strip()]
            tags = ["#" + w.lstrip("#").upper() for w in raw]
        return {
            "title": self.title_edit.text().strip() or self.video.title,
            "topic_title": self.topic_box.currentText().strip() or "Geral",
            "hashtags": tags,
            "note": self.note_edit.toPlainText().strip() or None,
        }
