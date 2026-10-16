from __future__ import annotations

from pathlib import Path

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

# Keep the camera legible by default while leaving the drafting grid and vector
# geometry visible over it. CameraPanel and WorkspaceView share this value so
# the initial slider position always matches the rendered image.
DEFAULT_CAMERA_OVERLAY_OPACITY = 0.70


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
    padding: 5px 28px 5px 24px;
}
QMenu::item:selected {
    background: #3D665F;
    color: #FFFFFF;
}
QMenu::item:disabled {
    color: #999FA5;
}
QMenu::indicator {
    width: 14px;
    height: 14px;
    left: 5px;
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
QToolBar#drawingToolbar QToolButton {
    min-width: 94px;
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
    background: #2B2D30;
    border: 1px solid #53585D;
    border-radius: 2px;
    padding: 2px 4px;
    min-width: 18px;
    min-height: 18px;
}
QToolButton:hover {
    background: #41474C;
    border-color: #929BA4;
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
QToolButton:focus {
    border-color: #82E3D0;
}
QToolButton:disabled {
    color: #91969C;
    background: #292B2E;
    border-color: #3E4246;
}
QToolButton::menu-indicator, QPushButton::menu-indicator {
    image: url("__THEME_ASSETS__/control-chevron-down.svg");
    width: 10px;
    height: 6px;
}
QToolButton::menu-indicator:disabled, QPushButton::menu-indicator:disabled {
    image: url("__THEME_ASSETS__/control-chevron-down-disabled.svg");
}
QToolButton#inspectorPanelMenuButton {
    padding-right: 17px;
}
QToolButton#inspectorPanelMenuButton::menu-indicator {
    subcontrol-origin: padding;
    subcontrol-position: right center;
    right: 4px;
}
QToolButton#saveLayerProfileButton[profileChanged="true"] {
    background: #237A43;
    color: #FFFFFF;
    border-color: #62C881;
}
QToolButton#saveLayerProfileButton[profileChanged="true"]:hover {
    background: #2D9152;
}
QToolButton#saveLayerProfileButton[profileChanged="true"]:pressed {
    background: #195C32;
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
    background: #292C30;
    border: 1px solid #5E656C;
    border-bottom: 2px solid #5E656C;
    padding: 4px 7px;
    min-height: 16px;
}
QTabBar#inspectorTabBar::tab:hover,
QTabBar::tab:hover {
    background: #333333;
}
QTabBar#inspectorTabBar::tab:selected,
QTabBar::tab:selected {
    background: #294D46;
    border-bottom-color: #82E3D0;
    color: #FFFFFF;
    font-weight: 600;
}
QTabBar::tab:disabled {
    color: #91969C;
    background: #26282B;
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
QLabel#mutedLabel, QLabel[role="muted"] {
    color: #BBC2C9;
    background: transparent;
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
    background: #373C42;
    border: 1px solid #707982;
    border-radius: 3px;
    padding: 3px 7px;
    min-height: 20px;
}
QPushButton:hover {
    background: #485159;
    border-color: #A5B2BD;
}
QPushButton:pressed {
    background: #222222;
    border-color: #777777;
}
QPushButton:focus {
    border-color: #82E3D0;
}
QPushButton:checked {
    background: #2A5C50;
    border-color: #82E3D0;
    color: #FFFFFF;
}
QPushButton:default, QPushButton[role="primary"] {
    background: #287D6D;
    color: #FFFFFF;
    border-color: #66CEBA;
}
QPushButton#primaryActionButton, QPushButton[role="primary"] {
    font-weight: 700;
}
QPushButton:default:hover, QPushButton[role="primary"]:hover {
    background: #329583;
}
QPushButton:disabled, QPushButton[role="primary"]:disabled {
    color: #999FA5;
    background: #292B2E;
    border-color: #42474C;
}

