"""Editable, window-local operator shortcuts with editor-safe focus gating."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QAbstractSpinBox, QComboBox, QDialog, QDockWidget, QGridLayout,
    QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)
from i18n import tr, signals
from settings_window import SettingsWindow, form_values, saves_section


# Stable action IDs are persisted. Only presentation labels are translated.
ACTIONS = (
    ('seat_1', '选择选手一', 'Alt+1'),
    ('seat_2', '选择选手二', 'Alt+2'),
    ('seat_3', '选择选手三', 'Alt+3'),
    ('seat_4', '选择选手四', 'Alt+4'),
    ('riichi', '切换立直', 'R'),
    ('tenpai', '切换听牌', 'T'),
    ('focus_ron', '准备荣和', 'F2'),
    ('focus_tsumo', '准备自摸', 'F3'),
    ('draw', '流局结算', 'F4'),
    ('next_hand', '进入下一局', 'F5'),
    ('replay', '重播动画', 'F6'),
    ('dora', '选择宝牌', 'D'),
    ('meld', '选择副露', 'M'),
    ('commentary_show', '显示术语', 'F7'),
    ('commentary_hide', '隐藏术语', 'F8'),
    ('overlays', '显示或隐藏画面', 'F9'),
    ('undo', '对局撤销', 'Ctrl+Z'),
    ('redo', '对局重做', 'Ctrl+Shift+Z'),
    ('submit_win', '确认和牌', ''),
)
DEFAULT_HOTKEYS = {action: key for action, _, key in ACTIONS}
ACTION_LABELS = {action: label for action, label, _ in ACTIONS}


def normalize_sequence(value):
    """Portable single-chord representation, or an empty unbound shortcut."""
    if isinstance(value, QKeySequence):
        sequence = QKeySequence(value)
    elif isinstance(value, str):
        if not value.strip():
            return ''
        sequence = QKeySequence.fromString(value, QKeySequence.SequenceFormat.PortableText)
    else:
        raise ValueError(tr('快捷键无效'))
    if sequence.isEmpty():
        return ''
    if sequence.count() != 1:
        raise ValueError(tr('快捷键必须是单次组合'))
    key = sequence[0].key()
    if key in (Qt.Key.Key_unknown, Qt.Key.Key_Shift, Qt.Key.Key_Control,
               Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr) or int(key) <= 0:
        raise ValueError(tr('快捷键无效'))
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


def validate_bindings(mapping):
    """Validate all bindings together before replacing any active shortcuts."""
    if not isinstance(mapping, dict):
        raise ValueError(tr('快捷键配置格式无效'))
    if any(key not in DEFAULT_HOTKEYS for key in mapping):
        raise ValueError(tr('未知的快捷键动作'))
    candidate = dict(DEFAULT_HOTKEYS)
    candidate.update(mapping)
    result, used = {}, {}
    for action, value in candidate.items():
        key = normalize_sequence(value)
        if key:
            if key in used:
                raise ValueError(tr('快捷键冲突：{first} 与 {second} 使用 {key}').format(
                    first=tr(ACTION_LABELS[used[key]]), second=tr(ACTION_LABELS[action]), key=key))
            used[key] = action
        result[action] = key
    return result


class ShortcutController(QObject):
    """Own only this main window's shortcuts, never OS-wide hotkeys.

    actions maps the stable IDs above to button.click or another safe callback.
    refresh() also reloads imported settings; rebind() applies an editable patch.
    """
    bindings_changed = Signal(object)

    def __init__(self, window, model, actions):
        super().__init__(window)
        self.window, self.model = window, model
        self.actions = dict(actions)
        self.shortcuts = {}
        self._bindings = {}
        self._disposed = False
        self._sync_pending = False
        self._app = QApplication.instance()
        self._app.installEventFilter(self)
        self._app.focusChanged.connect(self._focus_changed)
        self.window.destroyed.connect(self.dispose)
        self.refresh()

    @property
    def bindings(self):
        return dict(self._bindings)

    def key_label(self, action):
        return QKeySequence(self._bindings.get(action, '')).toString(QKeySequence.SequenceFormat.NativeText)

    def _stored_bindings(self):
        try:
            return validate_bindings(self.model.settings.get('hotkeys', {}))
        except (ValueError, TypeError):
            # A malformed imported settings object must not break the console
            # or leave two actions attached to a potentially destructive key.
            return dict(DEFAULT_HOTKEYS)

    def _install(self, bindings):
        for shortcut in self.shortcuts.values():
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.shortcuts = {}
        self._bindings = dict(bindings)
        for action, key in bindings.items():
            if not key:
                continue
            shortcut = QShortcut(QKeySequence(key), self.window)
            # Application context includes floating native docks. can_trigger()
            # still admits only this operator window and its own dock windows.
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.setEnabled(False)
            shortcut.activated.connect(lambda action=action: self._dispatch(action))
            self.shortcuts[action] = shortcut
        self.sync_enabled()
        self.bindings_changed.emit(self.bindings)

    def refresh(self):
        if self._disposed:
            return
        bindings = self._stored_bindings()
        if bindings != self._bindings:
            self._install(bindings)
        self.sync_enabled()

    def rebind(self, mapping, save=True):
        candidate = self.bindings
        if not isinstance(mapping, dict):
            raise ValueError(tr('快捷键配置格式无效'))
        candidate.update(mapping)
        bindings = validate_bindings(candidate)
        existed = 'hotkeys' in self.model.settings
        previous = self.model.settings.get('hotkeys')
        self.model.settings['hotkeys'] = dict(bindings)
        try:
            if save:
                self.model.save()
        except Exception:
            if existed:
                self.model.settings['hotkeys'] = previous
            else:
                self.model.settings.pop('hotkeys', None)
            raise
        self._install(bindings)
        return self.bindings

    def can_trigger(self):
        if self._disposed or not self.window.isVisible():
            return False
        active = self._app.activeWindow()
        operator_window = active is self.window.window()
        if isinstance(active, QDockWidget):
            operator_window = active.parentWidget() is self.window
        if not operator_window:
            return False
        if self._app.activeModalWidget() is not None or self._app.activePopupWidget() is not None:
            return False
        focus = self._app.focusWidget()
        if (focus is not None and focus is not active
                and not active.isAncestorOf(focus)):
            return False
        # Check ancestors too: spin boxes, editable combos and sequence editors
        # route actual key events through internal QLineEdit child widgets.
        while focus is not None:
            if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit,
                                  QAbstractSpinBox, QComboBox, QKeySequenceEdit)):
                return False
            if focus is active:
                break
            focus = focus.parentWidget()
        return True

    def sync_enabled(self):
        if self._disposed:
            return
        enabled = self.can_trigger()
        for action, shortcut in self.shortcuts.items():
            shortcut.setEnabled(enabled and callable(self.actions.get(action)))

    def _schedule_sync(self):
        if self._disposed or self._sync_pending:
            return
        self._sync_pending = True
        QTimer.singleShot(0, self._deferred_sync)

    def _deferred_sync(self):
        self._sync_pending = False
        self.sync_enabled()

    def _focus_changed(self, *_):
        self.sync_enabled()

    def _dispatch(self, action):
        # Re-check at activation in case a modal was opened during key dispatch.
        if self.can_trigger():
            callback = self.actions.get(action)
            if callable(callback):
                callback()

    def eventFilter(self, obj, event):
        if self._disposed:
            return False
        kind = event.type()
        if kind == QEvent.Type.ShortcutOverride:
            # Disabling BEFORE shortcut matching preserves native editor Ctrl+Z.
            self.sync_enabled()
        elif kind in (QEvent.Type.WindowDeactivate, QEvent.Type.ApplicationDeactivate):
            for shortcut in self.shortcuts.values():
                shortcut.setEnabled(False)
            self._schedule_sync()
        elif kind in (QEvent.Type.WindowActivate, QEvent.Type.ApplicationActivate,
                      QEvent.Type.FocusIn, QEvent.Type.FocusOut, QEvent.Type.Show, QEvent.Type.Hide):
            self._schedule_sync()
        return False

    def open_dialog(self):
        dialog = ShortcutDialog(self, self.window)
        try:
            return dialog.exec()
        finally:
            dialog.deleteLater()
            self.sync_enabled()

    def dispose(self, *_):
        if self._disposed:
            return
        self._disposed = True
        self._app.removeEventFilter(self)
        try:
            self._app.focusChanged.disconnect(self._focus_changed)
        except (RuntimeError, TypeError):
            pass
        for shortcut in self.shortcuts.values():
            try:
                shortcut.setEnabled(False)
            except RuntimeError:
                pass


class ShortcutDialog(SettingsWindow):
    def __init__(self, controller, parent=None):
        super().__init__(parent or controller.window)
        self.controller = controller
        self.setModal(True)
        self.resize(640, 650)
        layout = QVBoxLayout(self)
        self.description = QLabel()
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        self.action_header, self.key_header = QLabel(), QLabel()
        grid.addWidget(self.action_header, 0, 0);grid.addWidget(self.key_header, 0, 1)
        self.edits, self.labels, self.clear_buttons = {}, {}, {}
        for row, (action, _, _) in enumerate(ACTIONS, 1):
            label = QLabel()
            editor = QKeySequenceEdit(QKeySequence(controller.bindings[action]))
            editor.setObjectName('hotkey_' + action)
            if hasattr(editor, 'setMaximumSequenceLength'):
                editor.setMaximumSequenceLength(1)
            clear = QPushButton()
            clear.setAutoDefault(False)
            clear.clicked.connect(editor.clear)
            editor.keySequenceChanged.connect(self._clear_error)
            grid.addWidget(label, row, 0);grid.addWidget(editor, row, 1);grid.addWidget(clear, row, 2)
            self.labels[action], self.edits[action], self.clear_buttons[action] = label, editor, clear
        grid.setColumnStretch(1, 1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.error_label = QLabel()
        self.error_label.setObjectName('error')
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        self.clear_all_button, self.defaults_button = QPushButton(), QPushButton()
        self.save_button, self.cancel_button = QPushButton(), QPushButton()
        for button in (self.clear_all_button, self.defaults_button, self.save_button, self.cancel_button):
            button.setAutoDefault(False)
        self.clear_all_button.clicked.connect(self.clear_all)
        self.defaults_button.clicked.connect(self.restore_defaults)
        self.save_button.clicked.connect(self.save)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.clear_all_button);buttons.addWidget(self.defaults_button);buttons.addStretch(1)
        buttons.addWidget(self.save_button);buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        signals.language_changed.connect(self.retranslate)
        self.retranslate()
        self.track_settings('hotkeys', lambda: form_values(*self.edits.values()), self.save)

    def retranslate(self, *_):
        self.setWindowTitle(tr('快捷键设置'))
        self.description.setText(tr('仅在主控台活跃、未输入文字或数字时生效。每个动作可绑定一个组合键，留空表示不绑定。'))
        self.action_header.setText(tr('动作'));self.key_header.setText(tr('快捷键'))
        for action, label, _ in ACTIONS:
            self.labels[action].setText(tr(label))
            self.clear_buttons[action].setText(tr('清空'))
        self.clear_all_button.setText(tr('清空全部'));self.defaults_button.setText(tr('恢复默认'))
        self.save_button.setText(tr('保存'));self.cancel_button.setText(tr('关闭'))

    def _clear_error(self, *_):
        self.error_label.clear()

    def clear_all(self):
        for editor in self.edits.values():
            editor.clear()

    def restore_defaults(self):
        for action, editor in self.edits.items():
            editor.setKeySequence(QKeySequence(DEFAULT_HOTKEYS[action]))
        self.error_label.clear()

    @saves_section('hotkeys')
    def save(self):
        bindings = {action:editor.keySequence() for action, editor in self.edits.items()}
        try:
            self.controller.rebind(bindings)
        except ValueError as error:
            self.error_label.setText(str(error))
            return False
        except Exception as error:
            self.error_label.setText(tr('快捷键保存失败：{error}').format(error=str(error)))
            return False
        self.error_label.clear()
        return True
