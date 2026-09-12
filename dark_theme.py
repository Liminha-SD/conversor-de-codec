"""
Dark Blue Theme para PySide6
Paleta: fundo preto (#0F0F0F) + azul (#019DEA)

Uso:
    from dark_theme import apply_theme, COLORS
    apply_theme(app)          # aplica no QApplication inteiro
    apply_theme(widget)       # ou em um widget específico
"""

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtGui import QFont


COLORS = {
    "bg_main":       "#0F0F0F",
    "bg_card":       "#121212",
    "bg_input":      "#080808",
    "accent":        "#019DEA",
    "accent_hover":  "#00B4FF",
    "accent_press":  "#007ACC",
    "border":        "#1A1A1A",
    "border_mid":    "#2A2A2A",
    "text":          "#FFFFFF",
    "text_dim":      "#AAAAAA",
    "danger_bg":     "#2A1515",
    "danger_text":   "#FF6666",
    "danger_border": "#4A2525",
    "secondary_bg":  "#202020",
    "secondary_border": "#303030",
}


STYLESHEET = f"""
    QMainWindow, QDialog {{
        background-color: {COLORS['bg_main']};
    }}
    QWidget {{
        background-color: {COLORS['bg_main']};
        color: {COLORS['text']};
        font-family: 'Segoe UI', 'Noto Sans', Arial, sans-serif;
        font-size: 15px;
    }}
    QGroupBox {{
        border: 1px solid {COLORS['border_mid']};
        border-radius: 6px;
        margin-top: 10px;
        padding-top: 15px;
        font-weight: bold;
        background-color: {COLORS['bg_card']};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top center;
        padding: 0 10px;
        color: {COLORS['accent']};
        background-color: {COLORS['bg_main']};
    }}
    QPushButton {{
        background-color: {COLORS['accent']};
        color: white;
        border: none;
        padding: 6px 12px;
        border-radius: 4px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: {COLORS['accent_hover']};
    }}
    QPushButton:pressed {{
        background-color: {COLORS['accent_press']};
    }}
    QPushButton:disabled {{
        background-color: #1A3A4A;
        color: #557788;
    }}
    QPushButton#secondary {{
        background-color: {COLORS['secondary_bg']};
        color: {COLORS['text']};
        border: 1px solid {COLORS['secondary_border']};
        font-size: 13px;
        padding: 5px 10px;
    }}
    QPushButton#secondary:hover {{
        background-color: #2A2A2A;
    }}
    QPushButton#danger {{
        background-color: {COLORS['danger_bg']};
        color: {COLORS['danger_text']};
        border: 1px solid {COLORS['danger_border']};
        font-size: 13px;
        padding: 5px 10px;
    }}
    QPushButton#danger:hover {{
        background-color: #3A1A1A;
    }}
    QListWidget {{
        background-color: {COLORS['bg_input']};
        border: 1px solid {COLORS['border']};
        border-radius: 4px;
        padding: 2px;
        outline: none;
    }}
    QListWidget::item {{
        padding: 6px;
        border-bottom: 1px solid {COLORS['bg_card']};
    }}
    QListWidget::item:selected {{
        background-color: #151515;
        color: {COLORS['accent']};
    }}
    QLineEdit {{
        background-color: {COLORS['bg_input']};
        border: 1px solid {COLORS['border']};
        border-radius: 4px;
        padding: 6px;
        color: {COLORS['text']};
    }}
    QLineEdit:focus {{
        border: 1px solid {COLORS['accent']};
    }}
    QTextEdit, QPlainTextEdit {{
        background-color: {COLORS['bg_input']};
        border: 1px solid {COLORS['border']};
        border-radius: 4px;
        padding: 6px;
        color: {COLORS['text']};
    }}
    QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {COLORS['accent']};
    }}
    QComboBox {{
        background-color: {COLORS['bg_input']};
        border: 1px solid {COLORS['border']};
        border-radius: 4px;
        padding: 5px 8px;
        color: {COLORS['text']};
    }}
    QComboBox:focus {{
        border: 1px solid {COLORS['accent']};
    }}
    QComboBox::drop-down {{
        border: none;
        padding-right: 8px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {COLORS['bg_card']};
        border: 1px solid {COLORS['border_mid']};
        selection-background-color: #1A3A4A;
        selection-color: {COLORS['accent']};
    }}
    QSpinBox, QDoubleSpinBox {{
        background-color: {COLORS['bg_input']};
        border: 1px solid {COLORS['border']};
        border-radius: 4px;
        padding: 5px;
        color: {COLORS['text']};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {COLORS['accent']};
    }}
    QProgressBar {{
        background-color: {COLORS['bg_input']};
        border: 1px solid {COLORS['border']};
        border-radius: 6px;
        text-align: center;
        height: 18px;
        font-weight: bold;
        font-size: 13px;
    }}
    QProgressBar::chunk {{
        background-color: {COLORS['accent']};
        border-radius: 6px;
    }}
    QCheckBox {{
        spacing: 8px;
        padding: 2px;
    }}
    QCheckBox::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid #333333;
        border-radius: 3px;
        background-color: {COLORS['bg_input']};
    }}
    QCheckBox::indicator:checked {{
        background-color: {COLORS['accent']};
        border: 1px solid {COLORS['accent']};
    }}
    QRadioButton {{
        spacing: 8px;
    }}
    QRadioButton::indicator {{
        width: 14px;
        height: 14px;
        border: 1px solid #333333;
        border-radius: 7px;
        background-color: {COLORS['bg_input']};
    }}
    QRadioButton::indicator:checked {{
        background-color: {COLORS['accent']};
        border: 1px solid {COLORS['accent']};
    }}
    QSlider::groove:horizontal {{
        height: 4px;
        background: {COLORS['border_mid']};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {COLORS['accent']};
        width: 14px;
        height: 14px;
        margin: -5px 0;
        border-radius: 7px;
    }}
    QSlider::sub-page:horizontal {{
        background: {COLORS['accent']};
        border-radius: 2px;
    }}
    QScrollBar:vertical {{
        background: {COLORS['bg_card']};
        width: 8px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: #333333;
        border-radius: 4px;
        min-height: 20px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #444444;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: {COLORS['bg_card']};
        height: 8px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: #333333;
        border-radius: 4px;
        min-width: 20px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: #444444;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}
    QTabWidget::pane {{
        border: 1px solid {COLORS['border_mid']};
        background-color: {COLORS['bg_card']};
        border-radius: 4px;
    }}
    QTabBar::tab {{
        background-color: {COLORS['bg_main']};
        color: {COLORS['text_dim']};
        padding: 8px 16px;
        border: 1px solid {COLORS['border_mid']};
        border-bottom: none;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
    }}
    QTabBar::tab:selected {{
        background-color: {COLORS['bg_card']};
        color: {COLORS['accent']};
        font-weight: bold;
    }}
    QTableWidget {{
        background-color: {COLORS['bg_input']};
        border: 1px solid {COLORS['border']};
        gridline-color: {COLORS['border_mid']};
        color: {COLORS['text']};
    }}
    QTableWidget::item:selected {{
        background-color: #1A3A4A;
        color: {COLORS['accent']};
    }}
    QHeaderView::section {{
        background-color: {COLORS['bg_card']};
        color: {COLORS['accent']};
        border: 1px solid {COLORS['border_mid']};
        padding: 5px;
        font-weight: bold;
    }}
    QMenuBar {{
        background-color: {COLORS['bg_main']};
        color: {COLORS['text']};
    }}
    QMenuBar::item:selected {{
        background-color: {COLORS['bg_card']};
        color: {COLORS['accent']};
    }}
    QMenu {{
        background-color: {COLORS['bg_card']};
        border: 1px solid {COLORS['border_mid']};
        color: {COLORS['text']};
    }}
    QMenu::item:selected {{
        background-color: #1A3A4A;
        color: {COLORS['accent']};
    }}
    QToolTip {{
        background-color: {COLORS['bg_card']};
        color: {COLORS['text']};
        border: 1px solid {COLORS['accent']};
        border-radius: 4px;
        padding: 4px;
    }}
    QStatusBar {{
        background-color: {COLORS['bg_card']};
        color: {COLORS['text_dim']};
        font-size: 13px;
    }}
    QLabel#title {{
        color: {COLORS['accent']};
        font-size: 22px;
        font-weight: bold;
    }}
    QLabel#subtitle {{
        color: {COLORS['text_dim']};
        font-size: 13px;
    }}
    QLabel#status {{
        color: {COLORS['accent']};
        font-weight: bold;
        font-size: 13px;
    }}
    QFrame#separator {{
        background-color: {COLORS['border_mid']};
        max-height: 1px;
    }}
"""


def apply_theme(target: QApplication | QWidget) -> None:
    target.setStyleSheet(STYLESHEET)


def set_default_font(app: QApplication, size: int = 15) -> None:
    font = QFont("Segoe UI", size)
    app.setFont(font)