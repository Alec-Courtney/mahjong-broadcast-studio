"""Settings shared by all events on this installation."""
from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QCheckBox, QFileDialog, QFontComboBox, QFormLayout, QLabel, QVBoxLayout

import artwork
from application_preferences import capture_preferences
from desk_tiles import TILE_SIZE_KEYS, TILE_SIZE_RANGE
from i18n import LANGUAGES, tr
from settings_drafts import merge_draft
from settings_transfer import export_system_settings, import_system_settings
from settings_window import SettingsWindow, pending_spin_edits, saves_section
from ui_widgets import button, choice, integer, note, row, selected


class SystemSettingsDialog(SettingsWindow):
    def __init__(self, main):
        super().__init__(main)
        self.model = main.model
        self.setWindowTitle('系统设置')
        self.resize(630, 490)
        layout = QVBoxLayout(self)
        layout.addWidget(note('这些设置在切换赛事时继续使用。'))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.theme = choice([('深色', 'dark'), ('浅色', 'light')])
        self.language = choice(LANGUAGES)
        self.language.setProperty('i18n_literal', True)
        self.console_font = QFontComboBox()
        self.console_font.setProperty('i18n_literal', True)
        self.console_font_size = integer(9, 24)
        self.settings_font_size = integer(9, 24)
        for key in TILE_SIZE_KEYS:
            control = integer(*TILE_SIZE_RANGE, step=10)
            control.setSuffix(' %')
            setattr(self, key, control)
        self.dock_animations = QCheckBox('启用导播面板停靠动画')
        for control in (self.console_font_size, self.settings_font_size):
            control.setSuffix(' px')
        for label, control in (
            ('后台主题', self.theme), ('界面语言', self.language), ('导播台字体', self.console_font),
            ('导播台字号', self.console_font_size), ('设置页面字号', self.settings_font_size),
            ('副露牌面尺寸', self.console_meld_tile_scale), ('听牌牌面尺寸', self.console_wait_tile_scale),
        ):
            form.addRow(label, control)
        form.addRow('', self.dock_animations)
        layout.addLayout(form)
        layout.addWidget(note('字号即时保存。面板停靠动画只用于导播操作界面。'))
        layout.addWidget(note('牌面尺寸即时保存，仅调整导播台显示；80%为原大小，100%为默认大小。'))
        layout.addStretch()
        layout.addLayout(row(button('导出全局设置', self.export_file), button('导入全局设置', self.import_file), 1))
        self.error = QLabel()
        self.error.setObjectName('error')
        self.error.setWordWrap(True)
        self.status = QLabel()
        self.status.setObjectName('success')
        self.status.setProperty('i18n_literal', True)
        layout.addWidget(self.error)
        layout.addWidget(self.status)
        layout.addLayout(row(button('重新读取', self.reload), 1, button('保存', self.apply), button('关闭', self.close)))
        self.reload()
        self.track_settings('system', lambda: (self.values(), pending_spin_edits(self)), self.apply)
        for key in ('console_font_size', 'settings_font_size'):
            getattr(self, key).valueChanged.connect(lambda value, field=key: self.save_font_size(field, value))
        for key in TILE_SIZE_KEYS:
            getattr(self, key).valueChanged.connect(lambda value, field=key: self.save_live_size(field, value, '牌面尺寸已即时保存'))

    def live_values(self):
        return self.model.preferences.read() or capture_preferences(self.model.settings)

    def values(self):
        return dict(console_theme=self.theme.currentData(), language=self.language.currentData(),
                    console_font_family=self.console_font.currentFont().family(),
                    console_font_size=self.console_font_size.value(), settings_font_size=self.settings_font_size.value(),
                    **{key: getattr(self, key).value() for key in TILE_SIZE_KEYS},
                    console_dock_animations=self.dock_animations.isChecked())

    def reload(self):
        self._live = self.live_values()
        selected(self.theme, self._live['console_theme'])
        selected(self.language, self._live['language'])
        self.console_font.setCurrentFont(QFont(self._live['console_font_family'] or artwork.FONT))
        self.dock_animations.setChecked(self._live['console_dock_animations'])
        for key in ('console_font_size', 'settings_font_size', *TILE_SIZE_KEYS):
            control = getattr(self, key)
            with QSignalBlocker(control):
                control.setValue(self._live[key])
        self._draft = self.values()
        self.error.clear()
        self.status.clear()
        self.mark_saved()

    def discard_settings(self):
        self.reload()

    def run(self, action, message):
        try:
            action()
        except (ValueError, OSError) as error:
            self.status.clear()
            self.error.setText(str(error))
            return False
        self.error.clear()
        self.status.setText(tr(message))
        return True

    @saves_section('system')
    def apply(self):
        def save():
            for control in (self.console_font_size, self.settings_font_size, *(getattr(self, key) for key in TILE_SIZE_KEYS)):
                if not control.hasAcceptableInput():
                    raise ValueError('数字尚未填写完整，请检查后再保存')
                control.interpretText()
            values, changed, conflicts = merge_draft(self._draft, self.values(), self._live, self.live_values())
            if conflicts:
                raise ValueError('同一设置已在其他窗口修改，请重新读取后再保存')
            if changed:
                self.model.save_preferences(dict(self.model.settings, **{key: values[key] for key in changed}),
                                            application_keys=changed)
            self.reload()
        return self.run(save, '全局设置已保存')

    def save_font_size(self, key, value):
        self.save_live_size(key, value, '字号已即时保存')

    def save_live_size(self, key, value, message):
        previous = self._live[key]
        if not self.run(lambda: self.model.save_preferences(dict(self.model.settings, **{key: value}),
                                                            application_keys={key}), message):
            control = getattr(self, key)
            with QSignalBlocker(control):
                control.setValue(previous)
            return
        self._live[key] = self._draft[key] = value
        self._settings_sections['system']['baseline'][0][key] = value

    def export_file(self):
        filename, _ = QFileDialog.getSaveFileName(self, tr('导出全局设置'), '全局设置.json', 'JSON (*.json)')
        if filename:
            self.run(lambda: export_system_settings(self.model, filename), '已导出已保存的全局设置')

    def import_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, tr('导入全局设置'), '', 'JSON (*.json)')
        if filename:
            def load():
                import_system_settings(self.model, filename)
                self.reload()
            self.run(load, '全局设置已应用')
