from __future__ import annotations


DARK_STYLESHEET = """
QWidget {
    color: #DDE5EC;
    background: #10161C;
    font-family: "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 10pt;
}
QMainWindow, QDialog {
    background: #0C1217;
}
QMenuBar, QMenu, QToolBar, QStatusBar {
    background: #111920;
    border-color: #25323C;
}
QMenuBar::item:selected, QMenu::item:selected {
    background: #20333B;
}
QToolBar {
    spacing: 4px;
    padding: 4px;
    border: none;
    border-bottom: 1px solid #26343E;
}
QToolButton {
    border: 1px solid transparent;
    border-radius: 5px;
    padding: 5px;
}
QToolButton:hover {
    background: #1C2A32;
    border-color: #30444F;
}
QToolButton:checked {
    background: #183C3A;
    border-color: #4FC3A1;
}
QDockWidget {
    color: #EAF1F5;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background: #152029;
    border-bottom: 1px solid #2A3944;
    padding: 7px 9px;
    font-weight: 600;
}
QGroupBox {
    border: 1px solid #2A3944;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QPushButton {
    background: #18232B;
    border: 1px solid #30404B;
    border-radius: 5px;
    padding: 6px 10px;
}
QPushButton:hover {
    background: #20303A;
    border-color: #45606F;
}
QPushButton:pressed {
    background: #152027;
}
QPushButton:default {
    background: #2D9F88;
    color: #07130F;
    border-color: #4FC3A1;
    font-weight: 700;
}
QPushButton:disabled {
    color: #65727C;
    background: #141B21;
    border-color: #222D35;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {
    background: #0D141A;
    border: 1px solid #2A3944;
    border-radius: 4px;
    padding: 5px;
    selection-background-color: #2D9F88;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #4FC3A1;
}
QListWidget, QTreeWidget, QTableWidget {
    background: #0C1318;
    alternate-background-color: #111A21;
    border: 1px solid #27343E;
    gridline-color: #26343E;
    outline: none;
}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {
    background: #1D3B3B;
    color: #F2F7F9;
}
QHeaderView::section {
    background: #172129;
    border: none;
    border-right: 1px solid #2A3944;
    border-bottom: 1px solid #2A3944;
    padding: 6px;
    font-weight: 600;
}
QTabWidget::pane {
    border: 1px solid #2A3944;
}
QTabBar::tab {
    background: #121B22;
    border: 1px solid #26343E;
    padding: 6px 10px;
}
QTabBar::tab:selected {
    background: #1B2B32;
    border-bottom-color: #4FC3A1;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #0D141A;
    border: none;
    width: 12px;
    height: 12px;
}
QScrollBar::handle {
    background: #34444F;
    border-radius: 5px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover {
    background: #4A5D69;
}
QProgressBar {
    border: 1px solid #2A3944;
    border-radius: 4px;
    text-align: center;
    background: #0D141A;
}
QProgressBar::chunk {
    background: #4FC3A1;
    border-radius: 3px;
}
#dangerButton {
    background: #8D2D39;
    border-color: #E35D6A;
    color: #FFFFFF;
    font-weight: 700;
}
#dangerButton:hover {
    background: #A63846;
}
#workspaceFrame {
    border: 1px solid #2A3944;
    background: #0A1015;
}
#statusGood {
    color: #4FC3A1;
}
#statusWarning {
    color: #E7B55C;
}
#statusBad {
    color: #E35D6A;
}
"""


def apply_dark_theme(application: object) -> None:
    application.setStyle("Fusion")
    application.setStyleSheet(DARK_STYLESHEET)
