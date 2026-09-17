"""
Тёмная тема для Dahua Exporter.

Сделана вручную (QSS — это подмножество CSS), чтобы не тянуть лишние
зависимости вроде qdarktheme. Палитра — глубокий синевато-серый с
акцентным голубым, в духе современных десктопных приложений.
"""

from __future__ import annotations

# --- палитра --------------------------------------------------------------

BG        = "#0f1419"   # фон окна
BG_PANEL  = "#161b22"   # панели, карточки
BG_INPUT  = "#1c2430"   # поля ввода
BG_HOVER  = "#232d3b"   # наведение
BORDER    = "#2a3544"   # границы
BORDER_HL = "#3a4a5e"   # границы при фокусе

TEXT      = "#e6edf3"   # основной текст
TEXT_DIM  = "#8b98a8"   # вторичный текст
TEXT_MUTE = "#5f6b7a"   # подсказки

ACCENT       = "#2f81f7"   # кнопки, выделение
ACCENT_HOVER = "#4c94ff"
ACCENT_PRESS = "#1f6feb"

SUCCESS = "#3fb950"
WARNING = "#d29922"
DANGER  = "#f85149"


STYLESHEET = f"""
/* ========== общее ========== */
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {BG};
}}

/* ========== вкладки ========== */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    background-color: {BG_PANEL};
    top: -1px;
}}

QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 10px 22px;
    margin-right: 4px;
    border: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
}}

QTabBar::tab:hover {{
    color: {TEXT};
    background: {BG_HOVER};
}}

QTabBar::tab:selected {{
    color: {TEXT};
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-bottom: none;
}}

/* ========== группы (карточки) ========== */
QGroupBox {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 18px 14px 14px 14px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.6px;
}}

/* ========== поля ввода ========== */
QLineEdit, QDateTimeEdit, QComboBox, QSpinBox {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    min-height: 18px;
}}

QLineEdit:hover, QDateTimeEdit:hover, QComboBox:hover, QSpinBox:hover {{
    border-color: {BORDER_HL};
}}

QLineEdit:focus, QDateTimeEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}

QLineEdit:disabled, QComboBox:disabled {{
    background-color: {BG};
    color: {TEXT_MUTE};
}}

QLineEdit[echoMode="2"] {{
    letter-spacing: 2px;
}}

/* выпадающий список */
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 10px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_HL};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}

QComboBox QAbstractItemView::item {{
    padding: 7px 10px;
    border-radius: 6px;
    min-height: 20px;
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: {BG_HOVER};
}}

/* календарь */
QCalendarWidget QWidget {{
    background-color: {BG_PANEL};
}}
QCalendarWidget QAbstractItemView:enabled {{
    background-color: {BG_PANEL};
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}

/* ========== кнопки ========== */
QPushButton {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 9px 18px;
    color: {TEXT};
    font-weight: 600;
    min-height: 18px;
}}

QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: {BORDER_HL};
}}

QPushButton:pressed {{
    background-color: {BG};
}}

QPushButton:disabled {{
    background-color: {BG};
    color: {TEXT_MUTE};
    border-color: {BORDER};
}}

/* главная кнопка (подключение, выгрузка) */
QPushButton#primary {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    color: #ffffff;
}}

QPushButton#primary:hover {{
    background-color: {ACCENT_HOVER};
    border-color: {ACCENT_HOVER};
}}

QPushButton#primary:pressed {{
    background-color: {ACCENT_PRESS};
}}

QPushButton#primary:disabled {{
    background-color: {BORDER};
    border-color: {BORDER};
    color: {TEXT_MUTE};
}}

/* опасная кнопка (отмена) */
QPushButton#danger {{
    background-color: transparent;
    border: 1px solid {DANGER};
    color: {DANGER};
}}

QPushButton#danger:hover {{
    background-color: {DANGER};
    color: #ffffff;
}}

QPushButton#danger:disabled {{
    border-color: {BORDER};
    color: {TEXT_MUTE};
}}

/* ========== прогресс ========== */
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    height: 22px;
    text-align: center;
    color: {TEXT};
    font-weight: 600;
    font-size: 11px;
}}

QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 7px;
}}

/* ========== чекбоксы ========== */
QCheckBox {{
    spacing: 8px;
    color: {TEXT};
}}

QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border: 1px solid {BORDER_HL};
    border-radius: 5px;
    background-color: {BG_INPUT};
}}

QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}

QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}

/* ========== метки ========== */
QLabel {{
    background: transparent;
}}

QLabel#hint {{
    color: {TEXT_MUTE};
    font-size: 11px;
}}

QLabel#status {{
    color: {TEXT_DIM};
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px 14px;
}}

QLabel#success {{
    color: {SUCCESS};
}}

QLabel#warning {{
    color: {WARNING};
}}

QLabel#danger {{
    color: {DANGER};
}}

/* ========== полосы прокрутки ========== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {BORDER_HL};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: {TEXT_MUTE};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}

QScrollBar::handle:horizontal {{
    background: {BORDER_HL};
    border-radius: 5px;
    min-width: 30px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

/* ========== прочее ========== */
QToolTip {{
    background-color: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER_HL};
    border-radius: 6px;
    padding: 6px 9px;
}}

QMessageBox {{
    background-color: {BG_PANEL};
}}

QMessageBox QLabel {{
    color: {TEXT};
}}

/* ========== таблицы и списки ========== */
QTableWidget, QListWidget, QTreeWidget {{
    background-color: {BG_INPUT};
    alternate-background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    gridline-color: {BORDER};
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
    outline: none;
}}

QHeaderView::section {{
    background-color: {BG_PANEL};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 10px;
    font-size: 11px;
    font-weight: 700;
}}

QTableWidget::item, QListWidget::item, QTreeWidget::item {{
    padding: 7px 8px;
    border-bottom: 1px solid {BORDER};
}}

QTableWidget::item:hover, QListWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {BG_HOVER};
}}

/* пустые/неактивные состояния */
QLabel#emptyState {{
    color: {TEXT_MUTE};
    padding: 24px;
    font-size: 13px;
}}

QFrame#separator {{
    background-color: {BORDER};
    min-height: 1px;
    max-height: 1px;
}}
"""
