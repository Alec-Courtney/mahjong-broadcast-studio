"""Draft-only typography and color settings for both player-card styles."""
import copy

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFontComboBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from card_style import default_card_style, normalize_card_style, validate_card_style
from color_input import ColorInput as ColorButton
from i18n import tr
from ui_widgets import button, choice, integer, note, quantity_control, row, selected


class PaintEditor(QWidget):
    """The same compact color controls edit a text fill or a card background."""
    changed = Signal()

    def __init__(self, allow_team=False):
        super().__init__()
        self.allow_team = allow_team
        self.loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.mode = choice([('纯色', 'solid'), ('渐变', 'gradient')])
        self.direction = choice([('从左到右', 'horizontal'), ('从上到下', 'vertical'), ('左上到右下', 'diagonal')])
        self.source = choice([('跟随队色', 'team'), ('自定义颜色', 'custom')]) if allow_team else None
        first = row('填充', self.mode, 1)
        if self.source:
            first.insertWidget(0, self.source)
        layout.addLayout(first)
        self.start, self.end = ColorButton(), ColorButton()
        self.start_alpha, self.end_alpha = integer(0, 100), integer(0, 100)
        colors = QHBoxLayout()
        for label, control, alpha in (('起始色', self.start, self.start_alpha), ('结束色', self.end, self.end_alpha)):
            column = QVBoxLayout()
            column.setSpacing(3)
            column.addWidget(QLabel(label))
            column.addWidget(control)
            alpha.setSuffix(' %')
            alpha.setRange(0, 100)
            alpha.setMinimumWidth(108)
            alpha.setMaximumWidth(116)
            alpha.setToolTip('0% 全透明，100% 不透明')
            alpha_label = QLabel('不透明度')
            alpha_label.setProperty('i18nLiteral', True)
            column.addLayout(row(alpha_label, alpha))
            colors.addLayout(column)
        layout.addLayout(colors)
        layout.addLayout(row('渐变方向', self.direction, 1))
        for control in (self.mode, self.direction, self.source):
            if control: control.currentIndexChanged.connect(self.on_change)
        self.start.changed.connect(self.color_changed)
        self.end.changed.connect(self.color_changed)
        self.start_alpha.valueChanged.connect(lambda value: self.change_alpha(self.start, value))
        self.end_alpha.valueChanged.connect(lambda value: self.change_alpha(self.end, value))

    def set_value(self, value):
        self.loading = True
        selected(self.mode, value['mode'])
        selected(self.direction, value['direction'])
        if self.source: selected(self.source, value['source'])
        self.start.set_color(value['start'])
        self.end.set_color(value['end'])
        self.start_alpha.setValue(round(self.start.color.alphaF()*100))
        self.end_alpha.setValue(round(self.end.color.alphaF()*100))
        self.loading = False
        self.update_controls()

    def value(self):
        value = dict(mode=self.mode.currentData(), start=self.start.value(), end=self.end.value(),
                     direction=self.direction.currentData())
        if self.source: value['source'] = self.source.currentData()
        return value

    def update_controls(self):
        custom = not self.source or self.source.currentData() == 'custom'
        gradient = self.mode.currentData() == 'gradient'
        self.start.setEnabled(custom)
        self.end.setEnabled(custom and gradient)
        self.end_alpha.setEnabled(gradient)
        self.direction.setEnabled(gradient)

    def color_changed(self):
        self.loading = True
        self.start_alpha.setValue(round(self.start.color.alphaF()*100))
        self.end_alpha.setValue(round(self.end.color.alphaF()*100))
        self.loading = False
        self.on_change()

    def change_alpha(self, control, value):
        if self.loading: return
        color = QColor(control.color)
        color.setAlphaF(value/100)
        control.set_color(color.name(QColor.NameFormat.HexArgb))
        self.on_change()

    def on_change(self, *_):
        self.update_controls()
        if not self.loading: self.changed.emit()


