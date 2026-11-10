"""Operator-only palettes. Broadcast artwork never consumes these colors."""
from pathlib import Path
import sys

PALETTES = {
    'dark': dict(bg='#151a21', panel='#1e2630', field='#252f3b', hover='#303e4e',
                 text='#edf1f5', muted='#a1afbf', border='#3c4b5e', accent='#62b8e8',
                 selected='#254a65', primary='#256b96', error='#ffaaa2', success='#95d7b2',
                 draft='#223446', published='#21392e',
                 scroll_track='#202a36', scroll_thumb='#b7c6d7',
                 scroll_hover='#d6e1ed', scroll_pressed='#f2f6fb'),
    'light': dict(bg='#edf1f6', panel='#ffffff', field='#f8fafc', hover='#e7eff8',
                  text='#1f3044', muted='#5f6e80', border='#b8c6d6', accent='#176ca5',
                  selected='#e2eef9', primary='#176ca5', error='#b02d3d', success='#286840',
                  draft='#edf5ff', published='#eaf5ee',
                  scroll_track='#e8eef5', scroll_thumb='#74879e',
                  scroll_hover='#61758d', scroll_pressed='#4c6179'),
}


def palette(settings):
    return PALETTES.get(settings.get('console_theme'), PALETTES['dark'])


