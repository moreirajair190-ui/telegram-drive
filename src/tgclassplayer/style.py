"""Tema premium (dark) do TGClassPlayer v5.

Paleta inspirada em apps premium de streaming/educação: fundo escuro
profundo, cartões com leve elevação, acento roxo/índigo e detalhes em ciano.
"""

# Paleta central — reaproveitada em widgets que desenham na mão (player).
COLORS = {
    "bg": "#0a0e1a",
    "bg_alt": "#0f1424",
    "panel": "#141a2e",
    "panel_alt": "#1a2138",
    "card": "#161d33",
    "border": "#252e4a",
    "border_soft": "#1f2740",
    "text": "#eef1f8",
    "muted": "#8a95b3",
    "muted2": "#5d688a",
    "accent": "#7c5cff",
    "accent_hover": "#8e72ff",
    "accent_press": "#6a49f2",
    "accent2": "#22d3ee",
    "success": "#34d399",
    "warning": "#fbbf24",
    "danger": "#f87171",
}

APP_QSS = """
* {
    font-family: "Segoe UI", "Inter", "Roboto", Arial, sans-serif;
    font-size: 10.5pt;
    outline: none;
}

QMainWindow, QDialog {
    background: #0a0e1a;
    color: #eef1f8;
}

QWidget#Sidebar {
    background: #0f1424;
    border-right: 1px solid #1f2740;
}

QWidget#CenterPane {
    background: #0a0e1a;
}

QWidget#RightPane {
    background: #0f1424;
    border-left: 1px solid #1f2740;
}

/* ---------------------------------------------------------------- Tipografia */
QLabel { color: #eef1f8; background: transparent; }

QLabel#Brand {
    font-size: 19pt;
    font-weight: 800;
    color: #ffffff;
    letter-spacing: -0.5px;
}
QLabel#BrandAccent {
    font-size: 19pt;
    font-weight: 800;
    color: #7c5cff;
    letter-spacing: -0.5px;
}
QLabel#AppTitle {
    font-size: 19pt;
    font-weight: 800;
    color: #ffffff;
    letter-spacing: -0.5px;
}
QLabel#PageTitle {
    font-size: 17pt;
    font-weight: 800;
    color: #ffffff;
    letter-spacing: -0.4px;
}
QLabel#SectionTitle {
    font-size: 9.5pt;
    font-weight: 800;
    color: #5d688a;
    letter-spacing: 1.2px;
}
QLabel#PanelTitle {
    font-size: 13pt;
    font-weight: 750;
    color: #ffffff;
}
QLabel#Muted {
    color: #8a95b3;
}
QLabel#Muted2 {
    color: #5d688a;
    font-size: 9.5pt;
}
QLabel#StatusLabel {
    padding: 9px 12px;
    border: 1px solid #252e4a;
    border-radius: 12px;
    background: #141a2e;
    color: #8a95b3;
    font-weight: 600;
}
QLabel#StatusConnected {
    padding: 9px 12px;
    border: 1px solid rgba(52,211,153,0.35);
    border-radius: 12px;
    background: rgba(52,211,153,0.10);
    color: #34d399;
    font-weight: 700;
}
QLabel#Pill {
    background: #1a2138;
    border: 1px solid #252e4a;
    border-radius: 999px;
    padding: 4px 11px;
    color: #b8c0db;
    font-size: 9pt;
    font-weight: 700;
}

/* ------------------------------------------------------------------- Botões */
QPushButton {
    background: #1a2138;
    border: 1px solid #2a3352;
    border-radius: 12px;
    padding: 10px 14px;
    color: #e6e9f5;
    font-weight: 650;
}
QPushButton:hover {
    background: #222b46;
    border-color: #3a456b;
}
QPushButton:pressed {
    background: #161d33;
}
QPushButton:disabled {
    color: #5d688a;
    background: #131829;
    border-color: #1f2740;
}
QPushButton#PrimaryButton {
    background: #7c5cff;
    color: #ffffff;
    border: 1px solid #7c5cff;
    font-weight: 750;
}
QPushButton#PrimaryButton:hover {
    background: #8e72ff;
    border-color: #8e72ff;
}
QPushButton#PrimaryButton:pressed {
    background: #6a49f2;
}
QPushButton#GhostButton {
    background: transparent;
    border: 1px solid transparent;
    color: #b8c0db;
    text-align: left;
    padding: 9px 12px;
}
QPushButton#GhostButton:hover {
    background: #1a2138;
    border-color: #252e4a;
}
QPushButton#DangerButton {
    background: transparent;
    border: 1px solid rgba(248,113,113,0.4);
    color: #f87171;
}
QPushButton#DangerButton:hover {
    background: rgba(248,113,113,0.12);
}
QPushButton#IconButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 6px;
    min-width: 34px;
    max-width: 40px;
}
QPushButton#IconButton:hover {
    background: #222b46;
    border-color: #2a3352;
}

/* ------------------------------------------------------------------ Entradas */
QLineEdit, QSpinBox, QPlainTextEdit, QTextEdit, QComboBox {
    background: #0e1322;
    border: 1px solid #2a3352;
    border-radius: 12px;
    padding: 10px 12px;
    color: #eef1f8;
    selection-background-color: #7c5cff;
    selection-color: #ffffff;
}
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {
    border: 1px solid #7c5cff;
}
QLineEdit::placeholder { color: #5d688a; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow {
    image: none;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid #8a95b3;
    margin-right: 10px;
}
QComboBox QAbstractItemView {
    background: #141a2e;
    border: 1px solid #2a3352;
    border-radius: 10px;
    selection-background-color: #7c5cff;
    color: #eef1f8;
    padding: 4px;
}

/* ----------------------------------------------------------------- Listas */
QListWidget {
    background: transparent;
    border: none;
    padding: 2px;
}
QListWidget::item {
    padding: 11px 12px;
    border-radius: 12px;
    margin: 2px 0;
    color: #c3cae0;
}
QListWidget::item:hover {
    background: #1a2138;
}
QListWidget::item:selected {
    background: rgba(124,92,255,0.16);
    color: #ffffff;
    border: 1px solid rgba(124,92,255,0.45);
}

QTreeWidget {
    background: transparent;
    border: none;
    padding: 4px;
    alternate-background-color: transparent;
}
QTreeWidget::item {
    padding: 9px 6px;
    border-radius: 10px;
    min-height: 30px;
    color: #c3cae0;
}
QTreeWidget::item:hover {
    background: #161d33;
}
QTreeWidget::item:selected {
    background: rgba(124,92,255,0.16);
    color: #ffffff;
}
QTreeWidget::branch {
    background: transparent;
}
QTreeWidget::branch:has-children:!has-siblings:closed,
QTreeWidget::branch:closed:has-children:has-siblings {
    image: none;
    border-image: none;
}
QTreeWidget::branch:open:has-children:!has-siblings,
QTreeWidget::branch:open:has-children:has-siblings {
    image: none;
    border-image: none;
}

QHeaderView::section {
    background: transparent;
    color: #5d688a;
    border: none;
    border-bottom: 1px solid #1f2740;
    padding: 10px 6px;
    font-weight: 750;
    font-size: 9pt;
}

/* ------------------------------------------------------------------- Cartões */
QFrame#Card {
    background: #141a2e;
    border: 1px solid #252e4a;
    border-radius: 18px;
}
QFrame#CardSoft {
    background: #11172a;
    border: 1px solid #1f2740;
    border-radius: 16px;
}
QFrame#Divider {
    background: #1f2740;
    max-height: 1px;
    border: none;
}
QFrame#HeroCard {
    background: #141a2e;
    border: 1px solid #2a3352;
    border-radius: 20px;
}

/* ------------------------------------------------------------------ Diálogos */
QDialog {
    background: #0f1424;
}
QDialog#PlayerDialog {
    background: #05070f;
}

/* ------------------------------------------------------------------- Sliders */
QSlider::groove:horizontal {
    height: 6px;
    background: #252e4a;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    background: #7c5cff;
    border: 2px solid #ffffff;
}
QSlider::handle:horizontal:hover {
    background: #8e72ff;
}
QSlider::sub-page:horizontal {
    background: #7c5cff;
    border-radius: 3px;
}

/* --------------------------------------------------------------- Scrollbars */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #2a3352;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #3a456b;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #2a3352;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ------------------------------------------------------------------- Progresso */
QProgressBar {
    background: #11172a;
    border: 1px solid #252e4a;
    border-radius: 8px;
    text-align: center;
    color: #c3cae0;
    height: 16px;
}
QProgressBar::chunk {
    background: #7c5cff;
    border-radius: 7px;
}

/* ----------------------------------------------------------------- Menu bar */
QMenuBar {
    background: #0f1424;
    color: #c3cae0;
    border-bottom: 1px solid #1f2740;
}
QMenuBar::item:selected {
    background: #1a2138;
    border-radius: 8px;
}
QMenu {
    background: #141a2e;
    border: 1px solid #2a3352;
    border-radius: 10px;
    padding: 6px;
    color: #e6e9f5;
}
QMenu::item {
    padding: 8px 22px;
    border-radius: 8px;
}
QMenu::item:selected {
    background: #7c5cff;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background: #252e4a;
    margin: 6px 4px;
}

/* ------------------------------------------------------------------ Tooltip */
QToolTip {
    background: #1a2138;
    color: #eef1f8;
    border: 1px solid #2a3352;
    border-radius: 8px;
    padding: 6px 10px;
}

/* ------------------------------------------------------------------- Checkbox */
QCheckBox { color: #c3cae0; spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px;
    border: 1px solid #2a3352;
    border-radius: 6px;
    background: #0e1322;
}
QCheckBox::indicator:checked {
    background: #7c5cff;
    border-color: #7c5cff;
}
"""
