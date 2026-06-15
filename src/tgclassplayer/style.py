"""Temas premium do TGClassPlayer — CLARO 🌞 e ESCURO 🌙.

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
#Brand {{ font-size: 22px; font-weight: 900; color: {c['text']}; letter-spacing: -0.5px; }}
#BrandAccent {{ font-size: 22px; font-weight: 900; color: {c['accent']}; letter-spacing: -0.5px; }}
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
#GhostButton:hover {{ color: {c['text']}; border-color: {c['accent']}; }}
#DangerButton {{ color: {c['danger']}; border-color: {c['border']}; }}
#DangerButton:hover {{ background: {c['danger']}; color: #fff; border-color: {c['danger']}; }}
#IconButton {{ padding: 8px 10px; min-width: 18px; }}
#ThemeToggle {{ padding: 8px 12px; font-size: 15px; }}

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
QTreeWidget::item {{ padding: 7px 4px; border-radius: 7px; }}
QTreeWidget::item:hover {{ background: {c['hover']}; }}
QTreeWidget::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
QHeaderView::section {{
    background: {c['bg2']}; color: {c['muted']};
    border: none; border-bottom: 1px solid {c['border_soft']};
    padding: 8px 8px; font-weight: 800; font-size: 11px;
}}
QTreeWidget {{ outline: none; }}

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
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px; }}
QScrollBar::handle:vertical {{
    background: {c['border']}; border-radius: 6px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 4px; }}
QScrollBar::handle:horizontal {{
    background: {c['border']}; border-radius: 6px; min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
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
#PlayerDialog {{ background: #000; }}
"""


# Compatibilidade: alguns módulos antigos importavam APP_QSS / COLORS.
COLORS = DARK
APP_QSS = build_qss("dark")