def stylesheet(settings):
    c = dict(palette(settings))
    controls = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'resources' / 'controls'
    theme = 'light' if settings.get('console_theme') == 'light' else 'dark'
    for symbol in ('plus', 'minus'):
        c['step_' + symbol] = (controls / f'step-{symbol}-{theme}.svg').as_posix()
        c['step_' + symbol + '_disabled'] = (controls / f'step-{symbol}-disabled.svg').as_posix()
    return '''
QMainWindow,QDialog{background:%(bg)s;color:%(text)s}
QWidget{color:%(text)s;font-family:"Microsoft YaHei","Noto Sans CJK SC","Noto Sans CJK JP";font-size:12px}
QWidget#settingsPage,QWidget#settingsBody{background:%(bg)s}
QWidget#consoleHeader{background:%(panel)s;border-bottom:1px solid %(border)s}
QGroupBox{background:%(panel)s;border:1px solid %(border)s;border-radius:6px;margin-top:12px;padding:12px 9px 8px}
QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 5px;color:%(muted)s}
QGroupBox[tileTarget="true"]{border:2px solid %(accent)s}
QGroupBox[panelBody="true"]{margin-top:0;padding:6px;border-radius:4px}
QPushButton,QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit,QKeySequenceEdit{border:1px solid %(border)s;border-radius:4px;background:%(field)s;color:%(text)s;min-height:28px;padding:2px 7px}
QPushButton:hover{background:%(hover)s;border-color:%(accent)s}
QPushButton:checked{background:%(selected)s;border-color:%(accent)s}
QPushButton:disabled{color:%(muted)s;background:%(panel)s}
QPushButton[operatorChoice="true"]:checked{background:%(primary)s;color:white;border:2px solid %(accent)s;font-weight:600}
QPushButton[operatorChoice="true"]:checked:hover{background:%(primary)s;border-color:%(text)s}
QPushButton[operatorChoice="true"]:disabled{background:%(panel)s;color:%(muted)s;border:1px solid %(border)s}
QPushButton[currentSeat="true"]{background:%(selected)s;border:2px solid %(accent)s;font-weight:600}
QPushButton[calledTile="true"]{border:2px solid %(accent)s}
QPushButton#tileChoice:hover{border:2px solid %(accent)s}
QPushButton#tileChoice:pressed{border:3px solid %(primary)s}
QToolButton#handEditAction,QToolButton#handEditApply{background:%(field)s;color:%(text)s;border:1px solid %(border)s;border-radius:3px}
QToolButton#handEditAction:hover{background:%(hover)s;border-color:%(accent)s}
QToolButton#handEditApply{background:%(primary)s;color:white;border-color:%(primary)s;font-weight:600}
QToolButton#handEditAction:disabled,QToolButton#handEditApply:disabled{background:%(panel)s;color:%(muted)s;border-color:%(border)s}
QPushButton#primary{background:%(primary)s;color:white;border-color:%(primary)s;min-height:32px}
QPushButton#primary:disabled{background:%(panel)s;color:%(muted)s;border-color:%(border)s}
QPushButton#riichi:checked{background:#b23241;color:white;border-color:#db6876}
QPushButton#tenpai:checked{background:#916124;color:white;border-color:#be975f}
QLabel#score{font-size:23px}QLabel#muted{color:%(muted)s;font-size:11px}
QLabel#error{color:%(error)s}QLabel#success{color:%(success)s}QLabel#sectionTitle{font-size:22px;font-weight:600}
QLabel#deltaPlus{color:%(success)s}QLabel#deltaMinus{color:%(error)s}
QLabel#draftPreview{background:%(draft)s;color:%(text)s;border-left:3px solid %(accent)s;padding:5px 7px}
QLabel#draftPreview[published="true"]{background:%(published)s;border-left-color:%(success)s}
QScrollArea{border:0;background:transparent}QScrollArea>QWidget>QWidget{background:%(panel)s}
QCheckBox{spacing:6px}QCheckBox::indicator{width:15px;height:15px;border:1px solid %(border)s;background:%(field)s;border-radius:3px}
QCheckBox::indicator:checked{background:%(primary)s;border:2px solid %(accent)s}
QSpinBox,QDoubleSpinBox{padding:2px 60px 2px 7px;min-height:30px}
QSpinBox::up-button,QDoubleSpinBox::up-button{subcontrol-origin:border;subcontrol-position:center right;width:28px;height:30px;background:%(field)s;border-left:1px solid %(border)s;border-top-right-radius:4px;border-bottom-right-radius:4px}
QSpinBox::down-button,QDoubleSpinBox::down-button{subcontrol-origin:border;subcontrol-position:center right;right:28px;width:28px;height:30px;background:%(field)s;border-left:1px solid %(border)s}
QDoubleSpinBox[compactUma="true"]{padding-right:48px}
QDoubleSpinBox[compactUma="true"]::up-button{width:22px}
QDoubleSpinBox[compactUma="true"]::down-button{right:22px;width:22px}
QSpinBox::up-button:hover,QSpinBox::down-button:hover,QDoubleSpinBox::up-button:hover,QDoubleSpinBox::down-button:hover{background:%(hover)s}
QSpinBox::up-button:pressed,QSpinBox::down-button:pressed,QDoubleSpinBox::up-button:pressed,QDoubleSpinBox::down-button:pressed{background:%(selected)s}
QSpinBox::up-button:disabled,QSpinBox::down-button:disabled,QDoubleSpinBox::up-button:disabled,QDoubleSpinBox::down-button:disabled{background:%(panel)s}
QSpinBox::up-arrow,QSpinBox::down-arrow,QDoubleSpinBox::up-arrow,QDoubleSpinBox::down-arrow{width:16px;height:16px}
QSpinBox::up-arrow,QDoubleSpinBox::up-arrow{image:url("%(step_plus)s")}
QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{image:url("%(step_minus)s")}
QSpinBox::up-arrow:disabled,QDoubleSpinBox::up-arrow:disabled{image:url("%(step_plus_disabled)s")}
QSpinBox::down-arrow:disabled,QDoubleSpinBox::down-arrow:disabled{image:url("%(step_minus_disabled)s")}
QTextEdit,QPlainTextEdit{background:%(field)s;color:%(text)s;border:1px solid %(border)s;border-radius:4px;padding:6px}
QTabWidget::pane{border:1px solid %(border)s;background:%(bg)s}
QTabBar{background:%(bg)s}
QTabBar::tab{background:%(panel)s;padding:8px 12px;color:%(muted)s}QTabBar::tab:selected{background:%(selected)s;color:%(text)s}
QComboBox QAbstractItemView{background:%(field)s;color:%(text)s;selection-background-color:%(selected)s;selection-color:%(text)s}
QListWidget,QTreeWidget{background:%(panel)s;border:0;outline:0;padding:6px}
QListWidget::item,QTreeWidget::item{padding:10px 9px;min-height:22px;border-radius:4px}
QListWidget::item:selected,QTreeWidget::item:selected{background:%(selected)s;color:%(text)s}
QListWidget::item:hover,QTreeWidget::item:hover{background:%(hover)s}
QTableWidget,QTableView{background:%(panel)s;alternate-background-color:%(bg)s;gridline-color:%(border)s;selection-background-color:%(selected)s;selection-color:%(text)s;border:1px solid %(border)s}
QHeaderView::section{background:%(field)s;color:%(text)s;padding:8px;border:0;border-bottom:1px solid %(border)s}
QMenuBar,QMenu{background:%(panel)s;color:%(text)s}QMenu{border:1px solid %(border)s;padding:5px}
QMenu::item{padding:7px 22px}QMenu::item:selected,QMenuBar::item:selected{background:%(selected)s}
QDockWidget{font-weight:500}QDockWidget::title{background:%(field)s;padding:7px;border:1px solid %(border)s}
QToolBar#operatorNavigation{background:%(panel)s;border:0;spacing:7px;padding:7px 0 7px 7px}
QToolBar#operatorNavigation QToolButton{background:%(panel)s;border:0;border-radius:4px;padding:12px 8px;color:%(text)s;text-align:left}
QToolBar#operatorNavigation QToolButton:hover{background:%(selected)s}
QToolBar#operatorPanelSwitches{background:%(panel)s;border:0;spacing:3px;padding:3px}
QToolBar#operatorPanelSwitches QToolButton{background:%(field)s;color:%(text)s;border:1px solid %(border)s;border-radius:3px;padding:4px 8px}
QToolBar#operatorPanelSwitches QToolButton:hover{background:%(hover)s;border-color:%(accent)s}
QToolBar#operatorPanelSwitches QToolButton:checked{background:%(selected)s;border-color:%(accent)s}
QWidget#navigationResizeHandle{background:transparent;border-right:2px solid %(border)s}
QWidget#navigationResizeHandle:hover{background:%(accent)s}
QMainWindow::separator{width:5px;height:5px;background:%(bg)s}QMainWindow::separator:hover{background:%(accent)s}
QSplitter::handle{background:%(border)s}
QToolTip{background:%(panel)s;color:%(text)s;border:1px solid %(border)s;padding:5px}
QScrollBar:vertical{width:9px;background:%(scroll_track)s;border-radius:4px}
QScrollBar::handle:vertical{background:%(scroll_thumb)s;min-height:26px;margin:0 1px;border-radius:3px}
QScrollBar::handle:vertical:hover{background:%(scroll_hover)s}
QScrollBar::handle:vertical:pressed{background:%(scroll_pressed)s}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent}
QScrollBar:horizontal{height:9px;background:%(bg)s}
QScrollBar::handle:horizontal{background:%(border)s;min-width:24px;border-radius:4px}
QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0}
''' % c


