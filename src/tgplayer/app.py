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

import ctypes
import ctypes.wintypes
import logging
import re
import sys
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl, QSize, QPoint
from PySide6.QtGui import QAction, QColor, QFont, QDesktopServices, QIcon
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
    QAbstractItemView,
    QVBoxLayout,
    QWidget,
    QSizeGrip,
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
from .files_tab import FilesTab
from .logging_setup import setup_logging
from .player import VideoPlayerDialog, is_webengine_available
from .study_tab import StudyTab
from .style import build_qss, palette
from .summary_parser import match_videos_to_tree
from .telegram_service import SessionRevokedError, TelegramService
from .utils import human_duration, human_size
from .vlc_locator import find_vlc, launch_vlc, open_vlc_download_page
from .paths import DATA_DIR, SESSION_DIR

log = logging.getLogger(__name__)

# Marcadores que identificam, na mensagem do erro, uma sessão inválida no
# servidor do Telegram (auth_key revogada/expirada). Usado como rede de
# segurança caso o serviço levante a exceção crua em vez de SessionRevokedError.
_SESSION_ERROR_MARKERS = (
    "AUTH_KEY_UNREGISTERED",
    "AUTH_KEY_INVALID",
    "AUTH_KEY_DUPLICATED",
    "SESSION_REVOKED",
    "SESSION_EXPIRED",
    "USER_DEACTIVATED",
    "UNAUTHORIZED",
    "401",
)


def _looks_like_session_error(exc: BaseException) -> bool:
    if isinstance(exc, SessionRevokedError):
        return True
    text = f"{type(exc).__name__}: {exc}".upper()
    return any(marker in text for marker in _SESSION_ERROR_MARKERS)


ROLE_VIDEO_ID = Qt.UserRole
ROLE_NODE_TYPE = Qt.UserRole + 1
ROLE_RAW_TITLE = Qt.UserRole + 2

# Identificador especial para "todas as matérias".
SUBJECT_ALL = -1
SUBJECT_NONE = 0  # vídeos sem matéria
SUBJECT_CONTINUE = -2  # filtro virtual "Continuar assistindo" (resume)


