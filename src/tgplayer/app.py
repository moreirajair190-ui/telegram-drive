"""Janela principal do TgPlayer (interface refeita do zero).

Estrutura da interface:
- Barra superior: marca, nome do curso/matéria, BARRA DE PROGRESSO GERAL
  (aulas assistidas/total, horas) e alternância de tema 🌞/🌙 (persistente).
- Abas (QTabWidget): "Aulas" (navegação por matérias + módulos, filtros, busca,
  edição completa, player) e "Acompanhamento" 📊 (Pomodoro, tarefas, gráficos).
- Aba "Aulas": 3 colunas — matérias (esquerda), árvore de módulos/aulas
  (centro) e painel de detalhes da aula (direita).

Toda a navegação usa o modelo de MATÉRIAS (subjects) do banco v6 e as novas
APIs de sincronização, streaming (player same-origin) e diálogos.
"""

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
    QButtonGroup,
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
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import Course, Database, Subject, Video
from .dialogs import (
    EditVideoDialog,
    LoginDialog,
    SelectCoursesDialog,
    StreamingSettingsDialog,
    SubjectsEditorDialog,
    wait_future,
)
from .logging_setup import setup_logging
from .player import VideoPlayerDialog
from .study_tab import StudyTab
from .style import build_qss, palette
from .summary_parser import match_videos_to_tree
from .telegram_service import TelegramService
from .utils import human_duration, human_size
from .vlc_locator import find_vlc, launch_vlc, open_vlc_download_page

log = logging.getLogger(__name__)

ROLE_VIDEO_ID = Qt.UserRole
ROLE_NODE_TYPE = Qt.UserRole + 1

