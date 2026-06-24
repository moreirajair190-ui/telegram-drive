"""Temas premium do TgPlayer — CLARO 🌞 e ESCURO 🌙.

Cada tema traz uma paleta calibrada (cores, contraste, sombras) e um QSS
completo. `build_qss(theme)` devolve a folha de estilo e `palette(theme)` o
dicionário de cores usado por widgets que precisam pintar à mão (árvore,
gráficos, etc.).
"""

from __future__ import annotations

# --------------------------------------------------------------------- paletas
DARK = {
    "name": "dark",
    "bg": "#0b0f1a",
    "bg2": "#0f1524",
    "panel": "#121a2c",
    "panel2": "#16203a",
    "card": "#141d31",
    "card_soft": "#101728",
    "border": "#243049",
    "border_soft": "#1b253b",
    "text": "#eef2fb",
    "text2": "#c4cde0",
    "muted": "#8b97b3",
    "muted2": "#6c7794",
    "accent": "#7c5cff",
    "accent2": "#22d3ee",
    "accent_text": "#ffffff",
    "good": "#34d399",
    "warn": "#fbbf24",
    "danger": "#f87171",
    "track": "#1d2840",
    "hover": "#1c2740",
    "selection": "#26315a",
    "chart_grid": "#22304d",
    # Faixas das pastas/tópicos na árvore de aulas (substituem o "roxo bugado").
    # Estratégia nova: o nível 0 (matéria/módulo) ganha uma faixa levemente
    # destacada; os níveis mais profundos ficam praticamente transparentes para
    # não empilhar "cinza sobre cinza" — quem dá a hierarquia é a indentação e
    # um pequeno indicador colorido à esquerda, deixando tudo mais leve e limpo.
    "folder_l0": "#18223c",   # módulo / matéria (nível 0) — faixa principal
    "folder_l1": "#131b30",   # subtópico (nível 1) — bem sutil
    "folder_l2": "#10172a",   # tipo / videoaula (nível 2+) — quase do fundo
    "folder_border": "#283a60",
    "folder_tint": "#7c5cff",  # indicador de pasta (acento) à esquerda
    "row_alt": "#0e1525",     # listras suaves nas linhas de vídeo
}

LIGHT = {
    "name": "light",
    "bg": "#f4f6fb",
    "bg2": "#eef1f8",
    "panel": "#ffffff",
    "panel2": "#f7f9fd",
    "card": "#ffffff",
    "card_soft": "#f3f6fc",
    "border": "#dde3ee",
    "border_soft": "#e7ecf5",
    "text": "#11192b",
    "text2": "#2c3650",
    "muted": "#5b6781",
    "muted2": "#8590a8",
    "accent": "#6d4bff",
    "accent2": "#0ea5b7",
    "accent_text": "#ffffff",
    "good": "#0f9d63",
    "warn": "#b97e09",
    "danger": "#dc2626",
    "track": "#e6eaf3",
    "hover": "#eef2fb",
    "selection": "#e4ddff",
    "chart_grid": "#dde3ee",
    # Faixas das pastas/tópicos na árvore de aulas (tema claro).
    # Cores muito mais claras e harmônicas: só o nível 0 recebe uma faixa
    # delicada (quase branca com um toque de violeta); os níveis profundos
    # ficam transparentes. Isso elimina o aspecto "cinza-azulado pesado".
    "folder_l0": "#f1f0fb",   # módulo / matéria — leve tom violeta
    "folder_l1": "#f9fafe",   # subtópico — quase branco
    "folder_l2": "#ffffff",   # tipo / videoaula — branco (some no fundo)
    "folder_border": "#e7e3f6",
    "folder_tint": "#6d4bff",  # indicador de pasta (acento) à esquerda
    "row_alt": "#f8fafe",
}


def palette(theme: str = "dark") -> dict[str, str]:
    return LIGHT if theme == "light" else DARK


