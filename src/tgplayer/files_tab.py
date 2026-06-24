"""Aba "🗂️ Arquivos" — navegador de mídia do Telegram (estilo Drive).

Inspirada no projeto caamer20/Telegram-Drive, esta aba permite explorar TODA a
mídia de um chat (vídeo / PDF / imagem / zip / áudio), com:

- seletor de chat (reaproveita os cursos já sincronizados);
- busca por nome/legenda e filtros por tipo;
- grade de miniaturas (carregadas em 2º plano, com poda LRU no serviço);
- baixar para o disco COM PROGRESSO;
- enviar arquivo do disco para o chat COM PROGRESSO (trata falta de permissão);
- pré-visualização de imagem; vídeo abre o player interno;
- copiar link t.me (quando o chat é público).

A aba é puramente Qt/Pyrogram: toda a I/O de rede roda no loop assíncrono do
``TelegramService`` (via ``service.call``), mantendo a UI responsiva.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .db import Database
from .utils import ensure_extension, human_size, safe_filename

# Glifos por categoria (fallback quando não há miniatura).
KIND_ICONS = {
    "video": "🎬",
    "image": "🖼️",
    "pdf": "📄",
    "zip": "🗜️",
    "audio": "🎵",
    "file": "📦",
}
KIND_LABELS = {
    "video": "Vídeos",
    "image": "Imagens",
    "pdf": "PDFs",
    "zip": "Compactados",
    "audio": "Áudios",
    "file": "Outros",
}
ICON_SIZE = QSize(176, 132)
GRID_SIZE = QSize(196, 188)


class ImagePreviewDialog(QDialog):
    """Pré-visualização simples de imagem com rolagem."""

    def __init__(self, title: str, image_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignCenter)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        pix = QPixmap(image_path)
        if not pix.isNull():
            label.setPixmap(
                pix.scaled(1280, 960, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            label.setText("Não foi possível carregar a imagem.")
        scroll.setWidget(label)
        layout.addWidget(scroll)


class FilesTab(QWidget):
    """Navegador de arquivos de mídia do chat selecionado."""

    # Emitido quando o usuário pede para assistir um vídeo no player interno.
    play_video_requested = Signal(dict)

    def __init__(
        self,
        db: Database,
        service: Any,
        get_current_course: Callable[[], object | None],
    ) -> None:
        super().__init__()
        self.db = db
        self.service = service
        self.get_current_course = get_current_course
        self._items: list[dict[str, Any]] = []
        self._thumb_queue: list[tuple[str, int]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        outer.addLayout(self._build_toolbar())
        outer.addLayout(self._build_filters())

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(ICON_SIZE)
        self.grid.setGridSize(GRID_SIZE)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setSpacing(8)
        self.grid.setWordWrap(True)
        self.grid.setUniformItemSizes(True)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._show_item_menu)
        self.grid.itemDoubleClicked.connect(self._on_item_activated)
        outer.addWidget(self.grid, 1)

        # Estado vazio amigável sobre a grade (some assim que houver arquivos).
        self._grid_placeholder = QLabel(
            "Nenhum arquivo para mostrar.\n"
            "Escolha um chat acima e clique em “Atualizar”.",
            self.grid.viewport(),
        )
        self._grid_placeholder.setObjectName("ListPlaceholder")
        self._grid_placeholder.setAlignment(Qt.AlignCenter)
        self._grid_placeholder.setWordWrap(True)
        self._grid_placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        def _reposition_ph() -> None:
            self._grid_placeholder.setGeometry(
                self.grid.viewport().rect().adjusted(20, 20, -20, -20)
            )
            self._grid_placeholder.setVisible(self.grid.count() == 0)

        self._reposition_grid_placeholder = _reposition_ph
        _orig_resize = self.grid.resizeEvent

        def _resize(event):  # noqa: ANN001
            _orig_resize(event)
            _reposition_ph()

        self.grid.resizeEvent = _resize  # type: ignore[assignment]
        _reposition_ph()

        self.status = QLabel("Selecione um chat e clique em Atualizar.")
        self.status.setObjectName("Muted2")
        outer.addWidget(self.status)

        # Carrega miniaturas em 2º plano, uma por vez (sem travar a UI).
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(120)
        self._thumb_timer.timeout.connect(self._pump_thumbnails)

        self.reload_chats()

    # ------------------------------------------------------------- construção
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)

        bar.addWidget(QLabel("Chat:"))
        self.chat_combo = QComboBox()
        self.chat_combo.setMinimumWidth(280)
        self.chat_combo.currentIndexChanged.connect(lambda _i: self.refresh_media())
        bar.addWidget(self.chat_combo, 1)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎  Buscar por nome ou legenda…")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self._apply_local_filter)
        self.search.textChanged.connect(self._apply_local_filter)
        bar.addWidget(self.search, 1)

        refresh_btn = QPushButton("⟳  Atualizar")
        refresh_btn.setObjectName("PrimaryButton")
        refresh_btn.clicked.connect(self.refresh_media)
        bar.addWidget(refresh_btn)

        upload_btn = QPushButton("⬆  Enviar arquivo")
        upload_btn.clicked.connect(self.upload_file)
        bar.addWidget(upload_btn)

        return bar

    def _build_filters(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._filter_buttons: dict[str, QPushButton] = {}
        defs = [("all", "Tudo")] + [
            (k, KIND_LABELS[k])
            for k in ("video", "image", "pdf", "zip", "audio", "file")
        ]
        for key, label in defs:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("GhostButton")
            btn.clicked.connect(lambda _c, k=key: self._set_filter(k))
            self._filter_buttons[key] = btn
            row.addWidget(btn)
        row.addStretch(1)
        self._active_filter = "all"
        self._filter_buttons["all"].setChecked(True)
        return row

    # ------------------------------------------------------------- dados/chats
    def reload_chats(self) -> None:
        """Preenche o seletor de chats com os cursos já sincronizados."""
        self.chat_combo.blockSignals(True)
        self.chat_combo.clear()
        try:
            courses = self.db.list_courses()
        except Exception:  # noqa: BLE001
            courses = []
        if not courses:
            self.chat_combo.addItem("— nenhum chat sincronizado —", None)
        for course in courses:
            self.chat_combo.addItem(
                course.title,
                {
                    "chat_id": course.chat_id,
                    "username": getattr(course, "username", None),
                },
            )
        # Seleciona o curso atual quando possível.
        current = self.get_current_course()
        if current is not None:
            for i in range(self.chat_combo.count()):
                data = self.chat_combo.itemData(i)
                if data and str(data.get("chat_id")) == str(
                    getattr(current, "chat_id", "")
                ):
                    self.chat_combo.setCurrentIndex(i)
                    break
        self.chat_combo.blockSignals(False)

    def _current_chat(self) -> dict[str, Any] | None:
        return self.chat_combo.currentData()

    def _selected_kinds(self) -> tuple[str, ...] | None:
        if self._active_filter == "all":
            return None
        return (self._active_filter,)

    def refresh_media(self) -> None:
        """Busca a mídia do chat selecionado no Telegram (assíncrono)."""
        chat = self._current_chat()
        if not chat:
            self.status.setText("Sincronize um curso na aba Aulas primeiro.")
            return
        if not getattr(self.service, "client", None):
            self.status.setText("Entre no Telegram para navegar pelos arquivos.")
            return
        self.status.setText("Carregando arquivos do chat…")
        QApplication.processEvents()
        try:
            future = self.service.call(
                self.service.list_chat_media(chat["chat_id"], limit=300)
            )
            items = future.result(timeout=60)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Falha ao carregar: {exc}")
            return
        for it in items:
            it.setdefault("username", chat.get("username"))
        self._items = items
        self._apply_local_filter()

    # ------------------------------------------------------------- filtros/UI
    def _set_filter(self, key: str) -> None:
        self._active_filter = key
        for k, btn in self._filter_buttons.items():
            btn.setChecked(k == key)
        self._apply_local_filter()

    def _apply_local_filter(self) -> None:
        q = self.search.text().strip().lower()
        kinds = self._selected_kinds()
        filtered = []
        for it in self._items:
            if kinds and it["kind"] not in kinds:
                continue
            if q and q not in (
                f"{it.get('file_name','')} {it.get('caption','')}".lower()
            ):
                continue
            filtered.append(it)
        self._populate(filtered)

    def _populate(self, items: list[dict[str, Any]]) -> None:
        self.grid.clear()
        self._thumb_queue.clear()
        for it in items:
            glyph = KIND_ICONS.get(it["kind"], "📦")
            name = it.get("file_name") or it.get("title") or "arquivo"
            label = f"{glyph}  {name}\n{human_size(it.get('size'))}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, it)
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            item.setSizeHint(GRID_SIZE)
            cached = None
            if it.get("has_thumb"):
                cached = self.service.cached_thumb(it["chat_id"], it["message_id"])
            if cached:
                item.setIcon(QIcon(cached))
            else:
                item.setIcon(self._placeholder_icon(glyph))
                if it.get("has_thumb"):
                    self._thumb_queue.append(
                        (str(it["chat_id"]), int(it["message_id"]))
                    )
            self.grid.addItem(item)
        total = len(items)
        self.status.setText(
            f"{total} arquivo(s)." if total else "Nenhum arquivo com esse filtro."
        )
        fn = getattr(self, "_reposition_grid_placeholder", None)
        if fn is not None:
            fn()
        if self._thumb_queue and not self._thumb_timer.isActive():
            self._thumb_timer.start()

    def _placeholder_icon(self, glyph: str) -> QIcon:
        from PySide6.QtGui import QFont, QPainter

        pix = QPixmap(ICON_SIZE)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        font = QFont()
        font.setPointSize(48)
        painter.setFont(font)
        painter.drawText(pix.rect(), Qt.AlignCenter, glyph)
        painter.end()
        return QIcon(pix)

    def _pump_thumbnails(self) -> None:
        """Baixa as miniaturas pendentes, uma por tick, e atualiza a grade."""
        if not self._thumb_queue:
            self._thumb_timer.stop()
            return
        chat_id, message_id = self._thumb_queue.pop(0)
        try:
            future = self.service.call(
                self.service.ensure_media_thumbnail(chat_id, message_id)
            )
            path = future.result(timeout=30)
        except Exception:  # noqa: BLE001
            path = None
        if not path:
            return
        for i in range(self.grid.count()):
            item = self.grid.item(i)
            data = item.data(Qt.UserRole)
            if (
                data
                and str(data.get("chat_id")) == chat_id
                and int(data.get("message_id")) == message_id
            ):
                item.setIcon(QIcon(path))
                break

    # ------------------------------------------------------------- interações
    def _on_item_activated(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.UserRole)
        if not data:
            return
        kind = data.get("kind")
        if kind == "video":
            self.play_video_requested.emit(dict(data))
        elif kind == "image":
            self._preview_image(data)
        else:
            self.download_item(data)

    def _show_item_menu(self, pos) -> None:
        item = self.grid.itemAt(pos)
        if not item:
            return
        data = item.data(Qt.UserRole)
        if not data:
            return
        menu = QMenu(self)
        kind = data.get("kind")
        play_act = preview_act = None
        if kind == "video":
            play_act = menu.addAction("▶  Abrir no player")
        elif kind == "image":
            preview_act = menu.addAction("👁  Pré-visualizar")
        download = menu.addAction("⬇  Baixar para o disco…")
        copy_link = menu.addAction("🔗  Copiar link t.me")
        action = menu.exec(self.grid.mapToGlobal(pos))
        if action is None:
            return
        if action == play_act:
            self.play_video_requested.emit(dict(data))
        elif action == preview_act:
            self._preview_image(data)
        elif action == download:
            self.download_item(data)
        elif action == copy_link:
            self._copy_link(data)

    def _copy_link(self, data: dict[str, Any]) -> None:
        username = data.get("username")
        link = self.service.telegram_message_link(username, data["message_id"])
        if not link:
            QMessageBox.information(
                self,
                "Link indisponível",
                "Só é possível gerar link t.me para chats públicos (com @usuário).",
            )
            return
        QApplication.clipboard().setText(link)
        self.status.setText(f"Link copiado: {link}")

    def _preview_image(self, data: dict[str, Any]) -> None:
        import tempfile
        from pathlib import Path

        name = ensure_extension(
            safe_filename(data.get("file_name") or "imagem"), data.get("mime_type")
        )
        dest = Path(tempfile.gettempdir()) / f"tgfiles_{data['message_id']}_{name}"
        if self._download_with_progress(data, str(dest), "Carregando imagem…"):
            dlg = ImagePreviewDialog(
                data.get("file_name") or "Imagem", str(dest), self
            )
            dlg.exec()

    def download_item(self, data: dict[str, Any]) -> None:
        suggested = ensure_extension(
            safe_filename(data.get("file_name") or data.get("title") or "arquivo"),
            data.get("mime_type"),
        )
        path, _ = QFileDialog.getSaveFileName(self, "Salvar arquivo como", suggested)
        if not path:
            return
        if self._download_with_progress(data, path, "Baixando arquivo…"):
            self.status.setText(f"Salvo em: {path}")
            QMessageBox.information(
                self, "Download concluído", f"Arquivo salvo em:\n{path}"
            )

    def _download_with_progress(
        self, data: dict[str, Any], dest_path: str, title: str
    ) -> bool:
        dlg = QProgressDialog(title, "Cancelar", 0, 100, self)
        dlg.setWindowTitle("Telegram")
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setValue(0)

        state = {"cancel": False}
        dlg.canceled.connect(lambda: state.update(cancel=True))

        def progress(current: int, total: int) -> None:
            if total:
                pct = int(current * 100 / total)
                QTimer.singleShot(0, lambda p=pct: dlg.setValue(min(p, 100)))

        future = self.service.call(
            self.service.download_media_file(
                data["chat_id"], data["message_id"], dest_path, progress_cb=progress
            )
        )
        while not future.done():
            QApplication.processEvents()
            if state["cancel"]:
                future.cancel()
                dlg.close()
                self.status.setText("Download cancelado.")
                return False
        dlg.close()
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erro no download", str(exc))
            return False
        if not result.get("ok"):
            QMessageBox.critical(
                self, "Erro no download", result.get("error") or "Falha desconhecida."
            )
            return False
        return True

    def upload_file(self) -> None:
        chat = self._current_chat()
        if not chat:
            QMessageBox.information(
                self, "Enviar arquivo", "Selecione um chat primeiro."
            )
            return
        if not getattr(self.service, "client", None):
            QMessageBox.information(
                self, "Enviar arquivo", "Entre no Telegram primeiro."
            )
            return
        path, _ = QFileDialog.getOpenFileName(self, "Escolher arquivo para enviar")
        if not path:
            return

        dlg = QProgressDialog("Enviando arquivo…", "Cancelar", 0, 100, self)
        dlg.setWindowTitle("Telegram")
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setValue(0)
        state = {"cancel": False}
        dlg.canceled.connect(lambda: state.update(cancel=True))

        def progress(current: int, total: int) -> None:
            if total:
                pct = int(current * 100 / total)
                QTimer.singleShot(0, lambda p=pct: dlg.setValue(min(p, 100)))

        future = self.service.call(
            self.service.upload_media_file(chat["chat_id"], path, progress_cb=progress)
        )
        while not future.done():
            QApplication.processEvents()
            if state["cancel"]:
                future.cancel()
                dlg.close()
                self.status.setText("Envio cancelado.")
                return
        dlg.close()
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erro no envio", str(exc))
            return
        if not result.get("ok"):
            QMessageBox.warning(
                self, "Não foi possível enviar", result.get("error") or "Falha."
            )
            return
        QMessageBox.information(
            self, "Envio concluído", "Arquivo enviado com sucesso!"
        )
        self.refresh_media()
