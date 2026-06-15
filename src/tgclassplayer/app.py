from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import Course, Database, Video
from .dialogs import (
    EditVideoDialog,
    LoginDialog,
    SelectCoursesDialog,
    SummaryEditorDialog,
    wait_future,
)
from .logging_setup import setup_logging
from .player import VideoPlayerDialog
from .style import APP_QSS, COLORS
from .summary_parser import count_tags, parse_menu
from .telegram_service import TelegramService
from .utils import human_duration, human_size
from .vlc_locator import find_vlc, launch_vlc, open_vlc_download_page

log = logging.getLogger(__name__)

ROLE_VIDEO_ID = Qt.UserRole
ROLE_NODE_TYPE = Qt.UserRole + 1
ROLE_TOPIC = Qt.UserRole + 2


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = Database()
        self.service = TelegramService()
        self.current_course_id: int | None = None
        self.current_video_id: int | None = None
        self.setWindowTitle("TGClassPlayer — Videoaulas do Telegram")
        self.resize(1440, 860)
        self.setMinimumSize(1100, 680)
        self.build_ui()
        self.refresh_courses()
        QTimer.singleShot(400, self.try_quick_connect)

    # ============================================================= construção UI
    def build_ui(self) -> None:
        root = QSplitter()
        root.setObjectName("RootSplitter")
        root.setChildrenCollapsible(False)
        root.setHandleWidth(1)
        self.setCentralWidget(root)

        root.addWidget(self._build_sidebar())
        root.addWidget(self._build_center())
        root.addWidget(self._build_right())
        root.setSizes([310, 800, 360])
        root.setStretchFactor(1, 1)

        self._build_menu()

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(280)
        sidebar.setMaximumWidth(360)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(20, 22, 20, 20)
        layout.setSpacing(12)

        # Marca
        brand_row = QHBoxLayout()
        brand_row.setSpacing(0)
        brand = QLabel("TGClass")
        brand.setObjectName("Brand")
        brand_accent = QLabel("Player")
        brand_accent.setObjectName("BrandAccent")
        brand_row.addWidget(brand)
        brand_row.addWidget(brand_accent)
        brand_row.addStretch(1)
        layout.addLayout(brand_row)
        subtitle = QLabel("Seus cursos do Telegram, organizados")
        subtitle.setObjectName("Muted2")
        layout.addWidget(subtitle)

        layout.addSpacing(6)
        self.status_label = QLabel("Não conectado")
        self.status_label.setObjectName("StatusLabel")
        layout.addWidget(self.status_label)

        self.login_btn = QPushButton("Conectar ao Telegram")
        self.login_btn.setObjectName("PrimaryButton")
        self.login_btn.clicked.connect(self.open_login)
        layout.addWidget(self.login_btn)

        self.add_courses_btn = QPushButton("+  Adicionar cursos")
        self.add_courses_btn.clicked.connect(self.add_courses_from_telegram)
        layout.addWidget(self.add_courses_btn)

        layout.addSpacing(10)
        section = QLabel("MEUS CURSOS")
        section.setObjectName("SectionTitle")
        layout.addWidget(section)

        self.course_list = QListWidget()
        self.course_list.currentItemChanged.connect(self.on_course_selected)
        self.course_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.course_list.customContextMenuRequested.connect(self.show_course_menu)
        layout.addWidget(self.course_list, 1)

        self.refresh_btn = QPushButton("↻  Atualizar lista")
        self.refresh_btn.setObjectName("GhostButton")
        self.refresh_btn.clicked.connect(self.refresh_courses)
        layout.addWidget(self.refresh_btn)

        return sidebar

    def _build_center(self) -> QWidget:
        center = QWidget()
        center.setObjectName("CenterPane")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)

        # Cabeçalho do curso
        header = QHBoxLayout()
        head_col = QVBoxLayout()
        head_col.setSpacing(4)
        self.course_title = QLabel("Selecione um curso")
        self.course_title.setObjectName("PageTitle")
        self.course_meta = QLabel("Conecte-se ao Telegram e adicione seus grupos/canais como cursos.")
        self.course_meta.setObjectName("Muted")
        self.course_meta.setWordWrap(True)
        head_col.addWidget(self.course_title)
        head_col.addWidget(self.course_meta)
        header.addLayout(head_col, 1)

        self.sync_btn = QPushButton("⟳  Sincronizar")
        self.sync_btn.setObjectName("PrimaryButton")
        self.sync_btn.clicked.connect(self.sync_current_course)
        self.summary_btn = QPushButton("✎  Editar sumário")
        self.summary_btn.clicked.connect(self.edit_summary)
        header.addWidget(self.summary_btn)
        header.addWidget(self.sync_btn)
        layout.addLayout(header)

        # Barra de progresso do curso
        self.course_progress = QProgressBar()
        self.course_progress.setTextVisible(True)
        self.course_progress.setFormat("%p% concluído")
        self.course_progress.setValue(0)
        layout.addWidget(self.course_progress)

        # Busca + filtros
        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍  Buscar aula, hashtag ou módulo...")
        self.search_box.textChanged.connect(self.render_current_course)
        search_row.addWidget(self.search_box, 1)
        self.fav_filter_btn = QPushButton("★ Favoritas")
        self.fav_filter_btn.setCheckable(True)
        self.fav_filter_btn.toggled.connect(self.render_current_course)
        search_row.addWidget(self.fav_filter_btn)
        self.pending_filter_btn = QPushButton("Pendentes")
        self.pending_filter_btn.setCheckable(True)
        self.pending_filter_btn.toggled.connect(self.render_current_course)
        search_row.addWidget(self.pending_filter_btn)
        layout.addLayout(search_row)

        # Árvore de aulas
        self.video_tree = QTreeWidget()
        self.video_tree.setHeaderLabels(["Aula / módulo", "Tags", "Duração", "Status"])
        self.video_tree.itemSelectionChanged.connect(self.on_video_selected)
        self.video_tree.itemDoubleClicked.connect(self.on_tree_item_double_clicked)
        self.video_tree.setRootIsDecorated(True)
        self.video_tree.setItemsExpandable(True)
        self.video_tree.setExpandsOnDoubleClick(False)
        self.video_tree.setAnimated(True)
        self.video_tree.setIndentation(22)
        self.video_tree.setUniformRowHeights(False)
        self.video_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.video_tree.customContextMenuRequested.connect(self.show_video_menu)
        self.video_tree.header()
        self.video_tree.setColumnWidth(0, 560)
        self.video_tree.setColumnWidth(1, 140)
        self.video_tree.setColumnWidth(2, 90)
        layout.addWidget(self.video_tree, 1)

        return center

    def _build_right(self) -> QWidget:
        right = QWidget()
        right.setObjectName("RightPane")
        right.setMinimumWidth(330)
        right.setMaximumWidth(440)
        layout = QVBoxLayout(right)
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(14)

        # Cartão da aula selecionada
        card = QFrame()
        card.setObjectName("HeroCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(8)
        label = QLabel("AULA SELECIONADA")
        label.setObjectName("SectionTitle")
        card_layout.addWidget(label)
        self.video_title = QLabel("Nenhuma aula selecionada")
        self.video_title.setObjectName("PanelTitle")
        self.video_title.setWordWrap(True)
        card_layout.addWidget(self.video_title)
        self.video_info = QLabel("Clique em uma aula para ver os detalhes e assistir.")
        self.video_info.setObjectName("Muted")
        self.video_info.setWordWrap(True)
        card_layout.addWidget(self.video_info)

        self.resume_bar = QProgressBar()
        self.resume_bar.setMaximumHeight(8)
        self.resume_bar.setTextVisible(False)
        self.resume_bar.setValue(0)
        self.resume_bar.hide()
        card_layout.addWidget(self.resume_bar)

        card_layout.addSpacing(8)
        self.watch_btn = QPushButton("▶  Assistir agora")
        self.watch_btn.setObjectName("PrimaryButton")
        self.watch_btn.clicked.connect(self.watch_selected_internal)
        card_layout.addWidget(self.watch_btn)

        row1 = QHBoxLayout()
        self.watch_vlc_btn = QPushButton("Abrir no VLC")
        self.watch_vlc_btn.clicked.connect(self.watch_selected_vlc)
        self.fav_btn = QPushButton("★ Favorito")
        self.fav_btn.clicked.connect(self.toggle_favorite_selected)
        row1.addWidget(self.watch_vlc_btn)
        row1.addWidget(self.fav_btn)
        card_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.edit_btn = QPushButton("✎ Editar")
        self.edit_btn.clicked.connect(self.edit_selected_video)
        self.mark_btn = QPushButton("✓ Assistida")
        self.mark_btn.clicked.connect(self.mark_selected_watched)
        row2.addWidget(self.edit_btn)
        row2.addWidget(self.mark_btn)
        card_layout.addLayout(row2)

        layout.addWidget(card)

        # Cartão "Como funciona"
        help_card = QFrame()
        help_card.setObjectName("CardSoft")
        h_layout = QVBoxLayout(help_card)
        h_layout.setContentsMargins(18, 16, 18, 16)
        h_layout.setSpacing(6)
        help_title = QLabel("COMO FUNCIONA")
        help_title.setObjectName("SectionTitle")
        h_layout.addWidget(help_title)
        help_text = QLabel(
            "O app cria um link local (127.0.0.1) e carrega o vídeo por blocos, "
            "sob demanda. A aula começa na hora, você pode pular para qualquer "
            "ponto sem travar e o vídeo não fica salvo no computador."
        )
        help_text.setObjectName("Muted")
        help_text.setWordWrap(True)
        h_layout.addWidget(help_text)
        layout.addWidget(help_card)

        layout.addStretch(1)
        return right

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        account = menubar.addMenu("Conta")
        relogin = QAction("Entrar / trocar conta", self)
        relogin.triggered.connect(self.open_login)
        account.addAction(relogin)
        logout = QAction("Sair da conta (logout)", self)
        logout.triggered.connect(self.do_logout)
        account.addAction(logout)

        tools = menubar.addMenu("Ferramentas")
        vlc_action = QAction("Configurar VLC...", self)
        vlc_action.triggered.connect(self.choose_vlc)
        tools.addAction(vlc_action)
        log_action = QAction("Abrir pasta de logs", self)
        log_action.triggered.connect(self.open_logs)
        tools.addAction(log_action)
        data_action = QAction("Abrir pasta de dados", self)
        data_action.triggered.connect(self.open_data_folder)
        tools.addAction(data_action)

        help_menu = menubar.addMenu("Ajuda")
        about = QAction("Sobre o TGClassPlayer", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    # ============================================================ conexão Telegram
    def try_quick_connect(self) -> None:
        api_id = self.db.get_setting("api_id")
        api_hash = self.db.get_setting("api_hash")
        if not api_id or not api_hash:
            return
        try:
            result = wait_future(
                self.service.call(self.service.ensure_connected(api_id, api_hash)),
                "Telegram", "Verificando sessão salva...", self, timeout_ms=15000,
            )
            if result.get("authorized"):
                me = result.get("me") or {}
                self._set_connected(me)
        except Exception:  # noqa: BLE001
            log.exception("Autoconexão falhou")
            self.status_label.setText("Sessão ainda não conectada")

    def _set_connected(self, me: dict[str, Any]) -> None:
        name = me.get("first_name") or me.get("username") or "Conta conectada"
        self.status_label.setText(f"●  {name}")
        self.status_label.setObjectName("StatusConnected")
        self.status_label.setStyleSheet("")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.login_btn.setText("Conta conectada ✓")

    def open_login(self) -> None:
        dlg = LoginDialog(self.service, self.db, self)
        if dlg.exec() == QDialog.Accepted:
            try:
                me = wait_future(self.service.call(self.service.get_me()), "Telegram", "Carregando conta...", self)
                self._set_connected(me or {})
                QMessageBox.information(self, "Telegram", "Conta conectada com sucesso!")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Telegram", str(exc))

    def do_logout(self) -> None:
        if QMessageBox.question(self, "Logout", "Deseja sair da conta do Telegram?") != QMessageBox.Yes:
            return
        try:
            wait_future(self.service.call(self.service.logout()), "Telegram", "Saindo...", self)
        except Exception:  # noqa: BLE001
            pass
        self.status_label.setText("Não conectado")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.login_btn.setText("Conectar ao Telegram")

    # =================================================================== cursos
    def add_courses_from_telegram(self) -> None:
        try:
            courses = wait_future(
                self.service.call(self.service.list_dialog_courses()),
                "Telegram", "Lendo seus grupos e canais...", self,
            )
            dlg = SelectCoursesDialog(courses, self)
            if dlg.exec() != QDialog.Accepted:
                return
            selected = dlg.selected_courses()
            for course in selected:
                self.db.upsert_course(course)
            self.refresh_courses()
            if selected:
                QMessageBox.information(self, "Cursos", f"{len(selected)} curso(s) adicionado(s).")
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao adicionar cursos")
            QMessageBox.critical(
                self, "Erro ao adicionar cursos",
                f"{exc}\n\nSe a sessão aparecer conectada mas o erro continuar, "
                "use Conta → Entrar / trocar conta para renovar a sessão.",
            )

    def refresh_courses(self) -> None:
        selected_id = self.current_course_id
        self.course_list.blockSignals(True)
        self.course_list.clear()
        for course in self.db.list_courses():
            videos = self.db.list_videos(course.id)
            watched = sum(1 for v in videos if v.watched_at)
            label = course.title
            sub = f"{len(videos)} aulas · {watched} assistidas" if videos else "sem sincronizar"
            item = QListWidgetItem(f"{label}\n{sub}")
            item.setData(Qt.UserRole, course.id)
            self.course_list.addItem(item)
            if selected_id and course.id == selected_id:
                self.course_list.setCurrentItem(item)
        self.course_list.blockSignals(False)
        if selected_id:
            self.render_current_course()

    def show_course_menu(self, pos) -> None:
        item = self.course_list.itemAt(pos)
        if not item:
            return
        course_id = int(item.data(Qt.UserRole))
        menu = QMenu(self)
        rename = menu.addAction("Renomear curso")
        sync = menu.addAction("Sincronizar")
        summary = menu.addAction("Editar sumário")
        menu.addSeparator()
        delete = menu.addAction("Excluir curso")
        action = menu.exec(self.course_list.mapToGlobal(pos))
        if action == rename:
            self._rename_course(course_id)
        elif action == sync:
            self.current_course_id = course_id
            self.sync_current_course()
        elif action == summary:
            self.current_course_id = course_id
            self.edit_summary()
        elif action == delete:
            self._delete_course(course_id)

    def _rename_course(self, course_id: int) -> None:
        course = self.db.get_course(course_id)
        if not course:
            return
        title, ok = QInputDialog.getText(self, "Renomear curso", "Novo nome:", text=course.title)
        if ok and title.strip():
            self.db.rename_course(course_id, title.strip())
            self.refresh_courses()

    def _delete_course(self, course_id: int) -> None:
        course = self.db.get_course(course_id)
        if not course:
            return
        if QMessageBox.question(
            self, "Excluir curso",
            f"Excluir “{course.title}” e todas as aulas indexadas?\n(O conteúdo no Telegram não é afetado.)",
        ) != QMessageBox.Yes:
            return
        self.db.delete_course(course_id)
        if self.current_course_id == course_id:
            self.current_course_id = None
            self.video_tree.clear()
            self.course_title.setText("Selecione um curso")
            self.course_meta.setText("")
        self.refresh_courses()

    def on_course_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None = None) -> None:
        if not current:
            self.current_course_id = None
            return
        self.current_course_id = int(current.data(Qt.UserRole))
        self.render_current_course()

    def get_current_course(self) -> Course | None:
        return self.db.get_course(self.current_course_id) if self.current_course_id else None

    def sync_current_course(self) -> None:
        course = self.get_current_course()
        if not course:
            QMessageBox.information(self, "Curso", "Selecione um curso primeiro.")
            return
        limit, ok = QInputDialog.getInt(
            self, "Sincronizar",
            "Quantas mensagens analisar? Use 99999 para praticamente tudo.",
            99999, 0, 1000000, 1000,
        )
        if not ok:
            return
        try:
            result = wait_future(
                self.service.call(self.service.sync_course(course.chat_id, limit=limit)),
                "Sincronizando", "Buscando vídeos, hashtags e sumários no Telegram...", self,
            )
            course_id = self.db.upsert_course(result["chat"])
            self.current_course_id = course_id
            self.db.update_course_summary(course_id, result.get("summary_text"), result.get("topics") or [])
            self.db.replace_videos(course_id, result.get("videos") or [])
            self.refresh_courses()
            self.render_current_course()
            QMessageBox.information(
                self, "Sincronizado",
                f"{len(result.get('videos') or [])} vídeo(s) encontrados em "
                f"{result.get('scanned')} mensagens analisadas.",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao sincronizar curso")
            QMessageBox.critical(self, "Erro ao sincronizar", f"{exc}")

    # =========================================================== render da árvore
    def render_current_course(self) -> None:
        course = self.get_current_course()
        self.video_tree.clear()
        self.current_video_id = None
        if not course:
            self.course_progress.setValue(0)
            return
        videos = self.db.list_videos(course.id)
        topics = course.topics()
        query = self.search_box.text().strip().lower()
        only_fav = self.fav_filter_btn.isChecked()
        only_pending = self.pending_filter_btn.isChecked()

        watched = sum(1 for v in videos if v.watched_at)
        total_size = sum(int(v.size or 0) for v in videos)
        total_duration = sum(int(v.duration or 0) for v in videos)
        self.course_title.setText(course.title)
        self.course_meta.setText(
            f"{len(videos)} aulas  ·  {len(topics)} sumários  ·  {watched} assistidas  ·  "
            f"{human_size(total_size)}  ·  {human_duration(total_duration)}  ·  "
            f"último sync: {self._fmt_date(course.last_sync)}"
        )
        pct = int((watched / len(videos)) * 100) if videos else 0
        self.course_progress.setValue(pct)

        def passes(video: Video) -> bool:
            if only_fav and not video.favorite:
                return False
            if only_pending and video.watched_at:
                return False
            if query and not self.video_matches(video, query):
                return False
            return True

        tag_map: dict[str, Video] = {}
        for video in videos:
            for tag in video.hashtags:
                tag_map.setdefault(tag.upper(), video)

        shown: set[int] = set()
        has_any = False

        if topics:
            for idx, topic in enumerate(topics):
                topic_title = str(topic.get("title") or "Sumário")
                summary_text = topic.get("summary_text") or ""
                topic_tags = topic.get("tags") or []
                telegram_topic_id = str(topic.get("telegram_topic_id") or topic.get("id") or "general")
                topic_item = QTreeWidgetItem([
                    f"📚  {topic_title}",
                    f"{len(topic_tags)} tags" if topic_tags else "",
                    "", "sumário",
                ])
                topic_item.setData(0, ROLE_NODE_TYPE, "topic")
                topic_item.setData(0, ROLE_TOPIC, topic_title)
                self._style_folder(topic_item)
                topic_item.setExpanded(idx == 0)

                before = topic_item.childCount()
                menu_root = parse_menu(summary_text)
                any_visible = False
                for node in menu_root.children:
                    if self.add_menu_node(topic_item, node, tag_map, shown, passes):
                        any_visible = True

                topic_unmatched = []
                if telegram_topic_id != "general":
                    topic_unmatched = [
                        v for v in videos
                        if v.id not in shown and str(v.topic_id or "general") == telegram_topic_id
                    ]
                # também agrupa por topic_title editado manualmente
                topic_unmatched += [
                    v for v in videos
                    if v.id not in shown and (v.topic_title or "") == topic_title and v not in topic_unmatched
                ]
                added_unmatched = self.add_video_group(
                    topic_item, "Outras aulas deste tópico", topic_unmatched, shown, passes,
                )
                any_visible = any_visible or added_unmatched or topic_item.childCount() > before

                if any_visible or (not query and not only_fav and not only_pending and summary_text):
                    self.video_tree.addTopLevelItem(topic_item)
                    has_any = True

        if not topics and course.summary_text:
            menu_root = parse_menu(course.summary_text)
            if count_tags(menu_root) > 0 or menu_root.children:
                for node in menu_root.children:
                    if self.add_menu_node(self.video_tree, node, tag_map, shown, passes):
                        has_any = True

        other_videos = [v for v in videos if v.id not in shown and passes(v)]
        if other_videos:
            parent = QTreeWidgetItem(["Outras videoaulas", "", "", "não indexadas"])
            parent.setData(0, ROLE_NODE_TYPE, "folder")
            self._style_folder(parent)
            parent.setExpanded(not has_any)
            grouped: dict[str, list[Video]] = {}
            for video in other_videos:
                key = video.topic_title or "Sem tópico"
                grouped.setdefault(key, []).append(video)
            added = False
            for title in sorted(grouped, key=lambda x: x.lower()):
                group_item = QTreeWidgetItem([title, "", "", "tópico"])
                group_item.setData(0, ROLE_NODE_TYPE, "folder")
                self._style_folder(group_item)
                group_item.setExpanded(False)
                if self.add_video_group(group_item, "", grouped[title], shown, passes, direct=True):
                    parent.addChild(group_item)
                    added = True
            if added:
                self.video_tree.addTopLevelItem(parent)
                has_any = True

        if not has_any:
            placeholder = QTreeWidgetItem(
                ["Nenhuma aula encontrada", "", "",
                 "Clique em Sincronizar" if not videos else "Ajuste a busca/filtros"]
            )
            self.video_tree.addTopLevelItem(placeholder)
        self.video_tree.expandToDepth(0)

    def _fmt_date(self, iso: str | None) -> str:
        if not iso:
            return "nunca"
        return iso.replace("T", " ")[:16]

    def _style_folder(self, item: QTreeWidgetItem) -> None:
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setForeground(0, QColor(COLORS["text"]))

    def add_video_group(self, parent_item, group_title, videos, shown, passes, direct=False) -> bool:
        container = parent_item if direct or not group_title else QTreeWidgetItem([group_title, "", "", "módulo"])
        if container is not parent_item:
            container.setData(0, ROLE_NODE_TYPE, "folder")
            self._style_folder(container)
        added = False
        for video in videos:
            if not passes(video):
                continue
            container.addChild(self.video_item(video))
            shown.add(video.id)
            added = True
        if added and container is not parent_item:
            parent_item.addChild(container)
        return added

    def add_menu_node(self, parent_widget_or_item, node, tag_map, shown, passes) -> bool:
        item = QTreeWidgetItem([node.title, "", "", "módulo"])
        item.setData(0, ROLE_NODE_TYPE, "folder")
        self._style_folder(item)
        item.setExpanded(False)
        any_visible = False
        for child in node.children:
            if self.add_menu_node(item, child, tag_map, shown, passes):
                any_visible = True
        for tag in node.tags:
            video = tag_map.get(tag.upper())
            if video:
                if not passes(video):
                    continue
                child_item = self.video_item(video, prefix=tag)
                item.addChild(child_item)
                shown.add(video.id)
                any_visible = True
        if any_visible:
            if isinstance(parent_widget_or_item, QTreeWidget):
                parent_widget_or_item.addTopLevelItem(item)
            else:
                parent_widget_or_item.addChild(item)
            return True
        return False

    def video_item(self, video: Video, prefix: str | None = None) -> QTreeWidgetItem:
        star = "★ " if video.favorite else ""
        title = f"{star}{prefix}  —  {video.title}" if prefix else f"{star}{video.title}"
        tags = " ".join(video.hashtags[:3])
        if video.watched_at:
            status = "✓ assistida"
        elif video.progress and video.progress > 0.02:
            status = f"▶ {int(video.progress * 100)}%"
        else:
            status = "pendente"
        item = QTreeWidgetItem([title, tags, human_duration(video.duration), status])
        item.setData(0, ROLE_VIDEO_ID, video.id)
        if video.watched_at:
            item.setForeground(0, QColor(COLORS["muted"]))
        return item

    def video_matches(self, video: Video, query: str) -> bool:
        hay = " ".join(
            [video.title, video.file_name, video.caption or "", " ".join(video.hashtags),
             video.topic_title or "", video.note or ""]
        ).lower()
        return query in hay

    # ================================================== seleção / detalhes da aula
    def on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        video_id = item.data(0, ROLE_VIDEO_ID)
        if video_id:
            self.current_video_id = int(video_id)
            self.watch_selected_internal()
            return
        if item.childCount() > 0:
            item.setExpanded(not item.isExpanded())

    def on_video_selected(self) -> None:
        items = self.video_tree.selectedItems()
        if not items:
            return
        item = items[0]
        video_id = item.data(0, ROLE_VIDEO_ID)
        if not video_id:
            self.current_video_id = None
            self.video_title.setText(item.text(0).replace("📚  ", ""))
            children = item.childCount()
            self.video_info.setText(
                f"Módulo/tópico selecionado · {children} item(ns). "
                "Clique na seta para abrir/fechar ou escolha uma aula."
            )
            self.resume_bar.hide()
            return
        self.current_video_id = int(video_id)
        video = self.db.get_video(self.current_video_id)
        if not video:
            return
        self.video_title.setText(("★ " if video.favorite else "") + video.title)
        tags = " ".join(video.hashtags) or "sem hashtags"
        status = "Assistida" if video.watched_at else (
            f"{int(video.progress*100)}% assistido" if video.progress > 0.02 else "Não assistida"
        )
        note = f"\nAnotação: {video.note}" if video.note else ""
        self.video_info.setText(
            f"{human_size(video.size)} · {human_duration(video.duration)} · {status}\n"
            f"Tópico: {video.topic_title or 'Geral'}\n"
            f"Tags: {tags}{note}"
        )
        if video.progress and 0.02 < video.progress < 0.95:
            self.resume_bar.setValue(int(video.progress * 100))
            self.resume_bar.show()
        else:
            self.resume_bar.hide()
        self.fav_btn.setText("★ Favorito" if not video.favorite else "☆ Remover favorito")

    def selected_video(self) -> Video | None:
        if not self.current_video_id:
            QMessageBox.information(self, "Aula", "Selecione uma videoaula primeiro.")
            return None
        return self.db.get_video(self.current_video_id)

    def show_video_menu(self, pos) -> None:
        item = self.video_tree.itemAt(pos)
        if not item:
            return
        video_id = item.data(0, ROLE_VIDEO_ID)
        if not video_id:
            return
        self.current_video_id = int(video_id)
        video = self.db.get_video(self.current_video_id)
        if not video:
            return
        menu = QMenu(self)
        watch = menu.addAction("▶ Assistir")
        vlc = menu.addAction("Abrir no VLC")
        menu.addSeparator()
        edit = menu.addAction("✎ Editar aula")
        fav = menu.addAction("☆ Remover favorito" if video.favorite else "★ Favoritar")
        if video.watched_at:
            mark = menu.addAction("Marcar como NÃO assistida")
        else:
            mark = menu.addAction("✓ Marcar como assistida")
        copy = menu.addAction("Copiar link temporário")
        menu.addSeparator()
        delete = menu.addAction("Remover da lista")
        action = menu.exec(self.video_tree.mapToGlobal(pos))
        if action == watch:
            self.watch_selected_internal()
        elif action == vlc:
            self.watch_selected_vlc()
        elif action == edit:
            self.edit_selected_video()
        elif action == fav:
            self.toggle_favorite_selected()
        elif action == mark:
            if video.watched_at:
                self.db.mark_unwatched(video.id)
            else:
                self.db.mark_watched(video.id)
            self.render_current_course()
            self.refresh_courses()
        elif action == copy:
            self.copy_stream_url()
        elif action == delete:
            self.db.delete_video(video.id)
            self.render_current_course()
            self.refresh_courses()

    # =================================================================== edições
    def edit_selected_video(self) -> None:
        video = self.selected_video()
        if not video:
            return
        course = self.get_current_course()
        topics = course.topics() if course else []
        dlg = EditVideoDialog(video, topics, self)
        if dlg.exec() != QDialog.Accepted:
            return
        values = dlg.values()
        self.db.rename_video(video.id, values["title"])
        self.db.set_video_topic(video.id, video.topic_id, values["topic_title"])
        self.db.set_video_hashtags(video.id, values["hashtags"])
        self.db.set_video_note(video.id, values["note"])
        self.render_current_course()
        self.on_video_selected()

    def edit_summary(self) -> None:
        course = self.get_current_course()
        if not course:
            QMessageBox.information(self, "Curso", "Selecione um curso primeiro.")
            return
        dlg = SummaryEditorDialog(course.topics(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        self.db.set_course_topics(course.id, dlg.result_topics())
        self.render_current_course()

    def toggle_favorite_selected(self) -> None:
        video = self.selected_video()
        if not video:
            return
        self.db.toggle_favorite(video.id)
        self.render_current_course()
        self.on_video_selected()

    def mark_selected_watched(self) -> None:
        video = self.selected_video()
        if not video:
            return
        self.db.mark_watched(video.id)
        self.render_current_course()
        self.refresh_courses()

    # =================================================================== player
    def _stream_payload(self, video: Video) -> dict[str, Any]:
        return {
            "chat_id": video.chat_id,
            "message_id": video.message_id,
            "title": video.title,
            "file_name": video.file_name,
            "mime_type": video.mime_type,
            "size": video.size,
        }

    def prepare_stream_for_selected(self) -> dict[str, Any] | None:
        video = self.selected_video()
        if not video:
            return None
        result = wait_future(
            self.service.call(self.service.prepare_stream(self._stream_payload(video))),
            "Streaming", "Preparando o link local para o player...", self,
        )
        return result if isinstance(result, dict) else None

    def watch_selected_internal(self) -> None:
        video = self.selected_video()
        if not video:
            return
        try:
            stream = self.prepare_stream_for_selected()
            if not stream or not stream.get("url"):
                return
            video_id = video.id

            def save_progress(position_ms: int, duration_ms: int) -> None:
                try:
                    self.db.save_progress(video_id, position_ms, duration_ms)
                except Exception:  # noqa: BLE001
                    pass

            try:
                dlg = VideoPlayerDialog(
                    video.title, stream.get("url"), stream.get("token"), self.service,
                    start_position_ms=video.position_ms, on_progress=save_progress, parent=self,
                )
            except Exception as exc:  # noqa: BLE001
                answer = QMessageBox.question(
                    self, "Player indisponível",
                    f"Não consegui abrir o player interno ({exc}). Deseja tentar o VLC?",
                )
                if answer == QMessageBox.Yes:
                    self.watch_selected_vlc()
                return
            dlg.exec()
            self.render_current_course()
            self.refresh_courses()
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao assistir no player interno")
            QMessageBox.critical(self, "Erro no streaming", f"{exc}")

    def watch_selected_vlc(self) -> None:
        video = self.selected_video()
        if not video:
            return
        try:
            stream = self.prepare_stream_for_selected()
            if not stream or not stream.get("url"):
                return
            url = stream.get("url")
            vlc_path = find_vlc(self.db.get_setting("vlc_path"))
            if not vlc_path:
                answer = QMessageBox.question(
                    self, "VLC não encontrado",
                    "Não encontrei o VLC. Deseja abrir a página oficial para instalar?",
                )
                if answer == QMessageBox.Yes:
                    open_vlc_download_page()
                return
            launch_vlc(vlc_path, url)
            self.db.mark_watched(video.id)
            self.render_current_course()
            self.refresh_courses()
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao assistir no VLC")
            QMessageBox.critical(self, "Erro no streaming", f"{exc}")

    def copy_stream_url(self) -> None:
        try:
            stream = self.prepare_stream_for_selected()
            url = stream.get("url") if stream else None
            if url:
                QApplication.clipboard().setText(url)
                QMessageBox.information(
                    self, "Link copiado",
                    "Link temporário copiado. Ele só funciona enquanto o app estiver aberto.",
                )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erro", str(exc))

    # ============================================================== ferramentas
    def choose_vlc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar vlc.exe", str(Path.home()),
            "VLC (vlc.exe);;Executável (*.exe);;Todos (*.*)",
        )
        if path:
            self.db.set_setting("vlc_path", path)
            QMessageBox.information(self, "VLC", "Caminho do VLC salvo.")

    def open_logs(self) -> None:
        from .paths import LOG_DIR
        self._open_folder(LOG_DIR)

    def open_data_folder(self) -> None:
        from .paths import DATA_DIR
        self._open_folder(DATA_DIR)

    def _open_folder(self, folder: Path) -> None:
        import os
        import subprocess
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def show_about(self) -> None:
        from . import __version__
        QMessageBox.information(
            self, "Sobre",
            f"TGClassPlayer v{__version__}\n\n"
            "Organize e assista às videoaulas dos seus cursos no Telegram, "
            "com player premium e streaming sob demanda (sem armazenar os vídeos).\n\n"
            "Nunca compartilhe seu código de login, senha 2FA ou API HASH.",
        )

    def closeEvent(self, event) -> None:
        try:
            self.service.stop()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)


def excepthook(exc_type, exc, tb):
    logging.getLogger(__name__).critical("Erro não tratado", exc_info=(exc_type, exc, tb))
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        QMessageBox.critical(None, "Erro inesperado", text[:4000])
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    setup_logging()
    sys.excepthook = excepthook
    app = QApplication(sys.argv)
    app.setApplicationName("TGClassPlayer")
    app.setStyleSheet(APP_QSS)
    try:
        app.setFont(QFont("Segoe UI", 10))
    except Exception:  # noqa: BLE001
        pass
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