/* Answer semantics take precedence over the generic default-button accent. */
QPushButton[confirmationAnswer="yes"] {
    background: #237A43;
    color: #FFFFFF;
    border-color: #62C881;
}
QPushButton[confirmationAnswer="yes"]:hover { background: #2D9152; }
QPushButton[confirmationAnswer="yes"]:pressed { background: #195C32; }
QPushButton[confirmationAnswer="no"] {
    background: #A92F3E;
    color: #FFFFFF;
    border-color: #F06270;
}
QPushButton[confirmationAnswer="no"]:hover { background: #C23949; }
QPushButton[confirmationAnswer="no"]:pressed { background: #7E222E; }
QPushButton[confirmationAnswer="cancel"] {
    background: #444444;
    color: #FFFFFF;
    border-color: #888888;
}
QPushButton[confirmationAnswer="cancel"]:hover { background: #555555; }
QPushButton[confirmationAnswer="cancel"]:pressed { background: #333333; }
QPushButton[confirmationAnswer="yes"]:default,
QPushButton[confirmationAnswer="no"]:default,
QPushButton[confirmationAnswer="cancel"]:default,
QPushButton[confirmationAnswer="yes"]:focus,
QPushButton[confirmationAnswer="no"]:focus,
QPushButton[confirmationAnswer="cancel"]:focus {
    border: 2px solid #FFFFFF;
    padding: 2px 6px;
}
QPushButton[confirmationAnswer="yes"]:disabled,
QPushButton[confirmationAnswer="no"]:disabled,
QPushButton[confirmationAnswer="cancel"]:disabled {
    color: #B0B0B0;
    border-color: #666666;
}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {
    color: #F3F3F3;
    background: #191C20;
    border: 1px solid #737D87;
    border-radius: 3px;
    padding: 2px 4px;
    selection-color: #FFFFFF;
    selection-background-color: #287B6C;
}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    min-height: 20px;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover,
QTextEdit:hover, QPlainTextEdit:hover {
    border-color: #A5B2BD;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #82E3D0;
    background: #1D2728;
}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {
    color: #999FA5;
    background: #292B2E;
    border-color: #42474C;
}
QComboBox {
    padding-right: 28px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    background: #3A424A;
    border-left: 1px solid #737D87;
    border-top-right-radius: 2px;
    border-bottom-right-radius: 2px;
}
QComboBox::drop-down:hover, QComboBox::drop-down:on {
    background: #50616A;
}
QComboBox::drop-down:disabled {
    background: #30353A;
    border-left-color: #42474C;
}
QComboBox::down-arrow {
    image: url("__THEME_ASSETS__/control-chevron-down.svg");
    width: 10px;
    height: 6px;
}
QComboBox::down-arrow:disabled {
    image: url("__THEME_ASSETS__/control-chevron-down-disabled.svg");
}
QComboBox QAbstractItemView {
    color: #F0F0F0;
    background: #242A30;
    border: 1px solid #87949F;
    selection-color: #FFFFFF;
    selection-background-color: #386B60;
    outline: none;
}
QComboBox QAbstractItemView::item {
    padding: 4px 7px;
    min-height: 20px;
}
QAbstractSpinBox {
    padding-right: 24px;
}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border;
    width: 21px;
    background: #3A424A;
    border-left: 1px solid #737D87;
}
QAbstractSpinBox::up-button {
    subcontrol-position: top right;
    border-bottom: 1px solid #737D87;
    border-top-right-radius: 3px;
}
QAbstractSpinBox::down-button {
    subcontrol-position: bottom right;
    border-bottom-right-radius: 3px;
}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {
    background: #50616A;
}
QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {
    background: #2A5C50;
}
QAbstractSpinBox::up-button:disabled, QAbstractSpinBox::down-button:disabled {
    background: #30353A;
    border-color: #42474C;
}
QAbstractSpinBox::up-arrow {
    image: url("__THEME_ASSETS__/control-chevron-up.svg");
    width: 10px;
    height: 6px;
}
QAbstractSpinBox::down-arrow {
    image: url("__THEME_ASSETS__/control-chevron-down.svg");
    width: 10px;
    height: 6px;
}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off {
    image: url("__THEME_ASSETS__/control-chevron-up-disabled.svg");
}
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off {
    image: url("__THEME_ASSETS__/control-chevron-down-disabled.svg");
}
/* The transform toolbar keeps its intentionally compact numeric fields. */
QToolBar#contextToolbar QAbstractSpinBox {
    padding-right: 17px;
}
QToolBar#contextToolbar QAbstractSpinBox::up-button,
QToolBar#contextToolbar QAbstractSpinBox::down-button {
    width: 14px;
}
QCheckBox, QRadioButton {
    spacing: 7px;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 2px;
}
QCheckBox:hover, QRadioButton:hover {
    background: #2C3539;
}
QCheckBox:focus, QRadioButton:focus {
    border-color: #82E3D0;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: #999FA5;
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
QCheckBox::indicator:checked:disabled {
    border-color: #738781;
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 0,
        stop: 0 #47665D, stop: 0.57 #47665D,
        stop: 0.58 #ADB8B4, stop: 1 #ADB8B4
    );
}
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #929DA7;
    border-radius: 9px;
    background: #191C20;
}
QRadioButton::indicator:checked {
    background: qradialgradient(
        cx: 0.5, cy: 0.5, radius: 0.5,
        fx: 0.5, fy: 0.5,
        stop: 0 #82E3D0, stop: 0.5 #82E3D0,
        stop: 0.56 #191C20, stop: 1 #191C20
    );
    border-color: #82E3D0;
}
QRadioButton::indicator:disabled {
    border-color: #5F686F;
}
QRadioButton::indicator:checked:disabled {
    background: qradialgradient(
        cx: 0.5, cy: 0.5, radius: 0.5,
        fx: 0.5, fy: 0.5,
        stop: 0 #738781, stop: 0.5 #738781,
        stop: 0.56 #292B2E, stop: 1 #292B2E
    );
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
QListWidget::item:hover, QTreeWidget::item:hover, QTableWidget::item:hover,
QTableView::item:hover, QTreeView::item:hover {
    background: #313E43;
}
QListWidget::item:selected, QTreeWidget::item:selected,
QTableWidget::item:selected, QTableView::item:selected, QTreeView::item:selected {
    background: #386B60;
    color: #FFFFFF;
}
QListWidget:focus, QTreeWidget:focus, QTableWidget:focus,
QTableView:focus, QTreeView:focus {
    border-color: #82E3D0;
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
    min-height: 24px;
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

/* Keep handles distinct from their track without crowding compact panels. */
QScrollBar:vertical, QScrollBar:horizontal {
    background: #191919;
    border: none;
    width: 12px;
    height: 12px;
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
/* Guided image editing shares the desktop's interaction colors. */
QFrame#imageEditorSidebar {
    background: #202428;
    border: 1px solid #4F5962;
    border-radius: 5px;
}
QLabel[role="sectionTitle"] {
    color: #F3F7FA;
    background: transparent;
    font-size: 11pt;
    font-weight: 600;
}
QLabel#editorTitle {
    color: #FFFFFF;
    background: transparent;
    font-size: 15pt;
    font-weight: 700;
}
QTabWidget#imageEditorWorkflow::pane {
    border: none;
    background: #202428;
}
QTabWidget#imageEditorWorkflow QTabBar::tab {
    padding: 7px 9px;
}
QPushButton[editorTool="true"] {
    text-align: left;
    padding: 6px 9px;
    min-height: 25px;
}
QPushButton[editorTool="true"]:checked {
    background: #2A5C50;
    border: 2px solid #82E3D0;
    padding: 5px 8px;
    color: #FFFFFF;
    font-weight: 600;
}
QLabel#editorToolHelp {
    color: #D6E3E7;
    background: #28343A;
    border: 1px solid #4E6670;
    border-radius: 4px;
    padding: 8px;
}
QLabel#editorPreviewState, QLabel#editorDraftState {
    color: #D2DBE0;
    background: transparent;
    padding: 3px 0;
}
QFrame#editorFooter {
    background: #252B30;
    border-top: 1px solid #53616B;
}
QToolTip {
    color: #FFFFFF;
    background: #303030;
    border: 1px solid #777777;
    padding: 3px 5px;
}
""".replace("__THEME_ASSETS__", (Path(__file__).resolve().parent / "assets").as_posix())


def apply_dark_theme(application: object) -> None:
    """Apply the compact desktop theme while preserving OS DPI scaling."""

    from .dialogs import install_confirmation_button_colors
    from .qt import require_qt

    _, QtGui, _ = require_qt()
    application.setStyle("Fusion")
    # Stylesheets do not cover every native primitive (menu checks, tree
    # branches, dialog icons). Give Fusion a matching palette as well.
    palette = QtGui.QPalette()
    roles = QtGui.QPalette.ColorRole
    colors = {
        roles.Window: "#202020", roles.WindowText: "#E6E6E6",
        roles.Base: "#191C20", roles.AlternateBase: "#242A30",
        roles.Text: "#F3F3F3", roles.Button: "#373C42",
        roles.ButtonText: "#F3F3F3", roles.BrightText: "#FFFFFF",
        roles.Highlight: "#386B60", roles.HighlightedText: "#FFFFFF",
        roles.ToolTipBase: "#303030", roles.ToolTipText: "#FFFFFF",
        roles.Link: "#82E3D0", roles.PlaceholderText: "#A8B0B8",
        roles.Light: "#929DA7", roles.Midlight: "#66717B",
        roles.Mid: "#4F5962", roles.Dark: "#16191C", roles.Shadow: "#101214",
    }
    for role, color in colors.items():
        palette.setColor(role, QtGui.QColor(color))
    for role in (roles.WindowText, roles.Text, roles.ButtonText, roles.PlaceholderText):
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, role, QtGui.QColor("#999FA5"))
    application.setPalette(palette)
    application.setStyleSheet(DARK_STYLESHEET)
    install_confirmation_button_colors(application)


__all__ = [
    "DARK_STYLESHEET",
    "DEFAULT_CAMERA_OVERLAY_OPACITY",
    "DRAFTING_COLORS",
    "apply_dark_theme",
]