# Identificador especial para "todas as matérias".
SUBJECT_ALL = -1
SUBJECT_NONE = 0  # vídeos sem matéria
SUBJECT_CONTINUE = -2  # filtro virtual "Continuar assistindo" (resume)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.db = Database()
        self.service = TelegramService(db=self.db)
        # Restaura a qualidade de streaming / modo adaptativo salvos.
        try:
            self.service.set_quality(
                self.db.get_setting("streaming_quality") or "original",
                (self.db.get_setting("adaptive_mode") or "0") == "1",
            )
        except Exception:  # noqa: BLE001
            pass
        self.theme = self.db.get_setting("theme") or "dark"
        self.current_course_id: int | None = None
        self.current_subject_id: int = SUBJECT_ALL
        self.current_video_id: int | None = None
        self.setWindowTitle("TgPlayer — Videoaulas do Telegram")
        self.resize(1500, 900)
        self.setMinimumSize(1180, 720)
        self.build_ui()
        self.apply_theme(self.theme, persist=False)
        self.refresh_courses()
        QTimer.singleShot(400, self.try_quick_connect)
        # Atualiza o widget de banda em tempo real (a cada 1.5s).
        self._bw_timer = QTimer(self)
        self._bw_timer.setInterval(1500)
        self._bw_timer.timeout.connect(self._update_bandwidth_widget)
        self._bw_timer.start()

    # ===================================================== construção da interface
    def build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_lessons_tab(), "🎬  Aulas")
        self.study_tab = StudyTab(self.db, self.get_current_course)
        self.tabs.addTab(self.study_tab, "📊  Acompanhamento")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

        self._build_menu()

    # ---------------------------------------------------------------- barra topo
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(78)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(22, 12, 22, 12)
        layout.setSpacing(18)

        brand_box = QHBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel("TGClass")
        brand.setObjectName("Brand")
        brand_accent = QLabel("Player")
        brand_accent.setObjectName("BrandAccent")
        brand_box.addWidget(brand)
        brand_box.addWidget(brand_accent)
        layout.addLayout(brand_box)

        # Curso/matéria atual + progresso geral.
        center = QVBoxLayout()
        center.setSpacing(3)
        self.topbar_title = QLabel("Selecione um curso")
        self.topbar_title.setObjectName("PageTitle")
        center.addWidget(self.topbar_title)

        prog_row = QHBoxLayout()
        prog_row.setSpacing(10)
        self.overall_progress = QProgressBar()
        self.overall_progress.setTextVisible(True)
        self.overall_progress.setFormat("%p% concluído")
        self.overall_progress.setValue(0)
        self.overall_progress.setFixedHeight(16)
        prog_row.addWidget(self.overall_progress, 1)
        self.overall_meta = QLabel("0/0 aulas · 0h")
        self.overall_meta.setObjectName("Muted2")
        prog_row.addWidget(self.overall_meta)
        center.addLayout(prog_row)
        layout.addLayout(center, 1)

        # Widget de banda em tempo real (some quando não há streaming ativo).
        self.bandwidth_label = QLabel("")
        self.bandwidth_label.setObjectName("Muted2")
        self.bandwidth_label.setToolTip("Banda agregada das sessões de streaming ativas")
        self.bandwidth_label.hide()
        layout.addWidget(self.bandwidth_label)

        # Status da conta.
        self.status_label = QLabel("Não conectado")
        self.status_label.setObjectName("StatusLabel")
        layout.addWidget(self.status_label)

        self.login_btn = QPushButton("Conectar")
        self.login_btn.setObjectName("PrimaryButton")
        self.login_btn.clicked.connect(self.open_login)
        layout.addWidget(self.login_btn)

        self.theme_btn = QPushButton("🌙")
        self.theme_btn.setObjectName("ThemeToggle")
        self.theme_btn.setToolTip("Alternar tema claro/escuro")
        self.theme_btn.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_btn)

        return bar

    # ---------------------------------------------------------------- aba "Aulas"
    def _build_lessons_tab(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        outer.addWidget(self._build_center(), 1)
        outer.addWidget(self._build_right())
        return page

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(300)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        section = QLabel("MEUS CURSOS")
        section.setObjectName("SectionTitle")
        layout.addWidget(section)

        self.course_list = QListWidget()
        self.course_list.currentItemChanged.connect(self.on_course_selected)
        self.course_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.course_list.customContextMenuRequested.connect(self.show_course_menu)
        layout.addWidget(self.course_list, 2)

        self.add_courses_btn = QPushButton("+  Adicionar cursos")
        self.add_courses_btn.clicked.connect(self.add_courses_from_telegram)
        layout.addWidget(self.add_courses_btn)

        layout.addSpacing(6)
        subj_section = QLabel("MATÉRIAS")
        subj_section.setObjectName("SectionTitle")
        layout.addWidget(subj_section)

        self.subject_list = QListWidget()
        self.subject_list.currentItemChanged.connect(self.on_subject_selected)
        self.subject_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.subject_list.customContextMenuRequested.connect(self.show_subject_menu)
        layout.addWidget(self.subject_list, 3)

        self.edit_subjects_btn = QPushButton("✎  Editar matérias/sumários")
        self.edit_subjects_btn.clicked.connect(self.edit_subjects)
        layout.addWidget(self.edit_subjects_btn)

        return sidebar

    def _build_center(self) -> QWidget:
        center = QWidget()
        center.setObjectName("CenterPane")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        head_col = QVBoxLayout()
        head_col.setSpacing(2)
        self.course_title = QLabel("Selecione um curso")
        self.course_title.setObjectName("PanelTitle")
        self.course_meta = QLabel("Conecte-se ao Telegram e adicione seus grupos/canais.")
        self.course_meta.setObjectName("Muted")
        self.course_meta.setWordWrap(True)
        head_col.addWidget(self.course_title)
        head_col.addWidget(self.course_meta)
        header.addLayout(head_col, 1)

        self.sync_btn = QPushButton("⟳  Sincronizar")
        self.sync_btn.setObjectName("PrimaryButton")
        self.sync_btn.clicked.connect(self.sync_current_course)
        header.addWidget(self.sync_btn)
        layout.addLayout(header)

        # Busca + filtros de status.
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍  Buscar aula, hashtag ou módulo...")
        self.search_box.textChanged.connect(self.render_lessons)
        search_row.addWidget(self.search_box, 1)
        layout.addLayout(search_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        for key, label in (
            ("todas", "Todas"),
            ("assistidas", "Assistidas"),
            ("pendentes", "Pendentes"),
            ("favoritas", "★ Favoritas"),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("filter_key", key)
            if key == "todas":
                btn.setChecked(True)
            self.filter_group.addButton(btn)
            filter_row.addWidget(btn)
        self.filter_group.buttonClicked.connect(lambda _b: self.render_lessons())
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        # Árvore: módulo -> aula -> tipo -> vídeos.
        self.video_tree = QTreeWidget()
        self.video_tree.setHeaderLabels(["Aula / módulo", "Tipo", "Duração", "Status"])
        self.video_tree.itemSelectionChanged.connect(self.on_video_selected)
        self.video_tree.itemDoubleClicked.connect(self.on_tree_double_clicked)
        self.video_tree.setRootIsDecorated(True)
        self.video_tree.setExpandsOnDoubleClick(False)
        self.video_tree.setAnimated(True)
        self.video_tree.setIndentation(20)
        self.video_tree.setUniformRowHeights(False)
        self.video_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.video_tree.customContextMenuRequested.connect(self.show_video_menu)
        self.video_tree.setColumnWidth(0, 560)
        self.video_tree.setColumnWidth(1, 120)
        self.video_tree.setColumnWidth(2, 90)
        layout.addWidget(self.video_tree, 1)

        return center

    def _build_right(self) -> QWidget:
        right = QWidget()
        right.setObjectName("RightPane")
        right.setFixedWidth(360)
        layout = QVBoxLayout(right)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        card = QFrame()
        card.setObjectName("HeroCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
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

        card_layout.addSpacing(6)
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
        self.mark_btn.clicked.connect(self.toggle_watched_selected)
        row2.addWidget(self.edit_btn)
        row2.addWidget(self.mark_btn)
        card_layout.addLayout(row2)
        layout.addWidget(card)

        # Cartão "como funciona".
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
            "sob demanda. A aula começa na hora, você pula para qualquer ponto "
            "sem travar e o vídeo não fica salvo no computador."
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
        theme_action = QAction("Alternar tema claro/escuro", self)
        theme_action.triggered.connect(self.toggle_theme)
        tools.addAction(theme_action)
        vlc_action = QAction("Configurar VLC...", self)
        vlc_action.triggered.connect(self.choose_vlc)
        tools.addAction(vlc_action)
        net_action = QAction("Streaming & Rede...", self)
        net_action.triggered.connect(self.open_streaming_settings)
        tools.addAction(net_action)
        log_action = QAction("Abrir pasta de logs", self)
        log_action.triggered.connect(self.open_logs)
        tools.addAction(log_action)
        data_action = QAction("Abrir pasta de dados", self)
        data_action.triggered.connect(self.open_data_folder)
        tools.addAction(data_action)

        help_menu = menubar.addMenu("Ajuda")
        about = QAction("Sobre o TgPlayer", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    # ====================================================================== tema
    def apply_theme(self, theme: str, persist: bool = True) -> None:
        self.theme = "light" if theme == "light" else "dark"
        app = QApplication.instance()
        if app:
            app.setStyleSheet(build_qss(self.theme))
        self.theme_btn.setText("🌞" if self.theme == "dark" else "🌙")
        try:
            self.study_tab.apply_palette(palette(self.theme))
        except Exception:  # noqa: BLE001
            pass
        if persist:
            self.db.set_setting("theme", self.theme)
        # Reaplica cores de itens pintados manualmente.
        if self.current_course_id:
            self.render_lessons()

    def toggle_theme(self) -> None:
        self.apply_theme("light" if self.theme == "dark" else "dark")

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.study_tab:
            try:
                self.study_tab.refresh()
            except Exception:  # noqa: BLE001
                pass

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
                self._set_connected(result.get("me") or {})
        except Exception:  # noqa: BLE001
            log.exception("Autoconexão falhou")
            self.status_label.setText("Sessão não conectada")

    def _update_bandwidth_widget(self) -> None:
        """Mostra/atualiza a banda agregada das sessões ativas na barra superior."""
        try:
            if self.service.active_sessions() <= 0:
                if self.bandwidth_label.isVisible():
                    self.bandwidth_label.hide()
                return
            kbps = self.service.total_measured_kbps()
            if kbps >= 1000:
                text = f"⬇ {kbps / 1000:.1f} Mbps"
            else:
                text = f"⬇ {kbps:.0f} kbps"
            self.bandwidth_label.setText(text)
            if not self.bandwidth_label.isVisible():
                self.bandwidth_label.show()
        except Exception:  # noqa: BLE001
            pass

    def _set_connected(self, me: dict[str, Any]) -> None:
        name = me.get("first_name") or me.get("username") or "Conectado"
        self.status_label.setText(f"●  {name}")
        self.status_label.setObjectName("StatusConnected")
        self._repolish(self.status_label)
        self.login_btn.setText("Conectado ✓")

    def _repolish(self, widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def open_login(self) -> None:
        dlg = LoginDialog(self.service, self.db, self)
        if dlg.exec() == QDialog.Accepted:
            try:
                me = wait_future(
                    self.service.call(self.service.get_me()), "Telegram", "Carregando conta...", self
                )
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
        self._repolish(self.status_label)
        self.login_btn.setText("Conectar")

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
            done, total = self.db.course_progress(course.id)
            kind = "🗂️ Fórum" if course.is_forum else (
                "📢 Canal" if (course.chat_type or "").upper() == "CHANNEL" else "👥 Grupo"
            )
            if total:
                pct = int(done / total * 100)
                sub = f"{total} aulas · {done} assistidas · {pct}%"
            else:
                sub = "sem sincronizar"
            item = QListWidgetItem(f"{course.title}\n{kind} · {sub}")
            item.setData(Qt.UserRole, course.id)
            if course.color:
                item.setForeground(QColor(course.color))
            self.course_list.addItem(item)
            if selected_id and course.id == selected_id:
                self.course_list.setCurrentItem(item)
        self.course_list.blockSignals(False)
        if selected_id and self.current_course_id == selected_id:
            self.refresh_subjects()
            self.render_lessons()

    def on_course_selected(self, current: QListWidgetItem | None, _prev=None) -> None:
        if not current:
            self.current_course_id = None
            return
        self.current_course_id = int(current.data(Qt.UserRole))
        self.current_subject_id = SUBJECT_ALL
        self.refresh_subjects()
        self.render_lessons()

    def get_current_course(self) -> Course | None:
        return self.db.get_course(self.current_course_id) if self.current_course_id else None

    def show_course_menu(self, pos) -> None:
        item = self.course_list.itemAt(pos)
        if not item:
            return
        course_id = int(item.data(Qt.UserRole))
        menu = QMenu(self)
        rename = menu.addAction("Renomear curso")
        color = menu.addAction("Definir cor")
        sync = menu.addAction("Sincronizar")
        subjects = menu.addAction("Editar matérias/sumários")
        menu.addSeparator()
        up = menu.addAction("↑ Mover para cima")
        down = menu.addAction("↓ Mover para baixo")
        menu.addSeparator()
        delete = menu.addAction("Excluir curso")
        action = menu.exec(self.course_list.mapToGlobal(pos))
        if action == rename:
            self._rename_course(course_id)
        elif action == color:
            self._set_course_color(course_id)
        elif action == sync:
            self.current_course_id = course_id
            self.sync_current_course()
        elif action == subjects:
            self.current_course_id = course_id
            self.refresh_subjects()
            self.edit_subjects()
        elif action == up:
            self._move_course(course_id, -1)
        elif action == down:
            self._move_course(course_id, 1)
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
            if self.current_course_id == course_id:
                self.render_lessons()

    def _set_course_color(self, course_id: int) -> None:
        from PySide6.QtWidgets import QColorDialog
        course = self.db.get_course(course_id)
        initial = QColor(course.color) if course and course.color else QColor("#7c5cff")
        chosen = QColorDialog.getColor(initial, self, "Cor do curso")
        if chosen.isValid():
            self.db.set_course_color(course_id, chosen.name())
            self.refresh_courses()

    def _move_course(self, course_id: int, delta: int) -> None:
        ids = [c.id for c in self.db.list_courses()]
        if course_id not in ids:
            return
        idx = ids.index(course_id)
        new = idx + delta
        if 0 <= new < len(ids):
            ids[idx], ids[new] = ids[new], ids[idx]
            self.db.reorder_courses(ids)
            self.refresh_courses()

    def _delete_course(self, course_id: int) -> None:
        course = self.db.get_course(course_id)
        if not course:
            return
        if QMessageBox.question(
            self, "Excluir curso",
            f"Excluir “{course.title}” e todas as aulas/matérias indexadas?\n"
            "(O conteúdo no Telegram não é afetado.)",
        ) != QMessageBox.Yes:
            return
        self.db.delete_course(course_id)
        if self.current_course_id == course_id:
            self.current_course_id = None
            self.current_subject_id = SUBJECT_ALL
            self.subject_list.clear()
            self.video_tree.clear()
            self.course_title.setText("Selecione um curso")
            self.course_meta.setText("")
            self._update_topbar(None)
        self.refresh_courses()

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
                "Sincronizando", "Detectando o tipo do chat e lendo aulas/sumários...", self,
            )
            course_id = self.db.upsert_course(result["chat"])
            self.current_course_id = course_id
            self._apply_sync_result(course_id, result)
            self.refresh_courses()
            self.refresh_subjects()
            self.render_lessons()
            detected = {
                "forum": "Fórum (cada tópico virou uma matéria)",
                "channel": "Canal (lista linear de aulas)",
                "group": "Grupo (matéria única)",
            }.get(result.get("detected", ""), result.get("detected", ""))
            QMessageBox.information(
                self, "Sincronizado",
                f"Tipo detectado: {detected}.\n"
                f"{len(result.get('videos') or [])} vídeo(s) em "
                f"{result.get('scanned')} mensagens analisadas.",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao sincronizar curso")
            QMessageBox.critical(self, "Erro ao sincronizar", f"{exc}")

    def _apply_sync_result(self, course_id: int, result: dict[str, Any]) -> None:
        """Cria/atualiza matérias e liga cada vídeo à sua matéria, preservando edições."""
        # 1) Matérias (subjects). Mapa telegram_topic_id -> subject_id.
        topic_to_subject: dict[str, int] = {}
        for s in result.get("subjects") or []:
            tg_id = str(s.get("telegram_topic_id") or "")
            subject_id = self.db.find_or_create_subject(
                course_id,
                s.get("title") or "Matéria",
                telegram_topic_id=tg_id or None,
                summary_text=s.get("summary_text") or "",
                manual=0,
            )
            # Atualiza o sumário vindo do Telegram apenas se a matéria não tiver
            # sido editada manualmente pelo usuário.
            existing = self.db.get_subject(subject_id)
            if existing and not existing.manual and s.get("summary_text"):
                self.db.update_subject_summary(subject_id, s.get("summary_text"))
            topic_to_subject[tg_id] = subject_id

        # 2) Vídeos: resolve subject_id pelo telegram_topic_id de cada vídeo.
        videos = []
        for v in result.get("videos") or []:
            tg_id = str(v.get("telegram_topic_id") or "general")
            v = dict(v)
            v["subject_id"] = topic_to_subject.get(tg_id)
            videos.append(v)
        self.db.replace_videos(course_id, videos)

    # ================================================================ matérias
    def refresh_subjects(self) -> None:
        self.subject_list.blockSignals(True)
        self.subject_list.clear()
        course = self.get_current_course()
        if not course:
            self.subject_list.blockSignals(False)
            return
        subjects = self.db.list_subjects(course.id)
        videos = self.db.list_videos(course.id)

        all_item = QListWidgetItem(f"📚  Todas as matérias  ·  {len(videos)} aulas")
        all_item.setData(Qt.UserRole, SUBJECT_ALL)
        self.subject_list.addItem(all_item)

        # Filtro virtual "Continuar assistindo" (apenas se houver aulas em curso).
        in_progress = sum(1 for v in videos if 0.02 < (v.progress or 0) < 0.95)
        if in_progress:
            cont = QListWidgetItem(f"▶️  Continuar assistindo  ·  {in_progress}")
            cont.setData(Qt.UserRole, SUBJECT_CONTINUE)
            self.subject_list.addItem(cont)

        for subject in subjects:
            count = sum(1 for v in videos if v.subject_id == subject.id)
            watched = sum(1 for v in videos if v.subject_id == subject.id and v.watched_at)
            item = QListWidgetItem(f"{subject.title}\n{count} aulas · {watched} assistidas")
            item.setData(Qt.UserRole, subject.id)
            self.subject_list.addItem(item)

        no_subject = sum(1 for v in videos if not v.subject_id)
        if no_subject:
            item = QListWidgetItem(f"❔  Sem matéria  ·  {no_subject} aulas")
            item.setData(Qt.UserRole, SUBJECT_NONE)
            self.subject_list.addItem(item)

        # Restaura seleção.
        target = self.current_subject_id
        for i in range(self.subject_list.count()):
            if int(self.subject_list.item(i).data(Qt.UserRole)) == target:
                self.subject_list.setCurrentRow(i)
                break
        else:
            self.subject_list.setCurrentRow(0)
            self.current_subject_id = SUBJECT_ALL
        self.subject_list.blockSignals(False)

    def on_subject_selected(self, current: QListWidgetItem | None, _prev=None) -> None:
        if not current:
            return
        self.current_subject_id = int(current.data(Qt.UserRole))
        self.render_lessons()

    def show_subject_menu(self, pos) -> None:
        item = self.subject_list.itemAt(pos)
        course = self.get_current_course()
        if not course:
            return
        menu = QMenu(self)
        new = menu.addAction("+ Nova matéria")
        sid = int(item.data(Qt.UserRole)) if item else None
        rename = edit_summary = up = down = delete = None
        if sid and sid > 0:
            rename = menu.addAction("Renomear matéria")
            edit_summary = menu.addAction("Editar sumário")
            menu.addSeparator()
            up = menu.addAction("↑ Mover para cima")
            down = menu.addAction("↓ Mover para baixo")
            menu.addSeparator()
            delete = menu.addAction("Excluir matéria")
        action = menu.exec(self.subject_list.mapToGlobal(pos))
        if action == new:
            self._add_subject(course.id)
        elif action and action == rename:
            self._rename_subject(sid)
        elif action and action == edit_summary:
            self.edit_subjects()
        elif action and action == up:
            self._move_subject(course.id, sid, -1)
        elif action and action == down:
            self._move_subject(course.id, sid, 1)
        elif action and action == delete:
            self._delete_subject(sid)

    def _add_subject(self, course_id: int) -> None:
        title, ok = QInputDialog.getText(self, "Nova matéria", "Nome da matéria:")
        if ok and title.strip():
            self.db.add_subject(course_id, title.strip(), manual=1)
            self.refresh_subjects()

    def _rename_subject(self, subject_id: int) -> None:
        subject = self.db.get_subject(subject_id)
        if not subject:
            return
        title, ok = QInputDialog.getText(
            self, "Renomear matéria", "Novo nome:", text=subject.title
        )
        if ok and title.strip():
            self.db.rename_subject(subject_id, title.strip())
            self.refresh_subjects()
            self.render_lessons()

    def _move_subject(self, course_id: int, subject_id: int, delta: int) -> None:
        ids = [s.id for s in self.db.list_subjects(course_id)]
        if subject_id not in ids:
            return
        idx = ids.index(subject_id)
        new = idx + delta
        if 0 <= new < len(ids):
            ids[idx], ids[new] = ids[new], ids[idx]
            self.db.reorder_subjects(ids)
            self.refresh_subjects()

    def _delete_subject(self, subject_id: int) -> None:
        subject = self.db.get_subject(subject_id)
        if not subject:
            return
        if QMessageBox.question(
            self, "Excluir matéria",
            f"Excluir a matéria “{subject.title}”?\n"
            "As aulas dela ficam como “Sem matéria” (não são apagadas).",
        ) != QMessageBox.Yes:
            return
        self.db.delete_subject(subject_id)
        if self.current_subject_id == subject_id:
            self.current_subject_id = SUBJECT_ALL
        self.refresh_subjects()
        self.render_lessons()

    def edit_subjects(self) -> None:
        course = self.get_current_course()
        if not course:
            QMessageBox.information(self, "Curso", "Selecione um curso primeiro.")
            return
        subjects = self.db.list_subjects(course.id)
        dlg = SubjectsEditorDialog(subjects, self)
        if dlg.exec() != QDialog.Accepted:
            return
        edited, deleted_ids = dlg.result()
        for sid in deleted_ids:
            self.db.delete_subject(int(sid))
        order_ids: list[int] = []
        for s in edited:
            if s.get("id"):
                sid = int(s["id"])
                self.db.rename_subject(sid, s.get("title") or "Matéria")
                self.db.update_subject_summary(sid, s.get("summary_text") or "")
            else:
                sid = self.db.add_subject(
                    course.id, s.get("title") or "Matéria",
                    summary_text=s.get("summary_text") or "", manual=1,
                )
            order_ids.append(sid)
        if order_ids:
            self.db.reorder_subjects(order_ids)
        self.refresh_subjects()
        self.render_lessons()

    # ============================================================ render das aulas
    def _filter_key(self) -> str:
        btn = self.filter_group.checkedButton()
        return btn.property("filter_key") if btn else "todas"

    def _update_topbar(self, course: Course | None) -> None:
        if not course:
            self.topbar_title.setText("Selecione um curso")
            self.overall_progress.setValue(0)
            self.overall_meta.setText("0/0 aulas · 0h")
            return
        videos = self.db.list_videos(course.id)
        watched = sum(1 for v in videos if v.watched_at)
        total = len(videos)
        pct = int((watched / total) * 100) if total else 0
        total_seconds = sum(int(v.duration or 0) for v in videos)
        watched_seconds = sum(int(v.duration or 0) for v in videos if v.watched_at)
        subject_name = ""
        if self.current_subject_id and self.current_subject_id > 0:
            subj = self.db.get_subject(self.current_subject_id)
            if subj:
                subject_name = f"  ›  {subj.title}"
        self.topbar_title.setText(f"{course.title}{subject_name}")
        self.overall_progress.setValue(pct)
        self.overall_meta.setText(
            f"{watched}/{total} aulas · "
            f"{human_duration(watched_seconds)} / {human_duration(total_seconds)}"
        )

    def _video_passes(self, video: Video, query: str, filter_key: str) -> bool:
        if filter_key == "assistidas" and not video.watched_at:
            return False
        if filter_key == "pendentes" and video.watched_at:
            return False
        if filter_key == "favoritas" and not video.favorite:
            return False
        if query and not self._video_matches(video, query):
            return False
        return True

    def _video_matches(self, video: Video, query: str) -> bool:
        hay = " ".join(
            [
                video.title, video.file_name, video.caption or "",
                " ".join(video.hashtags), video.module or "", video.lesson or "",
                video.type or "", video.note or "",
            ]
        ).lower()
        return query in hay

    def render_lessons(self) -> None:
        course = self.get_current_course()
        self.video_tree.clear()
        self.current_video_id = None
        self._clear_detail()
        self._update_topbar(course)
        if not course:
            self.course_title.setText("Selecione um curso")
            self.course_meta.setText("Conecte-se ao Telegram e adicione seus grupos/canais.")
            return

        videos = self.db.list_videos(course.id)
        subjects = self.db.list_subjects(course.id)
        query = self.search_box.text().strip().lower()
        filter_key = self._filter_key()

        watched = sum(1 for v in videos if v.watched_at)
        self.course_title.setText(course.title)
        self.course_meta.setText(
            f"{len(videos)} aulas · {len(subjects)} matérias · {watched} assistidas · "
            f"último sync: {self._fmt_date(course.last_sync)}"
        )

        # Filtro virtual "Continuar assistindo": lista plana das aulas em curso,
        # ordenadas pelo acesso mais recente (resume).
        if self.current_subject_id == SUBJECT_CONTINUE:
            in_progress = [v for v in self.db.continue_watching(50)
                           if v.course_id == course.id]
            if query:
                in_progress = [v for v in in_progress if self._video_passes(v, query, "all")]
            if in_progress:
                parent = self._make_folder("▶️  Continuar assistindo", "matéria")
                for v in in_progress:
                    parent.addChild(self._video_item(v))
                self.video_tree.addTopLevelItem(parent)
                parent.setExpanded(True)
            else:
                self._show_empty_lessons("Nenhuma aula em andamento por aqui ainda.")
            return

        # Decide quais matérias mostrar conforme seleção da barra de matérias.
        if self.current_subject_id == SUBJECT_ALL:
            visible_subjects = subjects
            show_no_subject = True
        elif self.current_subject_id == SUBJECT_NONE:
            visible_subjects = []
            show_no_subject = True
        else:
            visible_subjects = [s for s in subjects if s.id == self.current_subject_id]
            show_no_subject = False

        has_any = False
        single_subject = len(visible_subjects) == 1 and not show_no_subject

        for subject in visible_subjects:
            subject_videos = [v for v in videos if v.subject_id == subject.id]
            added = self._render_subject(
                subject, subject_videos, query, filter_key, as_root=single_subject
            )
            has_any = has_any or added

        if show_no_subject:
            no_subject_videos = [v for v in videos if not v.subject_id]
            if no_subject_videos:
                filtered = [
                    v for v in no_subject_videos if self._video_passes(v, query, filter_key)
                ]
                if filtered:
                    parent = self._make_folder("❔  Sem matéria", "matéria")
                    for v in sorted(filtered, key=lambda x: x.message_id):
                        parent.addChild(self._video_item(v))
                    self.video_tree.addTopLevelItem(parent)
                    parent.setExpanded(True)
                    has_any = True

        if not has_any:
            placeholder = QTreeWidgetItem(
                ["Nenhuma aula encontrada", "", "",
                 "Clique em Sincronizar" if not videos else "Ajuste a busca/filtros"]
            )
            self.video_tree.addTopLevelItem(placeholder)
        self.video_tree.expandToDepth(0 if not single_subject else 2)

    def _show_empty_lessons(self, message: str) -> None:
        """Estado vazio amigável na lista de aulas (empty state)."""
        placeholder = QTreeWidgetItem([message, "", "", ""])
        self.video_tree.addTopLevelItem(placeholder)

    def _render_subject(
        self, subject: Subject, subject_videos: list[Video], query: str,
        filter_key: str, as_root: bool = False,
    ) -> bool:
        """Renderiza uma matéria: árvore módulo->aula->tipo + 'Sem módulo'."""
        result = match_videos_to_tree(subject.summary_text, subject_videos, subject.title)
        matched_ids = result.matched_ids

        # Índice tag -> vídeo (primeiro com a hashtag) para casar nós do sumário.
        tag_map: dict[str, Video] = {}
        for v in subject_videos:
            for tag in v.hashtags:
                tag_map.setdefault("#" + tag.lstrip("#").upper(), v)

        shown: set[int] = set()

        if as_root:
            subject_root = self.video_tree
        else:
            subject_root = self._make_folder(f"📘  {subject.title}", "matéria")

        any_visible = False
        for node in result.tree.children:
            if self._add_menu_node(subject_root, node, tag_map, shown, query, filter_key):
                any_visible = True

        # Vídeos da matéria que não casaram com o sumário -> "Sem módulo".
        leftover = [
            v for v in subject_videos
            if v.id not in shown and v.id not in matched_ids
            and self._video_passes(v, query, filter_key)
        ]
        # Também inclui vídeos casados por tag mas cujo nó não os exibiu (raro).
        leftover += [
            v for v in subject_videos
            if v.id not in shown and v.id in matched_ids
            and self._video_passes(v, query, filter_key)
        ]
        if leftover:
            folder = self._make_folder("Sem módulo", "módulo")
            for v in sorted(leftover, key=lambda x: x.message_id):
                folder.addChild(self._video_item(v))
                shown.add(v.id)
            if isinstance(subject_root, QTreeWidget):
                subject_root.addTopLevelItem(folder)
            else:
                subject_root.addChild(folder)
            any_visible = True

        if as_root:
            return any_visible

        if any_visible:
            self.video_tree.addTopLevelItem(subject_root)
            subject_root.setExpanded(True)
            return True
        return False

    def _add_menu_node(
        self, parent, node, tag_map: dict[str, Video], shown: set[int],
        query: str, filter_key: str,
    ) -> bool:
        item = self._make_folder(node.title, self._level_name(node.level))
        any_visible = False
        for child in node.children:
            if self._add_menu_node(item, child, tag_map, shown, query, filter_key):
                any_visible = True
        for tag in node.tags:
            video = tag_map.get("#" + tag.lstrip("#").upper())
            if video and video.id not in shown and self._video_passes(video, query, filter_key):
                item.addChild(self._video_item(video, prefix=tag))
                shown.add(video.id)
                any_visible = True
        if any_visible:
            if isinstance(parent, QTreeWidget):
                parent.addTopLevelItem(item)
            else:
                parent.addChild(item)
            return True
        return False

    @staticmethod
    def _level_name(level: int) -> str:
        return {1: "módulo", 2: "aula", 3: "tipo"}.get(level, "módulo")

    def _make_folder(self, title: str, kind: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title, "", "", kind])
        item.setData(0, ROLE_NODE_TYPE, "folder")
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setForeground(0, QColor(palette(self.theme)["text"]))
        item.setExpanded(False)
        return item

    @staticmethod
    def _video_meta_badge(video: Video) -> str:
        """VideoMetaBadge: resolução compacta (ex.: '1080p', '720p').

        Ideia portada do badge de metadados de vídeo do projeto de referência.
        Mostra a altura da fonte quando conhecida (gravada em moov_cache /
        importação). Sem altura, retorna string vazia.
        """
        height = getattr(video, "height", None)
        if not height:
            return ""
        h = int(height)
        if h >= 2160:
            return "4K"
        if h >= 1440:
            return "1440p"
        if h >= 1080:
            return "1080p"
        if h >= 720:
            return "720p"
        if h >= 480:
            return "480p"
        if h >= 360:
            return "360p"
        return f"{h}p"

    def _video_item(self, video: Video, prefix: str | None = None) -> QTreeWidgetItem:
        star = "★ " if video.favorite else ""
        check = "✅ " if video.watched_at else "⬜ "
        title = f"{check}{star}{prefix} — {video.title}" if prefix else f"{check}{star}{video.title}"
        if video.watched_at:
            status = "assistida"
        elif video.progress and video.progress > 0.02:
            status = f"▶ {int(video.progress * 100)}%"
        else:
            status = "pendente"
        # VideoMetaBadge: anexa a resolução à coluna de tipo quando conhecida.
        type_col = video.type or ""
        badge = self._video_meta_badge(video)
        if badge:
            type_col = f"{type_col} · {badge}" if type_col else badge
        item = QTreeWidgetItem(
            [title, type_col, human_duration(video.duration), status]
        )
        item.setData(0, ROLE_VIDEO_ID, video.id)
        if video.watched_at:
            item.setForeground(0, QColor(palette(self.theme)["muted"]))
        return item

    def _fmt_date(self, iso: str | None) -> str:
        if not iso:
            return "nunca"
        return iso.replace("T", " ")[:16]

    # ================================================== seleção / detalhe da aula
    def _clear_detail(self) -> None:
        self.video_title.setText("Nenhuma aula selecionada")
        self.video_info.setText("Clique em uma aula para ver os detalhes e assistir.")
        self.resume_bar.hide()
        self.fav_btn.setText("★ Favorito")
        self.mark_btn.setText("✓ Assistida")

    def on_tree_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
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
            self.video_title.setText(item.text(0))
            self.video_info.setText(
                f"Pasta/agrupamento · {item.childCount()} item(ns). "
                "Escolha uma aula para ver detalhes."
            )
            self.resume_bar.hide()
            return
        self.current_video_id = int(video_id)
        video = self.db.get_video(self.current_video_id)
        if not video:
            return
        self.video_title.setText(("★ " if video.favorite else "") + video.title)
        tags = " ".join(video.hashtags) or "sem hashtags"
        if video.watched_at:
            status = "Assistida"
        elif video.progress > 0.02:
            status = f"{int(video.progress * 100)}% assistido"
        else:
            status = "Não assistida"
        subject = self.db.get_subject(video.subject_id) if video.subject_id else None
        line1 = f"{human_size(video.size)} · {human_duration(video.duration)} · {status}"
        badge = self._video_meta_badge(video)
        if badge:
            line1 += f" · {badge}"
        parts = [
            line1,
            f"Matéria: {subject.title if subject else 'Sem matéria'}",
        ]
        if video.module:
            parts.append(f"Módulo: {video.module}")
        if video.type:
            parts.append(f"Tipo: {video.type}")
        parts.append(f"Tags: {tags}")
        if video.note:
            parts.append(f"Anotação: {video.note}")
        self.video_info.setText("\n".join(parts))
        if video.progress and 0.02 < video.progress < 0.95:
            self.resume_bar.setValue(int(video.progress * 100))
            self.resume_bar.show()
        else:
            self.resume_bar.hide()
        self.fav_btn.setText("☆ Remover favorito" if video.favorite else "★ Favorito")
        self.mark_btn.setText("↩ Não assistida" if video.watched_at else "✓ Assistida")

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
        mark = menu.addAction(
            "Marcar como NÃO assistida" if video.watched_at else "✓ Marcar como assistida"
        )
        copy = menu.addAction("Copiar link temporário")
        tg_link = menu.addAction("Copiar link do Telegram (t.me)")
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
            self.toggle_watched_selected()
        elif action == copy:
            self.copy_stream_url()
        elif action == tg_link:
            self.copy_telegram_link()
        elif action == delete:
            self.db.delete_video(video.id)
            self._refresh_after_change()

    def _refresh_after_change(self) -> None:
        self.render_lessons()
        self.refresh_courses()
        self.refresh_subjects()

    # =================================================================== edições
    def edit_selected_video(self) -> None:
        video = self.selected_video()
        if not video:
            return
        course = self.get_current_course()
        subjects = self.db.list_subjects(course.id) if course else []
        dlg = EditVideoDialog(video, subjects, self)
        if dlg.exec() != QDialog.Accepted:
            return
        values = dlg.values()
        self.db.rename_video(video.id, values["title"])
        self.db.set_video_subject(video.id, values["subject_id"])
        self.db.set_video_meta(
            video.id, values["module"], values["lesson"], values["type"]
        )
        self.db.set_video_hashtags(video.id, values["hashtags"])
        self.db.set_video_note(video.id, values["note"])
        self._refresh_after_change()
        self._reselect_video(video.id)

    def _reselect_video(self, video_id: int) -> None:
        self.current_video_id = video_id
        self.on_video_selected()

    def toggle_favorite_selected(self) -> None:
        video = self.selected_video()
        if not video:
            return
        self.db.toggle_favorite(video.id)
        self.render_lessons()
        self._reselect_video(video.id)

    def toggle_watched_selected(self) -> None:
        video = self.selected_video()
        if not video:
            return
        if video.watched_at:
            self.db.mark_unwatched(video.id)
        else:
            self.db.mark_watched(video.id)
        self._refresh_after_change()
        self._reselect_video(video.id)

    # =================================================================== player
    def _stream_payload(self, video: Video) -> dict[str, Any]:
        return {
            "chat_id": video.chat_id,
            "message_id": video.message_id,
            "title": video.title,
            "file_name": video.file_name,
            "mime_type": video.mime_type,
            "size": video.size,
            "start_position_ms": video.position_ms,
        }

    def _prepare_stream(self, video: Video) -> dict[str, Any] | None:
        result = wait_future(
            self.service.call(self.service.prepare_stream(self._stream_payload(video))),
            "Streaming", "Preparando o link local para o player...", self,
        )
        return result if isinstance(result, dict) else None

    def _playlist_for(self, video: Video) -> tuple[list[Video], int]:
        """Lista de aulas (ordem de exibição) para navegação ‹ › e auto-play.

        Usa as aulas da MESMA matéria (se houver) ou do curso, na ordem em que
        aparecem na biblioteca. Retorna (lista, índice da aula atual).
        """
        try:
            if video.subject_id:
                videos = self.db.list_videos_for_subject(video.subject_id)
            else:
                videos = self.db.list_videos(video.course_id)
        except Exception:  # noqa: BLE001
            videos = [video]
        if not videos:
            videos = [video]
        index = next((i for i, v in enumerate(videos) if v.id == video.id), 0)
        return videos, index

    def _prefetch_video_meta(self, video: Video) -> None:
        """Agenda a pré-busca de metadados/miniatura no loop de 2º plano.

        Fire-and-forget: usa `service.call` para rodar as corrotinas no loop do
        Telegram sem bloquear a UI. Os dados são persistidos em moov_cache e na
        tabela `videos`, refletindo na próxima atualização das listas.
        """
        try:
            payload = {
                "chat_id": video.chat_id,
                "message_id": video.message_id,
                "duration": video.duration,
                "size": video.size,
            }
            self.service.call(self.service.fetch_video_metadata(payload))
            self.service.call(self.service.ensure_thumbnail(payload))
        except Exception:  # noqa: BLE001
            pass

    def watch_selected_internal(self) -> None:
        video = self.selected_video()
        if not video:
            return
        self._open_player_for(video)

    def _open_player_for(self, video: Video) -> None:
        """Abre o player premium para `video`, com navegação/qualidade/debug."""
        try:
            stream = self._prepare_stream(video)
            if not stream:
                return
            video_id = video.id
            playlist, index = self._playlist_for(video)
            # Pré-busca de metadados (resolução/duração) e miniatura em 2º plano.
            if not (video.width and video.height):
                self._prefetch_video_meta(video)

            def save_progress(position_ms: int, duration_ms: int) -> None:
                try:
                    self.db.save_progress(video_id, position_ms, duration_ms)
                except Exception:  # noqa: BLE001
                    pass

            def open_vlc() -> None:
                self._launch_vlc(stream.get("stream_url") or stream.get("url"))

            def quality_change(quality: str, adaptive: bool) -> None:
                try:
                    self.service.set_quality(quality, adaptive)
                    self.db.set_setting("streaming_quality", quality)
                    self.db.set_setting("adaptive_mode", "1" if adaptive else "0")
                except Exception:  # noqa: BLE001
                    pass

            def clear_cache() -> None:
                try:
                    self.db.clear_moov_cache(video.chat_id, video.message_id)
                except Exception:  # noqa: BLE001
                    pass

            def rate_change(rate: float) -> None:
                try:
                    self.db.set_setting(
                        f"course_rate_{video.course_id}", f"{rate:g}"
                    )
                except Exception:  # noqa: BLE001
                    pass

            # Próxima/anterior: agenda a abertura da aula vizinha após fechar.
            self._pending_navigation = None

            def go_to(idx: int) -> None:
                if 0 <= idx < len(playlist):
                    self._pending_navigation = playlist[idx].id

            on_next = (lambda: go_to(index + 1)) if index < len(playlist) - 1 else None
            on_prev = (lambda: go_to(index - 1)) if index > 0 else None

            saved_rate = self.db.get_setting(f"course_rate_{video.course_id}") or "1"
            try:
                playback_rate = float(saved_rate)
            except Exception:  # noqa: BLE001
                playback_rate = 1.0

            try:
                dlg = VideoPlayerDialog(
                    video.title,
                    stream.get("stream_url") or stream.get("url"),
                    stream.get("token"),
                    self.service,
                    start_position_ms=video.position_ms,
                    on_progress=save_progress,
                    on_open_vlc=open_vlc,
                    player_url=stream.get("player_url"),
                    on_next=on_next,
                    on_prev=on_prev,
                    current_index=index,
                    total_items=len(playlist),
                    source_width=video.width,
                    source_height=video.height,
                    initial_quality=self.db.get_setting("streaming_quality") or "original",
                    adaptive_mode=(self.db.get_setting("adaptive_mode") or "0") == "1",
                    on_quality_change=quality_change,
                    on_clear_cache=clear_cache,
                    debug_overlay=(self.db.get_setting("debug_overlay") or "0") == "1",
                    playback_rate=playback_rate,
                    on_rate_change=rate_change,
                    parent=self,
                )
            except Exception as exc:  # noqa: BLE001
                if QMessageBox.question(
                    self, "Player indisponível",
                    f"Não consegui abrir o player interno ({exc}). Deseja tentar o VLC?",
                ) == QMessageBox.Yes:
                    self.watch_selected_vlc()
                return
            dlg.exec()
            self._refresh_after_change()
            self._reselect_video(video_id)
            # Se o usuário pediu próxima/anterior, abre a aula vizinha.
            nav_id = getattr(self, "_pending_navigation", None)
            if nav_id:
                self._pending_navigation = None
                nxt = self.db.get_video(nav_id)
                if nxt:
                    self.current_video_id = nxt.id
                    QTimer.singleShot(50, lambda: self._open_player_for(nxt))
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao assistir no player interno")
            QMessageBox.critical(self, "Erro no streaming", f"{exc}")

    def watch_selected_vlc(self) -> None:
        video = self.selected_video()
        if not video:
            return
        try:
            stream = self._prepare_stream(video)
            if not stream:
                return
            self._launch_vlc(stream.get("stream_url") or stream.get("url"))
            self.db.mark_watched(video.id)
            self._refresh_after_change()
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao assistir no VLC")
            QMessageBox.critical(self, "Erro no streaming", f"{exc}")

    def _launch_vlc(self, url: str | None) -> None:
        if not url:
            return
        vlc_path = find_vlc(self.db.get_setting("vlc_path"))
        if not vlc_path:
            if QMessageBox.question(
                self, "VLC não encontrado",
                "Não encontrei o VLC. Deseja abrir a página oficial para instalar?",
            ) == QMessageBox.Yes:
                open_vlc_download_page()
            return
        launch_vlc(vlc_path, url)

    def copy_stream_url(self) -> None:
        video = self.selected_video()
        if not video:
            return
        try:
            stream = self._prepare_stream(video)
            url = (stream.get("stream_url") or stream.get("url")) if stream else None
            if url:
                QApplication.clipboard().setText(url)
                QMessageBox.information(
                    self, "Link copiado",
                    "Link temporário copiado. Ele só funciona enquanto o app estiver aberto.",
                )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Erro", str(exc))

    def copy_telegram_link(self) -> None:
        """Copia o link nativo t.me da aula (somente canais/grupos PÚBLICOS).

        Ideia portada do recurso de 'copiar link nativo do Telegram' do projeto
        de referência. Requer que o curso tenha `username` (canal público).
        """
        video = self.selected_video()
        if not video:
            return
        course = self.db.get_course(video.course_id) if video.course_id else None
        username = course.username if course else None
        link = self.service.telegram_message_link(username, video.message_id)
        if not link:
            QMessageBox.information(
                self, "Link do Telegram",
                "Esta aula está em um canal/grupo PRIVADO (sem nome de usuário "
                "público), então não há um link t.me nativo. Use 'Copiar link "
                "temporário' para reproduzir enquanto o app estiver aberto.",
            )
            return
        QApplication.clipboard().setText(link)
        QMessageBox.information(
            self, "Link copiado",
            f"Link nativo do Telegram copiado:\n{link}",
        )

    # ============================================================== ferramentas
    def choose_vlc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar vlc.exe", str(Path.home()),
            "VLC (vlc.exe);;Executável (*.exe);;Todos (*.*)",
        )
        if path:
            self.db.set_setting("vlc_path", path)
            QMessageBox.information(self, "VLC", "Caminho do VLC salvo.")

    def open_streaming_settings(self) -> None:
        dlg = StreamingSettingsDialog(self.db, self)
        if dlg.exec() == QDialog.Accepted:
            quality, adaptive = dlg.result_quality()
            try:
                self.service.set_quality(quality, adaptive)
            except Exception:  # noqa: BLE001
                pass
            QMessageBox.information(
                self, "Streaming & Rede",
                "Configurações salvas. Ajustes de proxy entram em vigor na "
                "próxima conexão.",
            )

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
            f"TgPlayer v{__version__}\n\n"
            "Organize e assista às videoaulas dos seus cursos no Telegram, "
            "com player premium, streaming sob demanda (sem armazenar os vídeos) "
            "e módulo de acompanhamento de estudos.\n\n"
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


def _configure_rendering() -> None:
    """Evita "caixas pretas" sobre o texto/widgets em alguns PCs Windows.

    Em muitas placas de vídeo/drivers (e em builds empacotados com PyInstaller),
    a composição por GPU do Qt/QtWebEngine desenha retângulos pretos por cima de
    widgets. Forçar a composição por software e compartilhar o contexto OpenGL
    resolve o problema de forma confiável, com impacto mínimo de desempenho.
    """
    import os

    from PySide6.QtCore import QCoreApplication

    # Flags do Chromium do QtWebEngine: desliga GPU (causa das caixas pretas).
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    extra = "--disable-gpu --disable-gpu-compositing --disable-software-rasterizer"
    if extra not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " " + extra).strip()

    try:
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    except Exception:  # noqa: BLE001
        pass
    try:
        # Renderização por software do próprio Qt (corrige artefatos de overlay).
        QCoreApplication.setAttribute(Qt.AA_UseSoftwareOpenGL, True)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    setup_logging()
    sys.excepthook = excepthook
    _configure_rendering()
    app = QApplication(sys.argv)
    app.setApplicationName("TgPlayer")
    try:
        app.setFont(QFont("Segoe UI", 10))
    except Exception:  # noqa: BLE001
        pass
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
