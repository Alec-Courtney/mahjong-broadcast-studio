"""Shared save-in-place and unsaved-change handling for configuration windows."""
import copy
from functools import wraps
from PySide6.QtCore import QEvent, QObject, Qt

from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QDial, QDialog, QDockWidget,
    QKeySequenceEdit, QLineEdit, QMessageBox, QSlider, QTableWidget, QTextEdit,
)


class _InputWheelGuard(QObject):
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, (QAbstractSpinBox, QComboBox, QSlider, QDial)):
            return False
        window = watched.window()
        if isinstance(window, QDockWidget) and window.parentWidget() is not None:
            window = window.parentWidget().window()
        if window.property('liveDeskInputs'):
            return False
        # Ignore the edit while allowing the containing page to scroll.
        event.ignore()
        return True


def guard_settings_input_wheel():
    app = QApplication.instance()
    if not hasattr(app, '_input_wheel_guard'):
        app._input_wheel_guard = _InputWheelGuard(app)
        app.installEventFilter(app._input_wheel_guard)


def form_values(*widgets):
    """Only editable values; selection, scrolling and preview state are excluded."""
    def value(widget):
        if isinstance(widget, QTableWidget):
            return [[widget.item(r, c).text() if widget.item(r, c) else ''
                     for c in range(widget.columnCount())] for r in range(widget.rowCount())]
        if isinstance(widget, QComboBox): return widget.currentData()
        if isinstance(widget, QAbstractSpinBox):
            # Include partially typed input when deciding whether closing loses work.
            return widget.text()
        if isinstance(widget, QAbstractButton): return widget.isChecked()
        if isinstance(widget, QKeySequenceEdit): return widget.keySequence().toString()
        if isinstance(widget, QTextEdit): return widget.toPlainText()
        if isinstance(widget, QLineEdit): return widget.text()
        if callable(getattr(widget, 'text', None)): return widget.text()
        return copy.deepcopy(widget)
    return [value(widget) for widget in widgets]


def pending_spin_edits(root):
    """Track unfinished numeric typing, not preview controls' loaded values.

    Browsing a card's text/background changes the editor values and Qt child
    order. Only text that differs from its committed numeric value is a draft.
    """
    pending = []
    for control in root.findChildren(QAbstractSpinBox):
        text = control.text()
        if not control.hasAcceptableInput() or control.valueFromText(text) != control.value():
            pending.append((id(control), text))
    return sorted(pending)


def saves_section(key):
    """A successful existing save becomes the next close-warning baseline."""
    def decorate(callback):
        @wraps(callback)
        def save(self, *args, **kwargs):
            self._save_failed = False
            result = callback(self, *args, **kwargs)
            if not self._save_failed and result is not False:
                self.mark_saved(key)
            return result
        return save
    return decorate


class SettingsWindow(QDialog):
    def __init__(self, parent=None):
        # Native transient ownership keeps stacking and lifetime, without making
        # every settings form a stylesheet descendant of the live console.
        super().__init__(None)
        guard_settings_input_wheel()
        self._settings_owner = parent
        self._transient_owner = parent
        if parent is not None: parent.destroyed.connect(self.deleteLater)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setProperty('settingsWindow', True)
        self._settings_sections = {}
        self._allow_close = False
        self._saving_all = False
        self._save_failed = False

    def track_settings(self, key, values, save):
        self._settings_sections[key] = dict(values=values, save=save, baseline=copy.deepcopy(values()))
        if not hasattr(self, '_settings_model'):
            for owner in (self, getattr(self, 'main', None), getattr(self, 'panel', None),
                          getattr(self, 'controller', None), self._settings_owner):
                model = getattr(owner, 'model', None)
                if model is not None:
                    self._settings_model = model
                    model.display_changed.connect(self.refresh_settings_theme)
                    self.refresh_settings_theme()
                    break

    def set_transient_owner(self, owner):
        self._transient_owner = owner
        self._sync_transient_owner()

    def _sync_transient_owner(self):
        owner = self._transient_owner
        try:
            if owner is not None and self.windowHandle() is not None and owner.window().windowHandle() is not None:
                self.windowHandle().setTransientParent(owner.window().windowHandle())
        except RuntimeError:
            self._transient_owner = self._settings_owner

    def showEvent(self, event):
        super().showEvent(event)
        if self.isWindow():
            self._sync_transient_owner()

    def refresh_settings_theme(self):
        from desk_theme import apply_theme
        settings = self._settings_model.settings
        signature = tuple(settings.get(key) for key in ('console_theme', 'console_font_family', 'settings_font_size'))
        if signature != getattr(self, '_settings_theme_signature', None):
            self._settings_theme_signature = signature
            apply_theme(self, settings)

    def mark_saved(self, key=None):
        for name, section in self._settings_sections.items():
            if key is None or name == key:
                section['baseline'] = copy.deepcopy(section['values']())

    def dirty_sections(self):
        dirty = []
        for key, section in self._settings_sections.items():
            try:
                changed = section['values']() != section['baseline']
            except (ValueError, TypeError):
                changed = True
            if changed: dirty.append(key)
        return dirty

    def save_changes(self):
        self._saving_all = True
        try:
            for key in self.dirty_sections():
                section = self._settings_sections[key]
                self._save_failed = False
                result = section['save']()
                if self._save_failed or result is False or key in self.dirty_sections():
                    return False
        except Exception as error:
            self._save_failed = True
            label = getattr(self, 'error', None) or getattr(self, 'status', None)
            if callable(getattr(label, 'setText', None)):label.setText(str(error))
            elif callable(getattr(getattr(self, 'main', None), 'error', None)):self.main.error(str(error))
            return False
        finally:
            self._saving_all = False
        self.after_settings_saved()
        return True

    def after_settings_saved(self):
        pass

    def _confirm_close(self):
        if self._allow_close or not self.dirty_sections(): return True
        answer = self.ask_save_changes()
        if answer == QMessageBox.StandardButton.Yes:
            return self.save_changes()
        if answer == QMessageBox.StandardButton.No:
            self.discard_settings()
            return True
        return False

    def ask_save_changes(self):
        question = QMessageBox(QMessageBox.Icon.Question, '未保存的设置', '设置已修改，是否保存后关闭？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self)
        question.button(QMessageBox.StandardButton.Yes).setText('是')
        question.button(QMessageBox.StandardButton.No).setText('否')
        question.setDefaultButton(QMessageBox.StandardButton.Yes)
        return question.exec()

    def discard_settings(self):
        """Persistent editors reload their drafts here; saved data stays intact."""

    def reject(self):
        if not self.isWindow():
            self.window().close()
            return
        if self._confirm_close(): super().reject()

    def accept(self):
        if self._confirm_close(): super().accept()

    def closeEvent(self, event):
        if not self._confirm_close():
            event.ignore()
            return
        self._allow_close = True
        try: super().closeEvent(event)
        finally: self._allow_close = False
