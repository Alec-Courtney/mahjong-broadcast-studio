"""Coordinate drafts before a user replaces the current event or demo data."""
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox
from shiboken6 import isValid

from i18n import tr
from settings_window import SettingsWindow


def settings_windows(model, first=None):
    windows = []
    for widget in QApplication.topLevelWidgets():
        if not isinstance(widget, SettingsWindow) or not isValid(widget):continue
        owner_model = getattr(widget, '_settings_model', None) or getattr(widget, 'model', None)
        if owner_model is model:windows.append(widget)
    windows.sort(key=lambda widget: (widget is not first, widget.windowTitle(), id(widget)))
    return windows


class SettingsContextChange:
    def __init__(self, owner):
        self.owner = owner
        self.windows = settings_windows(owner.model, owner)
        self.save_error = ''

    def ask(self):
        titles = list(dict.fromkeys(window.windowTitle() for window in self.windows if window.dirty_sections()))
        question = QMessageBox(QMessageBox.Icon.Question, '切换前保存修改',
            '以下窗口有未保存的修改：\n'+'、'.join(titles)+'\n是否先保存再继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            self.owner)
        for answer, label in ((QMessageBox.StandardButton.Yes, '保存并继续'),
                              (QMessageBox.StandardButton.No, '放弃修改并继续'),
                              (QMessageBox.StandardButton.Cancel, '取消')):
            question.button(answer).setText(label)
        question.setDefaultButton(QMessageBox.StandardButton.Yes)
        return question.exec()

    def prepare(self, ask=None):
        self.save_error = ''
        dirty = [window for window in self.windows if window.dirty_sections()]
        if not dirty:return True
        answer = (ask or self.ask)()
        if answer != QMessageBox.StandardButton.Yes:
            return answer == QMessageBox.StandardButton.No
        for window in dirty:
            if window.save_changes():
                continue
            self.save_error = tr('无法保存“{window}”中的修改，请检查后再继续。').format(window=window.windowTitle())
            for name in ('error', 'error_label', 'status'):
                label = getattr(window, name, None)
                if isinstance(label, QLabel) and label.text():
                    self.save_error += '\n' + label.text()
                    break
            return False
        return True

    def finish(self, *, skip=None):
        """Reset only after replacement succeeds, keeping drafts on a failure."""
        for window in self.windows:
            if window is skip or not isValid(window):continue
            if not window._settings_sections:continue
            if type(window).discard_settings is SettingsWindow.discard_settings:
                previous = window._allow_close
                window._allow_close = True
                try:window.close()
                finally:window._allow_close = previous
            else:
                window.discard_settings()
                window.mark_saved()