def build_qss(theme: str = "dark") -> str:
    c = palette(theme)
    return f"""
* {{
    font-family: "Segoe UI", "Inter", Arial, sans-serif;
    outline: none;
}}
QMainWindow, QWidget {{
    background: {c['bg']};
    color: {c['text']};
    font-size: 13px;
}}
QToolTip {{
    background: {c['panel']}; color: {c['text']};
    border: 1px solid {c['border']}; padding: 6px 10px; border-radius: 8px;
}}

/* ---------------------------------------------------------------- estruturas */
#Sidebar {{ background: {c['bg2']}; border-right: 1px solid {c['border_soft']}; }}
#CenterPane {{ background: {c['bg']}; }}
#RightPane {{ background: {c['bg2']}; border-left: 1px solid {c['border_soft']}; }}
#TopBar {{ background: {c['panel']}; border-bottom: 1px solid {c['border_soft']}; }}

QSplitter::handle {{ background: {c['border_soft']}; }}

/* ------------------------------------------------------------------- textos */
#Brand {{ font-size: 21px; font-weight: 900; color: {c['text']}; letter-spacing: -0.5px; }}
#BrandAccent {{ font-size: 21px; font-weight: 900; color: {c['accent']}; letter-spacing: -0.5px; }}
#PomoTime {{ font-size: 42px; min-height: 46px; font-weight: 950; color: {c['text']}; letter-spacing: -1.2px; }}
#PomoPhase {{ font-size: 14px; font-weight: 850; color: {c['text']}; }}
#PageTitle {{ font-size: 22px; font-weight: 850; color: {c['text']}; }}
#PanelTitle {{ font-size: 16px; font-weight: 800; color: {c['text']}; }}
#SectionTitle {{ font-size: 11px; font-weight: 800; color: {c['muted2']};
    letter-spacing: 1.4px; text-transform: uppercase; }}
#Muted {{ color: {c['muted']}; }}
#Muted2 {{ color: {c['muted2']}; font-size: 12px; }}
#StatusLabel {{ color: {c['muted']}; font-weight: 700; padding: 4px 0; }}
#StatusConnected {{ color: {c['good']}; font-weight: 800; padding: 4px 0; }}
#BigNumber {{ font-size: 26px; font-weight: 900; color: {c['text']}; }}
#CardLabel {{ font-size: 11px; font-weight: 800; color: {c['muted2']};
    letter-spacing: 1px; text-transform: uppercase; }}

/* ------------------------------------------------------------------ botões */
QPushButton {{
    background: {c['panel']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 11px;
    padding: 9px 14px; font-weight: 700;
}}
QPushButton:hover {{ background: {c['hover']}; border-color: {c['accent']}; }}
QPushButton:pressed {{ background: {c['selection']}; }}
QPushButton:disabled {{ color: {c['muted2']}; border-color: {c['border_soft']}; }}

#PrimaryButton {{
    background: {c['accent']}; color: {c['accent_text']};
    border: 1px solid {c['accent']}; font-weight: 800;
}}
#PrimaryButton:hover {{ background: {c['accent2']}; border-color: {c['accent2']}; }}
#GhostButton {{ background: transparent; border: 1px dashed {c['border']}; color: {c['muted']}; }}

#TopMenuButton {{
    background: transparent; border: 1px solid transparent; border-radius: 8px;
    padding: 6px 10px; color: {c['muted']}; font-weight: 800;
}}
#TopMenuButton:hover {{ background: {c['hover']}; color: {c['text']}; border-color: {c['border_soft']}; }}
#TopMenuButton::menu-indicator {{ image: none; width: 0px; }}
#GhostButton:hover {{ color: {c['text']}; border-color: {c['accent']}; }}
#DangerButton {{ color: {c['danger']}; border-color: {c['border']}; }}
#DangerButton:hover {{ background: {c['danger']}; color: #fff; border-color: {c['danger']}; }}
#IconButton {{ padding: 8px 10px; min-width: 18px; }}
#ThemeToggle {{ padding: 8px 12px; font-size: 15px; }}
#WindowButton {{
    padding: 0px; min-width: 34px; max-width: 34px; min-height: 30px; max-height: 30px;
    border-radius: 8px; font-size: 14px; font-weight: 900; background: transparent;
    border: 1px solid transparent; color: {c['muted']};
}}
#WindowButton:hover {{ background: {c['hover']}; color: {c['text']}; border-color: {c['border']}; }}
#WindowCloseButton {{
    padding: 0px; min-width: 34px; max-width: 34px; min-height: 30px; max-height: 30px;
    border-radius: 8px; font-size: 17px; font-weight: 900; background: transparent;
    border: 1px solid transparent; color: {c['muted']};
}}
#WindowCloseButton:hover {{ background: {c['danger']}; color: #ffffff; border-color: {c['danger']}; }}

QPushButton:checkable {{ }}
QPushButton:checked {{
    background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']};
}}

/* ------------------------------------------------------------------ inputs */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDateEdit {{
    background: {c['panel']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 10px;
    padding: 9px 11px; selection-background-color: {c['accent']};
    selection-color: #fff;
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{
    border-color: {c['accent']};
}}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {c['panel']}; color: {c['text']};
    border: 1px solid {c['border']}; selection-background-color: {c['selection']};
    selection-color: {c['text']}; outline: none;
}}

QSpinBox {{
    min-height: 30px;
    qproperty-buttonSymbols: NoButtons;
    font-weight: 800;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; height: 0px; border: none; }}
QSpinBox::up-arrow, QSpinBox::down-arrow {{ width: 0px; height: 0px; }}

#PomoSpin {{
    min-height: 30px;
    max-height: 34px;
    padding: 5px 8px;
    font-size: 13px;
    font-weight: 850;
}}
#PomoButton {{
    min-height: 30px;
    padding: 6px 10px;
    font-size: 12px;
}}
#PomoPreset {{
    min-height: 28px;
    padding: 5px 8px;
    font-size: 11px;
}}

#StudyScroll {{ background: transparent; border: none; }}
#StudyContent {{ background: transparent; }}

/* ----------------------------------------------------------------- listas */
QListWidget, QTreeWidget, QTableWidget {{
    background: {c['panel']}; color: {c['text']};
    border: 1px solid {c['border_soft']}; border-radius: 12px;
    padding: 6px; alternate-background-color: {c['panel2']};
}}
QListWidget::item {{
    padding: 11px 12px; border-radius: 9px; margin: 2px 2px;
}}
QListWidget::item:hover {{ background: {c['hover']}; }}
QListWidget::item:selected {{
    background: {c['selection']}; color: {c['text']};
}}
/* A árvore agora é pintada por um delegate (LessonTreeDelegate) que desenha a
   linha inteira sem emendas/gaps. Por isso o QSS dos itens fica neutro:
   sem margin (que criava as faixas roxas "quebradas" entre as colunas) e sem
   background de seleção — quem cuida do destaque é o delegate. */
/* show-decoration-selected: 0 → a seleção NÃO pinta a área de indentação/branch
   à esquerda da 1ª coluna (era de lá que vinha a "faixa azul" solta). Quem
   desenha o realce é exclusivamente o LessonTreeDelegate. */
QTreeWidget {{ outline: 0; show-decoration-selected: 0; }}
QTreeWidget::item {{ padding: 10px 8px; border: 0px; margin: 0px; }}
QTreeWidget::item:hover {{ background: transparent; }}
QTreeWidget::item:selected, QTreeWidget::item:selected:active, QTreeWidget::item:selected:!active {{
    background: transparent; color: {c['text']}; border: 0px; outline: 0;
}}
QTreeWidget::item:focus {{ border: 0px; outline: 0; }}
/* A seta nativa do Qt foi desativada: em alguns temas ela criava uma faixa
   azul/roxa separada da linha. A árvore usa setas textuais ▸/▾ no próprio rótulo. */
QTreeWidget::branch, QTreeWidget::branch:selected, QTreeWidget::branch:has-children:closed,
QTreeWidget::branch:has-children:open, QTreeWidget::branch:closed:has-children:has-siblings,
QTreeWidget::branch:open:has-children:has-siblings {{
    width: 0px; min-width: 0px; max-width: 0px; border: 0px; border-image: none; image: none; background: transparent;
}}
QHeaderView::section {{
    background: {c['bg2']}; color: {c['muted']};
    border: none; border-bottom: 1px solid {c['border_soft']};
    padding: 8px 8px; font-weight: 800; font-size: 11px;
}}

/* -------------------------------------------------------------------- abas */
QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {c['muted']};
    padding: 10px 18px; margin-right: 4px; font-weight: 800;
    border: none; border-bottom: 3px solid transparent;
}}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{
    color: {c['accent']}; border-bottom: 3px solid {c['accent']};
}}

/* ----------------------------------------------------------------- cartões */
#HeroCard, #Card, #CardSoft, #StatCard {{
    background: {c['card']}; border: 1px solid {c['border_soft']};
    border-radius: 16px;
}}
#CardSoft {{ background: {c['card_soft']}; }}
#StatCard {{ background: {c['card']}; }}

/* ------------------------------------------------------ planejador (Kanban) */
#KanbanColumn {{
    background: {c['card_soft']}; border: 1px solid {c['border_soft']};
    border-radius: 16px;
}}
#KanbanTitle {{ color: {c['text']}; font-weight: 800; font-size: 13px; }}
#KanbanCount {{
    color: {c['muted']}; font-weight: 800; font-size: 11px;
    background: {c['panel2']}; border-radius: 9px; padding: 1px 9px;
}}
#KanbanList {{
    background: transparent; border: none; padding: 2px;
}}
#KanbanList::item {{ border: none; background: transparent; padding: 0px; margin: 0px; }}
#KanbanList::item:selected {{ background: transparent; }}
#KanbanCard {{
    background: {c['card']}; border: 1px solid {c['border']};
    border-radius: 12px;
}}
#KanbanCard:hover {{ border: 1px solid {c['accent']}; }}
#KanbanCardTitle {{ color: {c['text']}; font-weight: 700; font-size: 12px; }}

/* Calendário grande do planejador */
#CalMonth {{ font-size: 18px; font-weight: 900; color: {c['text']}; letter-spacing: -0.4px; }}
#CalNav {{
    background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']};
    border-radius: 9px; font-size: 18px; font-weight: 900; padding: 0px;
}}
#CalNav:hover {{ background: {c['hover']}; border-color: {c['accent']}; color: {c['accent']}; }}
#CalWeekday {{
    color: {c['muted2']}; font-size: 10.5px; font-weight: 900;
    letter-spacing: 1px; padding: 2px 0;
}}
#CalSelectedDay {{ color: {c['muted']}; font-size: 12px; font-weight: 700; padding-top: 2px; }}
/* As células do calendário (DayCell) recebem estilo dinâmico no próprio
   widget (cores dependem de hoje/selecionado/fora-do-mês). */
#DayCell {{ background: {c['panel']}; border: 1px solid {c['border_soft']}; border-radius: 12px; }}
#DayCell:hover {{ border-color: {c['accent']}; }}

/* -------------------------------------------------------------- progresso */
QProgressBar {{
    background: {c['track']}; border: none; border-radius: 8px;
    height: 14px; text-align: center; color: {c['text']};
    font-weight: 800; font-size: 11px;
}}
QProgressBar::chunk {{
    border-radius: 8px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['accent']}, stop:1 {c['accent2']});
}}

/* ------------------------------------------------------------------ slider */
QSlider::groove:horizontal {{
    height: 6px; background: {c['track']}; border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {c['accent']}, stop:1 {c['accent2']});
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #fff; width: 16px; height: 16px; margin: -6px 0;
    border-radius: 8px; border: 2px solid {c['accent']};
}}

/* ----------------------------------------------------------------- scroll */
/* Barra de rolagem fina e suave (estilo "overlay"): trilho transparente,
   alça arredondada que cresce levemente e ganha cor de destaque no hover. */
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 6px 2px 6px 2px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {c['border']}; border-radius: 5px; min-height: 42px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar::handle:vertical:pressed {{ background: {c['accent']}; }}
QScrollBar:horizontal {{
    background: transparent; height: 10px; margin: 2px 6px 2px 6px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {c['border']}; border-radius: 5px; min-width: 42px;
}}
QScrollBar::handle:horizontal:hover {{ background: {c['accent']}; }}
QScrollBar::handle:horizontal:pressed {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0; background: none; border: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -------------------------------------------------------------------- menu */
QMenuBar {{ background: {c['panel']}; color: {c['text']}; border-bottom: 1px solid {c['border_soft']}; }}
QMenuBar::item {{ padding: 8px 12px; background: transparent; }}
QMenuBar::item:selected {{ background: {c['hover']}; border-radius: 6px; }}
QMenu {{
    background: {c['panel']}; color: {c['text']};
    border: 1px solid {c['border']}; border-radius: 10px; padding: 6px;
}}
QMenu::item {{ padding: 8px 22px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['selection']}; }}
QMenu::separator {{ height: 1px; background: {c['border_soft']}; margin: 6px 8px; }}

QDialog {{ background: {c['bg']}; }}
QCheckBox {{ color: {c['text']}; spacing: 8px; }}
/* Rótulos SEM fundo opaco — evita "caixas" cobrindo o texto. */
QLabel {{ color: {c['text']}; background: transparent; }}

/* ----------------------------------------------------- estado vazio (listas) */
#ListPlaceholder {{
    color: {c['muted2']}; font-size: 12.5px; font-weight: 700;
    background: transparent; line-height: 150%;
}}

/* ------------------------------------------------------------ player embutido */
#PlayerDialog {{ background: #05070f; }}
#PlayerStage {{ background: #000; border: none; }}
#PlayerWeb {{ background: #000; }}
#PlayerBar {{
    background: {c['panel']}; border-top: 1px solid {c['border_soft']};
}}
#PlayerBar QPushButton#IconButton {{
    background: transparent; border: 1px solid transparent; border-radius: 10px;
    font-size: 17px; padding: 6px 10px; color: {c['text']};
}}
#PlayerBar QPushButton#IconButton:hover {{
    background: {c['hover']}; border-color: {c['border']};
}}
#PlayerLoading {{
    background: rgba(5, 7, 15, 0.86);
}}
#PlayerToast {{
    background: {c['panel2']}; color: {c['text']};
    border: 1px solid {c['accent']}; border-radius: 999px;
    padding: 8px 16px; font-weight: 800; margin: 8px;
}}
"""


# Compatibilidade: alguns módulos antigos importavam APP_QSS / COLORS.
COLORS = DARK
APP_QSS = build_qss("dark")