class CardPreview(QWidget):
    """Two read-only views of the same identity; no changes to the live model."""
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setProperty('typographyPreview', True)

    def paintEvent(self, event):
        from artwork import paint_ml_card, fmt
        from mahjong_values import display_score
        import tournament
        editor = self.editor
        player = editor.model.state['players'][editor.seat.currentIndex()]
        settings = editor.preview_settings()
        paint_card = paint_ml_card
        if editor.editing_theme == 'saikouisen':
            from artwork import paint_saikouisen_card
            paint_card = paint_saikouisen_card
        result = next((entry for entry in tournament.match_results(editor.model.state)
                       if entry['id'] == player['id']), None)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#283138'))
        left = 10
        unit = 320 if editor.editing_theme == 'mleague' else 280
        scale = min(1., (self.width()-20)/(unit*2))
        painter.translate((self.width()-unit*2*scale)/2, 7)
        painter.scale(scale, scale)
        painter.setFont(QFont(self.font().family(), 10))
        for column, label in enumerate(('当前点数', '赛后样式 · 按当前点数试算')):
            painter.setPen(QColor('#d6e0e5'))
            painter.drawText(QRectF(left+column*unit, 0, 260, 22), Qt.AlignmentFlag.AlignCenter, tr(label))
            score = display_score(editor.model.state, editor.seat.currentIndex(), settings.get('riichi_payment_mode'))
            value = fmt(score) if column == 0 else f"{result['points']:+,.1f}".replace('-', '−') if result else '—'
            rank = None if column == 0 else result['rank'] if result else '—'
            paint_card(painter, player['name'], player.get('team', ''), player.get('team_color', '#167e9e'),
                       value, rank, x=left+column*unit, y=25, width=260, settings=settings)
        painter.end()