def apply_theme(root, settings):
    """Always regenerate from tokens; font scaling is applied exactly once."""
    from typography import apply_desk, desk_stylesheet
    from PySide6.QtGui import QColor, QPalette
    settings = dict(settings)
    if root.property('settingsWindow'):
        settings['console_font_size'] = settings.get('settings_font_size', 14)
    colors = palette(settings)
    native = QPalette(root.palette())
    for role, token in ((QPalette.ColorRole.Window, 'bg'), (QPalette.ColorRole.Base, 'field'),
                        (QPalette.ColorRole.AlternateBase, 'bg'), (QPalette.ColorRole.Text, 'text'),
                        (QPalette.ColorRole.WindowText, 'text'), (QPalette.ColorRole.Button, 'field'),
                        (QPalette.ColorRole.ButtonText, 'text'), (QPalette.ColorRole.Highlight, 'primary'),
                        (QPalette.ColorRole.Link, 'accent'),
                        (QPalette.ColorRole.PlaceholderText, 'muted'), (QPalette.ColorRole.ToolTipBase, 'panel'),
                        (QPalette.ColorRole.ToolTipText, 'text')):
        native.setColor(role, QColor(colors[token]))
    native.setColor(QPalette.ColorRole.HighlightedText, QColor('white'))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        native.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors['muted']))
    if root.palette() != native: root.setPalette(native)
    source = stylesheet(settings)
    rendered = desk_stylesheet(source, settings)
    if root.styleSheet() != rendered: root.setStyleSheet(rendered)
    root._typography_base_style = source
    root._typography_rendered_style = rendered
    apply_desk(root, settings)
    # Qt's stylesheet palette can leave unstyled Link roles at the platform's
    # default blue. Painted recognition feedback needs the same theme accents.
    from PySide6.QtWidgets import QWidget
    for widget in root.findChildren(QWidget):
        if widget.window() is not root.window():
            continue
        if widget.property('typographyPreview'):
            continue
        inherited = widget.palette()
        accent, muted = QColor(colors['accent']), QColor(colors['muted'])
        if inherited.color(QPalette.ColorRole.Link) != accent or inherited.color(QPalette.ColorRole.PlaceholderText) != muted:
            inherited.setColor(QPalette.ColorRole.Link, accent)
            inherited.setColor(QPalette.ColorRole.PlaceholderText, muted)
            widget.setPalette(inherited)


def status(label, kind):
    label.setObjectName(kind)
    label.setStyleSheet('padding:4px')
    label.style().unpolish(label)
    label.style().polish(label)