class DraggableTopBar(QWidget):
    """Barra superior arrastável para janela sem moldura nativa.

    Importante: a barra NÃO pode capturar cliques feitos em botões/menus.
    Versões anteriores aceitavam o mousePressEvent no widget inteiro e, junto
    com o WM_NCHITTEST, isso fazia Conectar/minimizar/maximizar/fechar ficarem
    com aparência normal, mas sem clique real. A regra agora é: arrastar só em
    área vazia da barra.
    """

    def __init__(self, window: "MainWindow") -> None:
        super().__init__()
        self.window = window
        self._drag_pos: QPoint | None = None

    def _over_interactive(self, event) -> bool:  # noqa: ANN001
        try:
            global_pos = event.globalPosition().toPoint()
            return bool(self.window._is_global_point_on_titlebar_control(global_pos))
        except Exception:  # noqa: BLE001
            return False

    def mousePressEvent(self, event):  # noqa: ANN001
        if event.button() == Qt.LeftButton and not self._over_interactive(event):
            try:
                self._drag_pos = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
                event.accept()
                return
            except Exception:  # noqa: BLE001
                self._drag_pos = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: ANN001
        if event.buttons() & Qt.LeftButton and self._drag_pos is not None:
            try:
                if self.window.isMaximized():
                    old_width = max(1, self.window.width())
                    ratio = max(0.15, min(0.85, event.position().x() / old_width))
                    self.window.showNormal()
                    self.window._update_window_buttons()
                    self._drag_pos = QPoint(int(self.window.width() * ratio), 26)
                self.window.move(event.globalPosition().toPoint() - self._drag_pos)
                event.accept()
                return
            except Exception:  # noqa: BLE001
                pass
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: ANN001
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: ANN001
        if event.button() == Qt.LeftButton and not self._over_interactive(event):
            self.window.toggle_maximize_restore()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


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
        self._active_player: VideoPlayerDialog | None = None
        self.setWindowTitle("TgPlayer — Videoaulas do Telegram")
        # v6.4.14: voltamos à moldura nativa do Windows para garantir cliques,
        # redimensionamento, Snap Assist e botões minimizar/maximizar/fechar.
        # A tentativa de barra 100% customizada interceptava cliques em alguns
        # ambientes Windows/PyInstaller. A barra visual do TgPlayer fica dentro
        # do app, mas os controles reais da janela são nativos e estáveis.
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowCloseButtonHint
        )
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

        # Warm-up (pré-busca) ao SELECIONAR uma aula: aquece o início + monta o
        # cabeçalho faststart em 2º plano, com debounce de ~400 ms para não
        # disparar a cada movimento de seleção. Quando o usuário clicar
        # "Assistir", a partida já está quente → abertura quase instantânea.
        self._warmup_timer = QTimer(self)
        self._warmup_timer.setSingleShot(True)
        self._warmup_timer.setInterval(400)
        self._warmup_timer.timeout.connect(self._do_warmup)
        self._warmup_video_id: int | None = None

    def toggle_maximize_restore(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_window_buttons()

    def _update_window_buttons(self) -> None:
        btn = getattr(self, "max_btn", None)
        if btn is not None:
            btn.setText("❐" if self.isMaximized() else "□")
            btn.setToolTip("Restaurar" if self.isMaximized() else "Maximizar")

    def _titlebar_controls(self) -> list[QWidget]:
        """Widgets do topo que devem receber clique normal.

        O Windows pergunta primeiro via WM_NCHITTEST se aquela região é
        HTCAPTION/resize. Se respondermos HTCAPTION em cima de um botão, o Qt
        nunca recebe o clique. Por isso calculamos retângulos globais dos
        controles interativos em vez de confiar apenas em childAt().
        """
        names = (
            "login_btn", "theme_btn", "min_btn", "max_btn", "close_btn",
            "account_btn", "tools_btn", "help_btn",
        )
        widgets: list[QWidget] = []
        for name in names:
            widget = getattr(self, name, None)
            if isinstance(widget, QWidget) and widget.isVisible() and widget.isEnabled():
                widgets.append(widget)
        return widgets

    def _is_global_point_on_titlebar_control(self, global_pos: QPoint) -> bool:
        for widget in self._titlebar_controls():
            try:
                top_left = widget.mapToGlobal(QPoint(0, 0))
                rect = widget.rect().translated(top_left)
                # Margem pequena para compensar DPI/escala do Windows.
                rect = rect.adjusted(-3, -3, 3, 3)
                if rect.contains(global_pos):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def changeEvent(self, event):  # noqa: ANN001
        super().changeEvent(event)
        self._update_window_buttons()


    def nativeEvent(self, eventType, message):  # noqa: ANN001, N802
        """Não intercepta eventos nativos da janela.

        A v6.4.14 usa a barra nativa do Windows. Isso devolve ao sistema o
        controle de redimensionamento, Snap Assist e botões da janela, evitando
        que a região superior do app capture cliques em Conectar/tema/menus.
        """
        return super().nativeEvent(eventType, message)

    def showEvent(self, event):  # noqa: ANN001, N802
        super().showEvent(event)
        self._update_window_buttons()

    # ===================================================== construção da interface
    def build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._build_menu()
        root.addWidget(self._build_topbar())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_lessons_tab(), "🎬  Aulas")
        self.study_tab = StudyTab(self.db, self.get_current_course)
        self.tabs.addTab(self.study_tab, "📊  Acompanhamento")
        self.files_tab = FilesTab(self.db, self.service, self.get_current_course)
        self.files_tab.play_video_requested.connect(self._play_media_video)
        self.tabs.addTab(self.files_tab, "🗂️  Arquivos")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs, 1)

        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 6, 6)
        grip_row.addStretch(1)
        grip_row.addWidget(QSizeGrip(self), 0, Qt.AlignRight | Qt.AlignBottom)
        root.addLayout(grip_row)

        self._update_action_buttons_state(False)

    # ---------------------------------------------------------------- barra topo
    def _menu_button(self, text: str, menu: QMenu) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("TopMenuButton")
        btn.setMenu(menu)
        btn.setMinimumHeight(30)
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _build_topbar(self) -> QWidget:
        """Barra superior compacta.

        A barra deve se comportar como uma barra de título do app: logo, menus,
        estado de conexão e controles da janela. O nome do curso e o progresso
        ficam no cabeçalho da aba Aulas, evitando o visual espremido do topo.
        """
        # Barra visual interna; não é mais uma titlebar customizada.
        # Assim os botões e menus recebem clique normal em todos os Windows.
        bar = QWidget()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(52)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 8, 12, 8)
        layout.setSpacing(10)

        brand_box = QHBoxLayout()
        brand_box.setSpacing(0)
        brand = QLabel("Tg")
        brand.setObjectName("Brand")
        brand_accent = QLabel("Player")
        brand_accent.setObjectName("BrandAccent")
        brand_box.addWidget(brand)
        brand_box.addWidget(brand_accent)
        layout.addLayout(brand_box)

        self.account_btn = self._menu_button("Conta", self.account_menu)
        self.tools_btn = self._menu_button("Ferramentas", self.tools_menu)
        self.help_btn = self._menu_button("Ajuda", self.help_menu)
        layout.addWidget(self.account_btn)
        layout.addWidget(self.tools_btn)
        layout.addWidget(self.help_btn)

        # Área vazia arrastável. Não colocar título/progresso aqui.
        layout.addStretch(1)

        self.bandwidth_label = QLabel("")
        self.bandwidth_label.setObjectName("Muted2")
        self.bandwidth_label.setToolTip("Banda agregada das sessões de streaming ativas")
        self.bandwidth_label.hide()
        layout.addWidget(self.bandwidth_label)

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

        # Os botões de minimizar/maximizar/fechar ficam na moldura nativa do
        # Windows. Não criamos duplicatas aqui para não interceptar cliques.
        self.min_btn = None
        self.max_btn = None
        self.close_btn = None

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
        self.course_title.setObjectName("PageTitle")
        # topbar_title é mantido como alias para a lógica existente de progresso.
        self.topbar_title = self.course_title
        self.course_meta = QLabel("Conecte-se ao Telegram e adicione seus grupos/canais.")
        self.course_meta.setObjectName("Muted")
        self.course_meta.setWordWrap(True)
        head_col.addWidget(self.course_title)
        head_col.addWidget(self.course_meta)

        prog_row = QHBoxLayout()
        prog_row.setSpacing(10)
        self.overall_progress = QProgressBar()
        self.overall_progress.setTextVisible(True)
        self.overall_progress.setFormat("%p% concluído")
        self.overall_progress.setValue(0)
        self.overall_progress.setFixedHeight(14)
        prog_row.addWidget(self.overall_progress, 1)
        self.overall_meta = QLabel("0/0 aulas · 0h")
        self.overall_meta.setObjectName("Muted2")
        prog_row.addWidget(self.overall_meta)
        head_col.addLayout(prog_row)

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
        self.video_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.video_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.video_tree.setAllColumnsShowFocus(False)
        self.video_tree.setFocusPolicy(Qt.NoFocus)
        self.video_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.video_tree.setAlternatingRowColors(False)
        self.video_tree.itemSelectionChanged.connect(self.on_video_selected)
        self.video_tree.itemClicked.connect(self.on_tree_clicked)
        self.video_tree.itemDoubleClicked.connect(self.on_tree_double_clicked)
        self.video_tree.itemExpanded.connect(lambda item: self._refresh_folder_label(item))
        self.video_tree.itemCollapsed.connect(lambda item: self._refresh_folder_label(item))
        # Usamos setas textuais (▸/▾) dentro do rótulo. A decoração nativa do Qt
        # fica desligada para evitar a faixa azul/roxa separada na margem esquerda.
        self.video_tree.setRootIsDecorated(False)
        self.video_tree.setExpandsOnDoubleClick(False)
        self.video_tree.setAnimated(True)
        self.video_tree.setIndentation(24)
        self.video_tree.setUniformRowHeights(False)
        self.video_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.video_tree.customContextMenuRequested.connect(self.show_video_menu)
        self.video_tree.setColumnWidth(0, 620)
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
        self.watch_btn = QPushButton("▶  Assistir aqui")
        self.watch_btn.setObjectName("PrimaryButton")
        self.watch_btn.setToolTip(
            "Abre o player embutido (rápido) dentro do TgPlayer. O vídeo começa "
            "em poucos segundos, sem precisar abrir o VLC."
        )
        self.watch_btn.clicked.connect(self.watch_selected_internal)
        card_layout.addWidget(self.watch_btn)

        row_open = QHBoxLayout()
        self.watch_tg_btn = QPushButton("📲 Telegram")
        self.watch_tg_btn.setToolTip(
            "Abre a mensagem original no Telegram Desktop/64Gram/Nekogram."
        )
        self.watch_tg_btn.clicked.connect(self.open_selected_in_telegram)
        self.watch_vlc_btn = QPushButton("Abrir no VLC")
        self.watch_vlc_btn.clicked.connect(self.watch_selected_vlc)
        row_open.addWidget(self.watch_tg_btn)
        row_open.addWidget(self.watch_vlc_btn)
        card_layout.addLayout(row_open)

        row1 = QHBoxLayout()
        self.resume_point_btn = QPushButton("⏱ Salvar ponto")
        self.resume_point_btn.setToolTip("Salva manualmente o minuto em que você parou.")
        self.resume_point_btn.clicked.connect(self.save_resume_point_selected)
        row1.addWidget(self.resume_point_btn)
        card_layout.addLayout(row1)

        row1b = QHBoxLayout()
        self.fav_btn = QPushButton("★ Favorito")
        self.fav_btn.clicked.connect(self.toggle_favorite_selected)
        row1b.addWidget(self.fav_btn)
        card_layout.addLayout(row1b)

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
            "O botão principal 'Assistir aqui' abre o player embutido, que começa o "
            "vídeo em poucos segundos (streaming rápido com faststart). O progresso "
            "é salvo automaticamente. 'Telegram' e 'Abrir no VLC' continuam como "
            "alternativas."
        )
        help_text.setObjectName("Muted")
        help_text.setWordWrap(True)
        h_layout.addWidget(help_text)
        layout.addWidget(help_card)

        layout.addStretch(1)
        return right

    def _build_menu(self) -> None:
        """Cria menus internos usados pelos botões da barra superior.

        Não usamos mais QMenuBar nativo aqui, porque ele criava uma faixa separada
        acima da interface em janelas sem moldura.
        """
        self.account_menu = QMenu("Conta", self)
        relogin = QAction("Entrar / trocar conta", self)
        relogin.triggered.connect(self.open_login)
        self.account_menu.addAction(relogin)
        logout = QAction("Sair da conta (logout)", self)
        logout.triggered.connect(self.do_logout)
        self.account_menu.addAction(logout)
        clear_private = QAction("Limpar credenciais locais", self)
        clear_private.triggered.connect(self.clear_private_data)
        self.account_menu.addAction(clear_private)

        self.tools_menu = QMenu("Ferramentas", self)
        theme_action = QAction("Alternar tema claro/escuro", self)
        theme_action.triggered.connect(self.toggle_theme)
        self.tools_menu.addAction(theme_action)
        vlc_action = QAction("Configurar VLC...", self)
        vlc_action.triggered.connect(self.choose_vlc)
        self.tools_menu.addAction(vlc_action)
        net_action = QAction("Streaming & Rede...", self)
        net_action.triggered.connect(self.open_streaming_settings)
        self.tools_menu.addAction(net_action)
        log_action = QAction("Abrir pasta de logs", self)
        log_action.triggered.connect(self.open_logs)
        self.tools_menu.addAction(log_action)
        data_action = QAction("Abrir pasta de dados", self)
        data_action.triggered.connect(self.open_data_folder)
        self.tools_menu.addAction(data_action)

        self.help_menu = QMenu("Ajuda", self)
        about = QAction("Sobre o TgPlayer", self)
        about.triggered.connect(self.show_about)
        self.help_menu.addAction(about)

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
        widget = self.tabs.widget(index)
        if widget is self.study_tab:
            try:
                self.study_tab.refresh()
            except Exception:  # noqa: BLE001
                pass
        elif widget is getattr(self, "files_tab", None):
            try:
                self.files_tab.reload_chats()
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
            elif result.get("session_revoked"):
                # Sessão antiga foi invalidada no servidor; o arquivo local já
                # foi apagado pelo serviço. Avisamos sem assustar e oferecemos
                # reconexão imediata.
                self._set_disconnected("Sessão expirada — entre novamente")
                self._notify_session_revoked()
            else:
                self._set_disconnected("Sessão não conectada")
        except Exception:  # noqa: BLE001
            log.exception("Autoconexão falhou")
            self._set_disconnected("Sessão não conectada")

    def _set_disconnected(self, text: str = "Não conectado") -> None:
        self.status_label.setText(text)
        self.status_label.setObjectName("StatusLabel")
        self._repolish(self.status_label)
        self.login_btn.setText("Conectar")

    def _notify_session_revoked(self) -> None:
        """Avisa que a sessão expirou e abre o login se o usuário quiser."""
        resp = QMessageBox.question(
            self,
            "Sessão do Telegram expirou",
            "Sua sessão do Telegram expirou ou foi encerrada em outro "
            "dispositivo, então ela foi removida deste computador.\n\n"
            "Deseja entrar novamente agora?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if resp == QMessageBox.Yes:
            self.open_login()

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


    def clear_private_data(self) -> None:
        """Remove credenciais e sessão salvas somente deste computador."""
        if QMessageBox.question(
            self,
            "Limpar credenciais locais",
            "Isso apagará API ID, API HASH, telefone e arquivos de sessão salvos "
            "neste computador. Cursos/progresso continuam no banco local.\n\n"
            "Depois será necessário fazer login novamente. Deseja continuar?",
        ) != QMessageBox.Yes:
            return
        try:
            for key in ("api_id", "api_hash", "phone_number", "auth_flood_wait_until"):
                self.db.set_setting(key, "")
            for path in SESSION_DIR.glob("*"):
                try:
                    if path.is_file():
                        path.unlink()
                except Exception:  # noqa: BLE001
                    pass
            QMessageBox.information(
                self,
                "Credenciais removidas",
                f"Credenciais e sessões locais apagadas.\n\nPasta de dados deste computador:\n{DATA_DIR}",
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Erro", f"Não foi possível limpar tudo: {exc}")
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
        except SessionRevokedError:
            # Sessão revogada no servidor (ex.: AUTH_KEY_UNREGISTERED). O serviço
            # já apagou o arquivo de sessão; aqui só precisamos reautenticar.
            log.warning("Sessão revogada ao listar cursos; pedindo novo login.")
            self._set_disconnected("Sessão expirada — entre novamente")
            self._notify_session_revoked()
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao adicionar cursos")
            # Mesmo que o serviço não tenha classificado como SessionRevokedError,
            # detectamos a assinatura clássica do erro e oferecemos reconexão.
            if _looks_like_session_error(exc):
                self._set_disconnected("Sessão expirada — entre novamente")
                self._notify_session_revoked()
                return
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
        except SessionRevokedError:
            log.warning("Sessão revogada ao sincronizar; pedindo novo login.")
            self._set_disconnected("Sessão expirada — entre novamente")
            self._notify_session_revoked()
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao sincronizar curso")
            if _looks_like_session_error(exc):
                self._set_disconnected("Sessão expirada — entre novamente")
                self._notify_session_revoked()
                return
            QMessageBox.critical(self, "Erro ao sincronizar", f"{exc}")

    def _apply_sync_result(self, course_id: int, result: dict[str, Any]) -> None:
        """Cria/atualiza matérias e liga cada vídeo à sua matéria, preservando edições."""
        # 1) Matérias (subjects). Mapa telegram_topic_id -> subject_id.
        topic_to_subject: dict[str, int] = {}
        ordered_subject_ids: list[int] = []
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
            if existing and not existing.manual:
                new_title = s.get("title") or "Matéria"
                if new_title and new_title != existing.title:
                    self.db.rename_subject(subject_id, new_title)
                if s.get("summary_text"):
                    self.db.update_subject_summary(subject_id, s.get("summary_text"))
            topic_to_subject[tg_id] = subject_id
            ordered_subject_ids.append(subject_id)

        # Mantém a ordem detectada no Telegram (tópicos/sumários) também quando
        # a matéria já existia no banco. Sem isso, ressincronizar não corrigia a
        # ordem antiga e o sumário parecia continuar bagunçado.
        if ordered_subject_ids:
            try:
                self.db.reorder_subjects(ordered_subject_ids)
            except Exception:  # noqa: BLE001
                pass

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
        self._refresh_tree_folder_labels()
        self._update_action_buttons_state(False)

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
        # result.matched_ids fica disponível para diagnóstico futuro; a renderização
        # usa um mapeamento mais robusto abaixo.

        # Índice tag do SUMÁRIO -> vídeos. Quando o Telegram/vidsender coloca a
        # mesma hashtag em várias aulas (ex.: tudo como #CAR01), o casamento por
        # hashtag deixa o sumário bagunçado. Por isso esta função detecta baixa
        # cobertura/duplicação e distribui as aulas pela ORDEM do sumário.
        tag_map: dict[str, list[Video]] = self._assign_videos_to_summary_tags(
            result.tree, subject_videos
        )

        shown: set[int] = set()

        if as_root:
            subject_root = self.video_tree
        else:
            subject_root = self._make_folder(f"📘  {subject.title}", "matéria")

        any_visible = False
        for node in result.tree.children:
            if self._add_menu_node(subject_root, node, tag_map, shown, query, filter_key):
                any_visible = True

        # Vídeos ainda não exibidos -> "Sem módulo". Isso cobre aulas extras,
        # arquivos sem tag e qualquer sobra após a distribuição sequencial.
        leftover = [
            v for v in subject_videos
            if v.id not in shown and self._video_passes(v, query, filter_key)
        ]
        if leftover:
            folder = self._make_folder("Sem módulo / Não classificadas", "módulo")
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

    @staticmethod
    def _video_relevance_for_node(video: Video, node_title: str) -> int:
        """Pontua se a aula parece corresponder ao rótulo do sumário.

        Necessário quando várias aulas compartilham a mesma hashtag (#PSI01, por
        exemplo). Sem isso, a primeira ocorrência da tag engole todas as aulas e
        o sumário fica aparentemente bagunçado.
        """
        title = (node_title or "").casefold()
        # Ignora rótulos genéricos demais.
        generic = {"videoaula", "video aula", "aulas", "aula", "resumo", "módulo", "modulo"}
        if title.strip() in generic:
            return 0
        terms = [
            t for t in re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", title)
            if t.casefold() not in {"video", "aula", "videoaula", "mp4", "mkv", "mov"}
        ]
        if not terms:
            return 0
        hay = " ".join([
            video.title or "", video.file_name or "", video.caption or "",
            video.module or "", video.lesson or "", video.type or "",
        ]).casefold()
        score = 0
        for term in terms:
            if term.casefold() in hay:
                score += 2 if len(term) >= 5 else 1
        return score

    @staticmethod
    def _summary_tags_in_order(node) -> list[str]:
        """Retorna as hashtags do sumário na ordem visual da árvore."""
        tags: list[str] = []

        def walk(n) -> None:
            for tag in getattr(n, "tags", []) or []:
                norm = "#" + str(tag).lstrip("#").upper()
                tags.append(norm)
            for child in getattr(n, "children", []) or []:
                walk(child)

        walk(node)
        return tags

    @staticmethod
    def _slots_look_like_sequential_course(tags: list[str]) -> bool:
        """Detecta sumários tipo #CAR01 #CAR02 ... #CAR50.

        Nesses cursos, os nomes reais dos arquivos podem vir todos com #CAR01
        ou com códigos repetidos inferidos do padrão "CAR 1 01". Quando o sumário
        é claramente uma sequência, a ordem visual do sumário deve vencer.
        """
        nums: list[int] = []
        prefixes: set[str] = set()
        for tag in tags:
            m = re.fullmatch(r"#([A-ZÀ-Ÿ]{2,10})(\d{2,3})", str(tag).upper())
            if not m:
                continue
            prefixes.add(m.group(1))
            nums.append(int(m.group(2)))
        if len(nums) < max(5, len(tags) * 0.7):
            return False
        if len(prefixes) != 1:
            return False
        nums_sorted = nums
        sequential_hits = sum(1 for a, b in zip(nums_sorted, nums_sorted[1:]) if b == a + 1)
        return sequential_hits >= max(3, len(nums_sorted) - 3)

    @staticmethod
    def _video_order_key(video: Video) -> tuple[int, int, str]:
        # sort_order 0 em bancos antigos pode ser neutro para todos; message_id
        # preserva a ordem do Telegram depois que a sync reverte o histórico.
        return (int(video.sort_order or 0), int(video.message_id or 0), video.title or "")

    @staticmethod
    def _normalized_video_tags(video: Video) -> set[str]:
        return {"#" + str(t).lstrip("#").upper() for t in (video.hashtags or [])}

    def _assign_videos_to_summary_tags(self, tree, videos: list[Video]) -> dict[str, list[Video]]:
        """Casa vídeos com as tags do sumário sem bagunçar a ordem.

        Muitos canais criados por bots/vidsender colocam uma hashtag de tópico
        no nome de TODAS as aulas (ex.: #CAR01), enquanto o sumário usa #CAR01,
        #CAR02, #CAR03... como atalhos/slots. Se usarmos somente a hashtag, todas
        as aulas caem no primeiro subtópico. A regra abaixo faz:

        1. Usa casamento exato quando várias tags distintas cobrem os vídeos.
        2. Se detectar baixa cobertura ou colisão forte, distribui pela ordem do
           sumário e pela ordem das mensagens no Telegram.
        3. Nunca esconde aulas; sobras aparecem em "Sem módulo".
        """
        slots = self._summary_tags_in_order(tree)
        if not slots:
            return {}
        ordered_videos = sorted(videos, key=self._video_order_key)
        exact: dict[str, list[Video]] = {tag: [] for tag in slots}
        slot_set = set(slots)
        for v in ordered_videos:
            for tag in self._normalized_video_tags(v):
                if tag in slot_set:
                    exact.setdefault(tag, []).append(v)

        distinct_slots_with_hits = sum(1 for tag, vals in exact.items() if vals)
        max_collision = max((len(vals) for vals in exact.values()), default=0)
        useful_slots = max(1, min(len(slots), len(ordered_videos)))
        coverage = distinct_slots_with_hits / useful_slots
        # Ambíguo: poucas tags distintas bateram, ou uma única tag engoliu muita
        # coisa. Ex.: 50 aulas contendo #CAR01 e sumário com #CAR01..#CAR50.
        sequential_summary = self._slots_look_like_sequential_course(slots)
        ambiguous = bool(
            len(slots) >= 3 and ordered_videos and (
                sequential_summary
                or coverage < 0.45
                or max_collision >= max(4, len(ordered_videos) // 3)
            )
        )

        assigned: dict[str, list[Video]] = {tag: [] for tag in slots}
        used: set[int] = set()

        if not ambiguous:
            for tag in slots:
                candidates = [v for v in exact.get(tag, []) if v.id not in used]
                if not candidates:
                    continue
                # Em caso de colisão moderada, mantém só a primeira na ordem do
                # Telegram; as demais continuam disponíveis para slots vazios.
                chosen = sorted(candidates, key=self._video_order_key)[0]
                assigned[tag].append(chosen)
                used.add(chosen.id)

        # Preenche slots vazios pela ordem do sumário. Este é o modo que corrige
        # o caso CARDIO: CAR1/CAR2/CAR3 com tags sequenciais no sumário, mas tags
        # repetidas nas aulas reais.
        remaining = [v for v in ordered_videos if v.id not in used]
        rem_idx = 0
        for tag in slots:
            if assigned.get(tag):
                continue
            if rem_idx < len(remaining):
                assigned[tag].append(remaining[rem_idx])
                used.add(remaining[rem_idx].id)
                rem_idx += 1

        return assigned

    def _add_menu_node(
        self, parent, node, tag_map: dict[str, list[Video]], shown: set[int],
        query: str, filter_key: str,
    ) -> bool:
        item = self._make_folder(node.title, self._level_name(node.level))
        any_visible = False
        for child in node.children:
            if self._add_menu_node(item, child, tag_map, shown, query, filter_key):
                any_visible = True
        for tag in node.tags:
            videos_for_tag = [
                v for v in tag_map.get("#" + tag.lstrip("#").upper(), [])
                if v.id not in shown and self._video_passes(v, query, filter_key)
            ]
            if len(videos_for_tag) > 1:
                ranked = [(self._video_relevance_for_node(v, node.title), v) for v in videos_for_tag]
                best = [v for score, v in ranked if score > 0]
                if best:
                    videos_for_tag = best
            for video in sorted(videos_for_tag, key=lambda x: x.message_id):
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
        # Pastas não preenchem as colunas Tipo/Duração/Status. Antes apareciam
        # textos como "módulo"/"aula" no canto direito e a seleção ficava parecendo
        # quebrada em blocos roxos separados.
        item = QTreeWidgetItem([title, "", "", ""])
        item.setData(0, ROLE_NODE_TYPE, "folder")
        item.setData(0, ROLE_RAW_TITLE, title)
        item.setData(0, ROLE_RAW_TITLE + 1, kind)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setForeground(0, QColor(palette(self.theme)["text"]))
        item.setToolTip(0, "Pasta/tópico: expanda para escolher uma videoaula")
        item.setSizeHint(0, QSize(0, 42))
        item.setExpanded(False)
        self._refresh_folder_label(item)
        return item

    @staticmethod
    def _tree_item_depth(item: QTreeWidgetItem | None) -> int:
        """Profundidade visual do item na árvore.

        Como a seta nativa do Qt fica desativada para evitar a faixa azul/roxa
        separada, aplicamos recuo textual. Isso devolve a hierarquia visual
        (módulo > subtópico > tipo > aula) sem reintroduzir o bug da seleção.
        """
        depth = 0
        parent = item.parent() if item is not None else None
        while parent is not None:
            depth += 1
            parent = parent.parent()
        return depth

    @staticmethod
    def _visual_indent(depth: int) -> str:
        return "  " * max(0, depth)

    def _refresh_folder_label(self, item: QTreeWidgetItem) -> None:
        """Mantém seta textual e recuo hierárquico visíveis."""
        try:
            if item.data(0, ROLE_NODE_TYPE) != "folder":
                return
            raw = item.data(0, ROLE_RAW_TITLE) or item.text(0)
            arrow = "▾" if item.isExpanded() else "▸"
            icon = "📂" if item.isExpanded() else "📁"
            indent = self._visual_indent(self._tree_item_depth(item))
            item.setText(0, f"{indent}{arrow} {icon} {raw}")
        except Exception:  # noqa: BLE001
            pass

    def _refresh_video_label(self, item: QTreeWidgetItem) -> None:
        try:
            if item.data(0, ROLE_NODE_TYPE) != "video":
                return
            raw = item.data(0, ROLE_RAW_TITLE) or item.text(0)
            indent = self._visual_indent(self._tree_item_depth(item))
            item.setText(0, f"{indent}{raw}")
        except Exception:  # noqa: BLE001
            pass

    def _refresh_tree_folder_labels(self) -> None:
        def walk(item: QTreeWidgetItem) -> None:
            if item.data(0, ROLE_NODE_TYPE) == "folder":
                self._refresh_folder_label(item)
            elif item.data(0, ROLE_NODE_TYPE) == "video":
                self._refresh_video_label(item)
            for i in range(item.childCount()):
                walk(item.child(i))
        for i in range(self.video_tree.topLevelItemCount()):
            walk(self.video_tree.topLevelItem(i))

    @staticmethod
    def _find_video_descendants(item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        found: list[QTreeWidgetItem] = []
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, ROLE_NODE_TYPE) == "video" and child.data(0, ROLE_VIDEO_ID):
                found.append(child)
            found.extend(MainWindow._find_video_descendants(child))
        return found

    @staticmethod
    def _is_video_item(item: QTreeWidgetItem | None) -> bool:
        return bool(item and item.data(0, ROLE_NODE_TYPE) == "video" and item.data(0, ROLE_VIDEO_ID))

    @staticmethod
    def _is_folder_item(item: QTreeWidgetItem | None) -> bool:
        return bool(item and item.data(0, ROLE_NODE_TYPE) == "folder")

    def selected_node_type(self) -> str | None:
        items = self.video_tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, ROLE_NODE_TYPE)

    def _update_action_buttons_state(self, has_video: bool | None = None) -> None:
        if has_video is None:
            items = self.video_tree.selectedItems() if hasattr(self, "video_tree") else []
            has_video = bool(items and self._is_video_item(items[0]))
        for name in ("watch_btn", "watch_tg_btn", "watch_vlc_btn", "resume_point_btn", "fav_btn", "edit_btn", "mark_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(bool(has_video))

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
        # Videoaula NÃO usa seta triangular para não confundir com pasta expansível.
        # A seta ▸/▾ fica exclusiva dos módulos/tópicos.
        media_icon = "🎬 "
        progress_icon = "✓ " if video.watched_at else ("◐ " if video.progress and video.progress > 0.02 else "")
        display_title = re.sub(r"^#\w+\s*[—–-]\s*", "", video.title or "Aula").strip()
        title = (
            f"{media_icon}{progress_icon}{star}{prefix} — {display_title}"
            if prefix else f"{media_icon}{progress_icon}{star}{display_title}"
        )
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
        item.setData(0, ROLE_NODE_TYPE, "video")
        item.setData(0, ROLE_VIDEO_ID, video.id)
        item.setData(0, ROLE_RAW_TITLE, title)
        item.setSizeHint(0, QSize(0, 38))
        item.setToolTip(0, "Videoaula: duplo clique para assistir")
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
        self._update_action_buttons_state(False)

    def on_tree_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Clique simples em pasta apenas atualiza a seta textual.

        A expansão real fica no duplo clique, evitando abrir/fechar pastas sem
        querer ao navegar pela lista. Com rootIsDecorated(False), a seta é parte
        do texto e permanece alinhada/visível.
        """
        if self._is_folder_item(item) and column == 0:
            self._refresh_folder_label(item)

    def on_tree_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._is_video_item(item):
            self.current_video_id = int(item.data(0, ROLE_VIDEO_ID))
            self.watch_selected_internal()
            return
        if self._is_folder_item(item):
            videos = self._find_video_descendants(item)
            if len(videos) == 1:
                self.video_tree.setCurrentItem(videos[0])
                self.current_video_id = int(videos[0].data(0, ROLE_VIDEO_ID))
                return
            if item.childCount() > 0:
                item.setExpanded(not item.isExpanded())
                self._refresh_folder_label(item)

    def on_video_selected(self) -> None:
        items = self.video_tree.selectedItems()
        if not items:
            self.current_video_id = None
            self._clear_detail()
            return
        item = items[0]
        video_id = item.data(0, ROLE_VIDEO_ID)
        if not self._is_video_item(item):
            self.current_video_id = None
            self.video_title.setText(str(item.data(0, ROLE_RAW_TITLE) or item.text(0)))
            videos_inside = len(self._find_video_descendants(item))
            self.video_info.setText(
                "Você selecionou uma pasta/tópico. Expanda esta seção e escolha "
                f"uma videoaula.\n\nVideoaulas dentro desta pasta: {videos_inside}."
            )
            self.resume_bar.hide()
            self._update_action_buttons_state(False)
            return
        self.current_video_id = int(video_id)
        self._update_action_buttons_state(True)
        video = self.db.get_video(self.current_video_id)
        if not video:
            return
        # O player local foi removido da rota principal; não fazemos mais warm-up
        # de streaming ao selecionar uma aula, evitando travamentos/erros de peer.
        self.video_title.setText(("★ " if video.favorite else "") + re.sub(r"^#\w+\s*[—–-]\s*", "", video.title or "Aula"))
        tags = " ".join(video.hashtags) or "sem hashtags"
        if video.watched_at:
            status = "Assistida"
        elif video.progress > 0.02:
            status = f"{int(video.progress * 100)}% assistido"
        else:
            status = "Não assistida"
        subject = self.db.get_subject(video.subject_id) if video.subject_id else None
        resume_text = ""
        if video.position_ms and video.position_ms > 1000:
            resume_text = f" · parou em {human_duration(video.position_ms // 1000)}"
        line1 = f"{human_size(video.size)} · {human_duration(video.duration)} · {status}{resume_text}"
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
            node_type = self.selected_node_type()
            if node_type == "folder":
                QMessageBox.information(
                    self, "Aula",
                    "Você selecionou uma pasta/tópico. Expanda esta seção e escolha uma videoaula.",
                )
            else:
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
        watch_here = menu.addAction("▶ Assistir aqui (player embutido)")
        watch = menu.addAction("📲 Abrir no Telegram")
        vlc = menu.addAction("Abrir no VLC")
        save_point = menu.addAction("⏱ Salvar ponto onde parei")
        menu.addSeparator()
        edit = menu.addAction("✎ Editar aula")
        fav = menu.addAction("☆ Remover favorito" if video.favorite else "★ Favoritar")
        mark = menu.addAction(
            "Marcar como NÃO assistida" if video.watched_at else "✓ Marcar como assistida"
        )
        tg_link = menu.addAction("Copiar link do Telegram (t.me)")
        menu.addSeparator()
        delete = menu.addAction("Remover da lista")
        action = menu.exec(self.video_tree.mapToGlobal(pos))
        if action == watch_here:
            self.watch_selected_internal()
        elif action == watch:
            self.open_selected_in_telegram()
        elif action == vlc:
            self.watch_selected_vlc()
        elif action == save_point:
            self.save_resume_point_selected()
        elif action == edit:
            self.edit_selected_video()
        elif action == fav:
            self.toggle_favorite_selected()
        elif action == mark:
            self.toggle_watched_selected()
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

    def save_resume_point_selected(self) -> None:
        """Salva manualmente o ponto em que o usuário parou no Telegram.

        O Telegram Desktop/64Gram/Nekogram não expõe para apps externos o tempo
        exato visto no player. Por isso o TgPlayer permite registrar manualmente
        o minuto para exibir e retomar depois.
        """
        video = self.selected_video()
        if not video:
            return
        default = human_duration((video.position_ms or 0) // 1000) if video.position_ms else "0:00"
        text, ok = QInputDialog.getText(
            self,
            "Salvar ponto da aula",
            "Em qual tempo você parou? Use mm:ss ou hh:mm:ss:",
            text=default,
        )
        if not ok:
            return
        seconds = self._parse_time_to_seconds(text)
        if seconds is None:
            QMessageBox.warning(self, "Tempo inválido", "Digite no formato mm:ss ou hh:mm:ss. Exemplo: 12:34")
            return
        duration_ms = int(video.duration * 1000) if video.duration else None
        self.db.save_progress(video.id, int(seconds * 1000), duration_ms)
        self._refresh_after_change()
        self._reselect_video(video.id)

    @staticmethod
    def _parse_time_to_seconds(text: str) -> int | None:
        text = (text or "").strip().replace(",", ":")
        if not text:
            return None
        if text.isdigit():
            return int(text) * 60
        parts = text.split(":")
        if len(parts) not in (2, 3):
            return None
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if any(n < 0 for n in nums):
            return None
        if len(nums) == 2:
            m, sec = nums
            if sec >= 60:
                return None
            return m * 60 + sec
        h, m, sec = nums
        if m >= 60 or sec >= 60:
            return None
        return h * 3600 + m * 60 + sec

    # =================================================================== player
    def _stream_payload(self, video: Video) -> dict[str, Any]:
        course = self.db.get_course(video.course_id)
        return {
            "course_id": video.course_id,
            "chat_id": video.chat_id,
            "chat_username": getattr(course, "username", None) if course else None,
            "message_id": video.message_id,
            "file_id": getattr(video, "file_id", None),
            "file_unique_id": getattr(video, "file_unique_id", None),
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

    def _schedule_warmup(self, video_id: int) -> None:
        """Agenda (com debounce) o warm-up do início da aula selecionada."""
        self._warmup_video_id = video_id
        try:
            self._warmup_timer.start()  # reinicia o debounce de 400 ms
        except Exception:  # noqa: BLE001
            pass

    def _do_warmup(self) -> None:
        """Dispara o warm-up da aula atualmente marcada (fire-and-forget)."""
        video_id = self._warmup_video_id
        if not video_id:
            return
        try:
            if self.service.active_sessions() > 0:
                # Já há uma sessão tocando; não competir por banda.
                return
        except Exception:  # noqa: BLE001
            pass
        video = self.db.get_video(video_id)
        if not video or not video.size:
            return
        course = self.db.get_course(video.course_id)
        payload = {
            "course_id": video.course_id,
            "chat_id": video.chat_id,
            "chat_username": getattr(course, "username", None) if course else None,
            "message_id": video.message_id,
            "file_id": getattr(video, "file_id", None),
            "file_unique_id": getattr(video, "file_unique_id", None),
            "size": video.size,
            "mime_type": video.mime_type,
            "duration": video.duration,
            "width": video.width,
            "height": video.height,
        }
        try:
            self.service.call(self.service.warm_up_video(payload))
        except Exception:  # noqa: BLE001
            pass

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

    def _telegram_urls_for_video(self, video: Video) -> tuple[str | None, str | None]:
        """Retorna (app_url, web_url) para abrir a mensagem original no Telegram.

        - Canal/grupo público: tg://resolve?domain=...&post=... + https://t.me/...
        - Canal/grupo privado/supergrupo: tg://privatepost?... + https://t.me/c/...

        O link privado funciona quando o usuário é membro do chat no Telegram
        Desktop/64Gram/Nekogram.
        """
        course = self.db.get_course(video.course_id) if video.course_id else None
        username = (getattr(course, "username", None) or "").strip().lstrip("@") or None
        return self.service.telegram_message_urls(username, video.chat_id, video.message_id)

    def open_selected_in_telegram(self) -> None:
        video = self.selected_video()
        if not video:
            return
        app_url, web_url = self._telegram_urls_for_video(video)
        if not app_url and not web_url:
            QMessageBox.information(
                self,
                "Abrir no Telegram",
                "Não consegui montar um link nativo para esta aula. "
                "Tente sincronizar o curso novamente ou copie o link pelo menu.",
            )
            return
        opened = False
        # O protocolo tg:// abre Telegram Desktop e derivados quando registrados no Windows.
        if app_url:
            try:
                opened = bool(QDesktopServices.openUrl(QUrl(app_url)))
            except Exception:  # noqa: BLE001
                opened = False
        # Em links privados, ou quando tg:// não estiver registrado, t.me/c é o fallback.
        if not opened and web_url:
            try:
                opened = bool(QDesktopServices.openUrl(QUrl(web_url)))
            except Exception:  # noqa: BLE001
                opened = False
        if web_url:
            QApplication.clipboard().setText(web_url)
        if not opened:
            QMessageBox.information(
                self,
                "Link copiado",
                "Não consegui abrir automaticamente, mas copiei o link da aula. "
                "Cole no Telegram Desktop ou no navegador.",
            )
        else:
            # Não marca como assistida: abrir a mensagem no Telegram não prova que
            # a aula foi vista. Registra apenas acesso mínimo para aparecer em continuar.
            try:
                duration_ms = int(video.duration * 1000) if video.duration else None
                self.db.save_progress(video.id, max(1, int(video.position_ms or 0)), duration_ms)
            except Exception:  # noqa: BLE001
                pass

    def watch_selected_internal(self) -> None:
        """Abre a aula selecionada no PLAYER EMBUTIDO (rápido, same-origin)."""
        video = self.selected_video()
        if not video:
            return
        self._open_player_for(video)

    def _play_media_video(self, media: dict[str, Any]) -> None:
        """Abre mídia avulsa da aba Arquivos diretamente no Telegram quando possível."""
        try:
            course = self.get_current_course()
            username = getattr(course, "username", None) if course else None
            chat_id = media.get("chat_id") or getattr(course, "chat_id", None)
            message_id = int(media.get("message_id") or 0)
            app_url, web_url = self.service.telegram_message_urls(username, chat_id, message_id)
            opened = False
            if app_url:
                opened = bool(QDesktopServices.openUrl(QUrl(app_url)))
            if not opened and web_url:
                opened = bool(QDesktopServices.openUrl(QUrl(web_url)))
            if web_url:
                QApplication.clipboard().setText(web_url)
            if not opened:
                QMessageBox.information(self, "Abrir no Telegram", "Não consegui abrir automaticamente, mas copiei o link da mídia.")
        except Exception as exc:  # noqa: BLE001
            log.exception("Erro ao abrir mídia no Telegram")
            QMessageBox.critical(self, "Erro", str(exc))

    def _open_player_for(self, video: Video) -> None:
        """Prepara o streaming e abre o player embutido (QtWebEngine)."""
        if not is_webengine_available():
            # Sem QtWebEngine: oferece o VLC como caminho alternativo.
            if QMessageBox.question(
                self,
                "Player embutido indisponível",
                "O componente QtWebEngine não está disponível neste build, então "
                "o player embutido não pode abrir. Deseja assistir pelo VLC?",
            ) == QMessageBox.Yes:
                self.watch_selected_vlc()
            return

        stream = self._prepare_stream(video)
        if not stream:
            return
        token = stream.get("token")
        player_url = stream.get("player_url")
        stream_url = stream.get("stream_url") or stream.get("url")

        def _on_progress(position_ms: int, duration_ms: int) -> None:
            try:
                pos = max(0, int(position_ms or 0))
                dur = int(duration_ms or 0) or (
                    int(video.duration * 1000) if video.duration else None
                )
                self.db.save_progress(video.id, pos, dur)
                # Conclui a aula quando assistiu ~95% dela.
                if dur and pos >= int(dur * 0.95):
                    self.db.mark_watched(video.id)
            except Exception:  # noqa: BLE001
                log.exception("Falha ao salvar progresso do player embutido")

        def _on_open_vlc() -> None:
            try:
                # Mantém o stream vivo para o VLC externo e abre nele.
                if token:
                    self.service.call(self.service.release_stream_later(token, 7200))
                self._launch_vlc(stream_url)
            except Exception:  # noqa: BLE001
                log.exception("Falha ao abrir no VLC a partir do player")

        try:
            dlg = VideoPlayerDialog(
                title=video.title,
                url=stream_url,
                token=token,
                service=self.service,
                start_position_ms=int(video.position_ms or 0),
                on_progress=_on_progress,
                on_open_vlc=_on_open_vlc,
                player_url=player_url,
                parent=self,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao criar o player embutido")
            QMessageBox.critical(self, "Erro no player", str(exc))
            return

        # Mantém referência para não ser coletado e atualiza listas ao fechar.
        self._active_player = dlg
        dlg.finished.connect(lambda _=None: self._refresh_after_change())
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def watch_selected_vlc(self) -> None:
        video = self.selected_video()
        if not video:
            return
        try:
            stream = self._prepare_stream(video)
            if not stream:
                return
            self._launch_vlc(stream.get("stream_url") or stream.get("url"))
            # Abrir no VLC não significa que a aula foi assistida. Mantemos o
            # stream vivo por TTL e registramos só um acesso/progresso mínimo.
            try:
                self.service.call(self.service.release_stream_later(stream.get("token"), 7200))
            except Exception:  # noqa: BLE001
                pass
            try:
                duration_ms = int(video.duration * 1000) if video.duration else None
                self.db.save_progress(video.id, max(1, int(video.position_ms or 0)), duration_ms)
            except Exception:  # noqa: BLE001
                pass
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
        """Copia o link t.me da aula.

        Funciona para canais públicos e também para chats privados/supergrupos
        quando o Telegram aceita o formato t.me/c/<id>/<mensagem>.
        """
        video = self.selected_video()
        if not video:
            return
        course = self.db.get_course(video.course_id) if video.course_id else None
        username = course.username if course else None
        link = self.service.telegram_message_link(username, video.message_id, video.chat_id)
        if not link:
            QMessageBox.information(
                self, "Link do Telegram",
                "Não consegui montar um link t.me para esta aula. "
                "Tente sincronizar novamente.",
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
            "Organize e abra as videoaulas dos seus cursos diretamente no Telegram, "
            "com opção secundária de VLC e módulo de acompanhamento de estudos.\n\n"
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

    Em algumas placas de vídeo/drivers, a composição por GPU do Qt desenha
    retângulos pretos por cima de widgets. Forçar a composição por software
    resolve o problema de forma confiável, com impacto mínimo de desempenho.
    """
    import os

    from PySide6.QtCore import QCoreApplication

    # Também define flags do Chromium caso alguma dependência futura carregue QtWebEngine.
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
        icon_base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        icon_path = icon_base / "assets" / "icon.ico"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
    except Exception:  # noqa: BLE001
        pass
    try:
        app.setFont(QFont("Segoe UI", 10))
    except Exception:  # noqa: BLE001
        pass
    window = MainWindow()
    try:
        icon_base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        icon_path = icon_base / "assets" / "icon.ico"
        if icon_path.exists():
            window.setWindowIcon(QIcon(str(icon_path)))
    except Exception:  # noqa: BLE001
        pass
    window.showMaximized()
    window._update_window_buttons()
    sys.exit(app.exec())
