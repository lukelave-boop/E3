from __future__ import annotations

# Shared scene colors for the high-contrast drafting surface.  Workspace code
# may import this mapping instead of duplicating chrome-dependent color values.
DRAFTING_COLORS = {
    "outside": "#292929",
    "bed": "#FAFAFA",
    "minor_grid": "#E2E2E2",
    "major_grid": "#C7C7C7",
    "ruler_background": "#F2F2F2",
    "ruler_border": "#B8B8B8",
    "ruler_text": "#555555",
    "selection": "#00A58E",
}


# Compact neutral chrome around a light drafting surface.  Object-name
# selectors are intentionally stable because desktop widgets and regression
# tests use them as accessibility/layout contracts.
DARK_STYLESHEET = """
QWidget {
    color: #E6E6E6;
    background: #202020;
    font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 9pt;
}

QMainWindow, QDialog {
    background: #1C1C1C;
}

/* Menus and global tool chrome */
QMenuBar {
    background: #181818;
    border-bottom: 1px solid #383838;
    padding: 0;
    spacing: 0;
}
QMenuBar::item {
    background: transparent;
    padding: 3px 7px;
}
QMenuBar::item:selected, QMenuBar::item:pressed {
    background: #3A3A3A;
}
QMenu {
    background: #242424;
    border: 1px solid #555555;
    padding: 2px;
}
QMenu::item {
    padding: 4px 26px 4px 7px;
}
QMenu::item:selected {
    background: #3D665F;
    color: #FFFFFF;
}
QMenu::item:disabled {
    color: #777777;
}
QMenu::separator {
    height: 1px;
    background: #4A4A4A;
    margin: 3px 5px;
}

QToolBar {
    background: #242424;
    border: none;
    border-bottom: 1px solid #3A3A3A;
    spacing: 1px;
    padding: 2px;
}
QToolBar::separator {
    background: #4A4A4A;
    width: 1px;
    height: 1px;
    margin: 3px;
}
QToolBar#drawingToolbar {
    border-right: 1px solid #3F3F3F;
    border-bottom: none;
    padding: 2px 1px;
}
QToolBar#contextToolbar {
    background: #292929;
    border-top: 1px solid #414141;
    border-bottom: 1px solid #484848;
    padding: 1px 3px;
}
QToolBar#safetyToolbar {
    background: #252525;
    border-bottom: 1px solid #454545;
    padding: 1px 3px;
}
QToolBar#layerPaletteToolbar {
    background: #222222;
    border-top: 1px solid #484848;
    border-bottom: none;
    padding: 1px 3px;
}
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 2px 4px;
    min-width: 18px;
    min-height: 18px;
}
QToolButton:hover {
    background: #393939;
    border-color: #565656;
}
QToolButton:pressed {
    background: #171717;
    border-color: #686868;
}
QToolButton:checked {
    background: #375D56;
    border-color: #62C8B5;
    color: #FFFFFF;
}
QToolButton:disabled {
    color: #6F6F6F;
}
QToolButton::menu-indicator {
    width: 7px;
    height: 7px;
}

/* Docking and inspectors */
QDockWidget {
    color: #F2F2F2;
    background: #1E1E1E;
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background: #2B2B2B;
    border-top: 1px solid #404040;
    border-bottom: 1px solid #121212;
    padding: 3px 6px;
    font-weight: 600;
}
QDockWidget#layersDock::title {
    border-top: 3px solid #D05A35;
}
QDockWidget#inspectorDock::title {
    border-top: 3px solid #7E2D33;
}
QMainWindow::separator {
    background: #555555;
    width: 3px;
    height: 3px;
}
QMainWindow::separator:hover {
    background: #68C8B5;
}
QDockWidget::close-button, QDockWidget::float-button {
    background: transparent;
    border: none;
    padding: 1px;
}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {
    background: #555555;
}

QWidget#controlPanel {
    background: #1E1E1E;
}
QWidget#inspectorPage {
    background: #1E1E1E;
}
QScrollArea#inspectorScroll,
QScrollArea#inspectorScroll > QWidget,
QScrollArea#inspectorScroll QWidget#qt_scrollarea_viewport {
    background: #1E1E1E;
    border: none;
}
QTabWidget#inspectorTabs {
    background: #1C1C1C;
}
QTabWidget#inspectorTabs::pane {
    background: #1E1E1E;
    border: 1px solid #414141;
    border-top: none;
}
QTabBar#inspectorTabBar {
    background: #181818;
}
QTabBar#inspectorTabBar::tab,
QTabBar::tab {
    background: #242424;
    border: 1px solid #414141;
    border-bottom: 2px solid transparent;
    padding: 4px 7px;
    min-height: 16px;
}
QTabBar#inspectorTabBar::tab:hover,
QTabBar::tab:hover {
    background: #333333;
}
QTabBar#inspectorTabBar::tab:selected,
QTabBar::tab:selected {
    background: #303030;
    border-bottom-color: #58C7B2;
    color: #FFFFFF;
}
QTabWidget::pane {
    border: 1px solid #414141;
}

/* Compact forms */
QGroupBox {
    background: #1D1D1D;
    border: 1px solid #414141;
    border-radius: 2px;
    margin-top: 9px;
    padding-top: 5px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 6px;
    padding: 0 3px;
    color: #EAEAEA;
}
QLabel#panelHeading {
    color: #FFFFFF;
    font-size: 10pt;
    font-weight: 700;
}
QLabel#mutedLabel {
    color: #A8A8A8;
}
QLabel#warningLabel {
    color: #FFD689;
    background: #352A19;
    border: 1px solid #765A27;
    border-radius: 2px;
    padding: 5px;
}
QLabel#statusCard {
    background: #181818;
    border: 1px solid #414141;
    border-radius: 2px;
    padding: 5px;
}

QPushButton {
    background: #333333;
    border: 1px solid #555555;
    border-radius: 2px;
    padding: 3px 7px;
    min-height: 20px;
}
QPushButton:hover {
    background: #414141;
    border-color: #707070;
}
QPushButton:pressed {
    background: #222222;
    border-color: #777777;
}
QPushButton:default {
    background: #328F7E;
    color: #FFFFFF;
    border-color: #66CEBA;
}
QPushButton#primaryActionButton {
    font-weight: 700;
}
QPushButton:disabled {
    color: #747474;
    background: #262626;
    border-color: #393939;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {
    color: #F3F3F3;
    background: #151515;
    border: 1px solid #505050;
    border-radius: 1px;
    padding: 2px 4px;
    selection-color: #FFFFFF;
    selection-background-color: #287B6C;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    min-height: 20px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #63CDB9;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    color: #747474;
    background: #242424;
    border-color: #373737;
}
QComboBox {
    padding-right: 17px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 16px;
    border-left: 1px solid #444444;
}
QComboBox QAbstractItemView {
    color: #F0F0F0;
    background: #242424;
    border: 1px solid #555555;
    selection-background-color: #3D665F;
    outline: none;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    width: 14px;
    background: #292929;
    border-left: 1px solid #444444;
}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background: #424242;
}
QCheckBox, QRadioButton {
    spacing: 7px;
}
QCheckBox::indicator {
    width: 30px;
    height: 16px;
    border: 1px solid #777777;
    border-radius: 8px;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #E8E8E8, stop: 0.42 #E8E8E8,
        stop: 0.43 #4A4A4A, stop: 1 #4A4A4A
    );
}
QCheckBox::indicator:hover {
    border-color: #B8C0C8;
}
QCheckBox::indicator:checked {
    border-color: #55D6A8;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #20C978, stop: 0.57 #20C978,
        stop: 0.58 #F4F4F4, stop: 1 #F4F4F4
    );
}
QCheckBox::indicator:disabled {
    border-color: #4A4A4A;
    background: #333333;
}
QRadioButton::indicator {
    width: 13px;
    height: 13px;
}

/* Lists, operation tables, and headers */
QListWidget, QTreeWidget, QTableWidget, QTableView, QTreeView {
    background: #181818;
    alternate-background-color: #202020;
    border: 1px solid #424242;
    gridline-color: #383838;
    outline: none;
}
QListWidget::item, QTreeWidget::item, QTableWidget::item,
QTableView::item, QTreeView::item {
    padding: 2px;
}
QListWidget::item:selected, QTreeWidget::item:selected,
QTableWidget::item:selected, QTableView::item:selected, QTreeView::item:selected {
    background: #335C55;
    color: #FFFFFF;
}
QHeaderView::section {
    background: #2A2A2A;
    border: none;
    border-right: 1px solid #434343;
    border-bottom: 1px solid #4A4A4A;
    padding: 3px 4px;
    font-weight: 600;
}

/* Selection properties */
QWidget#contextPropertyBar, QWidget#contextPropertyEditor,
QWidget#contextCenterXField, QWidget#contextCenterYField,
QWidget#contextWidthField, QWidget#contextHeightField,
QWidget#contextWidthscaleField, QWidget#contextHeightscaleField,
QWidget#contextRotationField, QWidget#contextCornerradiusField,
QWidget#contextTransformunitsField {
    background: transparent;
}
QLabel#contextSelectionSummary {
    color: #FFFFFF;
    font-weight: 600;
}
QLabel#contextPropertyLabel {
    color: #B9B9B9;
    font-weight: 600;
}
QLabel#contextCompactUnits {
    color: #E2E2E2;
    background: #343434;
    border: 1px solid #555555;
    border-radius: 1px;
    padding: 1px 3px;
    font-weight: 600;
}
QFrame#contextPropertySeparator {
    color: #555555;
}
QToolBar#contextToolbar QDoubleSpinBox {
    min-height: 19px;
    padding-top: 1px;
    padding-bottom: 1px;
}

/* Runtime authority remains conspicuous without consuming a full panel. */
QWidget#runtimeSafetyStrip {
    background: #252525;
}
QLabel#runtimeStripHeading {
    color: #B8B8B8;
    font-weight: 600;
}
QLabel#statusGood, QLabel#statusWarning, QLabel#statusBad {
    border: 1px solid #555555;
    border-radius: 2px;
    padding: 2px 6px;
    font-weight: 700;
}
QLabel#statusGood {
    color: #74E0C7;
    background: #18352F;
    border-color: #3E756A;
}
QLabel#statusWarning {
    color: #FFD67A;
    background: #3B301A;
    border-color: #7B632A;
}
QLabel#statusBad {
    color: #FF9DA6;
    background: #431F25;
    border-color: #8D3A45;
}
QPushButton#dangerButton, #dangerButton {
    color: #FFFFFF;
    background: #A92F3E;
    border: 1px solid #F06270;
    border-radius: 2px;
    font-weight: 700;
    padding: 3px 9px;
}
QPushButton#dangerButton:hover, #dangerButton:hover {
    background: #C23949;
}
QPushButton#dangerButton:pressed, #dangerButton:pressed {
    background: #7E222E;
}

/* Bottom operation palette and status line */
QLabel#paletteHeading {
    color: #D8D8D8;
    font-weight: 600;
    padding: 0 4px;
}
QScrollArea#layerPaletteScroll,
QWidget#layerPaletteButtonHost {
    background: #222222;
    border: none;
}
QStatusBar {
    color: #E1E1E1;
    background: #181818;
    border-top: 1px solid #444444;
    min-height: 18px;
}
QStatusBar::item {
    border: none;
}
QStatusBar QLabel {
    background: transparent;
    padding: 0 4px;
}
QLabel#directEditStatus {
    color: #7ADBC8;
    border-right: 1px solid #444444;
    padding: 0 7px 0 3px;
    font-weight: 600;
}

/* Scrollbars are deliberately thin but retain a usable handle target. */
QScrollBar:vertical, QScrollBar:horizontal {
    background: #191919;
    border: none;
    width: 10px;
    height: 10px;
    margin: 0;
}
QScrollBar::handle {
    background: #555555;
    border-radius: 3px;
    min-height: 24px;
    min-width: 24px;
}
QScrollBar::handle:hover {
    background: #747474;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}

QProgressBar {
    color: #F2F2F2;
    background: #151515;
    border: 1px solid #474747;
    border-radius: 1px;
    text-align: center;
    min-height: 16px;
}
QProgressBar::chunk {
    background: #3DAA96;
}

QLabel#testImageBadge {
    color: #FFF3D6;
    background: #754E12;
    border: 1px solid #F0BC54;
    border-radius: 2px;
    padding: 4px 7px;
    font-weight: 700;
}
QGraphicsView#workspaceFrame, #workspaceFrame {
    background: #292929;
    border: 1px solid #555555;
}
QToolTip {
    color: #FFFFFF;
    background: #303030;
    border: 1px solid #777777;
    padding: 3px 5px;
}
"""


def apply_dark_theme(application: object) -> None:
    """Apply the compact desktop theme while preserving OS DPI scaling."""

    application.setStyle("Fusion")
    application.setStyleSheet(DARK_STYLESHEET)


__all__ = ["DARK_STYLESHEET", "DRAFTING_COLORS", "apply_dark_theme"]
