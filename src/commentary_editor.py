"""Edit a glossary subtitle draft and preview its actual broadcast layout."""
from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QEvent, QSignalBlocker
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QGridLayout, QGroupBox, QInputDialog, QLabel, QLineEdit, QVBoxLayout

import artwork
from broadcast_artwork import EXTRA_SIZES, commentary_layout, draw_commentary
from commentary_style import FONT_SIZE_RANGE, LETTER_SPACING_RANGE, commentary_style_options
from i18n import tr
from settings_drafts import merge_draft
from settings_preview import ArtworkPreview
from settings_window import form_values
from typography import overlay_scope
from ui_widgets import button, decimal, integer, note, row


class CommentaryStyleEditor(QGroupBox):
    def __init__(self, panel):
        super().__init__('术语字幕样式')
        self.panel = panel
        self.model = panel.model
        self._preview_signature = None
        self._preview_layout = None
        self.controls = {}

        layout = QVBoxLayout(self)
        self.preview = ArtworkPreview(EXTRA_SIZES['commentary'], self.draw_preview, height=150)
        self.preview.setAccessibleName(tr('术语字幕播出预览'))
        self.preview.installEventFilter(self)
        layout.addWidget(self.preview)
        grid = QGridLayout()
        for column, heading in enumerate(('字体', '字号', '字间距'), 1):
            grid.addWidget(QLabel(heading), 0, column)
        grid.setColumnStretch(1, 1)
        for index, (role, label) in enumerate((('title', '标题'), ('body', '正文')), 1):
            family = QLineEdit()
            family.setMaxLength(150)
            family.setClearButtonEnabled(True)
            family.setPlaceholderText('继承播出字体')
            size = integer(*FONT_SIZE_RANGE)
            size.setSuffix(' px')
            size.setKeyboardTracking(True)
            spacing = decimal(*LETTER_SPACING_RANGE)
            spacing.setSuffix(' px')
            spacing.setSingleStep(.5)
            spacing.setKeyboardTracking(True)
            grid.addWidget(QLabel(label), index, 0)
            grid.addLayout(row(family, button('选择字体', lambda _, field=family: self.choose_font(field))), index, 1)
            grid.addWidget(size, index, 2)
            grid.addWidget(spacing, index, 3)
            self.controls.update({
                f'commentary_{role}_font_family': family,
                f'commentary_{role}_font_size': size,
                f'commentary_{role}_letter_spacing': spacing,
            })
            family.textChanged.connect(self.refresh_preview)
            size.valueChanged.connect(self.refresh_preview)
            spacing.valueChanged.connect(self.refresh_preview)
        layout.addLayout(grid)
        layout.addWidget(note('预览按当前字幕宽度换行。字号和字间距随播出字号比例缩放。'))
        layout.addLayout(row(button('保存字幕样式', panel.save_commentary_style), 1))
        panel.commentary_title.textChanged.connect(self.refresh_preview)
        panel.commentary_body.textChanged.connect(self.refresh_preview)
        self.load(panel.config())
        self.model.display_changed.connect(self.sync_saved_style)

    def choose_font(self, field):
        families = QFontDatabase.families()
        current = field.text().strip() or self.model.settings.get('overlay_font_family') or artwork.FONT
        index = families.index(current) if current in families else 0
        chosen, accepted = QInputDialog.getItem(self, tr('选择字体'), tr('字体'), families, index, True)
        if accepted:
            field.setText(chosen)

    def values(self):
        return {key: field.text().strip() if isinstance(field, QLineEdit) else field.value()
                for key, field in self.controls.items()}

    def pending_values(self):
        return form_values(*self.controls.values())

    def load(self, config):
        self.baseline = commentary_style_options(config)
        for key, field in self.controls.items():
            with QSignalBlocker(field):
                if isinstance(field, QLineEdit):
                    field.setText(self.baseline[key])
                else:
                    field.setValue(self.baseline[key])
        self._baseline_values = self.values()
        self._loaded_values = self.pending_values()
        self.refresh_preview()

    def edited_values(self):
        for field in self.controls.values():
            if not isinstance(field, QLineEdit):
                if not field.hasAcceptableInput():
                    raise ValueError('请完成术语字幕字号或字间距的输入')
                field.interpretText()
        merged, changed, conflicts = merge_draft(
            self._baseline_values, self.values(), self.baseline, commentary_style_options(self.panel.config()))
        if conflicts:
            raise ValueError('术语字幕样式已在其他窗口修改，请重新读取后再保存')
        return {key: merged[key] for key in changed}

    def save(self):
        changes = self.edited_values()
        if changes:
            self.model.configure_commentary(changes)
        self.load(self.panel.config())

    def sync_saved_style(self):
        live = commentary_style_options(self.panel.config())
        if live != self.baseline and self.pending_values() == self._loaded_values:
            self.load(live)
            self.panel.mark_saved('commentary_style')
        else:
            self.refresh_preview()

    def eventFilter(self, obj, event):
        if obj is self.preview and event.type() == QEvent.Type.Show:
            self.refresh_preview(force=True)
        return super().eventFilter(obj, event)

    def refresh_preview(self, *_, force=False):
        if self.panel.setting_term or (not force and not self.preview.isVisible()):
            return
        config = self.panel.config()
        live = commentary_style_options(config)
        # Untouched fields can follow a newer saved style while the editor is open.
        live.update({key: value for key, value in self.values().items() if value != self._baseline_values[key]})
        draft = dict(live, commentary_title=self.panel.commentary_title.text(),
                     commentary_body=self.panel.commentary_body.toPlainText(),
                     show_match_title=config.get('show_match_title', True))
        settings = {key: self.model.settings.get(key, default) for key, default in
                    (('overlay_font_family', ''), ('overlay_font_scale', 1.))}
        width = self.panel.manager.base_size('commentary')[0]
        signature = (draft, settings, width)
        if signature == self._preview_signature:
            return
        self._preview_signature = signature
        self._preview_model = SimpleNamespace(state={'broadcast': {'config': draft}}, settings=settings)
        with overlay_scope(settings):
            self._preview_layout = commentary_layout(self._preview_model, width)
        self.preview.set_design_size((width, self._preview_layout['height']))
        self.preview.invalidate()

    def draw_preview(self, painter):
        if self._preview_layout is not None:
            draw_commentary(painter, self._preview_model, layout=self._preview_layout)