class CardStyleEditor(QWidget):
    changed = Signal()

    def __init__(self, model, seat=0, parent=None):
        super().__init__(parent)
        self.model = model
        self.styles = {
            'mleague': normalize_card_style(model.settings.get('player_card_style'), theme='mleague'),
            'saikouisen': normalize_card_style(model.settings.get('saikouisen_card_style'), theme='saikouisen'),
        }
        self.editing_theme = model.settings.get('theme', 'mleague')
        self.loading = False
        self.base_typography = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.editing_style = choice([('M.League', 'mleague'), ('最高位战', 'saikouisen')])
        selected(self.editing_style, self.editing_theme)
        layout.addLayout(row('编辑样式', self.editing_style, 1))
        self.seat = choice([(p['name'], i) for i, p in enumerate(model.state['players'])])
        self.seat.setProperty('i18nLiteral', True)
        self.seat.setCurrentIndex(seat)
        self.gap = quantity_control(QDoubleSpinBox())
        self.gap.setRange(0, 20)
        self.gap.setDecimals(1)
        self.gap.setSingleStep(.5)
        self.gap.setSuffix(' px')
        self.gap.setKeyboardTracking(False)
        layout.addLayout(row('预览选手', self.seat, 1, '队名与姓名间距', self.gap))
        self.preview = CardPreview(self)
        layout.addWidget(self.preview)
        layout.addWidget(note('预览使用当前选手与点数，只试算外观，不结算、不上屏。过长文字会自动缩小以避免重叠。'))

        controls = QHBoxLayout()
        controls.setSpacing(14)
        text_group = QGroupBox('文字')
        text_layout = QVBoxLayout(text_group)
        self.text_target = choice([('队名', 'team'), ('姓名', 'name'), ('点数／积分', 'score'), ('名次', 'rank')])
        text_layout.addWidget(self.text_target)
        self.inherit_font = QCheckBox('继承播出字体')
        self.family = QFontComboBox()
        self.family.setProperty('i18nLiteral', True)
        text_layout.addWidget(self.inherit_font)
        text_layout.addWidget(self.family)
        self.font_size = integer(8, 80)
        self.font_size.setSuffix(' px')
        self.spacing = quantity_control(QDoubleSpinBox())
        self.spacing.setRange(-2, 12)
        self.spacing.setDecimals(1)
        self.spacing.setSingleStep(.5)
        self.spacing.setSuffix(' px')
        self.spacing.setKeyboardTracking(False)
        self.bold = QCheckBox('粗体')
        form = QFormLayout()
        form.addRow('字号', self.font_size)
        form.addRow('字间距', self.spacing)
        text_layout.addLayout(form)
        text_layout.addWidget(self.bold)
        self.text_paint = PaintEditor()
        text_layout.addWidget(self.text_paint)
        controls.addWidget(text_group, 1)

        background_group = QGroupBox('背景')
        background_layout = QVBoxLayout(background_group)
        self.background_target = choice([('队色点数条', 'score'), ('队名／姓名底板', 'identity')])
        background_layout.addWidget(self.background_target)
        self.background_paint = PaintEditor(allow_team=True)
        background_layout.addWidget(self.background_paint)
        background_layout.addWidget(note('跟随队色可生成明暗渐变；自定义颜色可分别设置两端颜色及透明度。'))
        background_layout.addWidget(note('文字与背景可分别设为纯色或渐变。颜色窗口中的 Alpha 控制透明度。'))
        background_layout.addStretch()
        controls.addWidget(background_group, 1)
        layout.addLayout(controls)
        layout.addLayout(row(button('恢复当前样式默认值', self.reset_defaults), 1))
        layout.addWidget(note('两种样式分别保存；编辑对象不会切换正在播出的样式。选手、积分和赛后卡片共用对应样式设置。'))
        self.text_target.currentIndexChanged.connect(self.load_text)
        self.background_target.currentIndexChanged.connect(self.load_background)
        self.seat.currentIndexChanged.connect(lambda *_: self.preview.update())
        self.editing_style.currentIndexChanged.connect(self.change_style)
        self.gap.valueChanged.connect(self.change_gap)
        for signal in (self.family.currentFontChanged, self.inherit_font.toggled,
                       self.font_size.valueChanged, self.spacing.valueChanged, self.bold.toggled,
                       self.text_paint.changed):
            signal.connect(self.change_text)
        self.background_paint.changed.connect(self.change_background)
        self.load_all()

    @property
    def style(self):
        return self.styles[self.editing_theme]

    def value(self, theme=None):
        value = self.styles[theme or self.editing_theme]
        validate_card_style(value)
        return copy.deepcopy(value)

    def values(self):
        self.text_paint.value()
        self.background_paint.value()
        return dict(player_card_style=self.value('mleague'), saikouisen_card_style=self.value('saikouisen'))

    def preview_settings(self):
        settings = dict(self.model.settings)
        settings.update(self.base_typography)
        settings.update(theme=self.editing_theme, player_card_style=self.styles['mleague'],
                        saikouisen_card_style=self.styles['saikouisen'])
        return settings

    def change_style(self, *_):
        self.editing_theme = self.editing_style.currentData()
        self.load_all()

    def set_typography(self, family, scale):
        self.base_typography = dict(overlay_font_family=family, overlay_font_scale=scale)
        if self.inherit_font.isChecked():
            self.loading = True
            self.family.setCurrentFont(QFont(family))
            self.loading = False
        self.preview.update()

    def load_all(self):
        self.loading = True
        self.gap.setValue(self.style['team_name_gap'])
        self.loading = False
        self.load_text()
        self.load_background()
        self.preview.update()

    def load_text(self, *_):
        value = self.style['text'][self.text_target.currentData()]
        self.loading = True
        self.inherit_font.setChecked(not value['family'])
        family = value['family'] or self.preview_settings().get('overlay_font_family') or 'Microsoft YaHei'
        self.family.setCurrentFont(QFont(family))
        self.family.setEnabled(bool(value['family']))
        self.font_size.setValue(value['size'])
        self.spacing.setValue(value['letter_spacing'])
        self.bold.setChecked(value['bold'])
        self.text_paint.set_value(value['paint'])
        self.loading = False

    def load_background(self, *_):
        self.background_paint.set_value(self.style['backgrounds'][self.background_target.currentData()])

    def change_text(self, *_):
        if self.loading: return
        try:
            paint = self.text_paint.value()
        except ValueError:
            return
        self.family.setEnabled(not self.inherit_font.isChecked())
        self.style['text'][self.text_target.currentData()] = dict(
            family='' if self.inherit_font.isChecked() else self.family.currentFont().family(),
            size=self.font_size.value(), letter_spacing=self.spacing.value(), bold=self.bold.isChecked(),
            paint=paint,
        )
        self.preview.update()
        self.changed.emit()

    def change_background(self):
        try:
            paint = self.background_paint.value()
        except ValueError:
            return
        self.style['backgrounds'][self.background_target.currentData()] = paint
        self.preview.update()
        self.changed.emit()

    def change_gap(self, value):
        if self.loading: return
        self.style['team_name_gap'] = value
        self.preview.update()
        self.changed.emit()

    def reset_defaults(self):
        self.styles[self.editing_theme] = default_card_style(theme=self.editing_theme)
        self.load_all()
        self.changed.emit()
